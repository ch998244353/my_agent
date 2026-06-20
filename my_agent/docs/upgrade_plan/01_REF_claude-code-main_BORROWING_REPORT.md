# Borrowing Report: claude-code-main

## 1. Metadata

| Field | Value |
| --- | --- |
| Project name | `claude-code-main` |
| Project path | `C:\Users\ch\Desktop\ai agent学习\reference\claude-code-main` |
| Subagent name | `reference_claude-code-main_analysis` |
| Report scope | Phase 2 borrowing-candidate analysis only |
| Output file | `my_agent/docs/upgrade_plan/01_REF_claude-code-main_BORROWING_REPORT.md` |

Codegraph status: Codegraph was attempted with `projectPath=C:\Users\ch\Desktop\ai agent学习\reference\claude-code-main`. `codegraph_status` returned a broader workspace-sized index and `codegraph_files` for `src` returned no scoped files. `codegraph_context` also returned symbols from sibling reference projects. Therefore Codegraph was treated as unavailable for reliable scoped inspection of this reference, and the source evidence below comes from targeted reads under `reference/claude-code-main/` only.

Key files reviewed:

- `my_agent/docs/upgrade_plan/00_MY_AGENT_BASELINE.md`
- `reference/claude-code-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/claude-code-main/src/QueryEngine.ts`
- `reference/claude-code-main/src/query.ts`
- `reference/claude-code-main/src/Tool.ts`
- `reference/claude-code-main/src/tools.ts`
- `reference/claude-code-main/src/services/tools/toolOrchestration.ts`
- `reference/claude-code-main/src/services/tools/toolExecution.ts`
- `reference/claude-code-main/src/services/tools/StreamingToolExecutor.ts`
- `reference/claude-code-main/src/tools/FileReadTool/FileReadTool.ts`
- `reference/claude-code-main/src/tools/FileEditTool/FileEditTool.ts`
- `reference/claude-code-main/src/tools/FileWriteTool/FileWriteTool.ts`
- `reference/claude-code-main/src/tools/GlobTool/GlobTool.ts`
- `reference/claude-code-main/src/tools/GrepTool/GrepTool.ts`
- `reference/claude-code-main/src/tools/BashTool/BashTool.tsx`
- `reference/claude-code-main/src/utils/permissions/permissions.ts`
- `reference/claude-code-main/src/utils/permissions/permissionSetup.ts`
- `reference/claude-code-main/src/hooks/useCanUseTool.tsx`
- `reference/claude-code-main/src/utils/attachments.ts`
- `reference/claude-code-main/src/native-ts/file-index/index.ts`
- `reference/claude-code-main/src/tools/ToolSearchTool/ToolSearchTool.ts`
- `reference/claude-code-main/src/utils/toolSearch.ts`
- `reference/claude-code-main/src/tools/LSPTool/LSPTool.ts`
- `reference/claude-code-main/src/services/lsp/manager.ts`
- `reference/claude-code-main/src/utils/sessionStorage.ts`
- `reference/claude-code-main/src/services/compact/autoCompact.ts`
- `reference/claude-code-main/src/services/compact/microCompact.ts`
- `reference/claude-code-main/src/memdir/findRelevantMemories.ts`
- `reference/claude-code-main/src/tools/TodoWriteTool/TodoWriteTool.ts`
- `reference/claude-code-main/src/tools/TaskCreateTool/TaskCreateTool.ts`
- `reference/claude-code-main/src/tools/TaskUpdateTool/TaskUpdateTool.ts`
- `reference/claude-code-main/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
- `reference/claude-code-main/src/tools/AgentTool/AgentTool.tsx`
- `reference/claude-code-main/src/tools/AgentTool/runAgent.ts`
- `reference/claude-code-main/src/tools/AgentTool/agentToolUtils.ts`
- `reference/claude-code-main/src/tools/AgentTool/loadAgentsDir.ts`
- `reference/claude-code-main/src/tools/AgentTool/built-in/verificationAgent.ts`
- `reference/claude-code-main/src/utils/telemetry/sessionTracing.ts`
- `reference/claude-code-main/src/services/analytics/index.ts`
- `reference/claude-code-main/src/utils/queryProfiler.ts`

## 2. One-sentence project positioning

`claude-code-main` is a TypeScript terminal coding-agent CLI architecture whose most useful reference value for `my_agent` is its layered coding loop around schema-first tools, permission-gated side effects, durable transcript state, file/edit safety, retrieval surfaces, and subagent delegation.

## 3. Capability matrix related to `my_agent`

| Capability | Exists in Reference | my_agent Current State | Borrowing Value | Evidence Files |
| --- | --- | --- | --- | --- |
| Agent abstraction | Partial: no single generic `Agent` class; behavior is composed from `QueryEngine`, `query()`, `ToolUseContext`, and data-driven agent definitions. | Has explicit `Agent`, `AgentCapabilities`, and profile builder. | Borrow selected concepts for data-driven coding profiles and subagent definitions, not the whole object model. | `src/QueryEngine.ts`, `src/query.ts`, `src/tools/AgentTool/loadAgentsDir.ts`, baseline `src/agents/agent.py` |
| Runner / run loop | Yes: `query()` and `queryLoop()` are async-generator model/tool loops; `QueryEngine.submitMessage()` is the session wrapper. | Has synchronous `Runner.run_sync()` and `run_agent_loop()`. | Borrow the wrapper-vs-loop separation and event-yield idea conceptually; avoid copying product-coupled loop complexity. | `src/QueryEngine.ts`, `src/query.ts`, baseline `src/agents/runner.py`, `src/agents/run_loop.py` |
| Tool system | Yes: `Tool`, `ToolDef`, `buildTool()`, tool metadata for read-only, destructive, concurrency, UI/result mapping, and deferred discovery. | Has `FunctionTool`, `ToolRegistry`, planning/execution split, approval checks. | High value for richer tool metadata and unified internal contracts for built-in, external, and deferred tools. | `src/Tool.ts`, `src/tools.ts`, `src/services/tools/toolExecution.ts`, baseline `src/agents/tools.py` |
| File discovery | Yes: `GlobTool`, `GrepTool`, fuzzy file index, and `@file` attachment resolution. | Has workspace inventory, file search, literal text search, and simple related-file heuristics. | Borrow targeted pieces: line-aware mentions, fuzzy file lookup, and bounded result formatting. | `src/tools/GlobTool/GlobTool.ts`, `src/tools/GrepTool/GrepTool.ts`, `src/native-ts/file-index/index.ts`, `src/utils/attachments.ts`, baseline `src/agents/workspace_code.py` |
| Mention resolution | Yes: `@file`, quoted mentions, line ranges, agent mentions, and MCP resource mentions become attachments. | Has task mention detection and inventory resolution, but not explicit `@file#Lx-Ly` attachment flow. | High value for improving user-directed context selection without semantic RAG. | `src/utils/attachments.ts`, `src/components/PromptInput/PromptInput.tsx`, baseline `src/agents/context_mentions.py` |
| Repo context | Yes, through dynamic system/user context, attachments, prompt sections, memory, Git/environment context, and tool-driven search. | Has `RepoContextBuilder` with inventory, selected files, mentioned symbols, and literal matches. | Borrow prompt-section boundaries and attachment-based context projection; preserve `my_agent`'s simpler repo context builder. | `src/context.ts`, `src/constants/prompts.ts`, `src/utils/attachments.ts`, baseline `src/agents/repo_context.py` |
| Patch / diff / edit | Yes: direct file edit/write tools, read-before-edit state, stale-write checks, structured patch/diff output, LSP notification after writes. | Has `apply_patch`, patch parse/dry-run/apply, workspace path validation, approval policy; no first-class stale-write state or structured Git diff model. | Very high value for edit safety concepts, especially read-before-edit and stale-write detection. | `src/tools/FileEditTool/FileEditTool.ts`, `src/tools/FileWriteTool/FileWriteTool.ts`, `src/tools/FileReadTool/FileReadTool.ts`, baseline `src/agents/edit_tools.py`, `src/agents/patches.py` |
| Run state / task state | Yes: `QueryEngine` mutable session state plus app-state task/todo tools. | Has `RunState`, snapshots, pending approvals, CLI state envelope, trajectory evidence. | Borrow task/todo as explicit tools and durable transcript design; keep existing run-state contracts. | `src/QueryEngine.ts`, `src/tools/TodoWriteTool/TodoWriteTool.ts`, `src/tools/TaskCreateTool/TaskCreateTool.ts`, baseline `src/agents/run_state.py` |
| Session / memory / compression | Yes: append-only transcript chains, sidechain transcripts, compaction, microcompaction, memory file selection. | Has `AgentSession`, `JsonSession`, rule/model summarizer, basic compaction. | Borrow append-only transcript and "durable history vs projected context" separation; avoid copying full compaction stack. | `src/utils/sessionStorage.ts`, `src/services/compact/autoCompact.ts`, `src/services/compact/microCompact.ts`, `src/memdir/findRelevantMemories.ts`, baseline `src/agents/memory.py` |
| Tracing | Yes: OpenTelemetry spans, Perfetto support, analytics events, query profiler checkpoints. | Has trace/span abstraction and JSONL/in-memory tracing. | Borrow checkpoint naming around query phases and optional tool/model span attributes; avoid product analytics machinery. | `src/utils/telemetry/sessionTracing.ts`, `src/services/analytics/index.ts`, `src/utils/queryProfiler.ts`, baseline `src/agents/tracing.py` |
| Guardrails | Yes: permission modes, allow/deny/ask rules, hooks, tool validation, auto-mode classifier, shell/file safety. | Has input/output guardrails, tool guardrails, shell and patch approval policies. | Borrow layered side-effect gate: schema validation, tool-specific validation, hooks/policy, then call. | `src/utils/permissions/permissions.ts`, `src/utils/permissions/permissionSetup.ts`, `src/services/tools/toolExecution.ts`, baseline `src/agents/guardrails.py`, `src/agents/tool_guardrails.py` |
| RAG / repo map / symbol index | Partial: no classic vector RAG found; has practical retrieval through file index, `Grep`/`Glob`, LSP, tool search, and memory relevance selection. | No semantic RAG, no persistent runtime symbol graph; has inventory, literal search, AST outline for single Python files. | Borrow practical retrieval first: fuzzy files, LSP-style code intelligence contract, and tool search; defer vector RAG. | `src/native-ts/file-index/index.ts`, `src/tools/LSPTool/LSPTool.ts`, `src/tools/ToolSearchTool/ToolSearchTool.ts`, `src/memdir/findRelevantMemories.ts`, baseline `src/agents/workspace_code.py` |
| Planning / coding loop | Yes: plan mode tool, task tools, todo tool, query loop continuation after tool results. | Has functional but basic coding loop; no planner/executor split or mature multi-iteration coding workflow. | Borrow lightweight plan/task tools and loop nudges, not full plan-mode UI. | `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`, `src/tools/TodoWriteTool/TodoWriteTool.ts`, `src/query.ts`, baseline `src/agents/coding_cli.py` |
| Testing / validation | Yes: shell execution, LSP diagnostics, and verification subagent with strict command-evidence protocol. | Has shell/test tools and configurable verification command observations. | Borrow verification-agent workflow as a later role/subagent pattern; borrow command-evidence requirement sooner. | `src/tools/AgentTool/built-in/verificationAgent.ts`, `src/tools/BashTool/BashTool.tsx`, baseline `src/agents/verification.py` |
| Subagent / parallel task | Yes: `AgentTool`, `runAgent()`, sync/async subagents, sidechain transcripts, worktree isolation, tool filtering, background lifecycle. | No explicit subagents, no parallel tool execution or task decomposition. | Borrow a minimal subagent contract later; start with synchronous delegated worker and transcript isolation, not full teams/background/worktrees. | `src/tools/AgentTool/AgentTool.tsx`, `src/tools/AgentTool/runAgent.ts`, `src/tools/AgentTool/agentToolUtils.ts` |

