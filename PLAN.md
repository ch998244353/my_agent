# my_agent 教学计划：Chat Runtime / CLI 会话层

用途：本文件只服务于当前最推荐升级模块：把 `my_agent` 从“已有 run loop 和 example 的教学框架”升级为一个基础可用、可持续教学的聊天 Agent 入口。后续每节正式课程开始前必须先读取本文件，并且只实现当前课范围内的生产代码。

## 选取模块

本轮只选：`Chat Runtime / CLI 会话层`。

选择原因：

- `my_agent` 已经有 `Agent`、`Runner`、`run_chat_turn()`、`AgentSession`、`JsonSession`、`OpenAIResponsesModel`，但还缺少真正用户会使用的聊天运行时入口。
- 当前 `examples/basic_chat.py` 证明多轮聊天可以跑通，但它是演示脚本，不是稳定的聊天应用入口。
- Coding Agent 后续必须先有稳定会话入口，才能继续接入 shell、workspace、patch、git、test/lint 等工具。
- 该模块主要是添加薄运行层，不会破坏现有框架结构。

## 本模块不做的事

- 不重写 `Agent`、`Runner`、`run_loop.py`。
- 不实现 shell、workspace、文件编辑、git、patch、代码搜索。
- 不引入 streaming。
- 不引入完整 TUI/GUI。
- 不引入完整 MCP。
- 不引入 Docker sandbox。
- 不修改参考项目源码。
- 不删除用户已有中文注释。

## 当前 my_agent 相关结构

本模块主要触碰以下本地文件：

- `my_agent/src/agents/chat.py`
  - 当前只有 `run_chat_turn()` 和 `chat_stop_reason()`。
  - 需要增加聊天运行时需要的轻量结果整理函数，保持与 `Runner` 解耦。
- `my_agent/src/agents/memory.py`
  - 当前已有 `AgentSession`、`JsonSession`、summary/compaction。
  - 本模块只复用，不重写 memory。
- `my_agent/src/agents/models.py`
  - 当前已有 `OpenAIResponsesModel`。
  - 本模块只在创建真实聊天 agent 时复用，不扩展 provider。
- `my_agent/src/agents/__init__.py`
  - 需要导出新增公开入口。
- `my_agent/src/agents/chat_runtime.py`
  - 新增。负责聊天 agent 构造、session 构造、单轮结果归一化。
- `my_agent/src/agents/chat_cli.py`
  - 新增。负责终端交互循环。
- `my_agent/examples/basic_chat.py`
  - 后期可选择改为复用 chat runtime，但不强制。

## 主参考项目和位置

主要参考 `reference/openaiagent/`，只取轻量入口设计：

- `reference/openaiagent/src/agents/run.py`
  - 参考 `Runner.run_sync()` 作为用户入口 facade 的思路。
  - 不照搬 async、streamed、RunState resume。
- `reference/openaiagent/src/agents/memory/session.py`
  - 参考最小 session protocol 思路：`get_items()`、`add_items()`、`pop_item()`、`clear_session()`。
  - 本地已有 `SessionLike`、`AgentSession`、`JsonSession`，本模块只组织使用。
- `reference/openaiagent/src/agents/repl.py`
  - 参考 REPL/终端入口分层思想。
  - 不照搬完整命令系统。

辅助参考：

- `reference/mini-swe-agent-main/src/minisweagent/run/mini.py`
  - 参考 CLI 参数到 Agent/Model/Environment 的装配方式。
  - 本模块只做 chat agent 装配，不做 environment。
- `reference/mini-swe-agent-main/src/minisweagent/agents/utils/prompt_user.py`
  - 参考多行输入和终端 prompt 的思路。
- `reference/aider-main/aider/commands.py`
  - 只参考 slash command 入口思想。
  - 本模块只做 `/exit`、`/clear`、`/history`、`/help` 这类最小命令。

## 讲课规范

- 每节正式课程开始前必须先读取 `PLAN.md`。
- 每节课先用 100-300 个中文字符声明本节课目的，并举例说明当前代码实现后能做什么。
- 每节课必须实际更新代码，但只能更新该节课范围内的代码。
- 不要一次性完成整个模块。
- 每节课新增或修改的生产代码尽量不超过 80 行，不包含测试代码和复用代码。
- 展示修改代码位置请用可点击的 Markdown 本地文件链接，格式为 `[文件名:行号](<绝对路径:行号>)`。
- 如果是优化替代旧代码，要先说明旧代码形态和处理思路，再展示新代码。
- 如果是新增代码，要说明新增了哪些类、函数、字段、参数或导出项。
- 不展示测试代码，只说明测试覆盖意图和结果。
- 不删除用户注释，除非注释对应的代码已经消失；注释必须继续靠近目标代码。
- 所有讲课内容放在对话框内，不另开教学文件。
- 每节课结束时说明：完成了什么、未做什么、下一节为什么接着这样做。

