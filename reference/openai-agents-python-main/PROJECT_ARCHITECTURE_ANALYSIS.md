# OpenAI Agents Python 项目架构分析

> 分析对象：`C:\Users\ch\Desktop\ai agent学习\reference\openai-agents-python-main`  
> 生成日期：2026-05-28  
> 范围：基于当前仓库真实源码、示例和配置分析；未修改任何运行代码。  
> CodeGraph：已对当前项目执行知识图谱索引，当前索引为 776 个文件、22544 个符号节点、62340 条关系边，数据库位于 `.codegraph/codegraph.db`。

## 1. 项目总体定位

### 1.1 这个项目是什么类型的 Agent

这是一个 Python 版 **OpenAI Agents SDK**，不是单一业务 Agent，也不是专门的 Coding Agent。它提供的是通用 Agent 运行时框架，核心入口在 `src/agents/run.py`，核心实体在 `src/agents/agent.py`，模型适配层在 `src/agents/models/`，工具体系在 `src/agents/tool.py` 和 `src/agents/run_internal/tool_execution.py`。

项目支持的 Agent 类型包括：

- 普通 LLM Agent：`src/agents/agent.py` 中的 `Agent`。
- 多 Agent 协作：`src/agents/handoffs/__init__.py` 中的 `Handoff` / `handoff()`，以及 `Agent.as_tool()`。
- 可执行工具 Agent：通过 `FunctionTool`、`ShellTool`、`ApplyPatchTool`、`ComputerTool`、`CodeInterpreterTool` 等工具扩展。
- Sandbox Agent：`src/agents/sandbox/sandbox_agent.py` 中的 `SandboxAgent`，用于把 Agent 和隔离执行环境、文件系统、shell、patch 工具绑定。
- Realtime / Voice Agent：`src/agents/realtime/` 和 `src/agents/voice/`，面向实时音频/语音交互。
- 实验性 Codex 工具：`src/agents/extensions/experimental/codex/`，把 Codex CLI 任务包装成 Agent tool。

### 1.2 主要解决什么问题

这个项目解决的是“把 LLM 调用、工具调用、多 Agent 切换、结构化输出、记忆、追踪、审批恢复、sandbox 执行”组合成一个稳定运行时的问题。

它不是只封装一次模型调用，而是提供完整 loop：

1. 组织输入、系统指令、prompt、session 历史。
2. 调用模型。
3. 解析模型输出中的 message、tool call、handoff call、hosted tool call、approval request 等。
4. 执行本地工具或接收 hosted tool 结果。
5. 根据结果继续下一轮、切换 Agent、返回 final output，或因人工审批中断。
6. 持久化 session、RunState、trace、sandbox resume state。

### 1.3 核心能力

| 能力 | 关键实现 |
| --- | --- |
| Agent 定义 | `Agent` / `AgentBase` in `src/agents/agent.py` |
| 主运行循环 | `Runner` / `AgentRunner` in `src/agents/run.py`，`run_single_turn()` in `src/agents/run_internal/run_loop.py` |
| LLM 适配 | `Model` / `ModelProvider` in `src/agents/models/interface.py`，`OpenAIResponsesModel` in `src/agents/models/openai_responses.py` |
| 工具系统 | `FunctionTool` / hosted tools / shell / patch / computer tools in `src/agents/tool.py` |
| 工具执行调度 | `process_model_response()` / `execute_tools_and_side_effects()` in `src/agents/run_internal/turn_resolution.py` |
| 多 Agent | `Handoff` / `handoff()` in `src/agents/handoffs/__init__.py`，`Agent.as_tool()` in `src/agents/agent.py` |
| 记忆与会话 | `Session` protocol in `src/agents/memory/session.py`，`SQLiteSession` in `src/agents/memory/sqlite_session.py` |
| 持久化恢复 | `RunState` in `src/agents/run_state.py` |
| Guardrail | `InputGuardrail` / `OutputGuardrail` in `src/agents/guardrail.py`，tool guardrails in `src/agents/tool_guardrails.py` |
| Tracing / hooks | `RunHooksBase` / `AgentHooksBase` in `src/agents/lifecycle.py`，`Trace` / `Span` in `src/agents/tracing/` |
| RAG / retrieval | hosted `FileSearchTool` in `src/agents/tool.py`，示例在 `examples/tools/file_search.py` |
| Code execution / sandbox | `ShellTool` / `ApplyPatchTool` / `CodeInterpreterTool` in `src/agents/tool.py`，`SandboxAgent` in `src/agents/sandbox/` |

## 2. 项目目录结构

### 2.1 根目录

| 路径 | 职责 |
| --- | --- |
| `src/agents/` | SDK 主源码，包含 Agent、Runner、模型、工具、memory、sandbox、tracing 等核心运行时。 |
| `tests/` | 单元测试和集成测试，覆盖 run loop、tools、handoff、memory、MCP、sandbox、realtime 等。 |
| `examples/` | SDK 使用示例，包括 basic、handoffs、mcp、memory、sandbox、tools、voice、realtime 等。 |
| `docs/` | MkDocs 文档源文件。注意 `docs/ja`、`docs/ko`、`docs/zh` 是翻译目录。 |
| `.agents/skills/` | 仓库内 Codex 技能说明，例如验证、发布审查、文档同步等。 |
| `.github/` | CI、issue/PR 模板、GitHub 工作流。 |
| `pyproject.toml` | 包元数据、依赖、ruff、mypy、pytest 配置。项目名为 `openai-agents`，当前版本 `0.14.6`。 |
| `Makefile` | 常用开发命令：format、lint、typecheck、tests、docs 等。 |
| `mkdocs.yml` | 文档站点配置。 |
| `README.md` | 项目简介和快速开始。 |
| `AGENTS.md` | 给 Codex/贡献者的仓库工作规则。 |

### 2.2 `src/agents/` 主要结构

| 路径 | 职责 | 关键文件 / 类 |
| --- | --- | --- |
| `src/agents/__init__.py` | SDK 公共导出入口。 | 导出 `Agent`、`Runner`、`FunctionTool`、`RunState`、`ModelSettings` 等。 |
| `src/agents/agent.py` | Agent 数据模型、工具聚合、handoff、agent-as-tool。 | `AgentBase`、`Agent`、`Agent.as_tool()`、`Agent.get_system_prompt()` |
| `src/agents/run.py` | 用户调用入口和顶层运行控制。 | `Runner`、`AgentRunner` |
| `src/agents/run_internal/` | 运行 loop 的内部模块拆分。 | `run_loop.py`、`turn_resolution.py`、`tool_execution.py`、`session_persistence.py` |
| `src/agents/models/` | 模型抽象和 OpenAI / 多 provider 适配。 | `Model`、`OpenAIProvider`、`OpenAIResponsesModel`、`OpenAIChatCompletionsModel`、`MultiProvider` |
| `src/agents/tool.py` | 工具类型定义和 `@function_tool` 装饰器。 | `FunctionTool`、`FileSearchTool`、`ShellTool`、`ApplyPatchTool`、`ToolSearchTool` |
| `src/agents/handoffs/` | 多 Agent 交接。 | `Handoff`、`handoff()`、`HandoffInputData`、`nest_handoff_history()` |
| `src/agents/memory/` | 会话记忆协议和内置实现。 | `Session`、`SessionABC`、`SQLiteSession`、`OpenAIResponsesCompactionAwareSession` |
| `src/agents/mcp/` | MCP server 接入和 MCP tools 转换。 | `MCPServer`、`MCPServerStdio`、`MCPServerSse`、`MCPUtil`、`MCPServerManager` |
| `src/agents/sandbox/` | Sandbox Agent、隔离会话、manifest、capability、shell/patch/file tools。 | `SandboxAgent`、`SandboxRuntime`、`BaseSandboxSession`、`Manifest`、`Capability` |
| `src/agents/tracing/` | Trace / span / exporter / processor。 | `Trace`、`Span`、`DefaultTraceProvider`、`BackendSpanExporter` |
| `src/agents/realtime/` | Realtime Agent。 | Realtime session、events、model adapters。 |
| `src/agents/voice/` | Voice pipeline。 | speech、transcription、workflow helpers。 |
| `src/agents/extensions/` | 可选扩展，如 memory backend、LiteLLM、AnyLLM、experimental Codex。 | `extensions/memory/*`、`extensions/experimental/codex/*` |

