# Runtime Flows

## Normal Run

1. User code calls `Agent.run(task, config)` or `Runner.run_sync(agent, task, config)` (`src/agents/agent.py:Agent.run`; `src/agents/runner.py:Runner.run_sync`).
2. `run_agent_loop` chooses workflow/trace metadata, opens `trace`, `task_span`, and `agent_span`, then delegates to `_run_agent_loop_impl` (`src/agents/run_loop.py:run_agent_loop`; `src/agents/tracing.py:trace`; `src/agents/tracing.py:task_span`; `src/agents/tracing.py:agent_span`).
3. `_run_agent_loop_impl` creates or resumes `RunState`, resolves max steps/turns/tool behavior/model settings/tool limits, creates `RunContextWrapper`, resolves verification runner, collects hooks and guardrails (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_context.py:RunContextWrapper`; `src/agents/run_state.py:RunState`).
4. For non-resume runs, the task is added to `AgentMemory`, input guardrails run, and repo context is built from the task and workspace (`src/agents/memory.py:AgentMemory.add_task`; `src/agents/run_loop.py:_run_input_guardrails`; `src/agents/repo_context.py:build_task_repo_context`).
5. Each loop turn checks `RunState.next_limit_reason`, opens `turn_span`, calls `prepare_turn_input`, prepends session messages, increments model turn count, and calls `run_model_turn` (`src/agents/run_state.py:RunState.next_limit_reason`; `src/agents/tracing.py:turn_span`; `src/agents/model_turn.py:prepare_turn_input`; `src/agents/run_state.py:RunState.record_model_turn`).
6. `process_model_turn` splits tool calls from handoff calls and may record plain text final output if no tools/handoffs exist (`src/agents/turn_resolution.py:process_model_turn`; `src/agents/turn_resolution.py:_plain_text_final_output`; `src/agents/run_recording.py:record_model_text_final_output`).
7. `build_tool_execution_plan` classifies actions into approved tools, handoffs, and pending approvals; pending approval wins before model response final/stop handling (`src/agents/tool_planning.py:build_tool_execution_plan`; `src/agents/run_loop.py:_tool_calls_selected_for_execution`; `src/agents/turn_resolution.py:resolve_pending_approval_step`).
8. If final output exists, output guardrails run before the loop exits; if a stop state or approval state exists, `record_run_stopped` records the reason and exits (`src/agents/run_loop.py:_run_output_guardrails`; `src/agents/run_recording.py:record_run_stopped`; `src/agents/turn_resolution.py:NextStepStopped`).
9. Tool calls execute sequentially; handoff calls delegate to a target agent and stop the current tool loop (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_steps.py:execute_handoff`; `src/agents/tool_execution.py:execute_tool_call`).
10. On exit, lifecycle `on_agent_end` emits, `build_run_result` creates `RunResult`, and session history is saved if `RunConfig.session` exists (`src/agents/lifecycle.py:emit_agent_end`; `src/agents/run_state.py:build_run_result`; `src/agents/run_loop.py:_build_result_and_save_session`).

## Local Coding CLI Setup

