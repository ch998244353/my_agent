# Maintenance Log

## Purpose

This file tracks LLM-facing architecture documentation extraction and updates. Use it to preserve what was inspected, what changed, and what remains uncertain.

## Update Rule

When production code, public exports, runtime flow, state contracts, tool behavior, workspace context, sessions, tracing, verification, or tests change, update the relevant `docs/llm/*` file and append an entry here. Code is authoritative over these docs.

## Initial Extraction - 2026-06-12T02:59:07.9934369+08:00

- Source commit: f79ea5bf607765886311fa6ed6184cf7ccc90715.
- Codegraph action: initial index reported 86 Python files and missed tracked modules such as `src/agents/selected_files.py`; ran `codegraph index --force .`; final status is 98 Python files, 2507 nodes, 6634 edges.
- Files created: `AGENTS.md`, `docs/llm/ARCHITECTURE_INDEX.md`, `docs/llm/MODULE_CARDS.md`, `docs/llm/RUNTIME_FLOWS.md`, `docs/llm/SYMBOL_MAP.md`, `docs/llm/STATE_AND_CONTRACTS.md`, `docs/llm/MAINTENANCE_LOG.md`.
- Production code edits: none.
- Verification: citation check parsed 691 `path:symbol` references with 0 missing symbols; `python -m pytest` passed 496 tests in 3.17s on Windows/Python 3.14.2.

## Inspected Files

- Config and package: `pyproject.toml`; `src/agents/__init__.py:__all__`.
- Public construction: `src/agents/agent.py:Agent`; `src/agents/agents.py`; `src/agents/coding_agent.py:build_coding_agent`; `src/agents/chat_runtime.py:build_chat_runtime`.
- Run spine: `src/agents/runner.py:Runner.run_sync`; `src/agents/run_loop.py:run_agent_loop`; `src/agents/run_loop.py:_run_agent_loop_impl`; `src/agents/turn_resolution.py:process_model_turn`; `src/agents/run_steps.py:execute_handoff`.
- Model/output: `src/agents/model_turn.py:prepare_turn_input`; `src/agents/model_turn.py:run_model_turn`; `src/agents/models.py:OpenAIResponsesModel`; `src/agents/output.py:set_structured_final_answer`; `src/agents/model_settings.py:ModelSettings`.
- Tools/policy: `src/agents/tools.py:FunctionTool`; `src/agents/tool_runtime.py:ToolExecutionLimits`; `src/agents/tool_planning.py:build_tool_execution_plan`; `src/agents/tool_execution.py:execute_tool_call`; `src/agents/run_resume.py:resume_pending_tool_approvals`.
- Guardrails: `src/agents/guardrails.py:InputGuardrail`; `src/agents/tool_guardrails.py:ToolInputGuardrail`.
- Workspace/context: `src/agents/workspace.py:Workspace`; `src/agents/workspace_tools.py:create_readonly_workspace_tools`; `src/agents/workspace_inventory.py:build_workspace_inventory`; `src/agents/workspace_code.py:WorkspaceCodeReader`; `src/agents/workspace_code_tools.py:create_workspace_code_tools`; `src/agents/context_mentions.py:resolve_mentions_against_inventory`; `src/agents/selected_files.py:SelectedFilesState`; `src/agents/repo_context.py:build_task_repo_context`; `src/agents/context_chunks.py:build_turn_context`.
- Memory/session/chat: `src/agents/memory.py:AgentMemory`; `src/agents/memory.py:AgentSession`; `src/agents/memory.py:JsonSession`; `src/agents/chat.py:run_chat_turn`; `src/agents/chat_cli.py:main`.
- Observability/verification: `src/agents/tracing.py:Trace`; `src/agents/tracing.py:Span`; `src/agents/lifecycle.py:LifecycleHooks`; `src/agents/verification.py:VerificationPolicy`; `src/agents/run_recording.py:run_verification_after_tool`.
- Tests inventoried: all files under `tests/` returned by `rg --files`, including core, models, tools, workspace/context, memory/chat, tracing, and verification suites.

