# PLAN05 - Repo Context Chunk Integration 教学计划

本计划是“仓库理解与上下文选择能力”的最后一步。前四个计划分别建立了 workspace inventory、mention detection、selected files state 和 code index tools。PLAN05 要把它们串起来，生成真正能进入模型输入的 `repo_context`。

这个模块的目标不是让 Agent 自动改代码，而是让模型在思考前获得一段稳定、可审计、可裁剪的仓库上下文。它是后续安全编辑、验证闭环、CLI Coding Agent 的基础。

## 给新 Agent 的快速导航

先读这些 my_agent 文件：

- `my_agent/src/agents/context_chunks.py`
- `my_agent/src/agents/model_turn.py`
- `my_agent/src/agents/run_context.py`
- `my_agent/src/agents/workspace_inventory.py`
- `my_agent/src/agents/context_mentions.py`
- `my_agent/src/agents/selected_files.py`
- `my_agent/src/agents/code_index.py`

再读这些参考文件：

- `reference/aider-main/aider/repomap.py`
- `reference/aider-main/aider/coders/chat_chunks.py`
- `reference/aider-main/aider/coders/base_coder.py`

Aider 的 repo map 是大仓库上下文压缩的核心参考。但 `my_agent` 不应该在这个阶段复制完整 PageRank/tree-sitter 体系。这里先做轻量 repo context builder，把已有线索组织好。

## 模块目标

完成后，`my_agent` 应该能构建 `RepoContext`。它应该综合以下来源：

- PLAN01 的 workspace inventory。
- PLAN02 的 mention candidates。
- PLAN03 的 selected files state。
- PLAN04 的 code index provider 查询结果。

`RepoContext` 最终要作为 `repo_context` chunk 进入 `prepare_turn_input()` 生成的 messages。它必须稳定、有边界、可测试。

## 当前旧代码缺陷

`context_chunks.py` 已经预留 `CONTEXT_CHUNK_REPO_CONTEXT`，但目前没有任何真实内容来源。`build_turn_context(agent)` 当前主要处理 system instructions、memory summary 和 memory messages。没有 repo context 时，模型依然只能靠 memory 和工具临时探索仓库。

本计划要补足的是“把仓库理解结果放到模型输入层”的最后一段链路。

## 计划新增的 my_agent 内容

创建 `my_agent/src/agents/repo_context.py`：

- `RepoContextSection`
- `RepoContext`
- `RepoContextBuilder`
- `build_repo_context()`
- 文本渲染和截断 helper

修改 `my_agent/src/agents/run_context.py`：

- 增加 `CONTEXT_REPO_CONTEXT_KEY = "repo_context"`。
- 增加 `repo_context` property。

修改 `my_agent/src/agents/context_chunks.py`：

- 增加 repo context chunk 渲染。
- 确保 chunk 顺序稳定。

必要时修改 `my_agent/src/agents/model_turn.py`：

- 如果 PLAN03 已经把 `build_turn_context()` 改为接受 `context_wrapper`，这里继续沿用。

新增测试：

- `my_agent/tests/test_repo_context.py`
- 修改 `my_agent/tests/test_context_chunks.py`

## 功能边界

必须做：

- repo context 有结构化 section。
- selected files 优先进入 repo context。
- mention candidates 能进入 repo context。
- fake code index results 能进入 repo context。
- 支持 `max_chars` 截断。
- 去重重复路径和重复 section。
- 渲染成稳定 prompt chunk。

不能做：

- 不自动编辑文件。
- 不运行测试。
- 不调用真实 LLM。
- 不做复杂 token 估算。
- 不强依赖真实 CodeGraph。
- 不把整个仓库文件内容塞进 prompt。

## 课程 1：定义 RepoContext 数据结构

优化目标：让 repo context 有独立结构，而不是拼接字符串。

执行标准：测试能创建 section/context，并能渲染为文本。

新增能力：`RepoContextSection(title, content, source, priority)` 和 `RepoContext(sections, selected_paths, mentioned_symbols, truncated)`。

功能边界：不查询任何索引，不读取文件。

大致修改方案：新建 `repo_context.py`，写 dataclass、排序和 `to_text()`。新增代码控制在 60 到 80 行。

