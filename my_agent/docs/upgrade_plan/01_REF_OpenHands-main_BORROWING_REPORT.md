# OpenHands-main Borrowing Report

## 1. Metadata

| Field | Value |
| --- | --- |
| Project name | OpenHands-main |
| Project path | `C:\Users\ch\Desktop\ai agent学习\reference\OpenHands-main` |
| Subagent name | `reference_OpenHands-main_analysis` |
| Analysis scope | Only `reference/OpenHands-main/` |
| CodeGraph status | Available for the reference path; 2,059 files, 26,446 nodes, 54,626 edges, 51.11 MB database |

Key files reviewed:

- `my_agent/docs/upgrade_plan/00_MY_AGENT_BASELINE.md`
- `reference/OpenHands-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/OpenHands-main/pyproject.toml`
- `reference/OpenHands-main/openhands/app_server/app_conversation/app_conversation_service.py`
- `reference/OpenHands-main/openhands/app_server/app_conversation/app_conversation_service_base.py`
- `reference/OpenHands-main/openhands/app_server/app_conversation/live_status_app_conversation_service.py`
- `reference/OpenHands-main/openhands/app_server/app_conversation/app_conversation_models.py`
- `reference/OpenHands-main/openhands/app_server/app_conversation/skill_loader.py`
- `reference/OpenHands-main/openhands/app_server/sandbox/sandbox_models.py`
- `reference/OpenHands-main/openhands/app_server/sandbox/session_auth.py`
- `reference/OpenHands-main/openhands/app_server/sandbox/docker_sandbox_service.py`
- `reference/OpenHands-main/openhands/app_server/event/event_service.py`
- `reference/OpenHands-main/openhands/app_server/event/event_service_base.py`
- `reference/OpenHands-main/openhands/app_server/event_callback/event_callback_models.py`
- `reference/OpenHands-main/openhands/app_server/event_callback/webhook_router.py`
- `reference/OpenHands-main/openhands/app_server/pending_messages/pending_message_models.py`
- `reference/OpenHands-main/openhands/app_server/pending_messages/pending_message_router.py`
- `reference/OpenHands-main/openhands/app_server/mcp/mcp_router.py`
- `reference/OpenHands-main/openhands/app_server/settings/settings_models.py`
- `reference/OpenHands-main/openhands/app_server/settings/llm_profiles.py`
- `reference/OpenHands-main/openhands/app_server/secrets/secrets_models.py`
- `reference/OpenHands-main/openhands/app_server/constants.py`
- `reference/OpenHands-main/frontend/src/types/v1/core/base/action.ts`
- `reference/OpenHands-main/frontend/src/types/v1/core/base/observation.ts`
- `reference/OpenHands-main/frontend/src/types/v1/core/events/action-event.ts`
- `reference/OpenHands-main/frontend/src/types/v1/core/events/observation-event.ts`
- `reference/OpenHands-main/frontend/src/types/v1/core/openhands-event.ts`
- `reference/OpenHands-main/frontend/src/types/v1/type-guards.ts`
- `reference/OpenHands-main/frontend/src/stores/use-event-store.ts`
- `reference/OpenHands-main/frontend/src/contexts/conversation-websocket-context.tsx`
- `reference/OpenHands-main/frontend/src/hooks/use-handle-plan-click.ts`
- `reference/OpenHands-main/frontend/src/hooks/use-handle-build-plan-click.ts`
- `reference/OpenHands-main/skills/README.md`

## 2. One-Sentence Project Positioning

OpenHands-main is a production application shell around an external coding-agent SDK and agent server; its most useful reference value for `my_agent` is how it productizes coding-agent work through workspace setup, sandbox/session boundaries, event streams, planning mode, skills, and operational state rather than through a reusable in-repo agent loop.

## 3. Capability Matrix Related to `my_agent`

