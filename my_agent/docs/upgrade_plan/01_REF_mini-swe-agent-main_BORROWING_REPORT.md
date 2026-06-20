# Reference Borrowing Report: mini-swe-agent-main

## 1. Metadata

| Field | Value |
|---|---|
| Project name | `mini-swe-agent-main` |
| Project path | `C:\Users\ch\Desktop\ai agent学习\reference\mini-swe-agent-main` |
| Subagent name | `reference_mini-swe-agent-main_analysis` |
| Analysis scope | Only `reference/mini-swe-agent-main/` |
| Codegraph status | Available for this path: 133 indexed files, 1630 nodes, 3035 edges |
| Required baseline reviewed | `my_agent/docs/upgrade_plan/00_MY_AGENT_BASELINE.md` |
| Required reference architecture reviewed | `reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md` |

Key files reviewed:

- `src/minisweagent/__init__.py`
- `src/minisweagent/agents/default.py`
- `src/minisweagent/agents/interactive.py`
- `src/minisweagent/agents/__init__.py`
- `src/minisweagent/models/__init__.py`
- `src/minisweagent/models/litellm_model.py`
- `src/minisweagent/models/litellm_response_model.py`
- `src/minisweagent/models/litellm_textbased_model.py`
- `src/minisweagent/models/test_models.py`
- `src/minisweagent/models/utils/actions_toolcall.py`
- `src/minisweagent/models/utils/actions_toolcall_response.py`
- `src/minisweagent/models/utils/actions_text.py`
- `src/minisweagent/environments/__init__.py`
- `src/minisweagent/environments/local.py`
- `src/minisweagent/environments/docker.py`
- `src/minisweagent/config/__init__.py`
- `src/minisweagent/config/mini.yaml`
- `src/minisweagent/run/mini.py`
- `src/minisweagent/run/benchmarks/swebench.py`
- `src/minisweagent/run/benchmarks/programbench.py`
- `src/minisweagent/run/utilities/inspector.py`
- `tests/agents/test_default.py`
- `tests/agents/test_interactive.py`
- `tests/models/test_actions_toolcall.py`
- `tests/models/test_test_models.py`
- `tests/environments/test_local.py`
- `tests/run/test_cli_integration.py`

## 2. One-Sentence Project Positioning

`mini-swe-agent` is a deliberately compact bash-only coding agent that solves code tasks by iterating model response, command execution, observation, and trajectory save; its most useful lesson for `my_agent` is how much coding-agent behavior can be made debuggable through a small Agent / Model / Environment split and a linear command-observation loop.

## 3. Capability Matrix Related to `my_agent`

