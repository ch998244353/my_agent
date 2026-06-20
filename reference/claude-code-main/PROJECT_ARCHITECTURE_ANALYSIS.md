# Project Architecture Analysis

## 1. Scope and Method

This document analyzes the target repository as a coding-agent architecture reference. The analysis is read-only except for this Markdown file. No source files, git metadata, build files, or unrelated files were intentionally modified.

Method used:

- Main-agent direct source review of the CLI entrypoints, runtime loop, model adapter, tools, permissions, state, persistence, plugins, skills, MCP, and memory modules.
- Six focused read-only subagents covering project mapping, runtime flow, core modules, state/memory, advanced capabilities, and reuse/upgrade guidance.
- Direct filesystem and `rg` inspection for evidence because Codegraph was not usable for this checkout.

Codegraph note: Codegraph tooling was available and reported an index, but `codegraph_node` could not find `QueryEngine` for the requested project path even though direct filesystem inspection finds `src/QueryEngine.ts`. Earlier Codegraph file queries also pointed at sibling or broader workspace content. Therefore, this document treats Codegraph as available but not reliable for this target checkout, and all conclusions below are grounded in direct path/function evidence.

Important repository-shape finding: although the provided project instructions describe a Python coding-agent project, the actual repository is a TypeScript/Bun source tree. The target root contains `README.md` and `src/`; no `package.json`, lockfile, or `tsconfig` was found in this checkout. Direct source count under `src/` is 1,902 files.

## 2. Project Positioning

This repository is a TypeScript terminal coding-agent CLI source snapshot. Its own `README.md` describes it as a leaked `src/` directory for Claude Code, with TypeScript, Bun, and React/Ink. Independent source evidence supports that positioning:

- CLI bootstrap is in `src/entrypoints/cli.tsx:33` via `main()`.
- The primary Commander-based CLI assembly is in `src/main.tsx`, with command/action setup around `src/main.tsx:902` and the default action around `src/main.tsx:1006`.
- Interactive terminal UI is React/Ink based: `src/replLauncher.tsx:12` launches `REPL`, and `src/screens/REPL.tsx:572` defines `REPL()`.
- Headless/SDK execution flows through `src/cli/print.ts:976` and `src/QueryEngine.ts:1186`.
- Anthropic/Claude model transport is implemented in `src/services/api/claude.ts`, including `queryModelWithoutStreaming()` at `src/services/api/claude.ts:709` and `queryModelWithStreaming()` at `src/services/api/claude.ts:752`.
- The tool platform is central: `src/Tool.ts:362` defines `Tool`, `src/tools.ts:193` registers base tools, and `src/services/tools/toolExecution.ts:337` executes a model-requested tool use.

The product goal is a terminal-first software-engineering agent. It combines conversational model turns, file operations, shell execution, tool approval, subagents, MCP tools, plugins, skills, persistent transcripts, memory, compaction, LSP/code lookup, remote/bridge modes, and a React terminal UI.

Legal/provenance caution: because the repository identifies itself as leaked product source, it should be used as an architecture study reference, not as code to copy verbatim.

## 3. Directory Structure

Top-level target root:

| Path | Role |
| --- | --- |
| `README.md` | Descriptive snapshot README. Useful for orientation, but source files are the stronger evidence. |
| `src/` | Main TypeScript/TSX source tree. |

Key `src/` directories:

