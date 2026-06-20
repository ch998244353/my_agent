# Reference Comparison

## 1. Current `my_agent` Capability Summary

`my_agent` is already a small but functional Python coding-agent baseline. The core runtime has `Agent`, `Runner.run_sync()`, `run_agent_loop()`, `RunState`, `FunctionTool`, model calls through the OpenAI Responses adapter, workspace tools, shell/test tools, patch tools, approval pause/resume, verification feedback, tracing, trajectory JSONL, session memory, selected files, mention resolution, and lightweight repo context.

The key limitation is not the absence of a generic agent runtime. The limitation is that the coding-agent layer is still thin. It does not yet have a mature autonomous coding loop, robust edit safety, rich patch failure repair, first-class Git checkpoints, explicit plan/task state, a durable transcript/event model, semantic or graph-based repo intelligence, subagents, or sandboxed execution.

The current architecture should be preserved. The best upgrade path is to add targeted coding-agent capability loops around existing modules instead of replacing the runtime:

- Runtime boundary: `src/agents/agent.py`, `src/agents/runner.py`, `src/agents/run_loop.py`, `src/agents/run_state.py`.
- Tool boundary: `src/agents/tools.py`, `src/agents/tool_planning.py`, `src/agents/tool_execution.py`, `src/agents/tool_guardrails.py`.
- Coding workspace boundary: `src/agents/workspace.py`, `src/agents/workspace_code.py`, `src/agents/workspace_code_tools.py`, `src/agents/context_mentions.py`, `src/agents/repo_context.py`, `src/agents/selected_files.py`.
- Editing and validation boundary: `src/agents/patches.py`, `src/agents/edit_tools.py`, `src/agents/shell_tools.py`, `src/agents/verification.py`.
- Evidence and session boundary: `src/agents/trajectory.py`, `src/agents/run_recording.py`, `src/agents/memory.py`, `src/agents/coding_state.py`, `src/agents/coding_cli.py`, `src/agents/tracing.py`.

## 2. Capability Comparison Across Reference Projects

