from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents  # noqa: E402


class PublicApiTestCase(unittest.TestCase):
    def test_all_exports_are_available_from_package_root(self) -> None:
        for public_name in agents.__all__:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))

    def test_first_round_core_api_is_public(self) -> None:
        expected_public_api = {
            "Agent",
            "AgentCapabilities",
            "AgentMemory",
            "AgentSession",
            "AgentRunResult",
            "AgentToolError",
            "ChatMessage",
            "FunctionTool",
            "GuardrailFunctionOutput",
            "InputGuardrail",
            "InputGuardrailResult",
            "LifecycleHooks",
            "MiniCodeAgent",
            "MiniPythonExecutor",
            "ModelResponse",
            "OpenAIResponsesModel",
            "OutputGuardrail",
            "OutputGuardrailResult",
            "ModelSettings",
            "RunItem",
            "RunConfig",
            "RunContextWrapper",
            "RunState",
            "Runner",
            "StepRecord",
            "StructuredOutputError",
            "ToolArgument",
            "ToolCall",
            "ToolExecutionError",
            "ToolRegistry",
            "ToolSpec",
            "create_agent_tool",
            "function_tool",
            "input_guardrail",
            "output_guardrail",
            "output_schema_from_output_type",
            "parse_structured_output",
        }

        self.assertTrue(expected_public_api.issubset(set(agents.__all__)))
        self.assertNotIn("AgentStepTrace", agents.__all__)

    def test_tracing_api_is_public_from_package_root(self) -> None:
        expected_tracing_api = {
            "BatchTraceProcessor",
            "DebugTracingProcessor",
            "ExportingTracingProcessor",
            "InMemoryTracingExporter",
            "InMemoryTracingProcessor",
            "JSONLTracingExporter",
            "Span",
            "SpanData",
            "SpanRecord",
            "SynchronousMultiTracingProcessor",
            "Trace",
            "TraceRecord",
            "TracingExporter",
            "TracingProcessor",
            "add_trace_processor",
            "agent_span",
            "custom_span",
            "gen_span_id",
            "gen_trace_id",
            "get_current_span",
            "get_current_trace",
            "get_trace_processors",
            "guardrail_span",
            "handoff_span",
            "model_span",
            "set_trace_processors",
            "span",
            "tool_span",
            "trace",
        }

        self.assertTrue(expected_tracing_api.issubset(set(agents.__all__)))
        for public_name in expected_tracing_api:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))

    def test_current_core_modules_are_importable(self) -> None:
        module_names = [
            "agents.agent",
            "agents.agent_tools",
            "agents.agents",
            "agents.contracts",
            "agents.guardrails",
            "agents.handoffs",
            "agents.lifecycle",
            "agents.memory",
            "agents.models",
            "agents.output",
            "agents.python_executor",
            "agents.run_config",
            "agents.run_context",
            "agents.run_loop",
            "agents.run_state",
            "agents.run_steps",
            "agents.runner",
            "agents.tools",
        ]

        for module_name in module_names:
            with self.subTest(module_name=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
