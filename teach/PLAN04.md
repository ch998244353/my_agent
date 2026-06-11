# PLAN04 - 文件代码读取与轻量仓库检索工具教学计划

本计划是“仓库理解与上下文选择能力”的第四步。前三个计划已经建立了 workspace inventory、用户提及候选和 selected files 状态；PLAN04 的任务不是接入 CodeGraph，也不是建设复杂代码索引系统，而是把主流 Coding Agent 最常用的“文件代码读取方法”补齐。

本模块应该坚持简单：以 `Workspace` 安全边界为核心，复用 PLAN01 的 inventory，提供更适合代码任务的读取、搜索、片段定位和轻量 outline 能力。CodeGraph、LSP、tree-sitter、向量数据库、长期索引服务都不进入本阶段。它们以后可以作为独立 tool 插件或高级检索后端添加，但不应该污染当前 `my_agent` 的基础架构。

## 给新 Agent 的快速导航

先读这些 my_agent 文件：

- `my_agent/src/agents/workspace.py`
- `my_agent/src/agents/workspace_tools.py`
- `my_agent/src/agents/workspace_inventory.py`
- `my_agent/src/agents/context_mentions.py`
- `my_agent/src/agents/selected_files.py`
- `my_agent/src/agents/tools.py`
- `my_agent/src/agents/coding_agent.py`

再读这些参考项目文件：