| Capability | Exists in Reference | `my_agent` Current State | Borrowing Value | Evidence Files |
|---|---:|---|---|---|
| Agent abstraction | Yes | `my_agent` has `Agent`, `AgentCapabilities`, `Runner`, and a coding profile builder. | Moderate: reference shows a smaller `Agent` that owns only loop state and delegates provider/execution details. | `src/minisweagent/__init__.py`, `src/minisweagent/agents/default.py` |
| Runner / run loop | Partial | `my_agent` has `Runner.run_sync()` and `run_agent_loop()` with guardrails, tools, verification, approval resume, and tracing. | Moderate: reference loop is useful as a readability target, not as a replacement. | `src/minisweagent/agents/default.py`, `src/minisweagent/run/mini.py` |
| Tool system | Minimal | `my_agent` has a typed tool registry, planning, execution, guardrails, approvals, workspace tools, shell tools, and patch tools. | Low to moderate: the single bash tool is a simplification pattern, not a full tool-system replacement. | `src/minisweagent/models/utils/actions_toolcall.py`, `src/minisweagent/models/utils/actions_toolcall_response.py` |
| File discovery | Via shell only | `my_agent` has workspace inventory, file search, line reads, related-file heuristics, and selected files. | Low: reference delegates discovery to commands such as `ls`, `find`, `grep`, or `rg` through bash. | `src/minisweagent/config/mini.yaml`, `src/minisweagent/environments/local.py` |
| Mention resolution | No | `my_agent` has `detect_file_mentions()`, inventory matching, selected files, and repo context. | None for direct borrowing. | No mention-resolution modules found under `src/minisweagent`; `rg` found no repo-map or symbol-resolution implementation. |
| Repo context | Prompt-only / shell-discovered | `my_agent` has `build_task_repo_context()` and rendered repo context. | Low: reference prompt instructions may help shape workflow text, but not runtime context assembly. | `src/minisweagent/config/mini.yaml` |
| Patch / diff / edit | Via shell only | `my_agent` has `apply_patch`, patch parsing, dry-run validation, workspace path checks, and approval policies. | Low: reference has useful workflow prompting for editing, but no structured edit subsystem. | `src/minisweagent/config/mini.yaml`, `src/minisweagent/environments/local.py` |
| Run state / task state | Simple linear message list | `my_agent` has `RunState`, `RunResult`, snapshots, approval resume state, and CLI state envelopes. | Moderate: reference trajectory is simpler and can inform compact evidence views. | `src/minisweagent/agents/default.py`, `src/minisweagent/exceptions.py` |
| Session / memory / compression | No durable memory or compression | `my_agent` has `AgentSession`, `JsonSession`, memory compaction, and summarizers. | Low: reference is useful mainly as a reminder to keep early coding loops linear and inspectable. | `src/minisweagent/agents/default.py` |
| Tracing | Trajectory and logging, no span framework | `my_agent` has trace/span APIs and JSONL tracing. | Moderate: reference trajectory format may complement spans with a human-readable whole-run artifact. | `src/minisweagent/agents/default.py`, `src/minisweagent/run/utilities/inspector.py` |
| Guardrails | Basic format errors, limits, and human confirmation | `my_agent` has agent and tool guardrail layers plus coding policies. | Moderate: reference `FormatError` feedback loop is a concise pattern for malformed model actions. | `src/minisweagent/exceptions.py`, `src/minisweagent/models/utils/actions_toolcall.py`, `src/minisweagent/agents/interactive.py` |
| RAG / repo map / symbol index | No | `my_agent` lacks semantic RAG and persistent in-runtime symbol index but has heuristic repo context and AST outlines. | None for RAG or symbol index. | `rg` under `src/minisweagent` found no retrieval/vector/embedding/repo-map/symbol-index implementation. |
| Planning / coding loop | Prompt-guided only | `my_agent` has a functional coding CLI and verification feedback but no planner/executor split. | Moderate: reference prompt encodes a clear analyze/edit/verify/submit workflow. | `src/minisweagent/config/mini.yaml` |
| Testing / validation | Command execution and sentinel final submission | `my_agent` has shell/test tools and optional verification after tools. | Moderate: deterministic models are useful for tests; sentinel finish is less suitable because `my_agent` already has final-answer and verification concepts. | `src/minisweagent/environments/local.py`, `src/minisweagent/models/test_models.py`, `tests/agents/test_default.py` |
| Subagent / parallel task | No agent-level subagents; benchmark runners use threads | `my_agent` has no subagents or parallel tool execution. | Low: batch benchmark thread pools are not a subagent architecture, but can inform future evaluation runners. | `src/minisweagent/run/benchmarks/swebench.py`, `src/minisweagent/run/benchmarks/programbench.py` |

## 4. Borrowing Candidates

## BC-01: Minimal Agent / Model / Environment Boundary

- Problem solved:
  - Keeps the coding loop independent from provider-specific response formats and execution-environment details.
- Reference implementation:
  - Files:
    - `src/minisweagent/__init__.py`
    - `src/minisweagent/agents/default.py`
    - `src/minisweagent/models/litellm_model.py`
    - `src/minisweagent/environments/local.py`
  - Classes / functions:
    - `Model`
    - `Environment`
    - `Agent`
    - `DefaultAgent`
    - `LitellmModel`
    - `LocalEnvironment`
- Execution flow:
  - `DefaultAgent.run()` initializes messages from templates, loops through `step()`, asks `model.query()` for a response, calls `env.execute()` for parsed actions, and asks the model adapter to format observations back into messages.
