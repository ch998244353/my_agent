# MY_AGENT Architecture Baseline

## 1. Project positioning

`my_agent` is a small synchronous Python package for building an Agents SDK-style coding agent. The package metadata in `pyproject.toml` names it `my-agent`, requires Python `>=3.10`, and depends on `openai>=2.31.0`.

The runtime is centered on configurable `Agent` objects (`src/agents/agent.py:Agent`) that delegate runs to `Runner.run_sync()` (`src/agents/runner.py:Runner.run_sync`) and then to `run_agent_loop()` (`src/agents/run_loop.py:run_agent_loop`). The coding-agent layer is not a separate server or daemon. It is a builder/profile layer (`src/agents/coding_agent.py:CodingAgentProfile`, `src/agents/coding_agent.py:build_coding_agent`) plus a local CLI (`src/agents/coding_cli.py:run_coding_agent_cli`) over the same core runtime.

The project is currently best described as a teaching-grade but functional coding-agent baseline:

- It has model calls, tool calls, file read/search, patch editing, shell/test tools, approval pause/resume, session memory, verification feedback, tracing, and trajectory JSONL evidence.
- It does not have a planner/executor split, async or parallel tool execution, semantic RAG, a persistent in-runtime symbol index, Git checkpointing, issue/PR integration, or a mature autonomous multi-iteration coding loop.

Codegraph status for this baseline: the `my_agent` project graph indexes 113 Python files, 2936 nodes, and 7734 edges. Existing analysis documents read before this report: all files under `docs/llm/`.

## 2. Directory structure

Top-level project layout:

- `pyproject.toml`: package metadata and dependency declaration.
- `src/agents/`: runtime package. This is the only production code package.
- `examples/`: usage examples for chat, coding-agent profiles, workspace read/edit, shell/test tools, local CLI, and session compaction.
- `tests/`: unit tests covering runtime, models, tools, workspace, memory, tracing, CLI, verification, and trajectory behavior.
- `docs/llm/`: existing LLM-facing architecture documents.
- `docs/upgrade_plan/`: upgrade-planning documents. This report creates the first baseline file here.
- `.codegraph/`: external analysis index used by CodeGraph, not part of runtime behavior.

The main production modules under `src/agents/` group into:

- Public API and agent construction: `__init__.py`, `agent.py`, `agents.py`, `coding_agent.py`, `chat_runtime.py`.
- Runtime loop and state: `runner.py`, `run_loop.py`, `run_state.py`, `result.py`, `contracts.py`, `turn_resolution.py`, `run_steps.py`, `run_resume.py`, `run_recording.py`.
- Model calls: `model_turn.py`, `models.py`, `model_settings.py`, `output.py`.
- Tools: `tools.py`, `tool_runtime.py`, `tool_schema.py`, `tool_planning.py`, `tool_execution.py`, `tool_observations.py`, `tool_guardrails.py`.
- Coding/workspace tools: `workspace.py`, `workspace_manifest.py`, `workspace_inventory.py`, `workspace_tools.py`, `workspace_code.py`, `workspace_code_tools.py`, `selected_files.py`, `context_mentions.py`, `repo_context.py`, `context_chunks.py`, `shell_tools.py`, `edit_tools.py`, `patches.py`, `environment.py`, `coding_policies.py`.
- CLI/session/evidence: `coding_cli.py`, `coding_state.py`, `memory.py`, `trajectory.py`, `verification.py`.
- Observability and policy: `tracing.py`, `lifecycle.py`, `guardrails.py`.

## 3. Key entry files