- `reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/openaiagent/src/agents/tool.py`
- `reference/openaiagent/src/agents/function_schema.py`
- `reference/openaiagent/src/agents/tool_context.py`
- `reference/aider-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/aider-main/aider/repo.py`
- `reference/aider-main/aider/commands.py`
- `reference/aider-main/aider/coders/base_coder.py`
- `reference/aider-main/aider/coders/chat_chunks.py`
- `reference/aider-main/aider/repomap.py`
- `reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/mini-swe-agent-main/src/minisweagent/environments/local.py`
- `reference/mini-swe-agent-main/src/minisweagent/agents/default.py`
- `reference/OpenHands-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
- `reference/OpenHands-main/openhands/app_server/file_store/local.py`

openaiagent 的参考价值是工具 schema、工具上下文和工具边界。Aider 的参考价值是文件列表、已加入上下文文件、只读/可编辑文件和 repo map 的思想；本阶段只学习它“先找文件，再读片段，再压缩上下文”的方法，不复制完整 repo map 算法。mini-swe-agent 的参考价值是本地环境和命令边界。OpenHands 的参考价值是 workspace/file store 的隔离思想。

## 模块目标

完成后，`my_agent` 应该在现有 `WORKSPACE_READ` 能力内拥有更适合代码任务的读取工具。它应该能：

- 按路径安全读取完整文件或指定行号区间。
- 搜索 workspace 文本，并返回 path、line、匹配行和少量上下文行。
- 按文件名、扩展名或 glob-like pattern 从 inventory 中找文件候选。
- 对 Python 文件提取轻量 outline，例如 class、function、method 的名称和起始行。
- 根据 selected files 和 mention candidates 推荐相关文件，例如同名测试文件、同目录文件、源文件和测试文件互相映射。
- 把所有输出限制在明确的 `max_results`、`max_lines`、`max_chars` 边界内。

这个模块的重点是“读代码的方法变得可用”，不是“建立索引系统”。它可以称为 workspace code reading/search，但不要把它命名为 code index。

## 当前旧代码缺陷

当前 `workspace_tools.py` 已经有 `list_workspace_files`、`read_workspace_file` 和 `search_workspace_text`。这些工具是很好的基础，但对 Coding Agent 来说还不够顺手。

主要缺陷有四个：

第一，`read_workspace_file` 只能从文件开头裁剪，不能读取指定行号区间。真实代码任务经常需要“读第 120 到 180 行附近”，否则模型会收到过多无关内容。

第二，`search_workspace_text` 返回匹配行，但缺少 before/after context。真实 Coding Agent 常用 grep-like 搜索，但只看单行经常无法判断函数边界或调用语义。

第三，文件名查找还没有成为工具能力。PLAN02 可以识别 `context_chunks.py` 这样的候选，但 PLAN04 需要提供基于 inventory 的候选文件查找，帮助模型从 basename 或 pattern 定位真实路径。

第四，符号线索还没有轻量落点。当前阶段不做 CodeGraph，也不做完整语言服务，但可以用 Python 标准库 `ast` 给 Python 文件提取 class/function outline，满足教学项目的主流需求。

## 计划新增的 my_agent 内容

创建 `my_agent/src/agents/workspace_code.py`：

- `CodeLine`
- `CodeSearchMatch`
- `FileOutlineSymbol`
- `RelatedFileCandidate`
- `WorkspaceCodeReader`

创建 `my_agent/src/agents/workspace_code_tools.py`：

- `create_read_workspace_lines_tool(reader)`
- `create_search_workspace_code_tool(reader)`
- `create_find_workspace_files_tool(reader)`
- `create_outline_workspace_file_tool(reader)`
- `create_find_related_workspace_files_tool(reader)`
- `create_workspace_code_tools(reader)`

修改 `my_agent/src/agents/workspace_tools.py`：

- 保留现有三个 read-only 工具。
- 在 `create_readonly_workspace_tools(workspace)` 中追加 PLAN04 的增强读代码工具。
- 不新增 `CodingCapability.CODE_INDEX`。
- 不改变 `WORKSPACE_READ` 的语义，它仍然表示“只读 workspace 能力”。

按需修改 `my_agent/src/agents/coding_agent.py`：

- 通常不需要新增 capability。
- 如果工具注册路径需要调整，只在 `_register_workspace_read_tools()` 中保持调用 `create_readonly_workspace_tools()`。

新增测试：

- `my_agent/tests/test_workspace_code.py`
- `my_agent/tests/test_workspace_code_tools.py`

如果后续发现 `workspace_tools.py` 过大，可以只把新工具 factory 放入 `workspace_code_tools.py`，让 `workspace_tools.py` 做聚合入口。不要在本阶段重构全部 workspace 工具。

## 功能边界

必须做：

- 所有文件访问都经过 `Workspace.ensure_readable_path()` 或 PLAN01 inventory 的安全结果。
- 行号读取必须支持 `start_line`、`end_line`、`max_lines`，并返回是否 truncated。
- 搜索必须支持 `query`、`path`、`max_results`、`context_lines`。
- 文件候选查找必须复用 inventory，不直接无限遍历仓库。
- Python outline 使用 `ast`，解析失败时返回结构化错误或空结果，不抛出未捕获异常。
- 相关文件推荐只基于简单、可解释规则，例如同 basename、`tests/` 与 `src/` 映射、`test_*.py` 与 `*.py` 映射。
- 工具输出必须是稳定 dict，便于模型阅读和测试断言。

不能做：

- 不接入 CodeGraph。
- 不创建 `CodeIndexProvider`。
- 不创建 `CodeGraphIndexProvider`。
- 不新增 `CodingCapability.CODE_INDEX`。
- 不做 LSP、调用关系图、跨文件类型推断。
- 不做完整 Aider repo map 复刻。
- 不做向量 RAG、embedding、SQLite FTS 或持久化索引。
- 不读取 workspace 外路径。
- 不把搜索结果直接注入 prompt；PLAN05 才负责 repo context chunk。

## 课程 1：建立 workspace code 数据结构

优化目标：让代码读取结果有统一、可测试的数据结构。

执行标准：测试能构造 `CodeLine`、`CodeSearchMatch`、`FileOutlineSymbol`、`RelatedFileCandidate`，并稳定转成 dict。

旧代码缺陷：当前工具直接返回临时 dict，后续 repo context 难以复用。

新增能力：统一表示行号、文本、匹配上下文、outline symbol 和相关文件候选。

功能边界：本课不读取真实文件，不搜索 workspace。

大致修改方案：新建 `workspace_code.py`，定义小型 dataclass 和 `to_dict()`。新增业务代码控制在 60 到 80 行。

参考代码位置：

- `my_agent/src/agents/contracts.py` 的轻量数据结构风格。
- `reference/openaiagent/src/agents/tool.py` 的工具输出需要稳定结构。

## 课程 2：实现指定行号读取

优化目标：让 Agent 可以读取文件局部片段，而不是只能从文件头裁剪。

执行标准：测试创建一个多行 Python 文件，调用 `WorkspaceCodeReader.read_lines("pkg/a.py", start_line=3, end_line=7)` 返回第 3 到 7 行，包含 path、start_line、end_line、lines、truncated。

旧代码缺陷：`read_workspace_file` 只能按 `max_chars` 裁剪完整文件，不适合定位函数附近上下文。

新增能力：`read_lines(path, start_line, end_line=None, max_lines=120)`。

功能边界：只读取 UTF-8 文本；二进制或解码失败返回结构化错误；不自动扩大到函数边界。

大致修改方案：在 `WorkspaceCodeReader` 中保存 `workspace`，先调用 `workspace.ensure_readable_path(path)`，再按行切片。行号从 1 开始，非法行号归一化到安全范围。新增业务代码控制在 60 到 80 行。

参考代码位置：

- `my_agent/src/agents/workspace_tools.py` 的 `create_read_workspace_file_tool()`。
- `reference/aider-main/aider/coders/base_coder.py` 中围绕已选文件读取内容的思想。

## 课程 3：实现 grep-like 文本搜索

优化目标：让搜索结果带有上下文行，便于模型判断命中位置。

执行标准：测试创建多个文本文件，搜索 `needle` 时返回 path、line、text、before、after；`max_results` 生效；不可读路径不会出现在结果中。

旧代码缺陷：`search_workspace_text` 只有单行 match，缺少上下文。

新增能力：`search_text(query, path=".", max_results=50, context_lines=2)`。

功能边界：使用 Python 文件扫描即可，不要求调用 `rg`。如果以后要用 `rg`，应作为内部优化或独立插件，不改变本课接口。

大致修改方案：复用 `Workspace.ensure_readable_path()` 和 inventory 过滤结果；逐文件读取 UTF-8；找到命中后截取前后文；达到 `max_results` 立即截断。新增业务代码控制在 70 到 90 行，如果超过就把上下文切片拆成 helper。

参考代码位置：

- `my_agent/src/agents/workspace_tools.py` 的 `create_search_workspace_text_tool()`。
- `reference/mini-swe-agent-main/src/minisweagent/environments/local.py` 的本地命令/输出边界思想。

## 课程 4：实现文件候选查找

优化目标：把 PLAN02 的文件名候选落到真实 workspace 文件。

执行标准：测试用 inventory fixture 创建 `src/agents/context_chunks.py`、`tests/test_context_chunks.py`，调用 `find_files("context_chunks.py")` 能返回稳定候选列表；pattern 搜索能按扩展名或路径片段过滤。

旧代码缺陷：mention detector 能识别用户说了某个文件名，但工具层没有直接支持“按文件名找候选”。

新增能力：`find_files(query, max_results=20)`，支持完整相对路径、basename、路径片段和简单通配符。

功能边界：不做模糊编辑距离，不做 embedding 相似度。

大致修改方案：复用 `build_workspace_inventory()` 的 entries；建立临时的 path/basename 匹配逻辑；完整路径优先，basename 次之，片段匹配最后。新增业务代码控制在 60 到 80 行。

参考代码位置：

- `my_agent/src/agents/workspace_inventory.py`。
- `reference/aider-main/aider/repo.py` 的仓库文件列表思想。

## 课程 5：实现 Python 文件 outline

优化目标：给符号候选一个轻量落点，但不引入外部索引。

执行标准：测试创建 Python 文件，包含 class、method、function；`outline_file("pkg/a.py")` 返回名称、kind、line、parent；语法错误文件返回 error，不导致工具崩溃。

旧代码缺陷：PLAN02 识别出的 `RepoContextBuilder`、`build_turn_context` 这类符号候选没有轻量确认方式。

新增能力：`outline_file(path, max_symbols=80)`。

功能边界：只支持 Python 标准库 `ast`。非 Python 文件返回 unsupported。不要做跨文件引用、调用关系、继承分析。

大致修改方案：读取文件文本，调用 `ast.parse()`；遍历 `ClassDef`、`FunctionDef`、`AsyncFunctionDef`；方法的 parent 记录 class 名；按行号排序并截断。新增业务代码控制在 70 到 90 行。

参考代码位置：

- `reference/aider-main/aider/repomap.py` 的“从代码结构提取上下文”的思想。
- Python 标准库 `ast`，不引入新依赖。

## 课程 6：实现相关文件推荐

优化目标：让 selected files 能自然扩展到测试文件或源文件候选。

执行标准：测试给定 `src/foo/bar.py`，inventory 中存在 `tests/test_bar.py`、`src/foo/test_bar.py`、`src/foo/bar_test.py` 时，`find_related_files("src/foo/bar.py")` 返回这些候选并包含 reason。

旧代码缺陷：selected files 只能保存已有文件，不能根据常见代码项目结构推荐下一步该读的文件。

新增能力：`find_related_files(path, max_results=20)`。

功能边界：只做可解释规则，不做项目级复杂推断。

大致修改方案：从输入 path 提取 stem、suffix、目录；基于 inventory 匹配同 stem、`test_` 前缀、`_test` 后缀、`tests/` 目录、相邻目录。结果按 reason priority 和路径排序。新增业务代码控制在 60 到 80 行。

参考代码位置：

- `reference/aider-main/aider/coders/base_coder.py` 的已选文件集合思想。
- `reference/aider-main/aider/coders/chat_chunks.py` 的上下文组织思想。

## 课程 7：包装 workspace code tools 并接入 WORKSPACE_READ

优化目标：让模型能通过工具调用这些增强读取能力，同时保持现有 capability 简单。

执行标准：`create_readonly_workspace_tools(workspace)` 返回原有 list/read/search 工具，并追加 `read_workspace_lines`、`search_workspace_code`、`find_workspace_files`、`outline_workspace_file`、`find_related_workspace_files`。测试能用工具 registry 找到这些工具并用临时 workspace 执行。

旧代码缺陷：现有 `WORKSPACE_READ` 只暴露基础读文件工具，Coding Agent 做代码任务时需要频繁读过多内容。

新增能力：`workspace_code_tools.py` 中的工具 factory。

功能边界：工具只读，不修改文件，不运行 shell，不调用外部索引服务。

大致修改方案：新建 `workspace_code_tools.py`，风格参考 `workspace_tools.py`。在 `workspace_tools.create_readonly_workspace_tools()` 中创建 `WorkspaceCodeReader(workspace)` 并合并增强工具列表。通常不需要修改 `coding_agent.py`。新增业务代码控制在 70 到 100 行。

参考代码位置：

- `my_agent/src/agents/workspace_tools.py`。
- `reference/openaiagent/src/agents/function_schema.py`。
- `reference/openaiagent/src/agents/tool_context.py`。

## 本模块完成标准

本模块完成时，新 Agent 应该能证明：

1. 没有新增 CodeGraph、CodeIndexProvider、外部索引服务或新 capability。
2. `WORKSPACE_READ` 下的工具仍然全部只读，并受 `Workspace` 安全边界控制。
3. 可以读取指定文件行号区间。
4. 可以做 grep-like 文本搜索，并返回匹配上下文。
5. 可以按 basename、路径片段或简单 pattern 查找文件候选。
6. 可以对 Python 文件生成轻量 outline。
7. 可以根据源文件和测试文件命名规则推荐相关文件。
8. 所有工具都有稳定 dict 输出和 deterministic tests。

完成本模块后，下一步进入 PLAN05。PLAN05 会把 selected files、mentions、workspace code search/read 结果组织成 repo context chunk，而不是接入任何外部代码索引。
