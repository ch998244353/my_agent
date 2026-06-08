from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from typing import get_type_hints


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents  # noqa: E402
from agents.run_loop import _run_agent_loop_impl, run_agent_loop  # noqa: E402


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
            "RunResult",
            "RunResultBase",
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
            "agents.result",
            "agents.run_state",
            "agents.run_steps",
            "agents.runner",
            "agents.tools",
        ]

        for module_name in module_names:
            with self.subTest(module_name=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))

    def test_run_result_api_is_public_with_legacy_result_available(self) -> None:
        self.assertTrue(hasattr(agents, "AgentRunResult"))
        self.assertTrue(hasattr(agents, "RunResult"))
        self.assertTrue(hasattr(agents, "RunResultBase"))
        self.assertTrue(issubclass(agents.RunResult, agents.RunResultBase))

    def test_run_step_state_machine_api_is_public(self) -> None:
        expected_run_step_api = {
            "ProcessedResponse",
            "SingleStepResult",
            "NextStepFinalOutput",
            "NextStepRunAgain",
            "NextStepHandoff",
            "NextStepStopped",
            "MODEL_RETURNED_NO_TOOL_CALL",
        }

        self.assertTrue(expected_run_step_api.issubset(set(agents.__all__)))
        self.assertEqual(agents.NextStepRunAgain().reason, "tool_results")
        self.assertEqual(
            agents.MODEL_RETURNED_NO_TOOL_CALL,
            "model_returned_no_tool_call",
        )

    def test_tool_runtime_v2_api_is_public_from_package_root(self) -> None:
        expected_tool_runtime_api = {
            "ToolApprovalDecision",
            "ToolApprovalRequest",
            "ToolExecutionLimits",
            "ToolExecutionPlan",
            "ToolExecutionReport",
            "ToolTimeoutBehavior",
            "ToolTimeoutError",
            "build_tool_execution_plan",
            "format_tool_observation",
            "run_with_timeout",
        }

        self.assertTrue(expected_tool_runtime_api.issubset(set(agents.__all__)))
        for public_name in expected_tool_runtime_api:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))

        request = agents.ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "notes.txt"},
        )
        limits = agents.ToolExecutionLimits(timeout_seconds=1.0)
        report = agents.ToolExecutionReport(
            tool_name=request.tool_name,
            call_id=request.call_id,
            success=False,
            reason="tool_approval_required",
        )

        self.assertEqual(request.call_id, "call_1")
        self.assertEqual(limits.timeout_seconds, 1.0)
        self.assertEqual(report.to_metadata()["reason"], "tool_approval_required")

    def test_edit_tool_api_is_public_from_package_root(self) -> None:
        self.assertIn("create_apply_patch_tool", agents.__all__)
        self.assertTrue(hasattr(agents, "create_apply_patch_tool"))

    def test_model_hardening_api_is_public_without_private_helpers(self) -> None:
        expected_model_api = {
            "ModelAdapter",
            "ModelCallError",
            "ModelResponseError",
            "ModelResponseParseError",
            "ModelResponseStatusError",
            "ModelSettings",
            "OpenAIResponsesModel",
            "StructuredOutputRefusalError",
            "supports_model_adapter",
        }
        private_model_helpers = {
            "parse_tool_call_arguments",
            "response_item_to_tool_call",
            "response_output_text",
            "response_refusal_text",
            "tool_spec_to_openai_tool",
            "validate_response_status",
        }

        self.assertTrue(expected_model_api.issubset(set(agents.__all__)))
        for public_name in expected_model_api:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))
        self.assertTrue(private_model_helpers.isdisjoint(set(agents.__all__)))

    def test_run_entrypoints_are_annotated_with_run_result(self) -> None:
        runner_globals = {
            **agents.Runner.run_sync.__globals__,
            "Agent": agents.Agent,
        }
        loop_globals = {
            **run_agent_loop.__globals__,
            "Agent": agents.Agent,
        }

        self.assertIs(
            get_type_hints(agents.Runner.run_sync, globalns=runner_globals)["return"],
            agents.RunResult,
        )
        self.assertIs(get_type_hints(agents.Agent.run)["return"], agents.RunResult)
        self.assertIs(get_type_hints(agents.Agent._run)["return"], agents.RunResult)
        self.assertIs(
            get_type_hints(run_agent_loop, globalns=loop_globals)["return"],
            agents.RunResult,
        )
        self.assertIs(
            get_type_hints(_run_agent_loop_impl, globalns=loop_globals)["return"],
            agents.RunResult,
        )


if __name__ == "__main__":
    unittest.main()