## 4. Borrowing candidates

## BC-01: Schema-first tool contract with capability metadata

- Problem solved:
  - A coding agent needs every capability to expose a consistent contract for model schema, validation, permission classification, execution, result mapping, read-only/destructive status, and concurrency safety.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/Tool.ts`
    - `reference/claude-code-main/src/tools.ts`
    - `reference/claude-code-main/src/services/tools/toolExecution.ts`
  - Classes / functions:
    - `Tool`
    - `ToolDef`
    - `ToolUseContext`
    - `ToolPermissionContext`
    - `buildTool()`
    - `checkPermissionsAndCallTool()`
- Execution flow:
  - Tool definitions declare schemas and behavior metadata.
  - `buildTool()` fills conservative defaults.
  - The runtime validates model input with the tool schema before side effects.
  - Tool-specific validation and permission checks run before `tool.call()`.
  - Tool output is mapped into model-visible tool-result blocks.
- Value for `my_agent`:
  - `my_agent` already has `FunctionTool` and `ToolRegistry`, but the reference shows useful metadata that would make coding-agent tools safer and easier to schedule.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tools.py`
  - `src/agents/tool_schema.py`
  - `src/agents/tool_execution.py`
  - `src/agents/tool_planning.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add optional metadata fields to `FunctionTool`: `read_only`, `destructive`, `concurrency_safe`, `search_hint`, and `max_result_chars`.
  - Keep existing handler and registry APIs stable where possible.
  - Use the metadata in tool planning and approval decisions.
- Risks:
  - Adding metadata without immediate callers would violate the project preference for minimal changes. Only add fields when the execution loop or tool planner actually uses them.
- Evidence files:
  - `src/Tool.ts:123`
  - `src/Tool.ts:158`
  - `src/Tool.ts:362`
  - `src/Tool.ts:721`
  - `src/Tool.ts:783`
  - `src/services/tools/toolExecution.ts:599`

## BC-02: Layered tool execution boundary

- Problem solved:
  - Model tool calls can be malformed, unsafe, blocked by policy, or require user approval; the runtime needs one clear path from model input to side effect.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/services/tools/toolExecution.ts`
    - `reference/claude-code-main/src/services/tools/toolOrchestration.ts`
    - `reference/claude-code-main/src/hooks/useCanUseTool.tsx`
  - Classes / functions:
    - `runToolUse()`
    - `checkPermissionsAndCallTool()`
    - `runTools()`
    - `hasPermissionsToUseTool()`
