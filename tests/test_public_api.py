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
from agents.turn_resolution import (  # noqa: E402
    NextStepFinalOutput as TurnResolutionNextStepFinalOutput,
    ProcessedResponse as TurnResolutionProcessedResponse,
    SingleStepResult as TurnResolutionSingleStepResult,
)


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

    def test_task_graph_core_api_keeps_store_internals_private(self) -> None:
        from agents import (
            TASK_EVENT_SCHEMA_VERSION,
            InvalidTaskTransition,
            TaskAlreadyExistsError,
            TaskConflictError,
            TaskDependencyCycle,
            TaskDependencyNotReady,
            TaskEvent,
            TaskEventType,
            TaskLimitExceeded,
            TaskNode,
            TaskNotFoundError,
            TaskSpec,
            TaskStateError,
            TaskStatus,
            TaskStore,
            replay_task_events,
            task_event_from_dict,
            task_event_to_dict,
            task_node_from_dict,
            task_node_to_dict,
        )

        public_values = (
            InvalidTaskTransition,
            TaskAlreadyExistsError,
            TaskConflictError,
            TaskDependencyCycle,
            TaskDependencyNotReady,
            TaskEvent,
            TaskEventType,
            TaskLimitExceeded,
            TaskNode,
            TaskNotFoundError,
            TaskSpec,
            TaskStateError,
            TaskStatus,
            TaskStore,
            replay_task_events,
            task_event_from_dict,
            task_event_to_dict,
            task_node_from_dict,
            task_node_to_dict,
        )
        private_names = {
            "_ALLOWED_TRANSITIONS",
            "_UNSET",
            "_apply_event_batch",
            "_commit_nodes",
            "_dependency_path",
            "_events",
            "_lock",
            "_nodes",
            "_replace_node",
            "_reduce_task_event",
            "_seen_events",
            "_stage_cancelled_descendants",
            "_stage_dependency_blocks",
            "_stage_node",
            "_task_event_from_change",
            "transition_task_node",
        }

        public_names = {value.__name__ for value in public_values}
        public_names.add("TASK_EVENT_SCHEMA_VERSION")
        self.assertTrue(public_names.issubset(set(agents.__all__)))
        self.assertTrue(all(getattr(agents, name) for name in public_names))
        self.assertTrue(private_names.isdisjoint(set(agents.__all__)))
        self.assertFalse(any(hasattr(agents, name) for name in private_names))
        self.assertEqual(TASK_EVENT_SCHEMA_VERSION, 1)
        store = TaskStore()
        node = store.create(TaskSpec(summary="Public task graph"))
        self.assertIs(node.status, TaskStatus.PENDING)

    def test_planning_tools_expose_only_the_factory(self) -> None:
        from agents import create_task_tools
        from agents.task_tools import create_task_tools as module_factory

        private_names = {
            "_is_in_root_scope",
            "_require_owned_task",
            "_TaskStatusInput",
            "_RESULT_UNSET",
            "create_task",
            "update_task",
            "list_tasks",
        }

        self.assertIs(create_task_tools, module_factory)
        self.assertIn("create_task_tools", agents.__all__)
        self.assertTrue(private_names.isdisjoint(set(agents.__all__)))
        self.assertFalse(any(hasattr(agents, name) for name in private_names))

    def test_plan_core_exposes_only_the_stable_surface(self) -> None:
        planning = importlib.import_module("agents.planning")
        public_names = {
            "PLAN_DEFINITION_SCHEMA_VERSION",
            "PlanDefinition",
            "PlanExecution",
            "PlanStepSpec",
            "PlanValidationError",
            "build_plan_step_agent",
            "build_planning_agent",
            "plan_definition_from_dict",
            "plan_definition_to_dict",
            "render_plan_markdown",
        }
        private_names = {
            "_PLAN_STEP_AGENT_INSTRUCTIONS",
            "_PLANNING_AGENT_INSTRUCTIONS",
            "_require_fields",
            "_topological_steps",
            "_validate_execution_inputs",
            "_validate_execution_store",
        }

        self.assertEqual(set(planning.__all__), public_names)
        self.assertTrue(public_names.issubset(set(agents.__all__)))
        for public_name in public_names:
            with self.subTest(public_name=public_name):
                self.assertIs(
                    getattr(agents, public_name),
                    getattr(planning, public_name),
                )
        self.assertTrue(private_names.isdisjoint(set(planning.__all__)))
        self.assertTrue(private_names.isdisjoint(set(agents.__all__)))
        self.assertFalse(any(hasattr(agents, name) for name in private_names))

    def test_chat_backend_exposes_planning_service_without_execution_internals(
        self,
    ) -> None:
        chat_backend = importlib.import_module("agents.chat_backend")
        planning_service = importlib.import_module(
            "agents.chat_backend.planning_service"
        )
        public_names = {
            "PlanningApprovalSnapshot",
            "PlanningDraftOutcome",
            "PlanningInputError",
            "PlanningProgressEvent",
            "PlanningRecoveryReport",
            "PlanningRunError",
            "PlanningService",
            "PlanningSnapshot",
            "PlanningStepSnapshot",
        }
        private_names = {
            "PLANNING_DRAFT_SCHEMA",
            "_cancel_unfinished_tasks",
            "_claim_task",
            "_drive_running",
            "_safe_run_error",
        }

        self.assertEqual(set(planning_service.__all__), public_names)
        self.assertTrue(public_names.issubset(set(chat_backend.__all__)))
        for public_name in public_names:
            with self.subTest(public_name=public_name):
                self.assertIs(
                    getattr(chat_backend, public_name),
                    getattr(planning_service, public_name),
                )
                self.assertNotIn(public_name, agents.__all__)
        self.assertTrue(private_names.isdisjoint(set(chat_backend.__all__)))
        self.assertFalse(any(hasattr(chat_backend, name) for name in private_names))

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

    def test_run_control_api_keeps_runtime_internals_private(self) -> None:
        public_names = {
            "CancellationToken",
            "RunController",
            "RunStream",
            "SteerMessage",
        }
        internal_names = {
            "_CLOSE_SENTINEL",
            "_EventChannel",
            "_LoopEventBridge",
            "_RunRuntime",
            "_create_run_runtime",
        }

        self.assertTrue(public_names.issubset(set(agents.__all__)))
        self.assertTrue(internal_names.isdisjoint(set(agents.__all__)))
        self.assertFalse(any(hasattr(agents, name) for name in internal_names))
        for public_name in public_names:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))

    def test_run_event_api_is_public_from_package_root(self) -> None:
        public_names = {
            "ApprovalRequiredEvent",
            "ApprovalResolvedEvent",
            "ModelAttemptStartedEvent",
            "ModelCompletedEvent",
            "ModelFallbackEvent",
            "ModelRetryScheduledEvent",
            "ModelStartedEvent",
            "RunAlreadyFinishedError",
            "RunCancelledError",
            "RunCancelledEvent",
            "RunCompletedEvent",
            "RunErrorEvent",
            "RunEvent",
            "RunEventType",
            "RunFailedEvent",
            "RunStartedEvent",
            "RunStoppedEvent",
            "SteerAcceptedEvent",
            "SteerAppliedEvent",
            "TerminalRunEvent",
            "ToolCompletedEvent",
            "ToolFailedEvent",
            "ToolRequestedEvent",
            "ToolStartedEvent",
            "TurnCompletedEvent",
            "TurnStartedEvent",
            "event_to_dict",
            "is_terminal_event",
        }

        self.assertTrue(public_names.issubset(set(agents.__all__)))
        self.assertTrue(all(hasattr(agents, name) for name in public_names))

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
        self.assertIs(agents.ProcessedResponse, TurnResolutionProcessedResponse)
        self.assertIs(agents.SingleStepResult, TurnResolutionSingleStepResult)
        self.assertIs(agents.NextStepFinalOutput, TurnResolutionNextStepFinalOutput)
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

    def test_package_root_imports_planner_api_from_split_module(self) -> None:
        package_source = Path(agents.__file__).read_text(encoding="utf-8")

        self.assertIn(
            "from .tool_planning import ToolExecutionPlan, build_tool_execution_plan",
            package_source,
        )

    def test_edit_tool_api_is_public_from_package_root(self) -> None:
        self.assertIn("create_apply_patch_tool", agents.__all__)
        self.assertTrue(hasattr(agents, "create_apply_patch_tool"))

    def test_coding_cli_api_is_public_from_package_root(self) -> None:
        expected_coding_cli_api = {
            "CodingCliConfig",
            "build_coding_cli_setup",
            "parse_coding_cli_args",
            "run_coding_agent_cli",
        }

        self.assertTrue(expected_coding_cli_api.issubset(set(agents.__all__)))
        for public_name in expected_coding_cli_api:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))

    def test_trajectory_api_is_public_from_package_root(self) -> None:
        expected_trajectory_api = {
            "TrajectoryEvent",
            "trajectory_events_from_result",
            "write_trajectory_jsonl",
        }

        self.assertTrue(expected_trajectory_api.issubset(set(agents.__all__)))
        for public_name in expected_trajectory_api:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))

    def test_workspace_manifest_api_is_public_from_package_root(self) -> None:
        self.assertIn("WorkspaceManifest", agents.__all__)
        self.assertTrue(hasattr(agents, "WorkspaceManifest"))

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

    def test_model_stream_contract_api_is_public_without_accumulator_state(self) -> None:
        public_names = {
            "ModelRequest",
            "ModelEvent",
            "ResponseCreated",
            "TextDelta",
            "TextDone",
            "ReasoningDelta",
            "ReasoningDone",
            "RefusalDelta",
            "RefusalDone",
            "ToolCallStarted",
            "ToolArgumentsDelta",
            "ToolArgumentsDone",
            "UsageUpdated",
            "ResponseCompleted",
            "ResponseFailed",
            "ResponseIncomplete",
            "StreamingModelAdapter",
            "supports_streaming_model",
            "ModelErrorKind",
            "ModelProtocolError",
        }
        private_names = {
            "ModelEventAccumulator",
            "ModelEventDiagnostics",
            "_ContentBuffer",
            "_PendingToolCall",
        }

        self.assertTrue(public_names.issubset(set(agents.__all__)))
        for public_name in public_names:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))
        self.assertTrue(private_names.isdisjoint(set(agents.__all__)))

    def test_runtime_model_observability_api_keeps_helpers_private(self) -> None:
        public_names = {
            "Agent",
            "Runner",
            "RunStream",
            "SyncRunCollector",
            "RunEvent",
            "ModelAttemptStartedEvent",
            "ModelRetryScheduledEvent",
            "ModelFallbackEvent",
            "ModelRequest",
            "ModelEvent",
            "StreamingModelAdapter",
            "RetryAction",
            "RetryDecision",
            "RetryPolicy",
            "ModelAttemptNotice",
            "ModelAttemptRecord",
            "ModelCancelledError",
            "ModelExecution",
            "ModelExecutionError",
            "run_model_request",
            "RunResult",
            "RunStateSnapshot",
        }
        private_names = {
            "ModelEventAccumulator",
            "_OpenAIEventMapper",
            "RetryDelayDecision",
            "compute_retry_delay",
            "parse_retry_after",
            "_attempt_id",
            "_await_with_limits",
            "_consume_stream_attempt",
            "_sleep_with_cancel",
            "stream_model_response",
            "_emit_model_attempt_notice",
            "_LoopEventBridge",
            "_RunRuntime",
            "_EventChannel",
            "_chat_config",
            "_effective_run_config",
            "_resolve_run_config",
            "_resolve_task_binding",
            "_validate_task_link",
            "_wait_result",
        }

        self.assertTrue(public_names.issubset(set(agents.__all__)))
        for public_name in public_names:
            with self.subTest(public_name=public_name):
                self.assertTrue(hasattr(agents, public_name))
        self.assertTrue(private_names.isdisjoint(set(agents.__all__)))
        self.assertFalse(any(hasattr(agents, name) for name in private_names))

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
        self.assertIs(
            get_type_hints(agents.Runner.run_stream, globalns=runner_globals)["return"],
            agents.RunStream,
        )
        self.assertIs(
            get_type_hints(agents.Runner.run_async, globalns=runner_globals)["return"],
            agents.RunResult,
        )
        self.assertIs(get_type_hints(agents.Agent.run)["return"], agents.RunResult)
        self.assertIs(
            get_type_hints(agents.Agent.run_stream)["return"],
            agents.RunStream,
        )
        self.assertIs(
            get_type_hints(agents.Agent.run_async)["return"],
            agents.RunResult,
        )
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