## Known Gaps

- Real OpenAI Responses API behavior and default model string were not externally verified; docs only cite repo code (`src/agents/models.py:OpenAIResponsesModel`).
- Handoff propagation of parent `RunConfig`, context, sessions, and tracing metadata is UNKNOWN because `target_agent.run(task)` is called without config (`src/agents/run_steps.py:_execute_handoff_impl`).
- Hook exception behavior is NEEDS_VERIFICATION; lifecycle emitters do not catch exceptions in inspected code (`src/agents/lifecycle.py:emit_error`).
- Some source comments are mojibake/Chinese-encoded; architecture docs rely on code symbols and behavior, not comment text.

## Coding CLI Entrypoint - 2026-06-12T22:25:00+08:00

- Production code edits: added a local coding CLI module entrypoint, lazy package-root exports for coding CLI symbols, and a smoke example command builder (`src/agents/coding_cli.py`; `src/agents/__init__.py`; `examples/local_coding_cli.py`).
- Documentation updates: registered the coding CLI in `ARCHITECTURE_INDEX.md`, `MODULE_CARDS.md`, and `SYMBOL_MAP.md`.
- Verification: `python -m pytest tests/test_coding_cli.py -v` passed 17 tests; `python -m pytest tests/test_coding_agent_profile.py tests/test_public_api.py -v` passed 30 tests and 255 subtests on Windows/Python 3.14.2.
- Known follow-up: no console script is defined in `pyproject.toml`; the supported entry remains `python -m agents.coding_cli`.

## PLAN02 Workspace Manifest - 2026-06-14T20:10:00+08:00

- Production code edits: added `WorkspaceManifest`, manifest-aware `build_coding_agent` setup, manifest metadata, and CLI construction from workspace/test command arguments (`src/agents/workspace_manifest.py`; `src/agents/coding_agent.py`; `src/agents/coding_cli.py`).
- Documentation updates: documented manifest ownership through the coding-agent setup, run context key, and workspace/test command contracts (`docs/llm/MODULE_CARDS.md`; `docs/llm/STATE_AND_CONTRACTS.md`; `docs/llm/SYMBOL_MAP.md`).
- Verification: run `python -m pytest tests/test_workspace_manifest.py tests/test_coding_agent_profile.py tests/test_coding_cli.py -v` after changing manifest behavior.

## PLAN03 Structured Tool Observations - 2026-06-14T20:15:00+08:00

- Production code edits: added stable `ToolObservation` rendering for shell/test command results and patch results, including status, summary, details, output, changed files, errors, and truncation metadata (`src/agents/tool_observations.py`; `src/agents/shell_tools.py`; `src/agents/edit_tools.py`).
- Documentation updates: documented shell/edit/code tools as structured observation producers and kept tool execution observation contracts in the runtime docs (`docs/llm/MODULE_CARDS.md`; `docs/llm/STATE_AND_CONTRACTS.md`).
- Verification: run `python -m pytest tests/test_tool_observations.py tests/test_shell_tools.py tests/test_edit_tools.py -v` after changing observation fields.

## PLAN04 Approval-Gated Shell And Edit Tools - 2026-06-14T20:20:00+08:00

- Production code edits: added shell command classification, patch approval classification, policy wiring through coding-agent capability registration, and approval pause/resume coverage through existing `RunState` APIs (`src/agents/coding_policies.py`; `src/agents/coding_agent.py`; `src/agents/tool_planning.py`; `src/agents/tool_execution.py`; `src/agents/run_resume.py`).
- Behavior update: valid `dry_run=False` patch writes now require approval before execution, including small add/update patches; `dry_run=True` validation still runs without approval, and invalid patch text is allowed to reach the patch parser for normal structured errors (`src/agents/coding_policies.py:PatchApprovalPolicy.classify_patch_text`).
- Documentation updates: clarified the edit approval contract in `STATE_AND_CONTRACTS.md` and the shell/edit tool card in `MODULE_CARDS.md`.
- Verification: run `python -m pytest tests/test_coding_policies.py tests/test_tool_approval_pause.py tests/test_tool_approval_runtime.py tests/test_coding_agent_profile.py -v` after changing approval policy or resume behavior.