- Execution flow:
  - Resolve tool by name or alias.
  - Validate input schema.
  - Run tool-specific validation.
  - Run pre-tool hooks and permission checks.
  - Ask, deny, or allow.
  - Execute the tool and map output to model feedback.
- Value for `my_agent`:
  - `my_agent` already separates planning and execution; borrowing the stricter staged order would improve side-effect safety and model feedback consistency.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tool_execution.py`
  - `src/agents/tool_planning.py`
  - `src/agents/tool_guardrails.py`
  - `src/agents/tool_observations.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Make the execution path explicitly ordered: schema parse, tool boundary validation, approval/guardrail policy, execution, output interpretation, output guardrail, observation.
  - Keep the current Python abstractions; do not import the reference's hook system.
- Risks:
  - The reference has broad product-specific hooks and classifier behavior. Copying that would overcomplicate `my_agent`.
- Evidence files:
  - `src/services/tools/toolExecution.ts:337`
  - `src/services/tools/toolExecution.ts:599`
  - `src/services/tools/toolExecution.ts:1207`
  - `src/hooks/useCanUseTool.tsx:37`

## BC-03: Concurrency-safe tool batching

- Problem solved:
  - Read-only searches and file reads can run concurrently, while writes and shell commands often need serial execution.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/services/tools/toolOrchestration.ts`
    - `reference/claude-code-main/src/services/tools/StreamingToolExecutor.ts`
  - Classes / functions:
    - `runTools()`
    - `partitionToolCalls()`
    - `runToolsConcurrently()`
    - `runToolsSerially()`
    - `StreamingToolExecutor`
- Execution flow:
  - Tool calls are partitioned into consecutive concurrency-safe batches and single unsafe calls.
  - Safe batches run with a concurrency cap and emit ordered results.
  - Unsafe calls run serially with updated context after each call.
- Value for `my_agent`:
  - Useful for later performance work once `FunctionTool` has `concurrency_safe` metadata.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tool_planning.py`
  - `src/agents/tool_execution.py`
  - `src/agents/run_loop.py`
