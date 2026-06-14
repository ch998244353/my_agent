# Symbol Map

## High-Priority Runtime Symbols

| Symbol | Kind | File | Why it matters |
|---|---|---|---|
| `Agent` | class | `src/agents/agent.py` | Runtime owner for memory, model, tools, handoffs, hooks, guardrails, output type, and run delegation. |
| `AgentCapabilities` | dataclass | `src/agents/agent.py` | Enables default final-answer tool and python execution tool registration. |
| `Runner.run_sync` | method | `src/agents/runner.py` | Thin public sync entry that calls `run_agent_loop`. |
| `run_agent_loop` | function | `src/agents/run_loop.py` | Opens trace/task/agent spans and starts the runtime state machine. |
| `_run_agent_loop_impl` | function | `src/agents/run_loop.py` | Main orchestration body for guardrails, model turns, tools, handoffs, verification, and result building. |
| `resume_agent_loop` | function | `src/agents/run_loop.py` | Re-enters `_run_agent_loop_impl` with existing `RunState`. |
| `prepare_turn_input` | function | `src/agents/model_turn.py` | Converts agent memory/context/tools/settings into `TurnInput`. |
| `run_model_turn` | function | `src/agents/model_turn.py` | Traced model call wrapper with lifecycle and error wrapping. |
| `process_model_turn` | function | `src/agents/turn_resolution.py` | Classifies model output into tool calls, handoff calls, and possible final output. |
| `build_tool_execution_plan` | function | `src/agents/tool_planning.py` | Decides action/handoff/approval batches for a model turn. |
| `execute_tool_call` | function | `src/agents/tool_execution.py` | Traced tool execution wrapper. |
| `_execute_tool_call_impl` | function | `src/agents/tool_execution.py` | Actual tool enablement, approval, guardrail, handler, recording, and final-output logic. |
| `execute_handoff` | function | `src/agents/run_steps.py` | Runs target agents via handoff tool calls. |
| `build_run_result` | function | `src/agents/run_state.py` | Converts mutable `RunState` into frozen `RunResult`. |

## Coding CLI Symbols

| Symbol | File | Notes |
|---|---|---|
| `CodingCliConfig` | `src/agents/coding_cli.py` | Frozen snapshot of CLI task, workspace, profile, model, limits, and optional session path. |
| `parse_coding_cli_args` | `src/agents/coding_cli.py` | Converts `argparse` values into `CodingCliConfig` and validates profile/positive limits. |
| `build_coding_cli_setup` | `src/agents/coding_cli.py` | Converts config into `CodingAgentSetup` by selecting a profile, model, `WorkspaceManifest`, and optional `JsonSession`. |
| `run_coding_agent_cli` | `src/agents/coding_cli.py` | Runs one local coding task, optionally writes trajectory JSONL, and maps final output, pending approvals, and errors to process exit codes. |
| `build_example_command` | `examples/local_coding_cli.py` | Builds the recommended `python -m agents.coding_cli` command without calling a real model. |

## Scheme A Coding-Agent Symbols

| Symbol | File | Notes |
|---|---|---|
| `WorkspaceManifest` | `src/agents/workspace_manifest.py` | User-facing workspace/test-command/env policy that builds runtime `Workspace` objects and exposes JSON-safe metadata. |
| `CONTEXT_WORKSPACE_MANIFEST_KEY` | `src/agents/run_context.py` | Context key used by `build_coding_agent` to store the manifest alongside workspace and selected files. |
| `ToolObservation` | `src/agents/tool_observations.py` | Frozen structured observation with stable text and JSON-safe dict rendering. |
| `command_result_observation` | `src/agents/tool_observations.py` | Maps `CommandResult` into status/summary/details/output for shell and test tools. |
| `patch_result_observation` | `src/agents/tool_observations.py` | Maps `PatchResult` into dry-run/change/error observation details for the patch tool. |
| `SafetyDecision` | `src/agents/coding_policies.py` | `allow`/`approve`/`block` decision object shared by shell and patch policies. |
| `ShellCommandPolicy` | `src/agents/coding_policies.py` | Classifies shell commands by safe prefixes, approval prefixes, blocked fragments, and unknown commands. |
| `PatchApprovalPolicy` | `src/agents/coding_policies.py` | Allows dry-run validation and requires approval for valid write patches, with delete/large/write categories. |
| `TrajectoryEvent` | `src/agents/trajectory.py` | JSONL event shape for run evidence: event type, run id, step, payload, timestamp. |
| `trajectory_events_from_result` | `src/agents/trajectory.py` | Converts a `RunResult` plus task/workspace metadata into ordered trajectory events. |
| `write_trajectory_jsonl` | `src/agents/trajectory.py` | Writes trajectory events as one JSON object per line, creating parent directories. |
| `pending_approval_summaries` | `src/agents/result.py` | User-facing summary strings for pending approvals, used by the CLI print path. |

