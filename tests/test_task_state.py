import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from threading import Barrier, Thread
from uuid import UUID

from agents.task_state import (
    InvalidTaskTransition,
    TaskAlreadyExistsError,
    TaskConflictError,
    TaskDependencyCycle,
    TaskDependencyNotReady,
    TaskLimitExceeded,
    TaskNode,
    TaskNotFoundError,
    TaskSpec,
    TaskStore,
    TaskStatus,
    transition_task_node,
)


class TaskStateContractTests(unittest.TestCase):
    created_at = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)
    started_at = datetime(2026, 7, 25, 9, 1, tzinfo=timezone.utc)
    finished_at = datetime(2026, 7, 25, 9, 2, tzinfo=timezone.utc)

    def node(self, **overrides: object) -> TaskNode:
        values: dict[str, object] = {
            "task_id": "task-1",
            "summary": "Inspect the repository",
            "status": TaskStatus.PENDING,
            "revision": 1,
            "created_at": self.created_at,
        }
        values.update(overrides)
        return TaskNode(**values)

    def test_status_values_are_stable(self) -> None:
        self.assertEqual(
            [status.value for status in TaskStatus],
            [
                "pending",
                "running",
                "completed",
                "failed",
                "blocked",
                "cancelled",
            ],
        )

    def test_spec_normalizes_dependencies_and_is_frozen(self) -> None:
        spec = TaskSpec(
            summary="Inspect the repository",
            task_id="task-1",
            parent_id="parent-1",
            dependency_ids=["dependency-1"],
            owner_id="owner-1",
        )

        self.assertEqual(spec.dependency_ids, ("dependency-1",))
        with self.assertRaises(FrozenInstanceError):
            spec.summary = "changed"

    def test_spec_rejects_invalid_local_fields(self) -> None:
        invalid_specs = [
            {"summary": ""},
            {"summary": "Task", "task_id": " "},
            {"summary": "Task", "dependency_ids": "dependency-1"},
            {"summary": "Task", "dependency_ids": ["dependency-1", "dependency-1"]},
            {
                "summary": "Task",
                "task_id": "task-1",
                "dependency_ids": ["task-1"],
            },
        ]

        for values in invalid_specs:
            with self.subTest(values=values), self.assertRaises(
                (TypeError, ValueError)
            ):
                TaskSpec(**values)

    def test_all_six_node_states_accept_their_valid_fields(self) -> None:
        nodes = [
            self.node(),
            self.node(status=TaskStatus.RUNNING, started_at=self.started_at),
            self.node(
                status=TaskStatus.COMPLETED,
                started_at=self.started_at,
                finished_at=self.finished_at,
                result=None,
            ),
            self.node(
                status=TaskStatus.FAILED,
                started_at=self.started_at,
                finished_at=self.finished_at,
                error="tool failed",
            ),
            self.node(
                status=TaskStatus.BLOCKED,
                error="dependency failed",
                blocked_by=["dependency-1"],
            ),
            self.node(
                status=TaskStatus.CANCELLED,
                finished_at=self.finished_at,
                error="cancelled by parent",
            ),
        ]

        self.assertEqual([node.status for node in nodes], list(TaskStatus))
        self.assertEqual(nodes[4].blocked_by, ("dependency-1",))
        with self.assertRaises(FrozenInstanceError):
            nodes[0].revision = 2

    def test_node_rejects_invalid_revision_time_and_state_fields(self) -> None:
        invalid_nodes = [
            {"revision": True},
            {"revision": 0},
            {"created_at": datetime(2026, 7, 25, 9, 0)},
            {"status": "pending"},
            {"status": TaskStatus.RUNNING},
            {
                "status": TaskStatus.COMPLETED,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": "unexpected",
            },
            {
                "status": TaskStatus.FAILED,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            },
            {"status": TaskStatus.BLOCKED},
            {"status": TaskStatus.CANCELLED},
            {"blocked_by": ["dependency-1"]},
        ]

        for overrides in invalid_nodes:
            with self.subTest(overrides=overrides), self.assertRaises(
                (TypeError, ValueError)
            ):
                self.node(**overrides)

    def test_errors_keep_fields_needed_to_locate_the_failure(self) -> None:
        not_found = TaskNotFoundError("task-1")
        exists = TaskAlreadyExistsError("task-2")
        transition = InvalidTaskTransition(
            "task-3",
            TaskStatus.PENDING,
            TaskStatus.COMPLETED,
            "must run first",
        )
        cycle = TaskDependencyCycle(["task-1", "task-2", "task-1"])
        not_ready = TaskDependencyNotReady(
            "task-3",
            "task-1",
            TaskStatus.RUNNING,
        )
        conflict = TaskConflictError("task-4", 2, 3)
        limit = TaskLimitExceeded("max_nodes", 100, 101)

        self.assertEqual(not_found.task_id, "task-1")
        self.assertEqual(exists.task_id, "task-2")
        self.assertEqual(transition.reason, "must run first")
        self.assertEqual(cycle.path, ("task-1", "task-2", "task-1"))
        self.assertEqual(not_ready.dependency_status, TaskStatus.RUNNING)
        self.assertEqual((conflict.expected_revision, conflict.actual_revision), (2, 3))
        self.assertEqual(
            (limit.limit_name, limit.limit, limit.attempted),
            ("max_nodes", 100, 101),
        )

    def test_s01_v_002_transition_table_accepts_only_planned_edges(self) -> None:
        nodes = {
            TaskStatus.PENDING: self.node(),
            TaskStatus.RUNNING: self.node(
                status=TaskStatus.RUNNING,
                started_at=self.started_at,
            ),
            TaskStatus.COMPLETED: self.node(
                status=TaskStatus.COMPLETED,
                started_at=self.started_at,
                finished_at=self.finished_at,
            ),
            TaskStatus.FAILED: self.node(
                status=TaskStatus.FAILED,
                started_at=self.started_at,
                finished_at=self.finished_at,
                error="tool failed",
            ),
            TaskStatus.BLOCKED: self.node(
                status=TaskStatus.BLOCKED,
                started_at=self.started_at,
                error="dependency failed",
            ),
            TaskStatus.CANCELLED: self.node(
                status=TaskStatus.CANCELLED,
                finished_at=self.finished_at,
            ),
        }
        allowed = {
            (TaskStatus.PENDING, TaskStatus.RUNNING),
            (TaskStatus.PENDING, TaskStatus.BLOCKED),
            (TaskStatus.PENDING, TaskStatus.CANCELLED),
            (TaskStatus.RUNNING, TaskStatus.COMPLETED),
            (TaskStatus.RUNNING, TaskStatus.FAILED),
            (TaskStatus.RUNNING, TaskStatus.BLOCKED),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED),
            (TaskStatus.BLOCKED, TaskStatus.PENDING),
            (TaskStatus.BLOCKED, TaskStatus.CANCELLED),
        }
        target_fields = {
            TaskStatus.PENDING: {},
            TaskStatus.RUNNING: {},
            TaskStatus.COMPLETED: {"result": None},
            TaskStatus.FAILED: {"error": "tool failed"},
            TaskStatus.BLOCKED: {"error": "dependency failed"},
            TaskStatus.CANCELLED: {},
        }

        for from_status in TaskStatus:
            for to_status in TaskStatus:
                with self.subTest(from_status=from_status, to_status=to_status):
                    node = nodes[from_status]
                    if (from_status, to_status) in allowed:
                        updated = transition_task_node(
                            node,
                            to_status,
                            now=self.finished_at,
                            **target_fields[to_status],
                        )
                        self.assertEqual(updated.status, to_status)
                        self.assertEqual(updated.revision, node.revision + 1)
                        self.assertEqual(node.status, from_status)
                        self.assertEqual(node.revision, 1)
                    else:
                        with self.assertRaises(InvalidTaskTransition):
                            transition_task_node(
                                node,
                                to_status,
                                now=self.finished_at,
                                **target_fields[to_status],
                            )

    def test_transition_preserves_first_start_and_clears_block_fields(self) -> None:
        running = transition_task_node(
            self.node(),
            TaskStatus.RUNNING,
            now=self.started_at,
        )
        blocked = transition_task_node(
            running,
            TaskStatus.BLOCKED,
            now=self.finished_at,
            error="dependency failed",
            blocked_by=["dependency-1"],
        )
        pending = transition_task_node(
            blocked,
            TaskStatus.PENDING,
            now=self.finished_at,
        )
        running_again = transition_task_node(
            pending,
            TaskStatus.RUNNING,
            now=self.finished_at,
        )

        self.assertEqual(running_again.started_at, self.started_at)
        self.assertIsNone(pending.error)
        self.assertEqual(pending.blocked_by, ())
        self.assertIsNone(pending.finished_at)
        self.assertEqual(running_again.revision, 5)

    def test_completed_requires_a_result_but_accepts_explicit_none(self) -> None:
        running = self.node(
            status=TaskStatus.RUNNING,
            started_at=self.started_at,
        )

        with self.assertRaises(InvalidTaskTransition):
            transition_task_node(
                running,
                TaskStatus.COMPLETED,
                now=self.finished_at,
            )

        completed = transition_task_node(
            running,
            TaskStatus.COMPLETED,
            now=self.finished_at,
            result=None,
        )

        self.assertIsNone(completed.result)
        self.assertEqual(completed.finished_at, self.finished_at)

    def test_transition_rejects_invalid_target_fields(self) -> None:
        pending = self.node()
        running = self.node(
            status=TaskStatus.RUNNING,
            started_at=self.started_at,
        )

        invalid_calls = [
            (pending, TaskStatus.RUNNING, {"result": "unexpected"}),
            (pending, TaskStatus.BLOCKED, {"error": ""}),
            (running, TaskStatus.FAILED, {"error": None}),
            (
                running,
                TaskStatus.COMPLETED,
                {"result": "done", "blocked_by": ["task-2"]},
            ),
        ]
        for node, to_status, fields in invalid_calls:
            with self.subTest(to_status=to_status, fields=fields), self.assertRaises(
                InvalidTaskTransition
            ):
                transition_task_node(
                    node,
                    to_status,
                    now=self.finished_at,
                    **fields,
                )

        with self.assertRaises(ValueError):
            transition_task_node(
                pending,
                TaskStatus.RUNNING,
                now=datetime(2026, 7, 25, 9, 2),
            )

    def test_s01_v_001_creates_minimal_frozen_utc_node(self) -> None:
        default_node = TaskStore().create(TaskSpec(summary="Default"))
        UUID(default_node.task_id)
        self.assertEqual(
            default_node.created_at.utcoffset(),
            timezone.utc.utcoffset(None),
        )

        generated_ids = iter(["task-1", "task-2", "task-3"])
        store = TaskStore(
            clock=lambda: self.created_at,
            id_factory=lambda: next(generated_ids),
        )

        first = store.create(TaskSpec(summary="First", owner_id="owner-1"))
        second = store.create(
            TaskSpec(
                summary="Second",
                task_id="chosen-id",
                owner_id="owner-2",
            )
        )
        third = store.create(
            TaskSpec(
                summary="Third",
                parent_id=first.task_id,
                dependency_ids=[second.task_id],
                owner_id="owner-1",
            )
        )

        self.assertEqual(first.task_id, "task-1")
        self.assertEqual(first.status, TaskStatus.PENDING)
        self.assertEqual(first.revision, 1)
        self.assertEqual(first.created_at, self.created_at)
        self.assertEqual(first.dependency_ids, ())
        self.assertEqual(first.blocked_by, ())
        with self.assertRaises(FrozenInstanceError):
            first.revision = 2
        self.assertIs(store.get(third.task_id), third)
        self.assertEqual(store.list(), (first, second, third))
        self.assertEqual(store.list(status=TaskStatus.PENDING), (first, second, third))
        self.assertEqual(store.list(owner_id="owner-1"), (first, third))

    def test_store_rejects_invalid_configuration_and_queries(self) -> None:
        invalid_configs = [
            {"max_nodes": True},
            {"max_nodes": 0},
            {"max_depth": True},
            {"max_depth": -1},
            {"clock": None},
            {"id_factory": None},
        ]
        for values in invalid_configs:
            with self.subTest(values=values), self.assertRaises(
                (TypeError, ValueError)
            ):
                TaskStore(**values)

        store = TaskStore()
        with self.assertRaises(TaskNotFoundError):
            store.get("missing")
        with self.assertRaises(ValueError):
            store.get("")
        with self.assertRaises(TypeError):
            store.list(status="pending")
        with self.assertRaises(ValueError):
            store.list(owner_id=" ")

    def test_s01_v_008b_missing_references_leave_store_unchanged(self) -> None:
        store = TaskStore(
            clock=lambda: self.created_at,
            id_factory=lambda: "generated-id",
        )
        existing = store.create(TaskSpec(summary="Existing", task_id="existing"))

        invalid_specs = [
            TaskSpec(summary="Duplicate", task_id="existing"),
            TaskSpec(summary="Missing parent", parent_id="missing"),
            TaskSpec(summary="Self parent", task_id="self", parent_id="self"),
            TaskSpec(summary="Missing dependency", dependency_ids=["missing"]),
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec), self.assertRaises(
                (TaskAlreadyExistsError, TaskNotFoundError, ValueError)
            ):
                store.create(spec)
            self.assertEqual(store.list(), (existing,))

        generated_self_dependency = TaskSpec(
            summary="Generated self dependency",
            dependency_ids=["generated-id"],
        )
        with self.assertRaises(ValueError):
            store.create(generated_self_dependency)
        self.assertEqual(store.list(), (existing,))

    def test_s01_v_008a_limits_and_clock_leave_store_unchanged(self) -> None:
        limited = TaskStore(
            max_nodes=1,
            clock=lambda: self.created_at,
        )
        only_node = limited.create(TaskSpec(summary="Only", task_id="only"))
        with self.assertRaises(TaskLimitExceeded) as capacity_error:
            limited.create(TaskSpec(summary="Extra", task_id="extra"))
        self.assertEqual(capacity_error.exception.limit_name, "max_nodes")
        self.assertEqual(limited.list(), (only_node,))

        depth_limited = TaskStore(
            max_depth=1,
            clock=lambda: self.created_at,
        )
        root = depth_limited.create(TaskSpec(summary="Root", task_id="root"))
        child = depth_limited.create(
            TaskSpec(summary="Child", task_id="child", parent_id="root")
        )
        with self.assertRaises(TaskLimitExceeded) as depth_error:
            depth_limited.create(
                TaskSpec(
                    summary="Grandchild",
                    task_id="grandchild",
                    parent_id="child",
                )
            )
        self.assertEqual(depth_error.exception.limit_name, "max_depth")
        self.assertEqual(depth_limited.list(), (root, child))

        invalid_clock = TaskStore(
            clock=lambda: datetime(2026, 7, 25, 9, 0),
        )
        with self.assertRaises(ValueError):
            invalid_clock.create(TaskSpec(summary="No UTC", task_id="no-utc"))
        self.assertEqual(invalid_clock.list(), ())

    def test_store_transition_supports_every_legal_edge(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        cases = [
            (TaskStatus.PENDING, TaskStatus.RUNNING, {}),
            (TaskStatus.PENDING, TaskStatus.BLOCKED, {"error": "waiting"}),
            (TaskStatus.PENDING, TaskStatus.CANCELLED, {}),
            (TaskStatus.RUNNING, TaskStatus.COMPLETED, {"result": None}),
            (TaskStatus.RUNNING, TaskStatus.FAILED, {"error": "failed"}),
            (TaskStatus.RUNNING, TaskStatus.BLOCKED, {"error": "waiting"}),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED, {}),
            (TaskStatus.BLOCKED, TaskStatus.PENDING, {}),
            (TaskStatus.BLOCKED, TaskStatus.CANCELLED, {}),
        ]

        for index, (from_status, to_status, fields) in enumerate(cases):
            with self.subTest(from_status=from_status, to_status=to_status):
                node = store.create(
                    TaskSpec(summary="Transition", task_id=f"task-{index}")
                )
                if from_status is TaskStatus.RUNNING:
                    node = store.transition(
                        node.task_id,
                        expected_revision=node.revision,
                        to_status=TaskStatus.RUNNING,
                    )
                elif from_status is TaskStatus.BLOCKED:
                    node = store.transition(
                        node.task_id,
                        expected_revision=node.revision,
                        to_status=TaskStatus.BLOCKED,
                        error="waiting",
                    )

                updated = store.transition(
                    node.task_id,
                    expected_revision=node.revision,
                    to_status=to_status,
                    **fields,
                )
                self.assertEqual(updated.status, to_status)
                self.assertEqual(updated.revision, node.revision + 1)
                self.assertIs(store.get(node.task_id), updated)

    def test_store_transition_rejects_bad_revision_without_writing(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        node = store.create(TaskSpec(summary="CAS", task_id="cas"))

        invalid_revisions = [True, 0]
        for revision in invalid_revisions:
            with self.subTest(revision=revision), self.assertRaises(
                (TypeError, ValueError)
            ):
                store.transition(
                    node.task_id,
                    expected_revision=revision,
                    to_status=TaskStatus.RUNNING,
                )
            self.assertIs(store.get(node.task_id), node)

        for revision in [2, 100]:
            with self.subTest(revision=revision), self.assertRaises(
                TaskConflictError
            ):
                store.transition(
                    node.task_id,
                    expected_revision=revision,
                    to_status=TaskStatus.RUNNING,
                )
            self.assertIs(store.get(node.task_id), node)

        running = store.transition(
            node.task_id,
            expected_revision=1,
            to_status=TaskStatus.RUNNING,
        )
        with self.assertRaises(TaskConflictError):
            store.transition(
                node.task_id,
                expected_revision=1,
                to_status=TaskStatus.CANCELLED,
            )
        self.assertIs(store.get(node.task_id), running)

    def test_store_requires_completed_dependencies_before_running(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        dependencies: list[TaskNode] = []
        for status in [
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        ]:
            dependency = store.create(
                TaskSpec(
                    summary="Dependency",
                    task_id=f"dependency-{status.value}",
                )
            )
            if status is TaskStatus.RUNNING:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=1,
                    to_status=status,
                )
            elif status is TaskStatus.FAILED:
                running = store.transition(
                    dependency.task_id,
                    expected_revision=1,
                    to_status=TaskStatus.RUNNING,
                )
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=running.revision,
                    to_status=status,
                    error="failed",
                )
            elif status is TaskStatus.BLOCKED:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=1,
                    to_status=status,
                    error="waiting",
                )
            elif status is TaskStatus.CANCELLED:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=1,
                    to_status=status,
                )
            dependencies.append(dependency)

        for dependency in dependencies:
            target = store.create(
                TaskSpec(
                    summary="Target",
                    task_id=f"target-{dependency.status.value}",
                    dependency_ids=[dependency.task_id],
                )
            )
            with self.subTest(status=dependency.status), self.assertRaises(
                TaskDependencyNotReady
            ) as raised:
                store.transition(
                    target.task_id,
                    expected_revision=1,
                    to_status=TaskStatus.RUNNING,
                )
            self.assertEqual(raised.exception.dependency_id, dependency.task_id)
            self.assertEqual(raised.exception.dependency_status, dependency.status)
            self.assertIs(store.get(target.task_id), target)

        completed = store.create(
            TaskSpec(summary="Completed dependency", task_id="completed")
        )
        completed = store.transition(
            completed.task_id,
            expected_revision=1,
            to_status=TaskStatus.RUNNING,
        )
        completed = store.transition(
            completed.task_id,
            expected_revision=completed.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        target = store.create(
            TaskSpec(
                summary="Ready target",
                task_id="ready-target",
                dependency_ids=[completed.task_id],
            )
        )
        running = store.transition(
            target.task_id,
            expected_revision=1,
            to_status=TaskStatus.RUNNING,
        )
        self.assertEqual(running.status, TaskStatus.RUNNING)

    def test_s01_v_007_same_revision_concurrency_commits_once(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        node = store.create(TaskSpec(summary="Concurrent", task_id="concurrent"))
        barrier = Barrier(3)
        outcomes: list[TaskNode | BaseException] = []

        def update() -> None:
            barrier.wait()
            try:
                outcomes.append(
                    store.transition(
                        node.task_id,
                        expected_revision=node.revision,
                        to_status=TaskStatus.RUNNING,
                    )
                )
            except BaseException as exc:
                outcomes.append(exc)

        threads = [Thread(target=update), Thread(target=update)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(isinstance(item, TaskNode) for item in outcomes), 1)
        self.assertEqual(
            sum(isinstance(item, TaskConflictError) for item in outcomes),
            1,
        )
        updated = store.get(node.task_id)
        self.assertEqual(updated.status, TaskStatus.RUNNING)
        self.assertEqual(updated.revision, 2)

    def test_store_adds_dependency_to_pending_and_blocked_nodes(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        dependency = store.create(
            TaskSpec(summary="Dependency", task_id="dependency")
        )
        pending = store.create(TaskSpec(summary="Pending", task_id="pending"))

        updated = store.add_dependency(
            pending.task_id,
            dependency.task_id,
            expected_revision=pending.revision,
        )

        self.assertEqual(updated.dependency_ids, (dependency.task_id,))
        self.assertEqual(updated.revision, 2)
        self.assertIs(store.get(pending.task_id), updated)
        self.assertIs(store.get(dependency.task_id), dependency)

        blocked = store.create(TaskSpec(summary="Blocked", task_id="blocked"))
        blocked = store.transition(
            blocked.task_id,
            expected_revision=blocked.revision,
            to_status=TaskStatus.BLOCKED,
            error="waiting",
        )
        blocked_updated = store.add_dependency(
            blocked.task_id,
            dependency.task_id,
            expected_revision=blocked.revision,
        )
        self.assertEqual(blocked_updated.status, TaskStatus.BLOCKED)
        self.assertEqual(blocked_updated.dependency_ids, (dependency.task_id,))
        self.assertEqual(blocked_updated.revision, 3)

    def test_store_add_dependency_rejects_invalid_inputs_without_writing(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        dependency = store.create(
            TaskSpec(summary="Dependency", task_id="dependency")
        )
        target = store.create(TaskSpec(summary="Target", task_id="target"))

        invalid_calls = [
            ("missing", dependency.task_id, 1, TaskNotFoundError),
            (target.task_id, "missing", 1, TaskNotFoundError),
            (target.task_id, target.task_id, 1, ValueError),
            (target.task_id, dependency.task_id, True, TypeError),
            (target.task_id, dependency.task_id, 0, ValueError),
            (target.task_id, dependency.task_id, 2, TaskConflictError),
        ]
        for task_id, dependency_id, revision, error_type in invalid_calls:
            with self.subTest(
                task_id=task_id,
                dependency_id=dependency_id,
                revision=revision,
            ), self.assertRaises(error_type):
                store.add_dependency(
                    task_id,
                    dependency_id,
                    expected_revision=revision,
                )
            self.assertIs(store.get(target.task_id), target)
            self.assertIs(store.get(dependency.task_id), dependency)

        target = store.add_dependency(
            target.task_id,
            dependency.task_id,
            expected_revision=target.revision,
        )
        with self.assertRaises(ValueError):
            store.add_dependency(
                target.task_id,
                dependency.task_id,
                expected_revision=target.revision,
            )
        self.assertIs(store.get(target.task_id), target)

    def test_store_add_dependency_rejects_running_and_terminal_tasks(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        dependency = store.create(
            TaskSpec(summary="Dependency", task_id="dependency")
        )
        cases = [
            (TaskStatus.RUNNING, {}),
            (TaskStatus.COMPLETED, {"result": None}),
            (TaskStatus.FAILED, {"error": "failed"}),
            (TaskStatus.CANCELLED, {}),
        ]

        for index, (status, fields) in enumerate(cases):
            node = store.create(
                TaskSpec(summary="Target", task_id=f"target-{index}")
            )
            if status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                node = store.transition(
                    node.task_id,
                    expected_revision=node.revision,
                    to_status=TaskStatus.RUNNING,
                )
            node = store.transition(
                node.task_id,
                expected_revision=node.revision,
                to_status=status,
                **fields,
            )
            with self.subTest(status=status), self.assertRaises(ValueError):
                store.add_dependency(
                    node.task_id,
                    dependency.task_id,
                    expected_revision=node.revision,
                )
            self.assertIs(store.get(node.task_id), node)

    def test_s01_v_004_dependency_cycles_leave_graph_unchanged(self) -> None:
        two_node_store = TaskStore(clock=lambda: self.finished_at)
        first = two_node_store.create(TaskSpec(summary="First", task_id="A"))
        second = two_node_store.create(TaskSpec(summary="Second", task_id="B"))
        second = two_node_store.add_dependency(
            second.task_id,
            first.task_id,
            expected_revision=second.revision,
        )

        with self.assertRaises(TaskDependencyCycle) as two_node_error:
            two_node_store.add_dependency(
                first.task_id,
                second.task_id,
                expected_revision=first.revision,
            )
        self.assertEqual(two_node_error.exception.path, ("A", "B", "A"))
        self.assertIs(two_node_store.get(first.task_id), first)
        self.assertIs(two_node_store.get(second.task_id), second)

        three_node_store = TaskStore(clock=lambda: self.finished_at)
        first = three_node_store.create(TaskSpec(summary="First", task_id="A"))
        second = three_node_store.create(
            TaskSpec(summary="Second", task_id="B", dependency_ids=["A"])
        )
        third = three_node_store.create(
            TaskSpec(summary="Third", task_id="C", dependency_ids=["B"])
        )

        with self.assertRaises(TaskDependencyCycle) as three_node_error:
            three_node_store.add_dependency(
                first.task_id,
                third.task_id,
                expected_revision=first.revision,
            )
        self.assertEqual(
            three_node_error.exception.path,
            ("A", "C", "B", "A"),
        )
        self.assertIs(three_node_store.get(first.task_id), first)
        self.assertIs(three_node_store.get(second.task_id), second)
        self.assertIs(three_node_store.get(third.task_id), third)

    def test_store_keeps_parent_tree_out_of_dependency_cycle_checks(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        parent = store.create(TaskSpec(summary="Parent", task_id="parent"))
        first = store.create(
            TaskSpec(summary="First", task_id="first", parent_id=parent.task_id)
        )
        second = store.create(
            TaskSpec(summary="Second", task_id="second", parent_id=parent.task_id)
        )
        shared = store.create(TaskSpec(summary="Shared", task_id="shared"))

        first = store.add_dependency(
            first.task_id,
            shared.task_id,
            expected_revision=first.revision,
        )
        second = store.add_dependency(
            second.task_id,
            shared.task_id,
            expected_revision=second.revision,
        )
        first = store.add_dependency(
            first.task_id,
            second.task_id,
            expected_revision=first.revision,
        )

        self.assertEqual(first.dependency_ids, ("shared", "second"))
        self.assertEqual(second.dependency_ids, ("shared",))
        self.assertEqual(first.parent_id, parent.task_id)
        self.assertEqual(second.parent_id, parent.task_id)

    def test_s01_v_003_ready_returns_only_runnable_pending_nodes(
        self,
    ) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        no_dependency = store.create(
            TaskSpec(summary="No dependency", task_id="no-dependency")
        )
        parent = store.create(TaskSpec(summary="Parent", task_id="parent"))
        child = store.create(
            TaskSpec(summary="Child", task_id="child", parent_id=parent.task_id)
        )
        store.transition(
            parent.task_id,
            expected_revision=parent.revision,
            to_status=TaskStatus.CANCELLED,
        )

        dependency_nodes: dict[TaskStatus, TaskNode] = {}
        for status in TaskStatus:
            dependency = store.create(
                TaskSpec(
                    summary="Dependency",
                    task_id=f"dependency-{status.value}",
                )
            )
            if status is TaskStatus.RUNNING:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=status,
                )
            elif status is TaskStatus.COMPLETED:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=TaskStatus.RUNNING,
                )
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=status,
                    result="done",
                )
            elif status is TaskStatus.FAILED:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=TaskStatus.RUNNING,
                )
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=status,
                    error="failed",
                )
            elif status is TaskStatus.BLOCKED:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=status,
                    error="waiting",
                )
            elif status is TaskStatus.CANCELLED:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=status,
                )
            dependency_nodes[status] = dependency

        targets = {
            status: store.create(
                TaskSpec(
                    summary="Target",
                    task_id=f"target-{status.value}",
                    dependency_ids=[dependency.task_id],
                )
            )
            for status, dependency in dependency_nodes.items()
        }
        running_without_dependencies = store.create(
            TaskSpec(summary="Running", task_id="running")
        )
        store.transition(
            running_without_dependencies.task_id,
            expected_revision=running_without_dependencies.revision,
            to_status=TaskStatus.RUNNING,
        )
        before = store.list()

        ready = store.ready()

        self.assertEqual(
            ready,
            (
                no_dependency,
                dependency_nodes[TaskStatus.PENDING],
                targets[TaskStatus.COMPLETED],
            ),
        )
        self.assertEqual(
            store.get(child.task_id).status,
            TaskStatus.CANCELLED,
        )
        self.assertEqual(store.list(), before)

    def test_store_blocking_chain_is_breadth_first_and_creation_ordered(
        self,
    ) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        leaf_a = store.create(TaskSpec(summary="Leaf A", task_id="leaf-a"))
        leaf_b = store.create(TaskSpec(summary="Leaf B", task_id="leaf-b"))
        middle_a = store.create(TaskSpec(summary="Middle A", task_id="middle-a"))
        middle_b = store.create(TaskSpec(summary="Middle B", task_id="middle-b"))
        target = store.create(TaskSpec(summary="Target", task_id="target"))

        leaf_a = transition_task_node(
            leaf_a,
            TaskStatus.BLOCKED,
            now=self.finished_at,
            error="cycle guard",
            blocked_by=[target.task_id],
        )
        middle_a = transition_task_node(
            middle_a,
            TaskStatus.BLOCKED,
            now=self.finished_at,
            error="blocked",
            blocked_by=[leaf_b.task_id],
        )
        middle_b = transition_task_node(
            middle_b,
            TaskStatus.BLOCKED,
            now=self.finished_at,
            error="blocked",
            blocked_by=[leaf_a.task_id],
        )
        target = transition_task_node(
            target,
            TaskStatus.BLOCKED,
            now=self.finished_at,
            error="blocked",
            blocked_by=[middle_b.task_id, middle_a.task_id],
        )
        store._nodes.update(
            {
                node.task_id: node
                for node in (leaf_a, middle_a, middle_b, target)
            }
        )

        self.assertEqual(
            store.blocking_chain(target.task_id),
            ("middle-a", "middle-b", "leaf-a", "leaf-b"),
        )

    def test_store_blocking_chain_rejects_missing_blocker(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        node = store.create(TaskSpec(summary="Blocked", task_id="blocked"))
        node = transition_task_node(
            node,
            TaskStatus.BLOCKED,
            now=self.finished_at,
            error="missing blocker",
            blocked_by=["missing"],
        )
        store._nodes[node.task_id] = node

        with self.assertRaises(TaskNotFoundError) as raised:
            store.blocking_chain(node.task_id)
        self.assertEqual(raised.exception.task_id, "missing")

    def test_store_mark_owner_missing_blocks_pending_and_running_only(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        pending = store.create(
            TaskSpec(summary="Pending", task_id="pending", owner_id="worker-1")
        )
        running = store.create(
            TaskSpec(summary="Running", task_id="running", owner_id="worker-1")
        )
        running = store.transition(
            running.task_id,
            expected_revision=running.revision,
            to_status=TaskStatus.RUNNING,
        )
        blocked = store.create(
            TaskSpec(summary="Blocked", task_id="blocked", owner_id="worker-1")
        )
        blocked = store.transition(
            blocked.task_id,
            expected_revision=blocked.revision,
            to_status=TaskStatus.BLOCKED,
            error="already blocked",
        )
        completed = store.create(
            TaskSpec(summary="Completed", task_id="completed", owner_id="worker-1")
        )
        completed = store.transition(
            completed.task_id,
            expected_revision=completed.revision,
            to_status=TaskStatus.RUNNING,
        )
        completed = store.transition(
            completed.task_id,
            expected_revision=completed.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        other_owner = store.create(
            TaskSpec(summary="Other", task_id="other", owner_id="worker-2")
        )

        updated = store.mark_owner_missing("worker-1")

        self.assertEqual(
            tuple(node.task_id for node in updated),
            ("pending", "running"),
        )
        self.assertTrue(all(node.status is TaskStatus.BLOCKED for node in updated))
        self.assertTrue(
            all(node.error == "owner 'worker-1' is missing" for node in updated)
        )
        self.assertTrue(all(node.blocked_by == () for node in updated))
        self.assertEqual(updated[0].revision, pending.revision + 1)
        self.assertEqual(updated[1].revision, running.revision + 1)
        self.assertEqual(updated[1].started_at, running.started_at)
        self.assertIs(store.get(blocked.task_id), blocked)
        self.assertIs(store.get(completed.task_id), completed)
        self.assertIs(store.get(other_owner.task_id), other_owner)
        self.assertEqual(store.blocking_chain(updated[0].task_id), ())

    def test_store_mark_owner_missing_empty_and_failed_batches_do_not_write(
        self,
    ) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        first = store.create(
            TaskSpec(summary="First", task_id="first", owner_id="worker-1")
        )
        second = store.create(
            TaskSpec(summary="Second", task_id="second", owner_id="worker-1")
        )

        self.assertEqual(store.mark_owner_missing("missing-owner"), ())
        self.assertIs(store.get(first.task_id), first)
        self.assertIs(store.get(second.task_id), second)

        store._clock = lambda: datetime(2026, 7, 26, 9, 0)
        with self.assertRaises(ValueError):
            store.mark_owner_missing("worker-1")
        self.assertIs(store.get(first.task_id), first)
        self.assertIs(store.get(second.task_id), second)

        with self.assertRaises(ValueError):
            store.mark_owner_missing(" ")

    def test_s01_v_006a_negative_dependencies_block_downstream(self) -> None:
        cases = [
            (TaskStatus.FAILED, {"error": "boom"}),
            (TaskStatus.BLOCKED, {"error": "waiting"}),
            (TaskStatus.CANCELLED, {}),
        ]
        for status, fields in cases:
            with self.subTest(status=status):
                store = TaskStore(clock=lambda: self.finished_at)
                root = store.create(
                    TaskSpec(summary="Root", task_id="root")
                )
                middle = store.create(
                    TaskSpec(
                        summary="Middle",
                        task_id="middle",
                        dependency_ids=[root.task_id],
                    )
                )
                leaf = store.create(
                    TaskSpec(
                        summary="Leaf",
                        task_id="leaf",
                        dependency_ids=[middle.task_id],
                    )
                )
                if status is TaskStatus.FAILED:
                    root = store.transition(
                        root.task_id,
                        expected_revision=root.revision,
                        to_status=TaskStatus.RUNNING,
                    )

                changed = store.transition(
                    root.task_id,
                    expected_revision=root.revision,
                    to_status=status,
                    **fields,
                )

                current_middle = store.get(middle.task_id)
                current_leaf = store.get(leaf.task_id)
                self.assertEqual(changed.status, status)
                self.assertEqual(current_middle.status, TaskStatus.BLOCKED)
                self.assertEqual(current_leaf.status, TaskStatus.BLOCKED)
                self.assertEqual(current_middle.blocked_by, ("root",))
                self.assertEqual(current_leaf.blocked_by, ("middle",))
                self.assertEqual(
                    store.blocking_chain(leaf.task_id),
                    ("middle", "root"),
                )
                self.assertEqual(
                    current_middle.revision,
                    middle.revision + 1,
                )
                self.assertEqual(current_leaf.revision, leaf.revision + 1)

    def test_store_merges_blockers_without_double_revision(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        first = store.create(
            TaskSpec(
                summary="First",
                task_id="first",
                parent_id=root.task_id,
            )
        )
        second = store.create(
            TaskSpec(
                summary="Second",
                task_id="second",
                parent_id=root.task_id,
            )
        )
        target = store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                dependency_ids=[second.task_id, first.task_id],
            )
        )
        target = store.transition(
            target.task_id,
            expected_revision=target.revision,
            to_status=TaskStatus.BLOCKED,
            error="manual pause",
        )

        store.transition(
            root.task_id,
            expected_revision=root.revision,
            to_status=TaskStatus.CANCELLED,
        )

        changed = store.get(target.task_id)
        self.assertEqual(changed.status, TaskStatus.BLOCKED)
        self.assertEqual(changed.blocked_by, ("second", "first"))
        self.assertEqual(changed.revision, target.revision + 1)
        self.assertEqual(changed.error, "manual pause")
        self.assertEqual(
            store.blocking_chain(target.task_id),
            ("first", "second"),
        )

    def test_s01_v_005_parent_cancel_is_breadth_first_and_terminal_safe(
        self,
    ) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        first = store.create(
            TaskSpec(
                summary="First",
                task_id="first",
                parent_id=root.task_id,
                dependency_ids=[root.task_id],
            )
        )
        grandchild = store.create(
            TaskSpec(
                summary="Grandchild",
                task_id="grandchild",
                parent_id=first.task_id,
            )
        )
        completed = store.create(
            TaskSpec(
                summary="Completed",
                task_id="completed",
                parent_id=root.task_id,
            )
        )
        completed = store.transition(
            completed.task_id,
            expected_revision=completed.revision,
            to_status=TaskStatus.RUNNING,
        )
        completed = store.transition(
            completed.task_id,
            expected_revision=completed.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        below_completed = store.create(
            TaskSpec(
                summary="Below completed",
                task_id="below-completed",
                parent_id=completed.task_id,
            )
        )
        event_count = len(store.events())
        store.transition(
            root.task_id,
            expected_revision=root.revision,
            to_status=TaskStatus.CANCELLED,
        )
        written = [
            event.task_id for event in store.events()[event_count:]
        ]

        self.assertEqual(
            written,
            ["root", "first", "grandchild", "below-completed"],
        )
        self.assertEqual(store.get(first.task_id).status, TaskStatus.CANCELLED)
        self.assertEqual(
            store.get(grandchild.task_id).status,
            TaskStatus.CANCELLED,
        )
        self.assertIs(store.get(completed.task_id), completed)
        self.assertEqual(
            store.get(below_completed.task_id).status,
            TaskStatus.CANCELLED,
        )
        self.assertEqual(store.get(first.task_id).revision, first.revision + 1)
        self.assertEqual(
            store.get(grandchild.task_id).revision,
            grandchild.revision + 1,
        )

    def test_store_parent_cancellation_wins_over_dependency_blocking(
        self,
    ) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        child = store.create(
            TaskSpec(
                summary="Child",
                task_id="child",
                parent_id=root.task_id,
                dependency_ids=[root.task_id],
            )
        )

        store.transition(
            root.task_id,
            expected_revision=root.revision,
            to_status=TaskStatus.CANCELLED,
        )

        changed = store.get(child.task_id)
        self.assertEqual(changed.status, TaskStatus.CANCELLED)
        self.assertEqual(changed.blocked_by, ())
        self.assertEqual(changed.revision, child.revision + 1)

    def test_store_cascade_precompute_failure_writes_nothing(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        root = store.create(TaskSpec(summary="Root", task_id="root"))
        root = store.transition(
            root.task_id,
            expected_revision=root.revision,
            to_status=TaskStatus.RUNNING,
        )
        corrupt = TaskNode(
            task_id="corrupt",
            summary="Corrupt",
            status=TaskStatus.PENDING,
            revision=1,
            created_at=self.created_at,
            dependency_ids=(root.task_id, "missing"),
        )
        store._nodes[corrupt.task_id] = corrupt

        with self.assertRaises(TaskNotFoundError):
            store.transition(
                root.task_id,
                expected_revision=root.revision,
                to_status=TaskStatus.FAILED,
                error="boom",
            )

        self.assertIs(store.get(root.task_id), root)
        self.assertIs(store.get(corrupt.task_id), corrupt)

    def test_store_checks_dependencies_before_unblocking(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        dependency = store.create(
            TaskSpec(summary="Dependency", task_id="dependency")
        )
        target = store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                dependency_ids=[dependency.task_id],
            )
        )
        target = store.transition(
            target.task_id,
            expected_revision=target.revision,
            to_status=TaskStatus.BLOCKED,
            error="waiting",
        )

        with self.assertRaises(TaskDependencyNotReady):
            store.transition(
                target.task_id,
                expected_revision=target.revision,
                to_status=TaskStatus.PENDING,
            )
        self.assertIs(store.get(target.task_id), target)

        dependency = store.transition(
            dependency.task_id,
            expected_revision=dependency.revision,
            to_status=TaskStatus.RUNNING,
        )
        store.transition(
            dependency.task_id,
            expected_revision=dependency.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        self.assertIs(store.get(target.task_id), target)

        recovered = store.transition(
            target.task_id,
            expected_revision=target.revision,
            to_status=TaskStatus.PENDING,
        )
        self.assertEqual(recovered.status, TaskStatus.PENDING)
        self.assertIsNone(recovered.error)
        self.assertEqual(recovered.blocked_by, ())

    def test_s01_v_006b_owner_missing_blocks_dependency_chain(self) -> None:
        store = TaskStore(clock=lambda: self.finished_at)
        owned = store.create(
            TaskSpec(
                summary="Owned",
                task_id="owned",
                owner_id="worker-1",
            )
        )
        middle = store.create(
            TaskSpec(
                summary="Middle",
                task_id="middle",
                dependency_ids=[owned.task_id],
            )
        )
        leaf = store.create(
            TaskSpec(
                summary="Leaf",
                task_id="leaf",
                dependency_ids=[middle.task_id],
            )
        )

        changed = store.mark_owner_missing("worker-1")

        self.assertEqual(tuple(node.task_id for node in changed), ("owned",))
        self.assertEqual(store.get(owned.task_id).status, TaskStatus.BLOCKED)
        self.assertEqual(store.get(middle.task_id).blocked_by, ("owned",))
        self.assertEqual(store.get(leaf.task_id).blocked_by, ("middle",))
        self.assertEqual(
            store.blocking_chain(leaf.task_id),
            ("middle", "owned"),
        )


if __name__ == "__main__":
    unittest.main()
