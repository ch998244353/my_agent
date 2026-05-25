from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.contracts import ModelResponse, RunItem, ToolCall  # noqa: E402
from agents.result import RunResult, RunResultBase  # noqa: E402


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

    def test_to_state_returns_minimal_resume_surface(self) -> None:
        agent = object()
        final_item = RunItem("final_output", 1, "done")
        response = ModelResponse("resp_1", [], None, [])
        result = RunResult(
            final_answer="done",
            step_results=[],
            reached_final_answer=True,
            steps_taken=1,
            input="Plan next step.",
            last_agent=agent,
            raw_responses=(response,),
            new_items=(final_item,),
        )

        state = result.to_state()

        self.assertEqual(state["input"], "Plan next step.")
        self.assertIs(state["last_agent"], agent)
        self.assertEqual(state["last_response_id"], "resp_1")
        self.assertEqual(state["final_output"], "done")
        self.assertTrue(state["reached_final_answer"])
        self.assertEqual(state["new_items"], (final_item,))


if __name__ == "__main__":
    unittest.main()