1. `parse_coding_cli_args` validates profile and positive numeric limits, then returns a frozen `CodingCliConfig` with task, workspace, model, session, trajectory, and test-command settings (`src/agents/coding_cli.py:parse_coding_cli_args`; `src/agents/coding_cli.py:CodingCliConfig`).
2. `build_coding_cli_setup` builds a `WorkspaceManifest` from CLI workspace/test-command flags, resolves a `CodingAgentProfile`, creates an `OpenAIResponsesModel`, and calls `build_coding_agent` (`src/agents/coding_cli.py:build_coding_cli_setup`; `src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/coding_agent.py:build_coding_agent`).
3. `build_coding_agent` validates manifest/workspace compatibility, builds or accepts a runtime `Workspace`, creates a `LocalEnvironment` when shell/test/edit capabilities require one, and stores workspace, selected files, and manifest in `RunConfig.context` (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/environment.py:LocalEnvironment`; `src/agents/run_context.py:CONTEXT_WORKSPACE_MANIFEST_KEY`).
4. Capability registration adds read tools for workspace read, shell/test tools for shell-test/edit-local profiles, and apply-patch for edit-local; shell/edit tools receive policy callables when approval is enabled (`src/agents/coding_agent.py:_register_capability_tools`; `src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/edit_tools.py:create_apply_patch_tool`).
5. After `Agent.run`, `_print_result` writes final output or pending approval summaries, `_exit_code_for_result` maps result state to `0`, `1`, or `2`, and `_write_trajectory_from_result` runs only when `--trajectory-jsonl` is set (`src/agents/coding_cli.py:_print_result`; `src/agents/coding_cli.py:_exit_code_for_result`; `src/agents/coding_cli.py:_write_trajectory_from_result`).

## Coding Tool Observation and Policy Flow

1. Shell command planning calls `ShellCommandPolicy.needs_approval` through the normal `FunctionTool.needs_approval` contract when profile approval is enabled (`src/agents/coding_policies.py:ShellCommandPolicy.needs_approval`; `src/agents/tool_runtime.py:requires_tool_approval`).
2. At handler time, `run_shell_command` classifies again and raises `ToolExecutionError` for blocked commands before `Environment.run` can execute shell text (`src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/coding_policies.py:ShellCommandPolicy.classify`; `src/agents/tools.py:ToolExecutionError`).
3. Successful or failed shell/test command execution becomes a `ToolObservation` via `command_result_observation`, then renders to stable text for model feedback (`src/agents/environment.py:CommandResult`; `src/agents/tool_observations.py:command_result_observation`; `src/agents/tool_observations.py:ToolObservation.to_text`).
4. Patch planning calls `PatchApprovalPolicy.needs_approval`; `dry_run=True` and invalid patch text are allowed to reach the parser, but valid write patches pause for approval before the tool writes files (`src/agents/coding_policies.py:PatchApprovalPolicy.needs_approval`; `src/agents/coding_policies.py:PatchApprovalPolicy.classify_patch_text`; `src/agents/edit_tools.py:create_apply_patch_tool`).
5. Patch results become `ToolObservation` values with dry-run, changed files, change count, and error count details before being returned to the runtime (`src/agents/tool_observations.py:patch_result_observation`; `src/agents/patches.py:PatchResult`).

## Trajectory JSONL Export

1. The local coding CLI accepts `--trajectory-jsonl PATH` and keeps the flag optional; runs without the flag do not write a trajectory (`src/agents/coding_cli.py:_build_parser`; `src/agents/coding_cli.py:CodingCliConfig`).
2. After the agent run completes and `_print_result` has written the user-visible output, the CLI builds trajectory events from the returned `RunResult` (`src/agents/coding_cli.py:run_coding_agent_cli`; `src/agents/coding_cli.py:_write_trajectory_from_result`).
3. `trajectory_events_from_result` prepends `run_started`, maps supported `RunItem` values, and ends with `final_output` or an evidence-backed `run_stopped` event (`src/agents/trajectory.py:trajectory_events_from_result`).
4. `write_trajectory_jsonl` writes one JSON object per line and creates parent directories, making the file usable as later debugging or eval evidence (`src/agents/trajectory.py:write_trajectory_jsonl`).
5. Smoke command from the package root: `python -m agents.coding_cli --workspace . --task "Inspect repository and summarize the current agent state." --trajectory-jsonl .agent/last.jsonl`.
6. Smoke evidence: inspect `.agent/last.jsonl`; the first line should have `event_type` `run_started`, runtime lines should reflect model/tool/approval/verification evidence that actually occurred, and the final line should be `final_output` or `run_stopped`.

## Model Turn

1. `prepare_turn_input` calls `build_turn_context` and obtains tool specs from `Agent._tool_specs_for_model` (`src/agents/model_turn.py:prepare_turn_input`; `src/agents/context_chunks.py:build_turn_context`; `src/agents/agent.py:Agent._tool_specs_for_model`).
2. `build_turn_context` orders system instructions, session summary, repo context, memory messages, and selected files by priority (`src/agents/context_chunks.py:ContextChunk`; `src/agents/context_chunks.py:sort_context_chunks`; `src/agents/context_chunks.py:build_turn_context`).
3. `run_model_turn` opens a `model_span`, emits `on_llm_start`, and calls `_run_model_turn_impl`; non-`ModelCallError` exceptions are wrapped in `ModelCallError` (`src/agents/model_turn.py:run_model_turn`; `src/agents/models.py:ModelCallError`; `src/agents/tracing.py:model_span`).
4. `_run_model_turn_impl` prefers `model.get_response` through `call_model_response`; otherwise it calls legacy `model.decide` and returns one optional `ToolCall` (`src/agents/model_turn.py:_run_model_turn_impl`; `src/agents/models.py:call_model_response`).
5. A `ModelResponse` is appended as `RunItem(item_type="model_response")`, structured final output is parsed when `Agent.output_type` exists, and returned `ModelTurnResult.tool_calls` feeds turn resolution (`src/agents/model_turn.py:_run_model_turn_impl`; `src/agents/output.py:set_structured_final_answer`; `src/agents/model_turn.py:ModelTurnResult`).

## Tool Execution

1. The run loop records each selected `ToolCall` as `RunItem(item_type="tool_call")` before execution (`src/agents/run_recording.py:record_tool_call`; `src/agents/contracts.py:ToolCall`).
2. `execute_tool_call` opens a `tool_span` and calls `_execute_tool_call_impl`; span arguments/output are omitted when `trace_include_sensitive_data` is false (`src/agents/tool_execution.py:execute_tool_call`; `src/agents/tracing.py:tool_span`).
3. `_execute_tool_call_impl` resolves the tool, checks `FunctionTool.is_enabled_for`, handles prior approval rejection, checks approval requirement, runs input guardrails, executes `ToolRegistry.execute`, interprets result, runs output guardrails, and records tool result metadata (`src/agents/tool_execution.py:_execute_tool_call_impl`; `src/agents/tools.py:ToolRegistry.execute`; `src/agents/tool_execution.py:interpret_tool_result`).
4. Tool outputs are converted to observations through `render_tool_observation`; code execution results use `CodeExecutionResult.output/logs/is_final_answer` conventions (`src/agents/tool_execution.py:render_tool_observation`; `src/agents/contracts.py:CodeExecutionResult`).
5. If the tool should stop and `finalize_output=True`, `record_final_output` mutates `RunState.final_answer` and appends a `final_output` item (`src/agents/tool_execution.py:should_stop_after_tool`; `src/agents/run_recording.py:record_final_output`).
6. Tool errors become `tool_error` items, failed observations are sent back to compatible models via `record_tool_output`, and memory gets an error `StepRecord` (`src/agents/tool_execution.py:record_tool_error`; `src/agents/tool_execution.py:record_tool_output`; `src/agents/memory.py:AgentMemory.add_step`).

## Tool Approval Pause and Resume

1. `build_tool_execution_plan` checks current approval status and `FunctionTool.requires_approval_for`; unknown required approvals are recorded before actual execution (`src/agents/tool_planning.py:_planning_approval_decision_for`; `src/agents/tool_planning.py:_record_planned_approval_required`).
2. Pending approvals add `ToolApprovalRequest` to `RunState.new_items`, mutate `RunContextWrapper._tool_approvals`, and add `ToolCall` values to the pending-call field on `RunState` (`src/agents/contracts.py:ToolApprovalRequest`; `src/agents/run_context.py:RunContextWrapper.request_tool_call_approval`; `src/agents/run_state.py:RunState`).
3. `RunResult.pending_approvals` reads approval requests from `new_items`; `RunResult.to_state` serializes approval snapshots from `RunContextWrapper` (`src/agents/result.py:RunResultBase.pending_approvals`; `src/agents/result.py:RunResultBase.to_state`; `src/agents/run_state.py:ApprovalSnapshot`).
4. A caller restores with `RunState.from_snapshot`, approves/rejects through `RunContextWrapper`, and calls `resume_agent_loop` (`src/agents/run_state.py:RunState.from_snapshot`; `src/agents/run_context.py:RunContextWrapper.approve_tool_call`; `src/agents/run_loop.py:resume_agent_loop`).
5. `resume_pending_tool_approvals` executes approved or rejected pending calls in order and leaves remaining pending calls for another pause (`src/agents/run_resume.py:resume_pending_tool_approvals`; `src/agents/run_resume.py:ResumeToolApprovalResult`).

## Guardrail and Policy Flow

1. Input guardrails are collected from agent then config and run only for non-resume runs before repo context/model calls (`src/agents/run_loop.py:_collect_input_guardrails`; `src/agents/run_loop.py:_run_input_guardrails`).
2. Input guardrail results are traced with `guardrail_span`, appended to `RunState.input_guardrail_results`, and recorded as `input_guardrail` items (`src/agents/run_loop.py:_run_input_guardrail_with_tracing`; `src/agents/run_loop.py:_record_input_guardrail_result`; `src/agents/tracing.py:guardrail_span`).
3. Output guardrails run on model/tool/handoff final output; tripwire clears final output and records `output_guardrail_triggered` (`src/agents/run_loop.py:_run_output_guardrails`; `src/agents/run_loop.py:_clear_final_output`; `src/agents/run_recording.py:record_run_stopped`).
4. Tool guardrails run inside `_execute_tool_call_impl`; `reject_content` produces a failed observation, while `raise_exception` raises `ToolInputGuardrailTripwireTriggered` or `ToolOutputGuardrailTripwireTriggered` (`src/agents/tool_execution.py:_execute_tool_call_impl`; `src/agents/tool_guardrails.py:ToolGuardrailFunctionOutput`; `src/agents/tool_guardrails.py:ToolGuardrailTripwireTriggered`).

## Session Save/Load

1. `RunConfig.session` implements `SessionLike`: `get_items`, `add_items`, `pop_item`, `clear_session` (`src/agents/run_config.py:SessionLike`; `src/agents/run_config.py:RunConfig`).
2. Before each model turn, `_session_messages` converts session items into `ChatMessage` and `_prepend_session_messages` places them before current turn input (`src/agents/run_loop.py:_session_messages`; `src/agents/run_loop.py:_prepend_session_messages`; `src/agents/memory.py:session_item_to_message`).
3. After a run, `_save_result_to_session` calls `RunResult.to_input_list()` and writes those messages into the configured session (`src/agents/run_loop.py:_save_result_to_session`; `src/agents/result.py:RunResultBase.to_input_list`; `src/agents/memory.py:session_items_from_result`).
4. In-memory `AgentSession` stores turns and steps; disk `JsonSession` loads/saves a versioned JSON envelope with `session.to_dict()` (`src/agents/memory.py:AgentSession`; `src/agents/memory.py:JsonSession._load`; `src/agents/memory.py:JsonSession._save`).

## Memory Compaction

1. `AgentSession.__post_init__`, `add_task`, and `add_step` all trim turns/steps, sync flattened `steps`, and call `_compact_if_needed` (`src/agents/memory.py:AgentSession.__post_init__`; `src/agents/memory.py:AgentSession.add_step`; `src/agents/memory.py:AgentSession._compact_if_needed`).
2. `MemoryCompressor.should_compact` uses `CompactionPolicy.compact_after_turns`; `split_turns` keeps at least one recent turn and usually `keep_recent_turns` (`src/agents/memory.py:CompactionPolicy`; `src/agents/memory.py:MemoryCompressor.should_compact`; `src/agents/memory.py:MemoryCompressor.split_turns`).
3. `RuleBasedSummarizer` creates sectioned summaries from compact text; `ModelSummarizer` calls OpenAI and falls back to rule-based summarization on exception or empty output (`src/agents/memory.py:RuleBasedSummarizer`; `src/agents/memory.py:ModelSummarizer.summarize`).
4. `build_turn_context` renders `MemorySummary.to_message()` before raw memory messages and removes duplicate summary message from memory replay (`src/agents/context_chunks.py:_memory_summary_message`; `src/agents/context_chunks.py:_memory_messages_without_summary`).

## Tracing and Export

1. `run_agent_loop` opens a trace unless disabled or an existing current trace should be reused (`src/agents/run_loop.py:run_agent_loop`; `src/agents/tracing.py:trace`; `src/agents/tracing.py:get_current_trace`).
2. `Trace` and `Span` store `TraceRecord` and `SpanRecord`; current trace/span live in contextvars (`src/agents/tracing.py:Trace`; `src/agents/tracing.py:Span`; `src/agents/tracing.py:_current_trace`; `src/agents/tracing.py:_current_span`).
3. Span constructors encode runtime data for agent, task, turn, generation/model, tool/function, guardrail, and handoff events (`src/agents/tracing.py:agent_span`; `src/agents/tracing.py:turn_span`; `src/agents/tracing.py:model_span`; `src/agents/tracing.py:tool_span`; `src/agents/tracing.py:handoff_span`).
4. Global processors are managed through `add_trace_processor`, `set_trace_processors`, and `SynchronousMultiTracingProcessor`; exporters include memory and JSONL (`src/agents/tracing.py:add_trace_processor`; `src/agents/tracing.py:SynchronousMultiTracingProcessor`; `src/agents/tracing.py:InMemoryTracingExporter`; `src/agents/tracing.py:JSONLTracingExporter`).
5. Processor exceptions are swallowed by `_forward`, so tracing must not be used as a correctness side channel (`src/agents/tracing.py:SynchronousMultiTracingProcessor._forward`).

## Selected Files and Repo Context Assembly

1. `build_coding_agent` puts `Workspace` and an empty `SelectedFilesState` into `RunConfig.context` (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/run_context.py:CONTEXT_WORKSPACE_KEY`; `src/agents/run_context.py:CONTEXT_SELECTED_FILES_KEY`).
2. Before the first model turn, `build_task_repo_context` builds `WorkspaceInventory`, resolves file/symbol mentions, adds mentioned paths to `SelectedFilesState`, creates `RepoContext`, and writes it into context (`src/agents/repo_context.py:build_task_repo_context`; `src/agents/workspace_inventory.py:build_workspace_inventory`; `src/agents/context_mentions.py:resolve_mentions_against_inventory`; `src/agents/selected_files.py:SelectedFilesState.add_mentions`).
3. `RepoContextBuilder` creates sections for inventory, selected files, mentioned paths, mentioned symbols, and workspace code matches; `_limit_context` marks truncation if max chars are exceeded (`src/agents/repo_context.py:RepoContextBuilder.build`; `src/agents/repo_context.py:_limit_context`).
4. `build_turn_context` renders repo context as a user message before memory, and selected files as a later user message (`src/agents/context_chunks.py:_repo_context_chunk`; `src/agents/context_chunks.py:_selected_files_context_chunk`; `src/agents/context_chunks.py:build_turn_context`).