- `src/agents/agent.py`: defines `Agent`, `AgentCapabilities`, default final-answer tool registration, optional Python executor registration, handoff tool specs, and `Agent.run()`.
- `src/agents/runner.py`: thin public runner; `Runner.run_sync()` delegates directly to `run_agent_loop()`.
- `src/agents/run_loop.py`: main synchronous state machine. `run_agent_loop()` opens trace/task/agent spans, and `_run_agent_loop_impl()` performs guardrails, repo context assembly, model turns, tool planning, tool execution, verification, pause/resume, and result construction.
- `src/agents/model_turn.py`: prepares model input via `build_turn_context()`, calls the model, records `model_response` items, and parses structured final output.
- `src/agents/models.py`: model adapter boundary. `call_model_response()` invokes model adapters; `OpenAIResponsesModel.get_response()` builds Responses API request kwargs and calls `client.responses.create()`.
- `src/agents/tools.py`: `FunctionTool`, `ToolRegistry`, `function_tool()`, final-answer tool support, and basic argument validation.
- `src/agents/tool_execution.py`: executes one tool call with approval checks, tool guardrails, handler dispatch, model feedback, memory recording, and optional final-output recording.
- `src/agents/coding_agent.py`: builds a coding-agent setup from profile, workspace, manifest, environment, memory, and capability packs.
- `src/agents/coding_cli.py`: local CLI entrypoint for fresh runs, saved pending state, approve/reject resume, verification flags, and trajectory JSONL.

## 4. Core modules

`Agent` is the runtime owner. It stores memory, model, instructions, model settings, tools, handoffs, max steps, guardrails, hooks, and capabilities (`src/agents/agent.py:Agent`). It registers the default `final_answer` tool unless disabled and can register a mini Python executor when `AgentCapabilities.python_execution` is enabled.

`RunState` is the mutable in-run state container (`src/agents/run_state.py:RunState`). It tracks `new_items`, input, current/final agent, final answer state, turn and step counters, max limits, context wrapper, pending tool calls, and guardrail results. `RunResult` is the frozen user-facing result created by `build_run_result()` (`src/agents/run_state.py:build_run_result`, `src/agents/result.py:RunResult`).

The tool system is built around `FunctionTool` and `ToolRegistry` (`src/agents/tools.py:FunctionTool`, `src/agents/tools.py:ToolRegistry`). Tool planning and execution are split between `build_tool_execution_plan()` (`src/agents/tool_planning.py`) and `execute_tool_call()` (`src/agents/tool_execution.py`).

The coding-agent module is a profile/capability layer. `CodingAgentProfile` defines enabled capabilities, shell/edit approval defaults, shell and patch policies, and max limits (`src/agents/coding_agent.py:CodingAgentProfile`). `build_coding_agent()` registers workspace read, shell/test, and edit tools according to resolved capabilities.

The workspace/context layer provides bounded local file access through `Workspace.ensure_readable_path()` (`src/agents/workspace.py:Workspace`), inventory building, mention detection, selected-file state, repo context rendering, literal text search, Python AST outlines, and related-file heuristics.

## 5. Main execution flow

Main direct flow:

1. User code calls `Agent.run(task, config)` (`src/agents/agent.py:Agent.run`).
2. `Agent.run()` calls `Runner.run_sync()` (`src/agents/runner.py:Runner.run_sync`).
3. `Runner.run_sync()` calls `run_agent_loop()` (`src/agents/run_loop.py:run_agent_loop`).
4. `run_agent_loop()` opens `trace()`, `task_span()`, and `agent_span()`, then calls `_run_agent_loop_impl()`.
5. `_run_agent_loop_impl()` resolves limits, model settings, tool behavior, execution limits, verification runner, lifecycle hooks, and guardrails.
6. On fresh runs, it records the task in `agent.memory`, runs input guardrails, and calls `build_task_repo_context()` to add workspace/repo context.
7. Each loop turn calls `prepare_turn_input()`, prepends session messages, records a model turn, and calls `run_model_turn()`.
8. `process_model_turn()` classifies the normalized model response into text final output, tool calls, handoff calls, or stop states.
9. `build_tool_execution_plan()` splits model actions into handoffs, approved tools, and pending approvals.
10. If approvals are pending, the loop records `run_stopped` with `tool_approval_required` and returns a `RunResult`.
11. Otherwise, the loop records each `tool_call`, executes handoffs or tool calls sequentially, optionally runs verification, and either continues to another model turn or records final output.
12. On exit, `_build_result_and_save_session()` builds the `RunResult` and appends result messages to `RunConfig.session` when configured.

