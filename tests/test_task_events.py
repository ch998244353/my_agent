import json
import platform
import sys
import tracemalloc
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from threading import Barrier
from time import perf_counter

from agents.task_events import (
    replay_task_events,
    task_event_from_dict,
    task_event_to_dict,
    task_node_from_dict,
    task_node_to_dict,
)
from agents.task_state import (
    TASK_EVENT_SCHEMA_VERSION,
    TaskAlreadyExistsError,
    TaskConflictError,
    TaskDependencyCycle,
    TaskEvent,
    TaskEventType,
    TaskLimitExceeded,
    TaskNode,
    TaskNotFoundError,
    TaskSpec,
    TaskStatus,
    TaskStore,
    transition_task_node,
)


class TaskEventContractTests(unittest.TestCase):
    timestamp = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)

    def node(
        self,
        *,
        task_id: str = "task-1",
        revision: int = 1,
    ) -> TaskNode:
        return TaskNode(
            task_id=task_id,
            summary="Inspect the repository",
            status=TaskStatus.PENDING,
            revision=revision,
            created_at=self.timestamp,
        )

    def event(self, **overrides: object) -> TaskEvent:
        values: dict[str, object] = {
            "event_id": "event-1",
            "task_id": "task-1",
            "previous_revision": 0,
            "revision": 1,
            "event_type": TaskEventType.CREATED,
            "timestamp": self.timestamp,
            "node": self.node(),
        }
        values.update(overrides)
        return TaskEvent(**values)

    def test_created_and_updated_events_keep_their_identity(self) -> None:
        created = self.event()
        updated = self.event(
            event_id="event-2",
            previous_revision=1,
            revision=2,
            event_type=TaskEventType.UPDATED,
            node=self.node(revision=2),
            reason="dependency_added",
        )

        self.assertEqual(created.schema_version, TASK_EVENT_SCHEMA_VERSION)
        self.assertEqual(created.event_type.value, "created")
        self.assertEqual(updated.event_type.value, "updated")
        self.assertEqual(updated.reason, "dependency_added")
        with self.assertRaises(FrozenInstanceError):
            created.reason = "changed"

    def test_event_rejects_invalid_metadata_and_node_mismatches(self) -> None:
        cases = [
            ({"event_id": ""}, ValueError),
            ({"event_id": 1}, TypeError),
            ({"task_id": " "}, ValueError),
            ({"previous_revision": True}, TypeError),
            ({"previous_revision": -1}, ValueError),
            ({"revision": True}, TypeError),
            ({"revision": 0}, ValueError),
            ({"event_type": "created"}, TypeError),
            ({"timestamp": datetime(2026, 7, 26, 9, 0)}, ValueError),
            (
                {
                    "timestamp": datetime(
                        2026,
                        7,
                        26,
                        9,
                        0,
                        tzinfo=timezone(timedelta(hours=8)),
                    )
                },
                ValueError,
            ),
            ({"timestamp": "2026-07-26T09:00:00Z"}, TypeError),
            ({"node": "task-1"}, TypeError),
            ({"reason": " "}, ValueError),
            ({"schema_version": True}, TypeError),
            ({"schema_version": 2}, ValueError),
            ({"node": self.node(task_id="task-2")}, ValueError),
            ({"node": self.node(revision=2)}, ValueError),
        ]

        for overrides, error_type in cases:
            with self.subTest(overrides=overrides), self.assertRaises(error_type):
                self.event(**overrides)

    def test_event_rejects_invalid_revision_edges(self) -> None:
        cases = [
            {
                "previous_revision": 1,
            },
            {
                "revision": 2,
                "node": self.node(revision=2),
            },
            {
                "event_type": TaskEventType.UPDATED,
            },
            {
                "previous_revision": 1,
                "revision": 3,
                "event_type": TaskEventType.UPDATED,
                "node": self.node(revision=3),
            },
        ]

        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.event(**overrides)