### 2.3 关键入口文件

| 入口 | 文件 | 用途 |
| --- | --- | --- |
| SDK import 入口 | `src/agents/__init__.py` | 用户 `from agents import Agent, Runner`。 |
| Agent 定义入口 | `src/agents/agent.py` | 定义 Agent 的 instructions、tools、handoffs、guardrails、output type。 |
| 执行入口 | `src/agents/run.py` | `Runner.run()`、`Runner.run_sync()`、`Runner.run_streamed()`。 |
| 单 turn 执行 | `src/agents/run_internal/run_loop.py` | 准备 prompt/input，调用 LLM，进入 response 处理。 |
| 模型输出解析 | `src/agents/run_internal/turn_resolution.py` | 把模型 response 解析成 RunItem 和 tool/handoff/final next step。 |
| 工具执行 | `src/agents/run_internal/tool_execution.py` | 批量执行 function tool、自定义 tool、shell、patch、computer action。 |
| 结果对象 | `src/agents/result.py` | `RunResult`、`RunResultStreaming`、`to_state()`。 |
| 可恢复状态 | `src/agents/run_state.py` | 可 JSON 序列化的 pause/resume 状态。 |

## 3. Agent 主运行流程

### 3.1 从用户输入到最终输出的完整链路

普通非 streaming 链路如下：

```text
用户调用 Runner.run(agent, input, ...)
  -> Runner.run()
     -> DEFAULT_AGENT_RUNNER.run()
        -> AgentRunner.run()
           -> 创建/恢复 RunContextWrapper
           -> 创建 RunConfig
           -> 如 input 是 RunState，则恢复 current_agent、generated_items、approvals、trace、sandbox
           -> 准备 session / server-managed conversation tracker / sandbox runtime
           -> 打开 trace 和 task span
           -> while 未 final 且未超过 max_turns:
                -> SandboxRuntime.prepare_agent()
                   -> 普通 Agent: public_agent == execution_agent
                   -> SandboxAgent: clone 成 execution_agent，注入 sandbox instructions 和 capability tools
                -> current_agent.get_all_tools()
                   -> 合并本地 tools、MCP tools、deferred tools
                -> initialize_computer_tools()
                -> 第一轮运行 input guardrails
                -> run_single_turn()
                   -> execution_agent.get_system_prompt()
                   -> execution_agent.get_prompt()
                   -> get_handoffs()
                   -> get_output_schema()
                   -> prepare_input_with_session() 或 OpenAIServerConversationTracker.prepare_input()
                   -> get_new_response()
                      -> maybe_filter_model_input()
                      -> get_model()
                      -> model.get_response(...)
                   -> get_single_step_result_from_response()
                      -> process_model_response()
                      -> execute_tools_and_side_effects()
                         -> _build_plan_for_fresh_turn()
                         -> _execute_tool_plan()
                         -> 生成 NextStepFinalOutput / NextStepHandoff / NextStepRunAgain / NextStepInterruption
                -> AgentRunner 根据 next_step 决定：
                   -> final: output guardrails -> hooks -> session save -> RunResult
                   -> handoff: 更新 current_agent 和 input，下一轮
                   -> run_again: 保存 tool output，下一轮
                   -> interruption: 构造 RunResult，用户可 to_state() 后 approve/reject 再 resume
```

### 3.2 LLM 调用在哪里发生

LLM 调用集中在 `src/agents/run_internal/run_loop.py`：

- 非流式：`get_new_response()` 调用 `model.get_response(...)`。
- 流式：`run_single_turn_streamed()` 通过 `stream_response_with_retry()` 调用 `model.stream_response(...)`。

模型对象由 `src/agents/run_internal/turn_preparation.py` 的 `get_model()` 决定：

1. `run_config.model` 如果是 `Model` 实例，直接使用。
2. `run_config.model` 如果是字符串，用 `run_config.model_provider.get_model()`。
3. 否则如果 `agent.model` 是 `Model` 实例，直接使用。
4. 否则用 `run_config.model_provider.get_model(agent.model)`。

默认 provider 是 `MultiProvider`，定义在 `src/agents/models/multi_provider.py`。OpenAI provider 在 `src/agents/models/openai_provider.py` 中根据配置选择：

- `OpenAIResponsesModel`：`src/agents/models/openai_responses.py`
- `OpenAIResponsesWSModel`：Responses websocket transport
- `OpenAIChatCompletionsModel`：`src/agents/models/openai_chatcompletions.py`

### 3.3 tool call / final answer / handoff / code execution 如何被调度

调度核心在 `src/agents/run_internal/turn_resolution.py`：

- `process_model_response()` 解析 `ModelResponse.output`，识别 message、function call、handoff call、computer call、custom tool call、shell call、apply_patch call、MCP approval、file search、web search、code interpreter、image generation 等。
- `execute_tools_and_side_effects()` 根据解析结果建立 `ToolExecutionPlan`，调用 `_execute_tool_plan()` 执行本地工具和副作用。
- 执行完后按优先级决定下一步：
  1. 有审批或中断：返回 `NextStepInterruption`。
  2. 有 handoff：返回 `NextStepHandoff`。
  3. tool-use behavior 要求工具输出作为最终结果：返回 `NextStepFinalOutput`。
  4. 有 final message 且符合 output schema：返回 `NextStepFinalOutput`。
  5. 否则返回 `NextStepRunAgain`，把工具结果回填给模型继续下一轮。

工具执行本身在 `src/agents/run_internal/tool_execution.py`：

- `execute_function_tool_calls()` 使用 `_FunctionToolBatchExecutor` 并发执行 function tools。
- `execute_custom_tool_calls()` 执行 freeform/custom tools。
- `execute_local_shell_calls()`、`execute_shell_calls()`、`execute_apply_patch_calls()` 分别处理 local shell、Responses shell、apply_patch。
- `execute_computer_actions()` 处理 computer action 并生成截图输出。

### 3.4 Streaming 路径

Streaming 使用相同的语义模型，但事件通过队列输出：

- 用户入口：`Runner.run_streamed()` in `src/agents/run.py`
- 结果对象：`RunResultStreaming` in `src/agents/result.py`
- 后台 loop：`start_streaming()` / `run_single_turn_streamed()` in `src/agents/run_internal/run_loop.py`
- stream event 类型：`src/agents/stream_events.py`