## Trajectory JSONL Evidence - 2026-06-14T20:30:00+08:00

- Production code edits: exported `TrajectoryEvent`, `trajectory_events_from_result`, and `write_trajectory_jsonl` from the package root after adding the trajectory writer and coding CLI flag (`src/agents/__init__.py`; `src/agents/trajectory.py`; `src/agents/coding_cli.py`).
- Documentation updates: documented trajectory event types and contracts in `STATE_AND_CONTRACTS.md`, added CLI smoke instructions to `RUNTIME_FLOWS.md`, and registered the trajectory module card in `MODULE_CARDS.md`.
- Smoke command: `python -m agents.coding_cli --workspace . --task "Inspect repository and summarize the current agent state." --trajectory-jsonl .agent/last.jsonl`.
- Verification: run `python -m pytest tests/test_trajectory.py tests/test_coding_cli.py tests/test_result.py tests/test_run_state.py tests/test_public_api.py -v` after changing trajectory exports or event mapping.

## Latest Git Node Docs Refresh - 2026-06-15T03:57:00+08:00

- Source state: current git node `fb21d5c22be9e41aaae79af6ee8a10cf9488c34e`, compared against previous node `10ff882d29b22cb97401a3b907f7dc9990f7f9aa`.
- Codegraph action: ran `codegraph index --force .` for `my_agent` and checked the refreshed graph; status is 109 Python files, 2778 nodes, and 7308 edges.
- Diff evidence: inspected `git diff HEAD~1 HEAD` and CodeGraph context/explore output for the upgraded local coding CLI, `WorkspaceManifest`, coding policies, structured tool observations, trajectory JSONL, approval pause/resume restoration, and package exports.
- Documentation updates: refreshed `ARCHITECTURE_INDEX.md`, `MODULE_CARDS.md`, `RUNTIME_FLOWS.md`, `STATE_AND_CONTRACTS.md`, and `SYMBOL_MAP.md` so the current node's structure is represented in metadata, subsystem ownership, runtime flows, state contracts, symbol lookup, invariants, risks, and test anchors.
- Boundary correction: `WorkspaceManifest` and trajectory helpers are package-root exports; `ToolObservation` and coding policy classes are module-level surfaces in the current code and should not be described as package-root exports unless `src/agents/__init__.py` changes.
- Production code edits: none in this docs refresh.
- Verification: `git diff --check -- docs/llm` passed; no runtime tests were run because only LLM-facing Markdown documentation was edited.

## PLAN01 Snapshot State Contract - 2026-06-15T23:44:39+08:00

- Diff evidence: inspected `git diff -- my_agent/src/agents/run_state.py my_agent/src/agents/result.py my_agent/src/agents/contracts.py my_agent/src/agents/run_context.py my_agent/tests/test_run_state.py my_agent/tests/test_result.py my_agent/tests/test_run_context_approvals.py` before updating docs.
- Production code changes documented: added `RUN_STATE_SNAPSHOT_SCHEMA_VERSION`, `run_state_snapshot_to_dict`, `run_state_snapshot_from_dict`, schema-version validation, JSON-safe payload normalization through `_json_safe_value`, pending approval fallback export in `RunResult.to_state`, and restored-state approval helpers `RunState.approve_tool_call` / `RunState.reject_tool_call`.
- Documentation updates: synchronized snapshot/resume contracts in `STATE_AND_CONTRACTS.md`, pending approval flow in `RUNTIME_FLOWS.md`, result/state module card in `MODULE_CARDS.md`, symbol lookup in `SYMBOL_MAP.md`, and architecture invariants/risks in `ARCHITECTURE_INDEX.md`.
- Boundary: PLAN01 still does not add CLI state-file flags, state file writing, user approve/reject input parsing, shell/edit policy changes, or trajectory behavior changes.
- Verification: run `python -m pytest tests/test_run_state.py tests/test_result.py tests/test_run_context_approvals.py -q` and `python -m pytest tests/test_tool_approval_pause.py -q` after changing snapshot/resume state contracts.