Resume flow:

1. CLI or direct caller restores `RunState` from a snapshot (`src/agents/run_state.py:RunState.from_snapshot`).
2. Caller marks pending calls approved or rejected with `RunState.approve_tool_call()` or `RunState.reject_tool_call()`.
3. `resume_agent_loop()` calls `_run_agent_loop_impl()` with the existing state.
4. `_run_agent_loop_impl()` first calls `resume_pending_tool_approvals()` (`src/agents/run_resume.py`) before normal model turns.

## 6. LLM call locations

Primary model call path:

- `src/agents/model_turn.py:run_model_turn()` wraps the model call with `model_span()` and lifecycle hooks.
- `src/agents/model_turn.py:_run_model_turn_impl()` prefers models with `get_response()`, then calls `call_model_response()`.
- `src/agents/models.py:call_model_response()` invokes `model.get_response(messages, tool_specs, model_settings=...)` when the adapter accepts `model_settings`.
- `src/agents/models.py:OpenAIResponsesModel.get_response()` builds request kwargs, calls `self.client.responses.create(**request_kwargs)`, validates response status, extracts output text/refusal/usage/request id, parses tool calls, updates `previous_response_id`, and clears pending tool outputs.

Secondary model call path:

- `src/agents/memory.py:ModelSummarizer.summarize()` optionally calls `OpenAI().responses.create()` to summarize compacted session history. It catches exceptions and falls back to `RuleBasedSummarizer`.

Legacy/testing model path:

- If a model does not expose `get_response()`, `_run_model_turn_impl()` calls `agent.model.decide(messages, tool_specs)` and accepts one optional `ToolCall`.

OpenAI-specific behavior is concentrated in `src/agents/models.py:OpenAIResponsesModel` and `src/agents/memory.py:ModelSummarizer`; other runtime modules depend on normalized `ModelResponse`, `ToolCall`, and `ChatMessage` contracts.

## 7. Tool call flow

Tool specs are assembled by `Agent._tool_specs_for_model()` from `ToolRegistry.list_specs()` plus handoff tool specs (`src/agents/agent.py:_tool_specs_for_model`).

The model returns `ToolCall` values inside a `ModelResponse`. `process_model_turn()` wraps them into a processed turn (`src/agents/turn_resolution.py:process_model_turn`). `build_tool_execution_plan()` then:

- identifies handoff calls via `agent._handoff_target_for()`;
- checks approval status in `RunContextWrapper`;
- calls `tool.requires_approval_for()` for unknown approvals;
- records `tool_approval_required` `RunItem` values and `pending_tool_calls` when approval is needed;
- returns approved tool calls separately from pending calls.

`execute_tool_call()` opens a tool span and delegates to `_execute_tool_call_impl()`. The implementation:

- emits lifecycle `on_tool_start`;
- resolves the tool from the registry;
- rejects disabled tools;
- handles prior approval rejection;
- re-checks approval if not already approved;
- runs tool input guardrails;
- executes the handler through `ToolRegistry.execute()`;
- interprets output with `interpret_tool_result()`;
- runs output guardrails;
- appends a `tool_result` item to `RunState.new_items`;
- records tool output back to compatible models via `record_tool_output()`;
- adds a `StepRecord` observation to memory;
- records final output when the tool should stop.

Tool execution is sequential in the main loop. There is no parallel tool execution or scheduler.

## 8. File read/write, patch, and diff capabilities

Read capabilities:

- `Workspace.ensure_readable_path()` enforces root, allowed paths, and ignore patterns (`src/agents/workspace.py:Workspace.ensure_readable_path`).
- `create_list_workspace_files_tool()`, `create_read_workspace_file_tool()`, and `create_search_workspace_text_tool()` provide coarse workspace listing, full-file UTF-8 reads with clipping, and literal text search (`src/agents/workspace_tools.py`).
- `WorkspaceCodeReader.read_lines()`, `search_text()`, `find_files()`, `outline_file()`, and `find_related_files()` provide line-range reads, literal search with context, filename search, Python AST outlines, and naming-based related-file lookup (`src/agents/workspace_code.py:WorkspaceCodeReader`).
- `create_workspace_code_tools()` exposes `read_workspace_lines`, `search_workspace_code`, `find_workspace_files`, `outline_workspace_file`, and `find_related_workspace_files` tools (`src/agents/workspace_code_tools.py:create_workspace_code_tools`).