| Directory | Architectural role | Representative evidence |
| --- | --- | --- |
| `src/entrypoints/` | Process/bootstrap entrypoints for CLI, SDK/MCP, and auxiliary modes. | `src/entrypoints/cli.tsx:33`, `src/entrypoints/mcp.ts:35` |
| `src/main.tsx` | Main CLI parser/orchestrator; initializes settings, plugins, MCP, permissions, and mode-specific execution. | `src/main.tsx:902`, `src/main.tsx:1747`, `src/main.tsx:2704` |
| `src/cli/` | Headless, SDK, structured IO, print/non-interactive execution. | `src/cli/print.ts:976`, `src/cli/structuredIO.ts:533` |
| `src/screens/`, `src/components/`, `src/hooks/`, `src/ink/` | React/Ink terminal UI, REPL screens, interactive permission UI, state hooks. | `src/screens/REPL.tsx:572`, `src/hooks/useCanUseTool.tsx:37` |
| `src/query.ts`, `src/query/`, `src/QueryEngine.ts` | Conversation lifecycle and model/tool loop. | `src/query.ts:219`, `src/QueryEngine.ts:184` |
| `src/services/api/` | Anthropic API client, streaming parser, retries, fallback, request normalization. | `src/services/api/claude.ts:752`, `src/services/api/withRetry.ts:170` |
| `src/Tool.ts`, `src/tools.ts`, `src/tools/` | Tool contract, tool registry, individual built-in and extension tools. | `src/Tool.ts:158`, `src/Tool.ts:362`, `src/tools.ts:193` |
| `src/services/tools/` | Tool scheduling, streaming execution, hooks, permission/execution pipeline. | `src/services/tools/toolOrchestration.ts:19`, `src/services/tools/toolExecution.ts:337` |
| `src/utils/permissions/` | Permission modes, allow/deny/ask rules, shell/file safety, auto-mode classifier. | `src/utils/permissions/permissions.ts:473`, `src/utils/permissions/permissionSetup.ts:872` |
| `src/services/mcp/`, `src/tools/MCPTool/` | MCP client integration and normalization of MCP tools into the internal `Tool` contract. | `src/services/mcp/client.ts:2226`, `src/tools/MCPTool/MCPTool.ts:27` |
| `src/utils/plugins/`, `src/plugins/` | Plugin manifests, loader, marketplace/cache behavior, plugin-contributed commands, agents, skills, hooks, MCP/LSP. | `src/utils/plugins/schemas.ts:884`, `src/utils/plugins/pluginLoader.ts:3096` |
| `src/skills/`, `src/tools/SkillTool/` | Markdown-defined skills, skill command loading, model-invoked skill execution. | `src/skills/loadSkillsDir.ts:270`, `src/tools/SkillTool/SkillTool.ts:331` |
| `src/memdir/`, `src/services/SessionMemory/`, `src/services/compact/` | File memory, relevant-memory retrieval, session memory, automatic/micro/session compaction. | `src/memdir/memdir.ts:419`, `src/services/compact/autoCompact.ts:241` |
| `src/state/`, `src/bootstrap/` | Runtime app state store and process/session singleton state. | `src/state/AppStateStore.ts:89`, `src/bootstrap/state.ts:45` |
| `src/utils/sessionStorage.ts`, `src/utils/conversationRecovery.ts`, `src/utils/sessionRestore.ts` | JSONL transcript persistence, resume, restoration, snapshots. | `src/utils/sessionStorage.ts:1408`, `src/utils/conversationRecovery.ts:456` |
| `src/tasks/`, `src/tools/AgentTool/`, `src/utils/forkedAgent.ts`, `src/coordinator/` | Subagents, background/local/remote tasks, handoff-like delegation. | `src/tools/AgentTool/AgentTool.tsx:196`, `src/tools/AgentTool/runAgent.ts:248` |
| `src/services/lsp/`, `src/tools/LSPTool/` | LSP-backed code intelligence tool surface. | `src/services/lsp/manager.ts:145`, `src/tools/LSPTool/LSPTool.ts:127` |
| `src/bridge/`, `src/remote/`, `src/server/` | Remote sessions, bridge messaging, server/control modes. | `src/bridge/replBridge.ts`, `src/remote/remotePermissionBridge.ts` |
| `src/types/`, `src/schemas/` | Shared command/hook/plugin/log types and schemas. | `src/types/command.ts`, `src/types/logs.ts`, `src/utils/settings/types.ts:1104` |

## 4. Agent Runtime Flow

### 4.1 Bootstrap and mode selection

The outer executable path starts in `src/entrypoints/cli.tsx:33`. That bootstrap fast-paths lightweight operations such as version output, special MCP/daemon/remote modes, and then dynamically imports `../main.js` to reduce startup cost before full CLI initialization.

`src/main.tsx` then constructs the Commander program. Its pre-action hook performs startup work such as settings, policy, plugin/cache, MCP/resource prefetch, migrations, and permission context preparation. Important evidence:

- Commander setup: `src/main.tsx:902`.
- Pre-action/init work: `src/main.tsx:905`.
- Permission initialization: `src/main.tsx:1747` calls `initializeToolPermissionContext()`.
- MCP tool/command/resource assembly: `src/main.tsx:2704` calls `getMcpToolsCommandsAndResources()`.
- Plugin cache prefetch appears near `src/main.tsx:282` with `loadAllPluginsCacheOnly()`.

The runtime then branches into interactive REPL, headless/print mode, MCP/server modes, background/team/worktree modes, and other subcommands.

### 4.2 Interactive path

Interactive user input enters through the terminal UI:

1. `src/screens/REPL.tsx:4903` wires `PromptInput` to `onSubmit`.
2. `src/utils/handlePromptSubmit.ts:120` defines `handlePromptSubmit()`.
3. `src/utils/handlePromptSubmit.ts:396` delegates to `executeUserInput()`, which normalizes input and calls the query callback.
4. `src/screens/REPL.tsx:2661` builds a `ToolUseContext`, gathers current tools/MCP clients/resources and prompt context, then iterates `query()`.
5. Stream events from `query()` are handled by REPL event handling and appended/rendered into UI state.

### 4.3 Headless/SDK path

Non-interactive execution flows through:

1. `src/cli/print.ts:976` (`runHeadlessStreaming()`).
2. `src/QueryEngine.ts:1186` (`ask()`).
3. `src/QueryEngine.ts:184` (`QueryEngine`) as the session wrapper.
4. `src/QueryEngine.ts:209` (`submitMessage()`), which appends user input to mutable session messages and persists it before the model call.
5. `src/query.ts:219` (`query()`), which delegates to the main query loop.

