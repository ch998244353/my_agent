# Reference Borrowing Report: openaiagent

## 1. Metadata

| Field | Value |
| --- | --- |
| Project name | `reference/openaiagent` |
| Project path | `C:\Users\ch\Desktop\ai agent学习\reference\openaiagent` |
| Subagent name | `reference_openaiagent_analysis` |
| Report scope | Borrowing candidates only; no final upgrade plan |
| Codegraph status | Available for this project: 776 indexed files, 22,544 nodes, 62,340 edges |

Key files reviewed:

- `my_agent/docs/upgrade_plan/00_MY_AGENT_BASELINE.md`
- `reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/openaiagent/src/agents/agent.py`
- `reference/openaiagent/src/agents/run.py`
- `reference/openaiagent/src/agents/run_internal/run_loop.py`
- `reference/openaiagent/src/agents/run_internal/turn_resolution.py`
- `reference/openaiagent/src/agents/run_internal/run_steps.py`
- `reference/openaiagent/src/agents/run_internal/tool_execution.py`
- `reference/openaiagent/src/agents/tool.py`
- `reference/openaiagent/src/agents/function_schema.py`
- `reference/openaiagent/src/agents/run_context.py`
- `reference/openaiagent/src/agents/run_state.py`
- `reference/openaiagent/src/agents/result.py`
- `reference/openaiagent/src/agents/handoffs/__init__.py`
- `reference/openaiagent/src/agents/handoffs/history.py`
- `reference/openaiagent/src/agents/memory/session.py`
- `reference/openaiagent/src/agents/memory/sqlite_session.py`
- `reference/openaiagent/src/agents/tracing/create.py`
- `reference/openaiagent/src/agents/tracing/traces.py`
- `reference/openaiagent/src/agents/tracing/processor_interface.py`
- `reference/openaiagent/src/agents/tracing/processors.py`
- `reference/openaiagent/src/agents/guardrail.py`
- `reference/openaiagent/src/agents/tool_guardrails.py`
- `reference/openaiagent/src/agents/sandbox/sandbox_agent.py`
- `reference/openaiagent/src/agents/sandbox/runtime.py`
- `reference/openaiagent/src/agents/sandbox/manifest.py`
- `reference/openaiagent/src/agents/sandbox/capabilities/capability.py`
- `reference/openaiagent/src/agents/sandbox/capabilities/filesystem.py`
- `reference/openaiagent/src/agents/sandbox/capabilities/shell.py`
- `reference/openaiagent/src/agents/sandbox/capabilities/tools/shell_tool.py`
- `reference/openaiagent/src/agents/sandbox/capabilities/tools/apply_patch_tool.py`
- `reference/openaiagent/src/agents/extensions/experimental/codex/`

## 2. One-Sentence Project Positioning

`reference/openaiagent` is a general-purpose Python Agents SDK runtime, not a dedicated coding agent, and its most useful value for `my_agent` is the clean separation of declarative agent configuration, runner state machine, typed tool execution, handoff, tracing, guardrails, session persistence, and sandbox capability injection.

## 3. Capability Matrix Related to `my_agent`

