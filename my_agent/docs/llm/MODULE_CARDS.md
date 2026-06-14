# Module Cards

## Public API and Agent Construction

- Purpose: define the public import surface and create configured `Agent` objects for direct, chat, mini-code, and coding-agent workflows (`src/agents/__init__.py:__all__`; `src/agents/agent.py:Agent`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:build_chat_runtime`).
- Owned files: `src/agents/__init__.py`, `src/agents/agent.py`, `src/agents/agents.py`, `src/agents/coding_agent.py`, `src/agents/chat_runtime.py`.
- Public surface: `Agent`, `AgentCapabilities`, `Agent.as_tool`, `Agent.for_code`, `build_coding_agent`, `CodingAgentProfile`, `WorkspaceManifest`, `ToolObservation`, coding policy classes, trajectory helpers, `ChatRuntimeConfig`, `ChatRuntime` (`src/agents/agent.py:AgentCapabilities`; `src/agents/agent.py:Agent.as_tool`; `src/agents/coding_agent.py:CodingAgentProfile`; `src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/tool_observations.py:ToolObservation`; `src/agents/chat_runtime.py:ChatRuntimeConfig`).
- Depends on: memory, model settings, output schema, tools, handoffs, guardrails, run config, workspace, environment (`src/agents/agent.py:Agent.__post_init__`; `src/agents/coding_agent.py:_register_capability_tools`).
- Called by: examples, chat CLI/runtime, tests, and user code through package root (`src/agents/chat_cli.py:main`; `examples/coding_agent_profile.py`; `tests/test_public_api.py`).
- State: `Agent` owns mutable `memory` and `tool_registry`; `CodingAgentSetup` carries `agent`, `run_config`, `workspace`, `environment` (`src/agents/agent.py:Agent`; `src/agents/coding_agent.py:CodingAgentSetup`).
- Contracts: `Agent.__post_init__` registers default final-answer and python executor tools as capabilities require; structured output schema mutates compatible model fields (`src/agents/agent.py:Agent._prepare_tools`; `src/agents/agent.py:Agent._prepare_output_schema`).
- Common edit reasons: new capability pack, new default tool, new public export, new model/output config (`src/agents/coding_agent.py:DEFAULT_CAPABILITY_PACKS`; `src/agents/__init__.py:__all__`).
- Risks: `Agent.clone` shallow-copies list fields but shares memory/model/tool registry unless overridden; changing public exports needs tests (`src/agents/agent.py:Agent.clone`; `tests/test_public_api.py`).
- Evidence: `src/agents/agent.py:Agent`, `src/agents/coding_agent.py:build_coding_agent`, `src/agents/__init__.py:__all__`.

## Coding CLI Entrypoint

- Purpose: bind local CLI arguments to `CodingAgentSetup`, run one coding task, print a concise result, and return shell-friendly exit codes (`src/agents/coding_cli.py:run_coding_agent_cli`; `src/agents/coding_cli.py:_exit_code_for_result`).
- Owned files: `src/agents/coding_cli.py`, `examples/local_coding_cli.py`, `tests/test_coding_cli.py`.
- Public surface: `CodingCliConfig`, `parse_coding_cli_args`, `build_coding_cli_setup`, `run_coding_agent_cli` through package root lazy exports; config includes workspace/profile/model limits, `--session-json`, `--trajectory-jsonl`, and test-command defaults (`src/agents/coding_cli.py:CodingCliConfig`; `src/agents/__init__.py:__getattr__`).
- Depends on: `build_coding_agent`, `CodingAgentProfile`, `WorkspaceManifest`, `OpenAIResponsesModel`, `JsonSession`, trajectory helpers, and `Agent.run` (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/trajectory.py:write_trajectory_jsonl`; `src/agents/agent.py:Agent.run`).
- Called by: `python -m agents.coding_cli`, package-root imports, and the local smoke example (`src/agents/coding_cli.py:main`; `examples/local_coding_cli.py:build_example_command`).
- State: no persistent process state of its own; it may pass `JsonSession` into `RunConfig.session` and writes a trajectory file only when `--trajectory-jsonl` is provided (`src/agents/coding_cli.py:build_coding_cli_setup`; `src/agents/coding_cli.py:_write_trajectory_from_result`; `src/agents/memory.py:JsonSession`).
- Contracts: exit code `0` means final output, `1` means error or stopped without output, and `2` means pending approval (`src/agents/coding_cli.py:_exit_code_for_result`; `src/agents/result.py:RunResultBase.pending_approval_summaries`).
- Common edit reasons: new CLI flags, profile selection, result printing, package-root export behavior, or module smoke testing (`src/agents/coding_cli.py:_build_parser`; `tests/test_coding_cli.py`).
- Risks: package-root eager imports of `agents.coding_cli` create `python -m agents.coding_cli` runpy warnings, so exports are lazy through `__getattr__` (`src/agents/__init__.py:__getattr__`).
- Evidence: `tests/test_coding_cli.py`, `tests/test_public_api.py`.

