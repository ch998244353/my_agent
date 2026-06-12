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
