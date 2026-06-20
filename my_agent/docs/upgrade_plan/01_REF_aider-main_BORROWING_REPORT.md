# Reference Borrowing Report: aider-main

## 1. Metadata

| Field | Value |
|---|---|
| Project name | aider-main |
| Project path | `C:\Users\ch\Desktop\ai agent学习\reference\aider-main` |
| Subagent name | `reference_aider-main_analysis` |
| Codegraph status | Available for `reference/aider-main`; 203 indexed files, 2880 nodes, 7245 edges |
| Required baseline read | `my_agent/docs/upgrade_plan/00_MY_AGENT_BASELINE.md` |
| Required reference architecture read | `reference/aider-main/PROJECT_ARCHITECTURE_ANALYSIS.md` |

Key files reviewed:

- `reference/aider-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/aider-main/pyproject.toml`
- `reference/aider-main/aider/main.py`
- `reference/aider-main/aider/coders/base_coder.py`
- `reference/aider-main/aider/coders/chat_chunks.py`
- `reference/aider-main/aider/coders/__init__.py`
- `reference/aider-main/aider/coders/editblock_coder.py`
- `reference/aider-main/aider/coders/udiff_coder.py`
- `reference/aider-main/aider/coders/wholefile_coder.py`
- `reference/aider-main/aider/coders/patch_coder.py`
- `reference/aider-main/aider/coders/architect_coder.py`
- `reference/aider-main/aider/coders/context_coder.py`
- `reference/aider-main/aider/commands.py`
- `reference/aider-main/aider/repomap.py`
- `reference/aider-main/aider/repo.py`
- `reference/aider-main/aider/linter.py`
- `reference/aider-main/aider/history.py`
- `reference/aider-main/aider/models.py`
- `reference/aider-main/aider/io.py`
- `reference/aider-main/aider/help.py`
- `reference/aider-main/aider/run_cmd.py`
- `reference/aider-main/aider/analytics.py`
- `reference/aider-main/aider/watch.py`

## 2. One-Sentence Project Positioning

Aider is an interactive terminal coding agent that solves reliable repository editing through selected files, structured edit formats, repo-map retrieval, git checkpoints, and lint/test feedback; its most useful value for `my_agent` is the concrete coding loop that turns edit, validation, and context failures into model-visible repair turns.

## 3. Capability Matrix Related to `my_agent`

