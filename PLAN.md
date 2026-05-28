# my_agent Run Step 状态机教学计划

本文件是后续每节正式课程开始前必须先读取的教学约束。当前只升级 `my_agent` 的 Run Step 状态机与响应分流层，不制定完整 coding agent、RAG、workspace tools、approval、streaming、MCP 的路线，也不复刻 OpenAI Agents SDK 的工程级全部实现。

## 模块总目标

把当前 `run_loop.py` 中混在一起的“模型调用、工具调用、handoff、final output、停止原因判断”拆成更清楚的教学版状态机：

`model response -> ProcessedResponse -> SingleStepResult -> NextStep`

完成本模块后，`my_agent` 应该仍保持现有同步 API：

- `Runner.run_sync(agent, task, config=...)`
- `Agent.run(task, config=...)`

但主循环不再直接解释所有模型输出，而是只消费 `SingleStepResult.next_step`。这样后续新增 RAG、workspace tools、approval/resume、streaming、coding agent 工具闭环时，不需要继续把逻辑塞进一个越来越长的 `while + if + for` 主循环。

## 为什么做这个模块

当前 `my_agent` 已经能进行基础对话、工具调用、子 agent handoff、guardrails、session 读写、记忆压缩和 tracing。旧代码的主要缺陷不是不能跑，而是运行时边界还不够清楚：`run_loop.py` 同时负责调用模型、解释模型返回、执行工具、处理 handoff、处理 final answer 和停止原因。OpenAI Agents SDK 的核心运行模型是把模型响应先解析成 `ProcessedResponse`，再落成 `SingleStepResult`，最后由 `NextStepFinalOutput / NextStepHandoff / NextStepRunAgain / NextStepInterruption` 推进主循环。本模块只学习这个思想的最小可运行版本。

## 本模块做什么

- 新增或调整运行时数据结构：`ProcessedResponse`、`SingleStepResult`、`NextStepFinalOutput`、`NextStepRunAgain`、`NextStepHandoff`。
- 预留 `NextStepInterruption` 的概念边界，但本模块不实现 approval/resume。
- 把模型返回后的分类逻辑从 `run_loop.py` 下沉到 `run_steps.py` 或小型辅助模块。
- 保持现有工具调用、final answer、handoff、guardrails、session 写回行为不被破坏。
- 每节课只完成一个小的、可测试的增量。

## 本模块不做什么

本模块不做：

- RAG。
- 向量库。
- workspace 文件读写工具。
- shell / apply_patch 工具。
- streaming。
- MCP。
- approval / resume。
- server-managed conversation。
- 多 provider model adapter 重构。
- 完整 OpenAI Agents SDK 复刻。

这些能力都可以在本模块完成后继续作为独立教学模块推进。

## 固定讲课范式

- 每节正式课开始前必须先读取本文件。
- 每节正式课如需理解代码结构，先用 CodeGraph 查询相关符号和执行流，不全量读取源码。
- 每节正式课先用 100-300 字说明本节课目的，并举例说明当前代码已经能做什么、本节要补什么。
- 每节课都要实际更新代码，但不要一次性完成整个模块。
- 每节课只实现一个小的完整增量，新增实现代码尽量不超过 80 行，不含测试代码；如果确实需要更多代码，必须拆成更多课程。
- 每节课修改后，在对话框中逐个展示本节修改点，并说明每段代码的功能。
- 每节课展示本节修改点时，必须附上本节新增或修改的代码片段；如果代码较长，按文件分段展示关键新增/修改部分。
- 每个修改都要给出具体文件位置，具体到行号。
- 每个修改都要说明新增、删除或调整了哪些类、字段、函数或逻辑。
- 不删除用户已有注释，除非注释对应的目标代码已经消失，并保持注释和目标代码位置正确。
- 测试代码只简略说明，不展开讲解。
- 所有讲课内容放在对话框内，不另开教学文件。
- 每节课结束前必须运行相关测试；如果测试无法运行，要说明原因。

## 当前代码基础

当前 `my_agent` 已经具备这些基础：

- `Agent` 保存 instructions、tools、handoffs、guardrails、capabilities、model 等配置。
- `Runner.run_sync()` 调用 `run_agent_loop()` 执行同步主循环。
- `run_loop.py` 已经能读取 session 历史、执行 input/output guardrails、调用模型、执行工具、处理 handoff、写回 session。
- `run_steps.py` 已经包含 `TurnInput`、`ModelTurnResult`、`ToolExecutionOutcome`、`HandoffOutcome` 等局部结构。
- `RunState` 已经记录 `new_items`、`final_answer`、`current_turn`、`steps_taken`、guardrail results 等运行状态。

本模块会在这些已有结构上继续演进，不推倒重写。

## 课程安排

### 第 1 课：定义最小 `NextStep` 数据结构

目标：先建立状态机的返回语言，不改变主循环行为。

新增内容：

- 在 `run_steps.py` 中新增 `NextStepFinalOutput`、`NextStepRunAgain`、`NextStepHandoff` 数据结构。
- 新增 `NextStep` 类型别名。
- 新增 `SingleStepResult`，用于表达“一轮模型响应和副作用处理后的统一结果”。
- 本课只增加结构，不改 `run_loop.py` 的执行路径。

作用：让后续课程可以逐步把 `run_loop.py` 的判断分支迁移到 `SingleStepResult.next_step`。

状态：已完成。

