# Architecture Index

## Metadata

- generated_at: 2026-06-14T21:43:07.6300468+08:00
- source_commit: 10ff882d29b22cb97401a3b907f7dc9990f7f9aa with working-tree changes under `my_agent` and `teach`
- codegraph: project-scoped latest query for `my_agent`; status 109 Python files, 2770 nodes, 7087 edges
- confidence: HIGH for in-repo runtime flow; MEDIUM for real OpenAI API behavior; external API semantics are NEEDS_VERIFICATION

## Project One-Liner

`my-agent` is a synchronous Python Agents SDK-style runtime with configurable `Agent` objects, Responses-style model adapters, function tools, guardrails, workspace-aware coding-agent tools, workspace manifests, structured tool observations, coding safety policies, a local coding CLI, resumable approvals, sessions, memory compaction, tracing, trajectory JSONL export, and verification (`pyproject.toml`; `src/agents/agent.py:Agent`; `src/agents/run_loop.py:run_agent_loop`; `src/agents/models.py:OpenAIResponsesModel`; `src/agents/coding_cli.py:run_coding_agent_cli`; `src/agents/trajectory.py:trajectory_events_from_result`).

## Mental Model

- Public users construct an `Agent` directly or through profiles such as `build_coding_agent` and `build_chat_runtime` (`src/agents/agent.py:Agent`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:build_chat_runtime`).
- A run is a state machine over `RunState`: add task, build repo context, run input guardrails, call model, classify output, plan tool execution, run tools/handoffs, verify, and return `RunResult` (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_state.py:RunState`; `src/agents/result.py:RunResult`).
- Model calls are normalized to `ModelResponse`; custom models may expose `get_response`, while `OpenAIResponsesModel` builds OpenAI Responses request kwargs and parses function calls (`src/agents/model_turn.py:run_model_turn`; `src/agents/models.py:call_model_response`; `src/agents/models.py:OpenAIResponsesModel`).
- Tools are `FunctionTool` values registered in `ToolRegistry`; the loop executes `ToolCall` values through planning, approvals, guardrails, timeout/formatting, memory recording, and optional final-output detection (`src/agents/tools.py:FunctionTool`; `src/agents/tools.py:ToolRegistry`; `src/agents/tool_execution.py:execute_tool_call`; `src/agents/tool_planning.py:build_tool_execution_plan`).
- Workspace coding behavior is capability-driven: `CodingAgentProfile` registers read, shell/test, and edit tools, injects `Workspace`, `WorkspaceManifest`, `SelectedFilesState`, and optional `Environment` into `RunConfig.context`, and wires shell/edit approval policies when enabled (`src/agents/coding_agent.py:CodingAgentProfile`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/coding_policies.py:ShellCommandPolicy`).
- Local coding CLI behavior is a thin wrapper over existing runtime objects: parse arguments, build a manifest-backed `CodingAgentSetup`, call `Agent.run`, print output or pending approvals, optionally write trajectory JSONL, and return exit code `0`, `1`, or `2` (`src/agents/coding_cli.py:parse_coding_cli_args`; `src/agents/coding_cli.py:build_coding_cli_setup`; `src/agents/coding_cli.py:run_coding_agent_cli`; `src/agents/coding_cli.py:_write_trajectory_from_result`).
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
| Local coding CLI | `src/agents/coding_cli.py`, `examples/local_coding_cli.py` | `CodingCliConfig`, `parse_coding_cli_args`, `build_coding_cli_setup`, `run_coding_agent_cli` | `src/agents/coding_cli.py:run_coding_agent_cli`; `examples/local_coding_cli.py:build_example_command`; `tests/test_coding_cli.py` |
| Run loop and state machine | `src/agents/runner.py`, `src/agents/run_loop.py`, `src/agents/turn_resolution.py`, `src/agents/run_steps.py` | `Runner.run_sync`, `run_agent_loop`, `_run_agent_loop_impl`, `ProcessedResponse`, `NextStep*` | `src/agents/runner.py:Runner.run_sync`; `src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/turn_resolution.py:ProcessedResponse` |
| Model calls | `src/agents/model_turn.py`, `src/agents/models.py`, `src/agents/model_settings.py`, `src/agents/output.py` | `TurnInput`, `ModelTurnResult`, `OpenAIResponsesModel`, `ModelSettings`, `set_structured_final_answer` | `src/agents/model_turn.py:TurnInput`; `src/agents/models.py:OpenAIResponsesModel`; `src/agents/output.py:set_structured_final_answer` |
| Tool system | `src/agents/tools.py`, `src/agents/tool_runtime.py`, `src/agents/tool_execution.py`, `src/agents/tool_planning.py`, `src/agents/tool_schema.py`, `src/agents/tool_observations.py` | `FunctionTool`, `ToolRegistry`, `ToolExecutionPlan`, `ToolExecutionOutcome`, `ToolExecutionLimits`, `ToolObservation` | `src/agents/tools.py:FunctionTool`; `src/agents/tool_planning.py:ToolExecutionPlan`; `src/agents/tool_execution.py:ToolExecutionOutcome`; `src/agents/tool_observations.py:ToolObservation` |
| Guardrails and policy | `src/agents/guardrails.py`, `src/agents/tool_guardrails.py`, `src/agents/coding_policies.py` | `InputGuardrail`, `OutputGuardrail`, `ToolInputGuardrail`, `ToolOutputGuardrail`, `ToolGuardrailFunctionOutput`, `ShellCommandPolicy`, `PatchApprovalPolicy` | `src/agents/guardrails.py:InputGuardrail`; `src/agents/tool_guardrails.py:ToolGuardrailFunctionOutput`; `src/agents/coding_policies.py:SafetyDecision` |
| Workspace and repo context | `src/agents/workspace.py`, `src/agents/workspace_manifest.py`, `src/agents/workspace_tools.py`, `src/agents/workspace_inventory.py`, `src/agents/workspace_code.py`, `src/agents/workspace_code_tools.py`, `src/agents/context_mentions.py`, `src/agents/selected_files.py`, `src/agents/repo_context.py`, `src/agents/context_chunks.py` | `Workspace`, `WorkspaceManifest`, `WorkspaceCodeReader`, `SelectedFilesState`, `RepoContextBuilder`, `build_task_repo_context`, `build_turn_context` | `src/agents/workspace.py:Workspace`; `src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/repo_context.py:build_task_repo_context`; `src/agents/context_chunks.py:build_turn_context` |
| Memory and sessions | `src/agents/memory.py`, `src/agents/chat.py`, `src/agents/chat_runtime.py` | `AgentMemory`, `AgentSession`, `JsonSession`, `MemoryCompressor`, `run_chat_turn` | `src/agents/memory.py:AgentSession`; `src/agents/memory.py:JsonSession`; `src/agents/chat.py:run_chat_turn` |
| Tracing and lifecycle | `src/agents/tracing.py`, `src/agents/lifecycle.py` | `Trace`, `Span`, `BatchTraceProcessor`, `JSONLTracingExporter`, `LifecycleHooks` | `src/agents/tracing.py:Trace`; `src/agents/tracing.py:BatchTraceProcessor`; `src/agents/lifecycle.py:LifecycleHooks` |
| Results, contracts, and trajectory | `src/agents/contracts.py`, `src/agents/result.py`, `src/agents/run_state.py`, `src/agents/run_resume.py`, `src/agents/trajectory.py` | `ChatMessage`, `ToolCall`, `ModelResponse`, `RunItem`, `RunState`, `RunResult`, `RunStateSnapshot`, `TrajectoryEvent` | `src/agents/contracts.py:RunItem`; `src/agents/run_state.py:RunState`; `src/agents/result.py:RunResultBase.to_state`; `src/agents/trajectory.py:trajectory_events_from_result` |
| Verification | `src/agents/verification.py`, `src/agents/run_recording.py` | `VerificationPolicy`, `VerificationRunner`, `VerificationResult`, `run_verification_after_tool` | `src/agents/verification.py:VerificationPolicy`; `src/agents/run_recording.py:run_verification_after_tool` |

## Entry Points

- Direct run: `Agent.run(task, config)` -> `Runner.run_sync` (`src/agents/agent.py:Agent.run`; `src/agents/runner.py:Runner.run_sync`).
- Lower-level run: `run_agent_loop(agent, task, config)` and `resume_agent_loop(agent, run_state, config)` (`src/agents/run_loop.py:run_agent_loop`; `src/agents/run_loop.py:resume_agent_loop`).
- Chat: `run_chat_turn`, `ChatRuntime.run_turn`, and CLI `main` (`src/agents/chat.py:run_chat_turn`; `src/agents/chat_runtime.py:ChatRuntime.run_turn`; `src/agents/chat_cli.py:main`).
- Coding agent builder: `build_coding_agent` returns `CodingAgentSetup(agent, run_config, workspace, environment)` (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/coding_agent.py:CodingAgentSetup`).
- Coding CLI: `python -m agents.coding_cli --workspace . --task "..."` parses command values, builds a manifest-backed coding setup, runs once, maps results to exit codes, and can write `--trajectory-jsonl` evidence (`src/agents/coding_cli.py:main`; `src/agents/coding_cli.py:run_coding_agent_cli`; `src/agents/trajectory.py:write_trajectory_jsonl`).
- Trajectory writer: direct callers can convert a `RunResult` to `TrajectoryEvent` values and persist JSONL without enabling tracing (`src/agents/trajectory.py:trajectory_events_from_result`; `src/agents/trajectory.py:write_trajectory_jsonl`).
- Public exports: package root re-exports most runtime symbols via `__all__` (`src/agents/__init__.py:__all__`).

