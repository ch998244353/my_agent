# PLAN02 - File Mention Detection 教学计划

本计划是“仓库理解与上下文选择能力”的第二步。PLAN01 让 Agent 知道 workspace 里有什么；PLAN02 要让 Agent 能从用户的话里识别“用户可能在说哪些文件、哪些测试、哪些符号”。这一步仍然不做自动选择，也不做外部索引查询，它只产出候选线索。

这个模块非常适合教学，因为它能解释 Coding Agent 和普通聊天 Agent 的区别：用户经常不会给完整路径，只会说“改 runner”、“看一下 build_turn_context”、“测试 run_state”。如果 Agent 不能把这些自然语言线索转成候选文件或符号，后续 repo context 就没有起点。

## 给新 Agent 的快速导航

先读这些 my_agent 文件：

- `my_agent/src/agents/workspace_inventory.py`，这是 PLAN01 的产物。
- `my_agent/src/agents/workspace.py`
- `my_agent/src/agents/context_chunks.py`

再读这些参考项目文件：

- `reference/aider-main/aider/coders/base_coder.py`
- `reference/aider-main/aider/io.py`
- `reference/aider-main/aider/commands.py`

Aider 的核心启发是：文件上下文不是一次性自动决定的，而是持续从用户输入、模型回复、命令操作中发现和确认。PLAN02 只实现“发现候选”，确认和状态维护放到 PLAN03。

## 模块目标

完成后，`my_agent` 应该能对一段文本返回一组 `MentionCandidate`。候选类型至少包括：

- 显式路径，例如 `src/agents/run_loop.py`。
- 反引号包裹的路径，例如 `` `src/agents/context_chunks.py` ``。
- Windows 风格路径，例如 `src\agents\run_loop.py`。
- 文件名，例如 `run_loop.py`、`test_context_chunks.py`。
- 测试路径或测试文件名。
- 简单符号名，例如 `RunState`、`build_turn_context`。

候选必须是确定性的。不要调用 LLM，不要调用 shell，不要读取真实文件内容。

## 当前旧代码缺陷

当前 `my_agent` 有 workspace read/search 工具，但这些工具都需要模型已经知道要读什么。用户说“看看 runner 相关逻辑”时，系统没有任何模块负责把 `runner` 和 `run_loop.py`、`runner.py`、`Runner` 等候选联系起来。

这会导致模型要么猜文件名，要么先调用宽泛搜索工具。PLAN02 通过轻量规则先提供候选线索，降低盲目探索成本。

## 计划新增的 my_agent 内容

创建 `my_agent/src/agents/context_mentions.py`，放置以下内容：

- `MentionCandidate`：候选对象，包含 `text`、`kind`、`confidence`、`matched_path`、`source`。
- `detect_file_mentions(text)`：只从文本中提取候选，不依赖 workspace。
- `resolve_mentions_against_inventory(text, inventory)`：结合 PLAN01 的 inventory，将候选映射到实际 path。
- 私有 helper：路径规范化、重复候选合并、token 过滤。

新增测试：

- `my_agent/tests/test_context_mentions.py`

按需要修改 `my_agent/src/agents/__init__.py`，但不要急着导出太多 helper。

## 功能边界

必须做：

- 识别显式路径。
- 识别反引号路径。
- 识别常见文件名。
- 识别测试文件名。
- 识别简单 Python 符号 token。
- 能和 `WorkspaceInventory` 匹配。
- 输出顺序稳定并去重。

不能做：

- 不调用外部索引或复杂检索工具。
- 不做 fuzzy search 的复杂排名。
- 不自动加入 selected files。
- 不修改 prompt。
- 不根据 mention 直接读文件。

## 课程 1：定义 MentionCandidate

优化目标：建立候选线索的数据模型。

执行标准：测试能构造候选对象，能比较、去重、转 dict，并且默认 `matched_path` 可以为空。

新增能力：`MentionCandidate` 至少包含 `text`、`kind`、`confidence`、`matched_path`、`source`。`kind` 推荐先用字符串字面量：`path`、`filename`、`test`、`symbol`。

