# my_agent Session 与记忆压缩教学计划

本文件是后续每节正式课程开始前必须先读取的教学约束。当前只升级 `my_agent` 的 Session / Memory / Context Compaction 层，不制定完整 coding agent、RAG、workspace tools、approval、streaming 的路线。

## 模块目标

把现有 `AgentMemory` / `AgentSession` 从“agent 内部临时记忆”升级为“可被 `RunConfig` 和 `run_loop` 协作使用的本地会话历史层”。完成本模块后，agent 应该具备：

1. 多轮对话历史读取。
2. 本次 run 结果写回 session。
3. session 历史序列化与 JSON 文件持久化。
4. 明确区分 `SessionTurn / StepRecord` 结构化历史和 `ChatMessage` 模型上下文。
5. 规则剪切旧历史，保留最近 turn 原文。
6. 用规则式或模型式 summarizer 把旧历史压成更短 `MemorySummary`。
7. `run_loop` 最终使用“summary + 最近历史”作为模型上下文。

## 为什么继续做这个模块

当前 `my_agent` 已经有主循环、工具调用、handoff、guardrails、tracing、基础 `AgentSession`、`JsonSession` 和初步 `MemorySummary`。但当前压缩还不完整：`_compact_if_needed()` 已经能把旧 turn 移到 summary 并删除旧 turn，可 summary 内容主要是拼接旧历史，不是真正的“上下文压缩”。因此后续课程要把压缩策略从 `AgentSession` 中抽出来，先做规则剪切，再接模型式摘要。

## 参考 OpenAI Agents SDK 的边界

本模块参考 OpenAI Agents SDK 的这些思想：

- `Session` 协议只规定 `get_items / add_items / pop_item / clear_session`。
- run 开始从 session 读历史，run 结束把新 items 写回 session。
- 支持压缩的 session 可以额外提供 `run_compaction()`。
- OpenAI SDK 的 `OpenAIResponsesCompactionSession` 是工程版，会调用 `responses.compact` 并替换底层 session 历史。
- 官方 session memory 思路分为两类：Context Trimming 和 Context Summarization。

本模块暂不照搬：

- Responses API 原生 `responses.compact`。
- server-managed conversation / `previous_response_id`。
- streaming resume、approval resume、复杂去重。
- SQLite / Redis / MongoDB session。

## 固定讲课规则

- 每节正式课开始前必须先读取本文件。
- 每节正式课先用 100-300 字说明本节课目的，并举例说明当前代码已经能做什么、本节要补什么。
- 每节课只实现一个小的完整增量，新增实现代码尽量不超过 80 行，不含测试代码；如果确实需要更大改动，必须先拆课。
- 每节课都要实际更新代码，但不要提前完成后续课程内容。
- 每个修改都要在对话框里展示具体文件位置和行号，并说明新增、删除或调整了哪些类、字段、函数或逻辑。
- 不删除用户已有注释，除非注释对应的目标代码已经消失，并保持注释和目标代码位置正确。
- 测试代码只简略说明，不展开讲解。
- 所有讲课内容放在对话框内，不另开教学文件。

## 数据结构边界

### `AgentMemory`

负责单次 agent run 过程中的临时执行记忆。它记录当前任务、当前 run 的 step、工具调用、观察结果和错误。它不是跨 run 的长期 session。

### `AgentSession`

负责跨 run 的会话历史。它保存 `summary` 和多个 `SessionTurn`，对外提供 `get_items / add_items / pop_item / clear_session`，使 `RunConfig(session=...)` 可以被 `run_loop` 使用。

### `SessionTurn`

表示一轮用户对话：一个用户 task，加上该轮内部的多个 `StepRecord`。它是内部结构化历史，不直接发给模型。

### `StepRecord`

表示一次执行步骤，能保存 assistant 消息、工具调用、工具 observation、错误和 final answer 标记。它比普通聊天消息更完整，方便序列化、压缩和调试。

### `ChatMessage`