### 第 2 课：定义 `ProcessedResponse`

目标：把“模型返回了什么”从“下一步要做什么”中分离出来。

新增内容：

- 在 `run_steps.py` 中新增 `ProcessedResponse`。
- 保存模型响应、普通工具调用、handoff 工具调用、是否已有 final output 候选。
- 新增一个小函数，把当前 `ModelTurnResult` 分类成 `ProcessedResponse`。

作用：以后模型返回多个 tool call、handoff 或 final answer 时，先分类，再决定下一步，避免主循环直接读模型细节。

状态：已完成。

### 第 3 课：把 final output 分支落成 `NextStepFinalOutput`

目标：先迁移最简单的结束分支。

新增内容：

- 新增函数根据 `ProcessedResponse` 判断是否已经得到 final output。
- 返回 `SingleStepResult(next_step=NextStepFinalOutput(...))`。
- `run_loop.py` 只在这个分支上消费新的 `SingleStepResult`，其他分支暂时保持旧逻辑。

作用：让学生看到状态机如何一步步接管原主循环，而不是一次重构全部代码。

状态：已完成。

### 第 4 课：把“无工具调用停止”纳入 step resolution

目标：把模型没有返回工具调用时的停止原因从主循环中移出。

新增内容：

- 新增处理 “model_returned_no_tool_call” 的 step resolution 函数。
- 由 `SingleStepResult` 携带停止信息。
- 保持旧的 `record_run_stopped()` 行为。

作用：统一模型响应后的停止判断，为后面工具分支迁移做准备。

状态：已完成。

### 第 5 课：把普通工具调用落成 `NextStepRunAgain`

目标：让普通工具执行后明确表达“需要再次调用模型”。

新增内容：

- 新增处理普通 tool calls 的小函数。
- 工具执行后，如果没有 final answer，则返回 `NextStepRunAgain`。
- `run_loop.py` 根据 `NextStepRunAgain` 继续 while 循环。

作用：这是未来 RAG 工具、workspace 工具、shell 工具的基础闭环。

状态：已完成。

### 第 6 课：把工具结果作为 final output 的分支迁移出去

目标：把 `final_answer` 工具、`stop_on_first_tool`、`stop_at_tool_names` 等停止逻辑纳入 step resolution。

新增内容：

- 将 `ToolExecutionOutcome.should_stop` 解释为 `NextStepFinalOutput`。
- 保持 output guardrails 行为不变。
- 保持 `record_final_output()` 的调用位置清晰。

作用：工具既可能只是给模型提供 observation，也可能直接结束运行，本课把这两类结果分清。

状态：已完成。

### 第 7 课：把 handoff 分支落成 `NextStepHandoff`

目标：让子 agent 调用不再只是主循环里的特殊 if 分支。

新增内容：

- 新增 handoff step resolution。
- handoff 后返回 `NextStepHandoff(target_agent=...)` 或等价结构。
- `run_loop.py` 消费这个 next step，保持现有 `target_agent.run(task)` 行为。

作用：先保留当前 nested handoff 实现，但把“发生了 agent 转交”这件事显式纳入状态机。

状态：已完成。

### 第 8 课：收敛 `run_loop.py` 为状态机消费者

目标：让主循环只负责编排生命周期，不直接解释模型输出细节。

新增内容：

- 把第 3-7 课形成的分支统一接入 `run_loop.py`。
- 主循环根据 `NextStepFinalOutput / NextStepRunAgain / NextStepHandoff` 推进。
- 清理已经迁移出去的重复判断。

作用：完成本模块的核心结构升级。

状态：已完成。

### 第 9 课：补齐 session 与 guardrails 的集成验证

目标：确认状态机升级没有破坏现有上下文底座。

新增内容：

- 补充或调整测试，覆盖 session 写回、output guardrail 拦截、tool guardrail 拒绝、handoff 后 final output。
- 如有必要，微调 `RunItem` 写入顺序。

作用：保证后续 RAG 和 coding tools 能复用稳定的运行历史。

状态：已完成。

### 第 10 课：模块收尾与公开导出

目标：确认本模块可以作为后续 RAG / workspace tools / approval 的前置底座。

新增内容：

- 整理 `__init__.py` 中需要公开的状态机结构。
- 增加一个最小示例或测试，展示模型调用、工具执行、run again、final output 的完整链路。
- 全量测试通过。

作用：把状态机模块收束成稳定教学成果，后续模块不需要重新解释主循环结构。

状态：已完成。

## 本模块验收标准

- 外部调用方式不变：`Runner.run_sync()` 和 `Agent.run()` 仍可用。
- 现有测试全部通过。
- `run_loop.py` 不再承担所有模型响应解释逻辑。
- 模型响应会先被分类成 `ProcessedResponse`。
- 一轮运行结果会落成 `SingleStepResult`。
- 主循环根据 `NextStepFinalOutput / NextStepRunAgain / NextStepHandoff` 推进。
- 预留 `NextStepInterruption` 的概念边界，但不实现 approval/resume。
- session、guardrails、handoff、tool_use_behavior 的现有行为不被破坏。

## 后续模块衔接

本模块完成后，推荐后续模块顺序：

1. Model 接口与 `ModelSettings` 打通。
2. Tool planning / tool execution 分层。
3. 最小 RAG 工具模块。
4. Workspace tools。
5. Approval / resume。
6. Streaming。
7. Coding agent MVP。
