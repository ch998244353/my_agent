from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, get_ident
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from agents.chat_backend.conversation import StoredMessage
from agents.chat_backend.model_profiles import ModelProfile
from agents.chat_backend.planning_service import (
    PLANNING_DRAFT_SCHEMA,
    PlanningApprovalSnapshot,
    PlanningDraftOutcome,
    PlanningInputError,
    PlanningProgressEvent,
    PlanningRecoveryReport,
    PlanningRunError,
    PlanningService,
    PlanningSnapshot,
    PlanningStepSnapshot,
    _parse_draft_output,
    _safe_run_error,
)
from agents.chat_backend.planning_store import (
    CorruptPlanningRunError,
    InvalidPlanningTransition,
    PlanningConflictError,
    PlanningRun,
    PlanningRunNotFoundError,
)
from agents.contracts import ChatMessage, ModelResponse, ToolCall
from agents.planning import (
    PLAN_DEFINITION_SCHEMA_VERSION,
    PlanDefinition,
    PlanExecution,
    PlanStepSpec,
)
from agents.run_state import ApprovalSnapshot, RunStateSnapshot
from agents.runner import Runner
from agents.task_state import TaskStatus
from agents.verification import VerificationResult
from agents.workspace_manifest import WorkspaceManifest


class StructuredDraftModel:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self._outputs = list(outputs)
        self.messages: list[list[ChatMessage]] = []
        self.tool_specs = []
        self.response_schema = None
        self.response_schema_name = None
        self.response_schema_strict = False

    def get_response(self, messages, tool_specs) -> ModelResponse:
        self.messages.append(list(messages))
        self.tool_specs = list(tool_specs)
        return ModelResponse(
            response_id=f"planning-{len(self.messages)}",
            output=[],
            output_text=json.dumps(self._outputs.pop(0)),
            tool_calls=[],
        )


class PersistedStepModel:
    def __init__(self, actions, plans) -> None:
        self._actions = list(actions)
        self._plans = plans
        self.messages: list[list[ChatMessage]] = []
        self.running_before_call: list[tuple[str, ...]] = []

    def get_response(self, messages, tool_specs) -> ModelResponse:
        run = self._plans.run
        execution = PlanExecution.from_events(
            run.definition,
            run.task_events,
            owner_id=str(run.id),
        )
        self.running_before_call.append(
            tuple(
                node.task_id
                for node in execution.task_store.list()
                if node.status is TaskStatus.RUNNING
            )
        )
        self.messages.append(list(messages))
        action = self._actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, ToolCall):
            return ModelResponse(
                response_id=f"step-{len(self.messages)}",
                output=[],
                output_text=None,
                tool_calls=[action],
            )
        return ModelResponse(
            response_id=f"step-{len(self.messages)}",
            output=[],
            output_text=action,
            tool_calls=[],
        )


class InMemoryMessageStore:
    def __init__(self) -> None:
        self.items: list[StoredMessage] = []
        self.fail_add = False

    def list(self, conversation_id, limit=None):
        items = [
            item for item in self.items if item.conversation_id == conversation_id
        ]
        return tuple(items if limit is None else items[-limit:])

    def add_batch(self, conversation_id, items):
        if self.fail_add:
            raise RuntimeError("postgresql://secret")
        stored = tuple(
            StoredMessage(
                id=uuid4(),
                conversation_id=conversation_id,
                sequence=len(self.items) + offset + 1,
                role=item.role,
                content=item.content,
                created_at=datetime.now(UTC),
            )
            for offset, item in enumerate(items)
        )
        self.items.extend(stored)
        return stored

    def pop_last(self, conversation_id):
        if not self.items or self.items[-1].conversation_id != conversation_id:
            return None
        return self.items.pop()

    def clear(self, conversation_id):
        self.items = [
            item for item in self.items if item.conversation_id != conversation_id
        ]


class InMemoryPlanStore:
    def __init__(self) -> None:
        self.run: PlanningRun | None = None
        self._lock = Lock()

    def get_active(self, conversation_id):
        if self.run is None or self.run.conversation_id != conversation_id:
            return None
        if self.run.status in {"completed", "failed", "cancelled", "superseded"}:
            return None
        return self.run

    def get_latest(self, conversation_id):
        if self.run is None or self.run.conversation_id != conversation_id:
            return None
        return self.run

    def create_draft(
        self,
        conversation_id,
        objective,
        *,
        model_profile,
        manifest,
    ):
        now = datetime.now(UTC)
        self.run = PlanningRun(
            id=uuid4(),
            conversation_id=conversation_id,
            version=1,
            row_revision=1,
            status="drafting",
            objective=objective,
            supersedes_version=None,
            revision_note=None,
            last_error=None,
            definition=None,
            task_events=(),
            run_state_snapshot=None,
            model_snapshot=model_profile.public_dict(),
            workspace_snapshot=manifest.metadata(),
            created_at=now,
            updated_at=now,
        )
        return self.run

    def submit_for_approval(self, run_id, expected_row_revision, definition):
        assert self.run is not None
        if self.run.id != run_id or self.run.row_revision != expected_row_revision:
            raise PlanningConflictError(
                run_id,
                expected_row_revision,
                self.run.row_revision,
            )
        if self.run.status != "drafting":
            raise InvalidPlanningTransition(
                run_id,
                self.run.status,
                "submit_for_approval",
                "awaiting_approval",
            )
        self.run = replace(
            self.run,
            row_revision=self.run.row_revision + 1,
            status="awaiting_approval",
            definition=definition,
            updated_at=datetime.now(UTC),
        )
        return self.run

    def approve(self, run_id, expected_row_revision):
        with self._lock:
            assert self.run is not None
            if self.run.id != run_id:
                raise PlanningRunNotFoundError(run_id)
            if self.run.row_revision != expected_row_revision:
                raise PlanningConflictError(
                    run_id,
                    expected_row_revision,
                    self.run.row_revision,
                )
            if self.run.status != "awaiting_approval":
                raise InvalidPlanningTransition(
                    run_id,
                    self.run.status,
                    "approve",
                    "running",
                )
            execution = PlanExecution.materialize(
                self.run.definition,
                owner_id=str(run_id),
            )
            self.run = replace(
                self.run,
                row_revision=self.run.row_revision + 1,
                status="running",
                task_events=execution.task_store.events(),
                updated_at=datetime.now(UTC),
            )
            return self.run

    def save_execution(
        self,
        run_id,
        expected_row_revision,
        *,
        execution,
        status,
        run_state=None,
        last_error=None,
    ):
        with self._lock:
            assert self.run is not None
            if self.run.id != run_id:
                raise PlanningRunNotFoundError(run_id)
            if self.run.row_revision != expected_row_revision:
                raise PlanningConflictError(
                    run_id,
                    expected_row_revision,
                    self.run.row_revision,
                )
            self.run = replace(
                self.run,
                row_revision=self.run.row_revision + 1,
                status=status,
                task_events=execution.task_store.events(),
                run_state_snapshot=run_state,
                last_error=last_error,
                updated_at=datetime.now(UTC),
            )
            return self.run

    def get(self, run_id):
        if self.run is None or self.run.id != run_id:
            raise PlanningRunNotFoundError(run_id)
        return self.run

    def list_recoverable_running(self, *, limit=100):
        if self.run is None or self.run.status != "running":
            return ()
        return (self.run,)


