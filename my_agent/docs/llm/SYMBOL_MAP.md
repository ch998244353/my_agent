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
| `RUN_STATE_SNAPSHOT_SCHEMA_VERSION` | constant | `src/agents/run_state.py` | Version marker for the local JSON snapshot dict shape. |
| `run_state_snapshot_to_dict` | function | `src/agents/run_state.py` | Converts `RunStateSnapshot` into a schema-versioned JSON-safe dict. |
| `run_state_snapshot_from_dict` | function | `src/agents/run_state.py` | Validates snapshot schema version and restores a `RunStateSnapshot` from mapping data. |
| `_json_safe_value` | function | `src/agents/result.py` | Normalizes model/run item payloads so `RunResult.to_state()` can be JSON serialized. |
| `resume_pending_tool_approvals` | function | `src/agents/run_resume.py` | Executes approved or rejected pending tool calls before the resumed main loop continues. |

## Coding CLI Symbols

| Symbol | File | Notes |
|---|---|---|
| `ApprovalDecision` | `src/agents/coding_cli.py` | Frozen approve/reject decision keyed by `(tool_name, call_id)` with an optional rejection reason. |
| `CodingCliConfig` | `src/agents/coding_cli.py` | Frozen snapshot of CLI task, workspace, profile, model, limits, optional session path, state/resume paths, approval decisions, optional trajectory path, test-command settings, and verification settings. |
| `_parse_approval_ref` | `src/agents/coding_cli.py` | Parses `TOOL_NAME:CALL_ID` strings for `--approve` and `--reject`. |
| `_approval_decisions_from_args` | `src/agents/coding_cli.py` | Converts argparse approve/reject lists into `ApprovalDecision` values. |
| `_validate_coding_cli_args` | `src/agents/coding_cli.py` | Enforces fresh/resume argument boundaries, approve-all rejection conflict rules, and duplicate approve/reject conflicts. |
| `parse_coding_cli_args` | `src/agents/coding_cli.py` | Converts `argparse` values into `CodingCliConfig` and validates profile/positive limits plus resume approval rules. |
| `build_coding_cli_setup` | `src/agents/coding_cli.py` | Converts config into `CodingAgentSetup` by selecting a profile, model, `WorkspaceManifest`, optional `JsonSession`, and optional `VerificationPolicy`; explicit verification commands are also added to the manifest test-command allowlist. |
| `_print_verification_summary` | `src/agents/coding_cli.py` | Prints `RunResult.verification_summary` after final output or pending approval output, without changing exit-code mapping. |
| `_write_trajectory_from_result` | `src/agents/coding_cli.py` | Converts a fresh or resumed `RunResult` into trajectory events, preserves a caller-provided run id, appends saved-state evidence when present, and controls overwrite-vs-append mode for the JSONL writer. |
| `_config_from_state_envelope` | `src/agents/coding_cli.py` | Rebuilds resume config from a `CodingRunStateEnvelope`, including task/workspace/profile/model and test-command metadata. |
| `_apply_approval_decisions` | `src/agents/coding_cli.py` | Validates pending approval refs and writes approved/rejected status into the restored `RunState`. |
| `_run_state_from_state_envelope` | `src/agents/coding_cli.py` | Restores `RunState` from the state snapshot using a rebuilt `RunContextWrapper`, then applies approval decisions. |
| `_restore_previous_response_id` | `src/agents/coding_cli.py` | Restores saved `last_response_id` onto compatible model adapters for Responses continuation. |
| `_print_pending_approvals_from_envelope` | `src/agents/coding_cli.py` | Prints saved pending approval `summary` values when `--resume-state` is run without an approval decision, with legacy fallback output for old state files. |
| `_save_pending_state_from_result` | `src/agents/coding_cli.py` | Saves a state JSON only when the run has pending approvals and `CodingCliConfig.state_json` is set. |
| `_save_or_clear_resumed_state` | `src/agents/coding_cli.py` | Rewrites the resume state path when approvals pause again or deletes it when resume completes. |
| `_run_resumed_coding_agent_cli` | `src/agents/coding_cli.py` | Loads `--resume-state`, rebuilds setup/state, applies decisions, calls `resume_agent_loop`, and handles output/state lifecycle. |
| `run_coding_agent_cli` | `src/agents/coding_cli.py` | Runs a fresh local coding task or a resume-state approval flow, optionally writes pending state JSON and trajectory JSONL, and maps final output, pending approvals, and errors to process exit codes. |
| `build_example_command` | `examples/local_coding_cli.py` | Builds the recommended `python -m agents.coding_cli` command without calling a real model. |