`QueryEngine` is the closest thing to a runner. The codebase does not have a single generic `Agent` class or `Runner` class. Instead, agent behavior is composed from `QueryEngine`, `query()`, `ToolUseContext`, tool definitions, and optional subagent definitions.

### 4.4 Core model/tool loop

The main loop is in `src/query.ts`:

1. `query()` starts at `src/query.ts:219`.
2. It delegates into `queryLoop()`, which maintains per-turn state such as messages, compaction tracking, max-output recovery, stop-hook state, pending tool summaries, and transition reason.
3. Before each API call, `queryLoop()` projects durable history into `messagesForQuery`, applying compact-boundary behavior, token budgets, snipping, microcompaction, context collapse, and auto-compaction.
4. `deps.callModel()` is invoked around `src/query.ts:658` with:
   - `messages`
   - `systemPrompt`
   - `userContext`
   - `thinkingConfig`
   - `tools`
   - MCP tool state
   - active agents and allowed agent types
   - fallback model and query tracking metadata
5. Production model transport is `queryModelWithStreaming()` in `src/services/api/claude.ts:752`.
6. As stream chunks arrive, assistant messages are yielded. Any `tool_use` content blocks are collected as `ToolUseBlock[]`.
7. The loop does not trust only `stop_reason === "tool_use"`; `src/query.ts` treats actual `tool_use` blocks as the continuation signal.
8. If no tool use is present, it runs stop hooks/token-budget checks/recovery and returns a completed turn.
9. If tool use is present, execution continues through `StreamingToolExecutor` or `runTools()`.
10. Tool results are normalized into user `tool_result` messages, appended to the conversation, and the loop continues.

Runtime flow summary:

```text
PromptInput.onSubmit()
  -> handlePromptSubmit()
  -> processUserInput()
  -> REPL.onQueryImpl() or QueryEngine.submitMessage()
  -> query() / queryLoop()
  -> deps.callModel()
  -> queryModelWithStreaming()
  -> Anthropic streaming events
  -> assistant messages and tool_use blocks
  -> StreamingToolExecutor or runTools()
  -> runToolUse()
  -> validation, hooks, permissions, tool.call()
  -> user tool_result messages
  -> next queryLoop turn
  -> final assistant result when no tool_use blocks remain
```

### 4.5 Model request and stream parsing

`src/services/api/claude.ts` is the Anthropic adapter. Key points:

- `queryModelWithoutStreaming()` starts at `src/services/api/claude.ts:709`.
- `queryModelWithStreaming()` starts at `src/services/api/claude.ts:752`.
- API clients are constructed by `getAnthropicClient()` at `src/services/api/client.ts:88`.
- Streaming sends `anthropic.beta.messages.create({ ..., stream: true })` around `src/services/api/claude.ts:1822`.
- Stream parsing handles event types such as `message_start`, `content_block_start`, `content_block_delta`, and `message_delta` around `src/services/api/claude.ts:1980`, `src/services/api/claude.ts:1995`, `src/services/api/claude.ts:2053`, and `src/services/api/claude.ts:2213`.
- Retry/fallback behavior is centralized in `src/services/api/withRetry.ts:170`.

This layer is not provider-neutral. It contains Claude/Anthropic-specific message normalization, beta headers, tool-use formats, prompt caching behavior, streaming fallback, and provider variants such as Bedrock/Vertex-style paths elsewhere in the model utilities.

### 4.6 Tool scheduling and execution

Tool execution has a staged pipeline:

- `src/services/tools/toolOrchestration.ts:19` defines `runTools()`.
- `runTools()` partitions model-requested tool calls into concurrency-safe batches and serial batches. Its `partitionToolCalls()` checks each tool's `isConcurrencySafe()` after schema parsing.
- `src/services/tools/StreamingToolExecutor.ts:40` defines `StreamingToolExecutor`, which can begin safe tools while the model stream is still arriving and buffers results so the turn remains ordered.
- `src/services/tools/toolExecution.ts:337` defines `runToolUse()`.
- `src/services/tools/toolExecution.ts:599` defines `checkPermissionsAndCallTool()`, the key boundary where input schemas, tool-specific validation, hooks, permissions, and `tool.call()` meet.

The execution order is:

1. Resolve the tool by name or alias.
2. Validate model-provided input with the tool's Zod schema.
3. Run tool-specific `validateInput` if present.
4. Run pre-tool hooks through `runPreToolUseHooks()` in `src/services/tools/toolHooks.ts:435`.
5. Resolve permission decisions through the current `CanUseToolFn`.
6. Deny, ask, or allow.
7. Call `tool.call()` around `src/services/tools/toolExecution.ts:1207`.
8. Map tool output to Anthropic `tool_result` blocks.
9. Run post-tool hooks through `runPostToolUseHooks()` in `src/services/tools/toolHooks.ts:39`.

### 4.7 Subagents and handoff-like delegation

Subagents are implemented as tools and task/runtime contexts, not as a generic handoff framework:

- `src/tools/AgentTool/AgentTool.tsx:196` defines `AgentTool`.
- `src/tools/AgentTool/AgentTool.tsx:603` prepares `runAgent` parameters.
- `src/tools/AgentTool/runAgent.ts:248` defines `runAgent()`.
- `src/tools/AgentTool/runAgent.ts:748` re-enters the same `query()` loop with agent-specific context.
- `src/tools/AgentTool/loadAgentsDir.ts:73` loads agent definitions.
- `src/utils/forkedAgent.ts` and `src/tasks/LocalAgentTask/LocalAgentTask.tsx:270` support forked/local/background agent execution.
- `src/tools/AgentTool/agentToolUtils.ts:389` contains handoff classification logic.

Conclusion: "handoff" exists mostly as subagent/task delegation plus classifier review, not as a single standalone primitive.

### 4.8 Interruption, approval, and recovery

Important runtime controls:

- `QueryEngine.interrupt()` aborts the active `AbortController` at `src/QueryEngine.ts:1155`.
- `queryLoop()` handles aborted streams/tools by yielding synthetic interruption or missing tool-result blocks.
- Interactive approval flows through `src/hooks/useCanUseTool.tsx:37`, `src/hooks/toolPermission/PermissionContext.ts`, and `src/hooks/toolPermission/handlers/interactiveHandler.ts`.
- Headless/SDK approval uses `src/cli/print.ts:4267` and `src/cli/structuredIO.ts:533`.
- Retry and fallback are handled by `src/services/api/withRetry.ts:170` and fallback-trigger handling in `query.ts`.
- Prompt-too-long, media-size, max-output, stop-hook blocking, token-budget continuation, and compaction retries are handled inside `queryLoop()`.

## 5. Core Modules

### 5.1 Query lifecycle

| Element | Path | Responsibility |
| --- | --- | --- |
| `QueryEngine` | `src/QueryEngine.ts:184` | Owns conversation lifecycle state for SDK/headless use: mutable messages, abort controller, usage, permission denials, read-file state, transcript writes, final result extraction. |
| `submitMessage()` | `src/QueryEngine.ts:209` | Processes user input, appends messages, records transcript, calls `query()`, handles streamed messages and final public result. |
| `ask()` | `src/QueryEngine.ts:1186` | Convenience wrapper for headless/SDK querying. |
| `query()` | `src/query.ts:219` | Async-generator entry to the agent loop. |
| `queryLoop()` | `src/query.ts` | Stateful model/tool continuation loop; handles compaction, tool execution, stop hooks, interruptions, fallback, and completion. |
| `buildQueryConfig()` | `src/query/config.ts:29` | Builds feature-gated query configuration. |

Reusable idea: keep a small public session wrapper (`QueryEngine`) separate from the full async-generator loop (`query`). That separation is useful. However, the actual loop is highly product-coupled and should not be copied wholesale.

### 5.2 Model adapter

| Element | Path | Responsibility |
| --- | --- | --- |
| `queryModelWithStreaming()` | `src/services/api/claude.ts:752` | Streaming Anthropic model call and event parsing. |
| `queryModelWithoutStreaming()` | `src/services/api/claude.ts:709` | Non-streaming/fallback request path. |
| `getAnthropicClient()` | `src/services/api/client.ts:88` | API client construction. |
| `withRetry()` | `src/services/api/withRetry.ts:170` | Retry and fallback behavior. |
| model utilities | `src/utils/model/` | Model names, aliases, capabilities, provider-specific behavior. |

Reusable idea: isolate provider transport and stream parsing from the agent loop. Migration difficulty is high because request/response formats, tool schemas, prompt caching, and beta features are Claude-specific.

### 5.3 Tool system

| Element | Path | Responsibility |
| --- | --- | --- |
| `ToolUseContext` | `src/Tool.ts:158` | Runtime context passed to tools: options, permissions, MCP state, app-state accessors, messages, progress callbacks, abort controller, read-file state, and agent/session metadata. |
| `Tool` | `src/Tool.ts:362` | Unified tool contract: schema, prompt text, permissions, read-only/destructive/concurrency flags, result mapping, UI metadata. |
| `ToolDef` | `src/Tool.ts:721` | Definition input for `buildTool()`. |
| `buildTool()` | `src/Tool.ts:783` | Applies defaults and returns a built tool. |
| `getAllBaseTools()` | `src/tools.ts:193` | Built-in tool registry. |
| `getTools()` | `src/tools.ts:271` | Filters tools by mode/settings/availability. |
| `assembleToolPool()` | `src/tools.ts:345` | Combines built-ins and MCP tools with sorting/deduplication. |

Representative built-in tools:

- `AgentTool`: `src/tools/AgentTool/AgentTool.tsx:196`
- `BashTool`: `src/tools/BashTool/BashTool.tsx:420`
- `PowerShellTool`: `src/tools/PowerShellTool/PowerShellTool.tsx:272`
- `FileReadTool`: `src/tools/FileReadTool/FileReadTool.ts:337`
- `FileEditTool`: `src/tools/FileEditTool/FileEditTool.ts:86`
- `SkillTool`: `src/tools/SkillTool/SkillTool.ts:331`
- `ToolSearchTool`: `src/tools/ToolSearchTool/ToolSearchTool.ts:304`
- `MCPTool`: `src/tools/MCPTool/MCPTool.ts:27`