## PLAN02 CLI Pending State Persistence - 2026-06-16T15:14:00+08:00

- Diff evidence: inspected `git diff -- my_agent/src/agents/coding_state.py my_agent/src/agents/coding_cli.py my_agent/tests/test_coding_state.py my_agent/tests/test_coding_cli.py`; `coding_state.py` and `test_coding_state.py` were untracked at inspection time, so their current full file contents were inspected directly.
- Production code changes documented: added `--state-json`, `CodingCliConfig.state_json`, `_save_pending_state_from_result`, pending output messages for saved/not-saved state, and `CodingRunStateStore` / `CodingRunStateEnvelope` for writing and reading CLI state envelopes.
- State envelope contract: records envelope version, task, workspace root, profile name, resolved model, workspace manifest metadata, optional session/trajectory paths, nested `RunResult.to_state()` snapshot dict, and structured `pending_approvals` with `tool_name`, `call_id`, `arguments`, and `reason`.
- Security boundary: state files store manifest env key names only, not env values, API keys, or raw `RunConfig.context` objects.
- Documentation updates: synchronized CLI state-file contracts in `STATE_AND_CONTRACTS.md`, pending state runtime flow in `RUNTIME_FLOWS.md`, module ownership in `MODULE_CARDS.md`, symbol lookup in `SYMBOL_MAP.md`, and architecture summary/invariants/risks in `ARCHITECTURE_INDEX.md`.
- Boundary: PLAN02 only saves pending state. It does not parse approve/reject commands, call `resume_agent_loop`, or change trajectory JSONL event format.
- Verification: `python -m pytest my_agent\tests\test_coding_state.py my_agent\tests\test_coding_cli.py -q`; `git diff --check -- my_agent/docs/llm my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/tests/test_coding_cli.py my_agent/tests/test_coding_state.py`.

## PLAN03 CLI Approve/Reject Resume Docs Sync - 2026-06-19T22:05:00+08:00

- Diff evidence: inspected `git diff --name-only -- my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/run_context.py my_agent/src/agents/run_loop.py my_agent/src/agents/run_resume.py my_agent/tests/test_coding_cli.py my_agent/tests/test_tool_approval_pause.py` and `git diff --unified=0 -- my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/run_context.py my_agent/src/agents/run_loop.py my_agent/tests/test_coding_cli.py my_agent/tests/test_tool_approval_pause.py`.
- Diff summary: current PLAN03 code diff is concentrated in `src/agents/coding_cli.py`, `tests/test_coding_cli.py`, and `tests/test_tool_approval_pause.py`; `coding_state.py`, `run_context.py`, `run_loop.py`, and `run_resume.py` are consumed by the new flow but were not changed in this PLAN03 diff.
- Production code changes documented: added `ApprovalDecision`, `--resume-state`, `--approve`, `--reject`, `--rejection-reason`, `--approve-all`, state-envelope config restoration, `RunState.from_snapshot` restoration with `RunContextWrapper`, approval decision application, `previous_response_id` restoration, no-decision pending display, `resume_agent_loop` CLI entry, and state rewrite/delete lifecycle helpers.
- Behavior boundary: approve decisions execute the approved pending tool through the normal resume path; reject decisions record a rejected approval observation and do not call the rejected tool handler; `--resume-state` with no decision prints pending approvals and keeps the state file.
- State lifecycle: fresh pending runs write state only with `--state-json`; resumed pending runs rewrite the same state path; resumed completed runs delete the consumed state file.
- Documentation updates: synchronized approve/reject resume contracts in `STATE_AND_CONTRACTS.md`, full fresh pending -> state save -> approve/reject resume -> resumed run flow in `RUNTIME_FLOWS.md`, module ownership in `MODULE_CARDS.md`, symbol lookup in `SYMBOL_MAP.md`, and architecture summary/invariants/risks in `ARCHITECTURE_INDEX.md`.
- Verification commands for behavior: `python -m pytest my_agent\tests\test_coding_cli.py my_agent\tests\test_tool_approval_pause.py -q`; after documentation edits, run `git diff --check -- my_agent/docs/llm my_agent/src/agents/coding_cli.py my_agent/tests/test_coding_cli.py my_agent/tests/test_tool_approval_pause.py`.