## Coding CLI State Symbols

| Symbol | File | Notes |
|---|---|---|
| `CODING_RUN_STATE_ENVELOPE_VERSION` | `src/agents/coding_state.py` | Version marker for the CLI state-file wrapper; separate from nested `RUN_STATE_SNAPSHOT_SCHEMA_VERSION`. |
| `PENDING_APPROVAL_REQUIRED_FIELDS` | `src/agents/coding_state.py` | Required pending approval keys: `tool_name`, `call_id`, `arguments`, and `reason`. |
| `CodingRunStateEnvelope` | `src/agents/coding_state.py` | Dataclass shape for task/workspace/profile/model metadata, manifest metadata, nested run snapshot, and structured pending approvals. |
| `CodingRunStateStore` | `src/agents/coding_state.py` | Module-level state-file reader/writer used by the CLI; not exported from the package root in the current code. |
| `CodingRunStateStore.save_pending_result` | `src/agents/coding_state.py` | Writes the pending state envelope by calling `RunResult.to_state()` and `WorkspaceManifest.metadata()`. |
| `CodingRunStateStore.load_envelope` | `src/agents/coding_state.py` | Reads and validates envelope version, object fields, and pending approval shape before CLI resume applies approve/reject decisions. |

## Approval Summary Symbols

| Symbol | File | Notes |
|---|---|---|
| `approval_summary_for_tool_call` | `src/agents/approval_summaries.py` | Display-only summary helper for pending tool approvals; shell/test summaries include command/cwd/risk/reason and patch summaries include dry-run, changed paths, operations, risk, and reason. |
| `_patch_summary_parts` | `src/agents/approval_summaries.py` | Reads `patch` or `patch_text`, calls `parse_patch()` for display-only path/operation extraction, and returns the parse-failure marker when patch text is invalid. |
| `_PATCH_PARSE_FAILED` | `src/agents/approval_summaries.py` | Stable summary marker: `patch parse failed before approval summary`. |
| `PendingApprovalSummary` | `src/agents/result.py` | Frozen result-layer object preserving `tool_name`, `call_id`, `arguments`, `reason`, and display `summary`. |