| Capability | Exists in Reference | `my_agent` Current State | Borrowing Value | Evidence Files |
| --- | --- | --- | --- | --- |
| Agent abstraction | Partly; `Agent`, `ConversationSettings`, and `StartConversationRequest` are imported from `openhands-sdk`, not implemented here. | Native `Agent`, `AgentCapabilities`, tools, handoffs, memory, and run settings exist. | Low for core abstraction; useful only for request assembly around an agent. | `pyproject.toml`; `live_status_app_conversation_service.py`; `app_conversation_service_base.py` |
| Runner / run loop | Not present in this repo; delegated to `openhands-agent-server` / `openhands-sdk`. | Synchronous `Runner.run_sync()` and `run_agent_loop()` exist. | Not a source for loop internals; borrow app-level orchestration around the loop. | `PROJECT_ARCHITECTURE_ANALYSIS.md`; `pyproject.toml` |
| Tool system | Tool execution internals are external; this repo defines tool/action/observation contracts and selects default/planning tool packs. | Native `FunctionTool`, registry, tool planning/execution, workspace, shell, patch, verification tools. | Medium; borrow action/observation event contracts and split between default tools and planning tools. | `action.ts`; `observation.ts`; `action-event.ts`; `observation-event.ts`; `live_status_app_conversation_service.py` |
| File discovery | Present as tool categories through external tools and frontend observation types: `GlobAction`, `GrepAction`, `GlobObservation`, `GrepObservation`. | Literal search, file listing, filename search, AST outline, related-file heuristics. | Medium; borrow the explicit glob/grep action result shape and truncation metadata. | `action.ts`; `observation.ts` |
| Mention resolution | Not clearly implemented in this repo; repo/skill loading is stronger than prompt mention resolution. | Regex mention detection and inventory matching exist. | Low; `my_agent` is already more direct here. | `00_MY_AGENT_BASELINE.md`; `skill_loader.py` |
| Repo context | Present through workspace preparation, selected repository metadata, skills/microagents, setup scripts, hooks, and project root resolution. | Lightweight repo context, inventory, mentions, selected files, literal symbol matches. | High; borrow staged repo preflight and repository-specific skill loading concepts. | `app_conversation_service_base.py`; `skill_loader.py`; `skills/README.md` |
| Patch / diff / edit | File editor action/observation contracts exist; executor is external. Git hooks setup exists. | Native patch parser/dry-run/apply flow; no first-class diff review. | Medium; borrow edit observation fields and pre-commit hook setup concept, not editor implementation. | `action.ts`; `observation.ts`; `app_conversation_service_base.py` |
| Run state / task state | Strong app-level start task and conversation state model; status progresses through workspace/sandbox/setup phases. | Per-run `RunState`, pending approval snapshots, CLI state store, trajectory records. | High; borrow explicit long-running task status model for coding startup and readiness. | `app_conversation_service.py`; `app_conversation_models.py`; `live_status_app_conversation_service.py` |
| Session / memory / compression | Condenser configured through external SDK; condensation events exist in frontend event union. | `AgentSession`, JSON session, rule/model summarizers, compaction policy. | Medium; borrow separation between conversation events and condenser configuration, not algorithm. | `app_conversation_service_base.py`; `openhands-event.ts` |
| Tracing | Laminar/user metadata and stats events are wired at app layer; detailed tracing is external. | Native trace/span system with sensitive-data gating and JSONL processors. | Medium; borrow metadata propagation and stats-event persistence. | `live_status_app_conversation_service.py`; `webhook_router.py`; `action-event.ts` |
| Guardrails | Security analyzer and security-risk fields exist, but core analyzer is external. Session-key sandbox guardrail is in repo. | Input/output guardrails, tool guardrails, shell/patch approval policies. | Medium; borrow boundary guardrails for sandbox session access and action risk metadata. | `session_auth.py`; `action-event.ts`; `app_conversation_service_base.py` |
| RAG / repo map / symbol index | No traditional vector RAG or in-repo symbol index found. Skills/microagents provide lightweight knowledge injection. | No semantic RAG; lightweight repo context and AST outlines. | Medium; borrow skills/microagents as a simpler near-term alternative to semantic RAG. | `skill_loader.py`; `skills/README.md`; `PROJECT_ARCHITECTURE_ANALYSIS.md` |
| Planning / coding loop | Present as separate plan agent mode, planning tools, `PLAN.md`, and UI build handoff prompt. | No explicit planner/executor split. | High; borrow a file-backed plan mode and explicit build handoff flow. | `live_status_app_conversation_service.py`; `use-handle-plan-click.ts`; `use-handle-build-plan-click.ts` |
| Testing / validation | Repo setup can install pre-commit hooks; validation/testing executor is external. | Verification command and post-tool verification observations exist. | Medium; borrow setup/pre-commit integration and status visibility, not test runner internals. | `app_conversation_service_base.py`; `00_MY_AGENT_BASELINE.md` |
| Subagent / parallel task | Supported through external registered agent definitions and planning sub-conversations; UI tracks sub-conversation IDs. | No subagents or parallel execution. | Medium; borrow conceptual sub-conversation model later, not the full external SDK design. | `app_conversation_models.py`; `live_status_app_conversation_service.py`; `use-handle-plan-click.ts` |