Write/edit capabilities:

- `create_apply_patch_tool()` exposes `apply_patch` with `patch` and `dry_run` arguments (`src/agents/edit_tools.py:create_apply_patch_tool`).
- `parse_patch()` supports a minimal `*** Begin Patch` / Add / Update / Delete / `*** End Patch` format (`src/agents/patches.py:parse_patch`).
- `dry_run_patch()` validates without writing; `apply_patch()` parses, validates paths, and writes/deletes/updates files inside the workspace (`src/agents/patches.py:dry_run_patch`, `src/agents/patches.py:apply_patch`).
- `PatchApprovalPolicy.classify_patch_text()` allows dry runs and invalid patches to reach the tool but requires approval for valid writes (`src/agents/coding_policies.py:PatchApprovalPolicy`).

Diff capabilities:

- The runtime has no first-class Git diff model or structured diff parser.
- Shell profile can run allowlisted `git diff` via `ShellCommandPolicy.safe_prefixes` and `run_shell_command`, but this is a shell command path, not a native diff subsystem (`src/agents/coding_policies.py:ShellCommandPolicy`, `src/agents/shell_tools.py:create_shell_command_tool`).

## 9. Repo context, selected files, related files, and mention resolution

Repo context is assembled before the first model turn on fresh workspace runs:

- `_run_agent_loop_impl()` calls `build_task_repo_context(task, context_wrapper)` when not resuming (`src/agents/run_loop.py:_run_agent_loop_impl`).
- `build_task_repo_context()` builds workspace inventory, resolves mentions against the inventory, updates `SelectedFilesState`, builds `RepoContext`, and stores it in the run context (`src/agents/repo_context.py:build_task_repo_context`).
- `detect_file_mentions()` uses regexes to identify path, filename, test filename, and symbol-like tokens (`src/agents/context_mentions.py:detect_file_mentions`).
- `resolve_mentions_against_inventory()` matches path/basename candidates against `WorkspaceInventory` (`src/agents/context_mentions.py:resolve_mentions_against_inventory`).
- `SelectedFilesState.add_file()` deduplicates paths and promotes read-only entries to editable without downgrading editable entries (`src/agents/selected_files.py:SelectedFilesState`).
- `RepoContextBuilder.build()` can include workspace inventory, selected files, mentioned paths, mentioned symbols, and literal code matches for mentioned symbols (`src/agents/repo_context.py:RepoContextBuilder`).
- `build_turn_context()` renders system instructions, memory summary, repo context, memory messages, and selected files by priority (`src/agents/context_chunks.py:build_turn_context`).

Related files are heuristic only. `WorkspaceCodeReader.find_related_files()` uses workspace inventory and naming conventions, not a graph or language server.

## 10. Run state, session, memory, and compression

There are separate state layers:

- Per-run mutable state: `RunState` (`src/agents/run_state.py:RunState`).
- Per-run frozen result: `RunResult` (`src/agents/result.py:RunResult`).
- JSON-safe run snapshot: `RunStateSnapshot`, `run_state_snapshot_to_dict()`, and `run_state_snapshot_from_dict()` (`src/agents/run_state.py`).
- CLI pending state envelope: `CodingRunStateStore` and `CodingRunStateEnvelope` (`src/agents/coding_state.py`).
- Conversation session memory: `AgentSession` and `JsonSession` (`src/agents/memory.py`).

`RunResult.to_state()` serializes enough state for approval resume, including approval snapshots and new items. `CodingRunStateStore.save_pending_result()` wraps that nested state with task, workspace root, profile, model, manifest metadata, optional session/trajectory paths, verification settings, and pending approval summaries.