| Capability | Exists in Reference | `my_agent` Current State | Borrowing Value | Evidence Files |
| --- | --- | --- | --- | --- |
| Agent abstraction | Yes. `AgentBase`, `Agent`, `clone()`, `as_tool()`, prompt and handoff fields. | Present as `Agent`, `AgentCapabilities`, `build_coding_agent()`. | Borrow clearer separation between public agent config, agent-as-tool, model settings, output type, hooks, and handoffs. | `src/agents/agent.py`, baseline `00_MY_AGENT_BASELINE.md` |
| Runner / run loop | Yes. `Runner` facade delegates to `AgentRunner`; loop produces final, handoff, rerun, or interruption. | Present but synchronous and more compact: `Runner.run_sync()` -> `run_agent_loop()`. | Borrow the facade vs executor split and `NextStep*` state modeling, not the full async implementation. | `src/agents/run.py`, `src/agents/run_internal/run_loop.py`, `src/agents/run_internal/run_steps.py` |
| Tool system | Yes. `FunctionTool`, `function_tool()`, hosted tools, shell, patch, tool search, tool origin, schema generation, timeout/error handling. | Present as `FunctionTool`, `ToolRegistry`, schema, planning, execution, workspace/shell/edit tools. | Borrow selected contract details: typed result, enabled checks, approval hooks, timeout/error formatter, origin metadata. | `src/agents/tool.py`, `src/agents/function_schema.py`, `src/agents/run_internal/tool_execution.py` |
| File discovery | Partial. Sandbox has filesystem capability and shell; no native repo inventory like `my_agent`. | Present through workspace inventory, file list, code search, selected files. | Borrow sandbox filesystem boundary concepts only; keep `my_agent` inventory layer. | `src/agents/sandbox/capabilities/filesystem.py`, `src/agents/sandbox/session/base_sandbox_session.py` |
| Mention resolution | Not found as a native local-code feature. | Present: mention detection and inventory resolution. | Not a borrowing source; `my_agent` is ahead here. | `src/agents/` search results; baseline `context_mentions.py` description |
| Repo context | Not found as a dedicated local repo-context builder. Sandbox memory prompts mention workspace/repo, but not a repo map. | Present: `build_task_repo_context()`, selected files, literal code matches. | Do not replace `my_agent` repo context with reference concepts. | `src/agents/sandbox/`, baseline `repo_context.py` description |
| Patch / diff / edit | Yes. `ApplyPatchTool`, `ApplyPatchEditor`, sandbox `SandboxApplyPatchTool`, `apply_diff()`, patch operation parsing. | Present: local patch parser, dry-run, apply patch, edit approval policy. | Borrow operation-level patch result shape and approval callback pattern; do not copy full editor stack. | `src/agents/tool.py`, `src/agents/editor.py`, `src/agents/apply_diff.py`, `src/agents/sandbox/capabilities/tools/apply_patch_tool.py` |
| Run state / task state | Yes. `RunState` serializes current agent, generated items, approvals, trace, sandbox resume, model responses. | Present: `RunState`, result snapshots, CLI pending state envelope. | Borrow ideas around approval identity and trace/sandbox metadata, but full serializer is too heavy. | `src/agents/run_state.py`, `src/agents/result.py`, baseline |
| Session / memory / compression | Yes. `Session` protocol, `SQLiteSession`, OpenAI Responses compaction-aware session protocol, sandbox memory generation. | Present: `AgentSession`, `JsonSession`, compaction with rule/model summarizers. | Borrow minimal async session protocol and SQLite persistence interface; avoid sandbox memory generation now. | `src/agents/memory/session.py`, `src/agents/memory/sqlite_session.py`, `src/agents/sandbox/memory/` |
| Tracing | Yes. Trace/span API, typed span helpers, processors, exporters, context manager. | Present: tracing module with spans and JSONL/in-memory support. | Borrow typed span taxonomy and processor/exporter separation selectively. | `src/agents/tracing/create.py`, `src/agents/tracing/traces.py`, `src/agents/tracing/processor_interface.py` |
| Guardrails | Yes. Agent input/output guardrails and tool input/output guardrails with allow, reject_content, raise_exception behavior. | Present: agent guardrails and tool guardrails. | Borrow the explicit tool guardrail behavior model and model-visible rejection path. | `src/agents/guardrail.py`, `src/agents/tool_guardrails.py`, `src/agents/run_internal/tool_execution.py` |
| RAG / repo map / symbol index | Hosted file search exists; no local repo map or symbol graph found. | No semantic RAG or symbol index; lightweight repo context exists. | Do not borrow hosted `FileSearchTool` as a coding repo index. Borrow only the idea that retrieval is a tool surface. | `src/agents/tool.py`, `examples/tools/file_search.py`, baseline |
| Planning / coding loop | General runner loop exists; sandbox examples are coding-adjacent. No planner/executor coding loop. | Basic coding loop exists; no planner/executor split. | Borrow loop control boundaries, not a coding planner. | `src/agents/run.py`, `src/agents/run_internal/turn_resolution.py`, `examples/sandbox/` |
| Testing / validation | Strong test suite exists around run state, HITL, tracing, sandbox, tool guardrails. Runtime has no dedicated test-failure parser. | Verification command observations exist; no structured failure parser. | Borrow test coverage patterns for resume, guardrails, tool approval, and sandbox behavior. | `tests/test_run_state.py`, `tests/test_hitl_error_scenarios.py`, `tests/test_tool_guardrails.py`, `tests/sandbox/test_runtime.py` |
| Subagent / parallel task | Yes via handoff and `Agent.as_tool()`; function tools can run in batches. No coding-specific subagent scheduler. | No subagent or parallel execution. | Borrow agent-as-tool before full handoff team orchestration; batch execution concept can inform future parallel tools. | `src/agents/agent.py`, `src/agents/handoffs/__init__.py`, `src/agents/run_internal/tool_execution.py` |

## BC-01: Declarative Agent Runtime Contract

- Problem solved:
  - Keeps an agent definition as a declarative object instead of spreading runtime configuration across the runner, model layer, and tool layer.
- Reference implementation:
  - Files:
    - `src/agents/agent.py`
  - Classes / functions:
    - `AgentBase`
    - `Agent`
    - `Agent.clone()`
    - `Agent.get_system_prompt()`
    - `Agent.get_prompt()`
- Execution flow:
  - `Runner` receives an `Agent`, asks it for tools, handoffs, prompt/instructions, model settings, output schema, hooks, and guardrails, then uses those values during each run turn.
