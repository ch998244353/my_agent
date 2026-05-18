from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    Agent,
    AgentMemory,
    GuardrailFunctionOutput,
    ModelResponse,
    RunConfig,
    Runner,
    ToolCall,
    input_guardrail,
    output_guardrail,
)


class CountingResponseModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get_response(self, messages, tool_specs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def final_answer_response(answer: str) -> ModelResponse:
    return ModelResponse(
        response_id=None,
        output=[],
        output_text=None,
        tool_calls=[
            ToolCall("final_answer", {"answer": answer}, f"call_{answer}"),
        ],
    )


class GuardrailsTestCase(unittest.TestCase):
    def test_input_guardrail_tripwire_stops_before_model_call(self) -> None:
        seen_inputs = []

        @input_guardrail(name="block_forbidden")
        def block_forbidden(context, agent, user_input):
            seen_inputs.append((context, agent, user_input))
            return GuardrailFunctionOutput(
                output_info={"reason": "blocked input"},
                tripwire_triggered="forbidden" in user_input,
            )

        model = CountingResponseModel([final_answer_response("should not run")])
        agent = Agent(memory=AgentMemory(), model=model)

        result = Runner.run_sync(
            agent,
            "this is forbidden",
            config=RunConfig(input_guardrails=[block_forbidden]),
        )

        self.assertEqual(model.calls, 0)
        self.assertEqual(result.current_turn, 0)
        self.assertFalse(result.reached_final_answer)
        self.assertIsNone(result.final_answer)
        self.assertEqual(len(result.input_guardrail_results), 1)
        self.assertTrue(result.input_guardrail_results[0].output.tripwire_triggered)
        self.assertEqual(result.input_guardrail_results[0].guardrail_name, "block_forbidden")
        self.assertIs(seen_inputs[0][0], result.context_wrapper)
        self.assertIs(seen_inputs[0][1], agent)
        self.assertEqual(seen_inputs[0][2], "this is forbidden")
        self.assertEqual(result.new_items[0].item_type, "input_guardrail")
        self.assertEqual(result.new_items[-1].payload, "input_guardrail_triggered")

    def test_output_guardrail_tripwire_blocks_final_answer(self) -> None:
        @output_guardrail(name="block_unsafe_output")
        def block_unsafe_output(context, agent, output):
            return GuardrailFunctionOutput(
                output_info={"checked_output": output},
                tripwire_triggered=output == "unsafe",
            )

        model = CountingResponseModel([final_answer_response("unsafe")])
        agent = Agent(memory=AgentMemory(), model=model)

        result = Runner.run_sync(
            agent,
            "Return unsafe.",
            config=RunConfig(output_guardrails=[block_unsafe_output]),
        )

        self.assertEqual(model.calls, 1)
        self.assertEqual(result.steps_taken, 1)
        self.assertFalse(result.reached_final_answer)
        self.assertIsNone(result.final_answer)
        self.assertEqual(len(result.output_guardrail_results), 1)
        self.assertTrue(result.output_guardrail_results[0].output.tripwire_triggered)
        self.assertEqual(
            result.output_guardrail_results[0].guardrail_name,
            "block_unsafe_output",
        )
        self.assertIn(
            "output_guardrail",
            [item.item_type for item in result.new_items],
        )
        self.assertNotIn(
            "final_output",
            [item.item_type for item in result.new_items],
        )
        self.assertEqual(result.new_items[-1].payload, "output_guardrail_triggered")

    def test_output_guardrail_allows_final_answer(self) -> None:
        @output_guardrail
        def allow_output(context, agent, output):
            return GuardrailFunctionOutput(
                output_info={"checked_output": output},
                tripwire_triggered=False,
            )

        model = CountingResponseModel([final_answer_response("safe")])
        agent = Agent(memory=AgentMemory(), model=model)

        result = Runner.run_sync(
            agent,
            "Return safe.",
            config=RunConfig(output_guardrails=[allow_output]),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "safe")
        self.assertEqual(len(result.output_guardrail_results), 1)
        self.assertFalse(result.output_guardrail_results[0].output.tripwire_triggered)
        self.assertEqual(
            result.output_guardrail_results[0].guardrail_name,
            "allow_output",
        )
        self.assertIn(
            "final_output",
            [item.item_type for item in result.new_items],
        )


if __name__ == "__main__":
    unittest.main()