## 课程安排

### 第 1 课：定义聊天运行结果结构

本节目标：

- 增加一个轻量 `ChatTurn` 或同等结构，把 `RunResult` 中用户最关心的内容整理出来。
- 例如调用一次聊天后，能稳定拿到 `answer`、`reached_final_answer`、`stop_reason`、`new_items_count`。

参考代码：

- 主参考：`reference/openaiagent/src/agents/result.py`
- 主参考：`reference/openaiagent/src/agents/run.py`
- 本地修改：`my_agent/src/agents/chat.py`
- 本地修改：`my_agent/src/agents/__init__.py`

预计生产代码：40-70 行。

新增内容：

- `ChatTurn` dataclass。
- `chat_turn_from_result(result: RunResult) -> ChatTurn`。
- 公开导出新增类型和函数。

完成标准：

- `run_chat_turn()` 仍返回原有 `RunResult`，不破坏旧 API。
- 新函数可从 `RunResult` 中整理出聊天层结果。
- 不改变 run loop。

### 第 2 课：新增 ChatRuntime 配置与 Agent 构造

本节目标：

- 新增 `chat_runtime.py`，集中管理聊天 agent 的默认构造。
- 例如用户只给 model 名称、instructions、session path，就能得到可运行的 chat agent 和 session。

参考代码：

- 主参考：`reference/mini-swe-agent-main/src/minisweagent/run/mini.py`
- 辅助参考：`reference/openaiagent/src/agents/run.py`
- 本地新增：`my_agent/src/agents/chat_runtime.py`
- 本地修改：`my_agent/src/agents/__init__.py`

预计生产代码：60-80 行。

新增内容：

- `ChatRuntimeConfig` dataclass。
- `build_chat_agent(config: ChatRuntimeConfig) -> Agent`。
- `build_chat_session(config: ChatRuntimeConfig) -> AgentSession | JsonSession | None`。

完成标准：

- 可以用默认配置创建 `Agent(memory=AgentMemory(), model=OpenAIResponsesModel(...))`。
- 可以选择内存 session 或 JSON 文件 session。
- 不引入 argparse，不做 CLI 循环。

### 第 3 课：实现单轮 ChatRuntime.run_turn()

本节目标：

- 在 `ChatRuntime` 中封装一次聊天调用，内部复用 `run_chat_turn()`，外部返回 `ChatTurn`。
- 例如后续 CLI 不需要理解 `RunResult`、`RunItem` 和 stop reason 细节。

参考代码：

- 主参考：`reference/openaiagent/src/agents/run.py`
- 辅助参考：`reference/mini-swe-agent-main/src/minisweagent/agents/default.py`
- 本地修改：`my_agent/src/agents/chat_runtime.py`
- 本地修改：`my_agent/src/agents/__init__.py`

预计生产代码：50-80 行。

新增内容：

- `ChatRuntime` class。
- `ChatRuntime.run_turn(message: str) -> ChatTurn`。
- 保存 `agent`、`session`、`max_turns`、`run_config`。

完成标准：

- `ChatRuntime` 能连续运行两轮，并使用同一个 session。
- 调用层不需要直接调用 `Runner.run_sync()`。
- 不做终端输入输出。

### 第 4 课：新增最小 chat_cli 入口函数

本节目标：

- 新增 `chat_cli.py`，实现一个最小终端循环，把用户输入交给 `ChatRuntime.run_turn()`。
- 例如运行入口能持续读取用户输入，打印 assistant 回复，输入 `/exit` 退出。

参考代码：

- 主参考：`reference/openaiagent/src/agents/repl.py`
- 辅助参考：`reference/mini-swe-agent-main/src/minisweagent/agents/utils/prompt_user.py`
- 本地新增：`my_agent/src/agents/chat_cli.py`
- 本地修改：`my_agent/src/agents/__init__.py`

预计生产代码：60-80 行。

新增内容：

- `run_chat_cli(runtime: ChatRuntime) -> None`。
- 最小命令：`/exit`、`/quit`。
- 空输入跳过。

完成标准：

- CLI 循环可以执行多轮对话。
- 退出命令不触发模型调用。
- 不做 argparse。