Streaming 中模型原始事件会以 `RawResponsesStreamEvent` 输出，RunItem 完成时以 `RunItemStreamEvent` 输出，handoff 时有 `AgentUpdatedStreamEvent`。

## 4. 核心模块分析

### 4.1 Agent / Runner / Loop

**职责**

`Agent` 是配置对象，`Runner` 是用户入口，`AgentRunner` 是执行器，`run_internal` 是实际 loop 拆分。

**关键类/函数**

- `AgentBase`：`src/agents/agent.py`
  - 字段：`name`、`handoff_description`、`tools`、`mcp_servers`、`mcp_config`。
  - 方法：`get_mcp_tools()`、`get_all_tools()`。
- `Agent`：`src/agents/agent.py`
  - 字段：`instructions`、`prompt`、`handoffs`、`model`、`model_settings`、`input_guardrails`、`output_guardrails`、`output_type`、`hooks`、`tool_use_behavior`、`reset_tool_choice`。
  - 方法：`clone()`、`as_tool()`、`get_system_prompt()`、`get_prompt()`。
- `Runner`：`src/agents/run.py`
  - 静态 facade：`run()`、`run_sync()`、`run_streamed()`。
- `AgentRunner`：`src/agents/run.py`
  - 实际运行器：处理 trace、session、RunState、sandbox、guardrail、turn loop。
- `run_single_turn()`：`src/agents/run_internal/run_loop.py`
  - 单轮非 streaming 执行。
- `get_new_response()`：`src/agents/run_internal/run_loop.py`
  - 模型调用封装。
- `process_model_response()` / `execute_tools_and_side_effects()`：`src/agents/run_internal/turn_resolution.py`
  - response 解析和 next step 决策。

**调用关系**

`Runner.run()` -> `AgentRunner.run()` -> `SandboxRuntime.prepare_agent()` -> `run_single_turn()` -> `get_new_response()` -> `model.get_response()` -> `get_single_step_result_from_response()` -> `process_model_response()` -> `execute_tools_and_side_effects()` -> `NextStep*`。

**可复用价值**

这是最值得学习的部分。它把 Agent loop 拆成“准备输入、调用模型、解析输出、执行工具、决定下一步”几个边界清晰的阶段。实现自己的 Coding Agent 时，可以直接借鉴 `NextStepFinalOutput` / `NextStepHandoff` / `NextStepRunAgain` / `NextStepInterruption` 这种状态机设计。

### 4.2 Model / LLM Adapter

**职责**

统一模型调用接口，把不同 provider 的输出转换成 SDK 内部通用的 `ModelResponse` 和 Responses-style item。

**关键类/函数**

- `Model`：`src/agents/models/interface.py`
  - 抽象方法：`get_response()`、`stream_response()`。
- `ModelProvider`：`src/agents/models/interface.py`
  - 抽象方法：`get_model()`。
- `ModelTracing`：`src/agents/models/interface.py`
  - 控制模型调用 trace 是否记录输入输出。
- `OpenAIProvider`：`src/agents/models/openai_provider.py`
  - 懒加载 `AsyncOpenAI`，选择 Responses / Chat Completions / websocket。
- `OpenAIResponsesModel`：`src/agents/models/openai_responses.py`
  - 调用 `client.responses.create(...)`，把 response output 转成 `ModelResponse`。
- `OpenAIChatCompletionsModel`：`src/agents/models/openai_chatcompletions.py`
  - 调用 Chat Completions，再通过 converter 转成 Responses-style output。
- `MultiProvider`：`src/agents/models/multi_provider.py`
  - 根据 `openai/`、`litellm/`、`any-llm/` 前缀路由。
- `get_response_with_retry()` / `stream_response_with_retry()`：`src/agents/run_internal/model_retry.py`
  - runner-managed retry，支持 stateful conversation rewind。

**调用关系**

`get_model()` in `turn_preparation.py` 选出 `Model`，`get_new_response()` 调用 `model.get_response()`；streaming 路径调用 `model.stream_response()`。

**可复用价值**

对自己的 Agent 项目，建议学习这个 adapter 边界：业务 loop 不直接依赖某个 LLM SDK，而是依赖 `Model` 抽象和统一 `ModelResponse`。这样后续切换 OpenAI、LiteLLM、OpenRouter、自建模型时不会污染运行 loop。

### 4.3 Tool / Function Tool

**职责**

把 Python 函数、本地工具、hosted tool、MCP tool、shell、patch、computer 等统一暴露给模型，并在模型请求后执行。

**关键类/函数**

- `FunctionTool`：`src/agents/tool.py`
  - 字段：`name`、`description`、`params_json_schema`、`on_invoke_tool`、`strict_json_schema`、`is_enabled`、`tool_input_guardrails`、`tool_output_guardrails`、`needs_approval`、`timeout_seconds`、`defer_loading`。
- `function_tool()`：`src/agents/tool.py`
  - 装饰器，从函数签名、docstring、Pydantic schema 自动生成 `FunctionTool`。
- `FuncSchema` / `function_schema()`：`src/agents/function_schema.py`
  - 解析函数签名，生成调用参数和 JSON schema。
- Hosted tools：`FileSearchTool`、`WebSearchTool`、`CodeInterpreterTool`、`HostedMCPTool`、`ImageGenerationTool` in `src/agents/tool.py`。
- Code/workspace tools：`ShellTool`、`LocalShellTool`、`ApplyPatchTool`、`ComputerTool` in `src/agents/tool.py`。
- Tool planning：`ToolExecutionPlan` in `src/agents/run_internal/tool_planning.py`。
- Tool execution：`execute_function_tool_calls()`、`execute_shell_calls()`、`execute_apply_patch_calls()` in `src/agents/run_internal/tool_execution.py`。

**调用关系**

`Agent.get_all_tools()` 聚合本地 tools 和 MCP tools；模型在 response 中发出 tool call；`process_model_response()` 把 tool call 放入 `ProcessedResponse` 的队列；`execute_tools_and_side_effects()` 构造 `ToolExecutionPlan`；`tool_execution.py` 具体执行，并生成 `ToolCallOutputItem` 回填给下一轮模型。

**可复用价值**

`FunctionTool` 是低迁移难度、高价值模块。自己的 Coding Agent 可优先参考：

- 从函数签名自动生成 schema。
- tool input/output guardrail。
- `needs_approval` 支持布尔或 callable。
- tool timeout 和错误格式化。
- 并发 tool batch 执行。

### 4.4 Memory / Session / Context

**职责**

Memory 这里主要指会话历史，不是完整语义记忆系统。运行上下文通过 `RunContextWrapper` 传给工具、guardrail、hook，不发送给 LLM。

**关键类/函数**

- `RunContextWrapper`：`src/agents/run_context.py`
  - 字段：`context`、`usage`、`turn_input`、`tool_input`、内部 `_approvals`。
  - 方法：`approve_tool()`、`reject_tool()`、`get_approval_status()`、`get_rejection_message()`、`_fork_with_tool_input()`。
- `ToolContext`：`src/agents/tool_context.py`
  - 继承 `RunContextWrapper`，增加 `tool_name`、`tool_call_id`、`tool_arguments`、`tool_namespace`、`agent`、`run_config`。