| Capability | `aider-main` | `claude-code-main` | `mini-swe-agent-main` | `openaiagent` | `OpenHands-main` | Current selection view |
| --- | --- | --- | --- | --- | --- | --- |
| Agent abstraction | Monolithic coding `Coder`; useful state ideas, not shape | Data-driven query/tool composition | Tiny Agent/Model/Environment split | Strong general `Agent` contract | App-level request assembly over external SDK | Keep current `Agent`; borrow selected runtime metadata from `openaiagent` and coding profile boundaries from `mini-swe-agent-main` |
| Runner / run loop | Reflection-oriented coding turn loop | Async query loop and session wrapper | Linear command-observation loop | Facade/executor split with explicit next steps | Loop is external | Borrow coding loop concepts from `aider-main`; borrow state outcome modeling from `openaiagent` |
| Tool system | Slash commands and edit protocols, not a modern tool registry | Strong schema-first metadata and side-effect gate | One bash tool as simplification pattern | Strong Python function-tool contract | Action/observation contracts | Borrow schema/metadata and staged execution from `claude-code-main` and `openaiagent`; keep current registry |
| File discovery | Git-aware tracked files, mentions, repo map | Glob, grep, fuzzy file index, line-aware `@file` | Shell-only | Limited local repo discovery | Glob/grep action contracts | Borrow `@file` line ranges and fuzzy discovery from `claude-code-main`; borrow repo map from `aider-main` |
| Mention resolution | File and identifier mention feedback | Explicit attachment flow with line ranges | None | None | Not central | `claude-code-main` is best for precise mention syntax; `aider-main` is useful for assistant-mentioned-file feedback |
| Repo context | Strong chunk layering and repo map | Prompt sections and attachments | Prompt-only | Not a repo context source | Workspace preflight and skills | Use `aider-main` for repo map and context chunks; use `OpenHands-main` skills later as lightweight repo knowledge |
| Patch / diff / edit | Multiple edit formats and repair feedback | Read-before-edit and stale-write safety | Shell-only editing | Structured patch result concepts | Edit action/observation shape | First borrow read-before-edit and patch repair feedback from `claude-code-main` plus `aider-main` |
| Run state / task state | Coding state inside `Coder` | Session and todo/task tools | Simple message list | Explicit run state and approval interruption | Startup/readiness states | Use `openaiagent` for loop outcome/approval modeling; use `OpenHands-main` for startup state if long-running CLI grows |
| Session / memory / compression | Active messages plus summarized history | Durable transcript vs projected context | None | Session protocol and SQLite backend | Conversation events and condenser config | Borrow durable transcript separation from `claude-code-main`; keep current memory compressor |
| Tracing / evidence | Cost and LLM history, no span tree | Query profiler and tracing | Trajectory-first evidence | Typed spans and processors | App metadata and event persistence | Strengthen local trajectory/event records; do not import product analytics |
| Guardrails | Edit permissions, token checks, validation feedback | Permission modes and layered tool gate | Format errors, limits, confirmation | Tool guardrail behavior model | Session-key and risk metadata | Borrow model-visible rejection and staged side-effect gates from `openaiagent` and `claude-code-main` |
| RAG / repo map / symbol index | Strong local repo map using tags and graph ranking | Practical retrieval, LSP concept, no vector RAG | None | Hosted file search only | Skills/microagents, no symbol index | Best next step is local repo map from `aider-main`, not vector RAG |
| Planning / coding loop | Architect/editor and reflection loop | Plan mode and todo/task tools | Prompt-guided workflow | General runner control | File-backed plan mode | Borrow file-backed plan mode from `OpenHands-main` plus Aider planner/editor flow after edit loop is reliable |
| Testing / validation | Lint/test repair feedback loop | Verification subagent and command evidence | Deterministic tests and limits | Strong runtime test patterns | Preflight/hooks status | Borrow Aider repair loop and Claude-style evidence discipline; use mini-swe deterministic model tests |
| Subagent / parallel task | No general subagents | Strong subagent system but broad | No agent subagents | Agent-as-tool and handoffs | Sub-conversation concept externalized | Defer full subagents; later start with synchronous agent-as-tool from `openaiagent` and `claude-code-main` |

## 3. Most Valuable Borrowing Candidates From Each Reference

### `aider-main`

- BC-01 Reflection-Oriented Coding Turn Loop: useful for making edit, lint, test, and model reflection part of one repair loop.
- BC-02 Prompt Context Chunk Layering: useful for making prompt sections explicit and token-aware.
- BC-03 RepoMap Symbol Graph Context: strongest local repo-intelligence reference.
- BC-05 Search/Replace Edit Failure Feedback: useful even if `my_agent` keeps its patch DSL.
- BC-07 Git Checkpoint, Diff, and Undo Workflow: valuable reliability feature, but must be implemented carefully.
- BC-08 Lint/Test Repair Feedback Loop: high value for an independently testable coding loop.
- BC-09 Planner/Editor Sequential Handoff: useful after basic edit/verify loop is stable.
- BC-10 Context-Selection Mode: useful for first-turn file selection.

### `claude-code-main`

- BC-01 Schema-first tool contract with capability metadata: useful for richer planning and permission behavior.
- BC-02 Layered tool execution boundary: useful for clear validation, policy, approval, and side-effect sequencing.
- BC-04 Read-before-edit and stale-write protection: one of the highest-value edit safety ideas.
- BC-05 Structured file discovery tools: practical retrieval without heavy RAG.
- BC-06 Explicit `@file` and line-range attachment flow: precise user-directed context.
- BC-07 Append-only transcript with parent chains: strong audit and recovery model.
- BC-08 Durable history separated from model-context projection: useful for longer coding sessions.
- BC-11 Lightweight task and todo tools: useful if model-visible task discipline is needed.
- BC-12 Read-only plan mode as a permission mode: good planning boundary.
- BC-13 Verification as a specialized read-only subagent: good later role pattern.
- BC-15 Query-phase tracing and profiling checkpoints: useful for loop debugging.