| Capability | Exists in Reference | `my_agent` Current State | Borrowing Value | Evidence Files |
|---|---|---|---|---|
| Agent abstraction | Yes. `Coder` owns model, messages, selected files, repo, commands, prompts, edit mode, and turn execution. | `Agent` exists as a general runtime owner; coding behavior is a profile over the same runtime. | Medium. Borrow coding-agent-specific state boundaries, not the monolithic class shape. | `aider/coders/base_coder.py:88`, `aider/coders/base_coder.py:125` |
| Runner / run loop | Yes. `Coder.run()`, `run_one()`, and `send_message()` implement input, model, edit, validation, and reflection. | `Runner.run_sync()` delegates to `run_agent_loop()` with tool execution and verification, but coding loop is less autonomous. | High. Adapt the reflection-oriented coding turn flow. | `aider/coders/base_coder.py:876`, `aider/coders/base_coder.py:924`, `aider/coders/base_coder.py:1419` |
| Tool system | Partly. Slash commands are the primary control surface; deprecated function coders are not the main path. | Function tools, tool registry, tool planning, approvals, guardrails, and execution are already present. | Medium. Borrow CLI command ergonomics, not the tool architecture. | `aider/commands.py:276`, `aider/commands.py:287`, `aider/commands.py:312` |
| File discovery | Yes. Git-aware tracked files, glob handling, add/drop commands, completions, and mention checks. | Workspace inventory, manifest, file search, selected files, and mention resolution already exist. | Medium. Borrow user-facing add/drop and token pressure behavior. | `aider/commands.py:799`, `aider/commands.py:912`, `aider/coders/base_coder.py:1714` |
| Mention resolution | Yes. Current user and assistant text are scanned for file names and identifiers. | `context_mentions.py` detects and resolves path, filename, test filename, and symbol-like tokens against inventory. | Medium. Borrow assistant-mentioned-file feedback and repo-map personalization signals. | `aider/coders/base_coder.py:713`, `aider/coders/base_coder.py:1560`, `aider/coders/context_coder.py:27` |
| Repo context | Yes. `ChatChunks` layers repo map, read-only files, editable files, current messages, and reminders. | `RepoContextBuilder` builds lightweight inventory, selected files, mentioned paths/symbols, and literal matches. | High. Adapt chunked prompt layering and token-aware context placement. | `aider/coders/chat_chunks.py:5`, `aider/coders/base_coder.py:1226`, `aider/coders/base_coder.py:1281` |
| Patch / diff / edit | Yes. Multiple edit formats: SEARCH/REPLACE, unified diff, whole file, and patch AST. | Minimal patch DSL with dry-run/apply exists; no native structured diff subsystem. | High. Borrow dry-run first, edit-permission checks, and failure feedback. | `aider/coders/editblock_coder.py:15`, `aider/coders/editblock_coder.py:41`, `aider/coders/patch_coder.py:17` |
| Run state / task state | Partly. State is held on `Coder` fields and transferred during mode switching. | `RunState`, snapshots, pending approvals, CLI state envelopes, and result objects already exist. | Low to medium. Borrow coding-specific fields only; avoid replacing current state contracts. | `aider/coders/base_coder.py:153`, `aider/coders/base_coder.py:170`, `aider/main.py:1165` |
| Session / memory / compression | Yes. `done_messages`, `cur_messages`, `ChatChunks`, and `ChatSummary` compress history. | `AgentSession`, `JsonSession`, `MemoryCompressor`, rule/model summarizers already exist. | Medium. Borrow simpler split between active turn and summarized older turns if it fits current memory boundaries. | `aider/history.py:7`, `aider/history.py:27`, `aider/history.py:98` |
| Tracing | Partly. Analytics, LLM history, chat history, and cost display exist; no span tree framework found. | Structured tracing spans exist for agent, turn, model, tool, guardrail, and handoff. | Low. Borrow cost/LLM transcript ideas, not tracing architecture. | `aider/analytics.py:60`, `aider/io.py:754`, `aider/coders/base_coder.py:1532` |
| Guardrails | Partly. Engineering checks for token limits, edit permission, gitignore, edit format errors, linter results, and model env validation. | Input/output guardrails and tool guardrails exist, plus shell/patch approval policies. | Medium. Borrow coding-specific guardrail observations and edit-failure reflection. | `aider/coders/base_coder.py:1396`, `aider/coders/base_coder.py:2191`, `aider/coders/base_coder.py:2296` |
| RAG / repo map / symbol index | Yes. RepoMap uses tree-sitter tags, fallback refs, NetworkX PageRank, personalization, and token-bounded rendering. | No semantic RAG or persistent runtime symbol graph; only inventory, literal search, AST outline per file. | Very high. This is Aider's strongest borrowing candidate. | `aider/repomap.py:103`, `aider/repomap.py:365`, `aider/repomap.py:524`, `aider/repomap.py:576` |
| Planning / coding loop | Partly. Architect/editor mode plans first, then a separate editor coder applies changes. | No explicit planner/executor split; coding profile runs one general loop. | High. Borrow flow shape for complex tasks after basic loop is stable. | `aider/coders/architect_coder.py:6`, `aider/coders/architect_coder.py:11`, `aider/coders/architect_coder.py:37` |
| Testing / validation | Yes. Post-edit lint/test output can become a reflected message for the next model turn. | Verification runner exists and failures become observations, but no rich failure parsing or repair plan. | High. Borrow edit-scoped lint/test repair loop and contextual lint output. | `aider/coders/base_coder.py:1599`, `aider/coders/base_coder.py:1616`, `aider/linter.py:82` |
| Subagent / parallel task | No general subagent system. Architect/editor is sequential delegation; context mode is sequential file selection. | No task decomposition or parallel subagents. | Low. Borrow planner/editor handoff only; do not infer parallelism from Aider. | `aider/coders/architect_coder.py:44`, `aider/coders/context_coder.py:21` |

## 4. Borrowing Candidates

## BC-01: Reflection-Oriented Coding Turn Loop

- Problem solved:
  - A coding agent needs a durable loop that can turn malformed edits, missing files, lint failures, test failures, and shell output into actionable next model turns.
- Reference implementation:
  - Files:
    - `aider/coders/base_coder.py`
  - Classes / functions:
    - `Coder.run()`
    - `Coder.run_one()`
    - `Coder.send_message()`
    - `Coder.apply_updates()`
    - `Coder.lint_edited()`
- Execution flow:
  - `run()` reads user input and calls `run_one()`.
  - `run_one()` initializes per-message state, preprocesses commands/URLs/file mentions, then calls `send_message()`.
  - `send_message()` appends the user message, builds context, sends to the model, stores the assistant response, checks assistant-mentioned files, applies edits, commits, lints, runs shell suggestions, runs tests, and sets `reflected_message` when repair is needed.
  - `run_one()` repeats with `reflected_message` until success or a reflection limit.