- `Session` protocol：`src/agents/memory/session.py`
  - `get_items()`、`add_items()`、`pop_item()`、`clear_session()`。
- `SQLiteSession`：`src/agents/memory/sqlite_session.py`
  - 内置 SQLite session，支持内存 DB 和文件 DB。
- `prepare_input_with_session()`：`src/agents/run_internal/session_persistence.py`
  - 合并 session history 和本轮新输入。
- `save_result_to_session()`：`src/agents/run_internal/session_persistence.py`
  - 把本轮输入和 RunItem 转成 model input item 后持久化。
- `OpenAIServerConversationTracker`：`src/agents/run_internal/oai_conversation.py`
  - 针对 server-managed conversation，只发送 delta，避免重复发送已确认 item。

**调用关系**

`AgentRunner.run()` 初始化 `RunContextWrapper` 和 session；每轮前 `prepare_input_with_session()` 合并历史；每轮后 `save_result_to_session()` 保存输入、message、tool output。若使用 `conversation_id` / `previous_response_id` / `auto_previous_response_id`，则改用 `OpenAIServerConversationTracker.prepare_input()` 做 delta 管理，并禁用普通 session 持久化路径。

**可复用价值**

对 Coding Agent，`RunContextWrapper` 的设计很适合借鉴：工具可以访问数据库连接、文件系统句柄、用户配置、usage、审批状态，但这些不会污染 LLM prompt。Session protocol 也适合作为最小记忆接口。

### 4.5 Prompt / Instruction

**职责**

支持静态 instructions、动态 instructions，以及 OpenAI Responses prompt template。

**关键类/函数**

- `Agent.instructions`：`src/agents/agent.py`
  - 可以是字符串，也可以是 callable：`(context, agent) -> str | None`。
- `Agent.get_system_prompt()`：`src/agents/agent.py`
  - 解析静态或动态 instructions。
- `Prompt` / `DynamicPromptFunction`：`src/agents/prompts.py`
  - 对应 OpenAI Responses API 的 prompt object：`id`、`version`、`variables`。
- `PromptUtil.to_model_input()`：`src/agents/prompts.py`
  - 把 prompt dict/callable 转成 `ResponsePromptParam`。
- `build_sandbox_instructions()`：`src/agents/sandbox/runtime_agent_preparation.py`
  - SandboxAgent 会把 base prompt、用户 instructions、capability instructions、filesystem tree 合并成最终 instructions。
- `prompt_with_handoff_instructions()`：`src/agents/extensions/handoff_prompt.py`
  - 给 handoff 场景追加推荐 prompt 前缀。

**调用关系**

`run_single_turn()` 调用 `execution_agent.get_system_prompt()` 和 `execution_agent.get_prompt()`；随后 `get_new_response()` 把 `system_instructions` 和 `prompt` 一起传入 `model.get_response()`。

**可复用价值**

建议借鉴“instructions 和 prompt template 分离”的设计：system instructions 是运行时策略，prompt template 是平台托管 prompt 引用。对 Coding Agent，还应参考 `build_sandbox_instructions()`，把文件系统能力、shell 使用规则、patch 规则按 capability 拼接，而不是写死在一个巨大 prompt 中。

### 4.6 Multi-agent / Handoff

**职责**

支持一个 Agent 把任务交给另一个 Agent，或把 Agent 包装成工具由当前 Agent 调用。

**关键类/函数**

- `Handoff`：`src/agents/handoffs/__init__.py`
  - 字段：`tool_name`、`tool_description`、`input_json_schema`、`on_invoke_handoff`、`agent_name`、`input_filter`、`nest_handoff_history`、`is_enabled`。
- `handoff()`：`src/agents/handoffs/__init__.py`
  - 把目标 Agent 包装成 handoff tool，默认 tool name 为 `transfer_to_<agent_name>`。
- `HandoffInputData`：`src/agents/handoffs/__init__.py`
  - 包含 `input_history`、`pre_handoff_items`、`new_items`、`run_context`、`input_items`。
- `nest_handoff_history()`：`src/agents/handoffs/history.py`
  - 把交接前 transcript 折叠成 `<CONVERSATION HISTORY>` assistant message。
- `remove_all_tools()`：`src/agents/extensions/handoff_filters.py`
  - 从 handoff input 中移除工具相关 items。
- `Agent.as_tool()`：`src/agents/agent.py`
  - 把 Agent 暴露成 `FunctionTool`，区别是不会改变当前 running agent。

**调用关系**

`get_handoffs()` in `src/agents/run_internal/turn_preparation.py` 把 `Agent.handoffs` 标准化为 `Handoff` 列表；`process_model_response()` 把 handoff function call 识别为 `ToolRunHandoff`；`execute_tools_and_side_effects()` 返回 `NextStepHandoff`；`AgentRunner.run()` 更新 `current_agent` 后继续 loop。

**可复用价值**

Handoff 很适合客服/任务路由/专家 Agent，但对从零做 Coding Agent 不一定第一阶段就需要。更建议先实现 Agent-as-tool 或子任务工具，等单 Agent loop 稳定后再引入 full handoff。

### 4.7 Guardrail / Validation

**职责**

提供输入、输出和工具级别的安全/质量检查，支持 tripwire 中断运行。

**关键类/函数**

- `GuardrailFunctionOutput`：`src/agents/guardrail.py`
  - 字段：`output_info`、`tripwire_triggered`。
- `InputGuardrail` / `input_guardrail()`：`src/agents/guardrail.py`
  - 对初始用户输入执行检查，可并行或串行。
- `OutputGuardrail` / `output_guardrail()`：`src/agents/guardrail.py`
  - 对 final output 执行检查。
- `run_input_guardrails()` / `run_output_guardrails()`：`src/agents/run_internal/guardrails.py`
  - 并发运行 guardrails，并在 tripwire 时抛出 `InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered`。
- `ToolInputGuardrail` / `ToolOutputGuardrail`：`src/agents/tool_guardrails.py`
  - 工具级输入输出检查。

**调用关系**

`AgentRunner.run()` 在第一轮模型调用前运行 input guardrails；final output 生成后运行 output guardrails；工具执行路径在 `tool_execution.py` 中围绕 function tool 调用执行 tool input/output guardrails。

**可复用价值**

对于 Coding Agent，建议优先借鉴 tool guardrails 和 approval，而不是复杂内容安全 guardrails。例如：删除文件、运行危险命令、访问密钥、修改依赖文件前触发 approval。

### 4.8 Tracing / Logging / Hook

**职责**

Tracing 记录 run、agent、turn、LLM、tool、handoff、guardrail 等 span；hooks 让用户在生命周期事件中插入逻辑。

**关键类/函数**

- `RunHooksBase` / `AgentHooksBase`：`src/agents/lifecycle.py`
  - hooks：`on_llm_start`、`on_llm_end`、`on_agent_start`、`on_agent_end`、`on_handoff`、`on_tool_start`、`on_tool_end`。
- `Trace` / `TraceState`：`src/agents/tracing/traces.py`
  - `TraceState` 可序列化 trace 元数据，用于 RunState resume。
- `Span` / `SpanImpl` / `SpanError`：`src/agents/tracing/spans.py`
  - span 抽象与实现。