## Workspace Manifest and Coding Policies

- Purpose: make local coding workspace and safety policy explicit before tools execute (`src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/coding_policies.py:ShellCommandPolicy`; `src/agents/coding_policies.py:PatchApprovalPolicy`).
- Owned files: `src/agents/workspace_manifest.py`, `src/agents/coding_policies.py`, plus wiring in `src/agents/coding_agent.py`, `src/agents/coding_cli.py`, `src/agents/shell_tools.py`, and `src/agents/edit_tools.py`.
- Public surface: `WorkspaceManifest`, `SafetyDecision`, `ShellCommandPolicy`, `PatchApprovalPolicy` (`src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/coding_policies.py:SafetyDecision`).
- Depends on: `Workspace` for path safety, patch parsing for edit classification, and `FunctionTool.needs_approval` callables for pause decisions (`src/agents/workspace.py:Workspace`; `src/agents/patches.py:parse_patch`; `src/agents/tool_runtime.py:requires_tool_approval`).
- Called by: `build_coding_agent`, CLI setup, shell command tools, and patch tools (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/coding_cli.py:build_coding_cli_setup`; `src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/edit_tools.py:create_apply_patch_tool`).
- State: immutable dataclasses only; manifest stores user-facing root/path/test-command/env policy and builds runtime `Workspace` objects on demand (`src/agents/workspace_manifest.py:WorkspaceManifest.build_workspace`).
- Contracts: manifest metadata is JSON-safe, `allowed_test_commands` includes the default test command, shell policy returns allow/approve/block, and write patches require approval while dry-run patches can validate without pausing (`src/agents/workspace_manifest.py:WorkspaceManifest.metadata`; `src/agents/coding_policies.py:ShellCommandPolicy.classify`; `src/agents/coding_policies.py:PatchApprovalPolicy.classify_patch_text`).
- Common edit reasons: add manifest fields, change test command defaults, alter shell safety prefixes, or tune patch approval thresholds (`tests/test_workspace_manifest.py`; `tests/test_coding_policies.py`).
- Risks: shell classification is conservative string/prefix matching rather than a shell AST; blocked fragments must stay synchronized with Windows and POSIX destructive patterns (`src/agents/coding_policies.py:ShellCommandPolicy.blocked_fragments`).
- Evidence: `tests/test_workspace_manifest.py`, `tests/test_coding_policies.py`, `tests/test_coding_agent_profile.py`, `tests/test_shell_tools.py`, `tests/test_edit_tools.py`.

## Tool Observation Rendering

- Purpose: standardize model-visible shell/test/patch results as structured observations with stable text rendering (`src/agents/tool_observations.py:ToolObservation`; `src/agents/tool_observations.py:command_result_observation`; `src/agents/tool_observations.py:patch_result_observation`).
- Owned files: `src/agents/tool_observations.py`; integrations live in `src/agents/environment.py`, `src/agents/shell_tools.py`, and `src/agents/edit_tools.py`.
- Public surface: `ToolObservation`, `command_result_observation`, `patch_result_observation` (`src/agents/tool_observations.py:__all__`).
- Depends on: `CommandResult`, `PatchResult`, and `clip_tool_text` (`src/agents/environment.py:CommandResult`; `src/agents/patches.py:PatchResult`; `src/agents/tool_runtime.py:clip_tool_text`).
- Called by: `CommandResult.to_observation`, shell/test command tools, and apply-patch tool (`src/agents/environment.py:CommandResult.to_observation`; `src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/edit_tools.py:create_apply_patch_tool`).
- State: no mutable state; observations are frozen dataclasses and render to JSON-safe dicts or stable text blocks (`src/agents/tool_observations.py:ToolObservation.to_dict`; `src/agents/tool_observations.py:ToolObservation.to_text`).
- Contracts: text starts with `Tool observation`, includes `tool`, `status`, `summary`, `details`, and `output`, and can mark truncation when output is clipped (`src/agents/tool_observations.py:ToolObservation.to_text`).
- Common edit reasons: add observation fields, change max-output behavior, or map new tool result types into trajectory-friendly details (`tests/test_tool_observations.py`; `tests/test_shell_tools.py`; `tests/test_edit_tools.py`).
- Risks: trajectory or model memory may persist command output; callers must still treat observations as potentially sensitive run evidence (`src/agents/tool_observations.py:_command_output`; `src/agents/trajectory.py:_normalize_trajectory_payload`).
- Evidence: `tests/test_tool_observations.py`, `tests/test_environment.py`, `tests/test_shell_tools.py`, `tests/test_edit_tools.py`.

## Trajectory JSONL Evidence

- Purpose: convert a completed or paused `RunResult` into a plain JSONL audit file for debugging, teaching, and future eval stages (`src/agents/trajectory.py:trajectory_events_from_result`; `src/agents/trajectory.py:write_trajectory_jsonl`).
- Owned files: `src/agents/trajectory.py`, `tests/test_trajectory.py`; CLI integration lives in `src/agents/coding_cli.py`.
- Public surface: `TrajectoryEvent`, `trajectory_events_from_result`, and `write_trajectory_jsonl` are exported from the package root (`src/agents/trajectory.py:TrajectoryEvent`; `src/agents/__init__.py:__all__`).
- Depends on: `RunResult`, `RunItem`, dataclass/path/exception normalization, and explicit CLI metadata (`src/agents/result.py:RunResult`; `src/agents/contracts.py:RunItem`; `src/agents/trajectory.py:_normalize_trajectory_payload`).
- Called by: `python -m agents.coding_cli --trajectory-jsonl ...` and any direct user code that wants run evidence without enabling tracing (`src/agents/coding_cli.py:_write_trajectory_from_result`).
- State: no mutable runtime state; writer creates or appends to the target JSONL file only when called (`src/agents/trajectory.py:write_trajectory_jsonl`).
- Contracts: trajectory is separate from tracing; it records supported runtime evidence, derives approval rejection from metadata, and preserves unknown run items as `runtime_item` (`src/agents/trajectory.py:_event_type_from_run_item`; `src/agents/trajectory.py:_event_from_run_item`).
- Common edit reasons: new `RunItem.item_type` values, new summary fields, or later inspector/eval readers (`src/agents/contracts.py:RunItem`; `src/agents/trajectory.py:_result_summary_payload`).
- Risks: payloads are normalized for JSON safety, but business-sensitive tool outputs may still appear because trajectory is user-facing run evidence; callers should choose file location accordingly (`src/agents/trajectory.py:_normalize_trajectory_payload`).
- Evidence: `tests/test_trajectory.py`, `tests/test_coding_cli.py`, `tests/test_public_api.py`.

## Chat Runtime and CLI

- Purpose: provide a minimal interactive chat wrapper over `Agent` plus optional session persistence (`src/agents/chat.py:run_chat_turn`; `src/agents/chat_runtime.py:ChatRuntime.run_turn`; `src/agents/chat_cli.py:run_chat_cli`).
- Owned files: `src/agents/chat.py`, `src/agents/chat_runtime.py`, `src/agents/chat_cli.py`.
- Public surface: `ChatTurn`, `ChatDiagnostics`, `chat_turn_from_result`, `ChatRuntimeConfig`, `build_chat_runtime`, `run_chat_cli` (`src/agents/chat.py:ChatTurn`; `src/agents/chat_runtime.py:build_chat_runtime`; `src/agents/chat_cli.py:main`).
- Depends on: `Runner.run_sync`, `RunConfig`, `AgentSession`/`JsonSession`, `OpenAIResponsesModel` (`src/agents/chat.py:run_chat_turn`; `src/agents/chat_runtime.py:build_chat_agent`; `src/agents/chat_runtime.py:build_chat_session`).
- Called by: CLI and package root exports (`src/agents/chat_cli.py:main`; `src/agents/__init__.py:run_chat_cli`).
- State: `ChatRuntime` tracks `last_turn` and `turn_count`; session state is delegated to `SessionLike` (`src/agents/chat_runtime.py:ChatRuntime`).
- Contracts: runtime session overrides `RunConfig.session` when present; `max_turns` from runtime overrides config if set (`src/agents/chat_runtime.py:_effective_run_config`; `src/agents/chat_runtime.py:_resolve_run_session`; `src/agents/chat_runtime.py:_resolve_run_max_turns`).
- Common edit reasons: CLI command changes, chat status mapping, session mode changes (`src/agents/chat_cli.py:_handle_chat_command`; `src/agents/chat.py:chat_turn_status_text`).
- Risks: no `pyproject.toml` console script is defined; CLI entry is `python -m agents.chat_cli` or direct module execution, NEEDS_VERIFICATION for packaging (`pyproject.toml`; `src/agents/chat_cli.py:main`).
- Evidence: `tests/test_chat.py`, `tests/test_basic_chat_example.py`.

## Run Loop and Turn State Machine

- Purpose: orchestrate a full synchronous agent run from task text to `RunResult` (`src/agents/run_loop.py:run_agent_loop`; `src/agents/run_loop.py:_run_agent_loop_impl`).
- Owned files: `src/agents/runner.py`, `src/agents/run_loop.py`, `src/agents/turn_resolution.py`, `src/agents/run_steps.py`.
- Public surface: `Runner.run_sync`, `run_agent_loop`, `resume_agent_loop`, `ProcessedResponse`, `SingleStepResult`, `NextStepFinalOutput`, `NextStepRunAgain`, `NextStepHandoff`, `NextStepPendingApproval`, `NextStepStopped` (`src/agents/runner.py:Runner.run_sync`; `src/agents/turn_resolution.py:NextStepPendingApproval`).
- Depends on: model turns, tool planning/execution, guardrails, repo context, state/result builders, lifecycle, tracing, verification (`src/agents/run_loop.py:_run_agent_loop_impl`).
- Called by: `Agent.run`, `Agent._run`, `Runner.run_sync`, chat helpers, handoff target agents (`src/agents/agent.py:Agent.run`; `src/agents/chat.py:run_chat_turn`; `src/agents/run_steps.py:_execute_handoff_impl`).
- State: mutates `RunState.input`, `last_agent`, `current_turn`, `steps_taken`, `new_items`, guardrail result lists, `final_answer`, `pending_tool_calls` (`src/agents/run_state.py:RunState`; `src/agents/run_loop.py:_run_agent_loop_impl`).
- Contracts: input guardrails run once only for non-resume runs; repo context builds only for non-resume runs; pending approvals are processed before the main loop on resume (`src/agents/run_loop.py:_run_input_guardrails`; `src/agents/run_loop.py:build_task_repo_context`; `src/agents/run_loop.py:resume_pending_tool_approvals`).
- Common edit reasons: stop reasons, max-turn/max-step semantics, output guardrail timing, approval pause/resume behavior (`src/agents/run_state.py:RunState.next_limit_reason`; `src/agents/run_loop.py:_clear_final_output`).
- Risks: `ToolGuardrailTripwireTriggered` is re-raised from tool loop and not converted to `RunResult` there; caller behavior is exception-based (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/tool_guardrails.py:ToolGuardrailTripwireTriggered`).
- Evidence: `tests/test_runner.py`, `tests/test_run_steps.py`, `tests/test_tool_approval_pause.py`.