### 第 5 课：增加 CLI slash commands

本节目标：

- 给 chat CLI 增加最小命令处理层，支持 `/help`、`/clear`、`/history`。
- 例如用户可以清空当前 session，或者查看最近历史消息角色。

参考代码：

- 主参考：`reference/aider-main/aider/commands.py`
- 辅助参考：`reference/openaiagent/src/agents/repl.py`
- 本地修改：`my_agent/src/agents/chat_cli.py`
- 本地必要时修改：`my_agent/src/agents/chat_runtime.py`

预计生产代码：60-80 行。

新增内容：

- `handle_chat_command(...)` 或同等私有函数。
- `/help` 打印命令说明。
- `/clear` 调用 session 的 `clear_session()`。
- `/history` 展示 session replay 的精简视图。

完成标准：

- 命令处理不污染模型上下文。
- JsonSession 和 AgentSession 都能被 `/clear` 清空。
- 不引入复杂命令注册系统。

### 第 6 课：新增模块运行入口

本节目标：

- 让聊天 CLI 可以通过 Python 模块方式启动。
- 例如 `python -m agents.chat_cli` 或一个简单 `main()` 可以创建默认 runtime 并进入聊天。

参考代码：

- 主参考：`reference/mini-swe-agent-main/src/minisweagent/run/mini.py`
- 辅助参考：`reference/aider-main/aider/__main__.py`
- 本地修改：`my_agent/src/agents/chat_cli.py`

预计生产代码：40-70 行。

新增内容：

- `main(argv: list[str] | None = None) -> int`。
- 最小参数解析：`--model`、`--session`、`--instructions`、`--max-turns`。
- `if __name__ == "__main__": raise SystemExit(main())`。

完成标准：

- 可以通过模块入口启动真实 OpenAI Responses chat。
- 没有 session 参数时使用内存 session。
- 有 session 参数时使用 JsonSession。

### 第 7 课：整理 example 复用 ChatRuntime

本节目标：

- 让 `examples/basic_chat.py` 复用 `ChatRuntime` 或新增一个更贴近真实使用的 example。
- 例如 example 不再自己手写重复的 session 调用流程。

参考代码：

- 主参考：`my_agent/examples/basic_chat.py`
- 辅助参考：`reference/mini-swe-agent-main/src/minisweagent/run/hello_world.py`
- 本地修改：`my_agent/examples/basic_chat.py`

预计生产代码：30-70 行。

新增/修改内容：

- 保留 DemoChatModel。
- 使用 `ChatRuntime` 运行两轮对话。
- 保持 example 不调用真实网络。

完成标准：

- 原有 example 行为保持。
- example 展示的是后续推荐用法。
- 不引入真实 OpenAI client。

### 第 8 课：补齐公开 API 和文档式自检

本节目标：

- 清理 `__init__.py` 导出，确保用户可以从 `agents` 导入聊天运行时相关对象。
- 做一次模块边界自检，保证课程结束后这个模块是完整的。

参考代码：

- 主参考：`reference/openaiagent/src/agents/__init__.py`
- 本地修改：`my_agent/src/agents/__init__.py`
- 本地必要时修改：`my_agent/src/agents/chat_runtime.py`
- 本地必要时修改：`my_agent/src/agents/chat_cli.py`

预计生产代码：20-60 行。

新增/修改内容：

- 导出 `ChatRuntimeConfig`、`ChatRuntime`、`ChatTurn`、`chat_turn_from_result`。
- 检查命名一致性。
- 检查模块职责是否过度耦合。

完成标准：

- 从 `agents` 包可以导入聊天运行时核心对象。
- CLI、runtime、chat helper 三层职责清晰。
- 不扩大到 Coding Agent 工具能力。

## 模块完成标准

完成 8 节课后，本模块才算结束：

- `my_agent` 有可复用的聊天运行时对象。
- `my_agent` 有最小终端聊天入口。
- 支持内存 session 和 JSON session。
- 支持多轮对话、清空会话、查看历史、退出。
- example 能复用新 runtime。
- 现有 `Runner`、`run_loop`、`AgentSession` 不被重写。
- 新增生产代码控制在 300-2000 行范围内。
- 每节课新增或修改生产代码尽量不超过 80 行。

## 下一步课程入口

用户要求开始正式课程时，从“第 1 课：定义聊天运行结果结构”开始。

正式课程开始时必须先读取本文件，然后再读本节涉及的本地文件和参考文件，最后进行小步代码修改。