表示模型真正能看到的扁平上下文。`AgentSession.replay()` 会把 `summary + SessionTurn + StepRecord` 转换成 `list[ChatMessage]`。

### `MemorySummary`

表示已压缩旧历史。它应该是短、结构化、可注入模型上下文的摘要，而不是旧历史的完整拼接。

## 课程安排

### 第 1 课：给 `RunConfig` 增加 session 入口

目标：只建立外部会话入口，不改变主循环行为。

新增内容：在 `run_config.py` 中增加可选 `session` 字段，并用最小类型约定说明 session 至少要提供历史读取和写入能力。作用是让调用方可以写 `RunConfig(session=...)`。

状态：已完成。

### 第 2 课：把 session 历史合并到本轮模型输入

目标：让 agent 在当前用户问题前看到已有对话历史。

新增内容：在 `run_loop` 开始阶段识别 `config.session`，把 session 历史转成 `ChatMessage` 后合并到本轮 `agent.memory` 可见的输入中。此课只做读取，不写回。

状态：已完成。

### 第 3 课：把本次 run 的结果写回 session

目标：让本次对话成为下一次对话的历史。

新增内容：在 run 结束时把用户输入、工具调用、工具结果、最终输出整理成 session 可保存的 turn。连续调用同一个 session 时，下一轮能看到上一轮发生过什么。

状态：已完成。

### 第 4 课：整理 session item 转换边界

目标：避免 `run_loop` 直接知道太多 `StepRecord` 和 `RunItem` 细节。

新增内容：增加小型转换函数，例如从 `RunResult` / `RunItem` 生成可回放的 `ChatMessage`。作用是把“运行事件如何变成聊天历史”集中管理。

状态：已完成。

### 第 5 课：给 `AgentSession` 增加最小序列化

目标：为本地保存和恢复做准备。

新增内容：给 `AgentSession` / `SessionTurn` / 必要的 `StepRecord` 数据增加 `to_dict` 和 `from_dict`。只保存任务、消息、工具调用、观察结果、错误和 final 标记，不做文件读写。

状态：已完成。

### 第 6 课：新增 JSON 文件 session

目标：让会话历史可以跨程序重启保存。

新增内容：新增轻量 `JsonSession`，负责从磁盘读取、追加、弹出最近 item、清空历史。文件格式保持简单可读，不引入数据库。

状态：已完成。

### 第 7 课：增加记忆压缩数据结构

目标：为长对话控制上下文长度。

新增内容：在 session 层增加 `MemorySummary` / `summary` 字段，并定义压缩后的历史如何作为一条系统消息进入模型上下文。此课只做数据结构，不接真实模型摘要。

状态：已完成。

### 第 8 课：实现初版规则式历史折叠

目标：让 session 能在 turn 太多时移动旧历史，保留最近历史。

新增内容：根据最大 turn 数触发压缩，把较早 turn 合并进 `summary`，保留最近 turn 原文。当前版本只把旧 turn 渲染并拼接到 summary，因此属于“历史折叠”，还不是真正短摘要。

状态：已完成但需继续改进。

### 第 9 课：抽出 `MemoryCompressor` 边界

目标：不要让 `AgentSession` 同时负责保存历史和决定如何压缩历史。

新增内容：

- 新增 `CompactionPolicy`，字段包括 `compact_after_turns`、`keep_recent_turns`、`max_summary_chars`。
- 新增 `MemoryCompressor`，负责判断是否需要压缩、选择旧 turns、保留最近 turns。
- `AgentSession` 只保存 `summary` 和 `turns`，调用 compressor 得到压缩结果。
- 测试证明 compressor 可以独立测试，不需要完整 run loop。

状态：已完成。

### 第 10 课：实现规则剪切 compact input

目标：在模型摘要前先减少噪声，避免把完整旧历史直接丢给 summarizer。

新增内容：

- 新增旧 turn 到 compact input 的转换函数。
- 只保留用户目标、关键 assistant 消息、工具名、关键参数、observation 摘要、错误摘要。
- 对长 observation、长 assistant message、长 error 做字符上限截断。
- 不剪切最近保留的 turns。