- Recommended borrowing method: not_recommended_now
- Implementation sketch:
  - Defer until the runtime supports async tool execution.
  - First add metadata and keep execution serial.
  - Later batch only tools marked read-only and concurrency-safe.
- Risks:
  - `my_agent` is synchronous and sequential today. Adding concurrency before async runtime support would be a large architectural change.
- Evidence files:
  - `src/services/tools/toolOrchestration.ts:19`
  - `src/services/tools/toolOrchestration.ts:91`
  - `src/services/tools/StreamingToolExecutor.ts:40`

## BC-04: Read-before-edit and stale-write protection

- Problem solved:
  - A coding agent can overwrite user changes or tool/linter changes if it edits a file it has not read recently.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/FileReadTool/FileReadTool.ts`
    - `reference/claude-code-main/src/tools/FileEditTool/FileEditTool.ts`
    - `reference/claude-code-main/src/tools/FileWriteTool/FileWriteTool.ts`
  - Classes / functions:
    - `FileReadTool`
    - `FileEditTool`
    - `FileWriteTool`
    - `readFileState`
    - `getFileModificationTime()`
- Execution flow:
  - File reads update a read-state cache with content, timestamp, and range metadata.
  - Edit/write validation rejects writes to existing files unless the file was fully read.
  - Edit/write re-checks modification time and content before writing.
  - After writing, the read-state cache is updated to the new content timestamp.
- Value for `my_agent`:
  - This directly strengthens `my_agent`'s edit tool without changing the public patch format.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/workspace.py`
  - `src/agents/workspace_code.py`
  - `src/agents/edit_tools.py`
  - `src/agents/patches.py`
  - `src/agents/run_state.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Track file reads in run context or selected-file state with path, mtime, and whether the read was full or partial.
  - Before applying a patch to an existing file, require a recent full read or a dry-run-only path.
  - Fail fast with a model-visible observation when the file changed after the read.
- Risks:
  - Patches can be generated from repo context rather than explicit file reads. The policy should not block all edits blindly; it should be introduced where the agent has a clear read state.
- Evidence files:
  - `src/tools/FileReadTool/FileReadTool.ts:337`
  - `src/tools/FileEditTool/FileEditTool.ts:275`
  - `src/tools/FileEditTool/FileEditTool.ts:301`
  - `src/tools/FileWriteTool/FileWriteTool.ts:198`
  - `src/tools/FileWriteTool/FileWriteTool.ts:266`

## BC-05: Structured file discovery tools

- Problem solved:
  - Coding agents need fast, bounded ways to find files and text without flooding model context.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/GlobTool/GlobTool.ts`
    - `reference/claude-code-main/src/tools/GrepTool/GrepTool.ts`
    - `reference/claude-code-main/src/native-ts/file-index/index.ts`
  - Classes / functions:
    - `GlobTool`
    - `GrepTool`
    - fuzzy file-index `search()`
- Execution flow:
  - `GlobTool` finds files by pattern and returns relative paths with limits.
  - `GrepTool` runs regex content search with modes for filenames, counts, and content snippets.
  - The native file index performs fuzzy path search with top-k scoring.
- Value for `my_agent`:
  - `my_agent` has file search and literal text search, but this reference suggests separate explicit tools for path-glob, regex grep, and fuzzy path lookup.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/workspace_code.py`
  - `src/agents/workspace_code_tools.py`
  - `src/agents/workspace_inventory.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Keep existing workspace safety boundary.
  - Add or refine tools around `find_workspace_files` and `search_workspace_code` to distinguish glob, literal search, regex search, and fuzzy filename search.
  - Return bounded, relative, line-aware results.
- Risks:
  - Regex search and fuzzy indexing add complexity. Start with direct wrappers over existing inventory and `rg`-like behavior if available.
- Evidence files:
  - `src/tools/GlobTool/GlobTool.ts:57`
  - `src/tools/GrepTool/GrepTool.ts:160`
  - `src/native-ts/file-index/index.ts:173`

## BC-06: Explicit `@file` and line-range attachment flow

- Problem solved:
  - Users often know the exact file or line range that matters; the agent should turn that into bounded context reliably.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/utils/attachments.ts`
    - `reference/claude-code-main/src/components/PromptInput/PromptInput.tsx`
  - Classes / functions:
    - `processAtMentionedFiles()`
    - `extractAtMentionedFiles()`
    - `parseAtMentionedFileLines()`
    - `generateFileAttachment()`
- Execution flow:
  - User input is scanned for `@path`, quoted `@"path with spaces"`, and `#Lx-Ly` suffixes.
  - Mentioned files are permission-checked.
  - Directories are listed with truncation.
  - Files are read into attachments with offset/limit.
  - Large files and PDFs are bounded or represented as references.
- Value for `my_agent`:
  - `my_agent` has mention detection, but this would make user-directed context much more precise.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/context_mentions.py`
  - `src/agents/repo_context.py`
  - `src/agents/context_chunks.py`
  - `src/agents/selected_files.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Extend mention parsing to support `@path` and optional `#Lstart-Lend`.
  - Resolve against `WorkspaceInventory`.
  - Add selected-file entries with line ranges and render them before general repo inventory.
- Risks:
  - Over-eager mention parsing can misinterpret emails, decorators, or prose. Keep syntax explicit and test quoted paths and line ranges.
- Evidence files:
  - `src/utils/attachments.ts:775`
  - `src/utils/attachments.ts:1890`
  - `src/utils/attachments.ts:2751`
  - `src/utils/attachments.ts:2830`
  - `src/utils/attachments.ts:3014`