## Common Task Lookup

- Add or change a public runtime API: inspect `src/agents/__init__.py:__all__`, `src/agents/agent.py:Agent`, and tests `tests/test_public_api.py`.
- Change the local coding command: inspect `src/agents/coding_cli.py`, `src/agents/workspace_manifest.py`, `src/agents/trajectory.py`, `examples/local_coding_cli.py`, and tests `tests/test_coding_cli.py`.
- Change main loop behavior: inspect `src/agents/run_loop.py:_run_agent_loop_impl`, `src/agents/turn_resolution.py:process_model_turn`, `src/agents/turn_resolution.py:NextStepStopped`, `src/agents/tool_planning.py:build_tool_execution_plan`, and tests `tests/test_runner.py`, `tests/test_run_steps.py`.
- Change model request/parse behavior: inspect `src/agents/models.py:OpenAIResponsesModel`, `src/agents/models.py:build_response_request_kwargs`, `src/agents/models.py:response_items_to_tool_calls`, and tests `tests/test_models.py`.
- Change tool creation/execution: inspect `src/agents/tools.py:FunctionTool`, `src/agents/tool_runtime.py:ToolExecutionLimits`, `src/agents/tool_runtime.py:requires_tool_approval`, `src/agents/tool_observations.py:ToolObservation`, `src/agents/coding_policies.py:ShellCommandPolicy`, `src/agents/tool_execution.py:execute_tool_call`, and tests `tests/test_tools.py`, `tests/test_tool_execution_plan.py`, `tests/test_tool_approval_runtime.py`, `tests/test_tool_observations.py`, `tests/test_coding_policies.py`.
- Change workspace/code context: inspect `src/agents/workspace_manifest.py:WorkspaceManifest`, `src/agents/repo_context.py:build_task_repo_context`, `src/agents/selected_files.py:SelectedFilesState`, `src/agents/workspace_code.py:WorkspaceCodeReader`, and tests `tests/test_workspace_manifest.py`, `tests/test_repo_context.py`, `tests/test_selected_files.py`, `tests/test_context_chunks.py`, `tests/test_workspace_code.py`.
- Change session/memory compaction: inspect `src/agents/memory.py:AgentSession`, `src/agents/memory.py:MemoryCompressor`, `src/agents/memory.py:JsonSession`, and tests `tests/test_memory.py`, `tests/test_session_memory_example.py`.
- Change tracing: inspect `src/agents/tracing.py:Trace`, `src/agents/tracing.py:Span`, `src/agents/tracing.py:SynchronousMultiTracingProcessor`, and tests `tests/test_tracing.py`.
- Change verification: inspect `src/agents/verification.py:VerificationPolicy`, `src/agents/run_recording.py:run_verification_after_tool`, and tests `tests/test_verification.py`, `tests/test_verification_loop.py`.

