from __future__ import annotations

import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from agents.chat_backend.conversation import (
    ConversationArchivedError,
    ConversationNotFoundError,
)
from agents.chat_backend.model_profiles import ModelProfile
from agents.chat_backend.planning_store import (
    _ACTIVE_PLANNING_STATUSES,
    _PLANNING_RUN_COLUMNS,
    _PLANNING_VERSION_COLUMNS,
    _planning_run_from_row,
    _planning_version_from_row,
    ActivePlanningRunError,
    CorruptPlanningRunError,
    InvalidPlanningTransition,
    PlanningConflictError,
    PlanningRunNotFoundError,
    PlanStore,
)
from agents.planning import (
    PlanDefinition,
    PlanExecution,
    PlanStepSpec,
    plan_definition_to_dict,
)
from agents.run_state import (
    ApprovalSnapshot,
    RunStateSnapshot,
    run_state_snapshot_to_dict,
)
from agents.task_events import task_event_from_dict, task_event_to_dict
from agents.task_state import TaskStatus
from agents.workspace_manifest import WorkspaceManifest


class _Result:
    def __init__(
        self,
        one: object = None,
        many: tuple[tuple[object, ...], ...] = (),
    ) -> None:
        self.one = one
        self.many = many

    def fetchone(self) -> object:
        return self.one

    def fetchall(self) -> tuple[tuple[object, ...], ...]:
        return self.many


