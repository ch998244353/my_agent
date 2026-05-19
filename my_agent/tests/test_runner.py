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
    FunctionTool,
    ModelResponse,
    RunConfig,
    Runner,
    ToolArgument,
    ToolCall,
    ToolRegistry,
    ToolSpec,
)


class ScriptedModel:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self.last_messages = []
        self.last_tool_specs = []
        self._index = 0

    def decide(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        return action


class ScriptedResponseModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.last_messages = []
        self.last_tool_specs = []
        self._index = 0

    def get_response(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.responses):
            return ModelResponse(
                response_id=None,
                output=[],
                output_text=None,
                tool_calls=[],
            )
        response = self.responses[self._index]
        self._index += 1
        return response


def echo_tool() -> FunctionTool:
    return FunctionTool(
        spec=ToolSpec(
            name="echo_text",
            description="Return the same text.",
            arguments=[
                ToolArgument(
                    name="text",
                    description="Input text.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        ),
        handler=lambda text: text,
    )


class RunnerTestCase(unittest.TestCase):
    def test_runner_run_sync_executes_agent_loop(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "runner finished"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        result = Runner.run_sync(agent, "Finish through Runner.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "runner finished")
        self.assertEqual(result.steps_taken, 1)
        self.assertEqual(model.last_messages[0].content, "Finish through Runner.")

    def test_runner_run_sync_uses_runtime_loop_not_agent_private_run(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "runtime loop finished"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        def fail_if_called(task: str, config=None):
            raise AssertionError("Runner should not call Agent._run()")

        agent._run = fail_if_called

        result = Runner.run_sync(agent, "Finish through runtime loop.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "runtime loop finished")
        self.assertEqual(result.steps_taken, 1)

    def test_agent_run_keeps_backward_compatible_entrypoint(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "agent run still works"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        result = agent.run("Finish through Agent.run.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "agent run still works")
        self.assertEqual(result.steps_taken, 1)

    def test_runner_run_sync_accepts_run_config_max_steps(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "should not run"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, max_steps=5)

        result = Runner.run_sync(
            agent,
            "Stop before tool execution.",
            config=RunConfig(max_steps=0),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.steps_taken, 0)
        self.assertEqual(result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(result.new_items[-1].payload, "max_steps_reached")

    def test_runner_run_sync_accepts_run_config_max_turns(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "first"}, "call_1"),
                ToolCall("final_answer", {"answer": "should not run"}, "call_2"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)

        result = Runner.run_sync(
            agent,
            "Echo once, then stop before the second model call.",
            config=RunConfig(max_turns=1),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.current_turn, 1)
        self.assertEqual(result.max_turns, 1)
        self.assertEqual(result.steps_taken, 1)
        self.assertEqual(model._index, 1)
        self.assertEqual(result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(result.new_items[-1].payload, "max_turns_reached")

    def test_current_turn_counts_one_model_response_with_multiple_tools_once(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[
                ToolCall("echo_text", {"text": "first"}, "call_1"),
                ToolCall("echo_text", {"text": "second"}, "call_2"),
            ],
        )
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel([model_response]),
            tool_registry=registry,
            max_steps=5,
        )

        result = Runner.run_sync(
            agent,
            "Echo two values.",
            config=RunConfig(max_turns=1),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.current_turn, 1)
        self.assertEqual(result.steps_taken, 2)
        self.assertEqual(result.step_results, ["first", "second"])
        self.assertEqual(result.new_items[-1].payload, "max_turns_reached")

    def test_run_config_tool_use_behavior_overrides_agent_default(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall(
                        tool_name="echo_text",
                        arguments={"text": "direct"},
                        call_id="call_1",
                    )
                ]
            ),
            tool_registry=registry,
            tool_use_behavior="run_llm_again",
        )

        result = agent.run(
            "Return direct tool output.",
            config=RunConfig(tool_use_behavior="stop_on_first_tool"),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "direct")
        self.assertEqual(result.steps_taken, 1)


if __name__ == "__main__":
    unittest.main()