- Value for `my_agent`:
  - `my_agent` already has `Agent`, but the reference gives a cleaner pattern for keeping runtime-owned capabilities on the agent while keeping the runner focused on execution.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/agent.py`
  - `src/agents/coding_agent.py`
  - `src/agents/model_settings.py`
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Keep the existing `my_agent.Agent` public API stable.
  - Gradually clarify fields into stable groups: instructions/prompt, model settings, tools, handoffs, guardrails, hooks, output behavior, coding capabilities.
  - Add only fields needed by current coding-agent work; avoid mirroring every SDK option.
- Risks:
  - Copying the full reference shape would overfit a general SDK and add unused abstractions.
  - Adding async prompt callables would complicate the current synchronous loop.
- Evidence files:
  - `src/agents/agent.py`
  - `src/agents/run.py`

## BC-02: Runner Facade and Executor Split

- Problem solved:
  - Separates the public `Runner` API from the complex execution engine, making the user-facing entrypoint stable while the loop evolves internally.
- Reference implementation:
  - Files:
    - `src/agents/run.py`
    - `src/agents/run_internal/run_loop.py`
  - Classes / functions:
    - `Runner`
    - `AgentRunner`
    - `Runner.run()`
    - `Runner.run_sync()`
    - `Runner.run_streamed()`
    - `run_single_turn()`
- Execution flow:
  - `Runner` classmethods delegate to `DEFAULT_AGENT_RUNNER`; `AgentRunner.run()` manages context, session, trace, sandbox runtime, model turns, guardrails, and final result construction.
- Value for `my_agent`:
  - `my_agent.Runner.run_sync()` is intentionally thin. Borrowing the facade/executor split would let the coding loop grow without turning the public runner into a large integration module.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/runner.py`
  - `src/agents/run_loop.py`
  - optional new internal module under `src/agents/run_internal/` only if current files become too large.
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Preserve `Runner.run_sync()` as public API.
  - Introduce a private executor object only when new run modes or resume paths make `run_loop.py` difficult to maintain.
  - Move orchestration helpers behind the executor without changing result contracts.
- Risks:
  - Prematurely splitting `my_agent` could add indirection before the loop needs it.
  - Async parity with the reference is not a current requirement.
- Evidence files:
  - `src/agents/run.py`
  - `src/agents/run_internal/run_loop.py`

## BC-03: Explicit `NextStep` Loop Outcomes

- Problem solved:
  - Makes each model turn produce a typed control decision: final output, handoff, run again, or interruption.
- Reference implementation:
  - Files:
    - `src/agents/run_internal/run_steps.py`
    - `src/agents/run_internal/turn_resolution.py`
  - Classes / functions:
    - `NextStepFinalOutput`
    - `NextStepHandoff`
    - `NextStepRunAgain`
    - `NextStepInterruption`
    - `SingleStepResult`
    - `get_single_step_result_from_response()`
- Execution flow:
  - A model response is parsed into a `ProcessedResponse`; tool execution and handoff logic produce a `SingleStepResult`; the runner advances based on the `NextStep*` value.
- Value for `my_agent`:
  - This is one of the strongest borrowing candidates because coding-agent loops need clear stop, continue, approval pause, and future delegation states.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_steps.py`
  - `src/agents/turn_resolution.py`
  - `src/agents/run_loop.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Keep the small `my_agent` state machine, but model turn outcomes with explicit dataclasses.
  - Map current final-answer, tool-call, approval-required, max-limit, and verification-retry states onto these outcome classes.
  - Add handoff outcome only if handoff support is actively implemented.
- Risks:
  - Too many outcome variants could obscure the currently readable loop.
  - Resume serialization must stay compatible with existing `RunResult.to_state()` behavior.
- Evidence files:
  - `src/agents/run_internal/run_steps.py`
  - `src/agents/run_internal/turn_resolution.py`
  - `src/agents/run.py`

## BC-04: Response Processing Before Side Effects

- Problem solved:
  - Prevents model output parsing, tool routing, handoff routing, approval discovery, and side effects from being interleaved in one loop block.
- Reference implementation:
  - Files:
    - `src/agents/run_internal/turn_resolution.py`
    - `src/agents/run_internal/run_steps.py`
  - Classes / functions:
    - `process_model_response()`
    - `execute_tools_and_side_effects()`
    - `ProcessedResponse`
    - `ToolRunFunction`
    - `ToolRunHandoff`
    - `ToolRunShellCall`
    - `ToolRunApplyPatchCall`
- Execution flow:
  - `process_model_response()` classifies model output into messages, tool queues, handoff queues, approvals, shell, patch, custom, and hosted tool actions. `execute_tools_and_side_effects()` then executes or interrupts.
- Value for `my_agent`:
  - `my_agent` already has `turn_resolution`, `tool_planning`, and `tool_execution`; the reference validates the same boundary and offers a richer queue model.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/turn_resolution.py`
  - `src/agents/tool_planning.py`
  - `src/agents/tool_execution.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Keep parsing model output into an intermediate plan before executing any tool.
  - Add only the action groups `my_agent` needs now: function tool, handoff, shell, patch, approval.
  - Avoid hosted-tool and computer-action branches until they are real requirements.