## BC-01: Long-Running Coding Task Startup State Machine

- Problem solved:
  - Real coding-agent startup is not a single function call. Repository preparation, sandbox readiness, setup scripts, hook installation, skill loading, and conversation creation can all take time and fail independently.
- Reference implementation:
  - Files:
    - `openhands/app_server/app_conversation/app_conversation_service.py`
    - `openhands/app_server/app_conversation/live_status_app_conversation_service.py`
    - `openhands/app_server/app_conversation/app_conversation_models.py`
  - Classes / functions:
    - `AppConversationService.start_app_conversation`
    - `LiveStatusAppConversationService.start_app_conversation`
    - `LiveStatusAppConversationService._start_app_conversation`
    - `AppConversationStartTask`
    - `AppConversationStartTaskStatus`
- Execution flow:
  - Create a start task, yield/save it, wait for sandbox readiness, prepare workspace, build request, POST to agent server, save conversation info, mark the task ready or failed.
- Value for `my_agent`:
  - Turns CLI-only startup into visible state transitions and creates a natural place for preflight checks, verification setup, future UI/API progress, and restart diagnostics.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_state.py`
  - `src/agents/coding_cli.py`
  - possible new narrow module such as `src/agents/coding_startup.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add a small coding-start task record with statuses such as `preparing_workspace`, `loading_context`, `ready`, and `error`.
  - Persist status changes in the existing CLI state/trajectory path.
  - Keep the model synchronous at first; do not introduce a server just to mirror OpenHands.
- Risks:
  - Overbuilding a service layer before `my_agent` has an API or UI.
  - Duplicating `RunState`; startup state should remain separate from in-loop state.
- Evidence files:
  - `app_conversation_service.py`
  - `live_status_app_conversation_service.py`
  - `app_conversation_models.py`

## BC-02: Workspace Preflight Pipeline

- Problem solved:
  - Coding agents need repeatable workspace preparation before the first model turn: repository clone/init, working directory resolution, optional setup scripts, and optional hooks.
- Reference implementation:
  - Files:
    - `openhands/app_server/app_conversation/app_conversation_service_base.py`
  - Classes / functions:
    - `get_project_dir`
    - `AppConversationServiceBase.run_setup_scripts`
    - `AppConversationServiceBase.clone_or_init_git_repo`
    - `AppConversationServiceBase.maybe_run_setup_script`
    - `AppConversationServiceBase.maybe_setup_git_hooks`
- Execution flow:
  - Resolve project root, create or clone repository, configure Git identity, run `.openhands/setup.sh`, install `.openhands/pre-commit.sh`, then load skills.
- Value for `my_agent`:
  - Adds a disciplined pre-model phase for repo readiness and makes future Git/test behavior more deterministic.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/workspace.py`
  - `src/agents/environment.py`
  - `src/agents/coding_cli.py`
  - possible new `src/agents/workspace_setup.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add an optional local preflight step for a configured workspace root.
  - Detect and run project-local setup commands only when explicitly enabled by profile or manifest.
  - Record preflight steps into trajectory events.
- Risks:
  - Running repository setup scripts is a security boundary; require explicit approval or a policy gate.
  - `my_agent` should not clone remote repositories until credentials and source validation are designed.
- Evidence files:
  - `app_conversation_service_base.py`

## BC-03: Sandbox Boundary and Session Key Model

- Problem solved:
  - Tool execution and secrets access need an explicit runtime boundary, and that boundary needs a revocable credential.
