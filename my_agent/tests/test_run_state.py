from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import ModelResponse, RunItem, RunState, ToolCall  # noqa: E402
from mini_smolagent.run_state import build_run_result  # noqa: E402


class RunStateTestCase(unittest.TestCase):
    def test_run_state_keeps_process_items_not_legacy_result_fields(self) -> None:
        run_state_fields = {field.name for field in fields(RunState)}

        self.assertIn("new_items", run_state_fields)
        self.assertIn("handoff_depth", run_state_fields)
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


if __name__ == "__main__":
    unittest.main()
