# Next Upgrade Options

## Option A: Edit-Safe Repair Loop

1. Feature theme

Add a reliable coding loop around safe file editing, patch failure feedback, verification execution, and model-visible repair observations.

2. Recommendation level

strongly_recommended

3. Why it fits the current stage

This option upgrades the most important missing usable-coding-agent capability without redesigning the runtime. `my_agent` already has workspace path validation, patch parsing, dry-run/apply, shell/test tools, verification observations, approvals, trajectory records, and CLI state. The missing piece is a cohesive loop that prevents stale writes, explains edit failures to the model, runs narrow verification, and records evidence cleanly enough for repair.

Estimated total production LOC: 2,700 to 3,700.

4. Module breakdown

| Module | Function | Estimated LOC | Related `my_agent` Files | Reference Basis |
| --- | --- | ---:| --- | --- |
| Edit read-state tracker | Track file content digests after model/tool reads and reject writes against stale content | 450 | `src/agents/workspace.py`, `src/agents/edit_tools.py`, possible `src/agents/edit_state.py` | `claude-code-main` BC-04 |
| Patch failure observation formatter | Convert parse/dry-run/apply failures into concise model-visible repair observations | 450 | `src/agents/patches.py`, `src/agents/edit_tools.py`, `src/agents/tool_observations.py` | `aider-main` BC-05, `OpenHands-main` BC-11 |
| Verification repair loop | Run configured verification after edits and feed bounded failures back into the next turn | 650 | `src/agents/verification.py`, `src/agents/run_loop.py`, `src/agents/tool_execution.py` | `aider-main` BC-08, `claude-code-main` BC-13 |
| Coding loop policy glue | Keep generic runner intact while adding coding-specific repair continuation rules | 500 | `src/agents/coding_agent.py`, possible `src/agents/coding_loop.py`, `src/agents/run_steps.py` | `aider-main` BC-01, `mini-swe-agent-main` BC-02 |
| Evidence records | Record edit attempts, stale-write blocks, verification commands, and repair cycles | 400 | `src/agents/trajectory.py`, `src/agents/run_recording.py` | `mini-swe-agent-main` BC-06, `OpenHands-main` BC-04 |
| Deterministic tests support | Add fake model/tool fixtures for edit-verify loops | 250 | `tests/`, `src/agents/models.py` test doubles only | `mini-swe-agent-main` BC-10 |

5. Expected new / modified files

| File | New / Modified | Purpose | Related Module |
| --- | --- | --- | --- |
| `src/agents/edit_state.py` | New | Store read digests and stale-write checks for coding runs | Edit read-state tracker |
| `src/agents/edit_tools.py` | Modified | Require read-state checks before mutating workspace files | Edit read-state tracker |
| `src/agents/patches.py` | Modified | Return structured parse/dry-run/apply failure data | Patch failure observation formatter |
| `src/agents/tool_observations.py` | Modified | Render bounded edit and verification failure observations | Patch failure observation formatter |
| `src/agents/verification.py` | Modified | Normalize verification command evidence and repair feedback | Verification repair loop |
| `src/agents/coding_agent.py` | Modified | Add coding profile switches for edit repair and verification repair | Coding loop policy glue |
| `src/agents/run_loop.py` | Modified | Continue model loop after repairable edit/verification failures when policy allows | Verification repair loop |
| `src/agents/trajectory.py` | Modified | Add coding repair cycle records without changing source code behavior | Evidence records |
| `tests/test_edit_state.py` | New | Unit tests for read-state and stale-write checks | Edit read-state tracker |
| `tests/test_patch_repair_observations.py` | New | Unit tests for patch failure feedback | Patch failure observation formatter |
| `tests/test_coding_repair_loop.py` | New | End-to-end deterministic loop tests | Verification repair loop |

6. Reference basis

| Reference Project | BC | What to Borrow | Why This Source |
| --- | --- | --- | --- |
| `claude-code-main` | BC-04 | Read-before-edit and stale-write rejection | Most concrete edit-safety design |
| `aider-main` | BC-05 | Model-visible edit failure feedback | Directly improves patch self-repair |
| `aider-main` | BC-08 | Lint/test repair feedback loop | Closest to current synchronous loop |
| `mini-swe-agent-main` | BC-10 | Deterministic loop tests | Keeps the feature testable without API calls |
| `OpenHands-main` | BC-11 | Tool result shape for edit/search/task tracking | Useful for structured observations |
| `openaiagent` | BC-14 | Structured shell/apply-patch output and approval concepts | Python runtime reference |