## BC-07: Append-only transcript with parent chains

- Problem solved:
  - A coding agent needs crash recovery, resume, auditability, and subagent transcripts without treating in-memory run state as the only source of truth.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/utils/sessionStorage.ts`
    - `reference/claude-code-main/src/QueryEngine.ts`
    - `reference/claude-code-main/src/tools/AgentTool/runAgent.ts`
  - Classes / functions:
    - `getTranscriptPath()`
    - `getAgentTranscriptPath()`
    - `recordTranscript()`
    - `recordSidechainTranscript()`
    - `loadTranscriptFile()`
    - `QueryEngine.submitMessage()`
- Execution flow:
  - User messages are persisted before the model loop starts.
  - Transcript messages are appended as JSONL-like chains with UUID and parent relationships.
  - Subagents write sidechain transcripts.
  - Resume/recovery loads transcript files and related metadata.
- Value for `my_agent`:
  - `my_agent` has session memory and pending approval snapshots, but append-only transcript chains would improve audit and resume behavior.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/memory.py`
  - `src/agents/coding_state.py`
  - `src/agents/trajectory.py`
  - `src/agents/run_recording.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Keep `JsonSession` for conversation state.
  - Add an append-only run transcript or trajectory mode where each user/model/tool item has an id, parent id, timestamp, and source.
  - Persist the accepted user task before the first model call.
- Risks:
  - Duplicating `trajectory.py` and `JsonSession` responsibilities could create competing logs. Define one audit log boundary before implementing.
- Evidence files:
  - `src/utils/sessionStorage.ts:202`
  - `src/utils/sessionStorage.ts:247`
  - `src/utils/sessionStorage.ts:1405`
  - `src/utils/sessionStorage.ts:3467`
  - `src/QueryEngine.ts:436`
  - `src/tools/AgentTool/runAgent.ts:732`

## BC-08: Durable history separated from model-context projection

- Problem solved:
  - Long coding sessions need durable history, but the model can only receive a bounded projected context.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/services/compact/autoCompact.ts`
    - `reference/claude-code-main/src/services/compact/microCompact.ts`
    - `reference/claude-code-main/src/memdir/findRelevantMemories.ts`
    - `reference/claude-code-main/src/query.ts`
  - Classes / functions:
    - `autoCompactIfNeeded()`
    - `microcompactMessages()`
    - `findRelevantMemories()`
    - `queryLoop()`
- Execution flow:
  - Before model calls, the loop checks context pressure.
  - Microcompaction trims selected tool-result content.
  - Autocompaction summarizes or replaces older conversation content.
  - Relevant memory files are selected separately from transcript storage.
- Value for `my_agent`:
  - `my_agent` already has compaction. The reference reinforces keeping durable session records separate from model input projection.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/memory.py`
  - `src/agents/context_chunks.py`
  - `src/agents/model_turn.py`
- Recommended borrowing method: copy_concept_only
- Implementation sketch:
  - Preserve all session/trajectory records.
  - Build a separate `TurnContext` projection that can summarize, omit, or clip old tool outputs without deleting durable records.
  - Track when compaction occurred in model-visible context.
- Risks:
  - The reference compaction system is heavily product- and model-cache-coupled. Copying its mechanics would be too much for `my_agent`.
- Evidence files:
  - `src/services/compact/autoCompact.ts:241`
  - `src/services/compact/microCompact.ts:253`
  - `src/memdir/findRelevantMemories.ts:39`
  - `src/query.ts:402`
  - `src/query.ts:453`

## BC-09: Tool search and deferred tool loading

- Problem solved:
  - Large tool ecosystems consume too much prompt budget if every tool schema is always sent to the model.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/ToolSearchTool/ToolSearchTool.ts`
    - `reference/claude-code-main/src/utils/toolSearch.ts`
  - Classes / functions:
    - `ToolSearchTool`
    - `searchToolsWithKeywords()`
    - `extractDiscoveredToolNames()`
    - `getDeferredToolsDelta()`
- Execution flow:
  - Some tools are deferred.
  - The model searches by keyword or selects by explicit name.
  - Tool references in tool results tell later requests which schemas are discovered.
  - Compaction carries discovered tool names forward.
- Value for `my_agent`:
  - Not needed for the current small tool set, but valuable if MCP/plugin/tool count grows.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tools.py`
  - `src/agents/tool_schema.py`
  - `src/agents/context_chunks.py`
- Recommended borrowing method: not_recommended_now
- Implementation sketch:
  - Defer until `my_agent` has enough tools for tool-schema token pressure.
  - If needed, introduce simple tool categories or a search tool before implementing reference-style discovered-tool state.
- Risks:
  - Adds complexity to every model turn and can make tool availability harder to reason about.
- Evidence files:
  - `src/tools/ToolSearchTool/ToolSearchTool.ts:304`
  - `src/tools/ToolSearchTool/ToolSearchTool.ts:328`
  - `src/utils/toolSearch.ts:543`
  - `src/utils/toolSearch.ts:644`

## BC-10: LSP-style code intelligence tool

- Problem solved:
  - Text search is not enough for definitions, references, hover docs, symbols, or call hierarchy.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/LSPTool/LSPTool.ts`
    - `reference/claude-code-main/src/services/lsp/manager.ts`
    - `reference/claude-code-main/src/services/lsp/passiveFeedback.ts`
  - Classes / functions:
    - `LSPTool`
    - `initializeLspServerManager()`
    - `reinitializeLspServerManager()`
    - `registerLSPNotificationHandlers()`