class TaskEventDictionaryTests(unittest.TestCase):
    timestamp = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)

    def completed_node(self, result: object = None) -> TaskNode:
        return TaskNode(
            task_id="task-1",
            summary="Inspect the repository",
            status=TaskStatus.COMPLETED,
            revision=2,
            created_at=self.timestamp,
            parent_id="root-task",
            dependency_ids=("dependency-1",),
            owner_id="agent-1",
            result=result,
            started_at=self.timestamp,
            finished_at=self.timestamp,
        )

    def updated_event(self, result: object = None) -> TaskEvent:
        return TaskEvent(
            event_id="event-2",
            task_id="task-1",
            previous_revision=1,
            revision=2,
            event_type=TaskEventType.UPDATED,
            timestamp=self.timestamp,
            node=self.completed_node(result),
            reason="completed",
        )

    def test_node_and_event_round_trip_through_standard_json(self) -> None:
        result = {
            "message": "检查完成",
            "items": [1, True, None, {"score": 1.5}],
        }
        event = self.updated_event(result)

        node_payload = task_node_to_dict(event.node)
        event_payload = task_event_to_dict(event)

        self.assertEqual(node_payload["status"], "completed")
        self.assertEqual(node_payload["dependency_ids"], ["dependency-1"])
        self.assertEqual(node_payload["created_at"], "2026-07-26T09:00:00Z")
        self.assertEqual(event_payload["event_type"], "updated")
        self.assertEqual(event_payload["schema_version"], TASK_EVENT_SCHEMA_VERSION)
        self.assertEqual(
            task_node_from_dict(json.loads(json.dumps(node_payload))),
            event.node,
        )
        self.assertEqual(
            task_event_from_dict(json.loads(json.dumps(event_payload))),
            event,
        )

    def test_optional_node_fields_round_trip_as_none(self) -> None:
        node = TaskNode(
            task_id="task-1",
            summary="Inspect the repository",
            status=TaskStatus.PENDING,
            revision=1,
            created_at=self.timestamp,
        )
        event = TaskEvent(
            event_id="event-1",
            task_id="task-1",
            previous_revision=0,
            revision=1,
            event_type=TaskEventType.CREATED,
            timestamp=self.timestamp,
            node=node,
        )

        self.assertEqual(
            task_event_from_dict(
                json.loads(json.dumps(task_event_to_dict(event)))
            ),
            event,
        )

    def test_writer_rejects_values_that_json_would_change_or_guess(self) -> None:
        bad_results = (
            {"items": (1, 2)},
            {"items": {1, 2}},
            {1: "integer key"},
            {"value": float("nan")},
            {"value": object()},
        )

        for result in bad_results:
            with self.subTest(result=result), self.assertRaises(TypeError):
                task_node_to_dict(self.completed_node(result))

        with self.assertRaises(TypeError):
            task_node_to_dict("not a node")
        with self.assertRaises(TypeError):
            task_event_to_dict("not an event")

    def test_store_keeps_arbitrary_results_until_json_export(self) -> None:
        store = TaskStore(clock=lambda: self.timestamp)
        node = store.create(TaskSpec(summary="Run Python", task_id="task-1"))
        node = store.transition(
            node.task_id,
            expected_revision=node.revision,
            to_status=TaskStatus.RUNNING,
        )
        result = object()
        node = store.transition(
            node.task_id,
            expected_revision=node.revision,
            to_status=TaskStatus.COMPLETED,
            result=result,
        )

        self.assertIs(node.result, result)
        with self.assertRaises(TypeError):
            task_node_to_dict(node)

    def test_node_reader_rejects_wrong_shape_and_noncanonical_values(self) -> None:
        with self.assertRaises(TypeError):
            task_node_from_dict([])

        payload = task_node_to_dict(self.completed_node({"ok": True}))
        invalid_payloads: list[tuple[dict[str, object], type[Exception]]] = []

        missing = dict(payload)
        missing.pop("summary")
        invalid_payloads.append((missing, ValueError))

        extra = dict(payload)
        extra["unknown"] = None
        invalid_payloads.append((extra, ValueError))

        unknown_status = dict(payload)
        unknown_status["status"] = "waiting"
        invalid_payloads.append((unknown_status, ValueError))

        wrong_dependencies = dict(payload)
        wrong_dependencies["dependency_ids"] = ("dependency-1",)
        invalid_payloads.append((wrong_dependencies, TypeError))

        noncanonical_time = dict(payload)
        noncanonical_time["created_at"] = "2026-07-26T09:00:00+00:00"
        invalid_payloads.append((noncanonical_time, ValueError))

        lossy_result = dict(payload)
        lossy_result["result"] = {"items": (1, 2)}
        invalid_payloads.append((lossy_result, TypeError))

        for invalid, error_type in invalid_payloads:
            with self.subTest(invalid=invalid), self.assertRaises(error_type):
                task_node_from_dict(invalid)

    def test_event_reader_rejects_unknown_shape_schema_type_and_time(self) -> None:
        with self.assertRaises(TypeError):
            task_event_from_dict([])

        payload = task_event_to_dict(self.updated_event({"ok": True}))
        invalid_payloads: list[tuple[dict[str, object], type[Exception]]] = []

        missing = dict(payload)
        missing.pop("event_id")
        invalid_payloads.append((missing, ValueError))

        extra = dict(payload)
        extra["unknown"] = None
        invalid_payloads.append((extra, ValueError))

        wrong_schema_type = dict(payload)
        wrong_schema_type["schema_version"] = True
        invalid_payloads.append((wrong_schema_type, TypeError))

        unknown_schema = dict(payload)
        unknown_schema["schema_version"] = 2
        invalid_payloads.append((unknown_schema, ValueError))

        unknown_event_type = dict(payload)
        unknown_event_type["event_type"] = "deleted"
        invalid_payloads.append((unknown_event_type, ValueError))

        noncanonical_time = dict(payload)
        noncanonical_time["timestamp"] = "2026-07-26T09:00:00+00:00"
        invalid_payloads.append((noncanonical_time, ValueError))

        for invalid, error_type in invalid_payloads:
            with self.subTest(invalid=invalid), self.assertRaises(error_type):
                task_event_from_dict(invalid)


