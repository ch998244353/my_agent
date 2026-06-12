# Architecture Index

## Metadata

- generated_at: 2026-06-12T02:59:07.9934369+08:00
- source_commit: f79ea5bf607765886311fa6ed6184cf7ccc90715
- codegraph: force re-indexed with `codegraph index --force .`; final status 98 Python files, 2507 nodes, 6634 edges
- confidence: HIGH for in-repo runtime flow; MEDIUM for real OpenAI API behavior; external API semantics are NEEDS_VERIFICATION

## Project One-Liner

`my-agent` is a synchronous Python Agents SDK-style runtime with configurable `Agent` objects, Responses-style model adapters, function tools, guardrails, workspace-aware coding-agent tools, resumable approvals, sessions, memory compaction, tracing, and verification (`pyproject.toml`; `src/agents/agent.py:Agent`; `src/agents/run_loop.py:run_agent_loop`; `src/agents/models.py:OpenAIResponsesModel`).

## Mental Model

- Public users construct an `Agent` directly or through profiles such as `build_coding_agent` and `build_chat_runtime` (`src/agents/agent.py:Agent`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:build_chat_runtime`).
- A run is a state machine over `RunState`: add task, build repo context, run input guardrails, call model, classify output, plan tool execution, run tools/handoffs, verify, and return `RunResult` (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_state.py:RunState`; `src/agents/result.py:RunResult`).
- Model calls are normalized to `ModelResponse`; custom models may expose `get_response`, while `OpenAIResponsesModel` builds OpenAI Responses request kwargs and parses function calls (`src/agents/model_turn.py:run_model_turn`; `src/agents/models.py:call_model_response`; `src/agents/models.py:OpenAIResponsesModel`).
- Tools are `FunctionTool` values registered in `ToolRegistry`; the loop executes `ToolCall` values through planning, approvals, guardrails, timeout/formatting, memory recording, and optional final-output detection (`src/agents/tools.py:FunctionTool`; `src/agents/tools.py:ToolRegistry`; `src/agents/tool_execution.py:execute_tool_call`; `src/agents/tool_planning.py:build_tool_execution_plan`).
- Workspace coding behavior is capability-driven: `CodingAgentProfile` registers read, shell/test, and edit tools and injects `Workspace`, `SelectedFilesState`, and optional `Environment` into `RunConfig.context` (`src/agents/coding_agent.py:CodingAgentProfile`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/run_context.py:RunContextWrapper`).
- Persistent conversation history is separate from per-run memory: `RunConfig.session` supplies prior messages and receives `RunResult.to_input_list()` after a run (`src/agents/run_config.py:RunConfig`; `src/agents/run_loop.py:_session_messages`; `src/agents/run_loop.py:_save_result_to_session`; `src/agents/result.py:RunResultBase.to_input_list`).
- Tracing and lifecycle hooks are separate observability layers: tracing emits contextvar-based spans to processors/exporters, while lifecycle hooks call user callbacks (`src/agents/tracing.py:trace`; `src/agents/tracing.py:Span`; `src/agents/lifecycle.py:LifecycleHooks`).

## Runtime Spine

1. `Agent.run` delegates to `Runner.run_sync` (`src/agents/agent.py:Agent.run`; `src/agents/runner.py:Runner.run_sync`).
2. `Runner.run_sync` calls `run_agent_loop` (`src/agents/runner.py:Runner.run_sync`; `src/agents/run_loop.py:run_agent_loop`).
3. `run_agent_loop` opens trace/task/agent spans and calls `_run_agent_loop_impl` (`src/agents/run_loop.py:run_agent_loop`; `src/agents/tracing.py:trace`; `src/agents/tracing.py:task_span`; `src/agents/tracing.py:agent_span`).
4. `_run_agent_loop_impl` creates/resumes `RunState`, records task memory, builds repo context, runs guardrails, prepares turn input, calls the model, plans tools, executes tools/handoffs, and builds `RunResult` (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_state.py:build_run_result`).
5. `prepare_turn_input` uses `build_turn_context` plus `Agent._tool_specs_for_model`; `run_model_turn` calls `call_model_response` or fallback `model.decide` (`src/agents/model_turn.py:prepare_turn_input`; `src/agents/context_chunks.py:build_turn_context`; `src/agents/model_turn.py:run_model_turn`).
6. `process_model_turn` and `build_tool_execution_plan` decide whether the next step is final output, stop, pending approval, handoff, or tool execution (`src/agents/turn_resolution.py:process_model_turn`; `src/agents/tool_planning.py:build_tool_execution_plan`; `src/agents/turn_resolution.py:NextStepFinalOutput`).
7. Tool calls flow through `execute_tool_call`; outputs update `RunItem`, model pending tool outputs, and `AgentMemory` (`src/agents/tool_execution.py:execute_tool_call`; `src/agents/tool_execution.py:_execute_tool_call_impl`; `src/agents/contracts.py:RunItem`; `src/agents/memory.py:AgentMemory.add_step`).