- Execution flow:
  - LSP manager initializes in the background.
  - The code-intelligence tool validates file paths and waits for initialization if pending.
  - It opens files when needed and sends LSP requests.
  - Passive diagnostics can be registered and delivered later.
- Value for `my_agent`:
  - `my_agent` lacks a persistent symbol index. An LSP-like tool contract is a strong future direction after file/edit basics are stable.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/workspace_code.py`
  - `src/agents/workspace_code_tools.py`
  - possible future `src/agents/code_intelligence.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Start with a provider-neutral code-intelligence interface for Python: outline, definitions, references, diagnostics.
  - Implement with Python AST or external tools first; add real LSP later.
- Risks:
  - LSP server lifecycle, language support, and diagnostics delivery are substantial operational concerns.
- Evidence files:
  - `src/tools/LSPTool/LSPTool.ts:127`
  - `src/tools/LSPTool/LSPTool.ts:224`
  - `src/services/lsp/manager.ts:145`
  - `src/services/lsp/passiveFeedback.ts`

## BC-11: Lightweight task and todo tools

- Problem solved:
  - Coding agents need visible task state to avoid losing track of multi-step work and to encourage verification before final response.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/TodoWriteTool/TodoWriteTool.ts`
    - `reference/claude-code-main/src/tools/TaskCreateTool/TaskCreateTool.ts`
    - `reference/claude-code-main/src/tools/TaskUpdateTool/TaskUpdateTool.ts`
  - Classes / functions:
    - `TodoWriteTool`
    - `TaskCreateTool`
    - `TaskUpdateTool`
- Execution flow:
  - The model updates a todo list or structured task records through tools.
  - Task state is stored in app/session state.
  - Tool results nudge continued task tracking and verification.
- Value for `my_agent`:
  - `my_agent` has run state but no first-class planning/task state exposed as a tool.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_state.py`
  - `src/agents/tools.py`
  - `src/agents/coding_agent.py`
  - possible future `src/agents/task_state.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add a small `TaskList` dataclass and one `update_task_list` tool.
  - Keep it session-scoped and JSON-serializable.
  - Add a final-turn nudge when tasks close without verification evidence.
- Risks:
  - A task system can become a product feature by itself. Keep it minimal and avoid teams, owners, blockers, or hooks at first.
- Evidence files:
  - `src/tools/TodoWriteTool/TodoWriteTool.ts:31`
  - `src/tools/TodoWriteTool/TodoWriteTool.ts:65`
  - `src/tools/TaskCreateTool/TaskCreateTool.ts:48`
  - `src/tools/TaskUpdateTool/TaskUpdateTool.ts:88`

## BC-12: Read-only plan mode as a permission mode

- Problem solved:
  - For complex coding work, an agent may need an explicit exploration/planning phase where edits and shell writes are blocked.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
    - `reference/claude-code-main/src/utils/permissions/permissionSetup.ts`
    - `reference/claude-code-main/src/utils/messages.ts`
  - Classes / functions:
    - `EnterPlanModeTool`
    - `prepareContextForPlanMode()`
    - `applyPermissionUpdate()`
- Execution flow:
  - Model calls plan-mode tool.
  - Runtime changes permission mode to `plan`.
  - Tool result tells the model to explore and design without editing.
  - Exit from plan mode requires a separate approval path.
- Value for `my_agent`:
  - Useful as a conceptual boundary for planner/executor behavior without introducing a second agent yet.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_policies.py`
  - `src/agents/run_loop.py`
  - `src/agents/coding_agent.py`
- Recommended borrowing method: copy_concept_only
- Implementation sketch:
  - Add a coding profile option or run config flag for read-only planning.
  - In planning mode, expose read/search tools but not patch/shell write tools.
  - Leave plan approval UI out of scope until a concrete CLI flow needs it.
- Risks:
  - Tool-based plan-mode transitions can trap the agent if exit/approval is not designed. `my_agent` should first support externally selected read-only planning.
- Evidence files:
  - `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts:36`
  - `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts:77`
  - `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts:103`

## BC-13: Verification as a specialized read-only subagent

- Problem solved:
  - LLM coding agents tend to treat passing tests or code review as enough; verification needs independent command evidence and adversarial probes.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/AgentTool/built-in/verificationAgent.ts`
    - `reference/claude-code-main/src/tools/AgentTool/AgentTool.tsx`
    - `reference/claude-code-main/src/tools/BashTool/BashTool.tsx`
  - Classes / functions:
    - `VERIFICATION_AGENT`
    - `AgentTool`
    - `BashTool`
- Execution flow:
  - A verification agent receives the original task, changed files, and approach.
  - It is prohibited from editing project files.
  - It must run build/test/lint or concrete behavioral checks where applicable.
  - It emits a strict verdict.
- Value for `my_agent`:
  - `my_agent` already has verification command observations. A future verification role would make validation more autonomous and evidence-driven.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/verification.py`
  - `src/agents/coding_cli.py`
  - future subagent module
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Before subagents exist, strengthen `VerificationRunner` to require command, output, and result evidence.
  - Later, implement a read-only verification profile that can run tests and shell reads but cannot patch files.
- Risks:
  - A model-based verifier can still hallucinate evidence unless the runtime records actual command outputs and enforces read-only tools.
- Evidence files:
  - `src/tools/AgentTool/built-in/verificationAgent.ts`
  - `src/tools/BashTool/BashTool.tsx:420`

## BC-14: Minimal subagent delegation contract