- Risks:
  - The reference handles many Responses API item types that are not needed in `my_agent`.
  - A broad port would violate the minimal-diff rule.
- Evidence files:
  - `src/agents/run_internal/turn_resolution.py`
  - `src/agents/run_internal/tool_execution.py`

## BC-05: Function Tool Contract With Schema, Origin, Error, and Timeout Metadata

- Problem solved:
  - Gives every callable tool a single runtime contract for schema, invocation, approval, guardrails, enablement, failure formatting, and metadata.
- Reference implementation:
  - Files:
    - `src/agents/tool.py`
    - `src/agents/function_schema.py`
    - `src/agents/run_internal/tool_execution.py`
  - Classes / functions:
    - `FunctionTool`
    - `FunctionToolResult`
    - `function_tool()`
    - `function_schema()`
    - `invoke_function_tool()`
    - `_FunctionToolBatchExecutor`
- Execution flow:
  - Python callables are converted into JSON-schema tool specs, parsed through Pydantic models, invoked with optional context, and converted into tool output items with error and timeout handling.
- Value for `my_agent`:
  - `my_agent` has a functional tool registry. The reference suggests useful additions around origin metadata, timeout configuration, and structured function-tool results.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tools.py`
  - `src/agents/tool_schema.py`
  - `src/agents/tool_execution.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Add fields only when needed: `origin`, optional `timeout`, optional `is_enabled`, and a consistent error-message hook.
  - Preserve the existing `function_tool()` decorator behavior unless a test demands contract changes.
  - Avoid copying all hosted tool classes.
- Risks:
  - Reference `FunctionTool` supports a wide SDK surface; full adoption would bloat `my_agent`.
  - Timeout and cancellation semantics are harder in `my_agent` because the loop is synchronous.
- Evidence files:
  - `src/agents/tool.py`
  - `src/agents/function_schema.py`
  - `src/agents/run_internal/tool_execution.py`

## BC-06: Human Approval as First-Class Interruption State

- Problem solved:
  - Converts risky tool execution into a resumable interruption instead of a hard failure or ad hoc prompt.
- Reference implementation:
  - Files:
    - `src/agents/run_context.py`
    - `src/agents/run_state.py`
    - `src/agents/result.py`
    - `src/agents/run_internal/turn_resolution.py`
    - `src/agents/run_internal/tool_execution.py`
  - Classes / functions:
    - `RunContextWrapper.approve_tool()`
    - `RunContextWrapper.reject_tool()`
    - `RunState.approve()`
    - `RunState.reject()`
    - `resolve_approval_status()`
    - `resolve_approval_interruption()`
    - `NextStepInterruption`
- Execution flow:
  - Tool planning identifies a required approval, records an interruption, returns a result with serializable state, and resumes after `RunState.approve()` or `RunState.reject()`.
- Value for `my_agent`:
  - `my_agent` already has approval pause/resume. Borrowing the reference's approval identity and rejection-message handling can make the system more reliable across duplicate tool names, retries, and resume.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_state.py`
  - `src/agents/run_resume.py`
  - `src/agents/tool_planning.py`
  - `src/agents/tool_execution.py`
  - `src/agents/coding_state.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Keep existing pending approval snapshots.
  - Strengthen approval keys around tool name, namespace/origin, call id, and optional agent identity.
  - Preserve model-visible rejection observations so the loop can continue after user rejection.
- Risks:
  - Full reference approval serialization is complex and tied to multiple hosted tool types.
  - Changing approval IDs can break saved pending states unless versioned.
- Evidence files:
  - `src/agents/run_context.py`
  - `src/agents/run_state.py`
  - `src/agents/run_internal/turn_resolution.py`
  - `src/agents/run_internal/tool_execution.py`

## BC-07: Run Context Wrapper as Non-Model Runtime State

- Problem solved:
  - Provides tools and hooks with runtime state without leaking that state into the model prompt.
- Reference implementation:
  - Files:
    - `src/agents/run_context.py`
    - `src/agents/run_internal/agent_runner_helpers.py`
  - Classes / functions:
    - `RunContextWrapper`
    - `AgentHookContext`
    - `ensure_context_wrapper()`
    - `_fork_with_tool_input()`
    - `_fork_without_tool_input()`
- Execution flow:
  - Runner normalizes user context into `RunContextWrapper`; tools and hooks receive it; approval and usage state live there; tool calls may fork context with tool input.
- Value for `my_agent`:
  - `my_agent` has a context wrapper. The reference reinforces keeping workspace, approvals, usage, and private runtime dependencies out of model-visible context.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/contracts.py`
  - `src/agents/tool_runtime.py`
  - `src/agents/run_state.py`