Not found: a distinct `FunctionTool` class or module. The closest equivalent is the `Tool` / `ToolDef` / `buildTool()` pattern.

### 5.4 Permissions and guardrails

| Element | Path | Responsibility |
| --- | --- | --- |
| `initializeToolPermissionContext()` | `src/utils/permissions/permissionSetup.ts:872` | Builds initial permission context from settings/CLI/modes. |
| `hasPermissionsToUseTool` | `src/utils/permissions/permissions.ts:473` | Central `CanUseToolFn` implementation. |
| `useCanUseTool()` | `src/hooks/useCanUseTool.tsx:37` | Interactive permission bridge from React UI to permission engine. |
| filesystem safety | `src/utils/permissions/filesystem.ts` | Read/write/path safety checks. |
| yolo/auto classifier | `src/utils/permissions/yoloClassifier.ts:1012` | Auto-mode action classifier. |
| interactive handler | `src/hooks/toolPermission/handlers/interactiveHandler.ts` | Queues user approval prompts. |

Reusable idea: use layered safety. Validate model/tool input at the tool boundary, apply tool-specific validation, then evaluate permission rules and hooks before side effects. This is one of the strongest parts to study.

### 5.5 Prompt, context, skills, and memory

| Element | Path | Responsibility |
| --- | --- | --- |
| `getSystemPrompt()` | `src/constants/prompts.ts:444` | Builds system prompt sections. |
| `systemPromptSection()` | `src/constants/systemPromptSections.ts:20` | Names/cache-controls prompt sections. |
| `resolveSystemPromptSections()` | `src/constants/systemPromptSections.ts:43` | Resolves static/dynamic prompt sections. |
| `fetchSystemPromptParts()` | `src/utils/queryContext.ts:44` | Fetches system prompt parts for query execution. |
| `getSystemContext()` / `getUserContext()` | `src/context.ts` | Collects environment, git, memory, and user-context material. |
| `loadMemoryPrompt()` | `src/memdir/memdir.ts:419` | Loads memory-directory prompt material. |
| `findRelevantMemories()` | `src/memdir/findRelevantMemories.ts:39` | Query-time relevant memory lookup. |
| `SkillTool` | `src/tools/SkillTool/SkillTool.ts:331` | Model-invoked skill execution. |
| `createSkillCommand()` | `src/skills/loadSkillsDir.ts:270` | Converts Markdown skill files into commands/tools. |
| `loadSkillsFromSkillsDir()` | `src/skills/loadSkillsDir.ts:407` | Loads skills from configured directories. |

Reusable idea: split stable/static prompt sections from dynamic/cache-breaking context. Also treat skills as Markdown-defined capability packages with metadata and allowed tool constraints.

### 5.6 Compaction and session memory

| Element | Path | Responsibility |
| --- | --- | --- |
| `autoCompactIfNeeded()` | `src/services/compact/autoCompact.ts:241` | Automatic compaction threshold behavior. |
| `microcompactMessages()` | `src/services/compact/microCompact.ts:253` | Smaller context cleanup/compaction pass. |
| `trySessionMemoryCompaction()` | `src/services/compact/sessionMemoryCompact.ts:514` | Session-memory backed compaction strategy. |
| `initSessionMemory()` | `src/services/SessionMemory/sessionMemory.ts:357` | Initializes session-memory subsystem. |

Reusable idea: durable history and model context should be separate. Keep the append-only transcript intact while projecting/summarizing only the context sent to the model.

### 5.7 Persistence, resume, and export

| Element | Path | Responsibility |
| --- | --- | --- |
| `getTranscriptPath()` | `src/utils/sessionStorage.ts:202` | Computes JSONL transcript path under the projects directory. |
| `getAgentTranscriptPath()` | `src/utils/sessionStorage.ts:247` | Stores subagent transcripts separately. |
| `recordTranscript()` | `src/utils/sessionStorage.ts:1408` | Appends durable message chains. |
| `loadTranscriptFile()` | `src/utils/sessionStorage.ts:3472` | Loads transcript JSONL files. |
| conversation recovery | `src/utils/conversationRecovery.ts:456` | Loads/resumes a conversation chain. |
| session restore | `src/utils/sessionRestore.ts:95` | Restores selected state from logs/snapshots. |
| prompt history | `src/history.ts:290` | Separate prompt history storage, not full conversation transcript. |
| export command | `src/commands/export/export.tsx:49` | Plain-text conversation export. |

Reusable idea: append-only JSONL with `uuid` / `parentUuid` chains and leaf selection is a strong design for resumable agent sessions. The code writes the user message before the model call, improving recovery after crashes or kills.