class TaskEventReducerTests(unittest.TestCase):
    timestamp = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)

    def created_event(
        self,
        task_id: str,
        *,
        event_id: str | None = None,
        parent_id: str | None = None,
        dependency_ids: tuple[str, ...] = (),
    ) -> TaskEvent:
        node = TaskNode(
            task_id=task_id,
            summary=f"Task {task_id}",
            status=TaskStatus.PENDING,
            revision=1,
            created_at=self.timestamp,
            parent_id=parent_id,
            dependency_ids=dependency_ids,
        )
        return TaskEvent(
            event_id=event_id or f"create:{task_id}",
            task_id=task_id,
            previous_revision=0,
            revision=1,
            event_type=TaskEventType.CREATED,
            timestamp=self.timestamp,
            node=node,
        )

    def updated_event(
        self,
        current: TaskNode,
        candidate: TaskNode,
        *,
        event_id: str,
        timestamp: datetime | None = None,
    ) -> TaskEvent:
        return TaskEvent(
            event_id=event_id,
            task_id=current.task_id,
            previous_revision=current.revision,
            revision=candidate.revision,
            event_type=TaskEventType.UPDATED,
            timestamp=timestamp or self.timestamp,
            node=candidate,
        )

    def test_batch_applies_create_update_and_duplicate_once(self) -> None:
        store = TaskStore()
        created = self.created_event("task-1")

        self.assertEqual(
            store._apply_event_batch((created, created)),
            (created,),
        )
        self.assertEqual(store.list(), (created.node,))
        self.assertEqual(store.events(), (created,))
        self.assertEqual(store._apply_event_batch((created,)), ())

        started_at = self.timestamp + timedelta(minutes=1)
        running = transition_task_node(
            created.node,
            TaskStatus.RUNNING,
            now=started_at,
        )
        updated = self.updated_event(
            created.node,
            running,
            event_id="update:task-1:2",
            timestamp=started_at,
        )

        self.assertEqual(store._apply_event_batch((updated,)), (updated,))
        self.assertEqual(store.get("task-1"), running)
        self.assertEqual(store.events(), (created, updated))

    def test_read_only_store_queries_do_not_emit_events(self) -> None:
        store = TaskStore(clock=lambda: self.timestamp)
        node = store.create(
            TaskSpec(summary="Existing command", task_id="task-1")
        )
        before = store.events()

        self.assertIs(store.get(node.task_id), node)
        self.assertEqual(store.list(), (node,))
        self.assertEqual(store.ready(), (node,))
        self.assertEqual(store.blocking_chain(node.task_id), ())
        self.assertEqual(store.events(), before)

    def test_event_id_conflict_and_revision_gap_change_nothing(self) -> None:
        store = TaskStore()
        created = self.created_event("task-1")
        store._apply_event_batch((created,))

        conflicting = replace(created, reason="different content")
        with self.assertRaises(TaskConflictError) as raised:
            store._apply_event_batch((conflicting,))
        self.assertEqual(raised.exception.event_id, created.event_id)
        self.assertEqual(raised.exception.reason, "event_id_conflict")

        skipped_revision = replace(
            created.node,
            revision=3,
            dependency_ids=("missing",),
        )
        gap = TaskEvent(
            event_id="update:task-1:3",
            task_id="task-1",
            previous_revision=2,
            revision=3,
            event_type=TaskEventType.UPDATED,
            timestamp=self.timestamp,
            node=skipped_revision,
        )
        with self.assertRaises(TaskConflictError) as gap_raised:
            store._apply_event_batch((gap,))
        self.assertEqual(gap_raised.exception.expected_revision, 2)
        self.assertEqual(gap_raised.exception.actual_revision, 1)
        self.assertEqual(store.list(), (created.node,))
        self.assertEqual(store.events(), (created,))

    def test_created_event_rejects_existing_task_and_used_node(self) -> None:
        store = TaskStore()
        created = self.created_event("task-1")
        store._apply_event_batch((created,))

        with self.assertRaises(TaskAlreadyExistsError):
            store._apply_event_batch(
                (replace(created, event_id="create:task-1:again"),)
            )

        used_node = replace(
            self.created_event("used").node,
            started_at=self.timestamp,
        )
        used_event = replace(
            self.created_event("used"),
            node=used_node,
        )
        with self.assertRaises(ValueError):
            store._apply_event_batch((used_event,))

    def test_failed_batch_leaves_nodes_seen_events_and_journal_unchanged(
        self,
    ) -> None:
        store = TaskStore()
        root = self.created_event("root")
        missing_reference = self.created_event(
            "child",
            dependency_ids=("missing",),
        )

        with self.assertRaises(TaskNotFoundError):
            store._apply_event_batch((root, missing_reference))

        self.assertEqual(store.list(), ())
        self.assertEqual(store.events(), ())
        self.assertEqual(store._apply_event_batch((root,)), (root,))

    def test_graph_limits_and_dependency_cycle_are_rejected(self) -> None:
        limited_nodes = TaskStore(max_nodes=1)
        root = self.created_event("root")
        limited_nodes._apply_event_batch((root,))
        with self.assertRaises(TaskLimitExceeded):
            limited_nodes._apply_event_batch((self.created_event("extra"),))

        limited_depth = TaskStore(max_depth=0)
        limited_depth._apply_event_batch((root,))
        child = self.created_event("child", parent_id="root")
        with self.assertRaises(TaskLimitExceeded):
            limited_depth._apply_event_batch((child,))

        store = TaskStore()
        task_a = self.created_event("a")
        task_b = self.created_event("b")
        store._apply_event_batch((task_a, task_b))

        node_a = replace(
            task_a.node,
            dependency_ids=("b",),
            revision=2,
        )
        update_a = self.updated_event(
            task_a.node,
            node_a,
            event_id="update:a:2",
        )
        store._apply_event_batch((update_a,))

        node_b = replace(
            task_b.node,
            dependency_ids=("a",),
            revision=2,
        )
        update_b = self.updated_event(
            task_b.node,
            node_b,
            event_id="update:b:2",
        )
        with self.assertRaises(TaskDependencyCycle):
            store._apply_event_batch((update_b,))
        self.assertEqual(store.get("b"), task_b.node)
        self.assertEqual(store.events(), (task_a, task_b, update_a))

    def test_updates_cannot_change_creation_facts(self) -> None:
        changes = (
            {"summary": "Tampered"},
            {"parent_id": "other"},
            {"owner_id": "other-owner"},
            {"created_at": self.timestamp + timedelta(minutes=1)},
        )

        for index, change in enumerate(changes, start=1):
            with self.subTest(change=change):
                store = TaskStore()
                created = self.created_event("task-1")
                store._apply_event_batch((created,))
                candidate = replace(
                    created.node,
                    revision=2,
                    **change,
                )
                forged = self.updated_event(
                    created.node,
                    candidate,
                    event_id=f"forged:{index}",
                )

                with self.assertRaises(ValueError):
                    store._apply_event_batch((forged,))
                self.assertEqual(store.get("task-1"), created.node)
                self.assertEqual(store.events(), (created,))

    def test_dependency_append_and_blocker_merge_are_allowed(self) -> None:
        store = TaskStore()
        dependency_a = self.created_event("dependency-a")
        dependency_b = self.created_event("dependency-b")
        target = self.created_event(
            "target",
            dependency_ids=("dependency-a", "dependency-b"),
        )
        store._apply_event_batch((dependency_a, dependency_b, target))

        blocked_at = self.timestamp + timedelta(minutes=1)
        blocked = transition_task_node(
            target.node,
            TaskStatus.BLOCKED,
            now=blocked_at,
            error="dependency-a failed",
            blocked_by=("dependency-a",),
        )
        block_event = self.updated_event(
            target.node,
            blocked,
            event_id="update:target:2",
            timestamp=blocked_at,
        )
        store._apply_event_batch((block_event,))

        merged = replace(
            blocked,
            blocked_by=("dependency-a", "dependency-b"),
            revision=3,
        )
        merge_event = self.updated_event(
            blocked,
            merged,
            event_id="update:target:3",
        )
        self.assertEqual(
            store._apply_event_batch((merge_event,)),
            (merge_event,),
        )
        self.assertEqual(store.get("target"), merged)