- Recommended borrowing method: copy_concept_only
- Implementation sketch:
  - Audit current context usage and keep a strict split between model prompt chunks and runtime-only state.
  - Add small context-fork helpers only if tool guardrails or hooks need tool-specific context.
- Risks:
  - Over-expanding the context object can become a service locator.
  - Serialization of arbitrary context should remain boundary-specific.
- Evidence files:
  - `src/agents/run_context.py`
  - `src/agents/run_internal/agent_runner_helpers.py`

## BC-08: Handoff as a Tool-Call Transition

- Problem solved:
  - Lets a model transfer control to another agent through the same model-visible mechanism as tool calls.
- Reference implementation:
  - Files:
    - `src/agents/handoffs/__init__.py`
    - `src/agents/handoffs/history.py`
    - `src/agents/run_internal/turn_resolution.py`
  - Classes / functions:
    - `Handoff`
    - `handoff()`
    - `HandoffInputData`
    - `Handoff.get_transfer_message()`
    - `nest_handoff_history()`
    - `execute_handoffs()`
- Execution flow:
  - Agent handoffs are exposed as model tools. When selected, the runner optionally filters/nests history, runs handoff callbacks, and switches `current_agent`.
- Value for `my_agent`:
  - Useful for future coding roles such as triage, implementation, review, and validation. The best immediate borrowing is the model of handoff as a tool call, not a separate command channel.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/agent.py`
  - `src/agents/tool_planning.py`
  - `src/agents/turn_resolution.py`
  - `src/agents/run_steps.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Represent handoff targets as tool specs with stable names.
  - On handoff, switch the current agent and preserve only the relevant history.
  - Delay nested history summarization until multiple-agent flows are real.
- Risks:
  - `my_agent` does not yet need multi-agent orchestration for basic coding tasks.
  - Incorrect history filtering can hide important code context.
- Evidence files:
  - `src/agents/handoffs/__init__.py`
  - `src/agents/handoffs/history.py`
  - `src/agents/run_internal/turn_resolution.py`

## BC-09: Agent-as-Tool for Local Subtasks

- Problem solved:
  - Allows one agent to call another agent for a bounded subtask without transferring full control.
- Reference implementation:
  - Files:
    - `src/agents/agent.py`
    - `src/agents/agent_tool_state.py`
    - `src/agents/run_internal/tool_execution.py`
  - Classes / functions:
    - `Agent.as_tool()`
    - agent-tool pending state helpers
    - nested tool run result handling in `_FunctionToolBatchExecutor`
- Execution flow:
  - `Agent.as_tool()` wraps an agent as a `FunctionTool`; the parent model calls it; the nested run returns an output and may surface nested interruptions.
- Value for `my_agent`:
  - More suitable than full handoff for early subagent behavior, such as review-only or test-analysis tasks.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/agent.py`
  - `src/agents/tools.py`
  - `src/agents/tool_execution.py`
  - future coding-agent subtask module if needed.
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Add a narrow helper that turns an `Agent` into a tool with a prompt/input string.
  - Return only text or a structured result object.
  - Treat nested approval as unsupported initially unless a concrete workflow requires it.
- Risks:
  - Nested agent runs can make tracing, session history, and approval resume more complex.
  - Parallel subagents should not be added until deterministic single-subtask behavior is stable.
- Evidence files:
  - `src/agents/agent.py`
  - `src/agents/run_internal/tool_execution.py`
  - `examples/agent_patterns/`

## BC-10: Minimal Session Protocol With SQLite Backend

- Problem solved:
  - Separates conversation persistence from runner internals while keeping the interface small.
- Reference implementation:
  - Files:
    - `src/agents/memory/session.py`
    - `src/agents/memory/sqlite_session.py`
    - `src/agents/run_internal/session_persistence.py`
  - Classes / functions:
    - `Session`
    - `SessionABC`
    - `SQLiteSession`
    - `OpenAIResponsesCompactionAwareSession`
- Execution flow:
  - Runner reads session items before model calls, appends new items after turns, and can pop/clear history through a minimal async protocol.
- Value for `my_agent`:
  - `my_agent` already has JSON session memory. A SQLite backend and smaller protocol can improve durability without changing the run loop heavily.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/memory.py`
  - `src/agents/coding_cli.py`
  - `src/agents/run_loop.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Keep `AgentSession` behavior.
  - Add an optional `SQLiteSession` implementation behind the same conceptual operations: get, add, pop, clear.
  - Do not adopt OpenAI server conversation tracking unless the model layer moves to that mode.
- Risks:
  - Async session methods do not map directly to the current synchronous runtime.
  - Session persistence must stay distinct from approval-resume snapshots.
- Evidence files:
  - `src/agents/memory/session.py`
  - `src/agents/memory/sqlite_session.py`
  - `src/agents/run_internal/session_persistence.py`

## BC-11: Typed Tracing Spans and Processor Boundary

- Problem solved:
  - Gives the runtime observability without making tracing part of correctness.
- Reference implementation:
  - Files:
    - `src/agents/tracing/create.py`
    - `src/agents/tracing/traces.py`
    - `src/agents/tracing/spans.py`
    - `src/agents/tracing/span_data.py`
    - `src/agents/tracing/processor_interface.py`
    - `src/agents/tracing/processors.py`
  - Classes / functions:
    - `trace()`
    - `agent_span()`
    - `task_span()`
    - `turn_span()`
    - `response_span()`
    - `function_span()`
    - `handoff_span()`
    - `guardrail_span()`
    - `TracingProcessor`
    - `TracingExporter`
- Execution flow:
  - A run opens a trace; each major runtime operation opens typed spans; processors receive start/end events and exporters handle delivery.
- Value for `my_agent`:
  - `my_agent` tracing exists, but the typed span taxonomy and processor/exporter boundary can make coding-agent evidence clearer.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/tracing.py`
  - `src/agents/run_loop.py`
  - `src/agents/model_turn.py`
  - `src/agents/tool_execution.py`
  - `src/agents/trajectory.py`
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Keep the current tracing API.
  - Add or rename span types only where they match existing runtime boundaries: task, agent, turn, model, tool, guardrail, verification.
  - Keep JSONL/in-memory exporters; do not copy backend exporter complexity.