- Value for `my_agent`:
  - `my_agent` already has stronger abstractions, but this reference clarifies which responsibilities can stay separate: loop orchestration, model normalization, and environment execution.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_loop.py`
  - `src/agents/model_turn.py`
  - `src/agents/models.py`
  - `src/agents/environment.py`
  - `src/agents/shell_tools.py`
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - Do not replace the current runtime. Use the reference boundary as a review lens: keep provider-specific parsing in model adapters, keep shell execution in environment/tool modules, and keep `run_loop.py` focused on state transitions.
- Risks:
  - Directly copying the reference would remove `my_agent` features such as typed tools, approval resume, guardrails, and verification.
- Evidence files:
  - `src/minisweagent/__init__.py`
  - `src/minisweagent/agents/default.py`
  - `src/minisweagent/models/litellm_model.py`
  - `src/minisweagent/environments/local.py`

## BC-02: Linear Command-Observation Coding Loop

- Problem solved:
  - Makes the agent's behavior easy to inspect: every iteration is model message, action execution, observation message, repeat.
- Reference implementation:
  - Files:
    - `src/minisweagent/agents/default.py`
  - Classes / functions:
    - `DefaultAgent.run()`
    - `DefaultAgent.step()`
    - `DefaultAgent.query()`
    - `DefaultAgent.execute_actions()`
- Execution flow:
  - `run()` renders system/user prompts, loops until the last message has role `exit`, and saves the trajectory in a `finally` block each turn.
- Value for `my_agent`:
  - Useful as a mental model for a more autonomous coding loop and for simplifying CLI explanations and trajectory views.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_loop.py`
  - `src/agents/coding_cli.py`
  - `src/agents/trajectory.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Preserve `my_agent`'s existing tool/state machinery, but present coding-agent runs in the same readable sequence: model turn, selected tool calls, observations, verification, next turn.
- Risks:
  - Over-simplifying `my_agent` around this loop could break pause/resume approvals and structured final output.
- Evidence files:
  - `src/minisweagent/agents/default.py`
  - `tests/agents/test_default.py`

## BC-03: Model Adapter Owns Action Parsing

- Problem solved:
  - Prevents the agent loop from needing to understand Chat Completions, Responses API, or text-regex action formats.
- Reference implementation:
  - Files:
    - `src/minisweagent/models/litellm_model.py`
    - `src/minisweagent/models/litellm_response_model.py`
    - `src/minisweagent/models/litellm_textbased_model.py`
    - `src/minisweagent/models/utils/actions_toolcall.py`
    - `src/minisweagent/models/utils/actions_toolcall_response.py`
    - `src/minisweagent/models/utils/actions_text.py`
  - Classes / functions:
    - `LitellmModel.query()`
    - `LitellmModel._parse_actions()`
    - `LitellmResponseModel.query()`
    - `parse_toolcall_actions()`
    - `parse_toolcall_actions_response()`
    - `parse_regex_actions()`
- Execution flow:
  - Provider response is converted into a normalized assistant message with `extra.actions`; the agent loop reads only the normalized action list.
- Value for `my_agent`:
  - `my_agent` already normalizes model outputs into contracts, but this is a good pattern for keeping API-specific parsing outside the coding loop.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/models.py`
  - `src/agents/model_turn.py`
  - `src/agents/contracts.py`
  - `src/agents/turn_resolution.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - When expanding model support, keep each provider's tool-call quirks inside the adapter and expose a stable internal `ToolCall` / action contract to `run_loop.py`.
- Risks:
  - The reference uses loose dictionaries; `my_agent` should preserve its typed contracts instead of copying the dict shape.
- Evidence files:
  - `src/minisweagent/models/litellm_model.py`
  - `src/minisweagent/models/litellm_response_model.py`
  - `src/minisweagent/models/utils/actions_toolcall.py`
  - `src/minisweagent/models/utils/actions_toolcall_response.py`

## BC-04: Format Error as Model-Visible Recovery Message

- Problem solved:
  - Converts invalid model output into a structured observation that the model can repair on the next turn.
- Reference implementation:
  - Files:
    - `src/minisweagent/exceptions.py`
    - `src/minisweagent/models/utils/actions_toolcall.py`
    - `src/minisweagent/models/utils/actions_toolcall_response.py`
    - `src/minisweagent/models/utils/actions_text.py`
    - `src/minisweagent/config/mini.yaml`
  - Classes / functions:
    - `FormatError`
    - `InterruptAgentFlow`
    - `parse_toolcall_actions()`
    - `parse_toolcall_actions_response()`
    - `parse_regex_actions()`
- Execution flow:
  - Parser detects missing/invalid tool calls, raises `FormatError` carrying a user-style correction message, and `DefaultAgent.run()` appends that message through `InterruptAgentFlow`.
- Value for `my_agent`:
  - Useful for malformed model tool calls or invalid final-answer attempts: fail into model-visible corrective feedback rather than obscure internal exceptions.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/turn_resolution.py`
  - `src/agents/tool_observations.py`
  - `src/agents/model_turn.py`
  - `src/agents/run_loop.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Add or preserve a narrow path where model-format failures become explicit observations, while programmer errors and internal invariant breaks still fail fast.
- Risks:
  - Overusing this pattern could hide internal bugs. It should apply only to model output at the model boundary.
- Evidence files:
  - `src/minisweagent/exceptions.py`
  - `src/minisweagent/models/utils/actions_toolcall.py`
  - `src/minisweagent/config/mini.yaml`

## BC-05: Environment Execution Contract

- Problem solved:
  - Makes command execution replaceable across local shell, Docker, Singularity, and other sandboxes.
- Reference implementation:
  - Files:
    - `src/minisweagent/__init__.py`
    - `src/minisweagent/environments/__init__.py`
    - `src/minisweagent/environments/local.py`
    - `src/minisweagent/environments/docker.py`
  - Classes / functions:
    - `Environment`
    - `LocalEnvironment`
    - `DockerEnvironment`
    - `get_environment()`
    - `get_environment_class()`
- Execution flow:
  - CLI config resolves an environment class, creates it, and the agent sends every action to `env.execute(action)`, receiving `output`, `returncode`, and `exception_info`.
- Value for `my_agent`:
  - `my_agent` has local environment/shell tools; this reference offers a clear path for later Docker or sandbox execution without changing the agent loop.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/environment.py`
  - `src/agents/shell_tools.py`
  - `src/agents/coding_agent.py`
  - `src/agents/coding_policies.py`