- Value for `my_agent`:
  - `my_agent` already has tool execution, verification observations, and run state. The missing design is a coding-agent-specific turn policy that decides when verification/edit failures should automatically trigger another model turn.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_loop.py`
  - `src/agents/coding_agent.py`
  - `src/agents/verification.py`
  - `src/agents/tool_observations.py`
  - New narrow module such as `src/agents/coding_loop.py` if the flow should stay out of the generic runner.
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add a coding-loop policy object or helper that consumes model output, tool observations, patch results, and verification results.
  - Reuse current `RunState` and verification observation contracts.
  - Introduce a bounded reflection counter scoped to coding runs.
  - Feed structured edit/validation failure text into the next model input through existing context/message mechanisms.
- Risks:
  - Aider's `send_message()` is large and mixes many responsibilities; copying it would conflict with `my_agent`'s existing modular runner and tool execution layers.
  - Automatic reflection can create expensive or confusing loops unless bounded and recorded.
- Evidence files:
  - `aider/coders/base_coder.py:876`
  - `aider/coders/base_coder.py:924`
  - `aider/coders/base_coder.py:1419`
  - `aider/coders/base_coder.py:1585`
  - `aider/coders/base_coder.py:1599`
  - `aider/coders/base_coder.py:1616`

## BC-02: Prompt Context Chunk Layering

- Problem solved:
  - Coding prompts become brittle when system prompts, examples, repo maps, read-only files, editable files, active messages, and reminders are concatenated ad hoc.
- Reference implementation:
  - Files:
    - `aider/coders/chat_chunks.py`
    - `aider/coders/base_coder.py`
  - Classes / functions:
    - `ChatChunks`
    - `ChatChunks.all_messages()`
    - `ChatChunks.add_cache_control_headers()`
    - `Coder.format_chat_chunks()`
- Execution flow:
  - `format_chat_chunks()` builds system/example messages, summarizes older history, injects repo map, read-only file content, editable chat files, active messages, and optional reminders.
  - `ChatChunks.all_messages()` provides the final order.
  - Cache-control headers are applied to selected stable chunks when enabled.
- Value for `my_agent`:
  - `my_agent` has `build_turn_context()` and repo context rendering, but Aider's explicit chunk object is a useful model for keeping prompt sections inspectable, token-aware, and cache-aware.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/context_chunks.py`
  - `src/agents/repo_context.py`
  - `src/agents/model_turn.py`
  - `src/agents/memory.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Extend or revise `context_chunks.py` around a typed chunk container for system, memory summary, repo map, selected files, current messages, reminders, and tool/verification observations.
  - Keep the existing public model input contracts.
  - Add debug rendering for chunk token budgets.
- Risks:
  - Overly detailed chunk modeling can become a second hidden state system if it duplicates `RunState` or `AgentSession`.
  - Cache-control semantics are model-specific and should remain optional.
- Evidence files:
  - `aider/coders/chat_chunks.py:5`
  - `aider/coders/chat_chunks.py:16`
  - `aider/coders/chat_chunks.py:28`
  - `aider/coders/base_coder.py:1226`
  - `aider/coders/base_coder.py:1264`

## BC-03: RepoMap Symbol Graph Context

- Problem solved:
  - Large repositories cannot fit in the model context, and literal search alone misses relevant files and symbols.
- Reference implementation:
  - Files:
    - `aider/repomap.py`
    - `aider/coders/base_coder.py`
  - Classes / functions:
    - `RepoMap`
    - `RepoMap.get_repo_map()`
    - `RepoMap.get_ranked_tags()`
    - `RepoMap.get_ranked_tags_map()`
    - `Coder.get_repo_map()`
- Execution flow:
  - `Coder.get_repo_map()` collects current file mentions and identifier mentions.
  - `RepoMap.get_repo_map()` decides the token budget and calls ranked tag rendering.
  - `get_ranked_tags()` extracts definitions and references, builds a graph, personalizes ranking from chat files, mentioned files, and mentioned identifiers, then ranks definitions with PageRank.
  - `get_ranked_tags_map()` caches rendered maps based on selected files, other files, token budget, and mention hints.
- Value for `my_agent`:
  - This directly addresses a stated baseline gap: no persistent runtime symbol graph, repo map, semantic RAG, or dependency-aware retrieval.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/repo_context.py`
  - `src/agents/workspace_inventory.py`
  - `src/agents/workspace_code.py`
  - New module such as `src/agents/repo_map.py`
  - Optional future integration with external CodeGraph rather than tree-sitter-only indexing.
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Start with a small Python-only or CodeGraph-backed repo map that ranks files/symbols from selected files and task mentions.
  - Render only symbol outlines and line snippets within a token budget.
  - Cache index data by file path, mtime, and content hash.
  - Add repo-map output as a separate context chunk, not as a replacement for selected-file content.
- Risks:
  - Aider's implementation depends on tree-sitter, grep-ast, NetworkX, and diskcache; copying it would add significant dependencies and maintenance burden.
  - Ranking can mislead the model if symbol extraction is stale or language coverage is incomplete.
