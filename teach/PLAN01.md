# PLAN01 - Workspace Inventory 教学计划

本计划属于“仓库理解与上下文选择能力”阶段的第一步。新 Agent 接手时要先理解这一点：这个模块不是为了让 `my_agent` 立刻更聪明地改代码，而是先建立一个可靠的仓库观察层。后续的文件提及识别、selected files、CodeGraph 检索、repo context 都依赖它。

当前 `my_agent` 已经有 `Workspace` 和 `workspace_tools`。`Workspace` 负责 root、allowed paths、ignore patterns 和安全路径解析；`workspace_tools.py` 里已经有 `list_workspace_files`、`read_workspace_file`、`search_workspace_text`。问题是这些能力还停留在“工具 handler 内部逻辑”的层面，不能作为 prompt context builder、文件选择状态、仓库摘要的共同基础。因此本模块要把“列出 workspace 结构”抽成可复用的 inventory 模块。

## 给新 Agent 的快速导航

先读这些 my_agent 文件：

- `my_agent/src/agents/workspace.py`
- `my_agent/src/agents/workspace_tools.py`
- `my_agent/src/agents/context_chunks.py`
- `my_agent/tests/test_workspace.py`
- `my_agent/tests/test_workspace_tools.py`

再读这些参考项目文件：

- `reference/aider-main/aider/repo.py`
- `reference/aider-main/aider/coders/base_coder.py`
- `reference/OpenHands-main/openhands/app_server/app_conversation/app_conversation_service_base.py`

Aider 的价值在于它不会把整个仓库都塞进 prompt，而是先知道哪些文件存在、哪些文件被用户加入上下文、哪些文件只读。OpenHands 的价值在于它把 workspace 准备状态显式化。`my_agent` 目前缺的是这层中间结构。

参考项目只借鉴边界和数据流思想，不复制完整实现。Aider 的文件集合、只读文件和 repo map 说明“仓库清单”和“上下文预算”应该分层；OpenHands 的 workspace 准备流程说明“工作区状态”应该显式表达。PLAN01 只实现轻量 inventory，不引入 Git tracked files、token budget、setup script 或会话状态机。

## 模块目标

完成后，`my_agent` 应该能生成一个结构化的 workspace 清单。这个清单必须说明：

- workspace root 是什么。
- 从哪个 path 开始扫描。
- 每个条目是文件还是目录。
- 每个条目的相对路径是什么。
- 文件大小是多少。
- 条目是否 readable。
- 条目是否因为 ignore 或 allowed paths 被排除。
- 输出是否因为 `max_entries` 或 `max_depth` 被截断。

这个模块不应该读取文件内容，不应该做符号索引，不应该做文件自动选择，也不应该做 Git diff。它只解决“仓库里有什么，以及这些路径是否安全可见”。

结构化输出契约要从课程 1 开始固定：`WorkspaceFileEntry.to_dict()` 返回 `path`、`kind`、`size_bytes`、`readable`、`ignored`、`reason`；`WorkspaceInventory.to_dict()` 返回 `root`、`base_path`、`entries`、`truncated`。后续课程可以补充扫描来源和截断逻辑，但不要改变这些字段语义。

## 当前旧代码缺陷

`Workspace` 当前职责很清楚，但它是路径安全边界，不是仓库清单模型。`workspace_tools.py` 当前可以列文件，但输出逻辑直接写在工具函数里。这样会导致后续模块重复实现文件扫描逻辑：selected files 要扫一次，repo context 要扫一次，CLI `/files` 又要扫一次。

本计划要补足的不是一个新工具，而是一层可复用的 workspace inventory API。工具层只应该调用它。

## 计划新增的 my_agent 内容

创建 `my_agent/src/agents/workspace_inventory.py`，放置以下内容：

- `WorkspaceFileEntry`：表示一个文件或目录条目。
- `WorkspaceInventory`：表示一次扫描结果。
- `build_workspace_inventory()`：从 `Workspace` 安全扫描文件树。
- 私有辅助函数：负责排序、截断、相对路径渲染、ignore reason 生成。

修改 `my_agent/src/agents/workspace_tools.py`：

- 让 `create_list_workspace_files_tool()` 调用 `build_workspace_inventory()`。
- 保持原有工具输出兼容，但可以增加 `truncated`、`root`、`entries` 等结构化信息。

按需要修改 `my_agent/src/agents/__init__.py`：

- 如果后续教学需要公开类型，则导出 `WorkspaceInventory` 和 `WorkspaceFileEntry`。
- 如果不希望扩大 public API，可以只在模块内部使用。

新增测试：

- `my_agent/tests/test_workspace_inventory.py`
- 修改 `my_agent/tests/test_workspace_tools.py`

## 功能边界

必须做：

- 所有路径都通过 `Workspace.ensure_readable_path()` 或同等安全逻辑。
- 入口 path 必须先通过 `Workspace.ensure_readable_path()`；候选条目如需保留 excluded reason，可以使用 `resolve_path()`、`is_allowed()`、`is_ignored()` 这类同等安全逻辑计算 metadata，但不能绕过 workspace root 检查。
- 尊重 `allowed_paths`。
- 尊重 `ignore_patterns`。
- 输出顺序稳定，便于测试。
- 支持 `max_entries` 和 `max_depth`。
- 大目录要返回 `truncated=True`。

不能做：

- 不读取文件内容。
- 不做 token 预算。
- 不调用 CodeGraph。
- 不创建 selected files 状态。
- 不把 inventory 直接注入 prompt。

## 课程 1：建立 inventory 数据模型

