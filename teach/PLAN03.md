# PLAN03 - Selected Files State 教学计划

本计划是“仓库理解与上下文选择能力”的第三步。PLAN01 让 Agent 知道仓库文件结构，PLAN02 让 Agent 从用户文本里发现候选文件和符号；PLAN03 要把这些候选变成可维护的任务状态：哪些文件是本轮关注文件，哪些只读，哪些允许编辑，为什么被加入。

这个模块的关键是“显式状态”。真实 Coding Agent 不能每一轮都临时猜上下文。它必须能维护一个稳定的 selected files state，并让 prompt、编辑权限、CLI 命令、repo context 都围绕这个状态工作。

## 给新 Agent 的快速导航

先读这些 my_agent 文件：

- `my_agent/src/agents/run_context.py`
- `my_agent/src/agents/context_chunks.py`
- `my_agent/src/agents/coding_agent.py`
- `my_agent/src/agents/model_turn.py`
- `my_agent/src/agents/workspace.py`

再读这些参考项目文件：

- `reference/aider-main/aider/coders/base_coder.py`
- `reference/aider-main/aider/commands.py`

Aider 的 `abs_fnames` 和 `abs_read_only_fnames` 是这个模块最重要的参考。不要照搬 Aider 的大类结构，只借鉴“可编辑文件”和“只读上下文文件”分开的思想。

## 模块目标

完成后，`my_agent` 应该能维护一个 `SelectedFilesState`。它至少能表达：

- 某个相对路径是否被选中。
- 选中文件是 `read_only` 还是 `editable`。
- 文件为什么被选中，例如 `mentioned_by_user`、`auto_selected`、`manual_add`。
- 文件来自哪个模块，例如 mention detector、CLI、repo context builder。
- selected files summary 可以进入模型输入。

这个模块不读取文件内容，不做外部索引查询，不做 Git diff，也不实现 CLI `/add` `/drop`。CLI 后续可以调用这里的 API。

## 当前旧代码缺陷

当前 `RunContextWrapper` 只有 workspace、environment、verification runner 和 tool approval 状态。它不知道当前任务关注哪些文件。`context_chunks.py` 里已经预留 `selected_files` 常量，但没有实际内容来源。

如果没有 selected files state，后续会出现三个问题：

1. prompt 里无法稳定展示“当前关注文件”。
2. apply_patch 无法轻易限制只能改 editable files。
3. CLI 和自动上下文选择无法共享同一个状态。

## 计划新增的 my_agent 内容

创建 `my_agent/src/agents/selected_files.py`：

- `SelectedFile`：单个选中文件。
- `SelectedFilesState`：管理选中文件集合。
- `add_file()`、`drop_file()`、`list_files()`、`summary()`。
- `add_mentions()`：可选 helper，用 PLAN02 的 mention 结果加入状态。

修改 `my_agent/src/agents/run_context.py`：

- 增加 `CONTEXT_SELECTED_FILES_KEY = "selected_files"`。
- 增加 `selected_files` property。

修改 `my_agent/src/agents/context_chunks.py`：

- 在 `build_turn_context()` 中读取 selected files。
- 渲染 `selected_files` chunk。

修改 `my_agent/src/agents/coding_agent.py`：

- `build_coding_agent()` 默认在 context 中放入空的 `SelectedFilesState`。

新增测试：

- `my_agent/tests/test_selected_files.py`
- 修改 `my_agent/tests/test_context_chunks.py`

## 功能边界

必须做：

- 区分 read-only 和 editable。
- 记录 reason 和 source。
- 支持 add、drop、list、summary。
- 注入 `RunContextWrapper`。
- 注入 prompt chunk。

不能做：

- 不实现 CLI 命令。
- 不读取文件内容。
- 不自动调用外部检索工具。
- 不执行编辑权限拦截，这属于后续安全编辑方案。

## 课程 1：建立 SelectedFile 数据模型

优化目标：让单个选中文件有明确类型和原因。

执行标准：测试能创建 read-only 和 editable 条目，并能转 dict 或 summary line。

新增能力：`SelectedFile(path, mode, reason, source)`。`mode` 推荐先支持 `read_only` 和 `editable`。