## Error Handling

- Model call errors are wrapped/formatted through `ModelCallError`, traced on the model span, recorded as `model_error`, and break the loop (`src/agents/model_turn.py:run_model_turn`; `src/agents/models.py:ModelCallError`; `src/agents/run_recording.py:record_model_error`; `src/agents/run_loop.py:_run_agent_loop_impl`).
- Tool execution exceptions are traced and converted to `tool_error` items by the run loop except tool guardrail tripwires, which are re-raised (`src/agents/tool_execution.py:execute_tool_call`; `src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/tool_execution.py:record_tool_error`).
- Workspace path violations raise `WorkspacePathError`; workspace tools either propagate as tool errors or return structured UTF-8 decode errors (`src/agents/workspace.py:WorkspacePathError`; `src/agents/workspace_tools.py:create_read_workspace_file_tool`; `src/agents/workspace_code.py:WorkspaceCodeReader.read_lines`).
- Verification failures are not exceptions; they become `verification_result` with metadata `passed=False` and memory observations (`src/agents/verification.py:VerificationResult`; `src/agents/run_recording.py:run_verification_after_tool`).

## Tests and Verification Map

- Core loop and state machine: `tests/test_runner.py`, `tests/test_run_steps.py`, `tests/test_run_state.py`.
- Models and output: `tests/test_models.py`, `tests/test_output.py`, `tests/test_code_execution.py`.
- Tools, approvals, guardrails: `tests/test_tools.py`, `tests/test_tool_execution_plan.py`, `tests/test_tool_approval_runtime.py`, `tests/test_tool_approval_pause.py`, `tests/test_guardrails.py`, `tests/test_tool_guardrails.py`.
- Workspace/context: `tests/test_workspace.py`, `tests/test_workspace_tools.py`, `tests/test_workspace_inventory.py`, `tests/test_workspace_code.py`, `tests/test_workspace_code_tools.py`, `tests/test_context_mentions.py`, `tests/test_selected_files.py`, `tests/test_repo_context.py`, `tests/test_context_chunks.py`.
- Memory/session/chat: `tests/test_memory.py`, `tests/test_session_memory_example.py`, `tests/test_chat.py`, `tests/test_basic_chat_example.py`.
- Tracing/lifecycle/verification: `tests/test_tracing.py`, `tests/test_lifecycle.py`, `tests/test_verification.py`, `tests/test_verification_loop.py`.