- Reference implementation:
  - Files:
    - `openhands/app_server/sandbox/sandbox_models.py`
    - `openhands/app_server/sandbox/session_auth.py`
    - `openhands/app_server/sandbox/docker_sandbox_service.py`
  - Classes / functions:
    - `SandboxStatus`
    - `ExposedUrl`
    - `SandboxInfo`
    - `validate_session_key`
    - `validate_session_key_ownership`
    - `DockerSandboxService._docker_status_to_sandbox_status`
- Execution flow:
  - Sandbox status controls whether a session API key is exposed. Requests validate the key, reject non-running sandboxes, and optionally verify ownership.
- Value for `my_agent`:
  - Gives `my_agent` a concrete model for separating local workspace execution from future isolated execution.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/environment.py`
  - `src/agents/workspace.py`
  - `src/agents/shell_tools.py`
  - possible future `src/agents/sandbox.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Define a minimal `ExecutionEnvironmentInfo` with `status`, `root`, and optional exposed service metadata.
  - Keep local execution as the only implementation initially.
  - Treat paused/error/missing environments as hard stops for shell/edit tools.
- Risks:
  - A token model is unnecessary if `my_agent` remains a local-only library.
  - Docker sandbox implementation would be a large dependency and should not be copied now.
- Evidence files:
  - `sandbox_models.py`
  - `session_auth.py`
  - `docker_sandbox_service.py`

## BC-04: Action / Observation Event Protocol

- Problem solved:
  - Agent behavior becomes much easier to debug when every model action and environment observation has a stable event contract and correlation IDs.
- Reference implementation:
  - Files:
    - `frontend/src/types/v1/core/events/action-event.ts`
    - `frontend/src/types/v1/core/events/observation-event.ts`
    - `frontend/src/types/v1/core/base/action.ts`
    - `frontend/src/types/v1/core/base/observation.ts`
    - `frontend/src/types/v1/core/openhands-event.ts`
  - Classes / functions:
    - `ActionEvent`
    - `ObservationEvent`
    - `OpenHandsEvent`
    - `Action`
    - `Observation`
- Execution flow:
  - Agent emits an `ActionEvent` with thought, action payload, tool name, tool call ID, and response group ID. Environment returns an `ObservationEvent` linked by action ID and tool call ID.
- Value for `my_agent`:
  - Upgrades trajectory JSONL from loose evidence into a durable event stream that can later power UI, replay, review, and test assertions.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/trajectory.py`
  - `src/agents/run_recording.py`
  - `src/agents/tool_execution.py`
  - `src/agents/run_steps.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add a small internal event schema for `model_action`, `tool_observation`, `message`, `state_update`, and `final`.
  - Include `tool_call_id`, `action_id`, `tool_name`, timestamps, and optional summary/security metadata.
  - Preserve existing `RunItem` behavior; events should be an evidence layer, not a replacement for run state.
- Risks:
  - Duplicating existing `RunItem` and trajectory structures.
  - Frontend-specific union types should not be copied into Python directly.
- Evidence files:
  - `action-event.ts`
  - `observation-event.ts`
  - `action.ts`
  - `observation.ts`
  - `openhands-event.ts`

## BC-05: Event Store and Webhook-Style Persistence Boundary

- Problem solved:
  - Runtime execution and product/application concerns need a clean boundary for persistence, callbacks, and later display.
- Reference implementation:
  - Files:
    - `openhands/app_server/event/event_service.py`
    - `openhands/app_server/event/event_service_base.py`
    - `openhands/app_server/event_callback/webhook_router.py`
    - `openhands/app_server/event_callback/event_callback_models.py`
  - Classes / functions:
    - `EventService`
    - `EventServiceBase.save_event`
    - `EventServiceBase.search_events`
    - `on_event`
    - `EventCallbackProcessor`
- Execution flow:
  - Agent-server posts event batches to app-server; the app-server saves events, updates conversation metadata, tracks terminal status, and dispatches callbacks in the background.