- Span data：`src/agents/tracing/span_data.py`
  - `AgentSpanData`、`TaskSpanData`、`TurnSpanData`、`FunctionSpanData`、`GenerationSpanData`、`ResponseSpanData`、`HandoffSpanData`、`GuardrailSpanData` 等。
- `trace()`、`agent_span()`、`function_span()`、`guardrail_span()`：`src/agents/tracing/create.py`
- `DefaultTraceProvider`：`src/agents/tracing/provider.py`
- `BackendSpanExporter`：`src/agents/tracing/processors.py`
  - 默认向 OpenAI traces ingest endpoint 上报。

**调用关系**

`AgentRunner.run()` 创建 trace/task span/agent span/turn span；`run_single_turn()` 在 agent start、LLM start/end、tool start/end、handoff、final output 等位置触发 hooks 和 spans。错误通过 `SpanError` 附加到当前 span。

**可复用价值**

自己的 Coding Agent 应优先实现轻量 tracing：run id、turn id、model input hash、tool call、cwd、exit code、diff summary、approval decision。完整 exporter 和 OpenAI tracing API 可以后置。

### 4.9 RAG / Retrieval

**发现情况**

发现 hosted retrieval 能力，但未发现一个独立的本地 RAG pipeline（例如本地文档 chunk、embedding、向量库、rerank、context packer 一整套模块）。

已发现能力：

- `FileSearchTool`：`src/agents/tool.py`
  - Hosted OpenAI vector store retrieval tool，字段包括 `vector_store_ids`、`max_num_results`、`include_search_results`、`ranking_options`、`filters`。
- `WebSearchTool`：`src/agents/tool.py`
  - Hosted web search。
- 示例：`examples/tools/file_search.py`
  - 创建 OpenAI vector store、上传文件、给 Agent 配置 `FileSearchTool`。
- MCP resources：
  - `MCPServer.list_resources()`、`list_resource_templates()`、`read_resource()` in `src/agents/mcp/server.py`。
- Tool search：
  - `ToolSearchTool` in `src/agents/tool.py` 是“工具发现/延迟加载”，不是文档 RAG。

**未发现**

- 未发现本地 embedding 管道。
- 未发现本地 vector DB retriever 抽象。
- 未发现通用 RAG context compression/rerank 模块。

**可复用价值**

如果你的 Coding Agent 需要代码检索，不建议照搬 `FileSearchTool` 作为核心，因为它依赖 OpenAI vector store hosted tool。更适合借鉴的是“把 retrieval 作为 Tool 暴露给模型”的接口形态。代码库阅读可另行实现本地 symbol graph、ripgrep、AST index、embedding retriever，然后包装成 `FunctionTool` 风格。

### 4.10 Code execution / Sandbox

**职责**

项目同时提供 hosted code execution、本地工具执行、sandbox session 执行三类能力。

**关键类/函数**

通用工具层：

- `CodeInterpreterTool`：`src/agents/tool.py`
  - Hosted OpenAI code interpreter。
- `ShellTool`：`src/agents/tool.py`
  - 支持 hosted container shell 或本地 executor。
- `LocalShellTool`：`src/agents/tool.py`
  - legacy local shell integration。
- `ApplyPatchTool`：`src/agents/tool.py`
  - 本地 patch 工具，需要 `ApplyPatchEditor`。
- `ComputerTool`：`src/agents/tool.py`
  - local computer/browser harness。

Sandbox 层：

- `SandboxAgent`：`src/agents/sandbox/sandbox_agent.py`
  - `Agent` 子类，增加 `default_manifest`、`base_instructions`、`capabilities`、`run_as`。
- `SandboxRuntime`：`src/agents/sandbox/runtime.py`
  - 在 `AgentRunner` 每轮前准备 sandbox session 和 execution agent。
- `SandboxRuntimeSessionManager`：`src/agents/sandbox/runtime_session_manager.py`
  - 创建、恢复、清理 sandbox session，序列化 resume state。
- `BaseSandboxSession`：`src/agents/sandbox/session/base_sandbox_session.py`
  - 抽象执行环境，提供 `start()`、`stop()`、`shutdown()`、`exec()`、`read()`、`write()`、PTY 等。
- `BaseSandboxClient`：`src/agents/sandbox/session/sandbox_client.py`
  - sandbox provider 抽象：`create()`、`resume()`、`delete()`、session state 序列化。
- `Manifest`：`src/agents/sandbox/manifest.py`
  - 描述 workspace root、entries、environment、users、groups、mounts、path grants。
- `Capability`：`src/agents/sandbox/capabilities/capability.py`
  - capability 可注入 tools、instructions、manifest 变换、sampling params、context 变换。
- 默认 capabilities：`src/agents/sandbox/capabilities/capabilities.py`
  - `Filesystem()`、`Shell()`、`Compaction()`。
- `ExecCommandTool` / `WriteStdinTool`：`src/agents/sandbox/capabilities/tools/shell_tool.py`
  - sandbox shell 工具。
- `SandboxApplyPatchTool`：`src/agents/sandbox/capabilities/tools/apply_patch_tool.py`
  - sandbox 内 patch 工具，支持 freeform grammar 和 JSON 两种输入。

实验性 Codex：

- `codex_tool()`：`src/agents/extensions/experimental/codex/codex_tool.py`
  - 把 Codex CLI 包成 `FunctionTool`。
- `Thread.run_streamed()`：`src/agents/extensions/experimental/codex/thread.py`
  - 调用 `CodexExec.run(CodexExecArgs(...))`，解析 Codex stream event。

**调用关系**

`AgentRunner.run()` 构造 `SandboxRuntime`；每轮调用 `SandboxRuntime.prepare_agent()`。如果当前 Agent 是 `SandboxAgent`，则：

1. `SandboxRuntimeSessionManager.ensure_session()` 创建或恢复 session。
2. clone capabilities 并 bind 到 session。
3. `prepare_sandbox_agent()` clone 原 Agent，追加 sandbox instructions 和 capability tools。
4. loop 用 execution agent 参与模型调用和工具执行，但 public agent identity 保留给结果/用户。

**可复用价值**

这是对 Coding Agent 最有参考价值的部分之一。尤其值得学习：

- 用 `Manifest` 描述工作区，而不是让工具任意访问文件系统。
- 用 `Capability` 组合 shell、filesystem、memory、compaction。
- 用 `BaseSandboxSession` 抽象 Docker/local/remote provider。
- 用 `SandboxRuntime` 在运行时 clone agent 并注入工具，避免污染用户定义的 public agent。
- 用 `RunState._sandbox` 持久化 sandbox resume state。

### 4.11 Config / Schema / Type system

**职责**

用 dataclass、TypedDict、Protocol、Pydantic schema 管理公共 API、模型调用参数、工具 schema、输出 schema、RunState schema。

**关键类/函数**

- `RunConfig`：`src/agents/run_config.py`
  - 全局运行配置：model、model_provider、model_settings、handoff filters、guardrails、tracing、session、tool_error_formatter、sandbox 等。
- `SandboxRunConfig` / `SandboxConcurrencyLimits`：`src/agents/run_config.py`
  - sandbox client、session、manifest、snapshot、并发限制。
- `ModelSettings`：`src/agents/model_settings.py`
  - temperature、top_p、tool_choice、parallel_tool_calls、reasoning、verbosity、metadata、store、prompt_cache_retention、response_include、retry、extra_args 等。