## Model Calls and Output Parsing

- Purpose: prepare per-turn messages/tools/settings, call a model adapter, normalize responses, and detect structured/plain final output (`src/agents/model_turn.py:prepare_turn_input`; `src/agents/model_turn.py:run_model_turn`; `src/agents/models.py:OpenAIResponsesModel`; `src/agents/output.py:set_structured_final_answer`).
- Owned files: `src/agents/model_turn.py`, `src/agents/models.py`, `src/agents/model_settings.py`, `src/agents/output.py`.
- Public surface: `ModelAdapter`, `OpenAIResponsesModel`, `ResponseStatePolicy`, `ModelSettings`, model error classes, `output_schema_from_output_type`, `parse_structured_output` (`src/agents/models.py:ModelAdapter`; `src/agents/models.py:ResponseStatePolicy`; `src/agents/model_settings.py:ModelSettings`).
- Depends on: context chunks, contracts, tool specs, model settings, output schema/validation (`src/agents/model_turn.py:prepare_turn_input`; `src/agents/models.py:build_response_request_kwargs`).
- Called by: `run_model_turn`, tests, chat runtime through default model (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/chat_runtime.py:build_chat_agent`).
- State: `OpenAIResponsesModel` stores `last_response`, `last_input`, `last_tools`, `previous_response_id`, `pending_tool_outputs`, request summaries (`src/agents/models.py:OpenAIResponsesModel`).
- Contracts: `call_model_response` passes `model_settings` only if adapter accepts it; `OpenAIResponsesModel.record_tool_output` queues `function_call_output` input for next request; successful response clears pending tool outputs (`src/agents/models.py:call_model_response`; `src/agents/models.py:OpenAIResponsesModel.record_tool_output`; `src/agents/models.py:OpenAIResponsesModel.get_response`).
- Common edit reasons: model settings fields, OpenAI request kwargs, structured output validation, function-call parsing (`src/agents/models.py:_apply_model_settings`; `src/agents/models.py:function_call_item_to_tool_call`).
- Risks: real OpenAI API compatibility is NEEDS_VERIFICATION; default model string is code-defined and not externally validated (`src/agents/models.py:OpenAIResponsesModel`).
- Evidence: `tests/test_models.py`, `tests/test_output.py`, `tests/test_code_execution.py`.

## Tool Registry, Planning, Execution, and Approval

- Purpose: define tools, expose schemas to the model, validate/execute tool calls, pause on approvals, record observations, and decide whether tool output ends the run (`src/agents/tools.py:FunctionTool`; `src/agents/tool_planning.py:build_tool_execution_plan`; `src/agents/tool_execution.py:execute_tool_call`).
- Owned files: `src/agents/tools.py`, `src/agents/tool_schema.py`, `src/agents/tool_runtime.py`, `src/agents/tool_planning.py`, `src/agents/tool_execution.py`, `src/agents/run_resume.py`.
- Public surface: `function_tool`, `FunctionTool`, `ToolRegistry`, `ToolExecutionLimits`, `ToolApprovalDecision`, `ToolExecutionPlan`, `ToolExecutionOutcome`, `resume_pending_tool_approvals` (`src/agents/tools.py:function_tool`; `src/agents/tool_runtime.py:ToolExecutionLimits`; `src/agents/run_resume.py:resume_pending_tool_approvals`).
- Depends on: contracts, guardrails, timeout runtime, lifecycle, tracing, model `record_tool_output`, `RunContextWrapper` approvals (`src/agents/tool_execution.py:_execute_tool_call_impl`; `src/agents/tool_runtime.py:requires_tool_approval`).
- Called by: `run_loop` and resume path (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_resume.py:resume_pending_tool_approvals`).
- State: writes `RunItem` types `tool_call`, `tool_result`, `tool_error`, `tool_approval_required`; mutates `RunState.steps_taken`, `pending_tool_calls`, `context_wrapper._tool_approvals`, and `AgentMemory.steps` (`src/agents/tool_execution.py:record_tool_approval_required`; `src/agents/tool_planning.py:_record_planned_approval_required`; `src/agents/tool_execution.py:record_tool_error`).
- Contracts: `tool_use_behavior` supports `"run_llm_again"`, `"stop_on_first_tool"`, or `{"stop_at_tool_names": [...]}`; invalid values raise `ValueError` (`src/agents/tool_execution.py:should_stop_after_tool`).
- Common edit reasons: new approval behavior, timeout/output clipping, argument validation, final-answer tool semantics (`src/agents/tool_runtime.py:validate_tool_arguments`; `src/agents/tools.py:create_final_answer_tool`; `src/agents/tool_execution.py:is_final_answer_result`).
- Risks: approval checks happen both in planning and execution; keep `build_tool_execution_plan` and `_execute_tool_call_impl` in sync (`src/agents/tool_planning.py:_planning_approval_decision_for`; `src/agents/tool_execution.py:_execute_tool_call_impl`).
- Evidence: `tests/test_tools.py`, `tests/test_tool_execution_plan.py`, `tests/test_tool_approval_runtime.py`, `tests/test_tool_approval_pause.py`.

