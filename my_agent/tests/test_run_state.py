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

from agents import Agent, AgentMemory, ModelResponse, RunItem, RunState, ToolCall  # noqa: E402
from agents.result import RunResult  # noqa: E402
from agents.run_state import ApprovalSnapshot, RunStateSnapshot, build_run_result  # noqa: E402


class RunStateTestCase(unittest.TestCase):
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
        self.assertNotIn("trace", run_state_fields)
        self.assertNotIn("step_results", run_state_fields)
        self.assertNotIn("raw_responses", run_state_fields)

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