def question_output(question: str = "Which database should be used?"):
    return {"kind": "question", "question": question, "definition": None}


def plan_output():
    return {
        "kind": "plan",
        "question": None,
        "definition": {
            "schema_version": PLAN_DEFINITION_SCHEMA_VERSION,
            "objective": "Improve authentication",
            "steps": [
                {
                    "step_id": "inspect",
                    "title": "Inspect authentication",
                    "instruction": "Read the authentication path.",
                    "completion_criteria": "The current behavior is known.",
                    "dependency_ids": [],
                    "is_final_verification": False,
                },
                {
                    "step_id": "verify",
                    "title": "Verify the plan",
                    "instruction": "Run the frozen verification command.",
                    "completion_criteria": "The command succeeds.",
                    "dependency_ids": ["inspect"],
                    "is_final_verification": True,
                },
            ],
        },
    }


def execution_definition() -> PlanDefinition:
    return PlanDefinition(
        objective="Implement the approved change",
        steps=(
            PlanStepSpec(
                step_id="inspect",
                title="Inspect the target",
                instruction="Read the target implementation.",
                completion_criteria="The current behavior is understood.",
            ),
            PlanStepSpec(
                step_id="change",
                title="Apply the change",
                instruction="Edit the target implementation.",
                completion_criteria="The requested behavior is implemented.",
                dependency_ids=("inspect",),
            ),
            PlanStepSpec(
                step_id="verify",
                title="Verify the change",
                instruction="Run the frozen verification command.",
                completion_criteria="The command succeeds.",
                dependency_ids=("change",),
                is_final_verification=True,
            ),
        ),
    )


class PlanningServiceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_id = uuid4()
        self.conversation_id = uuid4()
        self.now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
        self.definition = PlanDefinition(
            objective="Implement the feature",
            steps=(
                PlanStepSpec(
                    "inspect",
                    "Inspect",
                    "Read the code",
                    "The current path is known",
                ),
                PlanStepSpec(
                    "change",
                    "Change",
                    "Edit the code",
                    "The feature works",
                    ("inspect",),
                ),
                PlanStepSpec(
                    "verify",
                    "Verify",
                    "Run tests",
                    "Tests pass",
                    ("change",),
                    True,
                ),
            ),
        )
        execution = PlanExecution.materialize(
            self.definition,
            owner_id=str(self.run_id),
        )
        inspect = execution.task_store.get("inspect")
        execution.task_store.transition(
            "inspect",
            expected_revision=inspect.revision,
            to_status=TaskStatus.RUNNING,
        )
        inspect = execution.task_store.get("inspect")
        execution.task_store.transition(
            "inspect",
            expected_revision=inspect.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        change = execution.task_store.get("change")
        execution.task_store.transition(
            "change",
            expected_revision=change.revision,
            to_status=TaskStatus.RUNNING,
        )
        change = execution.task_store.get("change")
        self.run_state = RunStateSnapshot(
            input="continue",
            last_agent_name="PlanStepAgent",
            last_response_id=None,
            current_turn=1,
            steps_taken=0,
            max_turns=None,
            max_steps=None,
            tool_approvals=(
                ApprovalSnapshot(
                    "apply_patch",
                    "call-1",
                    {"patch": "secret tool arguments"},
                    "pending",
                ),
                ApprovalSnapshot(
                    "shell_command",
                    "call-2",
                    {"command": "secret command"},
                    "approved",
                ),
            ),
            model_responses=({"secret": "model response"},),
            new_items=(),
            task_id="change",
            task_revision=change.revision,
        )
        self.run = PlanningRun(
            id=self.run_id,
            conversation_id=self.conversation_id,
            version=2,
            row_revision=7,
            status="awaiting_tool_approval",
            objective=self.definition.objective,
            supersedes_version=1,
            revision_note=None,
            last_error=None,
            definition=self.definition,
            task_events=execution.task_store.events(),
            run_state_snapshot=self.run_state,
            model_snapshot={"api_key": "secret-model-key"},
            workspace_snapshot={"env": {"TOKEN": "secret-env-value"}},
            created_at=self.now,
            updated_at=self.now,
        )
        self.plans = Mock()
        self.plans.get.return_value = self.run
        self.service = PlanningService(
            self.plans,
            Mock(),
            Mock(),
            Mock(),
        )

    def test_snapshot_projects_only_safe_planning_state(self) -> None:
        snapshot = self.service.get_snapshot(self.run_id)

        self.plans.get.assert_called_once_with(self.run_id)
        self.assertEqual(snapshot.status, "awaiting_tool_approval")
        self.assertEqual(snapshot.current_step_id, "change")
        self.assertEqual(snapshot.completed_step_ids, ("inspect",))
        self.assertEqual(
            snapshot.pending_approval_keys,
            (("apply_patch", "call-1"),),
        )
        self.assertIn("# Implement the feature", snapshot.plan_markdown or "")
        self.assertEqual(
            snapshot.allowed_actions,
            ("decide_tool_approval", "cancel"),
        )
        self.assertEqual(
            tuple(step.status for step in snapshot.steps),
            (
                TaskStatus.COMPLETED,
                TaskStatus.RUNNING,
                TaskStatus.PENDING,
            ),
        )
        self.assertEqual(snapshot.steps[0].result, "done")
        self.assertEqual(snapshot.steps[1].dependency_ids, ("inspect",))
        self.assertEqual(len(snapshot.pending_approvals), 1)
        self.assertEqual(snapshot.pending_approvals[0].call_id, "call-1")

        serialized = repr(asdict(snapshot))
        self.assertNotIn("secret-model-key", serialized)
        self.assertNotIn("secret-env-value", serialized)
        self.assertNotIn("secret tool arguments", serialized)
        self.assertNotIn("model response", serialized)

    def test_snapshot_replaces_internal_error_text(self) -> None:
        secret = "postgresql://user:password@host/database"
        self.plans.get.return_value = replace(
            self.run,
            status="failed",
            last_error=secret,
            run_state_snapshot=None,
        )

        snapshot = self.service.get_snapshot(self.run_id)

        self.assertEqual(snapshot.last_error, "planning run could not continue")
        self.assertNotIn(secret, repr(snapshot))

    def test_paused_snapshot_exposes_control_actions(self) -> None:
        secret = "postgresql://secret-host/private"
        self.plans.get.return_value = replace(
            self.run,
            status="paused",
            last_error=secret,
            run_state_snapshot=None,
        )

        snapshot = self.service.get_snapshot(self.run_id)

        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.current_step_id, "change")
        self.assertEqual(
            snapshot.allowed_actions,
            ("retry_current", "revise", "cancel"),
        )
        self.assertEqual(snapshot.last_error, "planning run could not continue")
        self.assertNotIn(secret, repr(snapshot))

    def test_paused_snapshot_exposes_fixed_safe_error_on_current_step(self) -> None:
        self.plans.get.return_value = replace(
            self.run,
            status="paused",
            last_error="planning verification failed",
            run_state_snapshot=None,
        )

        snapshot = self.service.get_snapshot(self.run_id)

        self.assertEqual(snapshot.last_error, "planning verification failed")
        self.assertEqual(snapshot.current_step_id, "change")
        self.assertEqual(snapshot.steps[1].error, "planning verification failed")

    def test_safe_run_error_accepts_only_fixed_codes(self) -> None:
        self.assertEqual(
            _safe_run_error("model_step_failed"),
            "planning model step failed",
        )
        with self.assertRaisesRegex(
            PlanningInputError,
            "unsupported planning run error code",
        ):
            _safe_run_error("postgresql://secret")  # type: ignore[arg-type]

    def test_store_errors_are_not_redefined(self) -> None:
        error = PlanningRunNotFoundError(self.run_id)
        self.plans.get.side_effect = error

        with self.assertRaises(PlanningRunNotFoundError) as raised:
            self.service.get_snapshot(self.run_id)

        self.assertIs(raised.exception, error)

    def test_public_values_are_frozen_and_errors_are_stable_bases(self) -> None:
        snapshot = self.service.get_snapshot(self.run_id)
        with self.assertRaises(FrozenInstanceError):
            snapshot.status = "completed"  # type: ignore[misc]

        self.assertTrue(issubclass(PlanningInputError, ValueError))
        self.assertTrue(issubclass(PlanningRunError, RuntimeError))
        self.assertTrue(
            all(
                hasattr(value_type, "__dataclass_fields__")
                for value_type in (
                    PlanningSnapshot,
                    PlanningStepSnapshot,
                    PlanningApprovalSnapshot,
                    PlanningDraftOutcome,
                    PlanningRecoveryReport,
                    PlanningProgressEvent,
                )
            )
        )

    def test_submitted_plan_projects_pending_steps_without_task_events(self) -> None:
        self.plans.get.return_value = replace(
            self.run,
            status="awaiting_approval",
            task_events=(),
            run_state_snapshot=None,
        )

        snapshot = self.service.get_snapshot(self.run_id)

        self.assertEqual(
            tuple(step.status for step in snapshot.steps),
            (TaskStatus.PENDING, TaskStatus.PENDING, TaskStatus.PENDING),
        )
        self.assertIsNone(snapshot.current_step_id)
        self.assertEqual(snapshot.completed_step_ids, ())

    def test_latest_snapshot_returns_latest_run_or_none(self) -> None:
        self.plans.get_latest.return_value = self.run

        snapshot = self.service.get_latest_snapshot(self.conversation_id)

        self.assertEqual(snapshot.run_id, self.run_id)
        self.plans.get_latest.assert_called_once_with(self.conversation_id)
        self.plans.get_latest.return_value = None
        self.assertIsNone(self.service.get_latest_snapshot(self.conversation_id))
        with self.assertRaises(PlanningInputError):
            self.service.get_latest_snapshot("bad")  # type: ignore[arg-type]