## Guardrails and Policy

- Purpose: implement input/output guardrails for agent-level text/final output and tool-level input/output behavior (`src/agents/guardrails.py:InputGuardrail`; `src/agents/tool_guardrails.py:ToolInputGuardrail`).
- Owned files: `src/agents/guardrails.py`, `src/agents/tool_guardrails.py`.
- Public surface: `input_guardrail`, `output_guardrail`, `GuardrailFunctionOutput`, `ToolGuardrailFunctionOutput.allow`, `reject_content`, `raise_exception` (`src/agents/guardrails.py:input_guardrail`; `src/agents/tool_guardrails.py:ToolGuardrailFunctionOutput`).
- Depends on: `RunContextWrapper`, `Agent`, `ToolCall`, run loop recording (`src/agents/guardrails.py:InputGuardrail.run`; `src/agents/tool_guardrails.py:ToolOutputGuardrail.run`; `src/agents/run_recording.py:record_tool_output_guardrail`).
- Called by: run loop for input/output; tool execution for tool guardrails (`src/agents/run_loop.py:_run_input_guardrails`; `src/agents/run_loop.py:_run_output_guardrails`; `src/agents/tool_execution.py:_execute_tool_call_impl`).
- State: appends guardrail result objects to `RunState` lists and `RunItem` entries (`src/agents/run_loop.py:_record_input_guardrail_result`; `src/agents/run_recording.py:record_tool_input_guardrail`).
- Contracts: agent output guardrail trip clears final output and records stop reason; tool guardrail `reject_content` becomes a model-visible failed observation, while `raise_exception` raises a tripwire exception (`src/agents/run_loop.py:_run_output_guardrails`; `src/agents/tool_execution.py:_execute_tool_call_impl`).
- Common edit reasons: new policy behavior, dynamic enablement, result metadata (`src/agents/tool_guardrails.py:_is_enabled_for`; `src/agents/run_recording.py:record_tool_input_guardrail`).
- Risks: agent-level input guardrail call order in `InputGuardrail.run` is `(context, agent, input)` but method signature is `(agent, agent_input, context)`; callers must use the wrapper, not call raw functions inconsistently (`src/agents/guardrails.py:InputGuardrail.run`; `src/agents/run_loop.py:_run_input_guardrail_with_tracing`).
- Evidence: `tests/test_guardrails.py`, `tests/test_tool_guardrails.py`.