- Value for `my_agent`:
  - Offers a clean split between the core run loop and durable external observers.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/trajectory.py`
  - `src/agents/tracing.py`
  - `src/agents/lifecycle.py`
  - possible future `src/agents/events.py`
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Introduce a minimal event sink interface with JSONL/file implementation.
  - Make trajectory recording consume normalized events.
  - Leave HTTP webhook/server support out until an API layer exists.
- Risks:
  - Creating abstract storage too early.
  - Background callbacks can hide failures if not surfaced clearly.
- Evidence files:
  - `event_service.py`
  - `event_service_base.py`
  - `webhook_router.py`
  - `event_callback_models.py`

## BC-06: Planning Agent as File-Backed Plan Mode

- Problem solved:
  - Complex coding tasks benefit from a plan-only phase that cannot edit implementation code and hands off through a concrete artifact.
- Reference implementation:
  - Files:
    - `openhands/app_server/app_conversation/live_status_app_conversation_service.py`
    - `frontend/src/hooks/use-handle-plan-click.ts`
    - `frontend/src/hooks/use-handle-build-plan-click.ts`
  - Classes / functions:
    - `PLANNING_AGENT_INSTRUCTION`
    - `AgentType.PLAN`
    - `get_planning_tools`
    - `useHandlePlanClick`
    - `useHandleBuildPlanClick`
- Execution flow:
  - UI creates a plan sub-conversation, the backend builds a planning agent with planning tools and a plan path, the user clicks Build, and the code agent receives a prompt to execute `.agents_tmp/PLAN.md`.
- Value for `my_agent`:
  - Gives a simple planner/executor split without requiring autonomous subagent orchestration.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_agent.py`
  - `src/agents/coding_cli.py`
  - `src/agents/tool_planning.py`
  - possible `src/agents/planning.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add a coding-agent profile mode that can only read/search and write a plan file.
  - Add a CLI command or flag to execute the saved plan with edit/shell tools enabled.
  - Keep the plan artifact plain Markdown.
- Risks:
  - Plan mode can become ceremony for small tasks.
  - Do not let the planning profile silently gain edit or shell permissions beyond the plan file.
- Evidence files:
  - `live_status_app_conversation_service.py`
  - `use-handle-plan-click.ts`
  - `use-handle-build-plan-click.ts`

## BC-07: Skills / Microagents as Lightweight Repo Knowledge

- Problem solved:
  - A coding agent needs project- and organization-specific guidance without building semantic RAG first.
- Reference implementation:
  - Files:
    - `openhands/app_server/app_conversation/skill_loader.py`
    - `openhands/app_server/app_conversation/app_conversation_service_base.py`
    - `skills/README.md`
    - `frontend/src/api/open-hands.types.ts`
  - Classes / functions:
    - `SkillInfo`
    - `load_skills_from_agent_server`
    - `_convert_skill_info_to_skill`
    - `AppConversationServiceBase.load_and_merge_all_skills`
    - `Microagent`
- Execution flow:
  - App server sends project/org/sandbox configuration to the agent server, receives normalized skills, converts triggers, merges by name, and injects them into agent context.
- Value for `my_agent`:
  - A practical bridge between static AGENTS-style instructions and full RAG.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/context_chunks.py`
  - `src/agents/repo_context.py`
  - `src/agents/coding_agent.py`
  - possible `src/agents/skills.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Load local repository skill files from a narrow path such as `.openhands/skills/` or `.agents/skills/`.
  - Support two trigger forms: always-on repo guidance and keyword/slash-command guidance.
  - Inject selected skills into turn context after baseline repo context.
- Risks:
  - Skill loading can conflict with existing AGENTS.md behavior.
  - Trigger logic should be simple and deterministic at first.
- Evidence files:
  - `skill_loader.py`
  - `app_conversation_service_base.py`
  - `skills/README.md`
  - `open-hands.types.ts`

## BC-08: Pending Message Queue for Not-Ready Runs

- Problem solved:
  - Users may send input while the runtime or workspace is still starting; dropping the message creates confusing behavior.
- Reference implementation:
  - Files:
    - `openhands/app_server/pending_messages/pending_message_models.py`
    - `openhands/app_server/pending_messages/pending_message_router.py`
    - `openhands/app_server/pending_messages/pending_message_service.py`
    - `openhands/app_server/app_conversation/live_status_app_conversation_service.py`
  - Classes / functions:
    - `PendingMessage`
    - `PendingMessageResponse`
    - `queue_pending_message`
    - `PendingMessageService`
    - `LiveStatusAppConversationService._process_pending_messages`
- Execution flow:
  - Messages are queued against a task or conversation ID, limited to a small count, then replayed to the agent server after conversation readiness.
- Value for `my_agent`:
  - Useful if `my_agent` grows an interactive CLI, API server, or UI where startup is asynchronous.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_cli.py`
  - `src/agents/coding_state.py`
  - future API/UI layer