- Evidence files:
  - `aider/repomap.py:103`
  - `aider/repomap.py:365`
  - `aider/repomap.py:470`
  - `aider/repomap.py:524`
  - `aider/repomap.py:576`
  - `aider/coders/base_coder.py:709`

## BC-04: Edit Format Strategy Layer

- Problem solved:
  - Different models and tasks need different edit protocols; one hard-coded patch format can reduce reliability.
- Reference implementation:
  - Files:
    - `aider/coders/base_coder.py`
    - `aider/coders/__init__.py`
    - `aider/coders/editblock_coder.py`
    - `aider/coders/udiff_coder.py`
    - `aider/coders/wholefile_coder.py`
    - `aider/coders/patch_coder.py`
    - `aider/models.py`
  - Classes / functions:
    - `Coder.create()`
    - `EditBlockCoder`
    - `UnifiedDiffCoder`
    - `WholeFileCoder`
    - `PatchCoder`
    - `ModelSettings.edit_format`
- Execution flow:
  - Model settings define a default `edit_format`.
  - `Coder.create()` selects a coder subclass from registered classes by `edit_format`.
  - Each subclass implements `get_edits()` and `apply_edits()`.
- Value for `my_agent`:
  - `my_agent` already has patch application, but not a first-class edit-protocol strategy. A small strategy layer could allow patch, search/replace, and possibly whole-file modes without changing the runner.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/patches.py`
  - `src/agents/edit_tools.py`
  - `src/agents/coding_agent.py`
  - New module such as `src/agents/edit_protocols.py`
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Define a narrow internal interface: parse model edit output, dry run, apply, and produce model-visible failure text.
  - Keep the current patch DSL as the initial default.
  - Add SEARCH/REPLACE only if it solves current patch failure modes.
  - Select protocol from coding profile or model settings, not from a broad plugin registry.
- Risks:
  - The project AGENTS instructions warn against speculative abstractions. Add this only when at least two edit protocols are truly needed.
  - Multiple edit formats complicate prompts and tests.
- Evidence files:
  - `aider/coders/base_coder.py:125`
  - `aider/coders/__init__.py`
  - `aider/coders/editblock_coder.py:15`
  - `aider/coders/wholefile_coder.py:10`
  - `aider/coders/patch_coder.py:17`
  - `aider/models.py:127`

## BC-05: Search/Replace Edit Failure Feedback

- Problem solved:
  - When an edit cannot be applied, the model needs precise, local feedback about what failed and what nearby text might match.
- Reference implementation:
  - Files:
    - `aider/coders/editblock_coder.py`
    - `aider/coders/base_coder.py`
  - Classes / functions:
    - `EditBlockCoder.get_edits()`
    - `EditBlockCoder.apply_edits()`
    - `do_replace()`
    - `find_similar_lines()`
    - `Coder.apply_updates()`
- Execution flow:
  - The model outputs SEARCH/REPLACE blocks.
  - `get_edits()` parses them and separates shell commands.
  - `apply_edits()` tries exact and whitespace-aware replacement.
  - Failed blocks produce a detailed error message with the failed block, possible nearby matching lines, and instructions to resend only failed blocks.
  - `Coder.apply_updates()` catches the `ValueError` and assigns it to `reflected_message`.
- Value for `my_agent`:
  - `my_agent` has patch dry-run/apply, but patch failure feedback can likely be improved by adopting Aider's model-visible repair style.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/patches.py`
  - `src/agents/edit_tools.py`
  - `src/agents/tool_observations.py`
  - `src/agents/verification.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Keep the current patch parser.
  - Improve dry-run and apply errors with file path, failed hunk/action, expected text, actual nearby text, and whether partial changes were applied.
  - Return failure as structured tool observation and optional reflection input.
- Risks:
  - SEARCH/REPLACE itself may be less robust than the existing patch DSL for multi-file changes.
  - Fuzzy matching can apply unintended edits if made too permissive; use it for suggestions before automatic application.
- Evidence files:
  - `aider/coders/editblock_coder.py:21`
  - `aider/coders/editblock_coder.py:41`
  - `aider/coders/editblock_coder.py:84`
  - `aider/coders/editblock_coder.py:98`
  - `aider/coders/base_coder.py:2296`

## BC-06: Selected Editable Files and Read-Only Context

- Problem solved:
  - A coding agent should distinguish files it may edit from files it may only use as context.
- Reference implementation:
  - Files:
    - `aider/coders/base_coder.py`
    - `aider/commands.py`
  - Classes / functions:
    - `Coder.abs_fnames`
    - `Coder.abs_read_only_fnames`
    - `Coder.allowed_to_edit()`
    - `Coder.prepare_to_edit()`
    - `Commands.cmd_add()`
    - `Commands.cmd_drop()`
- Execution flow:
  - `/add` moves files into the editable set after repo, path, ignore, and model capability checks.
  - read-only files can be promoted when allowed.
  - `allowed_to_edit()` confirms creation or edits to files not already added to chat.
  - `prepare_to_edit()` filters edits through those checks before write.
- Value for `my_agent`:
  - `my_agent` already has `SelectedFilesState` and workspace path safety. Aider's stronger user-facing distinction between editable and read-only context could make coding runs more controllable.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/selected_files.py`
  - `src/agents/workspace.py`
  - `src/agents/workspace_tools.py`
  - `src/agents/coding_cli.py`
  - `src/agents/coding_policies.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Preserve `Workspace.ensure_readable_path()` as the boundary.
  - Add explicit CLI/workflow commands or tool arguments for editable versus read-only selection.
  - Require approval before editing files not selected as editable.
  - Include selected-file status in repo context.
- Risks:
  - `my_agent` already has approval semantics; duplicating them in selection state could create inconsistent authority.
  - For non-interactive runs, confirmations need a clear policy fallback.
- Evidence files:
  - `aider/commands.py:799`
  - `aider/commands.py:912`
  - `aider/coders/base_coder.py:2191`
  - `aider/coders/base_coder.py:2269`

## BC-07: Git Checkpoint, Diff, and Undo Workflow

- Problem solved:
  - Users need confidence that agent edits are inspectable and reversible.
- Reference implementation:
  - Files:
    - `aider/repo.py`
    - `aider/coders/base_coder.py`
    - `aider/commands.py`
  - Classes / functions:
    - `GitRepo`
    - `GitRepo.commit()`
    - `GitRepo.get_diffs()`
    - `GitRepo.diff_commits()`
    - `Coder.auto_commit()`
    - `Commands.cmd_diff()`
    - `Commands.raw_cmd_undo()`
- Execution flow:
  - After edits, `Coder.auto_commit()` commits changed files with generated context.
  - `/diff` compares the current head to the commit before the last message.
  - `/undo` restores files from the last Aider commit if it has not been pushed.
- Value for `my_agent`:
  - Baseline explicitly lacks native Git checkpointing and structured diff review. This would materially improve coding-agent reliability.
- Possible mapping to `my_agent` files / modules:
  - New module such as `src/agents/git_tools.py`
  - `src/agents/coding_cli.py`
  - `src/agents/trajectory.py`
  - `src/agents/verification.py`
  - `src/agents/coding_policies.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add narrow native Git tools for status, diff, checkpoint commit, and undo checkpoint.
  - Keep shell `git` access separate from native audited Git operations.
  - Record checkpoint hashes in trajectory JSONL.
  - Do not auto-commit by default until CLI policy and tests are clear.