功能边界：本课不解析文本。

大致修改方案：新建 `context_mentions.py`，使用 frozen dataclass。增加简单的 `normalized_text` 或私有规范化函数，方便去重。新增代码控制在 50 到 70 行。

参考代码：`my_agent` 现有 dataclass 风格。

## 课程 2：识别显式路径

优化目标：从文本中找出最可靠的路径候选。

执行标准：输入 `请修改 src/agents/run_loop.py` 能返回 path candidate；输入反引号路径也能返回；Windows 反斜杠会规范化为 `/`。

新增能力：`detect_file_mentions()` 的路径规则。

功能边界：本课不验证路径是否存在，也不匹配 inventory。

大致修改方案：使用保守 regex 提取包含 `/` 或 `\` 且带扩展名的 token。去掉外层标点、反引号和引号。新增代码控制在 50 到 70 行。

参考代码：Aider `base_coder.py` 中 file mention 的思路，不需要照搬实现。

## 课程 3：识别文件名和测试名

优化目标：用户只说文件名时也能产生候选。

执行标准：`test_runner.py` 被识别为 `test` candidate；`runner.py` 被识别为 `filename` candidate。

新增能力：识别带常见代码扩展名的文件名 token，例如 `.py`、`.md`、`.json`、`.toml`、`.yaml`、`.yml`。

功能边界：不做模糊匹配，不把普通词误判成文件。

大致修改方案：增加 filename regex。对 `test_` 开头或 `/tests/` 语义的候选标记为 `test`。新增代码控制在 50 到 70 行。

参考代码：Aider 的 `/add` 文件体验。

## 课程 4：识别简单符号 token

优化目标：为 PLAN04 的轻量代码读取和 outline 查询提供入口。

执行标准：`RunState`、`WorkspaceInventory`、`build_turn_context` 能被识别为 symbol candidate；普通中文或英文短词不会大量进入结果。

新增能力：基于命名规则的 symbol candidate。

功能边界：不查定义，不判断符号是否真实存在。

大致修改方案：提取符合 Python 命名习惯的 token。推荐优先保留 CamelCase 和 snake_case 带下划线的 token；过滤短词、常见英文停用词和纯数字。新增代码控制在 50 到 70 行。

参考代码：后续轻量 symbol outline/search 的输入形态。

## 课程 5：与 WorkspaceInventory 匹配

优化目标：把文本候选映射到真实 workspace 文件。

执行标准：inventory 中存在 `src/agents/run_loop.py` 时，文本 `run_loop.py` 能匹配到该相对路径；重复候选只保留一次。

新增能力：`resolve_mentions_against_inventory(text, inventory)`。

功能边界：不要在这里自动读取文件内容，也不要写 selected files。

大致修改方案：从 inventory entries 建两个索引：完整相对路径索引和 basename 索引。完整路径优先，basename 多命中时可以返回多个候选或降低 confidence。新增代码控制在 60 到 80 行。

参考代码：PLAN01 的 `WorkspaceInventory`。

## 课程 6：稳定导出和边界测试

优化目标：让 mention detector 能被后续计划稳定调用。

执行标准：空输入返回空列表；重复 mention 去重；中文任务中夹杂路径或符号时仍能识别。

新增能力：稳定 API 和完整边界测试。

功能边界：不引入 LLM，不引入 shell。

大致修改方案：补 `__all__`，补测试覆盖。新增代码控制在 30 到 50 行。

参考代码：`my_agent/src/agents/__init__.py` 的导出风格。

## 本模块完成标准

本模块完成时，新 Agent 应该能证明：

1. `detect_file_mentions()` 是纯函数。
2. 显式路径、文件名、测试名和符号名都能被识别。
3. 候选结果顺序稳定、重复项合并。
4. `resolve_mentions_against_inventory()` 能把候选映射到真实 workspace path。
5. 没有任何真实 LLM、shell、外部索引依赖。

完成本模块后，下一步进入 PLAN03，把 mention 候选转成可维护的 selected files 状态。