## Invariants

- Workspace reads/writes must resolve under `Workspace.root`, pass `allowed_paths`, and avoid `ignore_patterns` through `Workspace.ensure_readable_path` (`src/agents/workspace.py:Workspace.ensure_readable_path`).
- `WorkspaceManifest` is the user-facing policy object for local coding runs; `build_workspace()` delegates path safety to `Workspace`, and `allowed_test_commands` always includes `default_test_command` after `__post_init__` (`src/agents/workspace_manifest.py:WorkspaceManifest.__post_init__`; `src/agents/workspace_manifest.py:WorkspaceManifest.build_workspace`).
- Tool steps count via `RunState.record_tool_step`, model turns count via `RunState.record_model_turn`, and `RunState.next_limit_reason` stops when max steps/turns are reached (`src/agents/run_state.py:RunState.record_tool_step`; `src/agents/run_state.py:RunState.record_model_turn`; `src/agents/run_state.py:RunState.next_limit_reason`).
- Shell/test/patch model-visible outputs should use `ToolObservation` helpers so status, summary, details, output, and truncation remain stable (`src/agents/tool_observations.py:ToolObservation`; `src/agents/tool_observations.py:command_result_observation`; `src/agents/tool_observations.py:patch_result_observation`).
- Coding shell/edit safety is policy-based: safe shell prefixes can run, risky or unknown shell commands require approval, blocked fragments raise before environment execution, and valid write patches require approval while dry runs can validate directly (`src/agents/coding_policies.py:ShellCommandPolicy.classify`; `src/agents/coding_policies.py:PatchApprovalPolicy.classify_patch_text`; `src/agents/shell_tools.py:create_shell_command_tool`).
- Trajectory JSONL is derived from `RunResult` history and must preserve unknown runtime items as `runtime_item` instead of dropping evidence (`src/agents/trajectory.py:_event_from_run_item`; `src/agents/trajectory.py:_event_type_from_run_item`).
- `final_answer` is authoritative only when `RunState.reached_final_answer` is true and a `final_output` `RunItem` has not been cleared by output guardrails (`src/agents/run_recording.py:record_final_output`; `src/agents/run_loop.py:_clear_final_output`).
- Pending tool approvals are keyed by `(tool_name, call_id)` and exported/imported through `RunContextWrapper` and `RunStateSnapshot` (`src/agents/run_context.py:RunContextWrapper._approval_key`; `src/agents/result.py:RunResultBase.to_state`; `src/agents/run_state.py:RunState.from_snapshot`).
- `AgentSession` compaction may replace older turns with `MemorySummary`; consumers must use `AgentSession.to_messages` or `get_items`, not direct `turns` assumptions (`src/agents/memory.py:AgentSession._compact_if_needed`; `src/agents/memory.py:AgentSession.to_messages`).