- Risks:
  - Git operations are destructive if undo/reset logic is wrong.
  - Commit generation through the model can add latency and cost; start with deterministic messages or optional model-generated messages.
- Evidence files:
  - `aider/repo.py:52`
  - `aider/repo.py:131`
  - `aider/repo.py:375`
  - `aider/repo.py:419`
  - `aider/coders/base_coder.py:2375`
  - `aider/commands.py:553`
  - `aider/commands.py:657`

## BC-08: Lint/Test Repair Feedback Loop

- Problem solved:
  - Edits are not reliable until the agent can observe syntax, lint, and test failures and use them to repair code.
- Reference implementation:
  - Files:
    - `aider/linter.py`
    - `aider/coders/base_coder.py`
    - `aider/commands.py`
  - Classes / functions:
    - `Linter`
    - `Linter.lint()`
    - `basic_lint()`
    - `Coder.lint_edited()`
    - `Commands.cmd_test()`
    - `Commands.cmd_run()`
- Execution flow:
  - After an edit, `Coder.lint_edited()` lints each edited file.
  - `Linter.lint()` returns a repair prompt including error text and tree context around failing lines.
  - If enabled, `cmd_test()` runs a test command and returns non-zero output to chat.
  - User confirmation controls whether lint/test failures become `reflected_message`.
