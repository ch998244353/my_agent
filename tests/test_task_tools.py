from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from agents.task_events import task_node_to_dict
from agents import (
    Agent,
    AgentMemory,
    ModelResponse,
    Runner,
    ToolCall,
    ToolCompletedEvent,
    ToolExecutionError,
    ToolFailedEvent,
    ToolRequestedEvent,
    ToolStartedEvent,
)
from agents.task_state import (
    InvalidTaskTransition,
    TaskConflictError,
    TaskDependencyNotReady,
    TaskEvent,
    TaskLimitExceeded,
    TaskNotFoundError,
    TaskSpec,
    TaskStatus,
    TaskStore,
)
from agents.task_tools import (
    _is_in_root_scope,
    _require_owned_task,
    create_task_tools,
)
from agents.tools import ToolRegistry


class PlanningResponseModel:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.messages_seen = []
        self.tool_specs_seen = []
        self.recorded_tool_outputs = []

    def get_response(self, messages, tool_specs):
        self.messages_seen.append(list(messages))
        self.tool_specs_seen.append(list(tool_specs))
        return next(self.responses)

    def record_tool_output(self, tool_call, output) -> None:
        self.recorded_tool_outputs.append((tool_call, output))


class TaskToolScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        self.store = TaskStore(clock=lambda: now)
        self.root = self.store.create(
            TaskSpec(
                summary="Root",
                task_id="root",
                owner_id="supervisor",
            )
        )
        self.child = self.store.create(
            TaskSpec(
                summary="Child",
                task_id="child",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.grandchild = self.store.create(
            TaskSpec(
                summary="Grandchild",
                task_id="grandchild",
                parent_id=self.child.task_id,
                owner_id="planner",
            )
        )
        self.other_owner = self.store.create(
            TaskSpec(
                summary="Other owner",
                task_id="other-owner",
                parent_id=self.root.task_id,
                owner_id="other",
            )
        )
        self.external_root = self.store.create(
            TaskSpec(
                summary="External root",
                task_id="external-root",
                owner_id="planner",
            )
        )
        self.external_child = self.store.create(
            TaskSpec(
                summary="External child",
                task_id="external-child",
                parent_id=self.external_root.task_id,
                owner_id="planner",
            )
        )

    def test_root_child_and_grandchild_are_in_scope(self) -> None:
        for task_id in (
            self.root.task_id,
            self.child.task_id,
            self.grandchild.task_id,
        ):
            with self.subTest(task_id=task_id):
                self.assertTrue(
                    _is_in_root_scope(self.store, self.root.task_id, task_id)
                )

    def test_other_tree_is_out_of_scope(self) -> None:
        for task_id in (
            self.external_root.task_id,
            self.external_child.task_id,
        ):
            with self.subTest(task_id=task_id):
                self.assertFalse(
                    _is_in_root_scope(self.store, self.root.task_id, task_id)
                )

    def test_require_owned_task_returns_scoped_owner_node(self) -> None:
        node = _require_owned_task(
            self.store,
            root_task_id=self.root.task_id,
            owner_id="planner",
            task_id=self.grandchild.task_id,
        )

        self.assertEqual(node, self.grandchild)

    def test_require_owned_task_rejects_outside_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside root task"):
            _require_owned_task(
                self.store,
                root_task_id=self.root.task_id,
                owner_id="planner",
                task_id=self.external_child.task_id,
            )

    def test_require_owned_task_rejects_owner_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "is owned by"):
            _require_owned_task(
                self.store,
                root_task_id=self.root.task_id,
                owner_id="planner",
                task_id=self.other_owner.task_id,
            )

    def test_missing_task_uses_store_error(self) -> None:
        with self.assertRaises(TaskNotFoundError):
            _is_in_root_scope(self.store, self.root.task_id, "missing")

    def test_checks_and_existing_projection_are_read_only(self) -> None:
        events_before = self.store.events()

        self.assertTrue(
            _is_in_root_scope(
                self.store,
                self.root.task_id,
                self.child.task_id,
            )
        )
        node = _require_owned_task(
            self.store,
            root_task_id=self.root.task_id,
            owner_id="planner",
            task_id=self.child.task_id,
        )
        json.dumps(task_node_to_dict(node))

        self.assertEqual(self.store.events(), events_before)


class CreateTaskToolTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime(2026, 7, 28, tzinfo=timezone.utc)
        self.store = TaskStore(
            clock=lambda: now,
            id_factory=lambda: "created-task",
        )
        self.root = self.store.create(
            TaskSpec(
                summary="Root",
                task_id="root",
                owner_id="supervisor",
            )
        )
        self.parent = self.store.create(
            TaskSpec(
                summary="Parent",
                task_id="parent",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.dependency = self.store.create(
            TaskSpec(
                summary="Dependency",
                task_id="dependency",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.external_root = self.store.create(
            TaskSpec(
                summary="External root",
                task_id="external-root",
                owner_id="planner",
            )
        )
        self.external_child = self.store.create(
            TaskSpec(
                summary="External child",
                task_id="external-child",
                parent_id=self.external_root.task_id,
                owner_id="planner",
            )
        )
        self.nodes_before_factory = self.store.list()
        self.events_before_factory = self.store.events()
        self.tools = create_task_tools(
            self.store,
            root_task_id=self.root.task_id,
            owner_id="planner",
        )
        self.tool = self.tools[0]

    def assert_tool_failure_leaves_store(
        self,
        arguments: dict[str, object],
        cause_type: type[BaseException],
    ) -> None:
        nodes_before = self.store.list()
        events_before = self.store.events()

        with self.assertRaises(ToolExecutionError) as raised:
            self.tool.execute(arguments)

        self.assertIsInstance(raised.exception.__cause__, cause_type)
        self.assertEqual(self.store.list(), nodes_before)
        self.assertEqual(self.store.events(), events_before)

    def test_factory_is_read_only_and_schema_is_minimal(self) -> None:
        self.assertEqual(self.store.list(), self.nodes_before_factory)
        self.assertEqual(self.store.events(), self.events_before_factory)
        self.assertEqual(
            [tool.name for tool in self.tools],
            ["create_task", "update_task", "list_tasks"],
        )
        self.assertEqual(
            self.tool.spec.argument_names(),
            ["summary", "dependency_ids", "parent_id"],
        )
        self.assertNotIn("owner_id", self.tool.spec.argument_names())
        self.assertNotIn("task_id", self.tool.spec.argument_names())

        arguments = {
            argument.name: argument
            for argument in self.tool.spec.arguments
        }
        self.assertTrue(arguments["summary"].required)
        self.assertFalse(arguments["dependency_ids"].required)
        self.assertFalse(arguments["parent_id"].required)

    def test_registry_creates_default_child_with_bound_owner(self) -> None:
        registry = ToolRegistry()
        registry.register(self.tool)
        node_count = len(self.store.list())
        event_count = len(self.store.events())

        output = registry.execute(
            "create_task",
            {"summary": "Created by tool"},
        )

        node = self.store.get("created-task")
        self.assertEqual(len(self.store.list()), node_count + 1)
        self.assertEqual(len(self.store.events()), event_count + 1)
        self.assertEqual(node.parent_id, self.root.task_id)
        self.assertEqual(node.owner_id, "planner")
        self.assertEqual(output, task_node_to_dict(node))
        self.assertEqual(self.store.events()[-1].reason, "created")

    def test_explicit_parent_and_dependencies_stay_in_scope(self) -> None:
        output = self.tool.execute(
            {
                "summary": "Nested task",
                "parent_id": self.parent.task_id,
                "dependency_ids": [self.dependency.task_id],
            }
        )

        node = self.store.get(output["task_id"])
        self.assertEqual(node.parent_id, self.parent.task_id)
        self.assertEqual(
            node.dependency_ids,
            (self.dependency.task_id,),
        )
        self.assertEqual(node.owner_id, "planner")

    def test_scope_failures_leave_store_unchanged(self) -> None:
        cases = (
            (
                {
                    "summary": "Outside parent",
                    "parent_id": self.external_child.task_id,
                },
                ValueError,
            ),
            (
                {
                    "summary": "Outside dependency",
                    "dependency_ids": [self.external_child.task_id],
                },
                ValueError,
            ),
            (
                {
                    "summary": "Missing dependency",
                    "dependency_ids": ["missing"],
                },
                TaskNotFoundError,
            ),
        )
        for arguments, cause_type in cases:
            with self.subTest(arguments=arguments):
                self.assert_tool_failure_leaves_store(
                    arguments,
                    cause_type,
                )

    def test_task_spec_failures_leave_store_unchanged(self) -> None:
        cases = (
            {"summary": "  "},
            {
                "summary": "Duplicate dependency",
                "dependency_ids": [
                    self.dependency.task_id,
                    self.dependency.task_id,
                ],
            },
            {
                "summary": "Empty dependency",
                "dependency_ids": [""],
            },
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assert_tool_failure_leaves_store(
                    arguments,
                    ValueError,
                )

    def test_store_limits_leave_store_unchanged(self) -> None:
        for store in (
            TaskStore(
                max_nodes=1,
                id_factory=lambda: "over-limit",
            ),
            TaskStore(
                max_depth=0,
                id_factory=lambda: "too-deep",
            ),
        ):
            root = store.create(
                TaskSpec(summary="Root", task_id="root")
            )
            tool = create_task_tools(
                store,
                root_task_id=root.task_id,
                owner_id="planner",
            )[0]
            nodes_before = store.list()
            events_before = store.events()

            with self.subTest(store=store):
                with self.assertRaises(ToolExecutionError) as raised:
                    tool.execute({"summary": "Rejected task"})
                self.assertIsInstance(
                    raised.exception.__cause__,
                    TaskLimitExceeded,
                )
                self.assertEqual(store.list(), nodes_before)
                self.assertEqual(store.events(), events_before)

    def test_factory_rejects_invalid_configuration(self) -> None:
        with self.assertRaises(TypeError):
            create_task_tools(
                object(),
                root_task_id=self.root.task_id,
                owner_id="planner",
            )
        with self.assertRaises(TaskNotFoundError):
            create_task_tools(
                self.store,
                root_task_id="missing",
                owner_id="planner",
            )
        with self.assertRaises(ValueError):
            create_task_tools(
                self.store,
                root_task_id=self.root.task_id,
                owner_id="  ",
            )


class UpdateTaskToolTests(unittest.TestCase):
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)

    def make_case(
        self,
        source_status: TaskStatus = TaskStatus.PENDING,
        *,
        owner_id: str = "planner",
        outside_scope: bool = False,
        dependency_completed: bool | None = None,
    ):
        event_ids = iter(f"event-{number}" for number in range(1, 100))
        store = TaskStore(
            clock=lambda: self.now,
            event_id_factory=lambda: next(event_ids),
        )
        root = store.create(
            TaskSpec(
                summary="Root",
                task_id="root",
                owner_id="supervisor",
            )
        )
        parent_id = root.task_id
        if outside_scope:
            external_root = store.create(
                TaskSpec(
                    summary="External root",
                    task_id="external-root",
                )
            )
            parent_id = external_root.task_id

        dependency_ids: tuple[str, ...] = ()
        if dependency_completed is not None:
            dependency = store.create(
                TaskSpec(
                    summary="Dependency",
                    task_id="dependency",
                    parent_id=root.task_id,
                    owner_id="planner",
                )
            )
            if dependency_completed:
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=TaskStatus.RUNNING,
                )
                dependency = store.transition(
                    dependency.task_id,
                    expected_revision=dependency.revision,
                    to_status=TaskStatus.COMPLETED,
                    result=None,
                )
            dependency_ids = (dependency.task_id,)

        target = store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                parent_id=parent_id,
                dependency_ids=dependency_ids,
                owner_id=owner_id,
            )
        )
        if source_status in {TaskStatus.RUNNING, TaskStatus.COMPLETED}:
            target = store.transition(
                target.task_id,
                expected_revision=target.revision,
                to_status=TaskStatus.RUNNING,
            )
        if source_status is TaskStatus.BLOCKED:
            target = store.transition(
                target.task_id,
                expected_revision=target.revision,
                to_status=TaskStatus.BLOCKED,
                error="waiting",
            )
        elif source_status is TaskStatus.COMPLETED:
            target = store.transition(
                target.task_id,
                expected_revision=target.revision,
                to_status=TaskStatus.COMPLETED,
                result={"done": True},
            )

        update_tool = create_task_tools(
            store,
            root_task_id=root.task_id,
            owner_id="planner",
        )[1]
        return store, target, update_tool

    def assert_update_failure(
        self,
        store: TaskStore,
        target,
        update_tool,
        arguments: dict[str, object],
        cause_type: type[BaseException],
    ) -> None:
        nodes_before = store.list()
        events_before = store.events()

        with self.assertRaises(ToolExecutionError) as raised:
            update_tool.execute(arguments)

        self.assertIsInstance(raised.exception.__cause__, cause_type)
        self.assertEqual(store.get(target.task_id), target)
        self.assertEqual(store.list(), nodes_before)
        self.assertEqual(store.events(), events_before)

    def test_update_schema_lists_statuses_and_optional_fields(self) -> None:
        store, _, update_tool = self.make_case()
        arguments = {
            argument.name: argument
            for argument in update_tool.spec.arguments
        }

        self.assertEqual(
            update_tool.spec.argument_names(),
            [
                "task_id",
                "expected_revision",
                "status",
                "result",
                "error",
            ],
        )
        self.assertEqual(
            arguments["status"].schema["enum"],
            [
                "pending",
                "running",
                "completed",
                "failed",
                "blocked",
                "cancelled",
            ],
        )
        self.assertTrue(arguments["task_id"].required)
        self.assertTrue(arguments["expected_revision"].required)
        self.assertTrue(arguments["status"].required)
        self.assertFalse(arguments["result"].required)
        self.assertFalse(arguments["error"].required)
        self.assertEqual(store.get("target").status, TaskStatus.PENDING)

    def test_every_legal_transition_uses_store_rules(self) -> None:
        cases = (
            (TaskStatus.PENDING, TaskStatus.RUNNING, {}),
            (
                TaskStatus.PENDING,
                TaskStatus.BLOCKED,
                {"error": "waiting"},
            ),
            (TaskStatus.PENDING, TaskStatus.CANCELLED, {}),
            (
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
                {"result": {"answer": 42}},
            ),
            (
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
                {"result": None},
            ),
            (
                TaskStatus.RUNNING,
                TaskStatus.FAILED,
                {"error": "failed"},
            ),
            (
                TaskStatus.RUNNING,
                TaskStatus.BLOCKED,
                {"error": "waiting"},
            ),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED, {}),
            (TaskStatus.BLOCKED, TaskStatus.PENDING, {}),
            (TaskStatus.BLOCKED, TaskStatus.CANCELLED, {}),
        )
        for source_status, target_status, extra in cases:
            dependency_completed = (
                True if source_status is TaskStatus.BLOCKED else None
            )
            store, target, update_tool = self.make_case(
                source_status,
                dependency_completed=dependency_completed,
            )
            event_count = len(store.events())
            registry = ToolRegistry()
            registry.register(update_tool)

            with self.subTest(
                source=source_status.value,
                target=target_status.value,
                extra=extra,
            ):
                output = registry.execute(
                    "update_task",
                    {
                        "task_id": target.task_id,
                        "expected_revision": target.revision,
                        "status": target_status.value,
                        **extra,
                    },
                )
                updated = store.get(target.task_id)
                self.assertEqual(updated.status, target_status)
                self.assertEqual(updated.revision, target.revision + 1)
                self.assertEqual(output, task_node_to_dict(updated))
                self.assertEqual(len(store.events()), event_count + 1)
                self.assertEqual(store.events()[-1].reason, "transition")

    def test_result_and_error_rules_leave_store_unchanged(self) -> None:
        cases = (
            (TaskStatus.RUNNING, "completed", {}),
            (
                TaskStatus.PENDING,
                "running",
                {"result": {"unexpected": True}},
            ),
            (TaskStatus.RUNNING, "failed", {}),
            (TaskStatus.PENDING, "blocked", {}),
            (
                TaskStatus.PENDING,
                "running",
                {"error": "unexpected"},
            ),
            (
                TaskStatus.RUNNING,
                "completed",
                {"result": None, "error": "unexpected"},
            ),
            (
                TaskStatus.BLOCKED,
                "pending",
                {"error": "unexpected"},
            ),
        )
        for source_status, status, extra in cases:
            dependency_completed = (
                True if source_status is TaskStatus.BLOCKED else None
            )
            store, target, update_tool = self.make_case(
                source_status,
                dependency_completed=dependency_completed,
            )
            arguments = {
                "task_id": target.task_id,
                "expected_revision": target.revision,
                "status": status,
                **extra,
            }

            with self.subTest(
                source=source_status.value,
                status=status,
                extra=extra,
            ):
                self.assert_update_failure(
                    store,
                    target,
                    update_tool,
                    arguments,
                    InvalidTaskTransition,
                )

    def test_status_and_revision_errors_leave_store_unchanged(self) -> None:
        cases = (
            ("unknown", 1, ValueError),
            ("running", True, TypeError),
            ("running", 0, ValueError),
        )
        for status, revision, cause_type in cases:
            store, target, update_tool = self.make_case()
            with self.subTest(status=status, revision=revision):
                self.assert_update_failure(
                    store,
                    target,
                    update_tool,
                    {
                        "task_id": target.task_id,
                        "expected_revision": revision,
                        "status": status,
                    },
                    cause_type,
                )

    def test_stale_revision_is_not_retried(self) -> None:
        store, stale, update_tool = self.make_case()
        current = store.transition(
            stale.task_id,
            expected_revision=stale.revision,
            to_status=TaskStatus.RUNNING,
        )

        self.assert_update_failure(
            store,
            current,
            update_tool,
            {
                "task_id": stale.task_id,
                "expected_revision": stale.revision,
                "status": "failed",
                "error": "late update",
            },
            TaskConflictError,
        )

    def test_terminal_and_dependency_rules_stay_in_store(self) -> None:
        completed_store, completed, completed_tool = self.make_case(
            TaskStatus.COMPLETED
        )
        self.assert_update_failure(
            completed_store,
            completed,
            completed_tool,
            {
                "task_id": completed.task_id,
                "expected_revision": completed.revision,
                "status": "running",
            },
            InvalidTaskTransition,
        )

        blocked_store, blocked, blocked_tool = self.make_case(
            TaskStatus.BLOCKED,
            dependency_completed=False,
        )
        self.assert_update_failure(
            blocked_store,
            blocked,
            blocked_tool,
            {
                "task_id": blocked.task_id,
                "expected_revision": blocked.revision,
                "status": "pending",
            },
            TaskDependencyNotReady,
        )

    def test_scope_and_owner_failures_leave_store_unchanged(self) -> None:
        cases = (
            {"outside_scope": True},
            {"owner_id": "other"},
        )
        for options in cases:
            store, target, update_tool = self.make_case(**options)
            with self.subTest(options=options):
                self.assert_update_failure(
                    store,
                    target,
                    update_tool,
                    {
                        "task_id": target.task_id,
                        "expected_revision": target.revision,
                        "status": "running",
                    },
                    ValueError,
                )


class ListTaskToolTests(unittest.TestCase):
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        event_ids = iter(f"list-event-{number}" for number in range(1, 100))
        self.store = TaskStore(
            clock=lambda: self.now,
            event_id_factory=lambda: next(event_ids),
        )
        self.root = self.store.create(
            TaskSpec(
                summary="Root",
                task_id="root",
                owner_id="supervisor",
            )
        )
        self.pending = self.store.create(
            TaskSpec(
                summary="Pending",
                task_id="pending",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.running = self.store.create(
            TaskSpec(
                summary="Running",
                task_id="running",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.running = self.store.transition(
            self.running.task_id,
            expected_revision=self.running.revision,
            to_status=TaskStatus.RUNNING,
        )
        self.completed = self.store.create(
            TaskSpec(
                summary="Completed",
                task_id="completed",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.completed = self.store.transition(
            self.completed.task_id,
            expected_revision=self.completed.revision,
            to_status=TaskStatus.RUNNING,
        )
        self.completed = self.store.transition(
            self.completed.task_id,
            expected_revision=self.completed.revision,
            to_status=TaskStatus.COMPLETED,
            result={"done": True},
        )
        self.ready_dependent = self.store.create(
            TaskSpec(
                summary="Ready dependent",
                task_id="ready-dependent",
                parent_id=self.root.task_id,
                dependency_ids=(self.completed.task_id,),
                owner_id="planner",
            )
        )
        self.failed_source = self.store.create(
            TaskSpec(
                summary="Failed source",
                task_id="failed-source",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.blocked = self.store.create(
            TaskSpec(
                summary="Blocked",
                task_id="blocked",
                parent_id=self.root.task_id,
                dependency_ids=(self.failed_source.task_id,),
                owner_id="planner",
            )
        )
        self.failed_source = self.store.transition(
            self.failed_source.task_id,
            expected_revision=self.failed_source.revision,
            to_status=TaskStatus.RUNNING,
        )
        self.failed_source = self.store.transition(
            self.failed_source.task_id,
            expected_revision=self.failed_source.revision,
            to_status=TaskStatus.FAILED,
            error="failed",
        )
        self.blocked = self.store.get(self.blocked.task_id)
        self.other_owner = self.store.create(
            TaskSpec(
                summary="Other owner",
                task_id="other-owner",
                parent_id=self.root.task_id,
                owner_id="other",
            )
        )
        self.external_root = self.store.create(
            TaskSpec(
                summary="External root",
                task_id="external-root",
                owner_id="planner",
            )
        )
        self.external_child = self.store.create(
            TaskSpec(
                summary="External child",
                task_id="external-child",
                parent_id=self.external_root.task_id,
                owner_id="planner",
            )
        )
        self.grandchild = self.store.create(
            TaskSpec(
                summary="Grandchild",
                task_id="grandchild",
                parent_id=self.pending.task_id,
                owner_id="planner",
            )
        )
        self.cancelled = self.store.create(
            TaskSpec(
                summary="Cancelled",
                task_id="cancelled",
                parent_id=self.root.task_id,
                owner_id="planner",
            )
        )
        self.cancelled = self.store.transition(
            self.cancelled.task_id,
            expected_revision=self.cancelled.revision,
            to_status=TaskStatus.CANCELLED,
        )
        self.tools = create_task_tools(
            self.store,
            root_task_id=self.root.task_id,
            owner_id="planner",
        )
        self.list_tool = self.tools[2]

    def test_factory_contract_and_list_schema(self) -> None:
        self.assertEqual(
            [tool.name for tool in self.tools],
            ["create_task", "update_task", "list_tasks"],
        )
        self.assertEqual(len({tool.name for tool in self.tools}), 3)
        self.assertEqual(
            self.list_tool.spec.argument_names(),
            ["status", "ready_only"],
        )
        self.assertTrue(
            all(
                not argument.required
                for argument in self.list_tool.spec.arguments
            )
        )

    def test_lists_only_matching_owner_and_root_in_store_order(self) -> None:
        output = self.list_tool.execute({})

        self.assertEqual(
            [node["task_id"] for node in output],
            [
                "pending",
                "running",
                "completed",
                "ready-dependent",
                "failed-source",
                "blocked",
                "grandchild",
                "cancelled",
            ],
        )
        self.assertNotIn(
            self.root.task_id,
            [node["task_id"] for node in output],
        )
        self.assertNotIn(
            self.other_owner.task_id,
            [node["task_id"] for node in output],
        )
        self.assertNotIn(
            self.external_child.task_id,
            [node["task_id"] for node in output],
        )

    def test_filters_every_status_and_returns_empty_list(self) -> None:
        expected = {
            "pending": ["pending", "ready-dependent", "grandchild"],
            "running": ["running"],
            "completed": ["completed"],
            "failed": ["failed-source"],
            "blocked": ["blocked"],
            "cancelled": ["cancelled"],
        }
        for status, task_ids in expected.items():
            with self.subTest(status=status):
                output = self.list_tool.execute({"status": status})
                self.assertEqual(
                    [node["task_id"] for node in output],
                    task_ids,
                )

        empty_tool = create_task_tools(
            self.store,
            root_task_id=self.root.task_id,
            owner_id="nobody",
        )[2]
        self.assertEqual(empty_tool.execute({}), [])

    def test_ready_only_reuses_store_ready_order(self) -> None:
        output = self.list_tool.execute({"ready_only": True})

        self.assertEqual(
            [node["task_id"] for node in output],
            ["pending", "ready-dependent", "grandchild"],
        )
        self.assertTrue(
            all(node["status"] == "pending" for node in output)
        )

    def test_matching_root_owner_can_see_root(self) -> None:
        supervisor_list = create_task_tools(
            self.store,
            root_task_id=self.root.task_id,
            owner_id="supervisor",
        )[2]

        output = supervisor_list.execute({})

        self.assertEqual(
            [node["task_id"] for node in output],
            [self.root.task_id],
        )

    def test_conflicting_or_unknown_filters_are_tool_errors(self) -> None:
        nodes_before = self.store.list()
        events_before = self.store.events()
        cases = (
            (
                {"status": "pending", "ready_only": True},
                ValueError,
            ),
            ({"status": "unknown"}, ValueError),
        )
        for arguments, cause_type in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolExecutionError) as raised:
                    self.list_tool.execute(arguments)
                self.assertIsInstance(
                    raised.exception.__cause__,
                    cause_type,
                )
                self.assertEqual(self.store.list(), nodes_before)
                self.assertEqual(self.store.events(), events_before)

    def test_queries_are_read_only_and_json_compatible(self) -> None:
        nodes_before = self.store.list()
        events_before = self.store.events()

        outputs = (
            self.list_tool.execute({}),
            self.list_tool.execute({"status": "running"}),
            self.list_tool.execute({"ready_only": True}),
        )

        for output in outputs:
            json.dumps(output)
        self.assertEqual(self.store.list(), nodes_before)
        self.assertEqual(self.store.events(), events_before)

    def test_blocked_output_uses_existing_node_fields_only(self) -> None:
        output = self.list_tool.execute({"status": "blocked"})

        self.assertEqual(len(output), 1)
        self.assertEqual(
            output[0]["blocked_by"],
            [self.failed_source.task_id],
        )
        self.assertNotIn("duration", output[0])
        self.assertNotIn("blocking_chain", output[0])

    def test_non_json_result_fails_without_changing_store(self) -> None:
        store = TaskStore(clock=lambda: self.now)
        root = store.create(
            TaskSpec(summary="Root", task_id="root")
        )
        target = store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                parent_id=root.task_id,
                owner_id="planner",
            )
        )
        target = store.transition(
            target.task_id,
            expected_revision=target.revision,
            to_status=TaskStatus.RUNNING,
        )
        store.transition(
            target.task_id,
            expected_revision=target.revision,
            to_status=TaskStatus.COMPLETED,
            result=object(),
        )
        list_tool = create_task_tools(
            store,
            root_task_id=root.task_id,
            owner_id="planner",
        )[2]
        nodes_before = store.list()
        events_before = store.events()

        with self.assertRaises(ToolExecutionError) as raised:
            list_tool.execute({})

        self.assertIsInstance(raised.exception.__cause__, TypeError)
        self.assertEqual(store.list(), nodes_before)
        self.assertEqual(store.events(), events_before)

    def test_three_tools_share_store_root_and_owner(self) -> None:
        created = self.tools[0].execute({"summary": "Shared task"})
        updated = self.tools[1].execute(
            {
                "task_id": created["task_id"],
                "expected_revision": created["revision"],
                "status": "running",
            }
        )

        output = self.list_tool.execute({"status": "running"})

        self.assertIn(
            updated["task_id"],
            [node["task_id"] for node in output],
        )


class PlanningToolAgentFlowTests(unittest.TestCase):
    now = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    planning_tool_names = {"create_task", "update_task", "list_tasks"}

    def success_case(self):
        event_ids = iter(f"agent-event-{number}" for number in range(1, 20))
        store = TaskStore(
            clock=lambda: self.now,
            id_factory=lambda: "task-child",
            event_id_factory=lambda: next(event_ids),
        )
        root = store.create(
            TaskSpec(
                summary="Root",
                task_id="root",
                owner_id="supervisor",
            )
        )
        model = PlanningResponseModel(
            [
                ModelResponse(
                    response_id="response-create",
                    output=[],
                    output_text=None,
                    tool_calls=[
                        ToolCall(
                            "create_task",
                            {
                                "summary": "Inspect current planning flow",
                                "parent_id": None,
                            },
                            "call-create",
                        )
                    ],
                ),
                ModelResponse(
                    response_id="response-update-list",
                    output=[],
                    output_text=None,
                    tool_calls=[
                        ToolCall(
                            "update_task",
                            {
                                "task_id": "task-child",
                                "expected_revision": 1,
                                "status": "running",
                            },
                            "call-update",
                        ),
                        ToolCall(
                            "list_tasks",
                            {},
                            "call-list",
                        ),
                    ],
                ),
                ModelResponse(
                    response_id="response-final",
                    output=[],
                    output_text="Planning tools completed.",
                    tool_calls=[],
                ),
            ]
        )
        registry = ToolRegistry()
        for tool in create_task_tools(
            store,
            root_task_id=root.task_id,
            owner_id="planner",
        ):
            registry.register(tool)
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
        )
        return store, model, agent

    def error_case(self, error_kind: str):
        event_ids = iter(f"error-event-{number}" for number in range(1, 20))
        store = TaskStore(
            clock=lambda: self.now,
            event_id_factory=lambda: next(event_ids),
        )
        root = store.create(
            TaskSpec(
                summary="Root",
                task_id="root",
                owner_id="supervisor",
            )
        )
        parent_id = root.task_id
        owner_id = "planner"
        if error_kind == "scope":
            parent_id = store.create(
                TaskSpec(
                    summary="External root",
                    task_id="external-root",
                )
            ).task_id
        elif error_kind == "owner":
            owner_id = "other"
        target = store.create(
            TaskSpec(
                summary="Target",
                task_id="target",
                parent_id=parent_id,
                owner_id=owner_id,
            )
        )
        arguments: dict[str, object] = {
            "task_id": target.task_id,
            "expected_revision": target.revision,
            "status": "running",
        }
        if error_kind == "stale":
            stale_revision = target.revision
            target = store.transition(
                target.task_id,
                expected_revision=target.revision,
                to_status=TaskStatus.RUNNING,
            )
            arguments = {
                "task_id": target.task_id,
                "expected_revision": stale_revision,
                "status": "failed",
                "error": "late update",
            }
        elif error_kind == "status":
            arguments["status"] = "unknown"

        model = PlanningResponseModel(
            [
                ModelResponse(
                    response_id=f"response-{error_kind}-error",
                    output=[],
                    output_text=None,
                    tool_calls=[
                        ToolCall(
                            "update_task",
                            arguments,
                            f"call-{error_kind}",
                        )
                    ],
                ),
                ModelResponse(
                    response_id=f"response-{error_kind}-final",
                    output=[],
                    output_text=f"Recovered from {error_kind} error.",
                    tool_calls=[],
                ),
            ]
        )
        registry = ToolRegistry()
        for tool in create_task_tools(
            store,
            root_task_id=root.task_id,
            owner_id="planner",
        ):
            registry.register(tool)
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
        )
        return store, target, model, agent

    def test_registered_tools_complete_real_agent_loop(self) -> None:
        store, model, agent = self.success_case()

        result = agent.run("Use planning tools.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "Planning tools completed.")
        self.assertEqual(len(model.tool_specs_seen), 3)
        for specs in model.tool_specs_seen:
            self.assertEqual(
                [
                    spec.name
                    for spec in specs
                    if spec.name in self.planning_tool_names
                ],
                ["create_task", "update_task", "list_tasks"],
            )

        child = store.get("task-child")
        self.assertEqual(child.status, TaskStatus.RUNNING)
        self.assertEqual(child.revision, 2)
        self.assertEqual(
            [event.reason for event in store.events()],
            ["created", "created", "transition"],
        )
        self.assertEqual(
            [call.tool_name for call, _ in model.recorded_tool_outputs],
            ["create_task", "update_task", "list_tasks"],
        )
        list_observation = model.recorded_tool_outputs[-1][1]
        self.assertIn("task-child", list_observation)
        self.assertIn("running", list_observation)
        self.assertIn("revision", list_observation)
        self.assertEqual(
            [item.item_type for item in result.new_items].count("tool_call"),
            3,
        )
        self.assertEqual(
            [item.item_type for item in result.new_items].count("tool_result"),
            3,
        )
        self.assertNotIn(
            "tool_error",
            [item.item_type for item in result.new_items],
        )
        self.assertFalse(
            any(isinstance(item.payload, TaskEvent) for item in result.new_items)
        )
        self.assertFalse(hasattr(result, "task_events"))

    def test_stream_keeps_existing_tool_event_order(self) -> None:
        store, _, agent = self.success_case()
        stream = Runner.run_stream(agent, "Use planning tools.")

        async def scenario():
            events = [event async for event in stream]
            return events, await stream._wait_result()

        events, result = asyncio.run(scenario())
        lifecycle = [
            (type(event), event.tool_name)
            for event in events
            if isinstance(
                event,
                (ToolRequestedEvent, ToolStartedEvent, ToolCompletedEvent),
            )
        ]

        self.assertEqual(
            lifecycle,
            [
                (ToolRequestedEvent, "create_task"),
                (ToolStartedEvent, "create_task"),
                (ToolCompletedEvent, "create_task"),
                (ToolRequestedEvent, "update_task"),
                (ToolStartedEvent, "update_task"),
                (ToolCompletedEvent, "update_task"),
                (ToolRequestedEvent, "list_tasks"),
                (ToolStartedEvent, "list_tasks"),
                (ToolCompletedEvent, "list_tasks"),
            ],
        )
        self.assertFalse(any(isinstance(event, ToolFailedEvent) for event in events))
        self.assertTrue(result.reached_final_answer)
        self.assertEqual(len(store.events()), 3)
        self.assertFalse(any(isinstance(event, TaskEvent) for event in events))

    def test_tool_errors_reach_model_and_allow_recovery(self) -> None:
        for error_kind in ("stale", "owner", "scope", "status"):
            store, target, model, agent = self.error_case(error_kind)
            nodes_before = store.list()
            events_before = store.events()

            with self.subTest(error_kind=error_kind):
                result = agent.run("Recover from a planning tool error.")
                self.assertTrue(result.reached_final_answer)
                self.assertEqual(
                    result.final_answer,
                    f"Recovered from {error_kind} error.",
                )
                self.assertEqual(store.get(target.task_id), target)
                self.assertEqual(store.list(), nodes_before)
                self.assertEqual(store.events(), events_before)
                error_items = [
                    item
                    for item in result.new_items
                    if item.item_type == "tool_error"
                ]
                self.assertEqual(len(error_items), 1)
                self.assertIn("update_task", error_items[0].payload)
                self.assertEqual(len(model.recorded_tool_outputs), 1)
                self.assertIn(
                    "status: error",
                    model.recorded_tool_outputs[0][1],
                )
                self.assertTrue(
                    any(
                        "Now let's retry" in message.content
                        for message in model.messages_seen[-1]
                    )
                )
                self.assertFalse(
                    any(
                        isinstance(item.payload, TaskEvent)
                        for item in result.new_items
                    )
                )

    def test_stream_reports_failed_tool_then_final_answer(self) -> None:
        store, target, _, agent = self.error_case("stale")
        events_before = store.events()
        stream = agent.run_stream("Recover from stale revision.")

        async def scenario():
            events = [event async for event in stream]
            return events, await stream._wait_result()

        events, result = asyncio.run(scenario())
        tool_events = [
            event
            for event in events
            if isinstance(
                event,
                (
                    ToolRequestedEvent,
                    ToolStartedEvent,
                    ToolCompletedEvent,
                    ToolFailedEvent,
                ),
            )
        ]

        self.assertEqual(
            [type(event) for event in tool_events],
            [ToolRequestedEvent, ToolStartedEvent, ToolFailedEvent],
        )
        self.assertTrue(result.reached_final_answer)
        self.assertEqual(store.get(target.task_id), target)
        self.assertEqual(store.events(), events_before)

    def test_unregistered_agents_have_no_planning_tools_or_side_effects(self) -> None:
        def unregistered_agent(answer: str):
            model = PlanningResponseModel(
                [
                    ModelResponse(
                        response_id=f"response-{answer}",
                        output=[],
                        output_text=answer,
                        tool_calls=[],
                    )
                ]
            )
            return model, Agent(
                memory=AgentMemory(),
                model=model,
                tool_registry=ToolRegistry(),
            )

        sync_model, sync_agent = unregistered_agent("sync done")
        async_model, async_agent = unregistered_agent("async done")
        stream_model, stream_agent = unregistered_agent("stream done")

        sync_result = sync_agent.run("Finish without planning.")
        async_result = asyncio.run(
            async_agent.run_async("Finish without planning.")
        )
        stream = stream_agent.run_stream("Finish without planning.")

        async def scenario():
            events = [event async for event in stream]
            return events, await stream._wait_result()

        stream_events, stream_result = asyncio.run(scenario())

        self.assertEqual(sync_result.final_answer, "sync done")
        self.assertEqual(async_result.final_answer, "async done")
        self.assertEqual(stream_result.final_answer, "stream done")
        for model in (sync_model, async_model, stream_model):
            visible_names = {
                spec.name
                for specs in model.tool_specs_seen
                for spec in specs
            }
            self.assertTrue(self.planning_tool_names.isdisjoint(visible_names))
            self.assertEqual(model.recorded_tool_outputs, [])
        for result in (sync_result, async_result, stream_result):
            self.assertFalse(
                any(
                    item.item_type in {"tool_call", "tool_result", "tool_error"}
                    for item in result.new_items
                )
            )
        self.assertFalse(
            any(
                isinstance(
                    event,
                    (
                        ToolRequestedEvent,
                        ToolStartedEvent,
                        ToolCompletedEvent,
                        ToolFailedEvent,
                    ),
                )
                for event in stream_events
            )
        )


if __name__ == "__main__":
    unittest.main()
