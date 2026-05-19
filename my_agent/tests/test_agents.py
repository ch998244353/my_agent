from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    Agent,
    AgentCapabilities,
    AgentMemory,
    AgentSession,
    FunctionTool,
    ModelResponse,
    MiniToolCallingAgent,
    MultiStepAgent,
    RunItem,
    RunState,
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
        self.recorded_tool_outputs = []
        self._index = 0

    def decide(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        if isinstance(action, Exception):
            raise action
        return action

    def record_tool_output(self, tool_call, output):
        self.recorded_tool_outputs.append((tool_call, output))


class ScriptedResponseModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.last_messages = []
        self.last_tool_specs = []
        self.recorded_tool_outputs = []
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
        if isinstance(response, Exception):
            raise response
        return response

    def record_tool_output(self, tool_call, output):
        self.recorded_tool_outputs.append((tool_call, output))


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


class AgentTestCase(unittest.TestCase):
    def test_compatibility_aliases_point_to_agent(self) -> None:
        self.assertIs(MultiStepAgent, Agent)
        self.assertIs(MiniToolCallingAgent, Agent)

    def test_agent_can_skip_final_answer_tool_with_capability_config(self) -> None:
        registry = ToolRegistry()

        Agent(
            memory=AgentMemory(),
            model=ScriptedModel([]),
            tool_registry=registry,
            capabilities=AgentCapabilities(final_answer_tool=False),
        )

        self.assertNotIn("final_answer", [spec.name for spec in registry.list_specs()])

    def test_agent_accepts_session_as_memory(self) -> None:
        session = AgentSession()
        agent = Agent(
            memory=session,
            model=ScriptedModel(
                [
                    ToolCall(
                        "final_answer",
                        {"answer": "done"},
                        "call_1",
                    )
                ]
            ),
        )

        run_result = agent.run("Return done.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(session.replay()[0].content, "Return done.")

    def test_agent_session_keeps_previous_run_as_turn_history(self) -> None:
        session = AgentSession()
        agent = Agent(
            memory=session,
            model=ScriptedModel(
                [
                    ToolCall("final_answer", {"answer": "first"}, "call_1"),
                    ToolCall("final_answer", {"answer": "second"}, "call_2"),
                ]
            ),
        )

        agent.run("First question.")
        agent.run("Second question.")

        user_messages = [
            message.content for message in session.replay() if message.role == "user"
        ]
        self.assertEqual(user_messages, ["First question.", "Second question."])

    def test_agent_exposes_handoff_targets_as_tool_specs(self) -> None:
        specialist = Agent(
            name="Math Agent",
            memory=AgentMemory(),
            model=ScriptedModel([]),
        )
        triage_model = ScriptedModel([])
        triage_agent = Agent(
            name="Triage Agent",
            memory=AgentMemory(),
            model=triage_model,
            handoffs=[specialist],
        )

        triage_agent.run("Route this request.")

        tool_names = [tool_spec.name for tool_spec in triage_model.last_tool_specs]
        self.assertIn("transfer_to_math_agent", tool_names)

    def test_agent_handoff_runs_target_agent_as_final_owner(self) -> None:
        specialist = Agent(
            name="Math Agent",
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall("final_answer", {"answer": "4"}, "call_specialist"),
                ]
            ),
        )
        triage_agent = Agent(
            name="Triage Agent",
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall(
                        "transfer_to_math_agent",
                        {"task": "Solve 2 + 2."},
                        "call_handoff",
                    )
                ]
            ),
            handoffs=[specialist],
        )

        run_result = triage_agent.run("I need math help.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, "4")
        self.assertEqual(specialist.memory.task, "Solve 2 + 2.")
        self.assertEqual(
            [item.item_type for item in run_result.new_items],
            ["tool_call", "handoff", "final_output"],
        )
        self.assertEqual(run_result.new_items[1].metadata["target_agent"], "Math Agent")

    def test_agent_executes_multiple_tool_steps(self) -> None:
        registry = ToolRegistry()

        def get_weather(city: str) -> str:
            return f"Weather in {city}: 18C and cloudy."

        def summarize_weather(report: str) -> str:
            return f"Summary: {report} Take an umbrella if needed."

        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="get_weather",
                    description="Get weather for a city.",
                    arguments=[
                        ToolArgument(
                            name="city",
                            description="City name.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="string",
                ),
                handler=get_weather,
            )
        )
        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="summarize_weather",
                    description="Summarize a weather report.",
                    arguments=[
                        ToolArgument(
                            name="report",
                            description="Weather report text.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="string",
                ),
                handler=summarize_weather,
            )
        )

        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="get_weather",
                    arguments={"city": "Shanghai"},
                    call_id="call_1",
                ),
                ToolCall(
                    tool_name="summarize_weather",
                    arguments={"report": "Weather in Shanghai: 18C and cloudy."},
                    call_id="call_2",
                ),
            ]
        )
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
            max_steps=5,
        )

        run_result = agent.run("Tell me the weather in Shanghai and summarize it.")

        self.assertEqual(run_result.steps_taken, 2)
        self.assertFalse(run_result.reached_final_answer)
        self.assertEqual(
            run_result.step_results,
            [
                "Weather in Shanghai: 18C and cloudy.",
                "Summary: Weather in Shanghai: 18C and cloudy. Take an umbrella if needed.",
            ],
        )

    def test_agent_executes_multiple_tool_calls_from_one_model_response(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        tool_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[
                ToolCall("echo_text", {"text": "first"}, "call_1"),
                ToolCall("echo_text", {"text": "second"}, "call_2"),
            ],
        )
        final_response = ModelResponse(
            response_id="resp_2",
            output=[],
            output_text="done",
            tool_calls=[],
        )
        model = ScriptedResponseModel(
            [
                tool_response,
                final_response,
            ]
        )
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
            max_steps=5,
        )

        run_result = agent.run("Echo two values.")

        self.assertEqual(run_result.step_results, ["first", "second"])
        self.assertEqual(run_result.steps_taken, 2)
        self.assertFalse(run_result.reached_final_answer)
        self.assertEqual(len(agent.memory.steps), 2)
        self.assertEqual(len(model.recorded_tool_outputs), 2)
        self.assertEqual(run_result.raw_responses, (tool_response, final_response))

    def test_agent_result_exposes_new_run_items(self) -> None:
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[
                ToolCall(
                    "final_answer",
                    {"answer": "done"},
                    "call_1",
                )
            ],
        )
        model = ScriptedResponseModel([model_response])
        agent = Agent(
            memory=AgentMemory(),
            model=model,
        )

        run_result = agent.run("Return done.")

        self.assertEqual(
            [item.item_type for item in run_result.new_items],
            ["model_response", "tool_call", "tool_result", "final_output"],
        )
        self.assertIsInstance(run_result.new_items[0], RunItem)
        self.assertIs(run_result.new_items[0].payload, model_response)
        self.assertEqual(run_result.new_items[1].payload.tool_name, "final_answer")
        self.assertEqual(run_result.new_items[2].payload, "done")
        self.assertEqual(run_result.new_items[3].payload, "done")

    def test_agent_uses_structured_model_output_as_final_answer(self) -> None:
        output_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["answer", "confidence"],
            "additionalProperties": False,
        }
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text='{"answer": "done", "confidence": 0.9}',
            tool_calls=[],
        )
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel([model_response]),
            output_type=output_schema,
        )

        run_result = agent.run("Return a structured answer.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(
            run_result.final_answer,
            {"answer": "done", "confidence": 0.9},
        )
        self.assertEqual(
            [item.item_type for item in run_result.new_items],
            ["model_response", "final_output"],
        )

    def test_agent_records_structured_output_schema_error(self) -> None:
        output_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        }
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text='{"extra": "bad"}',
            tool_calls=[],
        )
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel([model_response]),
            output_type=output_schema,
        )

        run_result = agent.run("Return a structured answer.")

        self.assertFalse(run_result.reached_final_answer)
        self.assertIsNone(run_result.final_answer)
        self.assertEqual(run_result.current_turn, 1)
        self.assertEqual(run_result.steps_taken, 0)
        self.assertEqual(
            [item.item_type for item in run_result.new_items],
            ["model_response", "model_error"],
        )
        self.assertIn("$.answer", run_result.new_items[-1].payload)

    def test_run_state_keeps_run_items_as_single_result_stream(self) -> None:
        run_state_fields = {field.name for field in fields(RunState)}

        self.assertIn("new_items", run_state_fields)
        self.assertNotIn("trace", run_state_fields)
        self.assertNotIn("step_results", run_state_fields)
        self.assertNotIn("raw_responses", run_state_fields)

    def test_agent_records_stop_reason_as_run_item(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel([]),
            tool_registry=registry,
        )

        run_result = agent.run("Stop without a tool call.")

        self.assertEqual(run_result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(run_result.new_items[-1].payload, "model_returned_no_tool_call")

    def test_agent_stop_on_first_tool_uses_tool_result_as_final_answer(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "direct result"}, "call_1"),
                ToolCall(
                    "final_answer",
                    {"answer": "should not run"},
                    "call_2",
                ),
            ]
        )
        memory = AgentMemory()
        agent = Agent(
            memory=memory,
            model=model,
            tool_registry=registry,
            tool_use_behavior="stop_on_first_tool",
        )

        run_result = agent.run("Return the first tool result directly.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, "direct result")
        self.assertEqual(run_result.steps_taken, 1)
        self.assertEqual(len(memory.steps), 1)
        self.assertTrue(memory.steps[0].is_final_answer)

    def test_agent_stop_at_tools_uses_named_tool_result_as_final_answer(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "named stop result"}, "call_1"),
                ToolCall(
                    "final_answer",
                    {"answer": "should not run"},
                    "call_2",
                ),
            ]
        )
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
            tool_use_behavior={"stop_at_tool_names": ["echo_text"]},
        )

        run_result = agent.run("Stop when echo_text returns.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, "named stop result")
        self.assertEqual(run_result.steps_taken, 1)

    def test_agent_records_tool_and_stop_events_as_run_items(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [ToolCall("echo_text", {"text": "hello"}, "call_1")]
            ),
            tool_registry=registry,
            max_steps=1,
        )

        run_result = agent.run("Echo hello.")

        self.assertEqual(
            [item.item_type for item in run_result.new_items],
            ["tool_call", "tool_result", "run_stopped"],
        )
        self.assertEqual(run_result.new_items[1].payload, "hello")
        self.assertEqual(run_result.new_items[-1].payload, "max_steps_reached")

    def test_agent_records_tool_error_and_allows_retry(self) -> None:
        model = ScriptedModel(
            [
                ToolCall("unknown_tool", {}, "call_1"),
                ToolCall(
                    "final_answer",
                    {"answer": "Recovered after reading the error."},
                    "call_2",
                ),
            ]
        )
        memory = AgentMemory()
        agent = Agent(
            memory=memory,
            model=model,
            tool_registry=ToolRegistry(),
            max_steps=3,
        )

        run_result = agent.run("Recover from a bad tool call.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, "Recovered after reading the error.")
        self.assertEqual(run_result.steps_taken, 2)
        self.assertIn("unknown_tool", memory.steps[0].error)
        self.assertEqual(run_result.new_items[1].item_type, "tool_error")
        self.assertIn("unknown_tool", run_result.new_items[1].payload)
        self.assertEqual(run_result.new_items[-1].item_type, "final_output")
        self.assertTrue(
            any("Now let's retry" in message.content for message in model.last_messages)
        )

    def test_agent_classifies_tool_argument_error(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {}, "call_1"),
                ToolCall(
                    "final_answer",
                    {"answer": "Recovered from bad arguments."},
                    "call_2",
                ),
            ]
        )
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
        )

        run_result = agent.run("Call echo_text with bad arguments, then recover.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.new_items[1].item_type, "tool_error")
        self.assertIn("text", run_result.new_items[1].payload)
        self.assertIn("Now let's retry", model.last_messages[-1].content)

    def test_agent_classifies_tool_execution_error(self) -> None:
        registry = ToolRegistry()

        def explode() -> str:
            raise ValueError("weather service unavailable")

        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="get_weather",
                    description="Get weather for a city.",
                    arguments=[],
                    returns="string",
                ),
                handler=explode,
            )
        )
        model = ScriptedModel(
            [
                ToolCall("get_weather", {}, "call_1"),
                ToolCall(
                    "final_answer",
                    {"answer": "Recovered from tool failure."},
                    "call_2",
                ),
            ]
        )
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
        )

        run_result = agent.run("Call a broken weather tool, then recover.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.new_items[1].item_type, "tool_error")
        self.assertIn("weather service unavailable", run_result.new_items[1].payload)

    def test_agent_classifies_model_error_and_stops(self) -> None:
        memory = AgentMemory()
        agent = Agent(
            memory=memory,
            model=ScriptedModel([RuntimeError("model API unavailable")]),
            tool_registry=ToolRegistry(),
        )

        run_result = agent.run("Trigger a model error.")

        self.assertFalse(run_result.reached_final_answer)
        self.assertEqual(run_result.current_turn, 1)
        self.assertEqual(run_result.steps_taken, 0)
        self.assertEqual(len(memory.steps), 1)
        self.assertIn("model API unavailable", memory.steps[0].error)
        self.assertEqual(run_result.new_items[0].item_type, "model_error")
        self.assertIn("model API unavailable", run_result.new_items[0].payload)

    def test_agent_stops_when_model_returns_no_tool_call(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel([]),
            tool_registry=registry,
        )

        run_result = agent.run("Stop without a tool call.")

        self.assertEqual(run_result.steps_taken, 0)
        self.assertFalse(run_result.reached_final_answer)
        self.assertEqual(run_result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(run_result.new_items[-1].payload, "model_returned_no_tool_call")

    def test_agent_respects_max_steps(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall("echo_text", {"text": "1"}, "call_1"),
                    ToolCall("echo_text", {"text": "2"}, "call_2"),
                    ToolCall("echo_text", {"text": "3"}, "call_3"),
                ]
            ),
            tool_registry=registry,
            max_steps=2,
        )

        run_result = agent.run("Echo twice.")

        self.assertEqual(run_result.step_results, ["1", "2"])
        self.assertFalse(run_result.reached_final_answer)
        self.assertEqual(run_result.steps_taken, 2)
        self.assertEqual(run_result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(run_result.new_items[-1].payload, "max_steps_reached")

    def test_agent_stops_after_final_answer(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "draft"}, "call_1"),
                ToolCall(
                    "final_answer",
                    {"answer": "This is the final answer."},
                    "call_2",
                ),
                ToolCall("echo_text", {"text": "should not run"}, "call_3"),
            ]
        )
        memory = AgentMemory()
        agent = Agent(memory=memory, model=model, tool_registry=registry, max_steps=5)

        run_result = agent.run("Echo, then finish.")

        self.assertEqual(run_result.step_results, ["draft", "This is the final answer."])
        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, "This is the final answer.")
        self.assertEqual(run_result.steps_taken, 2)
        self.assertEqual(len(memory.steps), 2)
        self.assertEqual(memory.steps[1].tool_calls[0].tool_name, "final_answer")
        self.assertTrue(memory.steps[1].is_final_answer)
        self.assertIn("final_answer", [spec.name for spec in model.last_tool_specs])


if __name__ == "__main__":
    unittest.main()