## State and Data Contracts

| Symbol | File | Owned data | Producers | Consumers |
|---|---|---|---|---|
| `ChatMessage` | `src/agents/contracts.py` | `role`, `content` | memory/session/model context | model adapters, session replay |
| `ToolArgument` | `src/agents/contracts.py` | argument name, description, JSON schema, required flag | `function_tool`, handoff/workspace tools | tool prompt/rendering, OpenAI conversion |
| `ToolSpec` | `src/agents/contracts.py` | tool name/description/arguments/return label | `FunctionTool`, handoff specs | model adapters and tool registry |
| `ToolCall` | `src/agents/contracts.py` | `tool_name`, `arguments`, `call_id` | model parsing or scripted models | planning, execution, approvals, memory |
| `ToolApprovalRequest` | `src/agents/contracts.py` | pending approval payload | tool planning/execution | `RunResult.pending_approvals`, snapshots |
| `ModelResponse` | `src/agents/contracts.py` | normalized model output, text, tool calls, usage, request metadata | `OpenAIResponsesModel.get_response`, model adapters | turn resolution, result raw responses |
| `RunItem` | `src/agents/contracts.py` | event type, step number, payload, metadata | run recording, model turn, tool execution | result summaries, state serialization, sessions |
| `StepRecord` | `src/agents/contracts.py` | memory-visible messages/tool calls/observation/error | tool execution, model errors, verification | `AgentMemory.to_messages`, compaction |
| `RunState` | `src/agents/run_state.py` | mutable in-run items, limits, final answer, context, approvals, guardrail results | run loop | all run subsystems |
| `RunStateSnapshot` | `src/agents/run_state.py` | serializable run continuation data | `RunResult.to_state` | `RunState.from_snapshot` |
| `RunResult` | `src/agents/result.py` | frozen final run output, items, raw responses, context | `build_run_result` | users, chat, sessions, resume |
| `TrajectoryEvent` | `src/agents/trajectory.py` | JSONL audit event generated from run history | `trajectory_events_from_result` | CLI trajectory writer, eval/debug readers |

## Tool Symbols

| Symbol | File | Notes |
|---|---|---|
| `FunctionTool` | `src/agents/tools.py` | Tool spec plus handler, enablement, guardrails, execution limits, approval policy. |
| `function_tool` | `src/agents/tools.py` | Decorator/factory that reflects Python signature and docstring into `ToolSpec`. |
| `ToolRegistry` | `src/agents/tools.py` | Name-to-tool registry used for specs and execution. |
| `create_final_answer_tool` | `src/agents/tools.py` | Default `final_answer(answer: str)` tool. |
| `ToolExecutionLimits` | `src/agents/tool_runtime.py` | Timeout/output limit policy for tool execution. |
| `ToolApprovalDecision` | `src/agents/tool_runtime.py` | Approval decision plus error details. |
| `requires_tool_approval` | `src/agents/tool_runtime.py` | Evaluates bool/callable approval policy. |
| `ToolExecutionPlan` | `src/agents/tool_planning.py` | Holds actions, tool calls, handoff calls, pending approvals, and approved batch. |
| `ToolExecutionOutcome` | `src/agents/tool_execution.py` | Tool execution return contract for loop resolution. |
| `record_tool_approval_required` | `src/agents/tool_execution.py` | Runtime approval request recorder for execution-time approvals. |
| `record_tool_approval_rejected` | `src/agents/tool_execution.py` | Converts rejected approval into model-visible failed observation. |
| `resume_pending_tool_approvals` | `src/agents/run_resume.py` | Executes approved/rejected pending calls and leaves remaining calls pending. |
| `ToolObservation` | `src/agents/tool_observations.py` | Stable shell/test/patch observation contract for text and dict rendering. |

