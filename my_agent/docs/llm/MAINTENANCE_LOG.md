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

## Scheme A Docs Refresh - 2026-06-14T21:43:07+08:00

- Source state: HEAD `10ff882d29b22cb97401a3b907f7dc9990f7f9aa` with working-tree changes under `my_agent` and `teach`; git status showed Scheme A source/test additions plus prior `docs/llm` edits.
- Codegraph action: queried latest project-scoped codegraph for `my_agent`; status is 109 Python files, 2770 nodes, and 7087 edges.
- Plan evidence: inspected `teach/PLAN01.md` through `teach/PLAN05.md`; the plans describe local coding CLI, workspace manifest, structured tool observations, approval-gated shell/edit tools, and trajectory JSONL.
- Documentation updates: refreshed `ARCHITECTURE_INDEX.md`, `MODULE_CARDS.md`, `RUNTIME_FLOWS.md`, `STATE_AND_CONTRACTS.md`, and `SYMBOL_MAP.md` so the Scheme A modules are represented in metadata, subsystem ownership, runtime flows, state contracts, symbol lookup, invariants, risks, and test anchors.
- Production code edits: none in this docs refresh.
- Verification: `git diff --check -- my_agent/docs/llm` passed; no runtime tests were run because only LLM-facing Markdown documentation was edited.