- Problem solved:
  - Some coding tasks benefit from delegated exploration, verification, or specialized roles without polluting the main conversation state.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/tools/AgentTool/AgentTool.tsx`
    - `reference/claude-code-main/src/tools/AgentTool/runAgent.ts`
    - `reference/claude-code-main/src/tools/AgentTool/loadAgentsDir.ts`
    - `reference/claude-code-main/src/tools/AgentTool/agentToolUtils.ts`
  - Classes / functions:
    - `AgentTool`
    - `runAgent()`
    - `resolveAgentTools()`
    - `finalizeAgentTool()`
    - `parseAgentFromJson()`
- Execution flow:
  - Parent model calls `AgentTool` with prompt, type, and execution options.
  - Runtime resolves agent definition and allowed tools.
  - Child agent gets its own context, messages, read-file cache, and optional transcript.
  - Child re-enters the main query loop.
  - Parent receives summarized/finalized child output.
- Value for `my_agent`:
  - High future value, but only after the base coding loop and transcript state are stable.
- Possible mapping to `my_agent` files / modules:
  - future `src/agents/subagents.py`
  - `src/agents/coding_agent.py`
  - `src/agents/run_loop.py`
  - `src/agents/memory.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Start with one synchronous child run function that takes an `Agent`, task text, allowed tool names, and isolated session.
  - Store child transcript separately.
  - Do not implement background agents, worktrees, teams, or remote isolation initially.
- Risks:
  - The reference subagent system is broad and product-coupled. Copying it directly would violate the minimal-change policy and destabilize `my_agent`.
- Evidence files:
  - `src/tools/AgentTool/AgentTool.tsx:196`
  - `src/tools/AgentTool/AgentTool.tsx:603`
  - `src/tools/AgentTool/runAgent.ts:248`
  - `src/tools/AgentTool/runAgent.ts:697`
  - `src/tools/AgentTool/runAgent.ts:748`
  - `src/tools/AgentTool/agentToolUtils.ts:122`
  - `src/tools/AgentTool/loadAgentsDir.ts:73`

## BC-15: Query-phase tracing and profiling checkpoints

- Problem solved:
  - Coding-agent latency and correctness issues are hard to diagnose without phase-level traces around context building, model calls, tool execution, and compaction.
- Reference implementation:
  - Files:
    - `reference/claude-code-main/src/utils/telemetry/sessionTracing.ts`
    - `reference/claude-code-main/src/utils/queryProfiler.ts`
    - `reference/claude-code-main/src/services/analytics/index.ts`
    - `reference/claude-code-main/src/query.ts`
  - Classes / functions:
    - `startInteractionSpan()`
    - `startLLMRequestSpan()`
    - `startToolSpan()`
    - `queryCheckpoint()`
    - `logEvent()`
- Execution flow:
  - User interaction starts a root span.
  - LLM requests and tool calls are child spans.
  - Query profiler records named checkpoints for key loop phases.
  - Analytics events record product-specific decisions and failures.
- Value for `my_agent`:
  - `my_agent` already has tracing; adding consistent query checkpoints could make coding-loop upgrades easier to debug.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tracing.py`
  - `src/agents/run_loop.py`
  - `src/agents/model_turn.py`
  - `src/agents/tool_execution.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add named trace/span attributes for `repo_context`, `model_turn`, `tool_plan`, `tool_execute`, `verification`, and `compaction`.
  - Keep local JSONL/in-memory tracing rather than adding OpenTelemetry.
- Risks:
  - Product analytics and telemetry exporters are not needed. Borrow only local checkpoint structure.
- Evidence files:
  - `src/utils/telemetry/sessionTracing.ts:176`
  - `src/utils/telemetry/sessionTracing.ts:307`
  - `src/utils/telemetry/sessionTracing.ts:494`
  - `src/utils/queryProfiler.ts:69`
  - `src/query.ts:1363`

## 5. Designs not suitable for current borrowing

| Design | Why Not Suitable Now | Future Condition | Evidence |
| --- | --- | --- | --- |
| Full `query.ts` loop | Highly coupled to Anthropic streaming, prompt caching, feature gates, UI state, compaction, telemetry, and product-specific recovery. | Consider only after `my_agent` has async streaming runtime requirements. | `src/query.ts` |
| Full React/Ink terminal UI | `my_agent` is a Python package plus CLI; the task is agent capability, not terminal product UI. | Only if a rich interactive TUI becomes a product goal. | `src/screens/REPL.tsx`, `src/components/` |
| Streaming tool executor | Requires async streaming model/tool execution and careful cancellation semantics. | After `my_agent` supports async loop and tool metadata. | `src/services/tools/StreamingToolExecutor.ts` |
| Full plugin ecosystem | Large manifest/cache/marketplace/hook/MCP/LSP system; too broad for current architecture. | After core tool, permission, and session boundaries are mature. | `src/utils/plugins/schemas.ts`, `src/utils/plugins/pluginLoader.ts` |
| Full MCP client normalization | Valuable but expands external tool trust and lifecycle surface. | After internal tool contracts and permissions are stable. | `src/services/mcp/client.ts`, `src/tools/MCPTool/MCPTool.ts` |
| Auto-mode classifier permissions | Requires another model/classifier path and complex policy state. | Only after simpler allow/deny/ask policies prove insufficient. | `src/utils/permissions/permissions.ts`, `src/utils/permissions/yoloClassifier.ts` |
| Background/team/remote subagents | Too product-specific and operationally heavy for `my_agent` now. | After synchronous subagents and transcript isolation work. | `src/tools/AgentTool/AgentTool.tsx`, `src/tasks/LocalAgentTask/LocalAgentTask.tsx`, `src/remote/` |
| Worktree isolation inside subagents | Useful but involves Git state, cleanup, branch handling, and path translation. | After native Git checkpointing and subagent execution exist. | `src/tools/AgentTool/AgentTool.tsx:590`, `src/utils/worktree.ts` |
| Full LSP server lifecycle | Needs per-language server management, startup, shutdown, diagnostics, and plugin configs. | After a minimal Python code-intelligence interface exists. | `src/services/lsp/manager.ts`, `src/tools/LSPTool/LSPTool.ts` |
| Product analytics stack | Events are product-specific and include sinks, sampling, GrowthBook gates, and metadata policy. | If `my_agent` later needs production telemetry; not for local teaching-grade runtime. | `src/services/analytics/index.ts`, `src/services/analytics/sink.ts` |