7. Recommended implementation order

1. Add read-state tracking for files read through workspace/file tools.
2. Gate patch/apply operations on tracked read-state and clear failure messages.
3. Normalize patch parse/dry-run/apply failures into model-visible observations.
4. Normalize verification command output into bounded repair observations.
5. Add coding-profile policy for one or more repair turns after edit or verification failure.
6. Record repair cycles in trajectory JSONL.
7. Add deterministic end-to-end tests with fake model responses.

8. Risks

- Stale-write checks can block legitimate edits if repo-context-only file visibility is treated as a real read.
- Verification output can be too large or too noisy unless clipped and labeled.
- Repair continuation can loop too long unless bounded by existing step and turn limits.
- Edit safety must not duplicate workspace boundary validation in every internal helper.

9. Acceptance criteria

- A model cannot modify a file through edit tools unless the file was read or explicitly selected under the policy.
- If a file changes after it was read, an edit attempt is rejected with a clear stale-write observation.
- Patch parse and apply failures are returned as concise model-visible feedback.
- A verification failure after an edit can trigger a bounded repair turn.
- Trajectory records show read, edit attempt, verification command, verification result, and repair turn.
- Existing approval pause/resume behavior still works.
- Targeted tests cover stale-write rejection, patch failure feedback, verification repair, and no-source-edit documentation paths.

10. Out of scope

- New edit protocol abstraction with multiple edit formats.
- Git checkpointing and undo.
- Async tool execution.
- Subagents.
- Docker or remote sandbox execution.
- UI, server, MCP, or plugin infrastructure.

## Option B: Repo Context And Explicit Mentions

1. Feature theme

Improve first-turn and mid-run code understanding through precise file mentions, line ranges, structured file discovery, token-aware context chunks, and a lightweight repo map.

2. Recommendation level

strongly_recommended

3. Why it fits the current stage

This option builds on existing workspace inventory, selected files, mention resolution, literal search, AST outlines, related-file heuristics, and repo-context rendering. It improves the agent before edits happen: better context reduces wrong-file edits and weak plans. The full repo map can be staged after explicit mentions and discovery are stable.

Estimated total production LOC: 3,200 to 4,800.

4. Module breakdown

| Module | Function | Estimated LOC | Related `my_agent` Files | Reference Basis |
| --- | --- | ---:| --- | --- |
| Explicit mention parser | Support `@path`, `@path:10`, `@path:10-30`, and quoted file mentions | 500 | `src/agents/context_mentions.py`, `src/agents/selected_files.py` | `claude-code-main` BC-06 |
| Attachment/context projection | Convert explicit mentions into bounded file snippets and selected context chunks | 550 | `src/agents/repo_context.py`, `src/agents/context_chunks.py` | `claude-code-main` BC-06, BC-08 |
| Structured file discovery | Add bounded glob, filename fuzzy match, and content search result formatting | 650 | `src/agents/workspace_code.py`, `src/agents/workspace_code_tools.py` | `claude-code-main` BC-05 |
| Token-aware context chunking | Separate instructions, selected files, repo map, search results, memory, and reminders | 550 | `src/agents/context_chunks.py`, `src/agents/model_turn.py` | `aider-main` BC-02 |
| Lightweight repo map | Build a Python-focused symbol summary with ranking by mentions, imports, and selected files | 900 | new `src/agents/repo_map.py`, `src/agents/workspace_code.py` | `aider-main` BC-03 |
| Context tests and fixtures | Validate mention parsing, path ambiguity, snippet clipping, and repo-map ranking | 400 | `tests/` | `mini-swe-agent-main` BC-10 |

5. Expected new / modified files