- Recommended borrowing method: not_recommended_now
- Implementation sketch:
  - Do not implement immediately for the synchronous CLI.
  - If an API/UI is added, model queued messages as simple records tied to a startup task.
- Risks:
  - No current async UI/API in `my_agent`, so this would add unused complexity.
- Evidence files:
  - `pending_message_models.py`
  - `pending_message_router.py`
  - `pending_message_service.py`
  - `live_status_app_conversation_service.py`

## BC-09: MCP Proxy Boundary for Privileged Integrations

- Problem solved:
  - Some tools need secrets or provider tokens that should stay in the app/server boundary rather than being exposed to the agent runtime.
- Reference implementation:
  - Files:
    - `openhands/app_server/mcp/mcp_router.py`
  - Classes / functions:
    - `FastMCP`
    - Tavily MCP proxy initialization
    - PR/MR creation MCP tools
- Execution flow:
  - The app server hosts MCP tools/proxies, keeps provider credentials server-side, and lets sandboxed agent runtimes call controlled tools.
- Value for `my_agent`:
  - Good design reference for future GitHub/search integrations without leaking credentials into shell environments.
- Possible mapping to `my_agent` files / modules:
  - future `src/agents/mcp_proxy.py`
  - future Git/provider integration modules
- Recommended borrowing method: not_recommended_now
- Implementation sketch:
  - Defer until `my_agent` exposes MCP or remote integrations.
  - First borrow only the principle: provider credentials should stay outside general shell/tool access.
- Risks:
  - Requires server runtime, auth, and MCP dependencies not currently present.
- Evidence files:
  - `mcp_router.py`

## BC-10: Secrets and LLM Profile Boundary

- Problem solved:
  - Coding agents need provider credentials and model profiles, but raw secret values should be masked, validated, and injected deliberately.
- Reference implementation:
  - Files:
    - `openhands/app_server/settings/settings_models.py`
    - `openhands/app_server/settings/llm_profiles.py`
    - `openhands/app_server/secrets/secrets_models.py`
    - `openhands/app_server/constants.py`
    - `openhands/app_server/app_conversation/live_status_app_conversation_service.py`
  - Classes / functions:
    - `Settings`
    - `LLMProfiles`
    - `Secrets`
    - `validate_secret_name`
    - `validate_secrets_dict`
    - `LiveStatusAppConversationService._configure_llm`
    - `LiveStatusAppConversationService._configure_llm_and_mcp`
- Execution flow:
  - User settings and secrets are loaded separately, validated, merged with API-provided secrets when allowed, converted into LLM/MCP config, and inserted into agent context.
- Value for `my_agent`:
  - Helps move from environment-only API keys to explicit profiles without weakening secret handling.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/models.py`
  - `src/agents/model_settings.py`
  - `src/agents/coding_agent.py`
  - possible `src/agents/secrets.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add a minimal local model profile object only if multiple profiles become necessary.
  - Validate secret names at the CLI/config boundary.
  - Keep raw secrets out of trajectory and trace data.
- Risks:
  - Too early a profile system could duplicate simple environment variable usage.
  - Secret migration/storage is product-level work, not core loop work.
- Evidence files:
  - `settings_models.py`
  - `llm_profiles.py`
  - `secrets_models.py`
  - `constants.py`
  - `live_status_app_conversation_service.py`

## BC-11: Tool Result Shape for Shell, Edit, Search, and Task Tracking

- Problem solved:
  - Tool outputs need enough structure for repair loops, UI, replay, and validation.
- Reference implementation:
  - Files:
    - `frontend/src/types/v1/core/base/action.ts`
    - `frontend/src/types/v1/core/base/observation.ts`
  - Classes / functions:
    - `ExecuteBashAction`
    - `ExecuteBashObservation`
    - `FileEditorAction`
    - `FileEditorObservation`
    - `TaskTrackerAction`
    - `TaskTrackerObservation`
    - `GlobObservation`
    - `GrepObservation`
- Execution flow:
  - Each tool class has a typed action payload and a typed observation payload that preserves command/path/result/error/truncation details.