功能边界：不管理集合，不接 context。

大致修改方案：新建 `selected_files.py`，定义 frozen dataclass，做最小字段校验。新增代码控制在 60 到 80 行。

参考代码：Aider chat files 和 read-only files 的区分。

## 课程 2：实现 SelectedFilesState

优化目标：提供集合级 add/drop/list 能力。

执行标准：重复添加同一文件不会产生重复条目；drop 后文件不再出现；按路径排序输出稳定。

新增能力：`SelectedFilesState`。

功能边界：不校验真实路径是否存在。路径存在性由调用方结合 inventory 或 workspace 校验。

大致修改方案：内部用 dict keyed by normalized path。提供 `add_file()`、`drop_file()`、`get()`、`files()`、`summary()`。新增代码控制在 60 到 80 行。

参考代码：Aider `/add` 和 `/drop` 的状态管理思想。

## 课程 3：接入 RunContextWrapper

优化目标：让 selected files 成为 run context 的一等状态。

执行标准：`RunContextWrapper(context={...}).selected_files` 能返回 `SelectedFilesState`，类型不匹配时返回 None。

新增能力：`CONTEXT_SELECTED_FILES_KEY` 和 property。

功能边界：不改 run loop，不改 Runner。

大致修改方案：修改 `run_context.py`，仿照 `workspace`、`environment`、`verification_runner` property 增加 selected files。新增代码控制在 50 到 70 行。

参考代码：`my_agent/src/agents/run_context.py`。

## 课程 4：在 build_coding_agent 中初始化状态

优化目标：创建 coding agent 时默认带有空 selected files state。

执行标准：`build_coding_agent()` 返回的 `run_config.context` 中包含 selected files state。

新增能力：coding setup 拥有上下文选择状态槽。

功能边界：不自动选择任何文件。

大致修改方案：修改 `coding_agent.py`，在 context dict 中加入 `CONTEXT_SELECTED_FILES_KEY`。新增代码控制在 50 到 70 行。

参考代码：`my_agent/src/agents/coding_agent.py` 当前注入 workspace/environment 的方式。

## 课程 5：渲染 selected_files chunk

优化目标：让模型输入看见当前关注文件。

执行标准：当 selected files 非空时，`prepare_turn_input()` 的 messages 中出现 selected files 摘要；为空时不产生噪声消息。

新增能力：`selected_files` context chunk。

功能边界：只展示路径、mode、reason，不读取文件内容。

大致修改方案：修改 `context_chunks.py`，让 `build_turn_context()` 可以接收或读取 run context。当前 `build_turn_context(agent)` 只接收 agent，因此可能需要调整为 `build_turn_context(agent, context_wrapper=None)`，并同步修改 `model_turn.prepare_turn_input()`。新增代码控制在 60 到 80 行。

参考代码：Aider `ChatChunks` 的分层 prompt 思想。

## 课程 6：把 mention 候选加入 selected state

优化目标：让 PLAN02 和 PLAN03 串起来。

执行标准：给一组带 `matched_path` 的 mention candidates，helper 能把它们加入 selected files state，reason 为 `mentioned_by_user`。

新增能力：`add_mentions()` 或 `SelectedFilesState.add_mentions()`。

功能边界：不要自动把所有 symbol candidates 都加入 selected files。只有 matched_path 明确的候选才能加入。

大致修改方案：在 `selected_files.py` 中添加 helper。新增代码控制在 50 到 70 行。

参考代码：PLAN02 的 `MentionCandidate`。

## 本模块完成标准

本模块完成时，新 Agent 应该能证明：

1. `SelectedFilesState` 能稳定管理 read-only 和 editable 文件。
2. selected files 存在于 `RunContextWrapper`。
3. `build_coding_agent()` 默认准备 selected files 状态。
4. `prepare_turn_input()` 能把 selected files 作为独立 chunk 注入模型输入。
5. PLAN02 的 matched mentions 可以被转为 selected files。

完成本模块后，下一步进入 PLAN04，为 selected files 和 symbol mentions 提供文件代码读取与轻量仓库检索能力。