- `AgentOutputSchema`：`src/agents/agent_output.py`
  - 用 Pydantic `TypeAdapter` 生成/验证结构化输出 schema。
- `FuncSchema`：`src/agents/function_schema.py`
  - 函数工具参数 schema。
- `RunItem` union：`src/agents/items.py`
  - message、tool call、handoff、reasoning、MCP、approval、compaction 等运行项。
- `ModelResponse`：`src/agents/items.py`
  - 模型输出统一数据结构。
- `RunState`：`src/agents/run_state.py`
  - 当前 schema version 为 `1.9`，`SCHEMA_VERSION_SUMMARIES` 记录每个版本含义。
- `ToolRun*` / `ProcessedResponse` / `SingleStepResult` / `NextStep*`：`src/agents/run_internal/run_steps.py`
  - run loop 内部类型系统。

**调用关系**

`RunConfig` 和 `ModelSettings` 在 `AgentRunner.run()` 初始化；`AgentOutputSchema` 在 `get_output_schema()` 创建并传给模型 adapter；`FuncSchema` 在 `function_tool()` 创建工具时使用；`RunItem` 和 `ModelResponse` 贯穿模型输出、工具执行、session 持久化和 RunState 序列化。

**可复用价值**

自己的 Coding Agent 应强烈借鉴“运行时 item 类型系统”。不要只维护一串聊天消息，应显式区分 user input、assistant message、tool call、tool output、approval、handoff、reasoning、patch output。这样恢复、审计、UI 展示、测试都会更稳定。

## 5. 关键数据结构

| 类型 | 文件路径 | 作用 |
| --- | --- | --- |
| `AgentBase` | `src/agents/agent.py` | Agent 基类，管理 name、tools、MCP servers 和工具聚合。 |
| `Agent` | `src/agents/agent.py` | 用户定义的核心 Agent 配置对象。 |
| `AgentToolStreamEvent` | `src/agents/agent.py` | `Agent.as_tool()` streaming callback 事件。 |
| `RunConfig` | `src/agents/run_config.py` | 单次 run 的全局配置。 |
| `ModelInputData` | `src/agents/run_config.py` | `call_model_input_filter` 修改模型输入时的容器。 |
| `SandboxRunConfig` | `src/agents/run_config.py` | sandbox client/session/manifest/snapshot 配置。 |
| `RunContextWrapper` | `src/agents/run_context.py` | 工具、guardrail、hook 可访问的运行上下文；不传给 LLM。 |
| `AgentHookContext` | `src/agents/run_context.py` | Agent hooks 使用的上下文。 |
| `ToolContext` | `src/agents/tool_context.py` | 单个工具调用上下文，包含 tool call id/name/arguments。 |
| `ModelSettings` | `src/agents/model_settings.py` | 模型采样、工具选择、reasoning、retry、extra args 配置。 |
| `MCPToolChoice` | `src/agents/model_settings.py` | MCP tool choice 的结构化表示。 |
| `Model` | `src/agents/models/interface.py` | LLM adapter 抽象。 |
| `ModelProvider` | `src/agents/models/interface.py` | model name 到 `Model` 的解析器。 |
| `ModelResponse` | `src/agents/items.py` | SDK 内部统一模型响应，包含 output、usage、response_id、request_id。 |
| `RunItemBase` | `src/agents/items.py` | 所有运行项基类，保存 originating agent 和 raw item。 |
| `MessageOutputItem` | `src/agents/items.py` | assistant message。 |
| `ToolCallItem` | `src/agents/items.py` | function/computer/file/web/MCP/code/shell 等 tool call。 |
| `ToolCallOutputItem` | `src/agents/items.py` | tool output，下一轮会转换为 input item。 |
| `HandoffCallItem` | `src/agents/items.py` | handoff tool call。 |
| `HandoffOutputItem` | `src/agents/items.py` | handoff 输出，记录 source/target agent。 |
| `ReasoningItem` | `src/agents/items.py` | Responses reasoning item。 |
| `ToolApprovalItem` | `src/agents/items.py` | 等待人工审批的工具调用。 |
| `RunResult` | `src/agents/result.py` | 非 streaming 运行结果，支持 `to_state()`。 |
| `RunResultStreaming` | `src/agents/result.py` | streaming 运行结果，持有事件队列、后台 task、cancel 逻辑。 |
| `RunState` | `src/agents/run_state.py` | 可序列化恢复状态，保存 agent、turn、items、responses、approvals、trace、sandbox。 |
| `ToolRunFunction` | `src/agents/run_internal/run_steps.py` | 待执行 function tool call。 |
| `ToolRunHandoff` | `src/agents/run_internal/run_steps.py` | 待执行 handoff call。 |
| `ToolRunShellCall` / `ToolRunApplyPatchCall` | `src/agents/run_internal/run_steps.py` | 待执行 shell / patch call。 |
| `ProcessedResponse` | `src/agents/run_internal/run_steps.py` | 模型 response 解析后的中间结构，含 RunItems 和 tool queues。 |
| `SingleStepResult` | `src/agents/run_internal/run_steps.py` | 单轮执行结果，包含 original input、model response、new items、next step。 |
| `NextStepFinalOutput` | `src/agents/run_internal/run_steps.py` | loop 终止并返回 final output。 |
| `NextStepHandoff` | `src/agents/run_internal/run_steps.py` | loop 切换到另一个 Agent。 |
| `NextStepRunAgain` | `src/agents/run_internal/run_steps.py` | 执行工具后继续下一轮模型调用。 |
| `NextStepInterruption` | `src/agents/run_internal/run_steps.py` | 需要审批/人工介入，中断并可恢复。 |
| `FunctionTool` | `src/agents/tool.py` | Python 函数工具的标准结构。 |
| `FunctionToolResult` | `src/agents/tool.py` | function tool 执行结果。 |
| `ToolOrigin` / `ToolOriginType` | `src/agents/tool.py` | 工具来源元数据，如 function、MCP。 |
| `Handoff` | `src/agents/handoffs/__init__.py` | 多 Agent 交接配置。 |
| `HandoffInputData` | `src/agents/handoffs/__init__.py` | handoff input filter 的输入输出结构。 |
| `GuardrailFunctionOutput` | `src/agents/guardrail.py` | guardrail 输出和 tripwire 标记。 |
| `InputGuardrailResult` / `OutputGuardrailResult` | `src/agents/guardrail.py` | guardrail 执行结果。 |
| `ToolGuardrailFunctionOutput` | `src/agents/tool_guardrails.py` | 工具级 guardrail 输出。 |
| `Session` | `src/agents/memory/session.py` | 会话历史协议。 |
| `SQLiteSession` | `src/agents/memory/sqlite_session.py` | 内置 SQLite 会话存储。 |
| `OpenAIServerConversationTracker` | `src/agents/run_internal/oai_conversation.py` | server-managed conversation delta tracking。 |
| `Trace` / `TraceState` | `src/agents/tracing/traces.py` | workflow trace 和可序列化 trace metadata。 |
| `Span` / `SpanError` | `src/agents/tracing/spans.py` | trace span 和错误信息。 |
| `SandboxAgent` | `src/agents/sandbox/sandbox_agent.py` | 带 sandbox 配置的 Agent。 |
| `SandboxRuntime` | `src/agents/sandbox/runtime.py` | Runner 中的 sandbox 准备/清理层。 |
| `SandboxRuntimeSessionManager` | `src/agents/sandbox/runtime_session_manager.py` | sandbox session 生命周期和 resume state。 |
| `Manifest` | `src/agents/sandbox/manifest.py` | sandbox workspace 描述。 |
| `Capability` | `src/agents/sandbox/capabilities/capability.py` | sandbox 能力插件抽象。 |
| `BaseSandboxSession` | `src/agents/sandbox/session/base_sandbox_session.py` | sandbox 执行环境抽象。 |
| `BaseSandboxClient` | `src/agents/sandbox/session/sandbox_client.py` | sandbox provider 抽象。 |
| `ExecCommandTool` / `WriteStdinTool` | `src/agents/sandbox/capabilities/tools/shell_tool.py` | sandbox shell 工具。 |
| `SandboxApplyPatchTool` | `src/agents/sandbox/capabilities/tools/apply_patch_tool.py` | sandbox patch 工具。 |