class TaskStoreJournalTests(unittest.TestCase):
    timestamp = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)

    def store(self) -> TaskStore:
        event_ids = iter(f"event-{number}" for number in range(1, 100))
        return TaskStore(
            clock=lambda: self.timestamp,
            event_id_factory=lambda: next(event_ids),
        )

    def test_four_write_commands_emit_events_and_keep_return_values(
        self,
    ) -> None:
        store = self.store()
        dependency = store.create(
            TaskSpec(summary="Dependency", task_id="dependency")
        )
        target = store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                owner_id="owner-1",
            )
        )
        target = store.add_dependency(
            target.task_id,
            dependency.task_id,
            expected_revision=target.revision,
        )
        dependency = store.transition(
            dependency.task_id,
            expected_revision=dependency.revision,
            to_status=TaskStatus.RUNNING,
        )
        dependency = store.transition(
            dependency.task_id,
            expected_revision=dependency.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        blocked_targets = store.mark_owner_missing("owner-1")

        events = store.events()
        self.assertEqual(
            [event.event_id for event in events],
            [f"event-{number}" for number in range(1, 7)],
        )
        self.assertEqual(
            [event.reason for event in events],
            [
                "created",
                "created",
                "dependency_added",
                "transition",
                "transition",
                "owner_missing",
            ],
        )
        self.assertIs(events[4].node, dependency)
        self.assertIs(events[4].node, store.get("dependency"))
        self.assertIs(events[2].node, target)
        self.assertEqual(blocked_targets, (store.get("target"),))
        self.assertEqual(events[-1].node, blocked_targets[0])

    def test_parent_cancel_keeps_breadth_first_journal_order(self) -> None:
        store = self.store()
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        store.create(
            TaskSpec(summary="First", task_id="first", parent_id="root")
        )
        store.create(
            TaskSpec(summary="Second", task_id="second", parent_id="root")
        )
        store.create(
            TaskSpec(
                summary="Grandchild",
                task_id="grandchild",
                parent_id="first",
            )
        )
        before = len(store.events())

        store.transition(
            root.task_id,
            expected_revision=root.revision,
            to_status=TaskStatus.CANCELLED,
        )

        cascade = store.events()[before:]
        self.assertEqual(
            [event.task_id for event in cascade],
            ["root", "first", "second", "grandchild"],
        )
        self.assertEqual(
            [event.reason for event in cascade],
            [
                "transition",
                "parent_cancelled",
                "parent_cancelled",
                "parent_cancelled",
            ],
        )
        self.assertEqual(
            {event.timestamp for event in cascade},
            {self.timestamp},
        )

    def test_failed_dependency_keeps_source_to_downstream_order(self) -> None:
        store = self.store()
        source = store.create(TaskSpec(summary="Source", task_id="source"))
        store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                dependency_ids=("source",),
            )
        )
        store.create(
            TaskSpec(
                summary="Downstream",
                task_id="downstream",
                dependency_ids=("target",),
            )
        )
        source = store.transition(
            source.task_id,
            expected_revision=source.revision,
            to_status=TaskStatus.RUNNING,
        )
        before = len(store.events())

        store.transition(
            source.task_id,
            expected_revision=source.revision,
            to_status=TaskStatus.FAILED,
            error="failed",
        )

        cascade = store.events()[before:]
        self.assertEqual(
            [event.task_id for event in cascade],
            ["source", "target", "downstream"],
        )
        self.assertEqual(
            [event.reason for event in cascade],
            ["transition", "dependency_blocked", "dependency_blocked"],
        )

    def test_owner_missing_records_direct_targets_before_downstream(self) -> None:
        store = self.store()
        store.create(
            TaskSpec(
                summary="Owned A",
                task_id="owned-a",
                owner_id="owner-1",
            )
        )
        store.create(
            TaskSpec(
                summary="Owned B",
                task_id="owned-b",
                owner_id="owner-1",
            )
        )
        store.create(
            TaskSpec(
                summary="Downstream",
                task_id="downstream",
                dependency_ids=("owned-a",),
            )
        )
        before = len(store.events())

        changed = store.mark_owner_missing("owner-1")

        cascade = store.events()[before:]
        self.assertEqual(
            [node.task_id for node in changed],
            ["owned-a", "owned-b"],
        )
        self.assertEqual(
            [event.task_id for event in cascade],
            ["owned-a", "owned-b", "downstream"],
        )
        self.assertEqual(
            [event.reason for event in cascade],
            ["owner_missing", "owner_missing", "dependency_blocked"],
        )

    def test_event_id_factory_failures_and_duplicates_are_atomic(self) -> None:
        class EventIdFactory:
            def __init__(self) -> None:
                self.calls = 0
                self.fail_at: int | None = None

            def __call__(self) -> str:
                self.calls += 1
                if self.calls == self.fail_at:
                    raise RuntimeError("event id failure")
                return f"event-{self.calls}"

        factory = EventIdFactory()
        store = TaskStore(
            clock=lambda: self.timestamp,
            event_id_factory=factory,
        )
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        child = store.create(
            TaskSpec(summary="Child", task_id="child", parent_id="root")
        )
        before_nodes = store.list()
        before_events = store.events()
        factory.fail_at = factory.calls + 2

        with self.assertRaises(RuntimeError):
            store.transition(
                root.task_id,
                expected_revision=root.revision,
                to_status=TaskStatus.CANCELLED,
            )

        self.assertEqual(store.list(), before_nodes)
        self.assertEqual(store.events(), before_events)
        self.assertIs(store.get(child.task_id), child)

        duplicate_store = TaskStore(
            clock=lambda: self.timestamp,
            event_id_factory=lambda: "same-event",
        )
        duplicate_store.create(TaskSpec(summary="First", task_id="first"))
        duplicate_events = duplicate_store.events()
        with self.assertRaises(TaskConflictError):
            duplicate_store.create(
                TaskSpec(summary="Second", task_id="second")
            )
        self.assertEqual(
            [node.task_id for node in duplicate_store.list()],
            ["first"],
        )
        self.assertEqual(duplicate_store.events(), duplicate_events)

        empty_id_store = TaskStore(
            clock=lambda: self.timestamp,
            event_id_factory=lambda: "",
        )
        with self.assertRaises(ValueError):
            empty_id_store.create(
                TaskSpec(summary="Bad event id", task_id="task-1")
            )
        self.assertEqual(empty_id_store.list(), ())
        self.assertEqual(empty_id_store.events(), ())

        with self.assertRaises(TypeError):
            TaskStore(event_id_factory=None)

    def test_no_owner_target_does_not_call_clock_or_event_factory(self) -> None:
        def fail() -> object:
            raise AssertionError("must not be called")

        store = TaskStore(clock=fail, event_id_factory=fail)

        self.assertEqual(store.mark_owner_missing("missing-owner"), ())
        self.assertEqual(store.events(), ())

    def test_same_revision_concurrency_adds_one_journal_event(self) -> None:
        store = self.store()
        node = store.create(TaskSpec(summary="Task", task_id="task-1"))
        barrier = Barrier(2)

        def start() -> TaskNode | TaskConflictError:
            barrier.wait()
            try:
                return store.transition(
                    node.task_id,
                    expected_revision=node.revision,
                    to_status=TaskStatus.RUNNING,
                )
            except TaskConflictError as exc:
                return exc

        before = len(store.events())
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: start(), range(2)))

        self.assertEqual(
            sum(isinstance(result, TaskNode) for result in results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, TaskConflictError) for result in results),
            1,
        )
        self.assertEqual(len(store.events()), before + 1)
        self.assertEqual(store.events()[-1].node, store.get(node.task_id))

    def test_non_json_result_stays_in_memory_journal(self) -> None:
        store = self.store()
        node = store.create(TaskSpec(summary="Task", task_id="task-1"))
        node = store.transition(
            node.task_id,
            expected_revision=node.revision,
            to_status=TaskStatus.RUNNING,
        )
        result = object()
        node = store.transition(
            node.task_id,
            expected_revision=node.revision,
            to_status=TaskStatus.COMPLETED,
            result=result,
        )

        self.assertIs(node.result, result)
        self.assertIs(store.events()[-1].node.result, result)
        with self.assertRaises(TypeError):
            task_event_to_dict(store.events()[-1])