| File | New / Modified | Purpose | Related Module |
| --- | --- | --- | --- |
| `src/agents/repo_map.py` | New | Build lightweight symbol summaries and ranked repo-map snippets | Lightweight repo map |
| `src/agents/context_mentions.py` | Modified | Parse explicit mentions and line ranges | Explicit mention parser |
| `src/agents/repo_context.py` | Modified | Include explicit attachments and repo-map chunks | Attachment/context projection |
| `src/agents/context_chunks.py` | Modified | Make context sections token-aware and inspectable | Token-aware context chunking |
| `src/agents/workspace_code.py` | Modified | Add bounded discovery helpers | Structured file discovery |
| `src/agents/workspace_code_tools.py` | Modified | Expose discovery tools with stable output schemas | Structured file discovery |
| `src/agents/model_turn.py` | Modified | Render chunked context in a stable order | Token-aware context chunking |
| `tests/test_context_mentions.py` | Modified | Cover explicit mention syntax | Explicit mention parser |
| `tests/test_repo_map.py` | New | Cover symbol summary and ranking behavior | Lightweight repo map |
| `tests/test_repo_context_projection.py` | New | Cover context chunk ordering and clipping | Attachment/context projection |

6. Reference basis

| Reference Project | BC | What to Borrow | Why This Source |
| --- | --- | --- | --- |
| `claude-code-main` | BC-06 | Explicit line-range attachment flow | Most precise user-directed context model |
| `claude-code-main` | BC-05 | Glob/grep/fuzzy discovery result shapes | Practical retrieval without vector RAG |
| `aider-main` | BC-02 | Prompt context chunk layering | Clean model for explicit prompt sections |
| `aider-main` | BC-03 | RepoMap symbol graph context | Strongest repo-map reference |
| `aider-main` | BC-10 | Context-selection mode | Useful for first-turn file selection |
| `OpenHands-main` | BC-07 | Skills/microagents as lightweight repo knowledge | Future extension, not first implementation |

7. Recommended implementation order

1. Extend mention parsing and path resolution for explicit line ranges.
2. Add bounded file snippet rendering for explicit mentions.
3. Add structured file discovery outputs with strict result limits.
4. Refactor repo context into explicit chunk sections without broad runtime changes.
5. Add a lightweight Python repo-map renderer using existing AST outlines first.
6. Add mention-personalized repo-map ranking.
7. Add tests for ambiguous mentions, missing files, large files, and ranking.

8. Risks

- Mention parsing can produce false positives if it treats ordinary prose as file requests.
- Repo-map ranking can become stale if it is cached too aggressively.
- Token-aware chunking can duplicate memory or selected-file context if boundaries are unclear.
- A full tree-sitter dependency would increase maintenance; start with existing Python AST capability.

9. Acceptance criteria

- `@file.py:10-30` resolves to the intended file and bounded snippet.
- Ambiguous mentions produce a clear model-visible clarification or ranked candidates.
- File discovery tools return bounded, stable, testable outputs.
- Repo context clearly separates selected files, explicit mentions, search results, repo map, and memory.
- Repo map improves symbol-level context for Python projects without requiring vector search.
- Existing selected-file and related-file behavior remains compatible.

10. Out of scope

- Semantic vector RAG.
- Full LSP lifecycle.
- Multi-language tree-sitter integration.
- MCP resources.
- External documentation retrieval.
- Automatic hidden edits based on repo-map-only context.

## Option C: File-Backed Planning And Task State

1. Feature theme

Add a read-only planning mode, file-backed plan artifact, lightweight task/todo state, and a controlled transition into execution.

2. Recommendation level

recommended

3. Why it fits the current stage

`my_agent` has a functional single-loop coding CLI but no planner/executor split. A small planning mode can make larger coding tasks more reliable without adding subagents. The plan artifact should be explicit, inspectable, and optional. This option is best after or alongside stronger edit/verification evidence.

Estimated total production LOC: 2,400 to 3,600.

4. Module breakdown

