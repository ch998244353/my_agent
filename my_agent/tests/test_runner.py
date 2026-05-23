from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
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
from agents.tracing import (  # noqa: E402
    BatchTraceProcessor,
    InMemoryTracingExporter,
    set_trace_processors,
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
    def tearDown(self) -> None:
        set_trace_processors([])

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

    def test_runner_run_sync_creates_trace_and_agent_span(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "traced"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        result = Runner.run_sync(
            agent,
            "Trace this run.",
            config=RunConfig(metadata={"course": "tracing"}),
        )
        processor.force_flush()
        exported = exporter.items()

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(
            [item["object"] for item in exported],
            ["trace.span", "trace.span", "trace.span", "trace.span", "trace.span", "trace"],
        )
        self.assertEqual(exported[0]["span_data"]["type"], "generation")
        self.assertEqual(exported[0]["span_data"]["model"], "ScriptedModel")
        self.assertEqual(exported[0]["span_data"]["agent"], "Planner")
        self.assertEqual(exported[0]["span_data"]["model_config"]["message_count"], 1)
        self.assertEqual(exported[0]["span_data"]["model_config"]["tool_count"], 1)
        self.assertEqual(exported[1]["span_data"]["type"], "function")
        self.assertEqual(exported[1]["span_data"]["name"], "final_answer")
        self.assertEqual(exported[1]["span_data"]["agent"], "Planner")
        self.assertEqual(exported[1]["span_data"]["input"], {"answer": "traced"})
        self.assertEqual(exported[1]["span_data"]["arguments"], {"answer": "traced"})
        self.assertEqual(exported[1]["span_data"]["output"], "traced")
        self.assertEqual(exported[2]["span_data"]["name"], "turn")
        self.assertEqual(exported[2]["span_data"]["data"]["turn"], 1)
        self.assertEqual(exported[2]["span_data"]["data"]["agent_name"], "Planner")
        self.assertEqual(exported[3]["span_data"]["type"], "agent")
        self.assertEqual(exported[3]["span_data"]["name"], "Planner")
        self.assertEqual(exported[3]["span_data"]["task"], "Trace this run.")
        self.assertEqual(exported[4]["span_data"]["name"], "task")
        self.assertEqual(exported[4]["span_data"]["data"]["name"], "Trace this run.")
        self.assertEqual(exported[5]["workflow_name"], "Planner")
        self.assertEqual(exported[5]["metadata"]["course"], "tracing")
        self.assertNotIn("spans", exported[5])

    def test_run_config_can_customize_trace_workflow_name(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "named trace"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        Runner.run_sync(
            agent,
            "Trace with a logical workflow name.",
            config=RunConfig(workflow_name="Course workflow"),
        )
        processor.force_flush()
        exported = exporter.items()

        self.assertEqual(exported[-1]["object"], "trace")
        self.assertEqual(exported[-1]["workflow_name"], "Course workflow")
        self.assertEqual(exported[-3]["span_data"]["type"], "agent")
        self.assertEqual(exported[-3]["span_data"]["name"], "Planner")
        self.assertEqual(exported[-2]["span_data"]["name"], "task")

    def test_run_config_can_disable_tracing_for_single_run(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "no trace"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        result = Runner.run_sync(
            agent,
            "Run without tracing.",
            config=RunConfig(tracing_disabled=True),
        )
        processor.force_flush()

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(exporter.items(), [])

    def test_run_config_can_exclude_sensitive_trace_data(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "secret"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        Runner.run_sync(
            agent,
            "User phone is 555-0100.",
            config=RunConfig(trace_include_sensitive_data=False),
        )
        processor.force_flush()
        exported = exporter.items()

        self.assertIsNone(exported[0]["span_data"]["input"])
        self.assertIsNone(exported[1]["span_data"]["input"])
        self.assertIsNone(exported[1]["span_data"]["output"])
        self.assertNotIn("arguments", exported[1]["span_data"])

    def test_run_config_can_set_trace_identity_and_metadata(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "identified trace"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        Runner.run_sync(
            agent,
            "Trace with identity.",
            config=RunConfig(
                metadata={"run": "context"},
                trace_metadata={"trace": "metadata"},
                trace_id="trace_course_1",
                group_id="group_course",
            ),
        )
        processor.force_flush()
        exported_trace = exporter.items()[-1]

        self.assertEqual(exported_trace["object"], "trace")
        self.assertEqual(exported_trace["id"], "trace_course_1")
        self.assertEqual(exported_trace["group_id"], "group_course")
        self.assertEqual(exported_trace["metadata"], {"trace": "metadata"})

    def test_tool_span_records_structured_error_type(self) -> None:
        def failing_tool() -> str:
            raise ValueError("boom")

        registry = ToolRegistry()
        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="fail_tool",
                    description="Always fails.",
                    arguments=[],
                    returns="string",
                ),
                handler=failing_tool,
            )
        )
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [
                ToolCall("fail_tool", {}, "call_fail"),
                ToolCall("final_answer", {"answer": "recovered"}, "call_done"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry, name="Repair")

        result = Runner.run_sync(agent, "Recover from a tool error.")
        processor.force_flush()
        tool_spans = [
            item for item in exporter.items()
            if item["object"] == "trace.span" and item["span_data"]["type"] == "function"
        ]

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(tool_spans[0]["span_data"]["name"], "fail_tool")
        self.assertEqual(tool_spans[0]["span_data"]["error_type"], "ToolExecutionError")
        self.assertEqual(tool_spans[0]["span_data"]["error_cause_type"], "ValueError")
        self.assertIn("boom", tool_spans[0]["error"]["message"])
        self.assertEqual(
            tool_spans[0]["error"]["data"],
            {
                "error_type": "ToolExecutionError",
                "error_cause_type": "ValueError",
            },
        )


if __name__ == "__main__":
    unittest.main()