- Risks:
  - Over-instrumentation can obscure the simpler trajectory evidence already present.
  - Sensitive-data flags must stay explicit.
- Evidence files:
  - `src/agents/tracing/create.py`
  - `src/agents/tracing/processor_interface.py`
  - `src/agents/tracing/processors.py`

## BC-12: Guardrail Behavior Model for Tool Safety

- Problem solved:
  - Gives safety checks a clear outcome: allow, reject content while continuing, or raise and halt.
- Reference implementation:
  - Files:
    - `src/agents/guardrail.py`
    - `src/agents/tool_guardrails.py`
    - `src/agents/run_internal/tool_execution.py`
  - Classes / functions:
    - `GuardrailFunctionOutput`
    - `InputGuardrail`
    - `OutputGuardrail`
    - `ToolGuardrailFunctionOutput`
    - `ToolInputGuardrail`
    - `ToolOutputGuardrail`
    - `_execute_tool_input_guardrails()`
    - `_execute_tool_output_guardrails()`
- Execution flow:
  - Guardrails run before model input, before final output acceptance, before tool invocation, and after tool output. Tool guardrails can reject with a model-visible message or raise a tripwire exception.
- Value for `my_agent`:
  - Highly relevant for shell, patch, filesystem, and external tool calls. The `reject_content` behavior is especially useful because it allows the model to recover.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/guardrails.py`
  - `src/agents/tool_guardrails.py`
  - `src/agents/tool_execution.py`
  - `src/agents/coding_policies.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Keep current guardrail APIs.
  - Add explicit behavior values for tool guardrails if not already equivalent.
  - Make rejection messages become normal tool observations, not hidden logs.
- Risks:
  - Guardrails should not duplicate workspace boundary validation.
  - Broad catch-and-continue behavior would conflict with `my_agent` fail-fast internal policy.
- Evidence files:
  - `src/agents/guardrail.py`
  - `src/agents/tool_guardrails.py`
  - `src/agents/run_internal/tool_execution.py`

## BC-13: Sandbox Agent and Capability Injection

- Problem solved:
  - Binds an agent to an execution environment where capabilities can add tools, instructions, and manifest changes at runtime.
- Reference implementation:
  - Files:
    - `src/agents/sandbox/sandbox_agent.py`
    - `src/agents/sandbox/runtime.py`
    - `src/agents/sandbox/runtime_agent_preparation.py`
    - `src/agents/sandbox/capabilities/capability.py`
    - `src/agents/sandbox/capabilities/filesystem.py`
    - `src/agents/sandbox/capabilities/shell.py`
  - Classes / functions:
    - `SandboxAgent`
    - `SandboxRuntime`
    - `prepare_sandbox_agent()`
    - `Capability`
    - `Filesystem`
    - `Shell`
- Execution flow:
  - Runner constructs `SandboxRuntime`; for a `SandboxAgent`, it ensures a sandbox session, clones capabilities, binds them to the session/user, injects capability tools and instructions, and runs a prepared execution agent.
- Value for `my_agent`:
  - Strong design reference for capability packs and workspace execution boundaries. `my_agent` should borrow the concept, not the full backend system.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_agent.py`
  - `src/agents/environment.py`
  - `src/agents/workspace.py`
  - `src/agents/workspace_tools.py`
  - `src/agents/shell_tools.py`
  - `src/agents/edit_tools.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Keep existing `CodingAgentProfile`.
  - Treat read, shell, edit, verification, and memory as explicit capability packs that register tools and instructions.
  - Keep the first implementation local-workspace based; do not introduce remote sandbox backends without a concrete requirement.