## Subsystem Table

| Subsystem | Owned files | Primary symbols | Evidence |
|---|---|---|---|
| Public API and agent construction | `src/agents/__init__.py`, `src/agents/agent.py`, `src/agents/agents.py`, `src/agents/coding_agent.py`, `src/agents/chat_runtime.py` | `Agent`, `AgentCapabilities`, `build_coding_agent`, `ChatRuntime` | `src/agents/__init__.py:__all__`; `src/agents/agent.py:Agent`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:ChatRuntime` |
| Run loop and state machine | `src/agents/runner.py`, `src/agents/run_loop.py`, `src/agents/turn_resolution.py`, `src/agents/run_steps.py` | `Runner.run_sync`, `run_agent_loop`, `_run_agent_loop_impl`, `ProcessedResponse`, `NextStep*` | `src/agents/runner.py:Runner.run_sync`; `src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/turn_resolution.py:ProcessedResponse` |
| Model calls | `src/agents/model_turn.py`, `src/agents/models.py`, `src/agents/model_settings.py`, `src/agents/output.py` | `TurnInput`, `ModelTurnResult`, `OpenAIResponsesModel`, `ModelSettings`, `set_structured_final_answer` | `src/agents/model_turn.py:TurnInput`; `src/agents/models.py:OpenAIResponsesModel`; `src/agents/output.py:set_structured_final_answer` |
| Tool system | `src/agents/tools.py`, `src/agents/tool_runtime.py`, `src/agents/tool_execution.py`, `src/agents/tool_planning.py`, `src/agents/tool_schema.py` | `FunctionTool`, `ToolRegistry`, `ToolExecutionPlan`, `ToolExecutionOutcome`, `ToolExecutionLimits` | `src/agents/tools.py:FunctionTool`; `src/agents/tool_planning.py:ToolExecutionPlan`; `src/agents/tool_execution.py:ToolExecutionOutcome` |
| Guardrails and policy | `src/agents/guardrails.py`, `src/agents/tool_guardrails.py` | `InputGuardrail`, `OutputGuardrail`, `ToolInputGuardrail`, `ToolOutputGuardrail`, `ToolGuardrailFunctionOutput` | `src/agents/guardrails.py:InputGuardrail`; `src/agents/tool_guardrails.py:ToolGuardrailFunctionOutput` |
| Workspace and repo context | `src/agents/workspace.py`, `src/agents/workspace_tools.py`, `src/agents/workspace_inventory.py`, `src/agents/workspace_code.py`, `src/agents/workspace_code_tools.py`, `src/agents/context_mentions.py`, `src/agents/selected_files.py`, `src/agents/repo_context.py`, `src/agents/context_chunks.py` | `Workspace`, `WorkspaceCodeReader`, `SelectedFilesState`, `RepoContextBuilder`, `build_task_repo_context`, `build_turn_context` | `src/agents/workspace.py:Workspace`; `src/agents/repo_context.py:build_task_repo_context`; `src/agents/context_chunks.py:build_turn_context` |
| Memory and sessions | `src/agents/memory.py`, `src/agents/chat.py`, `src/agents/chat_runtime.py` | `AgentMemory`, `AgentSession`, `JsonSession`, `MemoryCompressor`, `run_chat_turn` | `src/agents/memory.py:AgentSession`; `src/agents/memory.py:JsonSession`; `src/agents/chat.py:run_chat_turn` |
| Tracing and lifecycle | `src/agents/tracing.py`, `src/agents/lifecycle.py` | `Trace`, `Span`, `BatchTraceProcessor`, `JSONLTracingExporter`, `LifecycleHooks` | `src/agents/tracing.py:Trace`; `src/agents/tracing.py:BatchTraceProcessor`; `src/agents/lifecycle.py:LifecycleHooks` |
| Results and contracts | `src/agents/contracts.py`, `src/agents/result.py`, `src/agents/run_state.py`, `src/agents/run_resume.py` | `ChatMessage`, `ToolCall`, `ModelResponse`, `RunItem`, `RunState`, `RunResult`, `RunStateSnapshot` | `src/agents/contracts.py:RunItem`; `src/agents/run_state.py:RunState`; `src/agents/result.py:RunResultBase.to_state` |
| Verification | `src/agents/verification.py`, `src/agents/run_recording.py` | `VerificationPolicy`, `VerificationRunner`, `VerificationResult`, `run_verification_after_tool` | `src/agents/verification.py:VerificationPolicy`; `src/agents/run_recording.py:run_verification_after_tool` |

## Entry Points

- Direct run: `Agent.run(task, config)` -> `Runner.run_sync` (`src/agents/agent.py:Agent.run`; `src/agents/runner.py:Runner.run_sync`).
- Lower-level run: `run_agent_loop(agent, task, config)` and `resume_agent_loop(agent, run_state, config)` (`src/agents/run_loop.py:run_agent_loop`; `src/agents/run_loop.py:resume_agent_loop`).
- Chat: `run_chat_turn`, `ChatRuntime.run_turn`, and CLI `main` (`src/agents/chat.py:run_chat_turn`; `src/agents/chat_runtime.py:ChatRuntime.run_turn`; `src/agents/chat_cli.py:main`).
- Coding agent builder: `build_coding_agent` returns `CodingAgentSetup(agent, run_config, workspace, environment)` (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/coding_agent.py:CodingAgentSetup`).
- Public exports: package root re-exports most runtime symbols via `__all__` (`src/agents/__init__.py:__all__`).