| Module | Function | Estimated LOC | Related `my_agent` Files | Reference Basis |
| --- | --- | ---:| --- | --- |
| Planning profile | Build a read-only planning agent profile with no edit/shell mutation tools | 450 | `src/agents/coding_agent.py`, `src/agents/coding_policies.py` | `claude-code-main` BC-12 |
| Plan artifact manager | Read/write `PLAN.md` or configured plan file through approved boundary | 450 | new `src/agents/coding_plan.py`, `src/agents/workspace.py` | `OpenHands-main` BC-06 |
| Task/todo state | Represent small model-visible task list with status transitions | 500 | `src/agents/run_state.py`, `src/agents/coding_state.py` | `claude-code-main` BC-11 |
| Planner-to-executor handoff | Convert approved plan into execution instructions and selected context | 550 | `src/agents/coding_cli.py`, `src/agents/repo_context.py` | `aider-main` BC-09 |
| CLI commands/flags | Add plan-only and execute-plan entry points without a TUI | 350 | `src/agents/coding_cli.py` | `aider-main` BC-11 |
| Tests | Deterministic planner and handoff tests | 250 | `tests/` | `mini-swe-agent-main` BC-10 |

5. Expected new / modified files

| File | New / Modified | Purpose | Related Module |
| --- | --- | --- | --- |
| `src/agents/coding_plan.py` | New | Plan artifact and task state helpers | Plan artifact manager |
| `src/agents/coding_agent.py` | Modified | Build plan-only profile and execution profile | Planning profile |
| `src/agents/coding_policies.py` | Modified | Enforce read-only planning mode | Planning profile |
| `src/agents/coding_state.py` | Modified | Persist plan/task metadata in CLI state | Task/todo state |
| `src/agents/coding_cli.py` | Modified | Add plan and execute-plan flows | CLI commands/flags |
| `src/agents/repo_context.py` | Modified | Include plan artifact during execution | Planner-to-executor handoff |
| `tests/test_coding_plan.py` | New | Unit tests for plan artifact and task state | Plan artifact manager |
| `tests/test_plan_mode_cli.py` | New | CLI flow tests for plan-only and execute-plan | CLI commands/flags |

6. Reference basis

| Reference Project | BC | What to Borrow | Why This Source |
| --- | --- | --- | --- |
| `OpenHands-main` | BC-06 | File-backed plan mode and build handoff | Simple plan/build split |
| `aider-main` | BC-09 | Planner/editor sequential handoff | Coding-agent-specific flow |
| `claude-code-main` | BC-12 | Read-only plan mode as permission mode | Clean safety boundary |
| `claude-code-main` | BC-11 | Lightweight task/todo tools | Useful model-visible progress state |
| `mini-swe-agent-main` | BC-07 | Prompt-encoded workflow | Keeps plan mode simple |

7. Recommended implementation order

1. Add a read-only planning profile that cannot call edit or shell mutation tools.
2. Add plan artifact read/write through a narrow workspace boundary.
3. Add task/todo state as a small data model, not a broad project manager.
4. Add CLI plan-only flow.
5. Add execute-plan flow that injects the plan into repo context.
6. Add deterministic tests for plan generation, read-only enforcement, and execution handoff.

8. Risks

- Plan mode can become ceremonial if execution feedback is weak.
- Task state can grow into a broad manager abstraction if not kept small.
- Writing `PLAN.md` is a file modification and must be clearly user-approved or policy-governed.
- Planner and executor prompts can diverge if plan format is too vague.

9. Acceptance criteria

- Plan mode cannot mutate source files or run unapproved mutation commands.
- A plan artifact is generated in a predictable path with clear sections.
- Execution mode can load the plan and use it as context.
- Task state supports only necessary statuses and is covered by tests.
- Existing normal coding runs still work without planning mode.

10. Out of scope

- Parallel subagents.
- Background workers.
- Web UI plan review.
- Large project-management schema.
- Automatic implementation of unapproved plans.

## Option D: Durable Transcript And Action/Observation Events

1. Feature theme

Normalize run evidence into an append-only transcript and action/observation event stream while keeping model-context projection separate.

2. Recommendation level

recommended

3. Why it fits the current stage

`my_agent` already has `RunItem`, tracing, trajectory JSONL, memory sessions, and CLI pending state. The risk is that these artifacts diverge as the coding loop grows. A small durable transcript/event layer improves debugging, resume, future UI, and future subagents without adding a server.

Estimated total production LOC: 2,600 to 4,000.

4. Module breakdown