## Workspace, Selected Files, and Repo Context

- Purpose: provide bounded file access and assemble model-visible repository context from workspace inventory, file mentions, selected files, and code search (`src/agents/workspace.py:Workspace`; `src/agents/repo_context.py:build_task_repo_context`; `src/agents/context_chunks.py:build_turn_context`).
- Owned files: `src/agents/workspace.py`, `src/agents/workspace_manifest.py`, `src/agents/workspace_tools.py`, `src/agents/workspace_inventory.py`, `src/agents/workspace_code.py`, `src/agents/workspace_code_tools.py`, `src/agents/context_mentions.py`, `src/agents/selected_files.py`, `src/agents/repo_context.py`, `src/agents/context_chunks.py`.
- Public surface: `Workspace`, `WorkspaceManifest`, `WorkspaceFileEntry`, `WorkspaceInventory`, `WorkspaceCodeReader`, `SelectedFile`, `SelectedFilesState`, `RepoContext`, `RepoContextBuilder`, readonly workspace tools (`src/agents/workspace.py:Workspace`; `src/agents/workspace_manifest.py:WorkspaceManifest`; `src/agents/workspace_code.py:WorkspaceCodeReader`; `src/agents/selected_files.py:SelectedFilesState`; `src/agents/repo_context.py:RepoContextBuilder`).
- Depends on: filesystem, AST for Python outlines, workspace policy, run context keys (`src/agents/workspace_code.py:WorkspaceCodeReader.outline_file`; `src/agents/run_context.py:CONTEXT_WORKSPACE_KEY`).
- Called by: coding-agent builder, run loop before first model turn, model context construction, workspace tools (`src/agents/coding_agent.py:build_coding_agent`; `src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/workspace_tools.py:create_readonly_workspace_tools`).
- State: `SelectedFilesState` mutates in place inside `RunConfig.context`; `RepoContext` is written back to `context_wrapper.context[CONTEXT_REPO_CONTEXT_KEY]` (`src/agents/selected_files.py:SelectedFilesState.add_file`; `src/agents/repo_context.py:build_task_repo_context`).
- Contracts: selected files promote from read-only to editable but do not downgrade editable entries; repo context sections are priority-sorted and may be truncated by max chars (`src/agents/selected_files.py:SelectedFilesState.add_file`; `src/agents/repo_context.py:RepoContext.ordered_sections`; `src/agents/repo_context.py:_limit_context`).
- Common edit reasons: new context chunk, mention detection heuristic, workspace tool, inventory policy (`src/agents/context_mentions.py:detect_file_mentions`; `src/agents/context_chunks.py:build_turn_context`).
- Risks: search is literal substring and inventory-limited; no semantic parser beyond Python AST outlines (`src/agents/workspace_code.py:WorkspaceCodeReader.search_text`; `src/agents/workspace_code.py:WorkspaceCodeReader.outline_file`).
- Evidence: `tests/test_workspace.py`, `tests/test_workspace_tools.py`, `tests/test_workspace_inventory.py`, `tests/test_workspace_code.py`, `tests/test_selected_files.py`, `tests/test_repo_context.py`, `tests/test_context_chunks.py`.