## 6. 可参考模块清单

| 模块名称 | 文件路径 | 解决的问题 | 设计亮点 | 适合我自己的 Agent 借鉴的地方 | 迁移难度 |
| --- | --- | --- | --- | --- | --- |
| Runner 状态机 | `src/agents/run.py`、`src/agents/run_internal/run_loop.py` | 把用户输入、多轮模型调用、工具执行、最终输出串起来。 | facade 和实际 runner 分离，单 turn 逻辑下沉到 `run_internal`。 | Coding Agent 的主 loop 可以直接参考“turn -> model -> parse -> tool -> next step”。 | 中 |
| NextStep 类型系统 | `src/agents/run_internal/run_steps.py` | 明确每轮之后该 final、handoff、继续、还是中断。 | `NextStepFinalOutput` / `NextStepRunAgain` / `NextStepInterruption` 可读性强。 | 避免用布尔值/字符串拼凑运行状态。 | 低 |
| 模型输出解析 | `src/agents/run_internal/turn_resolution.py` | 把复杂 Responses output 分类成 message、tool、handoff、approval。 | 先解析成 `ProcessedResponse`，再执行副作用。 | Coding Agent 也应先解析计划，再执行 shell/patch。 | 中 |
| 工具执行计划 | `src/agents/run_internal/tool_planning.py` | 把 fresh turn 和 resume turn 的工具执行统一计划化。 | `ToolExecutionPlan` 聚合不同工具队列和 pending interruption。 | 支持审批恢复时非常有价值。 | 中 |
| FunctionTool | `src/agents/tool.py`、`src/agents/function_schema.py` | 把 Python 函数包装为 LLM tool。 | 自动 schema、docstring、Pydantic 校验、approval、timeout。 | 最适合直接参考实现自己的工具系统。 | 低 |
| Tool guardrails | `src/agents/tool_guardrails.py` | 对工具输入/输出做安全检查。 | allow/reject/raise 三种行为。 | 对 shell、rm、network、secret access 很有用。 | 低 |
| RunContextWrapper | `src/agents/run_context.py` | 给工具/hook 传运行依赖和审批状态。 | context 不进入 LLM，usage 和 approvals 集中管理。 | 用于存 workspace、DB、config、approval state。 | 低 |
| RunState pause/resume | `src/agents/run_state.py`、`src/agents/result.py` | 人工审批中断后序列化并恢复运行。 | schema version、approvals、trace、sandbox 都可持久化。 | Coding Agent 的 HITL 审批可重点参考。 | 高 |
| Session protocol | `src/agents/memory/session.py`、`src/agents/run_internal/session_persistence.py` | 会话历史存取和去重。 | 最小 `get/add/pop/clear` 协议，内部负责 dedupe 和 rewind。 | 适合先实现 SQLite/JSONL 版本。 | 中 |
| OpenAI server conversation tracker | `src/agents/run_internal/oai_conversation.py` | 使用 Responses server-side conversation 时只发送 delta。 | ID、call_id、fingerprint 多维去重。 | 只有用 server-managed conversation 时才需要。 | 高 |
| Handoff | `src/agents/handoffs/__init__.py`、`src/agents/handoffs/history.py` | 多 Agent 交接和历史整理。 | handoff 被建模成 tool call，支持 input filter 和 history nesting。 | 多角色 coding team 可参考，但不适合第一阶段照搬。 | 中 |
| Agent-as-tool | `src/agents/agent.py` | 让一个 Agent 作为工具被另一个 Agent 调用。 | 不改变 current agent，比 handoff 更局部。 | 可用于“代码审查子 Agent”“测试生成子 Agent”。 | 中 |
| Model adapter | `src/agents/models/interface.py`、`src/agents/models/openai_responses.py`、`src/agents/models/multi_provider.py` | 解耦 LLM provider 和运行 loop。 | 统一 `ModelResponse`，Chat Completions 也转换成 Responses-style item。 | 自研 Agent 应保留 provider 抽象。 | 中 |
| Retry 管理 | `src/agents/run_internal/model_retry.py`、`src/agents/retry.py` | 模型请求失败后的策略化重试。 | 区分 stateful request，重试前 rewind。 | 对长任务和 server conversation 很重要。 | 中 |
| Tracing | `src/agents/tracing/`、`src/agents/lifecycle.py` | 观察 Agent run、tool、LLM、handoff、guardrail。 | Trace/Span/Processor 抽象完整，hooks 和 tracing 分离。 | 先借鉴 hooks 和 span 数据模型，不必照搬 exporter。 | 中 |
| SandboxAgent | `src/agents/sandbox/sandbox_agent.py`、`src/agents/sandbox/runtime.py` | Agent 与隔离执行环境绑定。 | public agent 和 execution agent 分离，运行时注入工具。 | Coding Agent 非常值得学习。 | 高 |
| Sandbox Manifest | `src/agents/sandbox/manifest.py` | 描述 workspace、环境变量、用户、mount、path grants。 | 明确文件系统边界和远程挂载策略。 | 比直接暴露宿主机文件系统更安全。 | 中 |
| Sandbox Capability | `src/agents/sandbox/capabilities/capability.py`、`src/agents/sandbox/capabilities/*` | 组合 shell、filesystem、memory、compaction 等能力。 | capability 可同时改 prompt、tools、manifest、sampling params。 | Coding Agent 的插件化能力层可参考。 | 中 |
| Sandbox shell 工具 | `src/agents/sandbox/capabilities/tools/shell_tool.py` | 在 sandbox 中执行命令并支持 PTY。 | `exec_command` / `write_stdin` 分离，输出带 chunk id、wall time、exit code。 | Coding Agent shell 交互可直接借鉴格式。 | 中 |
| Sandbox apply_patch | `src/agents/sandbox/capabilities/tools/apply_patch_tool.py` | 让模型用 patch 修改 sandbox 文件。 | freeform grammar + JSON 输入，operation-level approval。 | 文件编辑工具非常适合参考。 | 中 |
| MCP tools | `src/agents/mcp/server.py`、`src/agents/mcp/util.py` | 把 MCP server tools 转成 FunctionTool。 | 支持 stdio/SSE/streamable HTTP、tool filter、approval、meta resolver。 | 如果你的 Agent 需要插件生态，可参考。 | 中 |
| Hosted FileSearch | `src/agents/tool.py`、`examples/tools/file_search.py` | 使用 OpenAI vector store 做检索。 | retrieval 作为模型 hosted tool。 | 可参考接口形态，不适合本地代码索引直接照搬。 | 低 |
| Experimental Codex tool | `src/agents/extensions/experimental/codex/codex_tool.py`、`src/agents/extensions/experimental/codex/thread.py` | 把 Codex CLI 作为 Agent tool 执行任务。 | thread_id 恢复、stream events、usage 回填。 | 做 Coding Agent 编排时值得研究，但属于 experimental。 | 中 |