| Module | Function | Estimated LOC | Related `my_agent` Files | Reference Basis |
| --- | --- | ---:| --- | --- |
| Transcript record model | Append-only model/user/tool/verification records with parent/run IDs | 600 | new `src/agents/transcript.py`, `src/agents/trajectory.py` | `claude-code-main` BC-07 |
| Context projection | Build model context from transcript plus memory without rewriting history | 500 | `src/agents/memory.py`, `src/agents/context_chunks.py`, `src/agents/model_turn.py` | `claude-code-main` BC-08 |
| Action/observation events | Normalize tool calls, shell commands, patch attempts, and verification results | 650 | `src/agents/run_recording.py`, `src/agents/tool_execution.py` | `OpenHands-main` BC-04 |
| Session persistence adapter | Optional JSON or SQLite transcript persistence behind current session boundary | 450 | `src/agents/memory.py`, possible `src/agents/session_store.py` | `openaiagent` BC-10 |
| Trace correlation | Link transcript events to trace spans without replacing tracing | 350 | `src/agents/tracing.py`, `src/agents/trajectory.py` | `openaiagent` BC-11 |
| Tests | Persistence, projection, and correlation tests | 300 | `tests/` | `mini-swe-agent-main` BC-06 |

5. Expected new / modified files

| File | New / Modified | Purpose | Related Module |
| --- | --- | --- | --- |
| `src/agents/transcript.py` | New | Durable transcript records and append API | Transcript record model |
| `src/agents/session_store.py` | New | Optional persistence adapter if current `memory.py` should stay narrow | Session persistence adapter |
| `src/agents/trajectory.py` | Modified | Emit or mirror normalized transcript/event records | Transcript record model |
| `src/agents/run_recording.py` | Modified | Record action/observation events | Action/observation events |
| `src/agents/tool_execution.py` | Modified | Attach event IDs and tool result metadata | Action/observation events |
| `src/agents/model_turn.py` | Modified | Use context projection instead of ad hoc history selection where practical | Context projection |
| `src/agents/tracing.py` | Modified | Add event/span correlation metadata | Trace correlation |
| `tests/test_transcript.py` | New | Transcript append and projection tests | Transcript record model |
| `tests/test_action_observation_events.py` | New | Event shape tests | Action/observation events |

6. Reference basis

| Reference Project | BC | What to Borrow | Why This Source |
| --- | --- | --- | --- |
| `claude-code-main` | BC-07 | Append-only transcript with parent chains | Strong audit and recovery model |
| `claude-code-main` | BC-08 | Durable history separated from model-context projection | Solves long-session context drift |
| `OpenHands-main` | BC-04 | Action/observation event protocol | Good normalized event shape |
| `mini-swe-agent-main` | BC-06 | Whole-run trajectory evidence | Keep evidence human-readable |
| `openaiagent` | BC-10 | Minimal session protocol and SQLite idea | Useful optional persistence |
| `openaiagent` | BC-11 | Typed trace spans and processors | Useful correlation taxonomy |

7. Recommended implementation order

1. Define transcript and action/observation record types.
2. Mirror existing trajectory records into the new model without changing runtime behavior.
3. Add context projection from transcript records.
4. Add optional persistence adapter only if current session store cannot represent transcript records cleanly.
5. Link events to trace span IDs.
6. Add tests for append-only behavior and model-context projection.

8. Risks

- Evidence formats can duplicate each other if ownership is unclear.
- Persistence can become a premature storage abstraction.
- Context projection can change model behavior if introduced too broadly at once.
- Transcript records must avoid storing unnecessary sensitive data.

9. Acceptance criteria

- Every model turn, tool call, patch attempt, shell command, and verification result can be represented as an append-only record.
- Model context can be projected from transcript records without mutating history.
- Existing trajectory output remains available or has a documented compatibility path.
- Trace spans can be correlated to transcript/event IDs.
- Tests cover transcript persistence, projection, and event ordering.

10. Out of scope

- WebSocket event server.
- Web UI or TUI.
- Remote event store.
- Product analytics.
- Subagent sidechain transcript execution.

## Option E: Minimal Agent-As-Tool Subtasks

1. Feature theme

Add a small synchronous subtask delegation mechanism where one agent can call another local agent profile as a tool for bounded read-only exploration or verification.

2. Recommendation level

optional

3. Why it fits the current stage