- Recommended borrowing method: adapt_tool_contract
- Implementation sketch:
  - Define a stable shell execution result contract and keep environment selection outside shell tool logic. Add Docker only when a concrete task requires sandboxing.
- Risks:
  - The reference uses `shell=True` locally and direct Docker subprocess calls; copying those details without `my_agent`'s approval/path policies would reduce safety.
- Evidence files:
  - `src/minisweagent/environments/local.py`
  - `src/minisweagent/environments/docker.py`
  - `src/minisweagent/environments/__init__.py`

## BC-06: Trajectory-First Whole-Run Evidence

- Problem solved:
  - Provides a single artifact containing config, model stats, messages, exit status, submission, and version for debugging and reproducibility.
- Reference implementation:
  - Files:
    - `src/minisweagent/agents/default.py`
    - `src/minisweagent/run/utilities/inspector.py`
  - Classes / functions:
    - `DefaultAgent.serialize()`
    - `DefaultAgent.save()`
    - `TrajectoryInspector`
- Execution flow:
  - `DefaultAgent.run()` calls `save()` after every loop turn; `serialize()` merges agent, model, and environment metadata with the full message trajectory.
- Value for `my_agent`:
  - `my_agent` already has trajectory JSONL and tracing, but a compact whole-run artifact could make review and debugging easier.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/trajectory.py`
  - `src/agents/run_recording.py`
  - `src/agents/coding_cli.py`
  - `src/agents/tracing.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Keep JSONL as event evidence, but consider an optional summary export that groups task metadata, config, final state, and key messages.
- Risks:
  - Duplicating tracing, session, and trajectory responsibilities could create conflicting evidence formats.
- Evidence files:
  - `src/minisweagent/agents/default.py`
  - `src/minisweagent/run/utilities/inspector.py`
  - `tests/run/test_save.py`

## BC-07: Prompt-Encoded Coding Workflow and Observation Clipping

- Problem solved:
  - Gives the model explicit workflow constraints and keeps large command outputs bounded before they re-enter context.
- Reference implementation:
  - Files:
    - `src/minisweagent/config/mini.yaml`
  - Classes / functions:
    - YAML `agent.instance_template`
    - YAML `model.observation_template`
    - YAML `model.format_error_template`
- Execution flow:
  - Config templates render the initial coding instructions and observation format; long outputs are split into head/tail with an elision count.
