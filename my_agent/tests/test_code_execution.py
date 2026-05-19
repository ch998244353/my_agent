from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    Agent,
    AgentCapabilities,
    AgentMemory,
    CodeExecutionError,
    FunctionTool,
    MiniCodeAgent,
    MiniPythonExecutor,
    OpenAIResponsesModel,
    ToolArgument,
    ToolCall,
    ToolRegistry,
    ToolSpec,
    create_final_answer_tool,
    create_python_executor_tool,
)


class ScriptedModel:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self.last_messages = []
        self.message_history = []
        self.recorded_tool_outputs = []
        self._index = 0

    def decide(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.message_history.append(list(messages))
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        if isinstance(action, Exception):
            raise action
        return action

    def record_tool_output(self, tool_call, output):
        self.recorded_tool_outputs.append((tool_call, output))


class FakeResponsesClient:
    def __init__(self, outputs):
        self.outputs = outputs
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        output_index = min(len(self.requests) - 1, len(self.outputs) - 1)
        return SimpleNamespace(
            id=f"resp_{len(self.requests)}",
            output=self.outputs[output_index],
        )


class FakeOpenAIClient:
    def __init__(self, outputs):
        self.responses = FakeResponsesClient(outputs)


def code_agent(model, *, registry=None, max_steps=5) -> Agent:
    return Agent(
        memory=AgentMemory(),
        model=model,
        tool_registry=registry or ToolRegistry(),
        max_steps=max_steps,
        capabilities=AgentCapabilities(
            final_answer_tool=False,
            python_execution=True,
        ),
    )


class MiniPythonExecutorTestCase(unittest.TestCase):
    def test_execute_captures_print_logs_and_last_expression_output(self) -> None:
        executor = MiniPythonExecutor()

        result = executor.execute(
            """x = 1 + 2
print("value", x)
x * 10"""
        )

        self.assertEqual(result.output, 30)
        self.assertEqual(result.logs, "value 3\n")
        self.assertFalse(result.is_final_answer)

    def test_execute_keeps_state_between_runs(self) -> None:
        executor = MiniPythonExecutor()

        first = executor.execute("value = 7")
        second = executor.execute("value + 1")

        self.assertIsNone(first.output)
        self.assertEqual(second.output, 8)

    def test_execute_wraps_python_errors(self) -> None:
        executor = MiniPythonExecutor()

        with self.assertRaises(CodeExecutionError) as error:
            executor.execute("missing_name + 1")

        self.assertIn("missing_name", str(error.exception))

    def test_execute_final_answer_stops_code_and_marks_result_final(self) -> None:
        executor = MiniPythonExecutor()

        result = executor.execute(
            """print("before")
final_answer("done")
print("after")"""
        )

        self.assertEqual(result.output, "done")
        self.assertEqual(result.logs, "before\n")
        self.assertTrue(result.is_final_answer)

    def test_python_executor_tool_argument_has_schema(self) -> None:
        tool = create_python_executor_tool(MiniPythonExecutor())

        argument = tool.spec.arguments[0]

        self.assertEqual(argument.name, "code")
        self.assertEqual(argument.schema, {"type": "string"})

    def test_final_answer_tool_argument_has_schema(self) -> None:
        tool = create_final_answer_tool()

        argument = tool.spec.arguments[0]

        self.assertEqual(argument.name, "answer")
        self.assertEqual(argument.schema, {"type": "string"})


class AgentPythonExecutionCapabilityTestCase(unittest.TestCase):
    def test_agent_registers_python_executor_when_capability_enabled(self) -> None:
        registry = ToolRegistry()

        Agent(
            memory=AgentMemory(),
            model=ScriptedModel([]),
            tool_registry=registry,
            capabilities=AgentCapabilities(
                final_answer_tool=False,
                python_execution=True,
            ),
        )

        self.assertIn("python_executor", [spec.name for spec in registry.list_specs()])

    def test_minicodeagent_compatibility_constructor_returns_agent(self) -> None:
        agent = MiniCodeAgent(
            memory=AgentMemory(),
            model=ScriptedModel([]),
        )

        self.assertIsInstance(agent, Agent)
        self.assertIn("python_executor", [spec.name for spec in agent.tool_registry.list_specs()])

    def test_agent_runs_python_executor_until_final_answer(self) -> None:
        memory = AgentMemory()
        model = ScriptedModel(
            [
                ToolCall(
                    "python_executor",
                    {"code": 'value = 7\nprint("stored", value)'},
                    "call_1",
                ),
                ToolCall(
                    "python_executor",
                    {"code": "final_answer(value + 1)"},
                    "call_2",
                ),
            ]
        )
        agent = Agent(
            memory=memory,
            model=model,
            max_steps=3,
            capabilities=AgentCapabilities(
                final_answer_tool=False,
                python_execution=True,
            ),
        )

        run_result = agent.run("Store a value, then answer.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, 8)
        self.assertEqual(run_result.steps_taken, 2)
        self.assertEqual(run_result.step_results, [None, 8])
        self.assertIn("Execution logs:\nstored 7\n", memory.steps[0].observation)
        self.assertIn("Last output from code snippet:\nNone", memory.steps[0].observation)
        self.assertTrue(memory.steps[1].is_final_answer)
        self.assertEqual(
            memory.return_full_code(),
            'value = 7\nprint("stored", value)\n\nfinal_answer(value + 1)',
        )

    def test_agent_uses_tool_registry_for_python_execution(self) -> None:
        model = ScriptedModel(
            [ToolCall("python_executor", {"code": "final_answer(8)"}, "call_1")]
        )
        agent = code_agent(model, max_steps=1)
        original_execute = agent.tool_registry.execute
        execute_calls = []

        def tracking_execute(tool_name, arguments):
            execute_calls.append((tool_name, dict(arguments)))
            return original_execute(tool_name, arguments)

        agent.tool_registry.execute = tracking_execute

        run_result = agent.run("Answer with Python.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(execute_calls, [("python_executor", {"code": "final_answer(8)"})])

    def test_agent_uses_provided_tool_registry(self) -> None:
        registry = ToolRegistry()
        registry.register(
            FunctionTool(
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
        )

        agent = code_agent(ScriptedModel([]), registry=registry)

        self.assertIs(agent.tool_registry, registry)
        self.assertEqual(
            {spec.name for spec in agent.tool_registry.list_specs()},
            {"echo_text", "python_executor"},
        )

    def test_agent_records_rendered_python_output_for_model_callback(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    "python_executor",
                    {"code": 'print("stored", 7)\nfinal_answer(8)'},
                    "call_1",
                )
            ]
        )
        agent = code_agent(model, max_steps=1)

        run_result = agent.run("Store a value, then answer.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(len(model.recorded_tool_outputs), 1)
        self.assertEqual(model.recorded_tool_outputs[0][0].tool_name, "python_executor")
        self.assertIn("Execution logs:\nstored 7\n", model.recorded_tool_outputs[0][1])

    def test_agent_writes_python_error_observation_and_retries(self) -> None:
        model = ScriptedModel(
            [
                ToolCall("python_executor", {"code": "missing_name + 1"}, "call_1"),
                ToolCall(
                    "python_executor",
                    {"code": 'final_answer("recovered")'},
                    "call_2",
                ),
            ]
        )
        agent = code_agent(model, max_steps=2)

        run_result = agent.run("Recover from bad code.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, "recovered")
        self.assertIn("missing_name", agent.memory.steps[0].error)
        self.assertTrue(
            any("Now let's retry" in message.content for message in model.message_history[1])
        )

    def test_agent_records_python_events_as_run_items(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    "python_executor",
                    {"code": 'value = 7\nprint("stored", value)'},
                    "call_1",
                ),
                ToolCall(
                    "python_executor",
                    {"code": "final_answer(value + 1)"},
                    "call_2",
                ),
            ]
        )
        agent = code_agent(model, max_steps=3)

        run_result = agent.run("Store a value, then answer.")

        self.assertEqual(
            [item.item_type for item in run_result.new_items],
            ["tool_call", "tool_result", "tool_call", "tool_result", "final_output"],
        )
        self.assertEqual(run_result.new_items[0].payload.tool_name, "python_executor")
        self.assertEqual(
            run_result.new_items[0].payload.arguments,
            {"code": 'value = 7\nprint("stored", value)'},
        )
        self.assertIn(
            "Execution logs:\nstored 7\n",
            run_result.new_items[1].metadata["observation"],
        )
        self.assertTrue(run_result.reached_final_answer)

    def test_agent_records_python_execution_error_as_run_item(self) -> None:
        model = ScriptedModel(
            [
                ToolCall("python_executor", {"code": "missing_name + 1"}, "call_1"),
                ToolCall(
                    "python_executor",
                    {"code": 'final_answer("recovered")'},
                    "call_2",
                ),
            ]
        )
        agent = code_agent(model, max_steps=2)

        run_result = agent.run("Recover from bad code.")

        self.assertEqual(run_result.new_items[1].item_type, "tool_error")
        self.assertIn("missing_name", run_result.new_items[1].payload)
        self.assertEqual(run_result.new_items[-1].item_type, "final_output")

    def test_agent_accepts_dict_like_python_execution_results(self) -> None:
        registry = ToolRegistry()
        registry.register(create_final_answer_tool())
        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="python_executor",
                    description="Return dict-like execution results.",
                    arguments=[
                        ToolArgument(
                            name="code",
                            description="Python code to execute.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="object",
                ),
                handler=lambda code: {
                    "output": len(code),
                    "logs": "dict logs\n",
                    "is_final_answer": True,
                },
            )
        )
        agent = code_agent(
            ScriptedModel([ToolCall("python_executor", {"code": "value = 7"}, "call_1")]),
            registry=registry,
            max_steps=1,
        )

        run_result = agent.run("Use dict-like result.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, 9)
        self.assertEqual(run_result.new_items[-1].item_type, "final_output")

    def test_agent_runs_python_executor_with_openai_responses_model_adapter(self) -> None:
        fake_client = FakeOpenAIClient(
            outputs=[
                [
                    {
                        "type": "function_call",
                        "name": "python_executor",
                        "arguments": "{\"code\": \"final_answer(8)\"}",
                        "call_id": "call_1",
                    }
                ]
            ]
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)
        agent = code_agent(model, max_steps=1)

        run_result = agent.run("Answer with Python.")

        self.assertTrue(run_result.reached_final_answer)
        self.assertEqual(run_result.final_answer, 8)
        self.assertEqual(fake_client.responses.requests[0]["tool_choice"], "auto")
        self.assertIn(
            "python_executor",
            [tool["name"] for tool in fake_client.responses.requests[0]["tools"]],
        )
        self.assertNotIn("text", fake_client.responses.requests[0])


if __name__ == "__main__":
    unittest.main()