## Model and Output Symbols

| Symbol | File | Notes |
|---|---|---|
| `ModelAdapter` | `src/agents/models.py` | Protocol for `get_response(messages, tool_specs) -> ModelResponse`. |
| `OpenAIResponsesModel` | `src/agents/models.py` | Concrete Responses-style adapter with pending tool outputs and previous response state. |
| `ResponseStatePolicy` | `src/agents/models.py` | Controls `previous_response_id` versus manual items and store/include behavior. |
| `build_response_request_kwargs` | `src/agents/models.py` | Central OpenAI request builder. |
| `response_items_to_tool_calls` | `src/agents/models.py` | Parses response output items into `ToolCall`. |
| `tool_call_output_to_response_input` | `src/agents/models.py` | Converts tool result to `function_call_output`. |
| `ModelSettings` | `src/agents/model_settings.py` | Per-agent/config model setting merge target. |
| `set_structured_final_answer` | `src/agents/output.py` | Parses model output text into typed final answer when schema is set. |
| `parse_structured_output` | `src/agents/output.py` | Minimal JSON/schema validator for structured output. |

## Memory and Session Symbols

| Symbol | File | Notes |
|---|---|---|
| `AgentMemory` | `src/agents/memory.py` | Per-agent task plus step list rendered to model messages. |
| `SessionTurn` | `src/agents/memory.py` | One session turn with task and steps. |
| `MemorySummary` | `src/agents/memory.py` | Compacted history rendered as a system message. |
| `CompactionPolicy` | `src/agents/memory.py` | Compaction threshold, keep count, and clipping policy. |
| `MemoryCompressor` | `src/agents/memory.py` | Splits, summarizes, trims, and returns compacted memory. |
| `RuleBasedSummarizer` | `src/agents/memory.py` | Local fallback summarizer. |
| `ModelSummarizer` | `src/agents/memory.py` | Optional OpenAI-backed summarizer with fallback. |
| `AgentSession` | `src/agents/memory.py` | In-memory session implementation and `AgentMemory` subclass. |
| `JsonSession` | `src/agents/memory.py` | Disk-backed `SessionLike` wrapper. |
| `SessionLike` | `src/agents/run_config.py` | Protocol consumed by run loop/chat runtime. |

## Workspace and Context Symbols

| Symbol | File | Notes |
|---|---|---|
| `Workspace` | `src/agents/workspace.py` | Root/allowed/ignored path policy. |
| `WorkspaceManifest` | `src/agents/workspace_manifest.py` | Serializable local coding workspace policy and test-command defaults. |
| `WorkspacePathError` | `src/agents/workspace.py` | Raised for outside root, outside allowed paths, ignored paths, not file/dir. |
| `WorkspaceFileEntry` | `src/agents/workspace_inventory.py` | Inventory entry with readable/ignored reason. |
| `WorkspaceInventory` | `src/agents/workspace_inventory.py` | Inventory result with truncation flag. |
| `build_workspace_inventory` | `src/agents/workspace_inventory.py` | Recursively scans readable workspace entries. |
| `WorkspaceCodeReader` | `src/agents/workspace_code.py` | Reads line ranges, searches text, finds files, outlines Python, finds related files. |
| `MentionCandidate` | `src/agents/context_mentions.py` | Detected/resolved path or symbol mention. |
| `detect_file_mentions` | `src/agents/context_mentions.py` | Extracts path/filename/test/symbol-like mentions from task text. |
| `resolve_mentions_against_inventory` | `src/agents/context_mentions.py` | Resolves mentions to inventory paths. |
| `SelectedFile` | `src/agents/selected_files.py` | Path, mode, reason, source for selected context file. |
| `SelectedFilesState` | `src/agents/selected_files.py` | Mutable selected-file set with promotion semantics. |
| `RepoContextSection` | `src/agents/repo_context.py` | Titled prioritized repo context block. |
| `RepoContext` | `src/agents/repo_context.py` | Full repo context plus selected paths, mentioned symbols, truncated flag. |
| `RepoContextBuilder` | `src/agents/repo_context.py` | Assembles inventory, selected, mentioned, and code-match sections. |
| `build_task_repo_context` | `src/agents/repo_context.py` | Run-loop entry for task-derived repo context. |
| `ContextChunk` | `src/agents/context_chunks.py` | Model-message chunk with priority and source. |
| `build_turn_context` | `src/agents/context_chunks.py` | Orders instructions, summary, repo context, memory, selected files. |