- Value for `my_agent`:
  - Useful for strengthening coding-agent instructions and tool observation formatting without adding new runtime abstractions.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_agent.py`
  - `src/agents/tool_observations.py`
  - `src/agents/context_chunks.py`
  - `src/agents/coding_cli.py`
- Recommended borrowing method: copy_concept_only
- Implementation sketch:
  - Borrow the concepts of explicit analyze/edit/verify workflow and bounded output summaries. Do not copy the exact prompt or shell-edit examples wholesale.
- Risks:
  - Prompt-only workflow enforcement is weaker than `my_agent`'s tool policies and verification loop.
- Evidence files:
  - `src/minisweagent/config/mini.yaml`

## BC-08: Human Confirmation Modes for Interactive Runs

- Problem solved:
  - Supports human-in-the-loop execution with confirm, yolo, and human command modes.
- Reference implementation:
  - Files:
    - `src/minisweagent/agents/interactive.py`
    - `tests/agents/test_interactive.py`
  - Classes / functions:
    - `InteractiveAgent`
    - `InteractiveAgentConfig`
    - `InteractiveAgent.execute_actions()`
    - `InteractiveAgent._ask_confirmation_or_interrupt()`
    - `InteractiveAgent._check_for_new_task_or_submit()`
- Execution flow:
  - Before executing commands, the interactive agent asks for confirmation unless mode or whitelist skips it; rejection is appended as a user interruption message and the loop continues.
- Value for `my_agent`:
  - `my_agent` already has approval pause/resume. The reference is still useful for CLI ergonomics and mode naming.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_cli.py`
  - `src/agents/tool_planning.py`
  - `src/agents/run_resume.py`
  - `src/agents/coding_policies.py`
- Recommended borrowing method: adapt_flow
- Implementation sketch:
  - Keep existing approval state contracts, but consider interactive CLI modes that map to existing policy choices: confirm, preapproved/yolo, and manual command injection.
- Risks:
  - Reference interaction is terminal-local and not durable; copying it would weaken `my_agent`'s resume behavior.
- Evidence files:
  - `src/minisweagent/agents/interactive.py`
  - `tests/agents/test_interactive.py`

## BC-09: YAML Config Stack with Short-Name Factories

- Problem solved:
  - Makes CLI runs configurable without changing Python code, while still allowing full import paths for custom components.
- Reference implementation:
  - Files:
    - `src/minisweagent/config/__init__.py`
    - `src/minisweagent/utils/serialize.py`
    - `src/minisweagent/agents/__init__.py`
    - `src/minisweagent/models/__init__.py`
    - `src/minisweagent/environments/__init__.py`
    - `src/minisweagent/run/mini.py`
  - Classes / functions:
    - `get_config_from_spec()`
    - `_key_value_spec_to_nested_dict()`
    - `recursive_merge()`
    - `get_agent()`
    - `get_model()`
    - `get_environment()`
- Execution flow:
  - CLI collects config specs, loads YAML or key-value overrides, recursively merges them, then instantiates model, environment, and agent through short-name/import-path factories.
