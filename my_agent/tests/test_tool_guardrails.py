from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent.agent import Agent  # noqa: E402
from mini_smolagent.contracts import ModelResponse, ToolCall  # noqa: E402
from mini_smolagent.memory import AgentMemory  # noqa: E402
from mini_smolagent.run_state import RunState, build_run_result  # noqa: E402
from mini_smolagent.run_steps import execute_tool_call, prepare_turn_input  # noqa: E402
from mini_smolagent.tools import function_tool  # noqa: E402
from mini_smolagent.tools import ToolRegistry  # noqa: E402
from mini_smolagent.run_context import RunContextWrapper  # noqa: E402
from mini_smolagent.tool_guardrails import (  # noqa: E402
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
    tool_input_guardrail,
    tool_output_guardrail,
)


class ToolGuardrailsTestCase(unittest.TestCase):
    def test_tool_input_guardrail_wraps_function_and_result(self) -> None:
        @tool_input_guardrail(name="block_delete")
        def block_delete(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.reject_content(
                "delete is blocked",
                output_info={"tool": tool_call.tool_name},
            )

        context = RunContextWrapper()
        tool_call = ToolCall("delete_file", {"path": "notes.txt"}, "call_1")

        result = block_delete.run(context, object(), tool_call)

        self.assertEqual(result.guardrail_name, "block_delete")
        self.assertIs(result.tool_call, tool_call)
        self.assertEqual(result.output.behavior, "reject_content")
        self.assertEqual(result.output.message, "delete is blocked")

    def test_tool_output_guardrail_defaults_to_function_name(self) -> None:
        @tool_output_guardrail
        def redact_output(context, agent, tool_call, output):
            return ToolGuardrailFunctionOutput.allow(
                output_info={"checked_output": output}
            )

        result = redact_output.run(
            RunContextWrapper(),
            object(),
            ToolCall("lookup", {}, "call_2"),
            "safe result",
        )

        self.assertEqual(result.guardrail_name, "redact_output")
        self.assertEqual(result.tool_output, "safe result")
        self.assertEqual(result.output.behavior, "allow")

    def test_function_tool_can_carry_tool_guardrails(self) -> None:
        @tool_input_guardrail
        def check_input(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.allow()

        @tool_output_guardrail
        def check_output(context, agent, tool_call, output):
            return ToolGuardrailFunctionOutput.allow(output_info=output)

        @function_tool(
            tool_input_guardrails=[check_input],
            tool_output_guardrails=[check_output],
        )
        def lookup(query: str) -> str:
            """Look up a value."""
            return query

        self.assertEqual(lookup.tool_input_guardrails, [check_input])
        self.assertEqual(lookup.tool_output_guardrails, [check_output])

    def test_tool_guardrail_api_is_public(self) -> None:
        import mini_smolagent

        self.assertIs(mini_smolagent.tool_input_guardrail, tool_input_guardrail)
        self.assertIs(mini_smolagent.tool_output_guardrail, tool_output_guardrail)

    def test_execute_tool_call_rejects_input_before_handler(self) -> None:
        class RecordingModel:
            def __init__(self) -> None:
                self.tool_outputs = []

            def record_tool_output(self, action, output) -> None:
                self.tool_outputs.append((action, output))

        called_with = []

        @tool_input_guardrail(name="block_delete")
        def block_delete(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.reject_content(
                "delete is blocked",
                output_info={"path": tool_call.arguments["path"]},
            )

        @function_tool(tool_input_guardrails=[block_delete])
        def delete_file(path: str) -> str:
            """Delete a file."""
            called_with.append(path)
            return "deleted"

        registry = ToolRegistry()
        registry.register(delete_file)
        model = RecordingModel()
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
        )
        run_state = RunState()
        action = ToolCall("delete_file", {"path": "notes.txt"}, "call_1")

        outcome = execute_tool_call(
            agent,
            action,
            run_state,
            step_number=1,
            tool_use_behavior="run_llm_again",
        )

        self.assertEqual(called_with, [])
        self.assertEqual(outcome.result_value, "delete is blocked")
        self.assertFalse(outcome.should_stop)
        self.assertEqual(
            [item.item_type for item in run_state.new_items],
            ["tool_input_guardrail", "tool_result"],
        )
        self.assertEqual(
            run_state.new_items[-1].metadata,
            {
                "observation": "delete is blocked",
                "rejected_by": "block_delete",
                "guardrail_stage": "input",
                "guardrail_name": "block_delete",
                "guardrail_behavior": "reject_content",
            },
        )
        self.assertEqual(model.tool_outputs[0], (action, "delete is blocked"))
        run_result = build_run_result(run_state)
        self.assertEqual(
            run_result.tool_input_guardrail_results[0].guardrail_name,
            "block_delete",
        )

    def test_execute_tool_call_rejects_output_after_handler(self) -> None:
        class RecordingModel:
            def __init__(self) -> None:
                self.tool_outputs = []

            def record_tool_output(self, action, output) -> None:
                self.tool_outputs.append((action, output))

        called_with = []

        @tool_output_guardrail(name="redact_secret")
        def redact_secret(context, agent, tool_call, output):
            return ToolGuardrailFunctionOutput.reject_content(
                "secret output was redacted",
                output_info={"original": output},
            )

        @function_tool(tool_output_guardrails=[redact_secret])
        def lookup_secret(name: str) -> str:
            """Look up a secret."""
            called_with.append(name)
            return "secret-token-123"

        registry = ToolRegistry()
        registry.register(lookup_secret)
        model = RecordingModel()
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            tool_registry=registry,
        )
        run_state = RunState()
        action = ToolCall("lookup_secret", {"name": "api"}, "call_2")

        outcome = execute_tool_call(
            agent,
            action,
            run_state,
            step_number=1,
            tool_use_behavior="run_llm_again",
        )

        self.assertEqual(called_with, ["api"])
        self.assertEqual(outcome.result_value, "secret output was redacted")
        self.assertEqual(
            [item.item_type for item in run_state.new_items],
            ["tool_output_guardrail", "tool_result"],
        )
        self.assertEqual(
            run_state.new_items[-1].metadata,
            {
                "observation": "secret output was redacted",
                "rejected_by": "redact_secret",
                "guardrail_stage": "output",
                "guardrail_name": "redact_secret",
                "guardrail_behavior": "reject_content",
            },
        )
        self.assertEqual(model.tool_outputs[0], (action, "secret output was redacted"))
        run_result = build_run_result(run_state)
        self.assertEqual(
            run_result.tool_output_guardrail_results[0].guardrail_name,
            "redact_secret",
        )

    def test_prepare_turn_input_filters_disabled_function_tools(self) -> None:
        @function_tool(
            is_enabled=lambda context, agent: bool(context.context["allow_secret"])
        )
        def lookup_secret(name: str) -> str:
            """Look up a secret."""
            return name

        registry = ToolRegistry()
        registry.register(lookup_secret)
        agent = Agent(
            memory=AgentMemory(),
            model=object(),
            tool_registry=registry,
        )
        run_state = RunState()

        run_state.context_wrapper.context = {"allow_secret": False}
        turn_input = prepare_turn_input(agent, run_state.context_wrapper)
        self.assertNotIn("lookup_secret", [spec.name for spec in turn_input.tool_specs])

        run_state.context_wrapper.context = {"allow_secret": True}
        turn_input = prepare_turn_input(agent, run_state.context_wrapper)
        self.assertIn("lookup_secret", [spec.name for spec in turn_input.tool_specs])

    def test_input_guardrail_raise_exception_uses_tripwire_exception(self) -> None:
        called_with = []

        @tool_input_guardrail(name="hard_stop")
        def hard_stop(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.raise_exception(
                output_info={"tool": tool_call.tool_name}
            )

        @function_tool(tool_input_guardrails=[hard_stop])
        def delete_file(path: str) -> str:
            """Delete a file."""
            called_with.append(path)
            return "deleted"

        registry = ToolRegistry()
        registry.register(delete_file)
        agent = Agent(memory=AgentMemory(), model=object(), tool_registry=registry)
        run_state = RunState()
        action = ToolCall("delete_file", {"path": "notes.txt"}, "call_3")

        with self.assertRaises(ToolInputGuardrailTripwireTriggered) as raised:
            execute_tool_call(agent, action, run_state, 1, "run_llm_again")

        self.assertEqual(called_with, [])
        self.assertEqual(raised.exception.guardrail_result.guardrail_name, "hard_stop")
        self.assertEqual(run_state.tool_input_guardrail_results[0].guardrail_name, "hard_stop")

    def test_output_guardrail_raise_exception_uses_tripwire_exception(self) -> None:
        called_with = []

        @tool_output_guardrail(name="hard_output_stop")
        def hard_output_stop(context, agent, tool_call, output):
            return ToolGuardrailFunctionOutput.raise_exception(output_info={"output": output})

        @function_tool(tool_output_guardrails=[hard_output_stop])
        def lookup_secret(name: str) -> str:
            """Look up a secret."""
            called_with.append(name)
            return "secret-token-123"

        registry = ToolRegistry()
        registry.register(lookup_secret)
        agent = Agent(memory=AgentMemory(), model=object(), tool_registry=registry)
        run_state = RunState()
        action = ToolCall("lookup_secret", {"name": "api"}, "call_4")

        with self.assertRaises(ToolOutputGuardrailTripwireTriggered) as raised:
            execute_tool_call(agent, action, run_state, 1, "run_llm_again")

        self.assertEqual(called_with, ["api"])
        self.assertEqual(
            raised.exception.guardrail_result.guardrail_name,
            "hard_output_stop",
        )
        self.assertEqual(
            run_state.tool_output_guardrail_results[0].guardrail_name,
            "hard_output_stop",
        )

    def test_agent_run_propagates_tool_guardrail_tripwire(self) -> None:
        class ToolCallingModel:
            def get_response(self, messages, tool_specs):
                return ModelResponse(
                    response_id=None,
                    output=[],
                    output_text=None,
                    tool_calls=[
                        ToolCall("delete_file", {"path": "notes.txt"}, "call_5"),
                    ],
                )

        @tool_input_guardrail(name="hard_stop")
        def hard_stop(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.raise_exception()

        @function_tool(tool_input_guardrails=[hard_stop])
        def delete_file(path: str) -> str:
            """Delete a file."""
            return "deleted"

        registry = ToolRegistry()
        registry.register(delete_file)
        agent = Agent(
            memory=AgentMemory(),
            model=ToolCallingModel(),
            tool_registry=registry,
        )

        with self.assertRaises(ToolInputGuardrailTripwireTriggered):
            agent.run("delete notes.txt")

    def test_disabled_input_guardrail_is_skipped(self) -> None:
        called_with = []

        @tool_input_guardrail(
            name="block_tmp",
            is_enabled=lambda context, agent, tool_call: tool_call.arguments["path"].endswith(".tmp"),
        )
        def block_tmp(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.reject_content("tmp file is blocked")

        @function_tool(tool_input_guardrails=[block_tmp])
        def delete_file(path: str) -> str:
            """Delete a file."""
            called_with.append(path)
            return "deleted"

        registry = ToolRegistry()
        registry.register(delete_file)
        agent = Agent(memory=AgentMemory(), model=object(), tool_registry=registry)
        run_state = RunState()
        action = ToolCall("delete_file", {"path": "notes.txt"}, "call_6")

        outcome = execute_tool_call(agent, action, run_state, 1, "run_llm_again")

        self.assertEqual(called_with, ["notes.txt"])
        self.assertEqual(outcome.result_value, "deleted")
        self.assertEqual(run_state.tool_input_guardrail_results, [])

    def test_disabled_output_guardrail_is_skipped(self) -> None:
        @tool_output_guardrail(name="redact_secret", is_enabled=lambda context, agent: False)
        def redact_secret(context, agent, tool_call, output):
            return ToolGuardrailFunctionOutput.reject_content("secret output was redacted")

        @function_tool(tool_output_guardrails=[redact_secret])
        def lookup_secret(name: str) -> str:
            """Look up a secret."""
            return "secret-token-123"

        registry = ToolRegistry()
        registry.register(lookup_secret)
        agent = Agent(memory=AgentMemory(), model=object(), tool_registry=registry)
        run_state = RunState()
        action = ToolCall("lookup_secret", {"name": "api"}, "call_7")

        outcome = execute_tool_call(agent, action, run_state, 1, "run_llm_again")

        self.assertEqual(outcome.result_value, "secret-token-123")
        self.assertEqual(run_state.tool_output_guardrail_results, [])


if __name__ == "__main__":
    unittest.main()
