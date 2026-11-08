from __future__ import annotations

import sys
import unittest
from dataclasses import asdict, fields
from pathlib import Path
import json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents.run_state as run_state_module  # noqa: E402
from agents import Agent, AgentMemory, ModelResponse, RunItem, RunState, ToolApprovalRequest, ToolCall  # noqa: E402
from agents.result import RunResult  # noqa: E402
from agents.model_retry import (  # noqa: E402
    ModelAttemptRecord,
    ModelExecution,
    ModelExecutionError,
)
from agents.models import ModelErrorKind, ModelResponseError  # noqa: E402
from agents.run_state import ApprovalSnapshot, RunStateSnapshot, build_run_result  # noqa: E402


class RunStateTestCase(unittest.TestCase):
    def test_snapshot_restores_private_model_state_for_tool_resume(self) -> None:
        class StatefulModel:
            def __init__(self, state=None) -> None:
                self.state = state

            def get_response(self, messages, tool_specs):
                _ = messages, tool_specs
                return ModelResponse("response", [], output_text="done")

            def export_state(self):
                return self.state

            def restore_state(self, state) -> None:
                self.state = state

        first_model = StatefulModel(
            {"tool_exchange": [{"reasoning_content": "private reasoning"}]}
        )
        first_agent = Agent(memory=AgentMemory(), model=first_model)
        payload = RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=0,
            last_agent=first_agent,
        ).to_state()

        restored_model = StatefulModel()
        restored_agent = Agent(memory=AgentMemory(), model=restored_model)
        RunState.from_snapshot(payload, agent=restored_agent)

        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["model_state"], first_model.state)
        self.assertEqual(restored_model.state, first_model.state)

    def test_run_state_accepts_unbound_and_complete_task_links(self) -> None:
        unbound = RunState()
        bound = RunState(task_id="task-1", task_revision=2)

        self.assertIsNone(unbound.task_id)
        self.assertIsNone(unbound.task_revision)
        self.assertEqual(bound.task_id, "task-1")
        self.assertEqual(bound.task_revision, 2)

    def test_run_state_rejects_partial_task_links(self) -> None:
        for values in (
            {"task_id": "task-1"},
            {"task_revision": 1},
        ):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "must be provided together"):
                    RunState(**values)

    def test_run_state_rejects_invalid_task_link_values(self) -> None:
        invalid_links = (
            ({"task_id": 1, "task_revision": 1}, TypeError),
            ({"task_id": "   ", "task_revision": 1}, ValueError),
            ({"task_id": "task-1", "task_revision": True}, TypeError),
            ({"task_id": "task-1", "task_revision": 1.0}, TypeError),
            ({"task_id": "task-1", "task_revision": 0}, ValueError),
            ({"task_id": "task-1", "task_revision": -1}, ValueError),
        )
        for values, error_type in invalid_links:
            with self.subTest(values=values):
                with self.assertRaises(error_type):
                    RunState(**values)

    def test_approval_snapshot_is_pure_data_and_keeps_tool_arguments(self) -> None:
        approval = ApprovalSnapshot(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "tmp.txt"},
            status="pending",
            reason="User approval is required before running this tool.",
        )

        payload = asdict(approval)

        self.assertEqual(
            payload,
            {
                "tool_name": "delete_file",
                "call_id": "call_1",
                "arguments": {"path": "tmp.txt"},
                "status": "pending",
                "reason": "User approval is required before running this tool.",
                "rejection_message": None,
            },
        )
        json.dumps(payload)

    def test_run_state_snapshot_is_pure_data_without_runtime_objects(self) -> None:
        approval = ApprovalSnapshot(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "tmp.txt"},
            status="pending",
        )
        snapshot = RunStateSnapshot(
            input="delete tmp.txt",
            last_agent_name="Planner",
            last_response_id="resp_1",
            current_turn=1,
            steps_taken=0,
            max_turns=4,
            max_steps=5,
            tool_approvals=(approval,),
            model_responses=({"response_id": "resp_1", "output": []},),
            new_items=(
                {
                    "item_type": "tool_approval_required",
                    "step_number": 1,
                    "payload": {"call_id": "call_1"},
                },
            ),
        )

        snapshot_fields = {field.name for field in fields(RunStateSnapshot)}
        payload = asdict(snapshot)

        self.assertNotIn("last_agent", snapshot_fields)
        self.assertNotIn("context_wrapper", snapshot_fields)
        self.assertEqual(payload["tool_approvals"][0]["arguments"], {"path": "tmp.txt"})
        json.dumps(payload)

    def test_run_state_snapshot_to_dict_adds_schema_version_and_json_payload(self) -> None:
        approval = ApprovalSnapshot(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "tmp.txt"},
            status="pending",
        )
        snapshot = RunStateSnapshot(
            input="delete tmp.txt",
            last_agent_name="Planner",
            last_response_id="resp_1",
            current_turn=1,
            steps_taken=0,
            max_turns=4,
            max_steps=5,
            tool_approvals=(approval,),
            model_responses=({"response_id": "resp_1", "output": []},),
            new_items=(
                {
                    "item_type": "tool_approval_required",
                    "step_number": 1,
                    "payload": {"call_id": "call_1"},
                },
            ),
        )
        exporter = getattr(run_state_module, "run_state_snapshot_to_dict", None)

        self.assertTrue(callable(exporter))
        payload = exporter(snapshot)

        self.assertEqual(
            payload["schema_version"],
            run_state_module.RUN_STATE_SNAPSHOT_SCHEMA_VERSION,
        )
        self.assertNotIn("last_agent", payload)
        self.assertNotIn("context_wrapper", payload)
        self.assertEqual(payload["tool_approvals"][0]["arguments"], {"path": "tmp.txt"})
        json.dumps(payload)

    def test_run_state_snapshot_from_dict_restores_versioned_payload(self) -> None:
        snapshot = RunStateSnapshot(
            input="delete tmp.txt",
            last_agent_name="Planner",
            last_response_id="resp_1",
            current_turn=2,
            steps_taken=1,
            max_turns=4,
            max_steps=5,
            tool_approvals=(
                ApprovalSnapshot(
                    tool_name="delete_file",
                    call_id="call_1",
                    arguments={"path": "tmp.txt"},
                    status="pending",
                    reason="Needs approval.",
                ),
            ),
            model_responses=({"response_id": "resp_1", "output": []},),
            new_items=(
                {
                    "item_type": "tool_approval_required",
                    "step_number": 1,
                    "payload": {"call_id": "call_1"},
                    "metadata": {},
                },
            ),
        )
        payload = json.loads(json.dumps(run_state_module.run_state_snapshot_to_dict(snapshot)))
        importer = getattr(run_state_module, "run_state_snapshot_from_dict", None)

        self.assertTrue(callable(importer))
        restored = importer(payload)

        self.assertEqual(restored.input, "delete tmp.txt")
        self.assertEqual(restored.current_turn, 2)
        self.assertEqual(restored.steps_taken, 1)
        self.assertEqual(restored.tool_approvals[0].call_id, "call_1")
        self.assertEqual(restored.tool_approvals[0].status, "pending")

    def test_run_state_snapshot_from_dict_accepts_legacy_payload_without_schema_version(self) -> None:
        snapshot = RunStateSnapshot(
            input="delete tmp.txt",
            last_agent_name="Planner",
            last_response_id="resp_1",
            current_turn=1,
            steps_taken=0,
            max_turns=4,
            max_steps=5,
            tool_approvals=(),
            model_responses=(),
            new_items=(),
        )
        legacy_payload = json.loads(json.dumps(asdict(snapshot)))
        legacy_payload.pop("model_attempts")
        legacy_payload.pop("last_model_terminal_reason")
        legacy_payload.pop("task_id")
        legacy_payload.pop("task_revision")

        restored = run_state_module.run_state_snapshot_from_dict(legacy_payload)

        self.assertEqual(restored.input, "delete tmp.txt")
        self.assertEqual(restored.current_turn, 1)
        self.assertEqual(restored.tool_approvals, ())
        self.assertEqual(restored.model_attempts, ())
        self.assertIsNone(restored.last_model_terminal_reason)
        self.assertIsNone(restored.task_id)
        self.assertIsNone(restored.task_revision)

    def test_run_state_snapshot_from_dict_accepts_v1_with_empty_model_defaults(
        self,
    ) -> None:
        payload = {
            "schema_version": 1,
            "input": "legacy",
            "last_agent_name": None,
            "last_response_id": None,
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [],
            "model_responses": [],
            "new_items": [],
            "model_attempts": [{"outcome": "must be ignored"}],
            "last_model_terminal_reason": "must be ignored",
            "task_id": "must-be-ignored",
            "task_revision": 99,
        }

        restored = run_state_module.run_state_snapshot_from_dict(payload)

        self.assertEqual(restored.model_attempts, ())
        self.assertIsNone(restored.last_model_terminal_reason)
        self.assertIsNone(restored.task_id)
        self.assertIsNone(restored.task_revision)

    def test_run_state_from_snapshot_rejects_unsupported_schema_version(self) -> None:
        base_payload = {
            "input": "delete tmp.txt",
            "last_agent_name": "Planner",
            "last_response_id": "resp_1",
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": 4,
            "max_steps": 5,
            "tool_approvals": [],
            "model_responses": [],
            "new_items": [],
        }

        for schema_version in (0, 5, "4", True):
            with self.subTest(schema_version=schema_version):
                with self.assertRaisesRegex(
                    ValueError,
                    "Unsupported run state snapshot schema version",
                ):
                    RunState.from_snapshot(
                        base_payload | {"schema_version": schema_version}
                    )

    def test_snapshot_v4_roundtrip_restores_model_attempts_and_task_link(self) -> None:
        attempts = (
            ModelAttemptRecord(
                request_id="req_1",
                attempt_id="attempt_1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                outcome="failed",
                duration_seconds=0.5,
                error_kind=ModelErrorKind.RATE_LIMIT,
                status_code=429,
                reason="model_fallback",
            ),
            ModelAttemptRecord(
                request_id="req_1",
                attempt_id="attempt_2",
                attempt_number=2,
                model_name="model-b",
                mode="stream",
                outcome="succeeded",
                duration_seconds=0.75,
                reason="model_fallback",
                response_id="resp_2",
                usage={"input_tokens": 10, "output_tokens": 4},
                ttft_seconds=0.2,
            ),
        )
        run_state = RunState(
            model_attempts=list(attempts),
            last_model_terminal_reason="completed",
            task_id="task-1",
            task_revision=3,
        )
        result = build_run_result(run_state)
        run_state.model_attempts.clear()

        payload = json.loads(json.dumps(result.to_state()))
        restored = RunState.from_snapshot(payload)

        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["model_attempts"][0]["error_kind"], "rate_limit")
        self.assertEqual(
            [attempt["model_name"] for attempt in payload["model_attempts"]],
            ["model-a", "model-b"],
        )
        self.assertEqual(payload["last_model_terminal_reason"], "completed")
        self.assertEqual(result.model_attempts, attempts)
        self.assertEqual(restored.model_attempts, list(attempts))
        self.assertEqual(restored.last_model_terminal_reason, "completed")
        self.assertEqual(restored.task_id, "task-1")
        self.assertEqual(restored.task_revision, 3)

    def test_snapshot_v2_restores_model_attempts_but_ignores_task_link(self) -> None:
        payload = {
            "schema_version": 2,
            "input": "legacy model retry",
            "last_agent_name": None,
            "last_response_id": None,
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [],
            "model_responses": [],
            "new_items": [],
            "model_attempts": [
                {
                    "request_id": "req-1",
                    "attempt_id": "attempt-1",
                    "attempt_number": 1,
                    "model_name": "model-a",
                    "mode": "stream",
                    "outcome": "succeeded",
                    "duration_seconds": 0.5,
                }
            ],
            "last_model_terminal_reason": "completed",
            "task_id": "must-be-ignored",
            "task_revision": False,
        }

        restored = RunState.from_snapshot(payload)

        self.assertEqual(len(restored.model_attempts), 1)
        self.assertEqual(restored.last_model_terminal_reason, "completed")
        self.assertIsNone(restored.task_id)
        self.assertIsNone(restored.task_revision)

    def test_snapshot_v3_rejects_invalid_task_links(self) -> None:
        base_payload = {
            "schema_version": 3,
            "input": "invalid task link",
            "last_agent_name": None,
            "last_response_id": None,
            "current_turn": 0,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [],
            "model_responses": [],
            "new_items": [],
        }
        invalid_links = (
            ({"task_id": "task-1"}, ValueError),
            ({"task_revision": 1}, ValueError),
            ({"task_id": 1, "task_revision": 1}, TypeError),
            ({"task_id": "   ", "task_revision": 1}, ValueError),
            ({"task_id": "task-1", "task_revision": True}, TypeError),
            ({"task_id": "task-1", "task_revision": 0}, ValueError),
        )

        for values, error_type in invalid_links:
            with self.subTest(values=values):
                with self.assertRaises(error_type):
                    RunState.from_snapshot(base_payload | values)

    def test_snapshot_v2_rejects_invalid_attempt_fields(self) -> None:
        payload = {
            "schema_version": 2,
            "input": "invalid",
            "last_agent_name": None,
            "last_response_id": None,
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [],
            "model_responses": [],
            "new_items": [],
            "model_attempts": [
                {
                    "request_id": "req_1",
                    "attempt_id": "attempt_1",
                    "attempt_number": 1,
                    "model_name": "model-a",
                    "mode": "batch",
                    "outcome": "succeeded",
                    "duration_seconds": 0.5,
                }
            ],
        }

        with self.assertRaises(ValueError):
            run_state_module.run_state_snapshot_from_dict(payload)

    def test_restored_run_state_can_approve_pending_tool_call_for_resume(self) -> None:
        request = ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "pending.txt"},
            reason="Needs approval.",
        )
        result = RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=1,
            input="delete pending.txt",
            new_items=(RunItem("tool_approval_required", 1, request),),
        )
        restored_payload = json.loads(json.dumps(result.to_state()))
        run_state = RunState.from_snapshot(restored_payload)

        run_state.approve_tool_call("delete_file", "call_1")

        self.assertEqual(
            run_state.context_wrapper.approval_status_for("delete_file", "call_1"),
            "approved",
        )
        self.assertEqual(
            run_state.pending_tool_calls,
            (ToolCall("delete_file", {"path": "pending.txt"}, "call_1"),),
        )
        self.assertIsInstance(run_state.new_items[0].payload, ToolApprovalRequest)

    def test_restored_run_state_can_reject_pending_tool_call_for_resume(self) -> None:
        request = ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "pending.txt"},
            reason="Needs approval.",
        )
        result = RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=1,
            input="delete pending.txt",
            new_items=(RunItem("tool_approval_required", 1, request),),
        )
        restored_payload = json.loads(json.dumps(result.to_state()))
        run_state = RunState.from_snapshot(restored_payload)

        run_state.reject_tool_call("delete_file", "call_1", "Outside workspace.")

        self.assertEqual(
            run_state.context_wrapper.approval_status_for("delete_file", "call_1"),
            "rejected",
        )
        self.assertEqual(
            run_state.context_wrapper.rejection_message_for("delete_file", "call_1"),
            "Outside workspace.",
        )
        self.assertEqual(
            run_state.pending_tool_calls,
            (ToolCall("delete_file", {"path": "pending.txt"}, "call_1"),),
        )

    def test_run_state_from_snapshot_restores_counters_approvals_and_tool_calls(self) -> None:
        agent = Agent(memory=AgentMemory(), model=object(), name="Planner")
        snapshot = RunStateSnapshot(
            input="delete files",
            last_agent_name="Planner",
            last_response_id="resp_1",
            current_turn=2,
            steps_taken=1,
            max_turns=4,
            max_steps=5,
            tool_approvals=(
                ApprovalSnapshot(
                    tool_name="delete_file",
                    call_id="call_1",
                    arguments={"path": "pending.txt"},
                    status="pending",
                    reason="Needs approval.",
                ),
                ApprovalSnapshot(
                    tool_name="delete_file",
                    call_id="call_2",
                    arguments={"path": "approved.txt"},
                    status="approved",
                    reason="Needs approval.",
                ),
                ApprovalSnapshot(
                    tool_name="delete_file",
                    call_id="call_3",
                    arguments={"path": "rejected.txt"},
                    status="rejected",
                    reason="Needs approval.",
                    rejection_message="Outside workspace.",
                ),
            ),
            model_responses=({"response_id": "resp_1", "output": []},),
            new_items=(
                {
                    "item_type": "tool_approval_required",
                    "step_number": 1,
                    "payload": {"call_id": "call_1"},
                    "metadata": {},
                },
            ),
        )
        persisted_state = json.loads(json.dumps(asdict(snapshot)))

        run_state = RunState.from_snapshot(persisted_state, agent=agent)

        self.assertEqual(run_state.input, "delete files")
        self.assertIs(run_state.last_agent, agent)
        self.assertEqual(run_state.current_turn, 2)
        self.assertEqual(run_state.steps_taken, 1)
        self.assertEqual(run_state.max_turns, 4)
        self.assertEqual(run_state.max_steps, 5)
        self.assertEqual(run_state.context_wrapper.approval_status_for("delete_file", "call_1"), "pending")
        self.assertEqual(run_state.context_wrapper.approval_status_for("delete_file", "call_2"), "approved")
        self.assertEqual(run_state.context_wrapper.approval_status_for("delete_file", "call_3"), "rejected")
        self.assertEqual(
            run_state.context_wrapper.rejection_message_for("delete_file", "call_3"),
            "Outside workspace.",
        )
        self.assertEqual(
            run_state.pending_tool_calls,
            (
                ToolCall("delete_file", {"path": "pending.txt"}, "call_1"),
                ToolCall("delete_file", {"path": "approved.txt"}, "call_2"),
                ToolCall("delete_file", {"path": "rejected.txt"}, "call_3"),
            ),
        )

    def test_run_state_keeps_process_items_not_legacy_result_fields(self) -> None:
        run_state_fields = {field.name for field in fields(RunState)}

        self.assertIn("new_items", run_state_fields)
        self.assertIn("handoff_depth", run_state_fields)
        self.assertIn("input", run_state_fields)
        self.assertIn("last_agent", run_state_fields)
        self.assertIn("current_turn", run_state_fields)
        self.assertIn("max_turns", run_state_fields)
        self.assertIn("max_steps", run_state_fields)
        self.assertIn("model_attempts", run_state_fields)
        self.assertIn("last_model_terminal_reason", run_state_fields)
        self.assertNotIn("trace", run_state_fields)
        self.assertNotIn("step_results", run_state_fields)
        self.assertNotIn("raw_responses", run_state_fields)

    def test_run_state_records_model_success_failure_and_cancellation(self) -> None:
        succeeded = ModelAttemptRecord(
            request_id="req_1",
            attempt_id="attempt_1",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
            outcome="succeeded",
            duration_seconds=0.5,
            response_id="resp_1",
        )
        response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text="done",
            tool_calls=[],
        )
        run_state = RunState()

        run_state.record_model_execution(ModelExecution(response, (succeeded,)))

        self.assertEqual(run_state.model_attempts, [succeeded])
        self.assertEqual(run_state.last_model_terminal_reason, "completed")
        failed = ModelAttemptRecord(
            request_id="req_2",
            attempt_id="attempt_2",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
            outcome="failed",
            duration_seconds=0.25,
            error_kind=ModelErrorKind.SERVER,
        )
        failure = ModelExecutionError(
            ModelResponseError("failed", kind=ModelErrorKind.SERVER),
            (failed,),
        )
        run_state.record_model_execution_error(failure)

        self.assertEqual(run_state.model_attempts, [succeeded, failed])
        self.assertEqual(run_state.last_model_terminal_reason, "server")
        cancelled = ModelAttemptRecord(
            request_id="req_3",
            attempt_id="attempt_3",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
            outcome="cancelled",
            duration_seconds=0.1,
            reason="cancelled",
        )
        run_state.record_model_cancelled((cancelled,))

        self.assertEqual(run_state.model_attempts, [succeeded, failed, cancelled])
        self.assertEqual(run_state.last_model_terminal_reason, "cancelled")
        run_state.record_model_cancelled()
        self.assertEqual(run_state.model_attempts, [succeeded, failed, cancelled])
        with self.assertRaises(TypeError):
            run_state.record_model_execution(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            run_state.record_model_execution_error(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            run_state.record_model_cancelled((object(),))  # type: ignore[arg-type]

    def test_run_state_uses_unknown_for_unclassified_model_errors(self) -> None:
        failed = ModelAttemptRecord(
            request_id="req_1",
            attempt_id="attempt_1",
            attempt_number=1,
            model_name="model-a",
            mode="non_stream",
            outcome="failed",
            duration_seconds=0,
        )
        run_state = RunState()

        run_state.record_model_execution_error(
            ModelExecutionError(ModelResponseError("failed"), (failed,))
        )

        self.assertEqual(run_state.last_model_terminal_reason, "unknown")

    def test_run_state_tracks_model_turns_and_tool_steps_separately(self) -> None:
        run_state = RunState(max_turns=2, max_steps=3)

        self.assertTrue(run_state.can_call_model())
        self.assertTrue(run_state.can_execute_tool())

        run_state.record_model_turn()
        run_state.record_model_turn()

        self.assertEqual(run_state.current_turn, 2)
        self.assertFalse(run_state.can_call_model())
        self.assertEqual(run_state.steps_taken, 0)

        run_state.record_tool_step()

        self.assertEqual(run_state.steps_taken, 1)
        self.assertTrue(run_state.can_execute_tool())

    def test_run_state_reports_no_limit_reason_when_limits_allow_progress(self) -> None:
        run_state = RunState(max_turns=2, max_steps=3)

        self.assertIsNone(run_state.model_limit_reason())
        self.assertIsNone(run_state.tool_limit_reason())
        self.assertIsNone(run_state.next_limit_reason())

    def test_run_state_reports_model_limit_reason_when_turns_are_exhausted(self) -> None:
        run_state = RunState(current_turn=2, max_turns=2, max_steps=3)

        self.assertEqual(run_state.model_limit_reason(), "max_turns_reached")
        self.assertIsNone(run_state.tool_limit_reason())
        self.assertEqual(run_state.next_limit_reason(), "max_turns_reached")

    def test_run_state_reports_tool_limit_reason_before_model_limit(self) -> None:
        run_state = RunState(current_turn=2, max_turns=2, steps_taken=3, max_steps=3)

        self.assertEqual(run_state.tool_limit_reason(), "max_steps_reached")
        self.assertEqual(run_state.model_limit_reason(), "max_turns_reached")
        self.assertEqual(run_state.next_limit_reason(), "max_steps_reached")

    def test_build_run_result_derives_legacy_surfaces_from_run_items(self) -> None:
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[ToolCall("echo_text", {"text": "hello"}, "call_1")],
        )
        run_state = RunState(
            new_items=[
                RunItem(
                    item_type="model_response",
                    step_number=1,
                    payload=model_response,
                ),
                RunItem(
                    item_type="tool_result",
                    step_number=1,
                    payload="hello",
                ),
                RunItem(
                    item_type="final_output",
                    step_number=1,
                    payload="hello",
                ),
            ],
            final_answer="hello",
            reached_final_answer=True,
            current_turn=1,
            max_turns=4,
            max_steps=5,
            steps_taken=1,
        )

        result = build_run_result(run_state)

        self.assertEqual(result.final_answer, "hello")
        self.assertEqual(result.step_results, ["hello"])
        self.assertEqual(result.raw_responses, (model_response,))
        self.assertEqual(result.new_items, tuple(run_state.new_items))
        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.current_turn, 1)
        self.assertEqual(result.max_turns, 4)
        self.assertEqual(result.max_steps, 5)
        self.assertEqual(result.steps_taken, 1)

    def test_build_run_result_returns_run_result_with_continuation_state(self) -> None:
        agent = Agent(memory=AgentMemory(), model=object(), name="Planner")
        run_state = RunState(
            input="Plan next step.",
            last_agent=agent,
            final_answer="ready",
            reached_final_answer=True,
        )

        result = build_run_result(run_state)

        self.assertIsInstance(result, RunResult)
        self.assertEqual(result.input, "Plan next step.")
        self.assertIs(result.last_agent, agent)


if __name__ == "__main__":
    unittest.main()