Not found: a database-backed conversation store. The active durable path appears to be JSONL transcripts plus snapshots.

### 5.8 Plugins, MCP, and extensions

| Element | Path | Responsibility |
| --- | --- | --- |
| `PluginManifestSchema` | `src/utils/plugins/schemas.ts:884` | Plugin manifest validation. |
| `loadAllPlugins()` | `src/utils/plugins/pluginLoader.ts:3096` | Full plugin discovery/loading. |
| `loadAllPluginsCacheOnly()` | `src/utils/plugins/pluginLoader.ts:3137` | Startup/cache-only plugin load path. |
| `refreshActivePlugins()` | `src/utils/plugins/refresh.ts:72` | Explicit plugin refresh. |
| `loadPluginMcpServers()` | `src/utils/plugins/mcpPluginIntegration.ts:131` | Plugin-provided MCP servers. |
| `getMcpToolsCommandsAndResources()` | `src/services/mcp/client.ts:2226` | MCP tool/command/resource integration. |
| `callMCPToolWithUrlElicitationRetry()` | `src/services/mcp/client.ts:2813` | MCP call retry with elicitation support. |
| `MCPTool` | `src/tools/MCPTool/MCPTool.ts:27` | Internal wrapper for external MCP tools. |

Reusable idea: normalize every external extension mechanism into the same internal `Tool` contract. Do not start a new agent by copying the full plugin ecosystem; it is broad and product-coupled.

### 5.9 Shell execution and sandboxing

| Element | Path | Responsibility |
| --- | --- | --- |
| `BashTool` | `src/tools/BashTool/BashTool.tsx:420` | Unix-like shell command execution tool. |
| `PowerShellTool` | `src/tools/PowerShellTool/PowerShellTool.tsx:272` | PowerShell command execution tool. |
| shell exec | `src/utils/Shell.ts:181` | Command execution wrapper. |
| sandbox adapter | `src/utils/sandbox/sandbox-adapter.ts:927` | Sandbox runtime integration. |
| bash permissions | `src/tools/BashTool/bashPermissions.ts` | Shell command classification/prefix rules. |

Reusable idea: shell execution should be treated as a first-class tool with risk classification, permission rules, streaming output, background execution, abort handling, and optional sandbox wrapping.

## 6. Key Data Structures

| Structure | Path | Purpose |
| --- | --- | --- |
| `Message[]` | Imported widely from `src/types/message.js`; active usage in `src/QueryEngine.ts:184` and `src/query.ts` | Core conversation sequence passed between session wrapper, loop, UI, transcript, compaction, and tools. Source definition file is missing in this checkout; see risks. |
| `QueryEngine` fields | `src/QueryEngine.ts:184` | Holds `mutableMessages`, abort controller, permission denials, total usage, read-file state, and discovered skill/memory state. |
| `ToolUseContext` | `src/Tool.ts:158` | Large execution context passed into every tool; includes app-state access, permission context, MCP state, messages, abort controller, progress handlers, agent IDs, and query tracking. |
| `ToolPermissionContext` | `src/Tool.ts:123` | Permission mode plus allow/deny/ask rule state and mode-specific flags. |
| `Tool` | `src/Tool.ts:362` | Unified internal representation for built-in, MCP, skill, and dynamic tools. |
| `AppState` | `src/state/AppStateStore.ts:89` | UI/runtime state: settings, tasks, permissions, MCP, plugins, agent definitions, file history, attribution, todos, hooks, bridge/remote status, notifications. |
| `State` singleton | `src/bootstrap/state.ts:45` | Process/session singleton: cwd, project root, session IDs, token/cost totals, model usage, cached memory/context, prompt-cache latches, invoked skills. |
| transcript messages | `src/utils/sessionStorage.ts:101` | Durable transcript includes user, assistant, attachment, and system messages; progress messages are excluded from transcript semantics. |
| serialized logs | `src/types/logs.ts:8` | `SerializedMessage` metadata such as cwd, user type, entrypoint, session ID, timestamp, version, git branch, and slug. |
| `SessionExternalMetadata` | `src/utils/sessionState.ts:32` | Coarse external session metadata: permission mode, model, pending action, post-turn summary, task summary. |
| settings schema | `src/utils/settings/types.ts:1104` | `SettingsJson` inferred from Zod `SettingsSchema`. |
| plugin manifest | `src/utils/plugins/schemas.ts:884` | Validated plugin configuration for commands, agents, skills, hooks, MCP/LSP, settings, user config, dependencies. |
| agent definitions | `src/tools/AgentTool/loadAgentsDir.ts:73` | Data-driven agent configuration loaded from directories/plugins. |

Important gap: `src/types/message.js` is imported throughout the project, but `src/types/` in this checkout contains no `message.ts` or `message.js`. The exact `Message` union is therefore inferred from usage sites such as `src/utils/sessionStorage.ts`, `src/utils/messages.ts`, and `src/types/logs.ts`.

## 7. Reusable Modules Table