`AgentSession` stores turns and steps, can compact older turns into `MemorySummary`, and exposes messages through `to_messages()` / `get_items()`. `JsonSession` persists `{"version": 1, "session": ...}` to disk. This session layer preserves conversation history; it is not the same as the approval-resume state file.

Memory compression exists but is simple. `MemoryCompressor` uses `CompactionPolicy`; `RuleBasedSummarizer` is local, and `ModelSummarizer` can call OpenAI but falls back on exceptions.

## 11. Tracing and guardrails

Tracing:

- `Trace` and `Span` live in `src/agents/tracing.py`.
- `trace()` creates a real trace, returns `NoOpTrace` when disabled, or returns `NoOpTrace` when `only_if_missing=True` and a trace already exists.
- `span()`, `agent_span()`, `turn_span()`, `model_span()`, `tool_span()`, `guardrail_span()`, and `handoff_span()` create typed spans.
- `run_agent_loop()`, `run_model_turn()`, and `execute_tool_call()` are traced.
- `trace_include_sensitive_data` gates model input and tool argument/output capture in trace data.
- Export/processor support includes in-memory and JSONL tracing according to the existing `docs/llm` analysis and `src/agents/tracing.py`.

Guardrails:

- Agent input/output guardrails are defined in `src/agents/guardrails.py`.
- Tool input/output guardrails are defined in `src/agents/tool_guardrails.py`.
- `_run_agent_loop_impl()` runs input guardrails before repo context/model calls and output guardrails before final output acceptance.
- `_execute_tool_call_impl()` runs tool input guardrails before the handler and tool output guardrails after handler output.
- Tool guardrails can allow, reject content into a model-visible failed observation, or raise a tripwire exception.

Coding safety policies:

- `ShellCommandPolicy` classifies shell text as allow/approve/block using string prefixes and blocked fragments (`src/agents/coding_policies.py:ShellCommandPolicy`).
- `PatchApprovalPolicy` requires approval for valid writes and allows dry-run validation (`src/agents/coding_policies.py:PatchApprovalPolicy`).
- These policies are not sandboxing. Path safety still belongs to `Workspace`, and shell execution still uses `LocalEnvironment.run()`.

## 12. Whether RAG, repo map, and symbol index exist

RAG: not present as a semantic retrieval system. There is no vector store, embedding pipeline, chunk database, retriever abstraction, or document ranking model. Existing retrieval is workspace inventory plus literal text search and task mention matching.

Repo map: partially present. `RepoContextBuilder` creates a model-visible repo context with inventory, selected files, mentioned paths/symbols, and literal code matches. This is a lightweight textual repo context, not a persistent repo map or dependency graph.

Symbol index: not present inside runtime. `WorkspaceCodeReader.outline_file()` parses a single Python file with `ast.parse()` and returns top-level/contained symbols. Codegraph exists externally in `.codegraph/` and was used for this report, but `my_agent` runtime does not query CodeGraph or maintain its own project-wide symbol graph.

## 13. Current coding loop status

The current coding loop is functional but still basic:

- It can run a local coding task through `python -m agents.coding_cli` (`src/agents/coding_cli.py:main`).
- Profiles support read-only, shell/test, and edit-local capability combinations via `CodingAgentProfile` and `PROFILE_BUILDERS`.
- Workspace read tools, shell/test tools, and patch tools are available according to profile.
- Shell and patch writes can pause for approval.
- Pending approvals can be saved to JSON with `--state-json`.
- Saved approvals can be resumed with `--resume-state` plus `--approve`, `--reject`, or `--approve-all`.
- Verification can be configured with `--verify-command` and `--verify-after-tool`; failures become observations visible to the next model turn.
- Trajectory JSONL can record run evidence, saved-state events, resume events, approval decisions, verification events, final output, or stopped state.

Current limitations:

- The loop is synchronous and sequential.
- It does not automatically create Git checkpoints, branches, commits, or PRs.
- It does not parse test failures into structured repair tasks beyond plain verification observations.
- It does not have planner/executor role separation.
- It does not have task decomposition, subagents, background monitors, or parallel execution.
- It does not include an interactive TUI; approvals are CLI flag based.

## 14. Missing capabilities

High-value missing capabilities for a stronger coding agent:

- Persistent repo map and symbol/dependency index available to the runtime.
- Semantic RAG or embedding-backed retrieval.
- Structured diff review and Git checkpoint management.
- Native Git status/diff/commit/branch workflows instead of shell-command-only Git access.
- Structured test failure parsing and repair planning.
- Language server integration for definitions, references, type errors, and diagnostics.
- Patch conflict detection beyond exact text replacement behavior.
- Multi-file edit planning and post-edit review loop.
- Parallel or async tool execution.
- Rich approval UI beyond CLI flags.
- Durable task database separate from pending approval snapshots.
- Explicit planner/worker separation or subagent orchestration.
- More robust OpenAI API compatibility checks against current developer docs before production use.

## 15. Areas that should not be heavily refactored now

Do not heavily refactor these areas before the next upgrade phase unless a concrete task requires it:

- `run_loop.py`: it is the central state machine. Upgrade by adding narrow hooks or small extracted behavior only when needed.
- `run_state.py`, `result.py`, and `coding_state.py`: snapshot/resume contracts are delicate and already tested. Preserve schema boundaries.
- `tools.py`, `tool_planning.py`, and `tool_execution.py`: approval semantics are split between planning and execution; broad rewrites could break pause/resume behavior.
- `workspace.py`, `workspace_manifest.py`, and `patches.py`: these form the file safety boundary. Keep changes targeted.
- `coding_cli.py`: it owns many CLI flows but is currently the integration point for state, verification, and trajectory. Avoid reorganizing it without tests for fresh, pending, resume, and trajectory paths.
- `memory.py`: session and compaction behavior are permissive by design; avoid mixing it with run-state resume.
- `tracing.py`: observability is separate from correctness. Do not make runtime correctness depend on tracing processors/exporters.

## 16. Key files reviewed

Existing project analysis docs:

- `docs/llm/ARCHITECTURE_INDEX.md`
- `docs/llm/MAINTENANCE_LOG.md`
- `docs/llm/MODULE_CARDS.md`
- `docs/llm/RUNTIME_FLOWS.md`
- `docs/llm/STATE_AND_CONTRACTS.md`
- `docs/llm/SYMBOL_MAP.md`

Core source files:

- `pyproject.toml`
- `src/agents/__init__.py`
- `src/agents/agent.py`
- `src/agents/runner.py`
- `src/agents/run_loop.py`
- `src/agents/model_turn.py`
- `src/agents/models.py`
- `src/agents/tools.py`
- `src/agents/tool_planning.py`
- `src/agents/tool_execution.py`
- `src/agents/tool_runtime.py`
- `src/agents/tool_observations.py`
- `src/agents/tool_guardrails.py`
- `src/agents/run_state.py`
- `src/agents/result.py`
- `src/agents/run_resume.py`
- `src/agents/run_recording.py`
- `src/agents/coding_agent.py`
- `src/agents/coding_cli.py`
- `src/agents/coding_state.py`
- `src/agents/coding_policies.py`
- `src/agents/workspace.py`
- `src/agents/workspace_manifest.py`
- `src/agents/workspace_inventory.py`
- `src/agents/workspace_tools.py`
- `src/agents/workspace_code.py`
- `src/agents/workspace_code_tools.py`
- `src/agents/selected_files.py`
- `src/agents/context_mentions.py`
- `src/agents/repo_context.py`
- `src/agents/context_chunks.py`
- `src/agents/shell_tools.py`
- `src/agents/edit_tools.py`
- `src/agents/patches.py`
- `src/agents/environment.py`
- `src/agents/memory.py`
- `src/agents/tracing.py`
- `src/agents/lifecycle.py`
- `src/agents/guardrails.py`
- `src/agents/verification.py`
- `src/agents/trajectory.py`