class _ScriptedConnection:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] = (),
    ) -> _Result:
        self.calls.append((statement, parameters))
        if not self.responses:
            raise AssertionError("unexpected SQL call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(statement, parameters)
        if not isinstance(response, _Result):
            raise AssertionError("scripted SQL response must be a _Result")
        return response


class _ScriptedDatabase:
    def __init__(self, connection: _ScriptedConnection) -> None:
        self._connection = connection
        self.connection_calls = 0
        self.transaction_calls = 0
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def connection(self) -> Iterator[_ScriptedConnection]:
        self.connection_calls += 1
        yield self._connection

    @contextmanager
    def transaction(self) -> Iterator[_ScriptedConnection]:
        self.transaction_calls += 1
        try:
            yield self._connection
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


class _UniqueViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = SimpleNamespace(constraint_name=constraint_name)
        super().__init__(constraint_name)


class PlanningStoreValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_id = uuid4()
        self.conversation_id = uuid4()
        self.now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    def profile(self, api_key: str = "secret-model-key") -> ModelProfile:
        return ModelProfile(
            id=uuid4(),
            name="Test model",
            provider="openai",
            protocol="responses",
            base_url="https://api.openai.com/v1",
            model="test-model",
            api_key=api_key,
            is_active=True,
        )

    def manifest(self, secret: str = "secret-env-value") -> WorkspaceManifest:
        return WorkspaceManifest(
            root=Path.cwd(),
            allowed_paths=("src", "tests"),
            env={"SECRET_TOKEN": secret},
        )

    def definition(self) -> PlanDefinition:
        return PlanDefinition(
            objective="Persist a plan",
            steps=(
                PlanStepSpec("work", "Work", "Do work", "Work is done"),
                PlanStepSpec(
                    "verify",
                    "Verify",
                    "Run checks",
                    "Checks pass",
                    ("work",),
                    True,
                ),
            ),
        )

    def execution(
        self,
        state: str = "initial",
        owner_id: UUID | None = None,
    ) -> PlanExecution:
        execution = PlanExecution.materialize(
            self.definition(),
            owner_id=str(owner_id or self.run_id),
        )
        if state == "initial":
            return execution

        work = execution.task_store.get("work")
        execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.RUNNING,
        )
        if state == "awaiting":
            return execution

        work = execution.task_store.get("work")
        if state == "failed":
            execution.task_store.transition(
                "work",
                expected_revision=work.revision,
                to_status=TaskStatus.FAILED,
                error="work failed",
            )
            verify = execution.task_store.get("verify")
            execution.task_store.transition(
                "verify",
                expected_revision=verify.revision,
                to_status=TaskStatus.CANCELLED,
            )
            return execution

        execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.COMPLETED,
            result={"work": "done"},
        )
        if state == "boundary":
            return execution

        verify = execution.task_store.get("verify")
        if state == "isolated":
            execution.task_store.transition(
                "verify",
                expected_revision=verify.revision,
                to_status=TaskStatus.CANCELLED,
            )
            return execution

        execution.task_store.transition(
            "verify",
            expected_revision=verify.revision,
            to_status=TaskStatus.RUNNING,
        )
        verify = execution.task_store.get("verify")
        execution.task_store.transition(
            "verify",
            expected_revision=verify.revision,
            to_status=TaskStatus.COMPLETED,
            result={"checks": "passed"},
        )
        return execution

    def event_payloads(
        self,
        owner_id: UUID | None = None,
        state: str = "initial",
    ) -> list[dict[str, object]]:
        execution = self.execution(state, owner_id)
        return [
            task_event_to_dict(event) for event in execution.task_store.events()
        ]

    def snapshot(self, status: str = "pending") -> RunStateSnapshot:
        return RunStateSnapshot(
            input="continue",
            last_agent_name="coding",
            last_response_id=None,
            current_turn=1,
            steps_taken=1,
            max_turns=None,
            max_steps=None,
            tool_approvals=(
                ApprovalSnapshot(
                    tool_name="edit_local",
                    call_id="call-1",
                    arguments={"path": "safe.py"},
                    status=status,  # type: ignore[arg-type]
                ),
            ),
            model_responses=(),
            new_items=(),
            task_id="work",
            task_revision=2,
        )

    def snapshot_payload(self, status: str = "pending") -> dict[str, object]:
        return run_state_snapshot_to_dict(self.snapshot(status))

    def row(
        self,
        status: str = "drafting",
        *,
        definition: object = None,
        events: object = None,
        run_state: object = None,
        last_error: object = None,
        model_snapshot: object = None,
        workspace_snapshot: object = None,
    ) -> tuple[object, ...]:
        return (
            self.run_id,
            self.conversation_id,
            1,
            1,
            status,
            "Persist a plan",
            None,
            None,
            last_error,
            definition,
            [] if events is None else events,
            run_state,
            {"model": "test"} if model_snapshot is None else model_snapshot,
            (
                {"workspace_root": "C:/safe"}
                if workspace_snapshot is None
                else workspace_snapshot
            ),
            self.now,
            self.now,
        )

    def saved_execution_result(
        self,
        current: tuple[object, ...],
    ) -> object:
        def result(
            statement: str,
            parameters: tuple[object, ...],
        ) -> _Result:
            self.assertIn("UPDATE planning_runs", statement)
            updated = list(current)
            updated[3] = current[3] + 1
            updated[4] = parameters[0]
            updated[8] = parameters[3]
            updated[10] = parameters[1].obj
            updated[11] = (
                parameters[2].obj if parameters[2] is not None else None
            )
            return _Result(tuple(updated))

        return result

    def assert_corrupt(
        self,
        row: tuple[object, ...] | list[object],
        reason: str,
    ) -> CorruptPlanningRunError:
        with self.assertRaises(CorruptPlanningRunError) as caught:
            _planning_run_from_row(row)
        self.assertEqual(caught.exception.reason, reason)
        self.assertIsNotNone(caught.exception.__cause__)
        return caught.exception

    def test_fixed_column_projections_match_the_migration_order(self) -> None:
        self.assertEqual(
            _PLANNING_RUN_COLUMNS,
            "id, conversation_id, version, row_revision, status, objective, "
            "supersedes_version, revision_note, last_error, definition, "
            "task_events, run_state_snapshot, model_snapshot, "
            "workspace_snapshot, created_at, updated_at",
        )
        self.assertEqual(
            _PLANNING_VERSION_COLUMNS,
            "id, conversation_id, version, row_revision, status, objective, "
            "supersedes_version, revision_note, created_at, updated_at",
        )

    def test_all_legal_status_shapes_decode(self) -> None:
        definition = plan_definition_to_dict(self.definition())
        events = self.event_payloads()
        rows = {
            "drafting": self.row(),
            "awaiting_approval": self.row(
                "awaiting_approval",
                definition=definition,
            ),
            "running": self.row(
                "running",
                definition=definition,
                events=events,
            ),
            "awaiting_tool_approval": self.row(
                "awaiting_tool_approval",
                definition=definition,
                events=self.event_payloads(state="awaiting"),
                run_state=self.snapshot_payload(),
            ),
            "paused": self.row(
                "paused",
                definition=definition,
                events=self.event_payloads(state="awaiting"),
                last_error="planning model step failed",
            ),
            "completed": self.row(
                "completed",
                definition=definition,
                events=self.event_payloads(state="completed"),
            ),
            "failed": self.row(
                "failed",
                definition=definition,
                events=self.event_payloads(state="isolated"),
                last_error="model failed",
            ),
            "cancelled": self.row("cancelled"),
            "superseded": self.row(
                "superseded",
                definition=definition,
                events=self.event_payloads(state="isolated"),
            ),
        }

        for status, row in rows.items():
            with self.subTest(status=status):
                run = _planning_run_from_row(row)
                self.assertEqual(run.status, status)
                self.assertIsInstance(run.task_events, tuple)

    def test_running_accepts_one_current_task_and_rejects_two(self) -> None:
        definition = self.definition()
        current = _planning_run_from_row(
            self.row(
                "running",
                definition=plan_definition_to_dict(definition),
                events=self.event_payloads(state="awaiting"),
            )
        )
        self.assertEqual(current.status, "running")

        parallel_definition = PlanDefinition(
            objective="Reject two running tasks",
            steps=(
                PlanStepSpec("first", "First", "Run first", "First done"),
                PlanStepSpec("second", "Second", "Run second", "Second done"),
                PlanStepSpec(
                    "verify",
                    "Verify",
                    "Run checks",
                    "Checks pass",
                    ("first", "second"),
                    True,
                ),
            ),
        )
        execution = PlanExecution.materialize(
            parallel_definition,
            owner_id=str(self.run_id),
        )
        for task_id in ("first", "second"):
            node = execution.task_store.get(task_id)
            execution.task_store.transition(
                task_id,
                expected_revision=node.revision,
                to_status=TaskStatus.RUNNING,
            )
        self.assert_corrupt(
            self.row(
                "running",
                definition=plan_definition_to_dict(parallel_definition),
                events=[
                    task_event_to_dict(event)
                    for event in execution.task_store.events()
                ],
            ),
            "plan execution invalid",
        )
        self.assert_corrupt(
            self.row(
                "paused",
                definition=plan_definition_to_dict(parallel_definition),
                events=[
                    task_event_to_dict(event)
                    for event in execution.task_store.events()
                ],
                last_error="planning model step failed",
            ),
            "plan execution invalid",
        )

    def test_full_row_decodes_existing_codecs_and_copies_snapshots(self) -> None:
        model = {"model": "test", "limits": {"tokens": 10}}
        workspace = {"workspace_root": "C:/safe", "allowed": ["src"]}
        run = _planning_run_from_row(
            self.row(
                "awaiting_tool_approval",
                definition=plan_definition_to_dict(self.definition()),
                events=self.event_payloads(state="awaiting"),
                run_state=self.snapshot_payload(),
                model_snapshot=model,
                workspace_snapshot=workspace,
            )
        )

        self.assertEqual(run.definition, self.definition())
        self.assertEqual(len(run.task_events), 3)
        self.assertEqual(run.run_state_snapshot.task_id, "work")
        self.assertIsNot(run.model_snapshot, model)
        self.assertIsNot(run.workspace_snapshot, workspace)
        model["model"] = "changed"
        workspace["workspace_root"] = "changed"
        self.assertEqual(run.model_snapshot["model"], "test")
        self.assertEqual(run.workspace_snapshot["workspace_root"], "C:/safe")

    def test_planning_run_is_frozen(self) -> None:
        run = _planning_run_from_row(self.row())
        with self.assertRaises(FrozenInstanceError):
            run.status = "cancelled"  # type: ignore[misc]

    def test_history_row_decodes_only_summary_fields(self) -> None:
        row = (
            self.run_id,
            self.conversation_id,
            2,
            3,
            "drafting",
            "Revise the plan",
            1,
            "Use the feedback",
            self.now,
            self.now,
        )

        version = _planning_version_from_row(row)

        self.assertEqual(version.id, self.run_id)
        self.assertEqual(version.version, 2)
        self.assertEqual(version.supersedes_version, 1)
        self.assertFalse(hasattr(version, "definition"))
        self.assertFalse(hasattr(version, "task_events"))

    def test_invalid_scalar_rows_are_rejected_before_json_decoding(self) -> None:
        valid = list(self.row())
        cases = {
            "missing column": valid[:-1],
            "invalid id": ["not-a-uuid", *valid[1:]],
            "invalid conversation": [valid[0], "not-a-uuid", *valid[2:]],
            "boolean version": [*valid[:2], True, *valid[3:]],
            "zero revision": [*valid[:3], 0, *valid[4:]],
            "unknown status": [*valid[:4], "sleeping", *valid[5:]],
            "blank objective": [*valid[:5], "   ", *valid[6:]],
            "naive timestamp": [*valid[:14], datetime(2026, 8, 8), valid[15]],
        }

        for name, row in cases.items():
            with self.subTest(name=name):
                self.assert_corrupt(row, "row values invalid")

    def test_invalid_definition_is_safely_wrapped(self) -> None:
        payload = plan_definition_to_dict(self.definition())
        payload["api_key"] = "never-print-this"
        error = self.assert_corrupt(
            self.row("awaiting_approval", definition=payload),
            "definition invalid",
        )

        self.assertEqual(error.run_id, self.run_id)
        self.assertNotIn("never-print-this", str(error))
        self.assertNotIn("api_key", str(error))

    def test_invalid_event_schema_and_owner_are_safely_wrapped(self) -> None:
        definition = plan_definition_to_dict(self.definition())
        bad_schema = deepcopy(self.event_payloads())
        bad_schema[0]["schema_version"] = 999
        self.assert_corrupt(
            self.row("running", definition=definition, events=bad_schema),
            "task events invalid",
        )

        wrong_owner = self.event_payloads(uuid4())
        self.assert_corrupt(
            self.row("running", definition=definition, events=wrong_owner),
            "plan execution invalid",
        )

    def test_snapshot_schema_and_pending_approval_are_checked(self) -> None:
        definition = plan_definition_to_dict(self.definition())
        events = self.event_payloads()
        unknown_schema = self.snapshot_payload()
        unknown_schema["schema_version"] = 999
        self.assert_corrupt(
            self.row(
                "awaiting_tool_approval",
                definition=definition,
                events=events,
                run_state=unknown_schema,
            ),
            "run state snapshot invalid",
        )

        self.assert_corrupt(
            self.row(
                "awaiting_tool_approval",
                definition=definition,
                events=events,
                run_state=self.snapshot_payload("approved"),
            ),
            "status combination invalid",
        )

    def test_status_combinations_are_checked_even_without_postgres(self) -> None:
        definition = plan_definition_to_dict(self.definition())
        events = self.event_payloads()
        cases = (
            self.row("drafting", definition=definition),
            self.row("awaiting_approval"),
            self.row("running", definition=definition),
            self.row(
                "running",
                definition=definition,
                events=events,
                run_state=self.snapshot_payload(),
            ),
            self.row(
                "failed",
                definition=definition,
                events=events,
            ),
            self.row(
                "paused",
                definition=definition,
                events=self.event_payloads(state="awaiting"),
            ),
            self.row(
                "paused",
                definition=definition,
                events=self.event_payloads(state="awaiting"),
                run_state=self.snapshot_payload(),
                last_error="planning model step failed",
            ),
            self.row("cancelled", events=events),
        )

        for row in cases:
            with self.subTest(status=row[4]):
                self.assert_corrupt(row, "status combination invalid")

    def test_version_row_rejects_invalid_fields(self) -> None:
        row = [
            self.run_id,
            self.conversation_id,
            1,
            1,
            "drafting",
            "Persist a plan",
            1,
            None,
            self.now,
            self.now,
        ]
        with self.assertRaises(CorruptPlanningRunError) as caught:
            _planning_version_from_row(row)
        self.assertEqual(caught.exception.reason, "version row values invalid")
        self.assertIsNotNone(caught.exception.__cause__)

    def test_stable_errors_keep_only_their_public_context(self) -> None:
        missing = PlanningRunNotFoundError(self.run_id)
        active = ActivePlanningRunError(self.conversation_id)
        conflict = PlanningConflictError(self.run_id, 2, 3)
        transition = InvalidPlanningTransition(
            self.run_id,
            "drafting",
            "approve",
            "running",
        )

        self.assertEqual(missing.run_id, self.run_id)
        self.assertEqual(active.conversation_id, self.conversation_id)
        self.assertEqual(conflict.expected_revision, 2)
        self.assertEqual(conflict.actual_revision, 3)
        self.assertEqual(transition.operation, "approve")
        self.assertEqual(transition.target_status, "running")

    def test_plan_store_constructor_only_keeps_the_database_reference(self) -> None:
        database = object()

        store = PlanStore(database)  # type: ignore[arg-type]

        self.assertIs(store._database, database)

    def test_public_inputs_fail_before_database_access(self) -> None:
        definition = self.definition()
        cases = (
            lambda store: store.get("not-a-uuid"),
            lambda store: store.get_active("not-a-uuid"),
            lambda store: store.get_latest("not-a-uuid"),
            lambda store: store.list_versions(self.conversation_id, limit=0),
            lambda store: store.list_versions(self.conversation_id, limit=True),
            lambda store: store.create_draft(
                self.conversation_id,
                "   ",
                model_profile=self.profile(),
                manifest=self.manifest(),
            ),
            lambda store: store.create_draft(
                self.conversation_id,
                "Persist a plan",
                model_profile=object(),
                manifest=self.manifest(),
            ),
            lambda store: store.submit_for_approval(
                self.run_id,
                0,
                definition,
            ),
            lambda store: store.submit_for_approval(
                self.run_id,
                1,
                object(),
            ),
            lambda store: store.approve(self.run_id, 0),
            lambda store: store.revise(self.run_id, 1, "   "),
            lambda store: store.cancel(self.run_id, 1, "   "),
            lambda store: store.list_recoverable_running(limit=0),
            lambda store: store.save_execution(
                self.run_id,
                0,
                execution=self.execution(),
                status="running",
            ),
            lambda store: store.save_execution(
                self.run_id,
                1,
                execution=object(),
                status="running",
            ),
            lambda store: store.save_execution(
                self.run_id,
                1,
                execution=self.execution(),
                status="paused",
            ),
            lambda store: store.save_execution(
                self.run_id,
                1,
                execution=self.execution("awaiting"),
                status="awaiting_tool_approval",
            ),
            lambda store: store.save_execution(
                self.run_id,
                1,
                execution=self.execution(),
                status="running",
                run_state=self.snapshot(),
            ),
            lambda store: store.save_execution(
                self.run_id,
                1,
                execution=self.execution("failed"),
                status="failed",
                last_error="   ",
            ),
        )

        for invoke in cases:
            with self.subTest(call=invoke):
                connection = _ScriptedConnection()
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises((TypeError, ValueError)):
                    invoke(store)
                self.assertEqual(database.connection_calls, 0)
                self.assertEqual(database.transaction_calls, 0)
                self.assertEqual(connection.calls, [])

    def test_create_draft_writes_safe_snapshots_and_switches_mode(self) -> None:
        profile = self.profile()
        manifest = self.manifest()
        model_snapshot = profile.public_dict()
        workspace_snapshot = manifest.metadata()
        connection = _ScriptedConnection(
            _Result(("active", "default")),
            _Result(None),
            _Result((1,)),
            _Result(
                self.row(
                    model_snapshot=model_snapshot,
                    workspace_snapshot=workspace_snapshot,
                )
            ),
            _Result(),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with patch(
            "agents.chat_backend.planning_store.uuid4",
            return_value=self.run_id,
        ):
            run = store.create_draft(
                self.conversation_id,
                "  Persist a plan  ",
                model_profile=profile,
                manifest=manifest,
            )

        self.assertEqual(run.status, "drafting")
        self.assertEqual(run.objective, "Persist a plan")
        self.assertEqual(database.transaction_calls, 1)
        self.assertEqual(database.commits, 1)
        self.assertIn("FOR UPDATE", connection.calls[0][0])
        self.assertIn("status IN", connection.calls[1][0])
        self.assertIn("MAX(version)", connection.calls[2][0])
        self.assertIn("INSERT INTO planning_runs", connection.calls[3][0])
        self.assertIn("SET mode = 'plan'", connection.calls[4][0])
        insert_parameters = connection.calls[3][1]
        saved_model = insert_parameters[4].obj
        saved_workspace = insert_parameters[5].obj
        saved_text = repr((saved_model, saved_workspace))
        self.assertNotIn("secret-model-key", saved_text)
        self.assertNotIn("secret-env-value", saved_text)
        self.assertTrue(saved_model["has_api_key"])
        self.assertEqual(saved_workspace["env_keys"], ["SECRET_TOKEN"])

    def test_create_draft_rejects_unavailable_conversations(self) -> None:
        cases = (
            (
                (_Result(None),),
                ConversationNotFoundError,
            ),
            (
                (_Result(("archived", "default")),),
                ConversationArchivedError,
            ),
            (
                (_Result(("active", "plan")), _Result((1,))),
                ActivePlanningRunError,
            ),
        )

        for responses, error in cases:
            with self.subTest(error=error):
                connection = _ScriptedConnection(*responses)
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(error):
                    store.create_draft(
                        self.conversation_id,
                        "Persist a plan",
                        model_profile=self.profile(),
                        manifest=self.manifest(),
                    )
                self.assertEqual(database.rollbacks, 1)

    def test_create_draft_maps_only_the_active_unique_constraint(self) -> None:
        for constraint, expected in (
            ("planning_runs_active_conversation_key", ActivePlanningRunError),
            ("planning_runs_conversation_id_version_key", _UniqueViolation),
        ):
            with self.subTest(constraint=constraint):
                connection = _ScriptedConnection(
                    _Result(("active", "default")),
                    _Result(None),
                    _Result((1,)),
                    _UniqueViolation(constraint),
                )
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with patch(
                    "agents.chat_backend.planning_store.UniqueViolation",
                    _UniqueViolation,
                ):
                    with self.assertRaises(expected) as caught:
                        store.create_draft(
                            self.conversation_id,
                            "Persist a plan",
                            model_profile=self.profile(),
                            manifest=self.manifest(),
                        )
                self.assertEqual(database.rollbacks, 1)
                if expected is ActivePlanningRunError:
                    self.assertIsInstance(caught.exception.__cause__, _UniqueViolation)

    def test_create_draft_update_failure_uses_transaction_rollback(self) -> None:
        connection = _ScriptedConnection(
            _Result(("active", "default")),
            _Result(None),
            _Result((1,)),
            _Result(self.row()),
            RuntimeError("mode update failed"),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with patch(
            "agents.chat_backend.planning_store.uuid4",
            return_value=self.run_id,
        ):
            with self.assertRaisesRegex(RuntimeError, "mode update failed"):
                store.create_draft(
                    self.conversation_id,
                    "Persist a plan",
                    model_profile=self.profile(),
                    manifest=self.manifest(),
                )

        self.assertEqual(database.rollbacks, 1)
        self.assertEqual(database.commits, 0)

    def test_get_returns_full_run_and_raises_only_for_missing_id(self) -> None:
        connection = _ScriptedConnection(_Result(self.row()))
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        run = store.get(self.run_id)

        self.assertEqual(run.id, self.run_id)
        self.assertIn(_PLANNING_RUN_COLUMNS, connection.calls[0][0])
        missing_connection = _ScriptedConnection(_Result(None))
        missing_store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(missing_connection)
        )
        with self.assertRaises(PlanningRunNotFoundError):
            missing_store.get(self.run_id)

    def test_get_active_returns_run_or_none_with_shared_statuses(self) -> None:
        connection = _ScriptedConnection(_Result(self.row()))
        store = PlanStore(_ScriptedDatabase(connection))  # type: ignore[arg-type]

        run = store.get_active(self.conversation_id)

        self.assertEqual(run.id, self.run_id)
        self.assertEqual(
            connection.calls[0][1][1:],
            _ACTIVE_PLANNING_STATUSES,
        )
        empty_store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(_ScriptedConnection(_Result(None)))
        )
        self.assertIsNone(empty_store.get_active(self.conversation_id))

    def test_get_latest_returns_terminal_or_active_latest_run(self) -> None:
        connection = _ScriptedConnection(
            _Result(
                self.row(
                    "completed",
                    definition=plan_definition_to_dict(self.definition()),
                    events=self.event_payloads(state="completed"),
                )
            )
        )
        store = PlanStore(_ScriptedDatabase(connection))  # type: ignore[arg-type]

        run = store.get_latest(self.conversation_id)

        self.assertEqual(run.status, "completed")
        statement, parameters = connection.calls[0]
        self.assertIn(_PLANNING_RUN_COLUMNS, statement)
        self.assertIn("ORDER BY version DESC, id DESC", statement)
        self.assertNotIn("status IN", statement)
        self.assertEqual(parameters, (self.conversation_id,))
        empty = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(_ScriptedConnection(_Result(None)))
        )
        self.assertIsNone(empty.get_latest(self.conversation_id))

    def test_list_versions_reads_only_summaries_in_descending_order(self) -> None:
        second_id = uuid4()
        newest = (
            second_id,
            self.conversation_id,
            2,
            1,
            "drafting",
            "Second",
            None,
            None,
            self.now,
            self.now,
        )
        oldest = (
            self.run_id,
            self.conversation_id,
            1,
            2,
            "completed",
            "First",
            None,
            None,
            self.now,
            self.now,
        )
        connection = _ScriptedConnection(_Result(many=(newest, oldest)))
        store = PlanStore(_ScriptedDatabase(connection))  # type: ignore[arg-type]

        versions = store.list_versions(self.conversation_id, limit=2)

        self.assertEqual(tuple(item.version for item in versions), (2, 1))
        statement, parameters = connection.calls[0]
        self.assertIn(_PLANNING_VERSION_COLUMNS, statement)
        self.assertNotIn("definition", statement)
        self.assertIn("ORDER BY version DESC, id DESC", statement)
        self.assertEqual(parameters, (self.conversation_id, 2))

    def test_submit_for_approval_locks_and_updates_with_cas(self) -> None:
        definition = self.definition()
        payload = plan_definition_to_dict(definition)
        updated = list(
            self.row(
                "awaiting_approval",
                definition=payload,
            )
        )
        updated[3] = 2
        connection = _ScriptedConnection(
            _Result(self.row()),
            _Result(tuple(updated)),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        run = store.submit_for_approval(self.run_id, 1, definition)

        self.assertEqual(run.status, "awaiting_approval")
        self.assertEqual(run.row_revision, 2)
        self.assertEqual(run.definition, definition)
        self.assertIn("FOR UPDATE", connection.calls[0][0])
        update_statement, update_parameters = connection.calls[1]
        self.assertIn("row_revision = row_revision + 1", update_statement)
        self.assertIn("WHERE id = %s AND row_revision = %s", update_statement)
        self.assertEqual(update_parameters[-2:], (self.run_id, 1))
        self.assertEqual(update_parameters[1].obj, payload)
        self.assertEqual(database.commits, 1)

    def test_submit_for_approval_rejects_missing_stale_and_wrong_status(self) -> None:
        definition = self.definition()
        stale = list(self.row())
        stale[3] = 2
        awaiting = self.row(
            "awaiting_approval",
            definition=plan_definition_to_dict(definition),
        )
        cases = (
            (_Result(None), PlanningRunNotFoundError),
            (_Result(tuple(stale)), PlanningConflictError),
            (_Result(awaiting), InvalidPlanningTransition),
        )

        for response, error in cases:
            with self.subTest(error=error):
                connection = _ScriptedConnection(response)
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(error):
                    store.submit_for_approval(self.run_id, 1, definition)
                self.assertEqual(len(connection.calls), 1)
                self.assertEqual(database.rollbacks, 1)

    def test_submit_cas_update_checks_revision_again(self) -> None:
        definition = self.definition()
        connection = _ScriptedConnection(
            _Result(self.row()),
            _Result(None),
            _Result((2,)),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with self.assertRaises(PlanningConflictError) as caught:
            store.submit_for_approval(self.run_id, 1, definition)

        self.assertEqual(caught.exception.expected_revision, 1)
        self.assertEqual(caught.exception.actual_revision, 2)
        self.assertEqual(database.rollbacks, 1)

    def test_approve_materializes_owned_events_and_enters_running(self) -> None:
        definition = self.definition()
        definition_payload = plan_definition_to_dict(definition)
        awaiting = self.row(
            "awaiting_approval",
            definition=definition_payload,
        )

        def updated_result(
            statement: str,
            parameters: tuple[object, ...],
        ) -> _Result:
            self.assertIn("task_events = %s", statement)
            events = parameters[1].obj
            updated = list(
                self.row(
                    "running",
                    definition=definition_payload,
                    events=events,
                )
            )
            updated[3] = 2
            return _Result(tuple(updated))

        connection = _ScriptedConnection(
            _Result(awaiting),
            updated_result,
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        run = store.approve(self.run_id, 1)

        self.assertEqual(run.status, "running")
        self.assertEqual(run.row_revision, 2)
        self.assertEqual(len(run.task_events), len(definition.steps))
        self.assertEqual(
            {event.node.owner_id for event in run.task_events},
            {str(self.run_id)},
        )
        restored = PlanExecution.from_events(
            run.definition,
            run.task_events,
            owner_id=str(run.id),
        )
        self.assertEqual(len(restored.task_store.list()), len(definition.steps))
        self.assertEqual(database.commits, 1)

    def test_approve_wraps_materialize_failure_without_writing(self) -> None:
        awaiting = self.row(
            "awaiting_approval",
            definition=plan_definition_to_dict(self.definition()),
        )
        connection = _ScriptedConnection(_Result(awaiting))
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with patch.object(
            PlanExecution,
            "materialize",
            side_effect=ValueError("private definition details"),
        ):
            with self.assertRaises(CorruptPlanningRunError) as caught:
                store.approve(self.run_id, 1)

        self.assertEqual(caught.exception.reason, "plan materialization invalid")
        self.assertNotIn("private definition details", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(len(connection.calls), 1)
        self.assertEqual(database.rollbacks, 1)

    def test_approve_and_revise_reject_drafting(self) -> None:
        for operation in (
            lambda store: store.approve(self.run_id, 1),
            lambda store: store.revise(self.run_id, 1, "Change it"),
        ):
            with self.subTest(operation=operation):
                connection = _ScriptedConnection(_Result(self.row()))
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(InvalidPlanningTransition):
                    operation(store)
                self.assertEqual(len(connection.calls), 1)
                self.assertEqual(database.rollbacks, 1)

    def test_revise_supersedes_old_run_and_inserts_version_one_higher(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        awaiting = self.row(
            "awaiting_approval",
            definition=definition_payload,
        )
        superseded = list(
            self.row(
                "superseded",
                definition=definition_payload,
            )
        )
        superseded[3] = 2
        new_run_id = uuid4()

        def inserted_result(
            statement: str,
            parameters: tuple[object, ...],
        ) -> _Result:
            self.assertIn("INSERT INTO planning_runs", statement)
            return _Result(
                (
                    parameters[0],
                    parameters[1],
                    parameters[2],
                    1,
                    "drafting",
                    parameters[3],
                    parameters[4],
                    parameters[5],
                    None,
                    None,
                    [],
                    None,
                    parameters[6].obj,
                    parameters[7].obj,
                    self.now,
                    self.now,
                )
            )

        connection = _ScriptedConnection(
            _Result(awaiting),
            _Result(tuple(superseded)),
            inserted_result,
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with patch(
            "agents.chat_backend.planning_store.uuid4",
            return_value=new_run_id,
        ):
            revised = store.revise(self.run_id, 1, "  Use smaller steps  ")

        self.assertEqual(revised.id, new_run_id)
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.row_revision, 1)
        self.assertEqual(revised.status, "drafting")
        self.assertEqual(revised.supersedes_version, 1)
        self.assertEqual(revised.revision_note, "Use smaller steps")
        self.assertEqual(revised.model_snapshot, awaiting[12])
        self.assertEqual(revised.workspace_snapshot, awaiting[13])
        self.assertEqual(len(connection.calls), 3)
        self.assertNotIn("UPDATE conversations", connection.calls[2][0])
        self.assertEqual(database.commits, 1)

    def test_revise_insert_failure_rolls_back_the_superseded_update(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        awaiting = self.row(
            "awaiting_approval",
            definition=definition_payload,
        )
        superseded = list(
            self.row(
                "superseded",
                definition=definition_payload,
            )
        )
        superseded[3] = 2
        connection = _ScriptedConnection(
            _Result(awaiting),
            _Result(tuple(superseded)),
            RuntimeError("revision insert failed"),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "revision insert failed"):
            store.revise(self.run_id, 1, "Use smaller steps")

        self.assertEqual(database.rollbacks, 1)
        self.assertEqual(database.commits, 0)

    def test_revise_closes_unfinished_tasks_from_execution_states(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        cases = (
            self.row(
                "running",
                definition=definition_payload,
                events=self.event_payloads(state="boundary"),
            ),
            self.row(
                "awaiting_tool_approval",
                definition=definition_payload,
                events=self.event_payloads(state="awaiting"),
                run_state=self.snapshot_payload(),
            ),
            self.row(
                "paused",
                definition=definition_payload,
                events=self.event_payloads(state="awaiting"),
                last_error="planning model step failed",
            ),
        )

        for current in cases:
            with self.subTest(status=current[4]):
                def superseded_result(
                    _statement: str,
                    parameters: tuple[object, ...],
                    current: tuple[object, ...] = current,
                ) -> _Result:
                    updated = list(current)
                    updated[3] = 2
                    updated[4] = "superseded"
                    updated[8] = None
                    updated[10] = parameters[1].obj
                    updated[11] = None
                    return _Result(tuple(updated))

                def inserted_result(
                    _statement: str,
                    parameters: tuple[object, ...],
                ) -> _Result:
                    return _Result(
                        (
                            parameters[0],
                            parameters[1],
                            parameters[2],
                            1,
                            "drafting",
                            parameters[3],
                            parameters[4],
                            parameters[5],
                            None,
                            None,
                            [],
                            None,
                            parameters[6].obj,
                            parameters[7].obj,
                            self.now,
                            self.now,
                        )
                    )

                connection = _ScriptedConnection(
                    _Result(current),
                    superseded_result,
                    inserted_result,
                )
                store = PlanStore(  # type: ignore[arg-type]
                    _ScriptedDatabase(connection)
                )

                revised = store.revise(self.run_id, 1, "Change the approach")

                self.assertEqual(revised.status, "drafting")
                old_events = connection.calls[1][1][1].obj
                old_execution = PlanExecution.from_events(
                    self.definition(),
                    tuple(
                        task_event_from_dict(event)
                        for event in old_events
                    ),
                    owner_id=str(self.run_id),
                )
                old_nodes = {
                    node.task_id: node
                    for node in old_execution.task_store.list()
                }
                self.assertTrue(
                    all(
                        node.status in (
                            TaskStatus.COMPLETED,
                            TaskStatus.CANCELLED,
                        )
                        for node in old_nodes.values()
                    )
                )
                if current[4] == "running":
                    self.assertEqual(old_nodes["work"].result, {"work": "done"})

    def test_cancel_accepts_every_active_status_and_restores_default_mode(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        events = self.event_payloads()
        rows = (
            self.row("drafting"),
            self.row("awaiting_approval", definition=definition_payload),
            self.row(
                "running",
                definition=definition_payload,
                events=events,
            ),
            self.row(
                "awaiting_tool_approval",
                definition=definition_payload,
                events=self.event_payloads(state="awaiting"),
                run_state=self.snapshot_payload(),
            ),
            self.row(
                "paused",
                definition=definition_payload,
                events=self.event_payloads(state="awaiting"),
                last_error="planning model step failed",
            ),
        )

        for current in rows:
            with self.subTest(status=current[4]):
                def updated_result(
                    statement: str,
                    parameters: tuple[object, ...],
                    current: tuple[object, ...] = current,
                ) -> _Result:
                    updated = list(current)
                    updated[3] = current[3] + 1
                    updated[4] = "cancelled"
                    updated[7] = parameters[1]
                    updated[8] = None
                    updated[10] = parameters[2].obj
                    updated[11] = None
                    return _Result(tuple(updated))

                connection = _ScriptedConnection(
                    _Result(current),
                    updated_result,
                    _Result(),
                )
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]

                cancelled = store.cancel(self.run_id, 1, "  User stopped  ")

                self.assertEqual(cancelled.status, "cancelled")
                self.assertEqual(cancelled.revision_note, "User stopped")
                self.assertIsNone(cancelled.run_state_snapshot)
                self.assertIsNone(cancelled.last_error)
                self.assertIn("SET mode = 'default'", connection.calls[-1][0])
                if cancelled.task_events:
                    execution = PlanExecution.from_events(
                        cancelled.definition,
                        cancelled.task_events,
                        owner_id=str(cancelled.id),
                    )
                    self.assertTrue(
                        all(
                            node.status is TaskStatus.CANCELLED
                            for node in execution.task_store.list()
                        )
                    )
                self.assertEqual(database.commits, 1)

    def test_cancel_reloads_nodes_after_dependency_propagation(self) -> None:
        definition = self.definition()
        execution = PlanExecution.materialize(
            definition,
            owner_id=str(self.run_id),
        )
        work = execution.task_store.get("work")
        execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.RUNNING,
        )
        work = execution.task_store.get("work")
        execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.COMPLETED,
            result={"ok": True},
        )
        events = [
            task_event_to_dict(event)
            for event in execution.task_store.events()
        ]
        current = self.row(
            "running",
            definition=plan_definition_to_dict(definition),
            events=events,
        )

        def updated_result(
            _statement: str,
            parameters: tuple[object, ...],
        ) -> _Result:
            updated = list(current)
            updated[3] = 2
            updated[4] = "cancelled"
            updated[7] = parameters[1]
            updated[10] = parameters[2].obj
            return _Result(tuple(updated))

        connection = _ScriptedConnection(
            _Result(current),
            updated_result,
            _Result(),
        )
        store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(connection)
        )

        cancelled = store.cancel(self.run_id, 1, "Stop remaining work")

        restored = PlanExecution.from_events(
            cancelled.definition,
            cancelled.task_events,
            owner_id=str(cancelled.id),
        )
        statuses = {
            node.task_id: node.status for node in restored.task_store.list()
        }
        self.assertEqual(statuses["work"], TaskStatus.COMPLETED)
        self.assertEqual(statuses["verify"], TaskStatus.CANCELLED)

    def test_cancel_rejects_terminal_runs_without_changing_mode(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        rows = (
            self.row(
                "completed",
                definition=definition_payload,
                events=self.event_payloads(state="completed"),
            ),
            self.row(
                "failed",
                definition=definition_payload,
                events=self.event_payloads(state="isolated"),
                last_error="failed",
            ),
            self.row("cancelled"),
            self.row("superseded", definition=definition_payload),
        )

        for current in rows:
            with self.subTest(status=current[4]):
                connection = _ScriptedConnection(_Result(current))
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(InvalidPlanningTransition):
                    store.cancel(self.run_id, 1, "Too late")
                self.assertEqual(len(connection.calls), 1)
                self.assertEqual(database.rollbacks, 1)

    def test_cancel_mode_failure_rolls_back_planning_update(self) -> None:
        current = self.row("drafting")
        cancelled = list(self.row("cancelled"))
        cancelled[3] = 2
        cancelled[7] = "Stop"
        connection = _ScriptedConnection(
            _Result(current),
            _Result(tuple(cancelled)),
            RuntimeError("mode reset failed"),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "mode reset failed"):
            store.cancel(self.run_id, 1, "Stop")

        self.assertEqual(database.rollbacks, 1)
        self.assertEqual(database.commits, 0)

    def test_save_execution_persists_a_running_step_boundary(self) -> None:
        execution = self.execution("boundary")
        payloads = [
            task_event_to_dict(event) for event in execution.task_store.events()
        ]
        current = self.row(
            "running",
            definition=plan_definition_to_dict(self.definition()),
            events=payloads[:2],
        )
        connection = _ScriptedConnection(
            _Result(current),
            self.saved_execution_result(current),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        saved = store.save_execution(
            self.run_id,
            1,
            execution=execution,
            status="running",
        )

        self.assertEqual(saved.status, "running")
        self.assertEqual(saved.row_revision, 2)
        self.assertEqual(saved.task_events, execution.task_store.events())
        self.assertIsNone(saved.run_state_snapshot)
        self.assertEqual(len(connection.calls), 2)
        self.assertEqual(database.commits, 1)

    def test_save_execution_persists_running_current_task_and_pause(self) -> None:
        execution = self.execution("awaiting")
        payloads = [
            task_event_to_dict(event) for event in execution.task_store.events()
        ]
        current = self.row(
            "running",
            definition=plan_definition_to_dict(self.definition()),
            events=payloads[:2],
        )
        connection = _ScriptedConnection(
            _Result(current),
            self.saved_execution_result(current),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        saved = store.save_execution(
            self.run_id,
            1,
            execution=execution,
            status="paused",
            last_error="  planning model step failed  ",
        )

        self.assertEqual(saved.status, "paused")
        self.assertEqual(saved.last_error, "planning model step failed")
        self.assertEqual(
            saved.task_events,
            execution.task_store.events(),
        )
        self.assertIsNone(saved.run_state_snapshot)
        self.assertEqual(len(connection.calls), 2)
        self.assertEqual(database.commits, 1)

    def test_save_execution_persists_and_clears_tool_approval_snapshot(self) -> None:
        awaiting_execution = self.execution("awaiting")
        awaiting_payloads = [
            task_event_to_dict(event)
            for event in awaiting_execution.task_store.events()
        ]
        running = self.row(
            "running",
            definition=plan_definition_to_dict(self.definition()),
            events=awaiting_payloads[:2],
        )
        awaiting_connection = _ScriptedConnection(
            _Result(running),
            self.saved_execution_result(running),
        )
        awaiting_store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(awaiting_connection)
        )

        paused = awaiting_store.save_execution(
            self.run_id,
            1,
            execution=awaiting_execution,
            status="awaiting_tool_approval",
            run_state=self.snapshot(),
        )

        self.assertEqual(paused.status, "awaiting_tool_approval")
        self.assertEqual(paused.run_state_snapshot.task_id, "work")
        self.assertEqual(paused.run_state_snapshot.task_revision, 2)
        self.assertEqual(awaiting_connection.calls[1][1][2].obj["schema_version"], 4)

        resumed_execution = self.execution("boundary")
        resumed_payloads = [
            task_event_to_dict(event)
            for event in resumed_execution.task_store.events()
        ]
        awaiting = self.row(
            "awaiting_tool_approval",
            definition=plan_definition_to_dict(self.definition()),
            events=resumed_payloads[:3],
            run_state=self.snapshot_payload(),
        )
        resume_connection = _ScriptedConnection(
            _Result(awaiting),
            self.saved_execution_result(awaiting),
        )
        resume_store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(resume_connection)
        )

        resumed = resume_store.save_execution(
            self.run_id,
            1,
            execution=resumed_execution,
            status="running",
        )

        self.assertEqual(resumed.status, "running")
        self.assertIsNone(resumed.run_state_snapshot)

    def test_save_execution_claims_paused_only_as_running(self) -> None:
        execution = self.execution("awaiting")
        payloads = [
            task_event_to_dict(event) for event in execution.task_store.events()
        ]
        paused = self.row(
            "paused",
            definition=plan_definition_to_dict(self.definition()),
            events=payloads,
            last_error="planning model step failed",
        )
        connection = _ScriptedConnection(
            _Result(paused),
            self.saved_execution_result(paused),
        )
        store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(connection)
        )

        claimed = store.save_execution(
            self.run_id,
            1,
            execution=execution,
            status="running",
        )

        self.assertEqual(claimed.status, "running")
        self.assertIsNone(claimed.last_error)

        invalid_connection = _ScriptedConnection(_Result(paused))
        invalid_store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(invalid_connection)
        )
        with self.assertRaises(InvalidPlanningTransition):
            invalid_store.save_execution(
                self.run_id,
                1,
                execution=execution,
                status="paused",
                last_error="still paused",
            )

    def test_save_execution_terminal_states_reset_conversation_mode(self) -> None:
        cases = (
            ("completed", self.execution("completed"), None),
            ("failed", self.execution("isolated"), "  model failed  "),
        )

        for status, execution, last_error in cases:
            with self.subTest(status=status):
                payloads = [
                    task_event_to_dict(event)
                    for event in execution.task_store.events()
                ]
                current = self.row(
                    "running",
                    definition=plan_definition_to_dict(self.definition()),
                    events=payloads[:2],
                )
                connection = _ScriptedConnection(
                    _Result(current),
                    self.saved_execution_result(current),
                    _Result(),
                )
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]

                saved = store.save_execution(
                    self.run_id,
                    1,
                    execution=execution,
                    status=status,  # type: ignore[arg-type]
                    last_error=last_error,
                )

                self.assertEqual(saved.status, status)
                self.assertEqual(
                    saved.last_error,
                    "model failed" if status == "failed" else None,
                )
                self.assertIn("SET mode = 'default'", connection.calls[-1][0])
                self.assertEqual(database.commits, 1)

    def test_save_execution_mode_failure_rolls_back_terminal_update(self) -> None:
        execution = self.execution("completed")
        payloads = [
            task_event_to_dict(event) for event in execution.task_store.events()
        ]
        current = self.row(
            "running",
            definition=plan_definition_to_dict(self.definition()),
            events=payloads[:2],
        )
        connection = _ScriptedConnection(
            _Result(current),
            self.saved_execution_result(current),
            RuntimeError("mode reset failed"),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "mode reset failed"):
            store.save_execution(
                self.run_id,
                1,
                execution=execution,
                status="completed",
            )

        self.assertEqual(database.rollbacks, 1)
        self.assertEqual(database.commits, 0)

    def test_save_execution_rejects_invalid_source_and_task_shapes(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        awaiting_approval = self.row(
            "awaiting_approval",
            definition=definition_payload,
        )
        source_connection = _ScriptedConnection(_Result(awaiting_approval))
        source_database = _ScriptedDatabase(source_connection)
        source_store = PlanStore(source_database)  # type: ignore[arg-type]

        with self.assertRaises(InvalidPlanningTransition):
            source_store.save_execution(
                self.run_id,
                1,
                execution=self.execution(),
                status="running",
            )
        self.assertEqual(source_database.rollbacks, 1)

        cases = (
            ("paused", self.execution("boundary"), None, "model failed"),
            (
                "awaiting_tool_approval",
                self.execution("awaiting"),
                replace(self.snapshot(), task_revision=99),
                None,
            ),
            ("completed", self.execution("boundary"), None, None),
            ("failed", self.execution("boundary"), None, "failed"),
        )
        for status, execution, run_state, last_error in cases:
            with self.subTest(status=status):
                events = execution.task_store.events()
                current = self.row(
                    "running",
                    definition=definition_payload,
                    events=[task_event_to_dict(event) for event in events[:2]],
                )
                connection = _ScriptedConnection(_Result(current))
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    store.save_execution(
                        self.run_id,
                        1,
                        execution=execution,
                        status=status,  # type: ignore[arg-type]
                        run_state=run_state,
                        last_error=last_error,
                    )
                self.assertEqual(len(connection.calls), 1)
                self.assertEqual(database.rollbacks, 1)

    def test_save_execution_rejects_changed_or_truncated_event_history(self) -> None:
        definition_payload = plan_definition_to_dict(self.definition())
        boundary = self.execution("boundary")
        boundary_events = boundary.task_store.events()
        boundary_row = self.row(
            "running",
            definition=definition_payload,
            events=[task_event_to_dict(event) for event in boundary_events],
        )
        truncated = PlanExecution.from_events(
            self.definition(),
            boundary_events[:2],
            owner_id=str(self.run_id),
        )
        cases = (
            (boundary_row, truncated),
            (
                self.row(
                    "running",
                    definition=definition_payload,
                    events=self.event_payloads(),
                ),
                self.execution(),
            ),
        )

        for current, execution in cases:
            with self.subTest(saved_event_count=len(current[10])):
                connection = _ScriptedConnection(_Result(current))
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(CorruptPlanningRunError) as caught:
                    store.save_execution(
                        self.run_id,
                        1,
                        execution=execution,
                        status="running",
                    )
                self.assertEqual(caught.exception.reason, "task event history changed")
                self.assertEqual(database.rollbacks, 1)

    def test_save_execution_rejects_another_definition_or_owner(self) -> None:
        current_execution = self.execution()
        current = self.row(
            "running",
            definition=plan_definition_to_dict(self.definition()),
            events=[
                task_event_to_dict(event)
                for event in current_execution.task_store.events()
            ],
        )
        changed_definition = PlanDefinition(
            objective="A different plan",
            steps=self.definition().steps,
        )
        cases = (
            (
                PlanExecution.materialize(
                    changed_definition,
                    owner_id=str(self.run_id),
                ),
                "execution definition changed",
            ),
            (self.execution(owner_id=uuid4()), "plan execution invalid"),
        )

        for execution, reason in cases:
            with self.subTest(reason=reason):
                connection = _ScriptedConnection(_Result(current))
                database = _ScriptedDatabase(connection)
                store = PlanStore(database)  # type: ignore[arg-type]
                with self.assertRaises(CorruptPlanningRunError) as caught:
                    store.save_execution(
                        self.run_id,
                        1,
                        execution=execution,
                        status="running",
                    )
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(database.rollbacks, 1)

    def test_execution_state_shapes_are_strictly_decoded(self) -> None:
        definition = plan_definition_to_dict(self.definition())
        rows = (
            self.row(
                "running",
                definition=definition,
                events=self.event_payloads(state="failed"),
            ),
            self.row(
                "paused",
                definition=definition,
                events=self.event_payloads(),
                last_error="planning model step failed",
            ),
            self.row(
                "awaiting_tool_approval",
                definition=definition,
                events=self.event_payloads(),
                run_state=self.snapshot_payload(),
            ),
            self.row(
                "completed",
                definition=definition,
                events=self.event_payloads(state="boundary"),
            ),
            self.row(
                "failed",
                definition=definition,
                events=self.event_payloads(state="boundary"),
                last_error="failed",
            ),
            self.row(
                "superseded",
                definition=definition,
                events=self.event_payloads(state="boundary"),
            ),
        )

        for row in rows:
            with self.subTest(status=row[4]):
                self.assert_corrupt(row, "plan execution invalid")

    def test_list_recoverable_running_uses_stable_order_and_strict_decode(self) -> None:
        definition = plan_definition_to_dict(self.definition())
        first = self.row(
            "running",
            definition=definition,
            events=self.event_payloads(),
        )
        second_id = uuid4()
        second = list(
            self.row(
                "running",
                definition=definition,
                events=self.event_payloads(second_id),
            )
        )
        second[0] = second_id
        connection = _ScriptedConnection(
            _Result(many=(first, tuple(second))),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        runs = store.list_recoverable_running(limit=2)

        self.assertEqual(tuple(run.id for run in runs), (self.run_id, second_id))
        statement, parameters = connection.calls[0]
        self.assertIn("WHERE status = 'running'", statement)
        self.assertIn("ORDER BY updated_at ASC, id ASC", statement)
        self.assertEqual(parameters, (2,))
        self.assertEqual(database.connection_calls, 1)

        corrupt = self.row(
            "completed",
            definition=definition,
            events=self.event_payloads(),
        )
        corrupt_store = PlanStore(  # type: ignore[arg-type]
            _ScriptedDatabase(
                _ScriptedConnection(_Result(many=(first, corrupt)))
            )
        )
        with self.assertRaises(CorruptPlanningRunError):
            corrupt_store.list_recoverable_running()

    def test_quarantine_running_marks_failed_and_closes_conversation(self) -> None:
        connection = _ScriptedConnection(
            _Result(one=(self.run_id, self.conversation_id, "running")),
            _Result(),
            _Result(),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        store.quarantine_running(self.run_id, "planning recovery failed")

        self.assertEqual(database.transaction_calls, 1)
        self.assertEqual(database.commits, 1)
        self.assertIn("FOR UPDATE", connection.calls[0][0])
        self.assertIn("status = 'failed'", connection.calls[1][0])
        self.assertIn("row_revision = row_revision + 1", connection.calls[1][0])
        self.assertEqual(
            connection.calls[1][1],
            ("planning recovery failed", self.run_id),
        )
        self.assertIn("mode = 'default'", connection.calls[2][0])
        self.assertEqual(connection.calls[2][1], (self.conversation_id,))

    def test_quarantine_running_does_not_overwrite_newer_status(self) -> None:
        connection = _ScriptedConnection(
            _Result(one=(self.run_id, self.conversation_id, "paused")),
        )
        database = _ScriptedDatabase(connection)
        store = PlanStore(database)  # type: ignore[arg-type]

        with self.assertRaises(InvalidPlanningTransition):
            store.quarantine_running(self.run_id, "planning recovery failed")

        self.assertEqual(database.rollbacks, 1)
        self.assertEqual(len(connection.calls), 1)


if __name__ == "__main__":
    unittest.main()