## Guardrail, Verification, and Observability Symbols

| Symbol | File | Notes |
|---|---|---|
| `InputGuardrail` | `src/agents/guardrails.py` | Agent input check wrapper. |
| `OutputGuardrail` | `src/agents/guardrails.py` | Agent final output check wrapper. |
| `ToolInputGuardrail` | `src/agents/tool_guardrails.py` | Tool-call pre-execution guardrail wrapper. |
| `ToolOutputGuardrail` | `src/agents/tool_guardrails.py` | Tool-output post-execution guardrail wrapper. |
| `ToolGuardrailFunctionOutput` | `src/agents/tool_guardrails.py` | `allow`, `reject_content`, `raise_exception` behavior contract. |
| `SafetyDecision` | `src/agents/coding_policies.py` | Policy result for shell/edit classification. |
| `ShellCommandPolicy` | `src/agents/coding_policies.py` | Shell allow/approve/block classifier used by coding-agent shell tools. |
| `PatchApprovalPolicy` | `src/agents/coding_policies.py` | Patch write approval classifier used by apply-patch tools. |
| `VerificationPolicy` | `src/agents/verification.py` | Commands, trigger tools, attempts, output limits. |
| `VerificationRunner` | `src/agents/verification.py` | Runs verification policy through `Environment`. |
| `LifecycleHooks` | `src/agents/lifecycle.py` | User callbacks for agent/model/tool/handoff/error events. |
| `Trace` | `src/agents/tracing.py` | Trace context manager and record owner. |
| `Span` | `src/agents/tracing.py` | Span context manager and record owner. |
| `TracingProcessor` | `src/agents/tracing.py` | Processor interface for trace/span lifecycle. |
| `TracingExporter` | `src/agents/tracing.py` | Exporter interface for finished trace/span snapshots. |
| `BatchTraceProcessor` | `src/agents/tracing.py` | Buffered tracing processor. |
| `JSONLTracingExporter` | `src/agents/tracing.py` | File exporter. |

## High-Value Test Anchors

| Area | Tests |
|---|---|
| Public API | `tests/test_public_api.py`, `tests/test_agent_config.py` |
| Coding CLI / Scheme A setup | `tests/test_coding_cli.py`, `tests/test_workspace_manifest.py`, `tests/test_coding_agent_profile.py` |
| Run loop | `tests/test_runner.py`, `tests/test_run_steps.py`, `tests/test_run_state.py` |
| Model/OpenAI adapter | `tests/test_models.py`, `tests/test_output.py`, `tests/test_code_execution.py` |
| Tools/approvals | `tests/test_tools.py`, `tests/test_tool_execution_plan.py`, `tests/test_tool_approval_runtime.py`, `tests/test_tool_approval_pause.py`, `tests/test_coding_policies.py`, `tests/test_tool_observations.py` |
| Guardrails | `tests/test_guardrails.py`, `tests/test_tool_guardrails.py` |
| Workspace/context | `tests/test_workspace.py`, `tests/test_workspace_tools.py`, `tests/test_workspace_inventory.py`, `tests/test_workspace_code.py`, `tests/test_workspace_code_tools.py`, `tests/test_context_mentions.py`, `tests/test_selected_files.py`, `tests/test_repo_context.py`, `tests/test_context_chunks.py` |
| Memory/session/chat | `tests/test_memory.py`, `tests/test_session_memory_example.py`, `tests/test_chat.py` |
| Observability/verification | `tests/test_tracing.py`, `tests/test_lifecycle.py`, `tests/test_verification.py`, `tests/test_verification_loop.py`, `tests/test_trajectory.py` |