Subagents are useful, but `my_agent` should not jump directly to background teams, worktrees, or remote workers. A synchronous agent-as-tool mechanism is the smallest viable step. It should be implemented only after edit safety and transcript evidence are stronger, because nested runs complicate approvals, tracing, and context.

Estimated total production LOC: 2,800 to 4,200.

4. Module breakdown

| Module | Function | Estimated LOC | Related `my_agent` Files | Reference Basis |
| --- | --- | ---:| --- | --- |
| Subagent profile registry | Define explicit local profiles for explorer/verifier roles | 450 | new `src/agents/subagents.py`, `src/agents/coding_agent.py` | `openaiagent` BC-09 |
| Agent-as-tool wrapper | Expose a bounded subagent call as a normal `FunctionTool` | 600 | `src/agents/tools.py`, `src/agents/tool_execution.py` | `openaiagent` BC-09, `claude-code-main` BC-14 |
| Transcript isolation | Store nested run evidence separately and summarize result to parent | 650 | `src/agents/trajectory.py`, `src/agents/transcript.py` if Option D exists | `claude-code-main` BC-14 |
| Permission inheritance | Restrict subagent tools, approval behavior, and write access by profile | 600 | `src/agents/coding_policies.py`, `src/agents/tool_guardrails.py` | `claude-code-main` BC-13 |
| Verifier role | Add read-only verification subagent profile using command evidence rules | 450 | `src/agents/verification.py`, `src/agents/coding_agent.py` | `claude-code-main` BC-13 |
| Tests | Nested run, permission, and evidence tests | 350 | `tests/` | `openaiagent` tests around agent tools and guardrails |

5. Expected new / modified files

| File | New / Modified | Purpose | Related Module |
| --- | --- | --- | --- |
| `src/agents/subagents.py` | New | Explicit subagent profile and invocation helpers | Subagent profile registry |
| `src/agents/tools.py` | Modified | Allow agent-as-tool registration | Agent-as-tool wrapper |
| `src/agents/tool_execution.py` | Modified | Execute nested agent tool calls with bounded context | Agent-as-tool wrapper |
| `src/agents/coding_policies.py` | Modified | Enforce subagent read/write/tool restrictions | Permission inheritance |
| `src/agents/verification.py` | Modified | Support verifier profile output format | Verifier role |
| `src/agents/trajectory.py` | Modified | Record nested run boundaries | Transcript isolation |
| `tests/test_subagents.py` | New | Unit tests for local agent-as-tool behavior | Agent-as-tool wrapper |
| `tests/test_verifier_subagent.py` | New | Read-only verifier behavior tests | Verifier role |

6. Reference basis

| Reference Project | BC | What to Borrow | Why This Source |
| --- | --- | --- | --- |
| `openaiagent` | BC-09 | Agent-as-tool for local subtasks | Smallest subagent form in Python |
| `claude-code-main` | BC-14 | Minimal delegation contract | Good constraints around scope and summaries |
| `claude-code-main` | BC-13 | Verification as specialized read-only subagent | Best first useful role |
| `openaiagent` | BC-08 | Handoff as future transition model | Useful later, not first step |
| `OpenHands-main` | Sub-conversation model | Future UI/API state concept | Avoid for local first version |

7. Recommended implementation order

1. Define read-only explorer and verifier subagent profiles.
2. Register a synchronous agent-as-tool wrapper.
3. Enforce profile-level tool restrictions.
4. Record nested run evidence and summarize result to parent.
5. Add verifier subagent using evidence-first command rules.
6. Add tests for nested failures, approvals, and trace/trajectory separation.

8. Risks

- Nested approvals can be confusing without clear ownership.
- Subagents can hide weak context selection behind more model calls.
- Transcript and trace records can become hard to follow.
- It can become a broad delegation framework if not limited to two roles.

9. Acceptance criteria

- Parent agent can call a read-only explorer or verifier as a tool.
- Subagent cannot mutate files unless explicitly configured for a later profile.
- Nested evidence is recorded separately and summarized back to parent.
- Verification subagent reports actual command evidence, not unsupported claims.
- Existing non-subagent runs are unaffected.

10. Out of scope