## Risks

- `LocalEnvironment.run` executes with `shell=True`; safety depends on `Workspace` cwd checks, `ShellCommandPolicy` string matching, tool approval, and caller-provided command policies (`src/agents/environment.py:LocalEnvironment.run`; `src/agents/coding_policies.py:ShellCommandPolicy`; `src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/shell_tools.py:create_test_command_tool`).
- Patch update applies the first exact text match via `str.replace(..., 1)`; ambiguous hunks can update an unintended duplicate block (`src/agents/patches.py:_apply_update_content`).
- `run_resume.py` imports `ToolExecutionOutcome` and `execute_tool_call` from `src/agents/run_steps.py`, which re-exports compatibility imports; changing `run_steps.py` can break approval resume unexpectedly (`src/agents/run_resume.py:resume_pending_tool_approvals`; `src/agents/run_steps.py:execute_tool_call`).
- Handoffs call `target_agent.run(task)` without passing parent `RunConfig.context` or tracing config; cross-agent context propagation is UNKNOWN (`src/agents/run_steps.py:_execute_handoff_impl`).
- Real OpenAI Responses API defaults, model name `gpt-5.4`, and request shape should be verified against current OpenAI docs before production use; repo code is the only evidence here (`src/agents/models.py:OpenAIResponsesModel`; `src/agents/models.py:build_response_request_kwargs`).

## Verification Notes

- Codegraph was queried with `projectPath` set to `my_agent`; current status is 109 Python files, 2770 nodes, and 7087 edges.
- Main evidence files inspected for this refresh: `teach/PLAN01.md` through `teach/PLAN05.md`, git path-limited status/diff for `my_agent` and `teach`, codegraph context/explore results for the Scheme A modules, and all six `docs/llm` files.
- Verification for this documentation-only refresh is recorded in `MAINTENANCE_LOG.md`.