### `mini-swe-agent-main`

- BC-01 Minimal Agent / Model / Environment Boundary: useful as a simplicity target.
- BC-02 Linear Command-Observation Coding Loop: useful for inspectable whole-run flow.
- BC-04 Format Error as Model-Visible Recovery Message: compact recovery pattern for malformed model actions.
- BC-06 Trajectory-First Whole-Run Evidence: useful complement to tracing.
- BC-07 Prompt-Encoded Coding Workflow and Observation Clipping: practical ergonomics.
- BC-10 Deterministic Model Adapters for Loop Tests: immediately useful for test coverage.
- BC-11 Cost, Step, and Wall-Time Limits in the Loop: useful bounded autonomy metadata.

### `openaiagent`

- BC-02 Runner Facade and Executor Split: useful when the loop grows but public API should stay stable.
- BC-03 Explicit `NextStep` Loop Outcomes: strong control-flow model for final, rerun, approval, and handoff.
- BC-04 Response Processing Before Side Effects: useful for safer tool execution.
- BC-05 Function Tool Contract With Schema, Origin, Error, and Timeout Metadata: useful selective tool-contract upgrade.
- BC-06 Human Approval as First-Class Interruption State: valuable if approval resume expands.
- BC-07 Run Context Wrapper as Non-Model Runtime State: low-risk cleanup candidate.
- BC-09 Agent-as-Tool for Local Subtasks: best future starting point for subagents.
- BC-10 Minimal Session Protocol With SQLite Backend: useful durable session option.
- BC-11 Typed Tracing Spans and Processor Boundary: useful evidence taxonomy.
- BC-12 Guardrail Behavior Model for Tool Safety: useful model-visible guardrail rejection path.
- BC-14 Shell and Apply-Patch Tool Surface: useful structured output and approval ideas.

### `OpenHands-main`

- BC-01 Long-Running Coding Task Startup State Machine: useful for readiness and restart diagnostics.
- BC-02 Workspace Preflight Pipeline: useful once setup scripts are explicitly approved.
- BC-04 Action / Observation Event Protocol: useful normalized evidence contract.
- BC-05 Event Store and Webhook-Style Persistence Boundary: useful later, but storage abstraction should stay small.
- BC-06 Planning Agent as File-Backed Plan Mode: practical plan/build split.
- BC-07 Skills / Microagents as Lightweight Repo Knowledge: useful as simpler alternative to semantic RAG.
- BC-10 Secrets and LLM Profile Boundary: useful at config boundaries only.
- BC-11 Tool Result Shape for Shell, Edit, Search, and Task Tracking: useful for repair loops and UI-ready evidence.

## 4. Multi-Project Comparison For Overlapping Capabilities

### Edit Safety And Repair

Best candidates:

- `claude-code-main` BC-04 for read-before-edit and stale-write protection.
- `aider-main` BC-05 for edit failure feedback.
- `aider-main` BC-08 for lint/test repair reflection.
- `openaiagent` BC-14 for structured shell/apply-patch output.
- `OpenHands-main` BC-11 for richer action/observation result shape.

Selection: start here. It forms a small usable feature loop: read files, apply patch safely, detect stale writes or patch failures, run verification, feed concise repair observations back to the model, and record evidence.

### Repo Context And Retrieval

Best candidates:

- `aider-main` BC-03 for repo map and symbol graph context.
- `aider-main` BC-02 for chunked prompt context.
- `claude-code-main` BC-05 and BC-06 for file discovery and explicit line mentions.
- `OpenHands-main` BC-07 for skills/microagents as lightweight repo knowledge.