- Value for `my_agent`:
  - `my_agent` has verification commands and observations, but Aider's edit-scoped lint context and test-output repair loop provide a better coding-specific feedback pattern.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/verification.py`
  - `src/agents/shell_tools.py`
  - `src/agents/workspace_code.py`
  - `src/agents/tool_observations.py`
  - `src/agents/run_loop.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Attach verification observations to edited files when possible.
  - Add optional syntax/lint checks after patch application.
  - Convert failures into structured observations plus concise repair prompts.
  - Keep approval and command policies in the existing shell/test policy layer.
- Risks:
  - Running linters/tests after every edit may be slow.
  - Aider's local shell execution model is not a sandbox; only borrow the feedback loop, not command safety assumptions.
- Evidence files:
  - `aider/linter.py:21`
  - `aider/linter.py:82`
  - `aider/linter.py:111`
  - `aider/coders/base_coder.py:1681`
  - `aider/coders/base_coder.py:1599`
  - `aider/commands.py:993`
  - `aider/commands.py:1013`

## BC-09: Planner/Editor Sequential Handoff

- Problem solved:
  - Complex coding tasks benefit from separating reasoning about the change from applying the actual edits.
- Reference implementation:
  - Files:
    - `aider/coders/architect_coder.py`
    - `aider/commands.py`
    - `aider/models.py`
  - Classes / functions:
    - `ArchitectCoder`
    - `ArchitectCoder.reply_completed()`
    - `Commands.cmd_architect()`
    - `ModelSettings.editor_model_name`
    - `ModelSettings.editor_edit_format`
- Execution flow:
  - `/architect` switches to architect mode or runs a one-off architect request.
  - The architect coder produces a plan in text.
  - On confirmation, it creates a new editor coder with an editor model and editor edit format.
  - The editor coder receives the architect plan as its input and applies changes.
- Value for `my_agent`:
  - Baseline lacks planner/executor separation. This provides a simple sequential version without introducing parallel subagents.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_agent.py`
  - `src/agents/run_loop.py`
  - `src/agents/agent.py`
  - New module such as `src/agents/coding_plan.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add an optional coding profile mode that first requests a plan, records it, then runs the existing editor-capable agent with that plan as context.
  - Keep the same `RunState` and tool approval machinery.
  - Start with a single model; editor model selection can come later.
- Risks:
  - Planner/editor can double cost and latency.
  - If plans are not constrained, editor turns may receive vague instructions rather than actionable edit steps.
- Evidence files:
  - `aider/coders/architect_coder.py:6`
  - `aider/coders/architect_coder.py:11`
  - `aider/coders/architect_coder.py:23`
  - `aider/coders/architect_coder.py:37`
  - `aider/commands.py:1190`
  - `aider/models.py:145`

## BC-10: Context-Selection Mode

- Problem solved:
  - Before editing, the agent often needs to identify which files should be loaded or selected.
- Reference implementation:
  - Files:
    - `aider/coders/context_coder.py`
    - `aider/commands.py`
    - `aider/coders/base_coder.py`
  - Classes / functions:
    - `ContextCoder`
    - `ContextCoder.__init__()`
    - `ContextCoder.reply_completed()`
    - `Commands.cmd_context()`
    - `Coder.get_file_mentions()`
- Execution flow:
  - Context mode enlarges and refreshes the repo map.
  - The model replies with files it thinks matter.
  - `reply_completed()` extracts file mentions, updates selected files, and reflects with a try-again prompt if selection changed and reflection budget remains.
- Value for `my_agent`:
  - `my_agent` already has mention resolution and related-file heuristics, but a dedicated "select context first" mode could improve first-turn accuracy on broad tasks.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/context_mentions.py`
  - `src/agents/repo_context.py`
  - `src/agents/selected_files.py`
  - `src/agents/coding_agent.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add an optional pre-edit context selection pass for tasks with no selected files or broad file mentions.
  - Use existing inventory/mention resolution first.
  - Feed selected files into `SelectedFilesState` and then continue to the normal coding loop.
- Risks:
  - A separate context pass can over-select files and increase token use.
  - It depends heavily on repo-map quality.
- Evidence files:
  - `aider/coders/context_coder.py:5`
  - `aider/coders/context_coder.py:14`
  - `aider/coders/context_coder.py:21`
  - `aider/coders/context_coder.py:27`
  - `aider/commands.py:1194`

## BC-11: Command Control Surface for CLI Coding Runs

- Problem solved:
  - Interactive coding agents need user commands for adding/dropping files, checking token pressure, running commands/tests, switching modes, and inspecting diffs.
- Reference implementation:
  - Files:
    - `aider/commands.py`
    - `aider/io.py`
  - Classes / functions:
    - `Commands`
    - `Commands.get_commands()`
    - `Commands.run()`
    - `Commands.cmd_add()`
    - `Commands.cmd_drop()`
    - `Commands.cmd_tokens()`
    - `InputOutput.get_input()`
    - `AutoCompleter`
- Execution flow:
  - Methods named `cmd_*` are automatically exposed as slash commands.
  - `run()` resolves exact or partial commands.
  - Command-specific completion supports files, read-only files, modes, and paths.
- Value for `my_agent`:
  - `my_agent` has a local CLI, approvals, state save/resume, and tools. Borrowing CLI command ergonomics can make it usable without changing the underlying agent runtime.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_cli.py`
  - `src/agents/workspace_tools.py`
  - `src/agents/workspace_code_tools.py`
  - `src/agents/selected_files.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Add a small command dispatcher for the CLI only.
  - Keep command handlers thin wrappers over existing runtime/tool APIs.
  - Start with commands for selected files, context tokens, verify, diff/status if native Git is added, and resume approvals.
- Risks:
  - Auto-registering every `cmd_*` can expose unintended commands if not disciplined.
  - CLI commands and model tools should remain distinct to avoid policy bypasses.
- Evidence files:
  - `aider/commands.py:276`
  - `aider/commands.py:287`
  - `aider/commands.py:312`
  - `aider/commands.py:445`
  - `aider/io.py:91`
  - `aider/io.py:523`

## BC-12: Model Capability Settings for Coding Behavior

- Problem solved:
  - Model choice affects edit format, repo-map usage, streaming, reminders, weak/editor models, and provider-specific parameters.
- Reference implementation:
  - Files:
    - `aider/models.py`
    - `aider/resources/model-settings.yml`
    - `aider/resources/model-metadata.json`
  - Classes / functions:
    - `ModelSettings`
    - `Model`
    - `Model.configure_model_settings()`
    - `Model.send_completion()`
- Execution flow:
  - Model metadata and settings are loaded into `ModelSettings`.
  - The selected model config determines default edit format, repo map use, streaming, cache behavior, weak model, editor model, and extra provider parameters.
  - `send_completion()` adapts request kwargs and calls LiteLLM.
- Value for `my_agent`:
  - `my_agent` should not copy Aider's broad provider compatibility, but a small coding-specific model capability record can help choose prompt/edit behavior.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/models.py`
  - `src/agents/model_settings.py`
  - `src/agents/coding_agent.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add a minimal coding-model capability record with fields such as preferred edit protocol, supports vision, max context hint, supports prompt caching, and planner/editor role.
  - Keep OpenAI Responses API logic in the existing model adapter.
  - Avoid importing Aider's provider/model registry.
- Risks:
  - Model capability tables become stale quickly.
  - Broad compatibility logic can distract from the project goal of a usable coding agent.
- Evidence files:
  - `aider/models.py:127`
  - `aider/models.py:329`
  - `aider/models.py:985`
  - `aider/main.py:822`

## 5. Designs Not Suitable for Current Borrowing

| Design | Why Not Suitable Now | Future Condition | Evidence |
|---|---|---|---|
| Deprecated function-tool edit coders | Aider's function-tool edit coders are not the current main path, and `my_agent` already has a modern tool registry and OpenAI Responses-oriented model adapter. | Revisit only if a structured tool-call edit protocol is designed natively for `my_agent`. | `aider/coders/*_func_coder.py`, `aider/models.py:1006` |
| Local shell execution safety model | Aider runs commands on the local shell with user confirmation; `my_agent` should preserve approval policies and avoid treating this as sandboxing. | Borrow command-output feedback only after explicit command policies and audit trail are in place. | `aider/run_cmd.py`, `aider/commands.py:1013`, `aider/coders/base_coder.py:2450` |
| Monolithic `Coder.send_message()` implementation | The flow is valuable, but the function combines prompt building, model retries, response processing, edit application, git, lint, shell, and test behavior. | Use it as a lifecycle reference after mapping responsibilities into existing `my_agent` modules. | `aider/coders/base_coder.py:1419` |
| Full multi-provider LiteLLM compatibility layer | `my_agent` depends on OpenAI and has a focused model adapter. Aider's provider compatibility has a large maintenance surface. | Consider a small provider abstraction only if project requirements expand beyond OpenAI. | `aider/models.py`, `aider/llm.py` |
| Streamlit GUI | It is not central to the coding-agent architecture and does not address the current upgrade goals. | Revisit after the CLI/loop/context/edit reliability is mature. | `aider/gui.py` |
| Analytics/event telemetry product layer | `my_agent` already has tracing; product analytics does not solve core coding-agent usability. | Revisit for opt-in usage telemetry or cost reporting after core loop stabilization. | `aider/analytics.py:60`, `aider/analytics.py:213` |
| Help/documentation vector RAG | Useful for Aider's own docs, but less important than repository code retrieval for a coding agent. | Revisit when `my_agent` needs product documentation QA or external-doc augmentation. | `aider/help.py:84`, `aider/help.py:133` |
| Watch mode / IDE comment triggers | Useful for IDE workflows, but not needed for making `my_agent` a usable coding agent baseline. | Revisit after CLI and core coding loop are reliable. | `aider/watch.py:65`, `aider/watch.py:181` |
| Exception-based mode switching | `SwitchCoder` works pragmatically in Aider, but `my_agent` has explicit run/result/state contracts. | If mode switching is added, prefer explicit state transitions. | `aider/commands.py:1190`, `aider/main.py:1165` |

## 6. Candidate Mapping to `my_agent`

| BC | Possible `my_agent` Module / File | Integration Difficulty | Benefit | Risk |
|---|---|---|---|---|
| BC-01 | `src/agents/run_loop.py`, `src/agents/coding_agent.py`, new `src/agents/coding_loop.py` | Medium | Stronger autonomous coding loop | Could blur generic runner and coding-specific policy |
| BC-02 | `src/agents/context_chunks.py`, `src/agents/repo_context.py`, `src/agents/model_turn.py` | Low to medium | Cleaner prompt assembly and token accounting | Duplicated memory/context state |
| BC-03 | New `src/agents/repo_map.py`, `src/agents/repo_context.py`, `src/agents/workspace_code.py` | High | Major improvement for large repos | Dependency and stale-index complexity |
| BC-04 | New `src/agents/edit_protocols.py`, `src/agents/patches.py`, `src/agents/edit_tools.py` | Medium | Model/edit-protocol flexibility | Premature abstraction if only one protocol is used |
| BC-05 | `src/agents/patches.py`, `src/agents/edit_tools.py`, `src/agents/tool_observations.py` | Low to medium | Better self-repair after patch failures | Too much fuzzy matching can be unsafe |
| BC-06 | `src/agents/selected_files.py`, `src/agents/workspace.py`, `src/agents/coding_cli.py` | Medium | Safer editable/read-only file control | Duplicate approval authority |
| BC-07 | New `src/agents/git_tools.py`, `src/agents/coding_cli.py`, `src/agents/trajectory.py` | Medium to high | Reversible, inspectable agent edits | Git mistakes can be destructive |
| BC-08 | `src/agents/verification.py`, `src/agents/shell_tools.py`, `src/agents/tool_observations.py` | Medium | More reliable repair loop | Slow or noisy validation |
| BC-09 | `src/agents/coding_agent.py`, new `src/agents/coding_plan.py` | Medium | Better complex-task handling | Extra cost and vague plans |
| BC-10 | `src/agents/context_mentions.py`, `src/agents/repo_context.py`, `src/agents/selected_files.py` | Medium | Better first-turn file selection | Over-selection and token bloat |
| BC-11 | `src/agents/coding_cli.py`, `src/agents/workspace_code_tools.py` | Low to medium | More usable local CLI | Commands may bypass tool policies if poorly integrated |
| BC-12 | `src/agents/model_settings.py`, `src/agents/models.py`, `src/agents/coding_agent.py` | Low to medium | Better model-specific coding behavior | Stale model metadata |

## 7. Questions for the Main Agent

1. Should the first implementation target Aider-style reflection inside the existing `run_loop.py`, or should coding runs get a thin `coding_loop.py` wrapper over the generic runner?
2. Should repo-map indexing use a new lightweight Python implementation, or should `my_agent` integrate with the existing external CodeGraph index when available?
3. Should Git checkpointing be native and opt-in first, or should it become the default for edit-capable coding profiles?
4. Is SEARCH/REPLACE worth adding as a second edit protocol now, or should only the current patch DSL get better failure feedback?
5. Should planner/editor mode be added before or after repo-map/context-selection improvements?
6. How interactive should the CLI become: Aider-like slash commands, flags only, or both?
7. Should verification failures automatically trigger reflection, or require a policy/approval setting?
8. Which state should own selected editable/read-only files: existing `SelectedFilesState`, `RunState`, or a coding-run-specific state object?

## 8. Summary Points for the Main Agent

1. Aider's strongest borrowing candidate is its repo map: symbol extraction, graph ranking, mention personalization, caching, and token-bounded rendering.
2. Its second strongest candidate is the reflection loop that turns edit/lint/test failures into the next model-visible repair message.
3. `my_agent` should not copy Aider's monolithic `Coder`; it should map the useful lifecycle into existing runner, state, tool, verification, and context modules.
4. Aider's `ChatChunks` is a clean reference for making prompt context explicit and inspectable.
5. `my_agent` already has a stronger function-tool runtime than Aider, so slash commands should be borrowed as CLI ergonomics only.
6. Aider's edit failure feedback is immediately useful even if `my_agent` keeps its current patch DSL.
7. Git checkpoint/diff/undo support is a high-value reliability feature but needs careful native implementation and tests.
8. Architect/editor and context-selection modes are useful sequential workflows, not evidence of a general subagent or parallel-task design.
9. Aider's local shell model should not be borrowed as a safety model; only its validation-feedback loop is suitable.
10. The reference should guide concrete coding-agent behavior, not broad framework redesign.