状态：已完成。

### 第 11 课：实现规则式 summarizer fallback

目标：在没有真实模型调用时，也能生成比拼接更短的摘要。

新增内容：

- 新增 `MemorySummarizer` 协议。
- 新增 `RuleBasedSummarizer`，把 compact input 转成结构化短摘要。
- 摘要固定输出这些段落：`User goals`、`Constraints`、`Decisions`、`Important facts`、`Open tasks`。
- 测试证明摘要长度小于 compact input，并保留关键事实。

状态：已完成。

### 第 12 课：实现模型式 summarizer

目标：让旧历史可以由模型生成更自然、更短的摘要。

新增内容：

- 新增 `ModelSummarizer`，接收一个最小模型接口或现有 model adapter。
- 构造固定 summary prompt，输入为旧 summary + compact input。
- 输出仍写入 `MemorySummary`。
- 先用 fake model 做确定性测试，再提供真实 OpenAI Responses API 接入。
- 如果本机存在 `OPENAI_API_KEY`，用小输入做一次真实模型 smoke test。
- 模型调用失败时 fallback 到 `RuleBasedSummarizer`。

状态：已完成。

### 第 13 课：把 summarizer 接入 `MemoryCompressor`

目标：让压缩流程真正从“旧 turn 拼接”变成“规则剪切 + 摘要生成”。

新增内容：

- `MemoryCompressor.compact(session)` 返回新的 `MemorySummary` 和保留 turns。
- `AgentSession` 在新增 turn 后触发 compressor。
- 旧 turn 被删除，summary 是 summarizer 生成的短摘要。
- 测试断言：压缩后 `turns` 只剩最近 N 轮，summary 不包含完整长 observation。

状态：已完成。

### 第 14 课：让 `JsonSession` 保存和恢复压缩策略

目标：程序重启后仍能继续按相同规则压缩。

新增内容：

- JSON 中保存 `summary` 和 `compaction_policy`。
- `JsonSession` 加载后能恢复 compressor 配置。
- 测试证明：写入多轮、重启 session、继续追加时，仍保持 `summary + 最近历史`。

状态：已完成。

### 第 15 课：确认 `run_loop` 使用压缩后的 session

目标：让主循环最终看到的是“旧历史摘要 + 最近历史”，不是完整旧历史。

新增内容：

- 补充 run_loop/session 集成测试。
- 确认 `config.session.get_items()` 返回的第一条是 summary message，后面是最近 turns。
- 确认本轮 run 结束后写回 session 不会破坏已有 summary。

状态：已完成。

### 第 16 课：Session / Memory 模块收尾

目标：确认本模块已经能作为后续 RAG / coding agent 的上下文底座。

新增内容：

- 整理公开导出。
- 统一命名：`AgentMemory` 负责单次执行，`AgentSession` 负责跨 run 历史，`JsonSession` 负责本地持久化，`MemoryCompressor` 负责上下文压缩。
- 添加一个最小示例：连续对话、多轮历史、触发压缩、重启恢复。
- 全量测试通过。

状态：已完成。

## 本模块验收标准

- `RunConfig(session=AgentSession(...))` 可以读写多轮历史。
- `RunConfig(session=JsonSession(...))` 可以跨程序保存历史。
- 历史超过阈值后，旧 turns 会被移出原文历史。
- 旧 turns 先经过规则剪切，再由 summarizer 生成短摘要。
- 模型上下文只包含 `MemorySummary + 最近 turns`。
- 最近 turns 保留原始工具调用、observation 和错误细节。
- 全量测试通过。

## 本模块不做的内容

本模块不做：

- RAG。
- 向量库。
- coding workspace tools。
- shell / apply_patch 工具。
- streaming。
- MCP。
- 完整 approval / resume。
- OpenAI server-managed conversation。
- 数据库 session。

Session 与记忆压缩完成后，再选择下一个升级模块。
