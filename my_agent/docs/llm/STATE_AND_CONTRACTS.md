# State and Contracts

## `Agent`

- Owned data: `memory`, `model`, `name`, `instructions`, `model_settings`, `output_type`, `tool_use_behavior`, `tool_registry`, `handoffs`, `max_steps`, `capabilities`, hooks, guardrails (`src/agents/agent.py:Agent`).
- Lifecycle: constructed directly or through builders; `__post_init__` prepares tools and output schema (`src/agents/agent.py:Agent.__post_init__`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:build_chat_agent`).
- Producers: user code, examples, profile builders, chat runtime (`examples/basic_chat.py`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:build_chat_agent`).
- Consumers: run loop, model turn, tool planner, handoff logic (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/model_turn.py:prepare_turn_input`; `src/agents/handoffs.py:handoff_map`).
- Mutation rules: dataclass is mutable; `tool_registry` and `memory` mutate during use; output schema preparation may mutate model fields (`src/agents/agent.py:Agent._prepare_output_schema`; `src/agents/tools.py:ToolRegistry.register`).
- Invariants: default final-answer tool is registered unless disabled; python executor tool is registered when python execution capability is enabled (`src/agents/agent.py:Agent._prepare_tools`; `src/agents/agent.py:AgentCapabilities`).
- Serialization: no direct `Agent` serialization exists. UNKNOWN for long-lived agent persistence.

## `RunConfig` and `RunContextWrapper`

- Owned data: `RunConfig` carries context, session, metadata, tracing, hooks, guardrails, limits, model settings, verification (`src/agents/run_config.py:RunConfig`).
- Owned runtime context: `RunContextWrapper` carries `context`, `usage`, `metadata`, and private tool approval map (`src/agents/run_context.py:RunContextWrapper`).
- Producers: user code, chat runtime, coding-agent builder (`src/agents/chat_runtime.py:_effective_run_config`; `src/agents/coding_agent.py:build_coding_agent`).
- Consumers: run loop, guardrails, tools, verification, workspace/repo context (`src/agents/run_loop.py:_create_run_context`; `src/agents/tool_execution.py:_execute_tool_call_impl`; `src/agents/repo_context.py:build_task_repo_context`).
- Mutation rules: `RunConfig` is frozen; `RunContextWrapper.context` may be mutated to add repo context, and approval status methods mutate `_tool_approvals` (`src/agents/repo_context.py:build_task_repo_context`; `src/agents/run_context.py:RunContextWrapper.request_tool_call_approval`).
- Invariants: typed context accessors return `None` on missing keys or type mismatch; approval keys are `(tool_name, call_id)` (`src/agents/run_context.py:RunContextWrapper._context_value`; `src/agents/run_context.py:RunContextWrapper._approval_key`).
- Serialization: approvals export through `export_tool_approvals` and import through `import_tool_approvals`; arbitrary `context` is not serialized by `RunResult.to_state` (`src/agents/run_context.py:RunContextWrapper.export_tool_approvals`; `src/agents/run_context.py:RunContextWrapper.import_tool_approvals`; `src/agents/result.py:RunResultBase.to_state`).

## `RunState`

- Owned data: `new_items`, input, last agent, final answer flags, counters/limits, handoff depth, context wrapper, pending tool calls, guardrail result lists (`src/agents/run_state.py:RunState`).
- Lifecycle: created at run start or restored via `RunState.from_snapshot`; converted to `RunResult` by `build_run_result` (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_state.py:RunState.from_snapshot`; `src/agents/run_state.py:build_run_result`).
- Producers: run loop and resume path (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_loop.py:resume_agent_loop`).
- Consumers: all runtime subsystems mutate/read it (`src/agents/model_turn.py:run_model_turn`; `src/agents/tool_execution.py:execute_tool_call`; `src/agents/run_recording.py:record_final_output`).
- Mutation rules: only model turns increment `current_turn`; tool/handoff/error/rejected approval steps increment `steps_taken`; final output mutation goes through `record_final_output` except handoff direct copy (`src/agents/run_state.py:RunState.record_model_turn`; `src/agents/run_state.py:RunState.record_tool_step`; `src/agents/run_recording.py:record_final_output`; `src/agents/run_steps.py:_execute_handoff_impl`).
- Invariants: `next_step_number == steps_taken + 1`; model limit checks max turns, tool limit checks max steps; `next_limit_reason` returns tool limit before model limit (`src/agents/run_state.py:RunState.next_step_number`; `src/agents/run_state.py:RunState.model_limit_reason`; `src/agents/run_state.py:RunState.next_limit_reason`).
- Serialization: `RunStateSnapshot` stores input, last agent name, last response id, counters, limits, approvals, model response states, new items; restoring rehydrates `RunItem` payloads shallowly and pending tool calls from approvals (`src/agents/run_state.py:RunStateSnapshot`; `src/agents/run_state.py:_run_items_from_snapshot`; `src/agents/run_state.py:_pending_tool_calls_from_approvals`).

## `RunResult`

- Owned data: frozen final answer, step results, counters, context wrapper, guardrail results, raw responses, new items (`src/agents/result.py:RunResultBase`; `src/agents/result.py:RunResult`).
- Lifecycle: built once at run end or pause; saved to session if configured (`src/agents/run_state.py:build_run_result`; `src/agents/run_loop.py:_build_result_and_save_session`).
- Producers: `build_run_result` only (`src/agents/run_state.py:build_run_result`).
- Consumers: user code, chat helpers, session save, resume snapshot (`src/agents/chat.py:chat_turn_from_result`; `src/agents/memory.py:session_items_from_result`; `src/agents/result.py:RunResultBase.to_state`).
- Mutation rules: frozen dataclass, but nested mutable objects may still exist in payloads. Treat as read-only contract (`src/agents/result.py:RunResult`).
- Invariants: `final_output` aliases `final_answer`; `pending_approvals` derives from `new_items`; `last_response_id` derives from `raw_responses[-1]` (`src/agents/result.py:RunResultBase.final_output`; `src/agents/result.py:RunResultBase.pending_approvals`; `src/agents/result.py:RunResultBase.last_response_id`).
- Serialization: `to_state` creates `RunStateSnapshot` via `asdict`; known payload converters exist for `ToolCall`, `ToolApprovalRequest`, `ModelResponse`, and `VerificationSummary`; arbitrary payloads pass through (`src/agents/result.py:RunResultBase.to_state`; `src/agents/result.py:_run_item_payload_to_state`).

## Message, Tool, and Model Contracts

- `ChatMessage`: role must be one of `system`, `user`, `assistant`, `tool_call`, `tool_response`; content is string (`src/agents/contracts.py:MessageRole`; `src/agents/contracts.py:ChatMessage`).
- `ToolSpec`: name, description, arguments, returns; argument names come from `ToolArgument` values (`src/agents/contracts.py:ToolSpec`; `src/agents/contracts.py:ToolArgument`).
- `ToolCall`: model-visible action with tool name, JSON-like arguments, call id (`src/agents/contracts.py:ToolCall`; `src/agents/models.py:function_call_item_to_tool_call`).
- `ModelResponse`: normalized output with optional response id, output text, tool calls, refusal, raw response, usage, request metadata (`src/agents/contracts.py:ModelResponse`; `src/agents/models.py:OpenAIResponsesModel.get_response`).
- `RunItem`: event log item; allowed types are enumerated in the literal and must be updated if new event types are introduced (`src/agents/contracts.py:RunItem`).
- Serialization risk: only selected payload types are converted to dicts; adding new payload object types needs `_run_item_payload_to_state` update (`src/agents/result.py:_run_item_payload_to_state`).

## Tool Registry and Execution Contracts

- `FunctionTool` owns `ToolSpec`, handler, enablement, tool guardrails, execution limits, approval policy (`src/agents/tools.py:FunctionTool`).
- `FunctionTool.execute` validates provided argument names against spec, enforces required args, then uses `run_with_timeout` around `handler(**arguments)` (`src/agents/tools.py:FunctionTool.execute`; `src/agents/tool_runtime.py:run_with_timeout`).
- `ToolRegistry.register` overwrites by tool name without warning; `get` raises `ToolNotFoundError`; `list_specs` filters disabled tools (`src/agents/tools.py:ToolRegistry.register`; `src/agents/tools.py:ToolRegistry.get`; `src/agents/tools.py:ToolRegistry.list_specs`).
- `tool_use_behavior` controls stopping after tool output; valid values are `"run_llm_again"`, `"stop_on_first_tool"`, or dict `stop_at_tool_names` (`src/agents/tool_execution.py:should_stop_after_tool`).
- Approval contract: planning can record pending approvals before execution; execution re-checks status/approval policy before handler call; context approval status is keyed by tool name and call id (`src/agents/tool_planning.py:build_tool_execution_plan`; `src/agents/tool_execution.py:_execute_tool_call_impl`; `src/agents/run_context.py:RunContextWrapper.approval_status_for`).

## Guardrail Contracts

- Agent input guardrail function signature is `(context, agent, agent_input)` and returns `GuardrailFunctionOutput`; wrapper method is called as `guardrail.run(agent, task, context)` (`src/agents/guardrails.py:InputGuardrail`; `src/agents/guardrails.py:GuardrailFunctionOutput`).
- Agent output guardrail function signature is `(context, agent, agent_output)` and returns `GuardrailFunctionOutput` (`src/agents/guardrails.py:OutputGuardrail`).
- Agent guardrail tripwire means stop: input stops before model calls; output clears final output and records stop reason (`src/agents/run_loop.py:_run_input_guardrails`; `src/agents/run_loop.py:_run_output_guardrails`; `src/agents/run_loop.py:_clear_final_output`).
- Tool guardrails return `ToolGuardrailFunctionOutput` with behavior `allow`, `reject_content`, or `raise_exception` (`src/agents/tool_guardrails.py:ToolGuardrailFunctionOutput`).
- Tool `reject_content` does not raise; it records a failed observation and continues the model loop unless stop behavior says otherwise (`src/agents/tool_execution.py:_execute_tool_call_impl`).

## Workspace and Context Contracts

- `Workspace.root` and `allowed_paths` are resolved in `__post_init__`; paths must remain under root (`src/agents/workspace.py:Workspace.__post_init__`; `src/agents/workspace.py:Workspace.resolve_path`).
- `Workspace.ensure_readable_path` enforces root, allowed paths, and ignore patterns; all workspace readers/tools should use it (`src/agents/workspace.py:Workspace.ensure_readable_path`; `src/agents/workspace_tools.py:create_read_workspace_file_tool`; `src/agents/workspace_code.py:WorkspaceCodeReader.read_lines`).
- `SelectedFilesState.add_file` deduplicates by normalized path and only promotes to editable, never downgrades editable to read-only (`src/agents/selected_files.py:SelectedFilesState.add_file`; `src/agents/selected_files.py:_normalize_selected_file_path`).
- `RepoContextBuilder.build` section order is priority-based through `RepoContext.ordered_sections`; context truncation may remove sections but keeps selected paths and mentioned symbols (`src/agents/repo_context.py:RepoContextBuilder.build`; `src/agents/repo_context.py:RepoContext.ordered_sections`; `src/agents/repo_context.py:_limit_context`).
- `build_turn_context` priority order is system instructions, session summary, repo context, memory, selected files (`src/agents/context_chunks.py:SYSTEM_CONTEXT_PRIORITY`; `src/agents/context_chunks.py:SESSION_SUMMARY_CONTEXT_PRIORITY`; `src/agents/context_chunks.py:REPO_CONTEXT_PRIORITY`; `src/agents/context_chunks.py:MEMORY_CONTEXT_PRIORITY`; `src/agents/context_chunks.py:SELECTED_FILES_CONTEXT_PRIORITY`).

## Memory and Session Contracts

- `AgentMemory.to_messages` renders one user task plus selected recent `StepRecord` messages/tool calls/observations/errors (`src/agents/memory.py:AgentMemory.to_messages`; `src/agents/memory.py:_append_step_messages`; `src/agents/memory.py:_select_recent_steps`).
- `AgentSession` is both `AgentMemory` and `SessionLike`; `turns` is authoritative and `steps` is a flattened synced view (`src/agents/memory.py:AgentSession`; `src/agents/memory.py:AgentSession._sync_steps_from_turns`).
- `AgentSession.add_items` starts a new turn for each user message and groups following assistant/tool messages into a `StepRecord` (`src/agents/memory.py:AgentSession.add_items`).
- `JsonSession` persists `{"version": 1, "session": session.to_dict()}` and tolerates missing/non-dict files by returning an empty session (`src/agents/memory.py:JsonSession._save`; `src/agents/memory.py:JsonSession._load`).
- Compaction contract: old turns may be replaced by `MemorySummary`; consumers must use `to_messages` or `get_items` instead of raw `turns` unless intentionally inspecting session internals (`src/agents/memory.py:AgentSession._compact_if_needed`; `src/agents/memory.py:MemorySummary.to_message`).

## Tracing and Lifecycle Contracts

- `trace(..., only_if_missing=True)` returns `NoOpTrace` if an active trace exists; nested agent runs do not replace current trace (`src/agents/tracing.py:trace`; `src/agents/run_loop.py:run_agent_loop`).
- `Span` requires an active trace unless `span()` returns `NoOpSpan`; direct `Span(...)` without trace can raise (`src/agents/tracing.py:Span.__init__`; `src/agents/tracing.py:span`).
- `record_span_error` records error type and optional cause type into span data and span error (`src/agents/tracing.py:record_span_error`).
- `SynchronousMultiTracingProcessor` forwards lifecycle events to processors and swallows processor exceptions (`src/agents/tracing.py:SynchronousMultiTracingProcessor._forward`).
- `LifecycleHooks` methods are no-op extension points; emitter functions call hooks sequentially and do not catch exceptions (`src/agents/lifecycle.py:LifecycleHooks`; `src/agents/lifecycle.py:emit_error`). NEEDS_VERIFICATION: hook exception propagation behavior is not explicitly tested in inspected docs.

## Verification Contracts

- `VerificationPolicy.enabled` is true only when commands are configured; `should_run_after_tool` requires enabled policy and tool name in `auto_after_tools` (`src/agents/verification.py:VerificationPolicy.enabled`; `src/agents/verification.py:VerificationPolicy.should_run_after_tool`).
- `VerificationRunner.run` stops on the first failed command and returns all attempted `VerificationResult` values (`src/agents/verification.py:VerificationRunner.run`).
- `run_verification_after_tool` respects trigger policy, runner existence, and max attempts; skipped attempts become `verification_skipped` items (`src/agents/run_recording.py:run_verification_after_tool`; `src/agents/run_recording.py:_record_verification_skipped`).
- `RunResult.verification_summary` derives attempts/skips/passed/last observation from `RunItem` history (`src/agents/result.py:RunResultBase.verification_summary`; `src/agents/result.py:_verification_summary_from_items`).

## Cross-Cutting Contracts

- Code beats docs: source symbols above are authoritative.
- New public symbols should be exported in `src/agents/__init__.py:__all__` and covered by `tests/test_public_api.py`.
- New `RunItem.item_type` values require updates to `src/agents/contracts.py:RunItem`, `src/agents/result.py:_run_item_payload_to_state`, and result/session expectations.
- New tool behavior should update both planning and execution when approval semantics are involved (`src/agents/tool_planning.py:build_tool_execution_plan`; `src/agents/tool_execution.py:_execute_tool_call_impl`).
- New workspace access must go through `Workspace.ensure_readable_path` unless the function intentionally handles unsafe paths and documents why (`src/agents/workspace.py:Workspace.ensure_readable_path`).
- Real OpenAI API behavior is external and time-varying; verify against OpenAI developer docs before changing `src/agents/models.py:OpenAIResponsesModel` request shape or default model assumptions.