## 7. 与从零实现 Coding Agent 相关的建议

### 7.1 最值得优先学习的模块

1. **主 loop 和 NextStep**
   - 读 `src/agents/run.py`、`src/agents/run_internal/run_loop.py`、`src/agents/run_internal/run_steps.py`。
   - 目标是学会把 Agent 运行拆成稳定状态机，而不是写一个递归/while 混杂的脚本。

2. **工具系统**
   - 读 `src/agents/tool.py`、`src/agents/function_schema.py`、`src/agents/run_internal/tool_execution.py`。
   - Coding Agent 的核心竞争力通常来自 tool 设计，而不是 prompt。

3. **模型输出解析和调度**
   - 读 `src/agents/run_internal/turn_resolution.py`。
   - 重点看 `process_model_response()` 如何把模型输出拆成队列，以及 `execute_tools_and_side_effects()` 如何决定下一步。

4. **RunContext 和审批恢复**
   - 读 `src/agents/run_context.py`、`src/agents/run_state.py`、`src/agents/result.py`。
   - 这是实现“运行危险 shell / apply_patch 前暂停，用户 approve 后继续”的关键。

5. **Sandbox**
   - 读 `src/agents/sandbox/sandbox_agent.py`、`src/agents/sandbox/runtime.py`、`src/agents/sandbox/manifest.py`、`src/agents/sandbox/capabilities/`。
   - Coding Agent 如果要改代码、跑测试、读文件，sandbox 边界很重要。

### 7.2 适合直接参考设计的模块

- `FunctionTool`：适合直接参考 schema、invoke、approval、timeout 设计。
- `RunItem`：适合直接参考运行历史的数据模型。
- `NextStep*`：适合直接参考 loop 状态机。
- `RunContextWrapper`：适合直接参考上下文隔离设计。
- `Session` protocol：适合直接参考最小记忆接口。
- `ToolExecutionPlan`：适合参考工具执行计划化。
- `Sandbox Capability`：适合参考插件式能力注入。
- `SandboxApplyPatchTool`：适合参考 patch grammar、operation parsing、approval。
- `ExecCommandTool`：适合参考 shell 输出格式和 PTY 长进程交互。

### 7.3 暂时不建议照搬的模块

- **完整 `RunState` 序列化**
  - 很强，但复杂度高。第一版 Coding Agent 可以只保存 messages、tool calls、cwd、pending approvals，后续再做 schema version。

- **完整 `OpenAIServerConversationTracker`**
  - 只有你深度使用 Responses server-managed conversation 时才需要。否则本地 session history 更简单。

- **完整 tracing exporter**
  - 可以先做本地 JSONL trace 或 SQLite trace。OpenAI trace ingest、processor、shutdown、batch exporter 可后置。

- **Realtime / Voice**
  - `src/agents/realtime/` 和 `src/agents/voice/` 与 Coding Agent 主线关系弱，除非你要做语音 coding assistant。

- **所有 sandbox backend**
  - 不要一开始就做 Docker、远程、snapshot、mount provider 全套。先实现本地 workspace + 命令隔离 + allowlist，再抽象 backend。

- **完整 MCP lifecycle**
  - MCP 很有价值，但第一版 Coding Agent 可先支持本地 tools。插件生态成熟后再接入 `MCPServerManager` 类似设计。

- **Hosted retrieval 作为代码索引核心**
  - `FileSearchTool` 适合知识库文件检索，不等同于代码理解。Coding Agent 更需要 symbol graph、ripgrep、AST、LSP、test impact 分析。

### 7.4 建议的 Coding Agent 架构演进路线

#### 第 1 阶段：最小可用 loop

- 定义 `AgentConfig`：model、instructions、tools、max_turns。
- 定义 `ModelAdapter`：统一返回 `ModelResponse`。
- 定义 `RunItem`：message、tool_call、tool_output、approval。
- 定义 `NextStep`：final、run_again、interrupted。
- 支持 `FunctionTool` 和基本 shell tool。

参考文件：`src/agents/run_internal/run_steps.py`、`src/agents/tool.py`。

#### 第 2 阶段：可靠工具执行

- 函数签名转 schema。
- tool timeout。
- tool error formatter。
- shell 命令输出截断。
- apply_patch 工具。
- approval：危险命令和文件删除前暂停。

参考文件：`src/agents/function_schema.py`、`src/agents/run_internal/tool_execution.py`、`src/agents/sandbox/capabilities/tools/apply_patch_tool.py`。

#### 第 3 阶段：工作区和记忆

- workspace manifest。
- cwd / allowed paths。
- session history。
- run trace。
- resume from pending approval。

参考文件：`src/agents/sandbox/manifest.py`、`src/agents/memory/session.py`、`src/agents/run_state.py`。

#### 第 4 阶段：代码理解能力

- 本地 `rg`/AST/LSP/code graph 工具。
- 文件摘要和 symbol index。
- test selection。
- diff review。
- PR summary generation。

本仓库未提供完整本地代码 RAG，但可参考 tools 抽象，把这些能力包装成 `FunctionTool`。

#### 第 5 阶段：多 Agent 和插件化

- Code writer / reviewer / tester 作为 agents-as-tools。
- handoff 用于跨角色任务移交。
- MCP 插件生态。
- sandbox backend 抽象。

参考文件：`src/agents/agent.py` 的 `Agent.as_tool()`、`src/agents/handoffs/__init__.py`、`src/agents/mcp/`、`src/agents/sandbox/session/sandbox_client.py`。

## 8. 重点结论

这个仓库的核心价值不是某个 prompt，而是 **运行时架构**：

- `Agent` 是声明式配置。
- `Runner` 是状态机。
- `Model` 是可替换 adapter。
- `RunItem` 是可审计运行历史。
- `FunctionTool` 是工具统一抽象。
- `NextStep` 是 loop 控制边界。
- `RunState` 是 HITL 和恢复边界。
- `SandboxAgent` 是 Coding Agent 最接近的执行环境抽象。

如果目标是升级自己的 Coding Agent，优先学习顺序应是：

1. `src/agents/run_internal/run_steps.py`
2. `src/agents/run.py`
3. `src/agents/run_internal/run_loop.py`
4. `src/agents/run_internal/turn_resolution.py`
5. `src/agents/tool.py`
6. `src/agents/function_schema.py`
7. `src/agents/run_context.py`
8. `src/agents/sandbox/`
9. `src/agents/run_state.py`
10. `src/agents/tracing/`

本项目已包含 Coding Agent 所需的大部分底层机制：工具执行、shell、patch、sandbox、approval、resume、trace。但它默认是通用 SDK，不是开箱即用的代码库理解 Agent；本地代码检索、测试选择、仓库语义索引、变更规划等能力需要在其 tool/capability 抽象之上补齐。