## Common Task Lookup

- Add or change a public runtime API: inspect `src/agents/__init__.py:__all__`, `src/agents/agent.py:Agent`, and tests `tests/test_public_api.py`.
- Change main loop behavior: inspect `src/agents/run_loop.py:_run_agent_loop_impl`, `src/agents/turn_resolution.py:process_model_turn`, `src/agents/turn_resolution.py:NextStepStopped`, `src/agents/tool_planning.py:build_tool_execution_plan`, and tests `tests/test_runner.py`, `tests/test_run_steps.py`.
- Change model request/parse behavior: inspect `src/agents/models.py:OpenAIResponsesModel`, `src/agents/models.py:build_response_request_kwargs`, `src/agents/models.py:response_items_to_tool_calls`, and tests `tests/test_models.py`.
- Change tool creation/execution: inspect `src/agents/tools.py:FunctionTool`, `src/agents/tool_runtime.py:ToolExecutionLimits`, `src/agents/tool_runtime.py:requires_tool_approval`, `src/agents/tool_execution.py:execute_tool_call`, and tests `tests/test_tools.py`, `tests/test_tool_execution_plan.py`, `tests/test_tool_approval_runtime.py`.
- Change workspace/code context: inspect `src/agents/repo_context.py:build_task_repo_context`, `src/agents/selected_files.py:SelectedFilesState`, `src/agents/workspace_code.py:WorkspaceCodeReader`, and tests `tests/test_repo_context.py`, `tests/test_selected_files.py`, `tests/test_context_chunks.py`, `tests/test_workspace_code.py`.
- Change session/memory compaction: inspect `src/agents/memory.py:AgentSession`, `src/agents/memory.py:MemoryCompressor`, `src/agents/memory.py:JsonSession`, and tests `tests/test_memory.py`, `tests/test_session_memory_example.py`.
- Change tracing: inspect `src/agents/tracing.py:Trace`, `src/agents/tracing.py:Span`, `src/agents/tracing.py:SynchronousMultiTracingProcessor`, and tests `tests/test_tracing.py`.
- Change verification: inspect `src/agents/verification.py:VerificationPolicy`, `src/agents/run_recording.py:run_verification_after_tool`, and tests `tests/test_verification.py`, `tests/test_verification_loop.py`.