## Shell, Edit, and Code Tools

- Purpose: expose environment commands, test commands, file patching, and code-reading/search tools as `FunctionTool` objects (`src/agents/shell_tools.py:create_shell_command_tool`; `src/agents/edit_tools.py:create_apply_patch_tool`; `src/agents/workspace_code_tools.py:create_workspace_code_tools`).
- Owned files: `src/agents/environment.py`, `src/agents/shell_tools.py`, `src/agents/edit_tools.py`, `src/agents/patches.py`, `src/agents/workspace_code_tools.py`.
- Public surface: `Environment`, `LocalEnvironment`, `CommandResult`, `create_shell_command_tool`, `create_test_command_tool`, `create_apply_patch_tool`, `ToolObservation`, patch dataclasses (`src/agents/environment.py:Environment`; `src/agents/environment.py:LocalEnvironment`; `src/agents/tool_observations.py:ToolObservation`; `src/agents/patches.py:PatchResult`).
- Depends on: `Workspace` for cwd/path bounds, `ToolApproval`, `FunctionTool`, `ShellCommandPolicy`, `PatchApprovalPolicy`, structured observation helpers, subprocess, patch parser (`src/agents/environment.py:LocalEnvironment._resolve_cwd`; `src/agents/coding_policies.py:ShellCommandPolicy`; `src/agents/coding_policies.py:PatchApprovalPolicy`; `src/agents/tool_observations.py:command_result_observation`; `src/agents/patches.py:parse_patch`).
- Called by: coding-agent capability packs and direct examples (`src/agents/coding_agent.py:_register_shell_tools`; `src/agents/coding_agent.py:_register_edit_tools`; `examples/shell_and_test_tools_agent.py`).
- State: shell tools do not mutate Python state except via environment side effects; patch tool writes/deletes files when `dry_run=False` (`src/agents/environment.py:LocalEnvironment.run`; `src/agents/patches.py:apply_patch`).
- Contracts: test commands are allowlisted; patch paths cannot be absolute and must pass `Workspace.ensure_readable_path`; dry-run patch validation may run without approval, but actual patch writes are approval-gated before the patch handler writes files (`src/agents/shell_tools.py:create_test_command_tool`; `src/agents/patches.py:validate_patch_paths`; `src/agents/coding_policies.py:PatchApprovalPolicy`).
- Common edit reasons: command allowlist semantics, patch format, workspace read tool signatures (`src/agents/shell_tools.py:DEFAULT_TEST_COMMAND`; `src/agents/workspace_code_tools.py:create_search_workspace_code_tool`).
- Risks: `LocalEnvironment.run` uses `shell=True`; patch hunk matching is text-based and first-match only (`src/agents/environment.py:LocalEnvironment.run`; `src/agents/patches.py:_apply_update_content`).
- Evidence: `tests/test_shell_tools.py`, `tests/test_edit_tools.py`, `tests/test_patches.py`, `tests/test_workspace_code_tools.py`.

## Memory, Sessions, and Compaction