class PlanningDraftContractTests(unittest.TestCase):
    def test_parser_accepts_only_question_or_valid_plan(self) -> None:
        self.assertEqual(
            _parse_draft_output(question_output("  Need a database?  ")),
            ("question", "Need a database?"),
        )
        kind, definition = _parse_draft_output(plan_output())
        self.assertEqual(kind, "plan")
        self.assertIsInstance(definition, PlanDefinition)
        self.assertEqual(
            _parse_draft_output(
                '{"kind":"question","question":"Need a database?",'
                '"definition":null}'
            ),
            ("question", "Need a database?"),
        )

        invalid = (
            None,
            "not json",
            "[]",
            {"kind": "question", "question": "Need input"},
            {"kind": "question", "question": " ", "definition": None},
            {
                "kind": "question",
                "question": "Need input",
                "definition": plan_output()["definition"],
            },
            {"kind": "plan", "question": "extra", "definition": None},
            {"kind": "unknown", "question": None, "definition": None},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                _parse_draft_output(value)

    def test_schema_requires_all_three_root_fields(self) -> None:
        self.assertEqual(
            set(PLANNING_DRAFT_SCHEMA["required"]),
            {"kind", "question", "definition"},
        )
        self.assertFalse(PLANNING_DRAFT_SCHEMA["additionalProperties"])


class PlanningServiceDraftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.conversation_id = uuid4()
        self.profile = ModelProfile(
            id=uuid4(),
            name="Local model",
            provider="ollama",
            protocol="chat_completions",
            base_url="http://localhost:11434/v1",
            model="qwen3",
            api_key="secret-api-key",
            is_active=True,
        )
        self.manifest = WorkspaceManifest(
            root=Path(self.temp_directory.name),
            env={"SECRET_TOKEN": "secret-env-value"},
        )
        self.plans = InMemoryPlanStore()
        self.messages = InMemoryMessageStore()
        self.profiles = Mock()
        self.profiles.active.return_value = self.profile
        self.profiles.get.return_value = self.profile
        self.manifest_provider = Mock(return_value=self.manifest)
        self.service = PlanningService(
            self.plans,
            self.messages,
            self.profiles,
            self.manifest_provider,
        )

    async def test_question_then_plan_uses_real_runner_and_readable_history(self) -> None:
        model = StructuredDraftModel([question_output(), plan_output()])
        events = []
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            question = await self.service.draft(
                self.conversation_id,
                "  Inspect authentication  ",
                on_event=events.append,
            )
            plan = await self.service.draft(
                self.conversation_id,
                "Use PostgreSQL",
                expected_row_revision=question.snapshot.row_revision,
            )

        self.assertEqual(question.kind, "question")
        self.assertEqual(question.snapshot.status, "drafting")
        self.assertEqual(question.snapshot.allowed_actions, ("draft", "cancel"))
        self.assertEqual(plan.kind, "plan")
        self.assertEqual(plan.snapshot.status, "awaiting_approval")
        self.assertEqual(
            plan.snapshot.allowed_actions,
            ("approve", "revise", "cancel"),
        )
        self.assertTrue(plan.content.startswith("# Improve authentication\n"))

        history = [item.content for item in self.messages.items]
        self.assertEqual(
            history,
            [
                "Inspect authentication",
                "Which database should be used?",
                "Use PostgreSQL",
                plan.content,
            ],
        )
        self.assertNotIn('"kind"', "\n".join(history))
        self.assertIn("Which database should be used?", model.messages[1][1].content)
        self.assertTrue(
            all(
                any(
                    message.role == "system"
                    and "Inspect authentication" in message.content
                    for message in request_messages
                )
                for request_messages in model.messages
            )
        )

        tool_names = {spec.name for spec in model.tool_specs}
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("search_workspace_text", tool_names)
        self.assertNotIn("run_shell_command", tool_names)
        self.assertNotIn("run_test_command", tool_names)
        self.assertNotIn("apply_patch", tool_names)
        self.assertIs(model.response_schema, PLANNING_DRAFT_SCHEMA)
        self.assertTrue(model.response_schema_strict)
        self.assertTrue(events)
        self.assertTrue(
            all(event.planning_run_id == question.snapshot.run_id for event in events)
        )
        self.assertTrue(all(event.task_id is None for event in events))

        run = self.plans.run
        self.assertIsNotNone(run)
        serialized = repr(run)
        self.assertNotIn("secret-api-key", serialized)
        self.assertNotIn("secret-env-value", serialized)
        self.profiles.active.assert_called_once_with()
        self.profiles.get.assert_called_once_with(self.profile.id)

    async def test_invalid_model_output_is_safe_and_saves_no_messages(self) -> None:
        invalid = question_output()
        invalid["definition"] = plan_output()["definition"]
        model = StructuredDraftModel([invalid])

        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            with self.assertRaisesRegex(
                PlanningRunError,
                "planning model returned an invalid draft",
            ) as raised:
                await self.service.draft(self.conversation_id, "secret raw input")

        self.assertNotIn("secret raw input", str(raised.exception))
        self.assertEqual(self.messages.items, [])
        self.assertEqual(self.plans.run.status, "drafting")

    async def test_retry_after_invalid_output_keeps_original_objective(self) -> None:
        invalid = question_output()
        invalid["definition"] = plan_output()["definition"]
        model = StructuredDraftModel([invalid, plan_output()])

        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            with self.assertRaises(PlanningRunError):
                await self.service.draft(self.conversation_id, "Original objective")
            outcome = await self.service.draft(
                self.conversation_id,
                "Retry now",
                expected_row_revision=1,
            )

        self.assertEqual(outcome.kind, "plan")
        self.assertEqual(
            [item.content for item in self.messages.items][0],
            "Retry now",
        )
        self.assertTrue(
            any(
                message.role == "system"
                and "Original objective" in message.content
                for message in model.messages[1]
            )
        )

    async def test_invalid_plan_graph_is_rejected_after_schema_parsing(self) -> None:
        invalid = plan_output()
        invalid["definition"]["steps"] = invalid["definition"]["steps"][:1]
        model = StructuredDraftModel([invalid])

        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            with self.assertRaisesRegex(
                PlanningRunError,
                "planning model returned an invalid draft",
            ):
                await self.service.draft(self.conversation_id, "Make a plan")

        self.assertEqual(self.messages.items, [])
        self.assertIsNone(self.plans.run.definition)

    async def test_plan_remains_authoritative_when_message_save_fails(self) -> None:
        self.messages.fail_add = True
        model = StructuredDraftModel([plan_output()])

        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            with self.assertRaisesRegex(
                PlanningRunError,
                "planning messages could not be saved",
            ) as raised:
                await self.service.draft(self.conversation_id, "Make a plan")

        self.assertNotIn("postgresql://secret", str(raised.exception))
        self.assertEqual(self.plans.run.status, "awaiting_approval")
        self.assertIsNotNone(self.plans.run.definition)
        self.assertEqual(self.messages.items, [])

    async def test_content_revision_and_state_checks_happen_before_runner(self) -> None:
        with self.assertRaises(PlanningInputError):
            await self.service.draft(self.conversation_id, "  ")
        with self.assertRaises(PlanningInputError):
            await self.service.draft(self.conversation_id, "x" * 20_001)
        with self.assertRaises(PlanningInputError):
            await self.service.draft(
                self.conversation_id,
                "new draft",
                expected_row_revision=1,
            )

        model = StructuredDraftModel([question_output()])
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            outcome = await self.service.draft(self.conversation_id, "start")
            with self.assertRaises(PlanningConflictError):
                await self.service.draft(
                    self.conversation_id,
                    "follow up",
                    expected_row_revision=outcome.snapshot.row_revision + 1,
                )

        self.plans.run = replace(self.plans.run, status="awaiting_approval")
        with self.assertRaises(InvalidPlanningTransition):
            await self.service.draft(self.conversation_id, "another follow up")

    async def test_follow_up_rejects_frozen_resource_drift(self) -> None:
        model = StructuredDraftModel([question_output()])
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            await self.service.draft(self.conversation_id, "start")

        self.profiles.get.return_value = replace(self.profile, model="changed")
        with self.assertRaisesRegex(PlanningRunError, "profile changed"):
            await self.service.draft(self.conversation_id, "follow up")

        self.profiles.get.return_value = self.profile
        changed_manifest = WorkspaceManifest(
            root=Path(self.temp_directory.name),
            allowed_paths=("src",),
        )
        self.manifest_provider.return_value = changed_manifest
        with self.assertRaisesRegex(PlanningRunError, "workspace changed"):
            await self.service.draft(self.conversation_id, "follow up")

        self.assertEqual(len(self.messages.items), 2)

    async def test_runner_error_does_not_expose_the_original_exception(self) -> None:
        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=object(),
            ),
            patch(
                "agents.chat_backend.planning_service.Runner.run_async",
                side_effect=RuntimeError("secret model response"),
            ),
        ):
            with self.assertRaisesRegex(PlanningRunError, "planning run failed") as raised:
                await self.service.draft(self.conversation_id, "start")

        self.assertNotIn("secret model response", str(raised.exception))
        self.assertEqual(self.messages.items, [])

    async def test_missing_profile_and_invalid_manifest_are_safe(self) -> None:
        self.profiles.active.return_value = None
        with self.assertRaisesRegex(
            PlanningRunError,
            "planning model profile is not configured",
        ):
            await self.service.draft(self.conversation_id, "start")

        self.profiles.active.return_value = self.profile
        self.manifest_provider.return_value = object()
        with self.assertRaisesRegex(
            PlanningRunError,
            "planning workspace could not be loaded",
        ):
            await self.service.draft(self.conversation_id, "start")

        self.assertIsNone(self.plans.run)
        self.assertEqual(self.messages.items, [])

    async def test_revise_reuses_draft_and_saves_readable_feedback(self) -> None:
        old = self.plans.create_draft(
            self.conversation_id,
            "Improve authentication",
            model_profile=self.profile,
            manifest=self.manifest,
        )
        old = self.plans.submit_for_approval(
            old.id,
            old.row_revision,
            execution_definition(),
        )

        def revise(run_id, expected_row_revision, feedback):
            self.assertEqual((run_id, expected_row_revision), (old.id, old.row_revision))
            now = datetime.now(UTC)
            self.plans.run = PlanningRun(
                id=uuid4(),
                conversation_id=old.conversation_id,
                version=old.version + 1,
                row_revision=1,
                status="drafting",
                objective=old.objective,
                supersedes_version=old.version,
                revision_note=feedback,
                last_error=None,
                definition=None,
                task_events=(),
                run_state_snapshot=None,
                model_snapshot=old.model_snapshot,
                workspace_snapshot=old.workspace_snapshot,
                created_at=now,
                updated_at=now,
            )
            return self.plans.run

        self.plans.revise = Mock(side_effect=revise)
        model = StructuredDraftModel([question_output("Which cache should replace it?")])
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            outcome = await self.service.revise(
                old.id,
                old.row_revision,
                "  Replace the database with a file cache  ",
            )

        self.assertEqual(outcome.kind, "question")
        self.assertEqual(outcome.snapshot.status, "drafting")
        self.assertEqual(outcome.snapshot.version, 2)
        self.assertEqual(
            [item.content for item in self.messages.items],
            [
                "Replace the database with a file cache",
                "Which cache should replace it?",
            ],
        )
        self.assertEqual(self.plans.run.model_snapshot, old.model_snapshot)
        self.assertEqual(self.plans.run.workspace_snapshot, old.workspace_snapshot)


class PlanningServiceExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.conversation_id = uuid4()
        self.profile = ModelProfile(
            id=uuid4(),
            name="Execution model",
            provider="ollama",
            protocol="chat_completions",
            base_url="http://localhost:11434/v1",
            model="qwen3",
            api_key="secret-api-key",
            is_active=True,
        )
        self.manifest = WorkspaceManifest(
            root=Path(self.temp_directory.name),
            env={"SECRET_TOKEN": "secret-env-value"},
        )
        self.plans = InMemoryPlanStore()
        self.messages = InMemoryMessageStore()
        self.profiles = Mock()
        self.profiles.get.return_value = self.profile
        self.manifest_provider = Mock(return_value=self.manifest)
        self.service = PlanningService(
            self.plans,
            self.messages,
            self.profiles,
            self.manifest_provider,
        )
        draft = self.plans.create_draft(
            self.conversation_id,
            "Implement the approved change",
            model_profile=self.profile,
            manifest=self.manifest,
        )
        self.awaiting = self.plans.submit_for_approval(
            draft.id,
            draft.row_revision,
            execution_definition(),
        )

    async def test_approve_runs_ordinary_steps_serially_after_persisting_claims(
        self,
    ) -> None:
        model = PersistedStepModel(
            ["Inspection complete", "Change complete"],
            self.plans,
        )
        events: list[PlanningProgressEvent] = []
        self.profiles.get.return_value = replace(
            self.profile,
            api_key="rotated-secret-api-key",
        )
        self.manifest_provider.return_value = WorkspaceManifest(
            root=Path(self.temp_directory.name),
            env={"SECRET_TOKEN": "rotated-secret-env-value"},
        )

        self.assertEqual(
            self.service.get_snapshot(self.awaiting.id).allowed_actions,
            ("approve", "revise", "cancel"),
        )
        worker_threads: list[int] = []

        def verify(policy):
            worker_threads.append(get_ident())
            self.assertEqual(policy.commands, ("python -m pytest",))
            self.assertEqual(policy.max_attempts, 1)
            return (
                VerificationResult(
                    command=policy.commands[0],
                    returncode=0,
                    passed=True,
                    output="secret verification output",
                ),
            )

        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                side_effect=verify,
            ),
        ):
            snapshot = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
                on_event=events.append,
            )

        self.assertEqual(snapshot.status, "completed")
        self.assertEqual(
            snapshot.completed_step_ids,
            ("inspect", "change", "verify"),
        )
        self.assertIsNone(snapshot.current_step_id)
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], get_ident())
        self.assertEqual(
            model.running_before_call,
            [("inspect",), ("change",)],
        )
        self.assertIn("inspect", model.messages[0][-1].content)
        self.assertIn("change", model.messages[1][-1].content)
        self.assertNotIn("Inspection complete", model.messages[0][-1].content)
        self.assertIn("Inspection complete", model.messages[1][-1].content)
        self.assertTrue(events)
        self.assertEqual(
            {event.task_id for event in events},
            {"inspect", "change"},
        )

        execution = PlanExecution.from_events(
            self.plans.run.definition,
            self.plans.run.task_events,
            owner_id=str(self.plans.run.id),
        )
        self.assertEqual(
            execution.task_store.get("verify").status,
            TaskStatus.COMPLETED,
        )
        self.assertNotIn(
            "secret verification output",
            repr(execution.task_store.get("verify")),
        )

    async def test_concurrent_approve_only_enters_runner_once(self) -> None:
        model = PersistedStepModel(
            ["Inspection complete", "Change complete"],
            self.plans,
        )
        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=(
                    VerificationResult(
                        command="python -m pytest",
                        returncode=0,
                        passed=True,
                    ),
                ),
            ),
        ):
            outcomes = await asyncio.gather(
                self.service.approve(
                    self.awaiting.id,
                    self.awaiting.row_revision,
                ),
                self.service.approve(
                    self.awaiting.id,
                    self.awaiting.row_revision,
                ),
                return_exceptions=True,
            )

        self.assertEqual(
            sum(isinstance(item, PlanningSnapshot) for item in outcomes),
            1,
        )
        self.assertEqual(
            sum(
                isinstance(
                    item,
                    (PlanningConflictError, InvalidPlanningTransition),
                )
                for item in outcomes
            ),
            1,
        )
        self.assertEqual(len(model.messages), 2)

    async def test_final_verification_failure_pauses_with_running_task(self) -> None:
        model = PersistedStepModel(
            ["Inspection complete", "Change complete"],
            self.plans,
        )
        secret = "postgresql://verification-secret"
        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=(
                    VerificationResult(
                        command="python -m pytest",
                        returncode=1,
                        passed=False,
                        output=secret,
                    ),
                ),
            ),
        ):
            snapshot = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.current_step_id, "verify")
        self.assertEqual(self.plans.run.last_error, "planning verification failed")
        self.assertNotIn(secret, repr(self.plans.run))
        execution = PlanExecution.from_events(
            self.plans.run.definition,
            self.plans.run.task_events,
            owner_id=str(self.plans.run.id),
        )
        self.assertEqual(
            execution.task_store.get("verify").status,
            TaskStatus.RUNNING,
        )

    async def test_final_verification_exception_pauses_without_leaking(self) -> None:
        model = PersistedStepModel(
            ["Inspection complete", "Change complete"],
            self.plans,
        )
        secret = "C:/secret/verification-runner"
        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                side_effect=RuntimeError(secret),
            ),
        ):
            snapshot = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.current_step_id, "verify")
        self.assertEqual(
            self.plans.run.last_error,
            "planning verification could not run",
        )
        self.assertNotIn(secret, repr(snapshot))
        self.assertNotIn(secret, repr(self.plans.run))

    async def test_final_verification_checks_manifest_after_claiming_task(self) -> None:
        run = self.plans.approve(
            self.awaiting.id,
            self.awaiting.row_revision,
        )
        execution = PlanExecution.from_events(
            run.definition,
            run.task_events,
            owner_id=str(run.id),
        )
        for task_id in ("inspect", "change"):
            node = execution.task_store.get(task_id)
            execution.task_store.transition(
                task_id,
                expected_revision=node.revision,
                to_status=TaskStatus.RUNNING,
            )
            node = execution.task_store.get(task_id)
            execution.task_store.transition(
                task_id,
                expected_revision=node.revision,
                to_status=TaskStatus.COMPLETED,
                result=f"{task_id} complete",
            )
        run = self.plans.save_execution(
            run.id,
            run.row_revision,
            execution=execution,
            status="running",
        )
        self.manifest_provider.return_value = WorkspaceManifest(
            root=Path(self.temp_directory.name),
            default_test_command="python -m unittest",
            env={"SECRET_TOKEN": "secret-env-value"},
        )

        with patch(
            "agents.chat_backend.planning_service.VerificationRunner.run"
        ) as verification:
            snapshot = await self.service._drive_running(run, None)

        verification.assert_not_called()
        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.current_step_id, "verify")
        self.assertEqual(
            self.plans.run.last_error,
            "planning verification could not run",
        )

    async def test_tool_approval_input_rules_are_checked_before_reading(self) -> None:
        cases = (
            {"tool_name": "", "call_id": "call-1", "decision": "approve"},
            {"tool_name": "apply_patch", "call_id": " ", "decision": "approve"},
            {"tool_name": "apply_patch", "call_id": "call-1", "decision": "allow"},
            {"tool_name": "apply_patch", "call_id": "call-1", "decision": "reject"},
            {
                "tool_name": "apply_patch",
                "call_id": "call-1",
                "decision": "approve",
                "rejection_message": "not used",
            },
            {
                "tool_name": "apply_patch",
                "call_id": "call-1",
                "decision": "reject",
                "rejection_message": "x" * 2001,
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(PlanningInputError):
                    await self.service.decide_tool_approval(
                        uuid4(),
                        1,
                        **values,
                    )

    async def test_approve_tool_call_resumes_same_state_and_finishes_plan(self) -> None:
        target = Path(self.temp_directory.name) / "approved.txt"
        model = PersistedStepModel(
            [
                ToolCall(
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: approved.txt\n"
                            "+approved\n"
                            "*** End Patch"
                        )
                    },
                    "call-approve",
                ),
                "Inspection complete",
                "Change complete",
            ],
            self.plans,
        )
        model.previous_response_id = None
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            waiting = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        resume_calls = []
        original_resume = Runner.resume_stream

        def resume(agent, run_state, config):
            resume_calls.append(run_state)
            return original_resume(agent, run_state, config)

        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.Runner.resume_stream",
                side_effect=resume,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=(
                    VerificationResult(
                        command="python -m pytest",
                        returncode=0,
                        passed=True,
                    ),
                ),
            ),
        ):
            snapshot = await self.service.decide_tool_approval(
                waiting.run_id,
                waiting.row_revision,
                "apply_patch",
                "call-approve",
                "approve",
            )

        self.assertEqual(snapshot.status, "completed")
        self.assertEqual(len(resume_calls), 1)
        self.assertEqual(model.previous_response_id, "step-1")
        self.assertEqual(resume_calls[0].task_id, "inspect")
        self.assertEqual(resume_calls[0].task_revision, 2)
        self.assertEqual(
            resume_calls[0].context_wrapper.approval_status_for(
                "apply_patch",
                "call-approve",
            ),
            "approved",
        )
        self.assertTrue(target.exists())

    async def test_reject_tool_call_resumes_without_running_the_tool(self) -> None:
        target = Path(self.temp_directory.name) / "rejected.txt"
        model = PersistedStepModel(
            [
                ToolCall(
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: rejected.txt\n"
                            "+rejected\n"
                            "*** End Patch"
                        )
                    },
                    "call-reject",
                ),
                "Inspection complete",
                "Change complete",
            ],
            self.plans,
        )
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            waiting = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=(
                    VerificationResult(
                        command="python -m pytest",
                        returncode=0,
                        passed=True,
                    ),
                ),
            ),
        ):
            snapshot = await self.service.decide_tool_approval(
                waiting.run_id,
                waiting.row_revision,
                "apply_patch",
                "call-reject",
                "reject",
                "Do not create this file.",
            )

        self.assertEqual(snapshot.status, "completed")
        self.assertFalse(target.exists())

    async def test_wrong_key_and_stale_revision_never_resume(self) -> None:
        model = PersistedStepModel(
            [
                ToolCall(
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: never-created.txt\n"
                            "+blocked\n"
                            "*** End Patch"
                        )
                    },
                    "shared-call-id",
                )
            ],
            self.plans,
        )
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            waiting = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        with patch(
            "agents.chat_backend.planning_service.Runner.resume_stream"
        ) as resume:
            with self.assertRaises(PlanningInputError):
                await self.service.decide_tool_approval(
                    waiting.run_id,
                    waiting.row_revision,
                    "shell_command",
                    "shared-call-id",
                    "approve",
                )
            with self.assertRaises(PlanningConflictError):
                await self.service.decide_tool_approval(
                    waiting.run_id,
                    waiting.row_revision - 1,
                    "apply_patch",
                    "shared-call-id",
                    "approve",
                )

        resume.assert_not_called()
        self.assertEqual(self.plans.run.status, "awaiting_tool_approval")

    async def test_resumed_step_can_pause_for_another_approval(self) -> None:
        model = PersistedStepModel(
            [
                ToolCall(
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: first.txt\n"
                            "+first\n"
                            "*** End Patch"
                        )
                    },
                    "call-first",
                ),
                ToolCall(
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: second.txt\n"
                            "+second\n"
                            "*** End Patch"
                        )
                    },
                    "call-second",
                ),
            ],
            self.plans,
        )
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            waiting = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )
            snapshot = await self.service.decide_tool_approval(
                waiting.run_id,
                waiting.row_revision,
                "apply_patch",
                "call-first",
                "approve",
            )

        self.assertEqual(snapshot.status, "awaiting_tool_approval")
        self.assertEqual(snapshot.current_step_id, "inspect")
        self.assertEqual(
            snapshot.pending_approval_keys,
            (("apply_patch", "call-second"),),
        )

    async def test_model_failure_pauses_without_failing_the_current_task(
        self,
    ) -> None:
        secret = "postgresql://user:password@secret-host/database"
        model = PersistedStepModel([RuntimeError(secret)], self.plans)
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            snapshot = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.current_step_id, "inspect")
        self.assertEqual(snapshot.last_error, "planning model step failed")
        self.assertEqual(self.plans.run.last_error, "planning model step failed")
        self.assertNotIn(secret, repr(snapshot))
        self.assertNotIn(secret, repr(self.plans.run))

        execution = PlanExecution.from_events(
            self.plans.run.definition,
            self.plans.run.task_events,
            owner_id=str(self.plans.run.id),
        )
        statuses = {node.status for node in execution.task_store.list()}
        self.assertIn(TaskStatus.RUNNING, statuses)
        self.assertNotIn(TaskStatus.FAILED, statuses)

    async def test_retry_current_fresh_runs_the_same_ordinary_task_after_cas(
        self,
    ) -> None:
        model = PersistedStepModel(
            [RuntimeError("first attempt failed"), "Inspection complete", "Change complete"],
            self.plans,
        )
        passed = (
            VerificationResult(
                command="python -m pytest",
                returncode=0,
                passed=True,
            ),
        )
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            paused = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=passed,
            ),
            patch(
                "agents.chat_backend.planning_service.Runner.resume_stream"
            ) as resume,
        ):
            with self.assertRaises(PlanningConflictError):
                await self.service.retry_current(
                    paused.run_id,
                    paused.row_revision - 1,
                )
            completed = await self.service.retry_current(
                paused.run_id,
                paused.row_revision,
            )

        resume.assert_not_called()
        self.assertEqual(completed.status, "completed")
        self.assertEqual(
            model.running_before_call,
            [("inspect",), ("inspect",), ("change",)],
        )

    async def test_retry_current_repeats_final_verification_without_model(self) -> None:
        model = PersistedStepModel(
            ["Inspection complete", "Change complete"],
            self.plans,
        )
        failed = (
            VerificationResult(
                command="python -m pytest",
                returncode=1,
                passed=False,
            ),
        )
        passed = (
            VerificationResult(
                command="python -m pytest",
                returncode=0,
                passed=True,
            ),
        )
        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=failed,
            ),
        ):
            paused = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        with (
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=passed,
            ),
            patch(
                "agents.chat_backend.planning_service.Runner.run_stream"
            ) as fresh,
        ):
            completed = await self.service.retry_current(
                paused.run_id,
                paused.row_revision,
            )

        fresh.assert_not_called()
        self.assertEqual(paused.current_step_id, "verify")
        self.assertEqual(completed.status, "completed")

    async def test_recover_running_fresh_replays_the_persisted_current_task(
        self,
    ) -> None:
        running = self.plans.approve(
            self.awaiting.id,
            self.awaiting.row_revision,
        )
        execution = PlanExecution.from_events(
            running.definition,
            running.task_events,
            owner_id=str(running.id),
        )
        current = execution.task_store.get("inspect")
        execution.task_store.transition(
            current.task_id,
            expected_revision=current.revision,
            to_status=TaskStatus.RUNNING,
        )
        self.plans.run = replace(
            running,
            row_revision=running.row_revision + 1,
            task_events=execution.task_store.events(),
        )
        model = PersistedStepModel(
            ["Inspection complete", "Change complete"],
            self.plans,
        )
        passed = (
            VerificationResult(
                command="python -m pytest",
                returncode=0,
                passed=True,
            ),
        )

        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=model,
            ),
            patch(
                "agents.chat_backend.planning_service.VerificationRunner.run",
                return_value=passed,
            ),
        ):
            report = await self.service.recover_running()

        self.assertEqual(report.scanned, 1)
        self.assertEqual(report.recovered_run_ids, (running.id,))
        self.assertEqual(report.deferred_run_ids, ())
        self.assertEqual(self.plans.run.status, "completed")
        self.assertEqual(
            model.running_before_call,
            [("inspect",), ("change",)],
        )

    async def test_cancel_and_control_inputs_are_safe(self) -> None:
        for invoke in (
            lambda: self.service.retry_current("bad", 1),
            lambda: self.service.revise(uuid4(), 1, "   "),
            lambda: self.service.cancel(uuid4(), 1, "x" * 20_001),
        ):
            with self.subTest(invoke=invoke), self.assertRaises(PlanningInputError):
                await invoke()

        cancelled = replace(
            self.awaiting,
            row_revision=self.awaiting.row_revision + 1,
            status="cancelled",
            revision_note="Stop the plan",
        )
        self.plans.cancel = Mock(return_value=cancelled)

        snapshot = await self.service.cancel(
            self.awaiting.id,
            self.awaiting.row_revision,
            "  Stop the plan  ",
        )

        self.assertEqual(snapshot.status, "cancelled")
        self.assertEqual(snapshot.allowed_actions, ())
        self.plans.cancel.assert_called_once_with(
            self.awaiting.id,
            self.awaiting.row_revision,
            "Stop the plan",
        )

    async def test_tool_error_and_oversized_result_pause_safely(self) -> None:
        for actions, expected_error in (
            (
                [
                    ToolCall(
                        "read_workspace_file",
                        {"path": "missing.txt"},
                        "call-missing",
                    ),
                    "Ignored final output",
                ],
                "planning tool step failed",
            ),
            (["x" * 20_001], "planning model step failed"),
        ):
            with self.subTest(expected_error=expected_error):
                self.setUp()
                model = PersistedStepModel(actions, self.plans)
                with patch(
                    "agents.chat_backend.planning_service.model_for_profile",
                    return_value=model,
                ):
                    snapshot = await self.service.approve(
                        self.awaiting.id,
                        self.awaiting.row_revision,
                    )

                self.assertEqual(snapshot.status, "paused")
                self.assertEqual(snapshot.current_step_id, "inspect")
                self.assertEqual(self.plans.run.last_error, expected_error)

    async def test_pending_tool_approval_saves_the_bound_v3_snapshot(self) -> None:
        model = PersistedStepModel(
            [
                ToolCall(
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: approval-demo.txt\n"
                            "+demo\n"
                            "*** End Patch"
                        )
                    },
                    "call-approval",
                )
            ],
            self.plans,
        )
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=model,
        ):
            snapshot = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        self.assertEqual(snapshot.status, "awaiting_tool_approval")
        self.assertEqual(snapshot.current_step_id, "inspect")
        self.assertEqual(
            snapshot.pending_approval_keys,
            (("apply_patch", "call-approval"),),
        )
        self.assertEqual(self.plans.run.run_state_snapshot.task_id, "inspect")
        self.assertEqual(
            self.plans.run.run_state_snapshot.task_revision,
            2,
        )
        self.assertIn("approval-demo.txt", snapshot.pending_approvals[0].summary)
        self.assertNotIn("+demo", repr(snapshot))

    async def test_frozen_resource_drift_pauses_after_the_task_is_claimed(
        self,
    ) -> None:
        self.profiles.get.return_value = replace(self.profile, model="changed")
        model_factory = Mock(return_value=object())
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            model_factory,
        ):
            snapshot = await self.service.approve(
                self.awaiting.id,
                self.awaiting.row_revision,
            )

        self.assertEqual(snapshot.status, "paused")
        self.assertEqual(snapshot.current_step_id, "inspect")
        self.assertEqual(self.plans.run.last_error, "planning model step failed")
        model_factory.assert_not_called()


class PlanningServiceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plans = Mock()
        self.service = PlanningService(
            self.plans,
            Mock(),
            Mock(),
            Mock(),
        )
        self.now = datetime.now(UTC)

    def running(self, run_id=None) -> PlanningRun:
        run_id = run_id or uuid4()
        definition = execution_definition()
        execution = PlanExecution.materialize(
            definition,
            owner_id=str(run_id),
        )
        return PlanningRun(
            id=run_id,
            conversation_id=uuid4(),
            version=1,
            row_revision=2,
            status="running",
            objective=definition.objective,
            supersedes_version=None,
            revision_note=None,
            last_error=None,
            definition=definition,
            task_events=execution.task_store.events(),
            run_state_snapshot=None,
            model_snapshot={},
            workspace_snapshot={},
            created_at=self.now,
            updated_at=self.now,
        )

    async def test_recover_running_is_serial_and_defers_temporary_failures(
        self,
    ) -> None:
        first, second, third = self.running(), self.running(), self.running()
        self.plans.list_recoverable_running.return_value = (
            first,
            second,
            third,
        )
        self.plans.get.side_effect = (first, second, third)
        order = []

        async def drive(run, on_event):
            order.append(run.id)
            if run.id == second.id:
                raise RuntimeError("temporary database failure")
            return Mock(status="paused")

        self.service._drive_running = AsyncMock(side_effect=drive)

        report = await self.service.recover_running(limit=3)

        self.assertEqual(order, [first.id, second.id, third.id])
        self.assertEqual(report.scanned, 3)
        self.assertEqual(report.recovered_run_ids, (first.id, third.id))
        self.assertEqual(report.isolated_run_ids, ())
        self.assertEqual(report.deferred_run_ids, (second.id,))

    async def test_recover_running_quarantines_corrupt_row_then_rescans(
        self,
    ) -> None:
        corrupt_id = uuid4()
        valid = self.running()
        self.plans.list_recoverable_running.side_effect = (
            CorruptPlanningRunError(corrupt_id, "task events invalid"),
            (valid,),
        )
        self.plans.get.return_value = valid
        self.service._drive_running = AsyncMock(return_value=Mock(status="completed"))

        report = await self.service.recover_running(limit=2)

        self.assertEqual(report.scanned, 2)
        self.assertEqual(report.recovered_run_ids, (valid.id,))
        self.assertEqual(report.isolated_run_ids, (corrupt_id,))
        self.assertEqual(report.deferred_run_ids, ())
        self.assertEqual(
            self.plans.list_recoverable_running.call_args_list,
            [unittest.mock.call(limit=2), unittest.mock.call(limit=1)],
        )
        self.plans.quarantine_running.assert_called_once_with(
            corrupt_id,
            "planning recovery failed",
        )

    async def test_recover_running_validates_limit_before_scanning(self) -> None:
        for limit in (True, 0, 101):
            with self.subTest(limit=limit), self.assertRaises(PlanningInputError):
                await self.service.recover_running(limit=limit)

        self.plans.list_recoverable_running.assert_not_called()


if __name__ == "__main__":
    unittest.main()
