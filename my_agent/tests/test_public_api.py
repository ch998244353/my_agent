from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import mini_smolagent  # noqa: E402


class PublicApiTestCase(unittest.TestCase):
    def test_all_exports_are_available_from_package_root(self) -> None:
        for public_name in mini_smolagent.__all__:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(mini_smolagent, public_name))

    def test_first_round_core_api_is_public(self) -> None:
        expected_public_api = {
            "Agent",
            "AgentCapabilities",
            "AgentMemory",
            "AgentSession",
            "AgentRunResult",
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
            "function_tool",
            "input_guardrail",
            "output_guardrail",
            "output_schema_from_output_type",
            "parse_structured_output",
        }

        self.assertTrue(expected_public_api.issubset(set(mini_smolagent.__all__)))
        self.assertNotIn("AgentStepTrace", mini_smolagent.__all__)

    def test_current_core_modules_are_importable(self) -> None:
        module_names = [
            "mini_smolagent.agent",
            "mini_smolagent.agents",
            "mini_smolagent.contracts",
            "mini_smolagent.guardrails",
            "mini_smolagent.handoffs",
            "mini_smolagent.lifecycle",
            "mini_smolagent.memory",
            "mini_smolagent.models",
            "mini_smolagent.output",
            "mini_smolagent.python_executor",
            "mini_smolagent.run_config",
            "mini_smolagent.run_context",
            "mini_smolagent.run_loop",
            "mini_smolagent.run_state",
            "mini_smolagent.run_steps",
            "mini_smolagent.runner",
            "mini_smolagent.tools",
        ]

        for module_name in module_names:
            with self.subTest(module_name=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