- Purpose: convert task/tool history into model messages, persist chat history, and compact old turns into summaries (`src/agents/memory.py:AgentMemory`; `src/agents/memory.py:AgentSession`; `src/agents/memory.py:MemoryCompressor`; `src/agents/memory.py:JsonSession`).
- Owned files: `src/agents/memory.py`.
- Public surface: `AgentMemory`, `AgentSession`, `SessionTurn`, `MemorySummary`, `CompactionPolicy`, `MemoryCompressor`, `RuleBasedSummarizer`, `ModelSummarizer`, `JsonSession` (`src/agents/memory.py:CompactionPolicy`; `src/agents/memory.py:ModelSummarizer`).
- Depends on: contracts, python executor tool name, optional OpenAI client in `ModelSummarizer` (`src/agents/memory.py:session_item_to_message`; `src/agents/memory.py:ModelSummarizer._client`).
- Called by: `Agent._messages_for_model`, context chunks, run loop session save/load, chat runtime (`src/agents/agent.py:Agent._messages_for_model`; `src/agents/context_chunks.py:_memory_summary_message`; `src/agents/run_loop.py:_session_messages`).
- State: `AgentMemory.task/steps`; `AgentSession.turns/summary`; `JsonSession.path` on disk (`src/agents/memory.py:AgentMemory`; `src/agents/memory.py:AgentSession`; `src/agents/memory.py:JsonSession._save`).
- Contracts: `AgentSession.add_items` opens new turns on user messages and stores non-user messages as `StepRecord.messages`; compaction is triggered in post-init and after task/step additions (`src/agents/memory.py:AgentSession.add_items`; `src/agents/memory.py:AgentSession._compact_if_needed`).
- Common edit reasons: session serialization schema, compaction policy, summary model prompt (`src/agents/memory.py:AgentSession.to_dict`; `src/agents/memory.py:CompactionPolicy`; `src/agents/memory.py:ModelSummarizer._system_prompt`).
- Risks: `JsonSession` writes JSON with version 1 but no migration framework beyond permissive load; `ModelSummarizer` swallows exceptions and falls back (`src/agents/memory.py:JsonSession._load`; `src/agents/memory.py:ModelSummarizer.summarize`).
- Evidence: `tests/test_memory.py`, `tests/test_session_memory_example.py`.

## Handoffs

- Purpose: expose handoff target agents as model-callable tools and run delegated target agents (`src/agents/handoffs.py:handoff_tool_specs`; `src/agents/run_steps.py:execute_handoff`).
- Owned files: `src/agents/handoffs.py`, `src/agents/run_steps.py`.
- Public surface: `handoff_tool_name`, `handoff_tool_specs`, `handoff_target_for`, `HandoffOutcome` (`src/agents/handoffs.py:handoff_tool_name`; `src/agents/run_steps.py:HandoffOutcome`).
- Depends on: `Agent.handoffs`, tool specs, lifecycle hooks, tracing, `target_agent.run` (`src/agents/agent.py:Agent._handoff_tool_specs`; `src/agents/run_steps.py:_execute_handoff_impl`).
- Called by: model tool spec assembly and run loop (`src/agents/agent.py:Agent._tool_specs_for_model`; `src/agents/run_loop.py:_run_agent_loop_impl`).
- State: parent `RunState` records a handoff `RunItem`, may copy target final answer into parent final output, and appends parent memory observation (`src/agents/run_steps.py:_execute_handoff_impl`).
- Contracts: handoff tool name is `transfer_to_` plus normalized target name; task arg defaults to parent memory task if missing (`src/agents/handoffs.py:HANDOFF_TOOL_PREFIX`; `src/agents/run_steps.py:execute_handoff`).
- Common edit reasons: context propagation, nested tracing, target run config (`src/agents/run_steps.py:_execute_handoff_impl`).
- Risks: parent `RunConfig` is not passed to target run; context/session propagation is UNKNOWN (`src/agents/run_steps.py:_execute_handoff_impl`).
- Evidence: `tests/test_handoffs.py`.

## Tracing, Lifecycle, and Observability