- Parallel subagent execution.
- Background workers.
- Worktree isolation.
- Remote threads or servers.
- Handoff teams.
- Arbitrary user-defined agent marketplace.

## Option F: Sandbox, Server, MCP, And Product Integrations

1. Feature theme

Add isolated execution, server/API orchestration, MCP proxying, plugin loading, and product-style integrations.

2. Recommendation level

not_recommended_now

3. Why it does not fit the current stage

This option would dominate the small Python package with infrastructure before the core coding loop is mature. It is valuable later, but only after edit safety, repo context, planning, and evidence boundaries are stable.

Estimated total production LOC: 4,000 to 5,000 for a narrow first slice, but the real design would likely exceed that quickly.

4. Module breakdown

| Module | Function | Estimated LOC | Related `my_agent` Files | Reference Basis |
| --- | --- | ---:| --- | --- |
| Sandbox interface | Abstract local, Docker, or remote execution backend | 900 | `src/agents/environment.py`, possible `src/agents/sandbox.py` | `openaiagent` BC-13, `OpenHands-main` BC-03 |
| Workspace preflight | Setup scripts, hooks, dependency checks, readiness states | 800 | possible `src/agents/workspace_setup.py`, `src/agents/coding_state.py` | `OpenHands-main` BC-02 |
| MCP/proxy boundary | External tool proxy with credential policy | 900 | future modules | `OpenHands-main` BC-09, `openaiagent` MCP |
| Server/session API | Long-running conversation API and pending messages | 900 | future API layer | `OpenHands-main` BC-01, BC-08 |
| Plugin/tool loading | Dynamic external tool discovery and manifests | 800 | `src/agents/tools.py` future extension | `claude-code-main` BC-09 |

5. Expected new / modified files

| File | New / Modified | Purpose | Related Module |
| --- | --- | --- | --- |
| `src/agents/sandbox.py` | New | Execution backend abstraction | Sandbox interface |
| `src/agents/workspace_setup.py` | New | Approved setup/preflight commands | Workspace preflight |
| `src/agents/server/` | New | API/session layer if ever needed | Server/session API |
| `src/agents/mcp_proxy.py` | New | Credentialed external tool proxy | MCP/proxy boundary |
| `src/agents/plugin_loader.py` | New | Dynamic tool/plugin loading | Plugin/tool loading |

6. Reference basis

| Reference Project | BC | What to Borrow | Why This Source |
| --- | --- | --- | --- |
| `OpenHands-main` | BC-01 | Long-running startup state | Best product orchestration reference |
| `OpenHands-main` | BC-02 | Workspace preflight | Good readiness model |
| `OpenHands-main` | BC-09 | MCP proxy boundary | Useful for credentialed integrations |
| `openaiagent` | BC-13 | Sandbox capability injection | Strong conceptual runtime boundary |
| `claude-code-main` | BC-09 | Deferred tool loading | Useful only after tool count grows |

7. Recommended implementation order

Do not implement this as the next stage. Re-evaluate after Options A, B, and either C or D have landed.

8. Risks

- Adds heavy infrastructure before core coding behavior is strong.
- Expands security and lifecycle responsibilities.
- Creates speculative plugin/service abstractions.
- Can make tests slower and less deterministic.

9. Acceptance criteria

This option should not be accepted until there is a concrete product requirement for isolation, server operation, MCP tools, or external integrations.

10. Out of scope

For the current stage, all of it is out of scope except small boundary lessons that can be used inside Options A to D.

## Best Recommended Option

The best immediate option is Option A: Edit-Safe Repair Loop.

Rationale:

- It upgrades the core "usable coding agent" behavior directly.
- It builds on existing `my_agent` modules instead of replacing them.
- It forms an independently testable feature loop.
- It reduces the highest practical risks: overwriting user edits, failing patches without recovery, and unverified changes.
- It creates evidence patterns that make later planning, repo context, and subagent work safer.

Recommended next sequence:

1. Implement Option A first.
2. Implement the explicit mention and structured discovery parts of Option B.
3. Add the lightweight repo map from Option B.
4. Add Option C planning mode if larger tasks remain hard to execute.
5. Add Option D transcript normalization before any subagent work.
6. Consider Option E only after transcript and permission boundaries are stable.