class TaskEventReplayTests(unittest.TestCase):
    timestamp = datetime(2026, 7, 26, 11, 0, tzinfo=timezone.utc)

    def recorded_store(self) -> TaskStore:
        event_ids = iter(f"replay-{number}" for number in range(1, 100))
        store = TaskStore(
            clock=lambda: self.timestamp,
            event_id_factory=lambda: next(event_ids),
        )
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        store.create(
            TaskSpec(summary="Child", task_id="child", parent_id="root")
        )
        source = store.create(TaskSpec(summary="Source", task_id="source"))
        store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                dependency_ids=("source",),
            )
        )
        store.create(
            TaskSpec(
                summary="Downstream",
                task_id="downstream",
                dependency_ids=("target",),
            )
        )
        store.create(
            TaskSpec(
                summary="Owned",
                task_id="owned",
                owner_id="owner-1",
            )
        )
        store.create(
            TaskSpec(
                summary="Owner downstream",
                task_id="owner-downstream",
                dependency_ids=("owned",),
            )
        )
        extra = store.create(TaskSpec(summary="Extra", task_id="extra"))
        added = store.create(TaskSpec(summary="Added", task_id="added"))
        store.add_dependency(
            added.task_id,
            extra.task_id,
            expected_revision=added.revision,
        )
        source = store.transition(
            source.task_id,
            expected_revision=source.revision,
            to_status=TaskStatus.RUNNING,
        )
        store.transition(
            source.task_id,
            expected_revision=source.revision,
            to_status=TaskStatus.FAILED,
            error="failed",
        )
        store.mark_owner_missing("owner-1")
        store.transition(
            root.task_id,
            expected_revision=root.revision,
            to_status=TaskStatus.CANCELLED,
        )
        return store

    def assert_same_store(
        self,
        original: TaskStore,
        restored: TaskStore,
    ) -> None:
        self.assertEqual(restored.list(), original.list())
        self.assertEqual(restored.ready(), original.ready())
        self.assertEqual(restored.events(), original.events())
        for node in original.list():
            self.assertEqual(
                restored.blocking_chain(node.task_id),
                original.blocking_chain(node.task_id),
            )

    def test_replays_the_full_journal_in_order(self) -> None:
        original = self.recorded_store()

        restored = replay_task_events(original.events())

        self.assert_same_store(original, restored)

    def test_duplicate_events_are_idempotent(self) -> None:
        original = self.recorded_store()
        repeated = tuple(
            event
            for event in original.events()
            for event in (event, event)
        )

        restored = replay_task_events(repeated)

        self.assert_same_store(original, restored)

    def test_json_round_trip_still_replays_the_same_store(self) -> None:
        original = self.recorded_store()
        payload = json.loads(
            json.dumps(
                [
                    task_event_to_dict(event)
                    for event in original.events()
                ]
            )
        )
        events = tuple(task_event_from_dict(item) for item in payload)

        restored = replay_task_events(events)

        self.assert_same_store(original, restored)

    def test_replay_rejects_changed_duplicates_and_broken_order(
        self,
    ) -> None:
        original = self.recorded_store()
        events = list(original.events())
        running_index = next(
            index
            for index, event in enumerate(events)
            if event.task_id == "source" and event.revision == 2
        )
        failed_index = next(
            index
            for index, event in enumerate(events)
            if event.task_id == "source" and event.revision == 3
        )

        changed_duplicate = [
            *events,
            replace(events[0], reason="tampered"),
        ]
        swapped = list(events)
        swapped[running_index], swapped[failed_index] = (
            swapped[failed_index],
            swapped[running_index],
        )
        missing_revision = [
            event
            for index, event in enumerate(events)
            if index != running_index
        ]
        repeated_revision = [
            *events,
            replace(events[running_index], event_id="new-event-id"),
        ]
        broken_cases = (
            changed_duplicate,
            swapped,
            missing_revision,
            repeated_revision,
        )
        for broken in broken_cases:
            with self.subTest(broken=broken), self.assertRaises(
                TaskConflictError
            ):
                replay_task_events(broken)

    def test_replay_rejects_missing_references_and_store_limits(self) -> None:
        events = self.recorded_store().events()
        without_root = tuple(
            event
            for event in events
            if not (
                event.task_id == "root"
                and event.event_type is TaskEventType.CREATED
            )
        )

        with self.assertRaises(TaskNotFoundError):
            replay_task_events(without_root)
        with self.assertRaises(TaskLimitExceeded):
            replay_task_events(events, max_nodes=1)
        with self.assertRaises(TaskLimitExceeded):
            replay_task_events(events, max_depth=0)

    def test_replay_consumes_input_once_and_rejects_untyped_values(
        self,
    ) -> None:
        events = self.recorded_store().events()

        class OneShotEvents:
            def __init__(self) -> None:
                self.iterations = 0

            def __iter__(self):
                self.iterations += 1
                if self.iterations > 1:
                    raise AssertionError("events were consumed twice")
                return iter(events)

        source = OneShotEvents()
        restored = replay_task_events(source)
        empty = replay_task_events(())

        self.assertEqual(source.iterations, 1)
        self.assertEqual(restored.events(), events)
        self.assertEqual(empty.list(), ())
        self.assertEqual(empty.events(), ())
        with self.assertRaises(TypeError):
            replay_task_events(None)
        with self.assertRaises(TypeError):
            replay_task_events([{"event_id": "not-a-TaskEvent"}])

    def test_replays_10k_events_and_reports_local_measurement(self) -> None:
        event_count = 10_000
        events = tuple(
            TaskEvent(
                event_id=f"event-{index}",
                task_id=f"task-{index}",
                previous_revision=0,
                revision=1,
                event_type=TaskEventType.CREATED,
                timestamp=self.timestamp,
                reason="created",
                node=TaskNode(
                    task_id=f"task-{index}",
                    summary=f"Task {index}",
                    status=TaskStatus.PENDING,
                    revision=1,
                    created_at=self.timestamp,
                    dependency_ids=(
                        ()
                        if index == 0
                        else (f"task-{index - 1}",)
                    ),
                ),
            )
            for index in range(event_count)
        )

        tracemalloc.start()
        started = perf_counter()
        try:
            restored = replay_task_events(
                events,
                max_nodes=event_count,
            )
        finally:
            elapsed_seconds = perf_counter() - started
            _, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        nodes = restored.list()
        self.assertEqual(len(nodes), event_count)
        self.assertEqual(len(restored.events()), event_count)
        self.assertEqual(nodes[0].dependency_ids, ())
        self.assertEqual(
            nodes[-1].dependency_ids,
            (f"task-{event_count - 2}",),
        )
        self.assertEqual(restored.ready(), (nodes[0],))
        with self.assertRaises(TaskLimitExceeded):
            replay_task_events(events)
        print(
            "TaskEvent replay measurement:",
            f"python={sys.version.replace(chr(10), ' ')}",
            f"platform={platform.platform()}",
            f"events={event_count}",
            f"elapsed_seconds={elapsed_seconds:.6f}",
            f"peak_bytes={peak_bytes}",
            f"peak_mib={peak_bytes / 1024 / 1024:.3f}",
        )


if __name__ == "__main__":
    unittest.main()