| Module / pattern | Evidence | What to borrow | Migration difficulty |
| --- | --- | --- | --- |
| Tool contract and registry | `src/Tool.ts:362`, `src/Tool.ts:783`, `src/tools.ts:193` | Schema-first tool interface, read-only/destructive/concurrency metadata, unified built-in and external tool handling. | Medium |
| Tool execution pipeline | `src/services/tools/toolExecution.ts:337`, `src/services/tools/toolExecution.ts:599`, `src/services/tools/toolOrchestration.ts:19` | Staged validation -> hooks -> permission -> call -> result mapping. | Medium |
| Streaming tool executor | `src/services/tools/StreamingToolExecutor.ts:40` | Start safe tools while model stream continues; buffer results in input order. | Medium |
| Safe file tools | `src/tools/FileReadTool/FileReadTool.ts:337`, `src/tools/FileEditTool/FileEditTool.ts:86` | Prior-read/stale-write concepts, exact edits, encoding/line-ending preservation. | Medium |
| Permission model | `src/utils/permissions/permissions.ts:473`, `src/utils/permissions/permissionSetup.ts:872`, `src/hooks/useCanUseTool.tsx:37` | Ordered allow/deny/ask policy, filesystem and shell risk checks, UI/headless approval split. | High |
| Shell execution subsystem | `src/tools/BashTool/BashTool.tsx:420`, `src/tools/PowerShellTool/PowerShellTool.tsx:272`, `src/utils/Shell.ts:181` | Streaming shell output, abort, background tasks, sandbox wrapping, command risk classification. | High |
| Query/session split | `src/QueryEngine.ts:184`, `src/query.ts:219` | Keep public session state wrapper separate from full model/tool loop. | High |
| Prompt sections | `src/constants/prompts.ts:444`, `src/constants/systemPromptSections.ts:20` | Named prompt sections with cache-aware dynamic/static boundaries. | Medium |
| Append-only transcript | `src/utils/sessionStorage.ts:1408`, `src/utils/sessionStorage.ts:3472` | JSONL event log, parent-chain recovery, pre-model user-message persistence. | Medium |
| Context compaction | `src/services/compact/autoCompact.ts:241`, `src/services/compact/microCompact.ts:253`, `src/services/compact/sessionMemoryCompact.ts:514` | Separate durable history from projected model context; compact before failure and recover after overflow. | High |
| Session/memory layers | `src/memdir/memdir.ts:419`, `src/memdir/findRelevantMemories.ts:39`, `src/services/SessionMemory/sessionMemory.ts:357` | Static memory files, relevant memory selection, session memory summaries. | Medium/High |
| Subagent isolation | `src/tools/AgentTool/runAgent.ts:248`, `src/utils/forkedAgent.ts`, `src/tasks/LocalAgentTask/LocalAgentTask.tsx:270` | Agent-specific context, sidechain transcripts, cloned mutable state, child abort controllers. | High |
| Skills | `src/skills/loadSkillsDir.ts:270`, `src/tools/SkillTool/SkillTool.ts:331` | Markdown skills with metadata, allowed tools, command conversion, model invocation. | Medium |
| Deferred tool discovery | `src/tools/ToolSearchTool/ToolSearchTool.ts:304`, `src/utils/toolSearch.ts:545` | Avoid loading all tool descriptions into every prompt; expose search/select for deferred tools. | Medium |
| MCP normalization | `src/services/mcp/client.ts:2226`, `src/tools/MCPTool/MCPTool.ts:27` | Wrap external MCP tools as first-class internal tools. | High |
| Plugin architecture | `src/utils/plugins/schemas.ts:884`, `src/utils/plugins/pluginLoader.ts:3096` | Manifest validation and plugin contribution boundaries. Study, but do not copy wholesale. | High |
| LSP code lookup | `src/services/lsp/manager.ts:145`, `src/tools/LSPTool/LSPTool.ts:127` | Offer precise code-intelligence tools instead of relying only on text search. | Medium |

## 8. Upgrade Advice for Building a New Coding Agent

Recommended implementation order:

1. Start with a minimal `Tool` interface and registry inspired by `src/Tool.ts` and `src/tools.ts`.
2. Implement safe file read/edit/write tools before broad shell execution. Borrow the ideas behind `FileReadTool` and `FileEditTool`, especially stale-write protection and read-before-edit state.
3. Add a permission model early. Keep it smaller than this repository's version, but preserve the boundary: schema validation first, then policy, then side effect.
4. Build the query loop after tools and permissions exist. Use `QueryEngine` + `query()` as a structural reference, but avoid copying the feature-gated complexity.
5. Add shell execution with conservative command classification, streaming output, abort, and optional sandbox support.
6. Add append-only JSONL transcript persistence and resume before adding multi-agent behavior.
7. Add context compaction and session memory once long conversations are real.
8. Add subagents only after the base tool loop and persistence model are stable.
9. Add skills/deferred tool search for extensibility.
10. Add MCP, LSP, and plugins last. They are powerful but expand surface area quickly.

Do not copy directly:

- `src/query.ts` as a whole. It is deeply coupled to Anthropic messages, prompt caching, feature gates, hooks, UI state, compaction, telemetry, and product behavior.
- `src/main.tsx` as an application template. It is useful for orientation but too broad as a starting point.
- `src/tools/AgentTool/AgentTool.tsx` wholesale. It mixes subagents, teams, background jobs, worktrees, remote isolation, permissions, and classification.
- `src/utils/plugins/pluginLoader.ts` wholesale. The full plugin/platform/cache/policy behavior is too large for an early agent.
- `src/services/mcp/client.ts` wholesale. Use its normalization boundary as a reference, not the complete implementation.
- The global `STATE` singleton in `src/bootstrap/state.ts:45` as-is. It is pragmatic, but it mixes many concerns and will become hard to test in a new architecture.

High-value concepts to reuse first:

- A single internal tool contract for every capability.
- Zod/runtime validation at external boundaries.
- Read-only/concurrency-safe tool metadata.
- Append-only transcript with parent IDs.
- User-message persistence before model call.
- Durable history separated from model-context projection.
- Permission modes that distinguish read, write, shell, and external tool effects.
- Skills as Markdown metadata plus executable command/tool definitions.

## 9. Architecture Risks and Maintenance Notes

### Snapshot completeness risks

- `src/types/message.js` is heavily imported, but no matching `message.ts` or `message.js` exists under `src/types/` in this checkout. This makes the exact `Message` union unavailable from source.
- Several modules appear compiled/transformed or snapshot-derived. Some imports may refer to generated or omitted files.
- The target root has no `package.json`, lockfile, or TypeScript config. Build/test/runtime commands cannot be inferred from this checkout alone.
- Codegraph was available but not reliable for this directory, so the analysis relies on direct file reading and search.

### Coupling risks

- `src/query.ts`, `src/main.tsx`, and `src/services/api/claude.ts` are large coordination files with broad responsibilities.
- `ToolUseContext` in `src/Tool.ts:158` is powerful but large; many tools implicitly depend on global app state, MCP state, permission state, and UI/session callbacks.
- The model adapter is not provider-neutral. Anthropic/Claude stream events, beta message params, prompt caching, tool-use formats, and fallback behavior are deeply integrated.
- The plugin and permission systems are load-bearing. Hooks can mutate tool input and affect permission decisions, so trust boundaries must be maintained carefully.
- Global `STATE` in `src/bootstrap/state.ts:45` mixes cwd, session IDs, token/cost tracking, prompt-cache flags, context caches, and invoked skills.

### Runtime feature uncertainty

Many paths are feature-gated or environment-dependent: streaming tool execution, context collapse, sandboxing, remote agents, background summarization, auto mode, and plugin/MCP integrations. Source inspection shows implemented paths, not which paths are enabled in any deployed build.

### Retrieval/RAG note

Classic vector/embedding RAG was not found. Retrieval exists as practical coding-agent retrieval:

- deferred tool search: `src/tools/ToolSearchTool/ToolSearchTool.ts:304`
- memory header/relevance selection: `src/memdir/findRelevantMemories.ts:39`
- LSP code lookup: `src/tools/LSPTool/LSPTool.ts:127`
- fuzzy/native file index: `src/native-ts/file-index/index.ts:173`

### Persistence risks

The JSONL transcript model is strong, but recovery is complex:

- Transcript entries use parent chains and compact-boundary behavior in `src/utils/sessionStorage.ts`.
- Resume restores conversation messages and selected snapshots/metadata, but it is not a full process snapshot.
- Some assistant transcript writes are intentionally fire-and-forget in `QueryEngine`, while user/system writes are awaited. The write queue reduces risk, but abrupt termination can still affect the latest async assistant write.

## 10. Concrete "Not Found" Findings

- Not found: Python project structure or Python agent runtime in the target checkout.
- Not found: root `package.json`, lockfile, or `tsconfig` in the target root.
- Not found: `src/types/message.ts` or `src/types/message.js`, despite many imports.
- Not found: a standalone `Agent` class that owns all agent behavior.
- Not found: a standalone `Runner` class; `QueryEngine` is the closest session runner.
- Not found: a distinct `FunctionTool` abstraction; the code uses `Tool`, `ToolDef`, and `buildTool()`.
- Not found: database-backed conversation persistence; JSONL transcript storage appears to be the active durable path.
- Not found: a classic vector database / embedding RAG pipeline.

## 11. Bottom Line

The strongest architectural lesson is not any single class. It is the layered runtime:

```text
CLI/UI input
  -> session wrapper
  -> model/tool loop
  -> provider adapter
  -> schema-first tools
  -> hooks and permissions
  -> side-effect tools
  -> durable transcript
  -> compaction/memory projection
```

For a new coding agent, the best pieces to study are the tool contract, permission pipeline, file-edit safety model, transcript/resume design, and compaction/memory separation. The least reusable pieces are the monolithic startup file, the full query loop, the plugin ecosystem, and product-specific Anthropic adapter details.