- Risks:
  - The reference sandbox stack is broad and backend-heavy.
  - Adding a generic capability plugin system too early would violate project anti-overengineering rules.
- Evidence files:
  - `src/agents/sandbox/sandbox_agent.py`
  - `src/agents/sandbox/runtime.py`
  - `src/agents/sandbox/capabilities/capability.py`
  - `src/agents/sandbox/capabilities/filesystem.py`
  - `src/agents/sandbox/capabilities/shell.py`

## BC-14: Shell and Apply-Patch Tool Surface

- Problem solved:
  - Exposes command execution and file mutation through explicit, auditable tool contracts.
- Reference implementation:
  - Files:
    - `src/agents/tool.py`
    - `src/agents/sandbox/capabilities/tools/shell_tool.py`
    - `src/agents/sandbox/capabilities/tools/apply_patch_tool.py`
    - `src/agents/sandbox/session/base_sandbox_session.py`
  - Classes / functions:
    - `ShellTool`
    - `ShellCommandRequest`
    - `ShellCommandOutput`
    - `ExecCommandTool`
    - `WriteStdinTool`
    - `ApplyPatchTool`
    - `SandboxApplyPatchTool`
    - `_parse_apply_patch_input()`
- Execution flow:
  - Shell calls are represented with structured command/output fields and optional approval. Patch calls parse freeform or JSON patch operations, execute through an editor/session boundary, and return structured output.
- Value for `my_agent`:
  - `my_agent` already has shell/test and patch tools. Borrow output normalization, PTY split concepts, and operation-level patch approval patterns where they solve current limitations.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/shell_tools.py`
  - `src/agents/edit_tools.py`
  - `src/agents/patches.py`
  - `src/agents/coding_policies.py`
  - `src/agents/environment.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Normalize shell output into status, exit code, stdout, stderr, and truncation metadata.
  - Keep `apply_patch` grammar compatible with current tests.
  - Consider operation-level approval only after current patch approval behavior is preserved.
- Risks:
  - PTY/interactive shell support can add substantial complexity.
  - Patch grammar changes can break existing model prompts and tests.
- Evidence files:
  - `src/agents/tool.py`
  - `src/agents/sandbox/capabilities/tools/shell_tool.py`
  - `src/agents/sandbox/capabilities/tools/apply_patch_tool.py`
  - `src/agents/sandbox/session/base_sandbox_session.py`

## 5. Designs Not Suitable for Current Borrowing

| Design | Why Not Suitable Now | Future Condition | Evidence |
| --- | --- | --- | --- |
| Full `RunState` serializer | Very broad: agent identities, trace state, sandbox state, model responses, approvals, guardrail results, nested agent tools. `my_agent` already has a smaller tested snapshot boundary. | Consider only if approval resume becomes cross-process, multi-agent, and sandbox-aware. | `src/agents/run_state.py`, `src/agents/result.py` |
| OpenAI server-managed conversation tracker | Tied to Responses API server conversation state and provider-specific behavior. | Consider if `my_agent` intentionally uses server-managed conversations as the main session path. | `src/agents/run_internal/oai_conversation.py`, `src/agents/run_internal/session_persistence.py` |
| Complete tracing backend exporter | The backend batch exporter, sanitization, truncation, and API delivery are more than the current local evidence need. | Consider after local trace schema stabilizes and external trace ingestion is a real requirement. | `src/agents/tracing/processors.py`, `src/agents/tracing/processor_interface.py` |
| Realtime and Voice modules | Unrelated to coding-agent upgrade. | Only relevant for a voice or realtime coding assistant product. | `src/agents/realtime/`, `src/agents/voice/` |
| Full sandbox backend system | Docker, remote snapshots, mounts, session clients, and persistence are too large for the current `my_agent` architecture. | Consider after local workspace boundaries are insufficient and a real isolation backend is required. | `src/agents/sandbox/sandboxes/`, `src/agents/sandbox/session/` |
| Hosted `FileSearchTool` as repo intelligence | Hosted vector search is not a local code understanding, mention resolution, repo map, or symbol index. | Could be useful for documentation RAG, not for core code navigation. | `src/agents/tool.py`, `examples/tools/file_search.py` |
| Full MCP lifecycle | Valuable but broader than the local coding-agent core; plugin dynamics would add a service layer early. | Consider when external tool ecosystem support is a product requirement. | `src/agents/mcp/`, examples under `examples/mcp/` |
| Experimental Codex tool wrapper | It wraps Codex CLI as a subprocess/tool and is explicitly experimental; using it would make `my_agent` depend on another coding agent. | Study later for thread/session streaming patterns, not as core implementation. | `src/agents/extensions/experimental/codex/`, `examples/tools/codex.py` |
| Sandbox memory generation pipeline | Rich but highly specific to sandbox rollouts, summaries, and memory artifacts. | Consider after `my_agent` has durable task histories and needs post-run memory extraction. | `src/agents/sandbox/memory/` |