Selection: implement explicit `@file[:line-range]` and structured file discovery before a full repo map. Then add a Python-focused repo map or CodeGraph-backed renderer as a separate option.

### Planning And Task Discipline

Best candidates:

- `OpenHands-main` BC-06 for file-backed plan mode.
- `aider-main` BC-09 for planner/editor sequential handoff.
- `claude-code-main` BC-11 and BC-12 for todo tools and read-only plan mode.
- `mini-swe-agent-main` BC-07 for prompt workflow and bounded observations.

Selection: make planning a permissioned, file-backed mode that can be tested independently. Do not add a large planner/executor framework yet.

### Runtime Control Flow And Guardrails

Best candidates:

- `openaiagent` BC-03 for explicit next-step outcomes.
- `openaiagent` BC-04 for response processing before side effects.
- `openaiagent` BC-05 for tool metadata and error/timeout behavior.
- `openaiagent` BC-12 for guardrail result behavior.
- `claude-code-main` BC-02 for layered side-effect gates.

Selection: borrow incrementally while preserving existing `RunState`, `ToolRegistry`, and approval contracts.

### Evidence, Session, And Transcript

Best candidates:

- `claude-code-main` BC-07 and BC-08 for append-only transcript and projected context separation.
- `mini-swe-agent-main` BC-06 for trajectory-first whole-run evidence.
- `openaiagent` BC-10 and BC-11 for session protocol and typed spans.
- `OpenHands-main` BC-04 for action/observation event correlation.

Selection: normalize evidence records first. Avoid event-store abstractions until there is more than one storage consumer.

### Subagents

Best candidates:

- `openaiagent` BC-09 for agent-as-tool.
- `claude-code-main` BC-14 for minimal delegation contract.
- `claude-code-main` BC-13 for verifier role discipline.
- `OpenHands-main` sub-conversation concepts for future UI/API state.

Selection: not the immediate best next stage. Subagents require stronger transcript, approval, and evidence boundaries first.

## 5. Best Reference Source For Each Capability Area

| Capability area | Best reference source | Secondary source | Why |
| --- | --- | --- | --- |
| Edit safety | `claude-code-main` BC-04 | `aider-main` BC-05 | Read-state and stale-write protection are concrete and testable; Aider adds model repair feedback |
| Patch failure repair | `aider-main` BC-05 | `OpenHands-main` BC-11 | Aider's failure feedback maps directly to model-visible recovery |
| Lint/test repair loop | `aider-main` BC-08 | `claude-code-main` BC-13 | Aider is closer to current no-subagent architecture |
| Tool metadata and side-effect gate | `openaiagent` BC-05 | `claude-code-main` BC-01/BC-02 | `openaiagent` is Python and closer to current runtime |
| Repo map | `aider-main` BC-03 | CodeGraph-backed local design | Aider is the strongest local symbol graph reference |
| Explicit mentions | `claude-code-main` BC-06 | `aider-main` BC-10 | Line-range attachments are precise and bounded |
| File discovery | `claude-code-main` BC-05 | `aider-main` file add/drop behavior | Practical retrieval is lower risk than vector RAG |
| Context chunking | `aider-main` BC-02 | `claude-code-main` prompt sections | Chunking maps to existing `context_chunks.py` and `repo_context.py` |
| Planning mode | `OpenHands-main` BC-06 | `aider-main` BC-09, `claude-code-main` BC-12 | File-backed plan mode is simple and testable |
| Task/todo state | `claude-code-main` BC-11 | `OpenHands-main` startup state | Model-visible task state can be kept small |
| Transcript/evidence | `claude-code-main` BC-07/BC-08 | `mini-swe-agent-main` BC-06, `OpenHands-main` BC-04 | Durable history and projected context separation reduce long-run confusion |
| Guardrails | `openaiagent` BC-12 | `claude-code-main` permission gate | Python guardrail behavior is closest to current code |
| Session persistence | `openaiagent` BC-10 | `claude-code-main` durable transcript | SQLite session is useful only if separated from approval resume state |
| Sandbox boundary | `mini-swe-agent-main` BC-05 | `OpenHands-main` BC-03, `openaiagent` BC-13 | Use environment boundary concepts first; defer Docker/remote backends |
| Subagents | `openaiagent` BC-09 | `claude-code-main` BC-14 | Agent-as-tool is the safest first form |