## Coding-Agent Workspace, Policy, and Evidence Symbols

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
| `state_saved_event` | `src/agents/trajectory.py` | CLI lifecycle evidence event for a pending state file write; payload records `state_path` and `pending_count`. |
| `resume_started_event` | `src/agents/trajectory.py` | CLI lifecycle evidence event for a resume attempt; payload records state path plus approval and rejection counts. |
| `approval_decision_event` | `src/agents/trajectory.py` | CLI lifecycle evidence event for user approval input; payload records tool name, call id, approved/rejected decision, and reason. |
| `write_trajectory_jsonl` | `src/agents/trajectory.py` | Writes trajectory events as one JSON object per line, creating parent directories. |
| `pending_approval_summaries` | `src/agents/result.py` | Result-layer pending approval objects with preserved legacy fields plus display `summary`, used by CLI printing and state persistence. |
| `verification_summary` | `src/agents/result.py` | Result-layer summary derived from `verification_result` and `verification_skipped` items; consumed by CLI summary output and trajectory payloads. |

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
| `RunStateSnapshot` | `src/agents/run_state.py` | serializable run continuation data | `RunResult.to_state`, `run_state_snapshot_to_dict` | `run_state_snapshot_from_dict`, `RunState.from_snapshot` |
| `CodingRunStateEnvelope` | `src/agents/coding_state.py` | CLI state-file wrapper around task/config metadata, manifest metadata, nested run snapshot, and pending approval summaries | `CodingRunStateStore.save_pending_result` | `CodingRunStateStore.load_envelope`, `coding_cli._run_resumed_coding_agent_cli` |
| `RunResult` | `src/agents/result.py` | frozen final run output, items, raw responses, context | `build_run_result` | users, chat, sessions, resume |
| `ApprovalSnapshot` | `src/agents/run_state.py` | JSON-safe approval status keyed by tool name and call id | `RunContextWrapper.export_tool_approvals`, `RunResult.to_state` fallback | `RunContextWrapper.import_tool_approvals`, `RunState.from_snapshot` |
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
| `VerificationResult.to_observation` | `src/agents/verification.py` | Renders status, command, returncode, timeout flag, and clipped output as model-visible verification feedback. |
| `VerificationRunner` | `src/agents/verification.py` | Runs verification policy through `Environment`. |
| `run_verification_after_tool` | `src/agents/run_recording.py` | Runtime hook that records `verification_result` / `verification_skipped` items and writes verification observations to memory. |
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
| Coding CLI / manifest setup | `tests/test_coding_cli.py`, `tests/test_workspace_manifest.py`, `tests/test_coding_agent_profile.py` |
| Coding CLI verification setup/output | `tests/test_coding_cli.py::test_build_coding_cli_setup_attaches_verification_policy`, `tests/test_coding_cli.py::test_run_coding_agent_cli_prints_verification_summary_after_final_output`, `tests/test_coding_cli.py::test_run_coding_agent_cli_feeds_failed_verification_to_next_turn` |
| Approval summaries | `tests/test_approval_summaries.py`, `tests/test_result.py::RunResultTestCase.test_pending_approval_summaries_include_approval_summary_text`, `tests/test_coding_cli.py::test_run_coding_agent_cli_prints_pending_approval_and_returns_two`, `tests/test_coding_state.py::test_save_pending_result_writes_envelope_and_loads_it` |
| Coding CLI state files | `tests/test_coding_state.py`, `tests/test_coding_cli.py::test_run_coding_agent_cli_writes_state_contract_from_fake_model`, `tests/test_coding_cli.py::test_run_coding_agent_cli_resumes_and_deletes_completed_state`, `tests/test_coding_cli.py::test_run_coding_agent_cli_resumes_and_rewrites_pending_state` |
| Coding CLI approve/reject resume | `tests/test_coding_cli.py`, `tests/test_tool_approval_pause.py::test_coding_cli_resume_run_state_applies_approve_decision`, `tests/test_tool_approval_pause.py::test_coding_cli_resume_run_state_applies_reject_decision`, `tests/test_tool_approval_pause.py::test_coding_cli_resume_run_state_approve_all_approves_pending_calls` |
| Run loop | `tests/test_runner.py`, `tests/test_run_steps.py`, `tests/test_run_state.py` |
| Model/OpenAI adapter | `tests/test_models.py`, `tests/test_output.py`, `tests/test_code_execution.py` |
| Tools/approvals | `tests/test_tools.py`, `tests/test_tool_execution_plan.py`, `tests/test_tool_approval_runtime.py`, `tests/test_tool_approval_pause.py`, `tests/test_coding_policies.py`, `tests/test_tool_observations.py` |
| Guardrails | `tests/test_guardrails.py`, `tests/test_tool_guardrails.py` |
| Workspace/context | `tests/test_workspace.py`, `tests/test_workspace_tools.py`, `tests/test_workspace_inventory.py`, `tests/test_workspace_code.py`, `tests/test_workspace_code_tools.py`, `tests/test_context_mentions.py`, `tests/test_selected_files.py`, `tests/test_repo_context.py`, `tests/test_context_chunks.py` |
| Memory/session/chat | `tests/test_memory.py`, `tests/test_session_memory_example.py`, `tests/test_chat.py` |
| Observability/verification/trajectory | `tests/test_tracing.py`, `tests/test_lifecycle.py`, `tests/test_verification.py`, `tests/test_verification_loop.py`, `tests/test_trajectory.py`, `tests/test_coding_cli.py::test_run_coding_agent_cli_writes_end_to_end_plan06_trajectory` |