## 6. Candidate Mapping to `my_agent`

| BC | Possible `my_agent` Module / File | Integration Difficulty | Benefit | Risk |
| --- | --- | --- | --- | --- |
| BC-01 | `src/agents/agent.py`, `src/agents/coding_agent.py` | Medium | Cleaner agent configuration boundary | Unused fields and API bloat |
| BC-02 | `src/agents/runner.py`, `src/agents/run_loop.py` | Medium | Public API remains stable while loop grows | Premature internal split |
| BC-03 | `src/agents/run_steps.py`, `src/agents/turn_resolution.py`, `src/agents/run_loop.py` | Medium | Clear control flow for final, rerun, approval, handoff | Snapshot compatibility changes |
| BC-04 | `src/agents/turn_resolution.py`, `src/agents/tool_planning.py`, `src/agents/tool_execution.py` | Medium | Side effects happen only after planning | Too many action groups |
| BC-05 | `src/agents/tools.py`, `src/agents/tool_schema.py`, `src/agents/tool_execution.py` | Medium | Stronger tool metadata, timeout, and error behavior | Async/cancellation mismatch |
| BC-06 | `src/agents/run_state.py`, `src/agents/run_resume.py`, `src/agents/coding_state.py` | High | More robust HITL approval resume | Saved-state schema migration |
| BC-07 | `src/agents/contracts.py`, `src/agents/tool_runtime.py`, `src/agents/run_state.py` | Low | Cleaner runtime-only context | Context object becomes too broad |
| BC-08 | `src/agents/agent.py`, `src/agents/turn_resolution.py`, `src/agents/run_steps.py` | High | Future role-based agent transitions | History filtering complexity |
| BC-09 | `src/agents/agent.py`, `src/agents/tools.py`, `src/agents/tool_execution.py` | High | Bounded subtask delegation | Nested approvals and tracing complexity |
| BC-10 | `src/agents/memory.py`, `src/agents/coding_cli.py` | Medium | Durable session backend | Mixing session and resume state |
| BC-11 | `src/agents/tracing.py`, `src/agents/trajectory.py` | Low | Better evidence taxonomy | Over-instrumentation |
| BC-12 | `src/agents/guardrails.py`, `src/agents/tool_guardrails.py`, `src/agents/coding_policies.py` | Low | Safer shell/edit behavior with model recovery | Duplicated boundary validation |
| BC-13 | `src/agents/coding_agent.py`, `src/agents/environment.py`, `src/agents/workspace.py` | High | Capability-pack model for coding tools | Premature plugin/capability framework |
| BC-14 | `src/agents/shell_tools.py`, `src/agents/edit_tools.py`, `src/agents/patches.py` | Medium | Better shell/patch auditability | Patch grammar and PTY complexity |

## 7. Questions for the Main Agent

1. Should `my_agent` remain strictly synchronous in the next implementation phase, or is an async runner boundary acceptable if it is internally hidden?
2. Should subagent support start with agent-as-tool only, leaving handoff for a later phase?
3. Should approval snapshot compatibility be preserved exactly, or can a versioned migration be introduced?
4. Is local workspace execution enough for the next phase, or should sandbox boundaries be modeled now even if the backend remains local?
5. Should tracing and trajectory remain separate outputs, or should trajectory records be generated from trace spans?
6. Should the next implementation phase invest in repo intelligence first, since the reference does not provide mention resolution, repo context, or symbol indexing?

## 8. Summary Points for the Main Agent

1. The strongest borrowing candidates are the `NextStep*` loop outcome model, response-processing-before-side-effects flow, tool approval interruption model, and tool guardrail behavior model.
2. `reference/openaiagent` is not a coding-agent repo intelligence reference; it does not provide native mention resolution, repo context assembly, or a local symbol index.
3. `my_agent` is already ahead of the reference in local workspace inventory, mention resolution, selected files, and lightweight repo context.
4. The reference's `FunctionTool` contract is useful, but only selected metadata and behavior should be adapted.
5. Handoff is worth studying as a future control-transfer mechanism, but agent-as-tool is the safer first borrowing path for subtask delegation.
6. The sandbox design is valuable conceptually: capabilities can inject tools and instructions into a prepared execution agent.
7. The full sandbox backend, full `RunState` serializer, full tracing exporter, full MCP lifecycle, and experimental Codex wrapper are not good current borrowing targets.
8. Session persistence can borrow the minimal protocol and SQLite backend idea while keeping session history separate from approval resume state.
9. Shell and patch tools should borrow structured output and approval concepts without adopting PTY or remote sandbox complexity immediately.
10. The upgrade work should treat this reference as a runtime architecture source, not as code to copy wholesale.