## 6. Designs Not Recommended Now

| Design | Source | Why not now | Future condition |
| --- | --- | --- | --- |
| Full Aider monolithic `Coder` | `aider-main` | Would replace existing runtime boundaries and combine too many responsibilities | Only use as lifecycle reference |
| Full Aider multi-provider LiteLLM layer | `aider-main` | Current model adapter is focused on OpenAI; broad provider compatibility is maintenance-heavy | Add only if concrete provider requirements appear |
| Full Aider Streamlit/watch/IDE workflows | `aider-main` | UI/watch workflows do not solve the current core coding loop | Revisit after CLI loop is reliable |
| Full Claude Code async streaming loop | `claude-code-main` | Coupled to product UI, streaming, compaction, telemetry, and feature gates | Revisit after an async runtime is explicitly required |
| Full plugin/MCP/LSP ecosystem | `claude-code-main`, `openaiagent`, `OpenHands-main` | Too broad; would add service and lifecycle layers before internal tools are mature | Revisit after tool contract and permissions stabilize |
| Concurrency-safe tool batching | `claude-code-main` | Conflicts with synchronous runtime and approval semantics | Revisit after tool metadata and async execution exist |
| Background/team/remote subagents | `claude-code-main`, `OpenHands-main` | Requires transcript isolation, approvals, worktree/Git handling, and long-running task state | Revisit after synchronous agent-as-tool works |
| Full Docker or remote sandbox system | `OpenHands-main`, `openaiagent` | Heavy lifecycle, security, networking, and persistence cost | Revisit if isolated execution becomes a product requirement |
| Hosted file search as repo intelligence | `openaiagent` | Hosted vector search is not a local code repo map or symbol index | Use for documentation RAG only if needed |
| Product analytics/exporter stack | `aider-main`, `claude-code-main`, `openaiagent` | Current need is local evidence, not product telemetry | Revisit only for production telemetry |
| Broad YAML/dynamic factory plugin config | `mini-swe-agent-main` | Project rules discourage speculative factories and registries | Add narrow config only for real CLI call sites |
| OpenHands app server/frontend/WebSocket architecture | `OpenHands-main` | `my_agent` is a Python package/CLI, not a web product | Revisit if API/UI becomes a goal |

## 7. Selection Rationale

The next-stage upgrades should be selected by independently testable feature loops, not by framework size. The best candidates are the ones that make `my_agent` more useful as a coding agent while preserving its small Python architecture.

The highest-value immediate loop is edit safety plus verification repair. It builds on existing patch, shell, verification, trajectory, and tool-observation modules; it does not require new servers, async execution, subagents, or a full symbol index. It can be tested with deterministic fake models and temporary workspaces.

The second-best loop is repo context and explicit mentions. `my_agent` already has workspace inventory and mention resolution, so line-range mentions, structured file discovery, context chunking, and a first repo-map renderer would improve first-turn accuracy without changing the core runner.

The third-best loop is file-backed planning/task state. It makes complex tasks more reliable, but it should come after the edit/verify loop has stronger evidence, because a planner without reliable execution feedback can produce polished but weak plans.

Evidence normalization is valuable across all options. It should be kept close to `trajectory.py` and `run_recording.py` at first, not turned into a broad event-store framework.

Full subagents, full sandboxing, async tool batching, MCP/plugin ecosystems, UI/server layers, and broad provider abstractions are not good current-stage choices. They become more reasonable after the smaller loops prove stable.