参考代码：Aider repo map 输出结构和 my_agent context chunk 风格。

## 课程 2：从 selected files 生成 context

优化目标：让 PLAN03 的 selected files 成为 repo context 的第一来源。

执行标准：selected files 中的 read-only/editable 文件会生成一个 selected files section。

新增能力：`RepoContextBuilder` 可以消费 `SelectedFilesState`。

功能边界：只列路径和 mode，不读取文件内容。

大致修改方案：实现 builder 的第一部分：读取 selected files，生成 section。新增代码控制在 60 到 80 行。

参考代码：PLAN03。

## 课程 3：从 mentions 生成 context

优化目标：让用户任务中的路径、文件名、符号线索进入 repo context。

执行标准：mention candidates 会生成 mentioned paths 和 mentioned symbols section。

新增能力：builder 消费 `MentionCandidate`。

功能边界：不自动把 symbol mention 变成文件内容；符号查询放到下一课。

大致修改方案：builder 接收 mentions，按 kind 分组，去重后生成 section。新增代码控制在 60 到 80 行。

参考代码：PLAN02。

## 课程 4：接入 code index 查询结果

优化目标：让符号和文本搜索结果能进入 repo context。

执行标准：使用 fake provider 时，builder 能把 symbol/text matches 渲染成 section。

新增能力：builder 调用 `CodeIndexProvider`。

功能边界：不要在这里写真实 CodeGraph 解析逻辑。PLAN04 已经处理 provider。

大致修改方案：对 symbol mentions 调用 `search_symbols()`；对路径或关键词可调用 `search_text()`。限制每类结果数量。新增代码控制在 60 到 80 行。

参考代码：PLAN04。

## 课程 5：实现去重和截断

优化目标：repo context 不能无限膨胀。

执行标准：重复路径只出现一次；超过 `max_chars` 时截断并标记 `truncated=True`。

新增能力：section-level 去重和简单字符预算。

功能边界：不做 tokenizer 级预算。

大致修改方案：在 `RepoContext.to_text(max_chars=...)` 或 builder 里实现简单裁剪。优先保留 selected files，再保留 mentions，再保留 index results。新增代码控制在 50 到 70 行。

参考代码：Aider `max_map_tokens` 的优先级思想。

## 课程 6：接入 RunContextWrapper

优化目标：让 repo context 成为 run context 的一部分。

执行标准：`RunContextWrapper(context={...}).repo_context` 能返回 `RepoContext`。

新增能力：`CONTEXT_REPO_CONTEXT_KEY` 和 property。

功能边界：不改 run loop。

大致修改方案：修改 `run_context.py`，仿照 workspace 和 selected files property。新增代码控制在 60 到 80 行。

参考代码：`my_agent/src/agents/run_context.py`。

## 课程 7：渲染 repo_context chunk

优化目标：让模型输入真正包含 repo context。

执行标准：调用 `prepare_turn_input()` 时，如果 context wrapper 中有 repo context，messages 里出现 `repo_context` 内容；没有 repo context 时不产生空消息。

新增能力：repo context prompt 注入。

功能边界：不要改变 memory 的原有语义，不要把 repo context 写入 long-term memory。

大致修改方案：修改 `context_chunks.py`。如果 PLAN03 已将 `build_turn_context()` 改为接受 `context_wrapper`，就在其中读取 `context_wrapper.repo_context`。设置明确 priority，让 repo context 出现在 system instructions 之后、普通 memory 之前或 selected files 附近，具体顺序必须由测试固定。新增代码控制在 50 到 70 行。

参考代码：`my_agent/src/agents/context_chunks.py` 和 Aider `ChatChunks`。

## 本模块完成标准

本模块完成时，新 Agent 应该能证明：

1. `RepoContext` 能独立构造、排序、渲染和截断。
2. selected files、mentions、code index results 都能成为 repo context section。
3. repo context 不重复、不无限增长。
4. repo context 存在于 `RunContextWrapper`。
5. `prepare_turn_input()` 输出的 messages 包含稳定的 `repo_context` chunk。
6. 没有 repo context 时，不产生空噪声消息。

完成 PLAN05 后，整个“仓库理解与上下文选择能力”阶段才算闭环。接下来可以进入安全编辑与验证闭环阶段。