## PLAN04 Approval Risk Summaries Docs Sync - 2026-06-19T23:10:00+08:00

- Diff evidence: inspected `git diff -- my_agent/src/agents/approval_summaries.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/tests/test_coding_cli.py my_agent/tests/test_coding_state.py` as required by PLAN04. Because `approval_summaries.py`, `coding_state.py`, `test_approval_summaries.py`, and `test_coding_state.py` were untracked at inspection time, also inspected `git status --short -- PLAN.md my_agent/src/agents/approval_summaries.py my_agent/src/agents/result.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/tests/test_approval_summaries.py my_agent/tests/test_result.py my_agent/tests/test_coding_cli.py my_agent/tests/test_coding_state.py my_agent/docs/llm`, full current contents of `approval_summaries.py` and `coding_state.py`, and `git diff -- my_agent/src/agents/result.py my_agent/tests/test_result.py`.
- Key code changes from diff/files: added `approval_summary_for_tool_call`, shell/test command summaries, patch summaries with `dry_run`, `changed_paths`, and `operations`, invalid patch marker `patch parse failed before approval summary`, `PendingApprovalSummary.summary`, `RunResult.pending_approval_summaries` integration, CLI pending output that prints `summary`, and state `pending_approvals[].summary` save/load support (`src/agents/approval_summaries.py`; `src/agents/result.py`; `src/agents/coding_cli.py`; `src/agents/coding_state.py`).
- Test evidence documented: `tests/test_approval_summaries.py` covers shell, patch, and invalid patch summaries; `tests/test_result.py` covers result-layer summary propagation; `tests/test_coding_cli.py` covers CLI printing and saved state summaries; `tests/test_coding_state.py` covers state envelope save/load with `summary`.
- Documentation updates: synchronized approval summary field sources and security boundaries in `STATE_AND_CONTRACTS.md`, pending output flow in `RUNTIME_FLOWS.md`, helper ownership in `MODULE_CARDS.md`, lookup entries in `SYMBOL_MAP.md`, and architecture summary/invariants/risks in `ARCHITECTURE_INDEX.md`.
- Behavior boundary: PLAN04 does not change shell/patch approval policy, does not execute patch dry-run for summaries, does not add an interactive confirmation UI, and does not store env values. Invalid patch summary text must not cause pending display or pending state saving to fail.
- Verification commands for behavior/docs: `python -m pytest my_agent\tests\test_approval_summaries.py my_agent\tests\test_result.py my_agent\tests\test_coding_state.py my_agent\tests\test_coding_cli.py -q`; `git diff --check -- my_agent/docs/llm my_agent/src/agents/approval_summaries.py my_agent/src/agents/result.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/tests/test_approval_summaries.py my_agent/tests/test_result.py my_agent/tests/test_coding_cli.py my_agent/tests/test_coding_state.py`.

## PLAN05 Verification CLI Loop Docs Sync - 2026-06-20T03:20:00+08:00