- Purpose: emit structured trace/span records and optional lifecycle callbacks around agent, model, tool, handoff, guardrail, and error events (`src/agents/tracing.py:Trace`; `src/agents/lifecycle.py:LifecycleHooks`).
- Owned files: `src/agents/tracing.py`, `src/agents/lifecycle.py`.
- Public surface: `trace`, `span`, `agent_span`, `turn_span`, `model_span`, `tool_span`, `guardrail_span`, `handoff_span`, processors/exporters, `LifecycleHooks` (`src/agents/tracing.py:trace`; `src/agents/tracing.py:BatchTraceProcessor`; `src/agents/lifecycle.py:emit_agent_start`).
- Depends on: contextvars, JSONL file writing, runtime callers setting data fields (`src/agents/tracing.py:_current_trace`; `src/agents/tracing.py:JSONLTracingExporter`).
- Called by: run loop, model turn, tool execution, handoff execution, guardrail wrappers (`src/agents/run_loop.py:run_agent_loop`; `src/agents/model_turn.py:run_model_turn`; `src/agents/tool_execution.py:execute_tool_call`; `src/agents/run_steps.py:execute_handoff`).
- State: global `_multi_processor` list, contextvars for current trace/span, exporter queues (`src/agents/tracing.py:_multi_processor`; `src/agents/tracing.py:_current_trace`; `src/agents/tracing.py:BatchTraceProcessor`).
- Contracts: nested calls use `trace(..., only_if_missing=True)` to avoid replacing an existing trace; `trace_include_sensitive_data` gates model/tool input capture in run loop callers (`src/agents/run_loop.py:run_agent_loop`; `src/agents/model_turn.py:run_model_turn`; `src/agents/tool_execution.py:execute_tool_call`).
- Common edit reasons: new span types, exporter behavior, sensitive-data filtering (`src/agents/tracing.py:function_span`; `src/agents/tracing.py:record_span_error`).
- Risks: `SynchronousMultiTracingProcessor._forward` swallows processor exceptions; observability failure is silent (`src/agents/tracing.py:SynchronousMultiTracingProcessor._forward`).
- Evidence: `tests/test_tracing.py`, `tests/test_lifecycle.py`.

## Result, State, and Serialization

- Purpose: store run progress and convert final state into user-visible `RunResult` plus serializable snapshots (`src/agents/run_state.py:RunState`; `src/agents/result.py:RunResultBase`; `src/agents/contracts.py:RunItem`).
- Owned files: `src/agents/contracts.py`, `src/agents/run_state.py`, `src/agents/result.py`, `src/agents/run_resume.py`.
- Public surface: `ChatMessage`, `ToolSpec`, `ToolCall`, `ToolApprovalRequest`, `ModelResponse`, `RunItem`, `StepRecord`, `RunState`, `RunStateSnapshot`, `RunResult`, `ApprovalSnapshot` (`src/agents/contracts.py:ToolCall`; `src/agents/run_state.py:RunStateSnapshot`; `src/agents/result.py:RunResult`).
- Depends on: guardrail result classes, verification result, run context approvals (`src/agents/result.py:_verification_summary_from_items`; `src/agents/result.py:RunResultBase.to_state`).
- Called by: all runtime modules via contracts and builders (`src/agents/run_state.py:build_run_result`; `src/agents/run_recording.py:record_final_output`).
- State: `RunState` is mutable in-run; `RunResult` is frozen and snapshots state at build time (`src/agents/run_state.py:RunState`; `src/agents/result.py:RunResult`).
- Contracts: `RunResult.final_output` aliases `final_answer`; `to_state` serializes approvals from current context wrapper and `new_items` payloads through helper converters (`src/agents/result.py:RunResultBase.final_output`; `src/agents/result.py:RunResultBase.to_state`).
- Common edit reasons: new `RunItem.item_type`, new result summary properties, resumable state (`src/agents/contracts.py:RunItem`; `src/agents/result.py:RunResultBase.pending_approvals`; `src/agents/run_state.py:RunState.from_snapshot`).
- Risks: payload serialization is shallow for unknown payloads; non-JSON-safe payloads can survive in `new_items` state (`src/agents/result.py:_run_item_payload_to_state`).
- Evidence: `tests/test_contracts.py`, `tests/test_result.py`, `tests/test_run_state.py`, `tests/test_run_context_approvals.py`.

## Verification and Tests

- Purpose: optionally run configured verification commands after selected tools and expose result summaries (`src/agents/verification.py:VerificationPolicy`; `src/agents/run_recording.py:run_verification_after_tool`; `src/agents/result.py:RunResultBase.verification_summary`).
- Owned files: `src/agents/verification.py`, verification paths in `src/agents/run_recording.py`.
- Public surface: `VerificationPolicy`, `VerificationResult`, `VerificationRunner` (`src/agents/verification.py:VerificationPolicy`; `src/agents/verification.py:VerificationRunner`).
- Depends on: `Environment.run`, `CommandResult`, tool names, `RunState.new_items` (`src/agents/verification.py:VerificationRunner.run`; `src/agents/run_recording.py:_verification_attempts_taken`).
- Called by: run loop after each executed non-handoff tool (`src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/run_recording.py:run_verification_after_tool`).
- State: writes `verification_result` or `verification_skipped` items and memory observations (`src/agents/run_recording.py:run_verification_after_tool`; `src/agents/run_recording.py:_record_verification_skipped`).
- Contracts: verification runs only if policy exists, runner exists, commands enabled, triggering tool is in `auto_after_tools`, and max attempts not reached (`src/agents/verification.py:VerificationPolicy.should_run_after_tool`; `src/agents/run_recording.py:run_verification_after_tool`).
- Common edit reasons: auto-verification triggers, max-attempt logic, output clipping (`src/agents/verification.py:VerificationResult.to_observation`).
- Risks: verification commands run through `Environment`, so shell and workspace risks apply (`src/agents/verification.py:VerificationRunner.run`; `src/agents/environment.py:LocalEnvironment.run`).
- Evidence: `tests/test_verification.py`, `tests/test_verification_loop.py`.