## Invariants

- Workspace reads/writes must resolve under `Workspace.root`, pass `allowed_paths`, and avoid `ignore_patterns` through `Workspace.ensure_readable_path` (`src/agents/workspace.py:Workspace.ensure_readable_path`).
- Tool steps count via `RunState.record_tool_step`, model turns count via `RunState.record_model_turn`, and `RunState.next_limit_reason` stops when max steps/turns are reached (`src/agents/run_state.py:RunState.record_tool_step`; `src/agents/run_state.py:RunState.record_model_turn`; `src/agents/run_state.py:RunState.next_limit_reason`).
- `final_answer` is authoritative only when `RunState.reached_final_answer` is true and a `final_output` `RunItem` has not been cleared by output guardrails (`src/agents/run_recording.py:record_final_output`; `src/agents/run_loop.py:_clear_final_output`).
- Pending tool approvals are keyed by `(tool_name, call_id)` and exported/imported through `RunContextWrapper` and `RunStateSnapshot` (`src/agents/run_context.py:RunContextWrapper._approval_key`; `src/agents/result.py:RunResultBase.to_state`; `src/agents/run_state.py:RunState.from_snapshot`).
- `AgentSession` compaction may replace older turns with `MemorySummary`; consumers must use `AgentSession.to_messages` or `get_items`, not direct `turns` assumptions (`src/agents/memory.py:AgentSession._compact_if_needed`; `src/agents/memory.py:AgentSession.to_messages`).

## Risks

- `LocalEnvironment.run` executes with `shell=True`; safety depends on `Workspace` cwd checks, tool approval, and caller-provided command policies (`src/agents/environment.py:LocalEnvironment.run`; `src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/shell_tools.py:create_test_command_tool`).
- Patch update applies the first exact text match via `str.replace(..., 1)`; ambiguous hunks can update an unintended duplicate block (`src/agents/patches.py:_apply_update_content`).
- `run_resume.py` imports `ToolExecutionOutcome` and `execute_tool_call` from `src/agents/run_steps.py`, which re-exports compatibility imports; changing `run_steps.py` can break approval resume unexpectedly (`src/agents/run_resume.py:resume_pending_tool_approvals`; `src/agents/run_steps.py:execute_tool_call`).
- Handoffs call `target_agent.run(task)` without passing parent `RunConfig.context` or tracing config; cross-agent context propagation is UNKNOWN (`src/agents/run_steps.py:_execute_handoff_impl`).
- Real OpenAI Responses API defaults, model name `gpt-5.4`, and request shape should be verified against current OpenAI docs before production use; repo code is the only evidence here (`src/agents/models.py:OpenAIResponsesModel`; `src/agents/models.py:build_response_request_kwargs`).

## Verification Notes

- Codegraph was force-rebuilt and status checked after discovering stale index coverage (`codegraph index --force .`; `codegraph status .`).
- Main evidence files inspected: all runtime core files under `src/agents`, tests list via `rg --files`, `pyproject.toml`, and package exports (`src/agents/__init__.py:__all__`).
- `python -m pytest` was run after docs generation: 496 passed in 3.17s on Windows/Python 3.14.2.