- Value for `my_agent`:
  - Could simplify local experimentation with coding profiles and models.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/coding_agent.py`
  - `src/agents/coding_cli.py`
  - `src/agents/models.py`
  - `src/agents/environment.py`
- Recommended borrowing method: adapt_structure
- Implementation sketch:
  - If `my_agent` needs configurable profiles, borrow the layered config idea. Avoid broad plugin registries; keep mappings small and explicit.
- Risks:
  - Dynamic import factories can become an accidental plugin system. This should remain narrow and CLI-facing.
- Evidence files:
  - `src/minisweagent/config/__init__.py`
  - `src/minisweagent/run/mini.py`
  - `src/minisweagent/agents/__init__.py`
  - `src/minisweagent/models/__init__.py`
  - `src/minisweagent/environments/__init__.py`

## BC-10: Deterministic Model Adapters for Loop Tests

- Problem solved:
  - Tests agent loop behavior without live LLM calls across text, tool-call, and Responses-style formats.
- Reference implementation:
  - Files:
    - `src/minisweagent/models/test_models.py`
    - `tests/models/test_test_models.py`
    - `tests/agents/test_default.py`
  - Classes / functions:
    - `DeterministicModel`
    - `DeterministicToolcallModel`
    - `DeterministicResponseAPIToolcallModel`
    - `make_output()`
    - `make_toolcall_output()`
    - `make_response_api_output()`
- Execution flow:
  - Test model returns predefined messages in sequence, increments a cursor, records synthetic cost, and uses the same observation formatting path as runtime adapters.
- Value for `my_agent`:
  - High test value for coding-loop behavior, malformed tool calls, approval pauses, verification observations, and final output without API calls.
- Possible mapping to `my_agent` files / modules:
  - `tests/`
  - `src/agents/models.py`
  - Existing fake model helpers if present
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Add or refine fake model helpers that emit normalized `ModelResponse` / `ToolCall` objects rather than copying mini-swe-agent's dict messages.
- Risks:
  - If fake models use a different contract than production adapters, tests may pass while real model integration breaks.
- Evidence files:
  - `src/minisweagent/models/test_models.py`
  - `tests/models/test_test_models.py`

## BC-11: Cost, Step, and Wall-Time Limits in the Loop

- Problem solved:
  - Prevents unbounded agent loops and provides predictable stopping reasons.
- Reference implementation:
  - Files:
    - `src/minisweagent/agents/default.py`
    - `src/minisweagent/models/__init__.py`
    - `src/minisweagent/exceptions.py`
  - Classes / functions:
    - `AgentConfig`
    - `DefaultAgent.query()`
    - `GlobalModelStats`
    - `LimitsExceeded`
    - `TimeExceeded`
- Execution flow:
  - Before each model query, the agent checks local step/cost/wall-time limits; model adapters add cost to a global stats tracker.
- Value for `my_agent`:
  - `my_agent` already has max step/tool limits. The cost and wall-time aspects may be useful if not already enforced consistently.
- Possible mapping to `my_agent` files / modules:
  - `src/agents/run_loop.py`
  - `src/agents/model_turn.py`
  - `src/agents/models.py`
  - `src/agents/coding_cli.py`
- Recommended borrowing method: adapt_data_model
- Implementation sketch:
  - Preserve existing limit handling, but consider explicit wall-time and cumulative model-cost fields in run state/result metadata.
- Risks:
  - Cost calculation can be provider-specific and unstable; avoid making correctness depend on exact cost availability.
- Evidence files:
  - `src/minisweagent/agents/default.py`
  - `src/minisweagent/models/__init__.py`
  - `src/minisweagent/exceptions.py`

## 5. Designs Not Suitable for Current Borrowing

| Design | Why Not Suitable Now | Future Condition | Evidence |
|---|---|---|---|
| Bash-only tool surface as the entire tool system | `my_agent` already has typed workspace, shell, test, and patch tools with policies and approvals. Replacing them would be a regression. | Could be offered as a restricted profile or compatibility mode. | `src/minisweagent/models/utils/actions_toolcall.py`, `src/minisweagent/config/mini.yaml` |
| Shell-based file edits as primary edit flow | `my_agent` has a structured patch tool with workspace safety and dry-run validation. | Useful only as a fallback when a task explicitly requires arbitrary shell editing. | `src/minisweagent/config/mini.yaml`, `src/minisweagent/environments/local.py` |
| Sentinel command as the only final-answer mechanism | `my_agent` already has `final_answer`, final output handling, and optional verification. | Could be used inside a sandboxed benchmark profile where the environment must produce a submission string. | `src/minisweagent/environments/local.py`, `src/minisweagent/environments/docker.py` |
| Full provider adapter set | The reference supports many provider paths; copying all would add maintenance surface outside the current task. | Add providers only when there is a concrete supported model/API need. | `src/minisweagent/models/` |
| Benchmark runners as production orchestration | SWE-bench and ProgramBench runners are evaluation utilities, not user-facing coding-agent architecture. | Useful after `my_agent` has a stable coding loop and needs benchmark evaluation. | `src/minisweagent/run/benchmarks/swebench.py`, `src/minisweagent/run/benchmarks/programbench.py` |
| ThreadPool benchmark parallelism as subagent design | It parallelizes independent benchmark instances, not subtasks within one agent run. | Could inform a future evaluation runner, not core subagent orchestration. | `src/minisweagent/run/benchmarks/swebench.py:245-270` |
| Textual trajectory inspector UI | The saved trajectory idea is useful, but a TUI is not necessary for the first upgrade step. | Add after evidence formats stabilize and users need interactive run browsing. | `src/minisweagent/run/utilities/inspector.py` |
| Dynamic import factories as broad plugin system | Project rules warn against speculative plugin systems. `my_agent` should keep mappings explicit and narrow. | Use only for CLI-configurable model/environment/profile selection with real call sites. | `src/minisweagent/agents/__init__.py`, `src/minisweagent/models/__init__.py`, `src/minisweagent/environments/__init__.py` |
| RAG / repo map / symbol index from reference | Not present in this reference. | Must be designed from `my_agent` needs or another reference, not mini-swe-agent. | `rg` under `src/minisweagent` found no retrieval/vector/embedding/repo-map/symbol-index implementation. |
| Agent-level subagents from reference | Not present in this reference. | Use another design source or a fresh design phase if subagents become a requirement. | `src/minisweagent/run/benchmarks/swebench.py`, `src/minisweagent/run/benchmarks/programbench.py` |

## 6. Candidate Mapping to `my_agent`

| BC | Possible `my_agent` Module / File | Integration Difficulty | Benefit | Risk |
|---|---|---|---|---|
| BC-01 | `src/agents/run_loop.py`, `src/agents/models.py`, `src/agents/environment.py` | Medium | Cleaner responsibility boundaries | May duplicate existing abstractions if treated as a rewrite |
| BC-02 | `src/agents/run_loop.py`, `src/agents/trajectory.py`, `src/agents/coding_cli.py` | Medium | More understandable coding-loop evidence | Could conflict with approval/resume flow if oversimplified |
| BC-03 | `src/agents/models.py`, `src/agents/model_turn.py`, `src/agents/contracts.py` | Medium | Provider parsing stays isolated | Loose dict contracts would weaken type clarity |
| BC-04 | `src/agents/turn_resolution.py`, `src/agents/tool_observations.py` | Low to Medium | Better model recovery from malformed actions | Could hide internal bugs if used too broadly |
| BC-05 | `src/agents/environment.py`, `src/agents/shell_tools.py` | Medium | Clear path to Docker/sandbox execution | Sandbox execution has security and lifecycle complexity |
| BC-06 | `src/agents/trajectory.py`, `src/agents/run_recording.py` | Low to Medium | Easier whole-run debugging | Multiple evidence formats may diverge |
| BC-07 | `src/agents/coding_agent.py`, `src/agents/tool_observations.py` | Low | Better prompt and bounded observations | Prompt rules alone are not enforcement |
| BC-08 | `src/agents/coding_cli.py`, `src/agents/run_resume.py` | Medium | Better local CLI ergonomics | Must preserve durable approval contracts |
| BC-09 | `src/agents/coding_cli.py`, `src/agents/coding_agent.py` | Medium | More flexible local runs | Dynamic factories can become overengineering |
| BC-10 | `tests/`, `src/agents/models.py` | Low | Faster deterministic loop tests | Fake model contract must match production |
| BC-11 | `src/agents/run_loop.py`, `src/agents/model_turn.py` | Low to Medium | Better bounded autonomy metadata | Provider cost estimates may be incomplete |

## 7. Questions for the Main Agent

1. Should `my_agent` keep structured patch editing as the primary edit path and treat shell edits as a secondary capability?
2. Should Docker/sandbox execution be part of the next upgrade, or should the immediate focus remain on local workspace safety and verification?
3. Is a whole-run trajectory summary desired in addition to the existing JSONL trajectory and tracing events?
4. Should malformed model tool-call handling become model-visible feedback, or should invalid tool calls fail the run more strictly?
5. Should CLI configuration stay profile-based, or should it accept layered YAML/key-value overrides similar to mini-swe-agent?
6. Which model APIs must be supported first: only OpenAI Responses, or multiple provider formats?
7. Should benchmark/evaluation runner design be deferred until the coding loop is more autonomous?

## 8. Summary Points for the Main Agent

1. The strongest reusable idea is the small Agent / Model / Environment boundary, not the exact implementation.
2. The reference intentionally avoids repo context, mention resolution, RAG, symbol indexing, structured patch tools, and subagents.
3. `my_agent` is already more capable in tools, approval state, verification, tracing, and workspace context.
4. Mini-swe-agent's bash-only tool contract is valuable as a simplification pattern but should not replace `my_agent`'s typed tools.
5. Model-visible format-error recovery is a compact design worth adapting for malformed model outputs at boundaries.
6. The local/Docker environment contract is a useful future path for sandbox execution.
7. The trajectory-first save format is a good complement to event tracing because it is easy to inspect as a whole run.
8. The prompt workflow and observation clipping in `mini.yaml` are useful concepts for coding-agent ergonomics.
9. Deterministic test models are directly useful for improving `my_agent` test coverage without live API calls.
10. Benchmark thread pools are evaluation infrastructure, not a subagent or parallel-task design for the core agent.