优化目标：让 workspace 文件清单有独立、可测试、可序列化的数据结构。

执行标准：测试能构造 `WorkspaceFileEntry` 和 `WorkspaceInventory`，并能转成 dict 或 observation-friendly 结构。

新增能力：`WorkspaceFileEntry` 至少包含 `path`、`kind`、`size_bytes`、`readable`、`ignored`、`reason`。`WorkspaceInventory` 至少包含 `root`、`base_path`、`entries`、`truncated`。

大致修改方案：创建 `workspace_inventory.py`，使用 frozen dataclass。先不要写扫描逻辑，只写数据结构和序列化方法。新增代码控制在 60 到 80 行。

参考代码：`my_agent/src/agents/contracts.py` 中 dataclass 的简单风格。

## 课程 2：实现安全扫描入口

优化目标：提供 `build_workspace_inventory(workspace, path='.', max_entries=200, max_depth=None)`。

执行标准：给临时目录创建几个文件和目录，调用 builder 能返回稳定条目列表。

新增能力：扫描目录，但所有入口 path 必须经过 `workspace.ensure_readable_path()`。

功能边界：本课不处理 ignore reason 的精细解释，也不做复杂截断，只先得到安全扫描结果。

大致修改方案：使用 `Path.iterdir()` 读取目录，目录和文件都生成 `WorkspaceFileEntry`。排序按相对路径字符串。新增代码控制在 60 到 80 行。

参考代码：`my_agent/src/agents/workspace_tools.py` 当前 list 工具。

## 课程 3：加入 allowed 和 ignore 原因

优化目标：让 inventory 不只是列出文件，还能说明文件为什么不可读或被跳过。

执行标准：被 `.git`、`.codegraph`、`__pycache__` 或自定义 ignore pattern 命中的路径不会被标记为 readable。

新增能力：`reason` 字段能区分 `outside_allowed_paths`、`ignored_by_workspace_policy`、`ok` 等最小原因。

功能边界：不需要把每一种 ignore pattern 都展开成复杂解释，只要能让后续 Agent 知道“这个路径不应进入上下文”。

大致修改方案：对候选路径调用 `workspace.is_allowed()` 和 `workspace.is_ignored()`。如果路径不可读，可以保留条目但标记 readable false；也可以跳过不可读条目，但测试必须固定行为。推荐保留目录级不可读信息，文件级 ignored 可不展开递归。新增代码控制在 50 到 70 行。

实现细节补充：不要只用 `ensure_readable_path()` 一把跳过候选条目，否则无法给出 `outside_allowed_paths` 或 `ignored_by_workspace_policy` 的 reason。课程 3 应先确认候选路径仍在 workspace root 内，再用 `is_allowed()`、`is_ignored()` 标记 `readable`、`ignored` 和 `reason`；ignored 或不可读目录不要继续递归展开。

参考代码：`my_agent/src/agents/workspace.py`。

## 课程 4：加入 max_entries 和 max_depth

优化目标：防止大型仓库扫描结果无限增长。

执行标准：当条目超过 `max_entries` 时，inventory 标记 `truncated=True`，并且不会返回超过上限的 entries。设置 `max_depth=1` 时，不递归深层目录。

新增能力：可控的 workspace 文件树输出。

功能边界：这不是 token budget，只是扫描结果边界。真正的 prompt 字符预算放到 PLAN05。

大致修改方案：扫描函数维护计数器和深度参数。达到上限后停止继续追加。新增代码控制在 50 到 70 行。

参考代码：Aider `repomap.py` 中控制 repo map 大小的设计思想。

## 课程 5：改造 list_workspace_files 工具

优化目标：让现有 list 工具复用 inventory，而不是保留两套扫描逻辑。

执行标准：原有 `test_workspace_tools.py` 继续通过，新增测试能看到 inventory 的 `truncated` 和 entry metadata。

新增能力：工具输出和后续上下文构建共享同一个 inventory 结果。

功能边界：不修改 `read_workspace_file` 和 `search_workspace_text` 行为。

大致修改方案：在 `create_list_workspace_files_tool()` 内调用 `build_workspace_inventory()`，把结果转换为当前工具期望的 dict 格式。新增代码控制在 40 到 60 行。

兼容要求：旧工具输出中的 `path`、`entries`、`truncated` 必须保留原语义，`entries` 仍是相对路径字符串列表。新增的 inventory metadata 可以放在额外字段中，不能破坏现有调用方和旧测试。

参考代码：`my_agent/src/agents/workspace_tools.py`。

## 课程 6：整理 public API 和测试边界

优化目标：让后续计划可以稳定导入 inventory 类型。

执行标准：如果选择导出，则 `from agents import WorkspaceInventory` 可用；如果不导出，则后续计划必须使用模块路径导入。

新增能力：明确 inventory 是内部能力还是公开教学 API。

功能边界：不要一次性把太多内部 helper 暴露到 `__init__.py`。

大致修改方案：按测试需要最小修改 `__init__.py`，补充 `__all__`。新增代码控制在 40 到 60 行。

参考代码：`my_agent/src/agents/__init__.py`。

## 本模块完成标准

本模块完成时，新 Agent 应该能运行单元测试证明以下事实：

1. Inventory 是独立模块，不依赖 tool handler。
2. Inventory 永远受 `Workspace` 安全边界约束。
3. Ignore 和 allowed paths 生效。
4. 扫描输出稳定、可截断、可序列化。
5. `list_workspace_files` 工具复用 inventory，不破坏旧行为。

完成本模块后，下一步进入 PLAN02，用 inventory 结果去解析和匹配用户任务中的文件提及。