- Value for `my_agent`:
  - Improves model-visible observations and trajectory quality, especially around edits and validation failures.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tool_observations.py`
  - `src/agents/shell_tools.py`
  - `src/agents/edit_tools.py`
  - `src/agents/workspace_code_tools.py`
  - `src/agents/verification.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Keep existing tools but normalize observations into typed dictionaries before rendering to the model and trajectory.
  - Add explicit fields for command, exit code, timeout, path, old/new content summary, grep/glob truncation, and task-list state where applicable.
- Risks:
  - Too much observation detail can increase token usage.
  - Avoid adding frontend-oriented fields that the Python runtime does not need.
- Evidence files:
  - `action.ts`
  - `observation.ts`

## BC-12: Frontend/Event Store Ideas for Future UI

- Problem solved:
  - Realtime agent output needs de-duplication, ordering, derived UI events, and separate handling for planning conversations.
- Reference implementation:
  - Files:
    - `frontend/src/stores/use-event-store.ts`
    - `frontend/src/contexts/conversation-websocket-context.tsx`
    - `frontend/src/types/v1/type-guards.ts`
  - Classes / functions:
    - `useEventStore`
    - `ConversationWebSocketProvider`
    - `isActionEvent`
    - `isObservationEvent`
    - `isPlanningFileEditorObservationEvent`
- Execution flow:
  - WebSocket events are added to a store, de-duplicated by ID, sorted by timestamp when needed, transformed into UI events, and split across main/planning streams.
- Value for `my_agent`:
  - Useful later if a local UI or TUI is built from trajectory/event records.
- Possible mapping to `my_agent` files / modules:
  - future UI layer
  - `src/agents/trajectory.py` as event source
- Recommended borrowing method: not_recommended_now
- Implementation sketch:
  - Defer UI state store work.
  - Ensure event records contain IDs and timestamps now so a future UI can consume them.
- Risks:
  - Frontend architecture is irrelevant to a Python package until a UI exists.
- Evidence files:
  - `use-event-store.ts`
  - `conversation-websocket-context.tsx`
  - `type-guards.ts`

## 5. Designs Not Suitable for Current Borrowing

| Design | Why Not Suitable Now | Future Condition | Evidence |
| --- | --- | --- | --- |
| Full OpenHands app server | `my_agent` is a Python package/CLI, not a product server; copying FastAPI/router/service structure would dominate the codebase. | Add API/UI requirements around persistent conversations. | `openhands/app_server/app.py`; `openhands/app_server/v1_router.py`; `PROJECT_ARCHITECTURE_ANALYSIS.md` |
| Core OpenHands agent loop | The loop is not implemented in this repo; it lives in external packages. | Analyze `openhands-sdk` directly as a separate reference phase. | `pyproject.toml`; `PROJECT_ARCHITECTURE_ANALYSIS.md` |
| Docker sandbox implementation | Heavy dependency, lifecycle complexity, networking, volumes, and health checks are beyond current `my_agent` scope. | Need isolated execution as a product requirement. | `docker_sandbox_service.py`; `sandbox_models.py` |
| Remote sandbox runtime | Depends on external runtime APIs and SaaS-style lifecycle management. | Need cloud-hosted execution. | `remote_sandbox_service.py`; `PROJECT_ARCHITECTURE_ANALYSIS.md` |
| Enterprise organization/billing/auth stack | Business/product layer unrelated to upgrading the coding loop. | SaaS product phase. | `enterprise/` |
| Full multi-provider Git integration | Broad GitHub/GitLab/Bitbucket/Azure DevOps support is too large for the next `my_agent` upgrade. | First implement a small native Git status/diff/checkpoint flow, then add providers. | `openhands/app_server/integrations/`; `mcp_router.py` |
| ACP external-agent support | Useful for product interoperability, not for improving `my_agent`'s own core coding behavior. | Need to host third-party CLI agents under one UI/API. | `live_status_app_conversation_service.py` |
| Pending-message server queue | Requires async conversation startup and a UI/API channel. | Add web UI or long-running server. | `pending_message_models.py`; `pending_message_router.py` |
| MCP proxy server | Requires server boundary and MCP runtime. | Add credentialed remote integrations. | `mcp_router.py` |
| Frontend WebSocket UI architecture | `my_agent` has no frontend. | Build UI/TUI around event records. | `conversation-websocket-context.tsx`; `use-event-store.ts` |