- Diff evidence: inspected `git diff -- my_agent/src/agents/coding_cli.py my_agent/src/agents/verification.py my_agent/src/agents/run_loop.py my_agent/tests/test_verification.py my_agent/tests/test_verification_loop.py my_agent/tests/test_coding_cli.py`, plus matching `--name-only` and `--stat` summaries before editing docs.
- Diff summary: current PLAN05 code/test diff is concentrated in `src/agents/coding_cli.py`, `tests/test_coding_cli.py`, and `tests/test_verification_loop.py`; `src/agents/verification.py`, `src/agents/run_loop.py`, and `tests/test_verification.py` are consumed by the documented flow but were not changed in this diff.
- Key code changes from diff: added CLI verification fields and flags (`--verify-command`, `--verify-after-tool`, `--verify-max-attempts`, `--verify-output-chars`), mapped verification CLI config into `VerificationPolicy`, appended explicit verification commands to manifest test-command allowlist, printed `RunResult.verification_summary`, preserved pending approval exit code `2`, and added fake-environment tests proving failed verification reaches the next model turn.
- Documentation updates: synchronized verification CLI parameter/config contracts in `STATE_AND_CONTRACTS.md`, added tool execution -> verification -> failure feedback -> next model turn flow in `RUNTIME_FLOWS.md`, updated `coding_cli.py` and verification module cards in `MODULE_CARDS.md`, added CLI verification lookup symbols and test anchors in `SYMBOL_MAP.md`, and marked CLI verification loop status plus PLAN06 trajectory boundary in `ARCHITECTURE_INDEX.md`.
- Behavior boundary: no verification parameters means no automatic verification because `RunConfig.verification` remains unset; verification failure is observation and run evidence, not an exception termination mechanism; this PLAN does not implement lint parsing, test selection, Git checkpointing, or the full PLAN06 trajectory evidence chain.
- Verification commands for behavior/docs: `python -m pytest my_agent\tests\test_coding_cli.py -q --basetemp my_agent\.tmp\pytest-docs-coding-cli`; `python -m pytest my_agent\tests\test_verification.py my_agent\tests\test_verification_loop.py -q --basetemp my_agent\.tmp\pytest-docs-verification`; `git diff --check -- my_agent/docs/llm my_agent/src/agents/coding_cli.py my_agent/tests/test_coding_cli.py my_agent/tests/test_verification_loop.py`.

## PLAN06 Trajectory Evidence Chain Docs Sync - 2026-06-20T17:46:11+08:00

- Diff evidence: inspected `git diff -- my_agent/src/agents/trajectory.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/verification.py my_agent/tests/test_trajectory.py my_agent/tests/test_coding_cli.py`, plus matching `--stat` and `--name-only` summaries before editing docs.
- Diff summary: current PLAN06 code/test diff is concentrated in `src/agents/trajectory.py`, `src/agents/coding_cli.py`, `tests/test_trajectory.py`, and `tests/test_coding_cli.py`; `src/agents/coding_state.py` and `src/agents/verification.py` are consumed by the documented flow but were not changed in this diff.
- Key code changes from diff: added `state_saved_event`, `resume_started_event`, `approval_decision_event`, trajectory append mode for resume, stable resume `run_id` reuse, saved-state evidence after pending state writes, approval decision evidence before resumed execution, and terminal payload `verification_summary` with clipped `last_observation`.
- Documentation updates: synchronized trajectory event fields and resume-state boundaries in `STATE_AND_CONTRACTS.md`, fresh/resume JSONL write order in `RUNTIME_FLOWS.md`, CLI and trajectory module cards in `MODULE_CARDS.md`, symbol lookup and test anchors in `SYMBOL_MAP.md`, and architecture summary/invariants/verification notes in `ARCHITECTURE_INDEX.md`.
- Behavior boundary: trajectory JSONL is an evidence chain only. Resume still loads the CLI state envelope from `--resume-state`; trajectory is not replayed, parsed, or used as the source for rebuilding `RunState`.
- Verification commands for behavior/docs: `python -m pytest my_agent\tests\test_trajectory.py my_agent\tests\test_coding_cli.py -q`; `git diff --check -- my_agent/docs/llm my_agent/src/agents/trajectory.py my_agent/src/agents/coding_cli.py my_agent/tests/test_trajectory.py my_agent/tests/test_coding_cli.py`.