## 6. Candidate mapping to `my_agent`

| BC | Possible `my_agent` Module / File | Integration Difficulty | Benefit | Risk |
| --- | --- | --- | --- | --- |
| BC-01 | `src/agents/tools.py`, `src/agents/tool_schema.py`, `src/agents/tool_planning.py` | Medium | Safer and more expressive tool planning | Metadata without immediate use becomes abstraction bloat |
| BC-02 | `src/agents/tool_execution.py`, `src/agents/tool_guardrails.py` | Medium | Clearer side-effect boundary | Duplicating existing guardrail checks |
| BC-03 | `src/agents/tool_execution.py`, `src/agents/run_loop.py` | High | Faster read/search batches | Conflicts with synchronous runtime |
| BC-04 | `src/agents/edit_tools.py`, `src/agents/patches.py`, `src/agents/workspace.py` | Medium | Prevents stale writes and user-change overwrites | Could block legitimate edits from repo-context-only reads |
| BC-05 | `src/agents/workspace_code.py`, `src/agents/workspace_code_tools.py` | Low/Medium | Better file and content discovery | Regex/fuzzy behavior needs bounded output tests |
| BC-06 | `src/agents/context_mentions.py`, `src/agents/repo_context.py` | Low/Medium | Precise user-directed context | Mention parser false positives |
| BC-07 | `src/agents/trajectory.py`, `src/agents/memory.py`, `src/agents/coding_state.py` | Medium | Better audit and recovery | Overlap with current session and trajectory files |
| BC-08 | `src/agents/memory.py`, `src/agents/context_chunks.py` | Medium | Longer coding sessions with stable audit history | Compaction can hide needed context if summaries are weak |
| BC-09 | `src/agents/tools.py`, `src/agents/context_chunks.py` | High | Scales large tool ecosystems | Not needed until tool count grows |
| BC-10 | `src/agents/workspace_code_tools.py`, future `code_intelligence.py` | High | Definitions/references/diagnostics | LSP lifecycle complexity |
| BC-11 | `src/agents/run_state.py`, `src/agents/coding_agent.py` | Low/Medium | Better multi-step coding loop discipline | Task system can grow too broad |
| BC-12 | `src/agents/coding_policies.py`, `src/agents/coding_agent.py` | Low/Medium | Clean read-only planning boundary | Requires clear exit/approval behavior |
| BC-13 | `src/agents/verification.py`, future subagent support | Medium/High | Stronger validation evidence | Verifier must not be able to fake command evidence |
| BC-14 | future `src/agents/subagents.py`, `src/agents/run_loop.py` | High | Delegated exploration and verification | Large change to state/session model |
| BC-15 | `src/agents/tracing.py`, `src/agents/run_loop.py` | Low/Medium | Easier debugging of loop upgrades | Avoid product telemetry sprawl |

## 7. Questions for the main agent

1. Should `my_agent` keep patch editing as the only write path, or should it add direct file edit/write tools with read-before-edit state?
2. Should task/todo state be exposed to the model as a tool, or kept as internal CLI/run-loop state?
3. Is the next upgrade expected to preserve the synchronous runtime, or can an async tool/model loop be introduced?
4. Should append-only transcript evidence extend `trajectory.py`, `JsonSession`, or a new separate run log?
5. Should `my_agent` target Python-only code intelligence first, or define a provider-neutral interface that can later support LSP?
6. Should verification remain a configured command loop, or become a specialized read-only agent profile once subagents exist?
7. What is the desired boundary between repo context auto-selection and explicit user mentions such as `@file#L10-L20`?

## 8. Summary points for the main agent

1. The most immediately borrowable reference designs are tool metadata, staged tool execution, read-before-edit safety, explicit file mentions, and append-only transcripts.
2. The reference does not have a clean `Agent`/`Runner` pair to copy; its useful structure is the separation between a session wrapper and a model/tool loop.
3. `my_agent` already has many baseline pieces, so borrowing should be incremental and targeted rather than a rewrite.
4. The reference's strongest edit-safety idea is read-state plus stale-write rejection before file modification.
5. Practical retrieval is more relevant than vector RAG right now: glob, grep, fuzzy file lookup, line-range mentions, and eventually code intelligence.
6. Subagents are valuable but should start as a small synchronous delegation contract, not background teams, worktrees, or remote agents.
7. Verification should become evidence-driven: commands run, output observed, result recorded.
8. The full plugin/MCP/LSP/telemetry stacks are useful design references but are too broad for current borrowing.
9. Codegraph could not be used reliably for this scoped reference, so this report relies on targeted source reads under `reference/claude-code-main/`.