## 6. Candidate Mapping to `my_agent`

| BC | Possible `my_agent` Module / File | Integration Difficulty | Benefit | Risk |
| --- | --- | --- | --- | --- |
| BC-01 | `src/agents/coding_state.py`, `src/agents/coding_cli.py`, possible `coding_startup.py` | Medium | Clear pre-run lifecycle and restart diagnostics | State duplication |
| BC-02 | `src/agents/workspace.py`, `src/agents/environment.py`, possible `workspace_setup.py` | Medium | Reproducible workspace readiness | Unsafe setup scripts if not approved |
| BC-03 | `src/agents/environment.py`, possible `sandbox.py` | Medium | Cleaner execution boundary | Premature sandbox abstraction |
| BC-04 | `src/agents/trajectory.py`, `src/agents/run_recording.py`, `src/agents/tool_execution.py` | Medium | Better replay/debug/event UI foundation | Duplicates `RunItem` if not scoped |
| BC-05 | `src/agents/trajectory.py`, `src/agents/lifecycle.py`, possible `events.py` | Medium | Decoupled event persistence | Abstract storage too early |
| BC-06 | `src/agents/coding_agent.py`, `src/agents/coding_cli.py`, possible `planning.py` | Medium | Practical planner/executor split | Ceremony for small tasks |
| BC-07 | `src/agents/context_chunks.py`, `src/agents/repo_context.py`, possible `skills.py` | Medium | Repo knowledge without vector RAG | Conflicts with AGENTS.md precedence |
| BC-08 | `src/agents/coding_state.py`, future API/UI | High for current package | Better async UX later | Not needed for synchronous CLI |
| BC-09 | future MCP/proxy modules | High | Safer credentialed integrations | Requires server/auth/MCP |
| BC-10 | `src/agents/models.py`, `src/agents/model_settings.py`, possible `secrets.py` | Medium | Safer multi-model/profile growth | Overcomplicates env-based config |
| BC-11 | `src/agents/tool_observations.py`, `src/agents/shell_tools.py`, `src/agents/edit_tools.py` | Low to Medium | More useful observations for repair loops | Token bloat |
| BC-12 | future UI/TUI; `src/agents/trajectory.py` as source | High for current package | UI-ready event stream later | Frontend concern too early |

## 7. Questions for the Main Agent

1. Should `my_agent` stay a local package/CLI for the next upgrade phase, or should the borrowing plan assume an API/server boundary?
2. Should planning mode be a first-class profile now, or should it wait until event/state normalization is stronger?
3. Which instruction sources should win if AGENTS.md, future `.openhands/skills/`, and user task text conflict?
4. Should workspace setup scripts be supported at all, and if so, should they require explicit approval every run?
5. Is native Git checkpoint/status/diff support in scope before any sandbox or provider integration work?
6. Should trajectory JSONL evolve into a normalized event stream, or should normalized events be a separate artifact?
7. Should the first repo-knowledge upgrade be skills/microagents, a symbol index, or better use of existing inventory/AST outlines?

## 8. Summary Points for the Main Agent

1. OpenHands-main is most useful as an application orchestration reference, not as a source for core agent-loop internals.
2. The core runner, LLM adapter, and default tool executors are external dependencies, so they should not be treated as borrowable from this repo.
3. The strongest candidates are startup task state, workspace preflight, action/observation event contracts, planning mode, and skills/microagents.
4. `my_agent` already has more native core-loop/tool machinery than this reference repo exposes directly.
5. OpenHands' plan mode is a practical low-complexity model: a plan-only agent writes `PLAN.md`, then a code agent executes it.
6. Skills/microagents are a near-term alternative to semantic RAG and fit `my_agent` better than introducing vector retrieval immediately.
7. Sandbox/session-key design is valuable conceptually, but full Docker/remote sandbox borrowing is too heavy for the current baseline.
8. Event persistence should be borrowed as a normalized evidence contract before any UI, webhook, or callback infrastructure.
9. Secret and profile handling is worth borrowing only at the boundary level: validation, masking, and deliberate injection.
10. Frontend WebSocket/store patterns should be deferred, but event IDs, timestamps, and action-observation correlation should be added early if events are normalized.
