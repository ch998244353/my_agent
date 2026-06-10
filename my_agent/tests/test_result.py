from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall  # noqa: E402
from agents.result import RunResult, RunResultBase  # noqa: E402
from agents.run_context import RunContextWrapper  # noqa: E402


class RunResultTestCase(unittest.TestCase):
    def test_run_result_shell_keeps_legacy_fields(self) -> None:
        result = RunResult(
            final_answer="done",
            step_results=["done"],
            reached_final_answer=True,
            steps_taken=1,
        )

        self.assertIsInstance(result, RunResultBase)
        self.assertEqual(result.final_answer, "done")
        self.assertEqual(result.step_results, ["done"])
        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.steps_taken, 1)

    def test_final_output_aliases_final_answer(self) -> None:
        result = RunResult(
            final_answer="done",
            step_results=[],
            reached_final_answer=True,
            steps_taken=0,
        )

        self.assertEqual(result.final_output, "done")

    def test_last_response_id_uses_latest_raw_response(self) -> None:
        first_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[],
        )
        second_response = ModelResponse(
            response_id="resp_2",
            output=[],
            output_text=None,
            tool_calls=[],
        )
        result = RunResult(
            final_answer="done",
            step_results=[],
            reached_final_answer=True,
            steps_taken=0,
            raw_responses=(first_response, second_response),
        )

        self.assertEqual(result.last_response_id, "resp_2")

    def test_last_response_id_is_none_without_raw_responses(self) -> None:
        result = RunResult(
            final_answer="done",
            step_results=[],
            reached_final_answer=True,
            steps_taken=0,
        )

        self.assertIsNone(result.last_response_id)

    def test_final_output_as_can_optionally_check_type(self) -> None:
        result = RunResult(
            final_answer="done",
            step_results=[],
            reached_final_answer=True,
            steps_taken=0,
        )

        self.assertEqual(result.final_output_as(str), "done")
        self.assertEqual(result.final_output_as(int), "done")
        with self.assertRaises(TypeError):
            result.final_output_as(int, raise_if_incorrect_type=True)

    def test_to_input_list_converts_run_items_to_chat_messages(self) -> None:
        tool_call = ToolCall("echo_text", {"text": "hello"}, "call_1")
        result = RunResult(
            final_answer="done",
            step_results=["hello"],
            reached_final_answer=True,
            steps_taken=1,
            input="Say hello.",
            new_items=(
                RunItem("tool_call", 1, tool_call),
                RunItem("tool_result", 1, "hello"),
                RunItem("final_output", 1, "done"),
            ),
        )

        messages = result.to_input_list()

        self.assertEqual(
            [message.role for message in messages],
            ["user", "tool_call", "tool_response", "assistant"],
        )
        self.assertEqual(messages[0].content, "Say hello.")
        self.assertEqual(messages[1].content, "call_1: echo_text(text='hello')")
        self.assertEqual(messages[2].content, "hello")
        self.assertEqual(messages[3].content, "done")

    def test_pending_approvals_returns_tool_approval_requests(self) -> None:
        request = ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "notes.txt"},
            reason="Needs user approval.",
        )
        result = RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=1,
            new_items=(
                RunItem("tool_call", 1, ToolCall("delete_file", {}, "call_1")),
                RunItem("tool_approval_required", 1, request),
                RunItem("tool_approval_required", 1, "legacy-metadata-only"),
            ),
        )

        self.assertEqual(result.pending_approvals, (request,))
        self.assertTrue(result.has_pending_approvals)

    def test_pending_approval_summaries_expose_display_ready_requests(self) -> None:
        first_request = ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "notes.txt"},
            reason="Needs user approval.",
        )
        second_request = ToolApprovalRequest(
            tool_name="write_file",
            call_id="call_2",
            arguments={"path": "todo.txt", "content": "ship"},
            reason=None,
        )
        result = RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=1,
            new_items=(
                RunItem("tool_approval_required", 1, first_request),
                RunItem("tool_result", 1, "unrelated"),
                RunItem("tool_approval_required", 1, second_request),
            ),
        )

        summaries = getattr(result, "pending_approval_summaries", None)

        self.assertIsNotNone(summaries)
        self.assertEqual(
            [
                (summary.tool_name, summary.call_id, summary.arguments, summary.reason)
                for summary in summaries
            ],
            [
                ("delete_file", "call_1", {"path": "notes.txt"}, "Needs user approval."),
                (
                    "write_file",
                    "call_2",
                    {"path": "todo.txt", "content": "ship"},
                    None,
                ),
            ],
        )

    def test_pending_approvals_is_empty_without_approval_items(self) -> None:
        result = RunResult(
            final_answer="done",
            step_results=[],
            reached_final_answer=True,
            steps_taken=1,
            new_items=(RunItem("tool_result", 1, "done"),),
        )

        self.assertEqual(result.pending_approvals, ())
        self.assertFalse(result.has_pending_approvals)

    def test_to_state_returns_json_compatible_resume_surface(self) -> None:
        class NamedAgent:
            name = "Planner"

        request = ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "notes.txt"},
            reason="Needs approval.",
        )
        context = RunContextWrapper()
        context.request_tool_call_approval("delete_file", "call_1")
        response = ModelResponse(
            "resp_1",
            [{"type": "function_call", "call_id": "call_1"}],
            None,
            [ToolCall("delete_file", {"path": "notes.txt"}, "call_1")],
            usage={"input_tokens": 10},
        )
        result = RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=1,
            input="Delete notes.",
            last_agent=NamedAgent(),
            current_turn=2,
            max_turns=4,
            max_steps=5,
            context_wrapper=context,
            raw_responses=(response,),
            new_items=(
                RunItem("model_response", 1, response),
                RunItem("tool_approval_required", 1, request),
            ),
        )

        state = result.to_state()

        json.dumps(state)
        self.assertNotIn("last_agent", state)
        self.assertEqual(state["input"], "Delete notes.")
        self.assertEqual(state["last_agent_name"], "Planner")
        self.assertEqual(state["last_response_id"], "resp_1")
        self.assertEqual(state["current_turn"], 2)
        self.assertEqual(state["steps_taken"], 1)
        self.assertEqual(state["max_turns"], 4)
        self.assertEqual(state["max_steps"], 5)
        self.assertEqual(state["tool_approvals"][0]["arguments"], {"path": "notes.txt"})
        self.assertEqual(state["tool_approvals"][0]["status"], "pending")
        self.assertEqual(state["model_responses"][0]["response_id"], "resp_1")
        self.assertEqual(state["new_items"][1]["payload"]["call_id"], "call_1")


if __name__ == "__main__":
    unittest.main()
