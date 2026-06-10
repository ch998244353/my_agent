# PLAN04 - CodeGraph Repo Context Tools 教学计划

本计划是“仓库理解与上下文选择能力”的第四步。前三个计划建立了文件结构、文本线索和 selected files 状态；PLAN04 开始让 Agent 拥有“查代码索引”的工具能力。这个模块的重点不是把查询结果直接塞进 prompt，而是先建立可替换的 code index provider 和工具封装。

项目 `AGENTS.md` 已明确说明本项目使用 CodeGraph，并要求 Codex 在符号搜索、调用关系、影响分析、项目结构查询时使用 CodeGraph。这里要把这种能力变成 `my_agent` 自己的工具层能力，同时保证 CodeGraph 不可用时不会让 Agent 崩溃。

## 给新 Agent 的快速导航

先读这些 my_agent 文件：

- `my_agent/src/agents/tools.py`
- `my_agent/src/agents/workspace_tools.py`
- `my_agent/src/agents/coding_agent.py`
- `my_agent/src/agents/workspace.py`

再读这些参考文件：

- `AGENTS.md`
- `reference/aider-main/aider/repomap.py`
- `reference/openaiagent/src/agents/tool.py`
- `reference/mini-swe-agent-main/src/minisweagent/__init__.py`

Aider 的参考价值是 repo map 和符号相关上下文选择。mini-swe-agent 的参考价值是用 Protocol 把 Agent、Model、Environment 解耦。openaiagent 的参考价值是工具 schema 和工具来源清晰。

## 模块目标

完成后，`my_agent` 应该有一个统一的 `CodeIndexProvider` 协议。它至少支持：

- 查询索引状态。
- 列出索引文件或 workspace 文件。
- 文本搜索。
- 符号搜索。

同时，`my_agent` 应该能把 provider 包装成 FunctionTool，让模型可以调用：

- `code_index_status`
- `search_workspace_code`
- `find_workspace_symbols`

如果 CodeGraph 不可用，工具必须返回结构化 unavailable 或 fallback 结果，而不是抛出未捕获异常。

## 当前旧代码缺陷

当前 `workspace_tools.py` 只有文件级 read/search。它能搜索文本，但不能表达“这是符号定义”“这是调用关系入口”“索引当前是否可用”。对一个 Coding Agent 来说，真实能力不只来自读文件，还来自知道该查哪个符号、哪个文件、哪个调用关系。

另外，直接把 CodeGraph 调用写进工具 handler 会让后续测试困难。因此本计划先抽象 provider，再写工具。

## 计划新增的 my_agent 内容

创建 `my_agent/src/agents/code_index.py`：

- `CodeIndexStatus`
- `CodeIndexFile`
- `CodeIndexMatch`
- `CodeIndexProvider` Protocol
- `RipgrepIndexProvider`
- `CodeGraphIndexProvider`

创建 `my_agent/src/agents/code_index_tools.py`：

- `create_code_index_status_tool(provider)`
- `create_search_workspace_code_tool(provider)`
- `create_find_workspace_symbols_tool(provider)`
- `create_code_index_tools(provider)`

修改 `my_agent/src/agents/coding_agent.py`：

- 新增 `CodingCapability.CODE_INDEX`。
- 新增 capability pack，将 code index tools 注册到 Agent。

新增测试：

- `my_agent/tests/test_code_index.py`
- `my_agent/tests/test_code_index_tools.py`

## 功能边界

必须做：

- provider 协议稳定。
- CodeGraph 不可用时有 graceful fallback。
- 工具输出结构化 dict。
- fake provider 可用于单测。
- `coding_agent.py` 可选择启用 code index capability。

不能做：

- 不在本计划注入 prompt。
- 不做 repo context builder。
- 不做复杂 ranking。
- 不依赖真实 LLM。
- 不要求测试环境一定安装 CodeGraph。

## 课程 1：定义 code index 数据结构

优化目标：让索引查询结果有统一表示。

执行标准：测试能构造 status、file、match 对象，并序列化成 dict。

新增能力：`CodeIndexStatus` 表示 available、backend、message；`CodeIndexMatch` 表示 path、line、symbol、text、score。

功能边界：不查询真实索引。

大致修改方案：新建 `code_index.py`，先写 dataclass 和 `to_dict()`。新增代码控制在 60 到 80 行。

参考代码：`my_agent/src/agents/contracts.py`。

## 课程 2：定义 provider 协议和 fake provider

优化目标：让工具层不依赖具体索引实现。

执行标准：测试能用 fake provider 返回固定 files、text matches、symbol matches。

新增能力：`CodeIndexProvider` Protocol。

功能边界：fake provider 只用于测试，不放进正式 capability。

大致修改方案：定义 `status()`、`files()`、`search_text(query, paths=None, limit=20)`、`search_symbols(query, limit=20)`。新增代码控制在 60 到 80 行。

参考代码：mini-swe-agent 的 `Model`、`Environment` Protocol。

## 课程 3：实现 rg fallback 文本搜索

优化目标：即使没有 CodeGraph，也能进行基础代码文本搜索。

执行标准：在临时 workspace 中创建文件，`RipgrepIndexProvider.search_text("needle")` 能返回 path、line、text。

新增能力：`RipgrepIndexProvider.search_text()`。

功能边界：本课不实现符号搜索。符号搜索可返回 unavailable 或空结果。

大致修改方案：使用 `subprocess.run(["rg", "--line-number", "--column", query, root])` 或 PowerShell 环境可用的等价调用。注意不要 shell 拼接用户输入。解析 `path:line:column:text`。新增代码控制在 60 到 80 行。

参考代码：Aider repo search 思想；my_agent `environment.py` 的命令结果结构。

## 课程 4：实现文件结构查询

优化目标：provider 能返回 workspace 文件结构。

执行标准：provider.files() 能返回 PLAN01 inventory 中 readable 文件。

新增能力：`files()` 统一接口。

功能边界：不做 PageRank，不做复杂排序。

大致修改方案：让 `RipgrepIndexProvider` 或基础 provider 复用 `build_workspace_inventory()`。新增代码控制在 60 到 80 行。

参考代码：PLAN01。

## 课程 5：实现 CodeGraph provider 外壳

优化目标：为 CodeGraph 接入建立边界。

执行标准：在没有 `.codegraph` 或没有 `codegraph` CLI 时，`status()` 返回 available false；有 CLI 时能返回基本状态文本。

新增能力：`CodeGraphIndexProvider`。

功能边界：不要在本课深度解析 CodeGraph SQLite 内部结构。优先通过 CLI 或简单存在性检查。

大致修改方案：检测 workspace 下 `.codegraph/codegraph.db`，检测 `codegraph` 命令是否可用。`status()` 返回结构化结果。`search_symbols()` 可以先调用 `codegraph query` 并解析最小输出；如果解析不稳定，先返回 raw text match 也可以，但必须测试 unavailable 分支。新增代码控制在 60 到 80 行。

参考代码：项目 `AGENTS.md` 的 CodeGraph 规则。

## 课程 6：包装 FunctionTools

优化目标：让模型可以通过工具调用 code index。

执行标准：tool specs 中出现 `code_index_status`、`search_workspace_code`、`find_workspace_symbols`，fake provider 测试能得到结构化输出。

新增能力：`create_code_index_tools(provider)`。

功能边界：工具只查询，不修改文件。

大致修改方案：新建 `code_index_tools.py`，风格参考 `workspace_tools.py`。每个工具捕获 provider unavailable，并返回 dict。新增代码控制在 60 到 80 行。

参考代码：`my_agent/src/agents/workspace_tools.py`。

## 课程 7：增加 CodingCapability.CODE_INDEX

优化目标：让 coding agent profile 能声明式启用代码索引能力。

执行标准：构建带 code index capability 的 coding agent 时，tool registry 包含 code index tools。

新增能力：`CodingCapability.CODE_INDEX` 和 capability pack。

功能边界：不要默认强制所有 coding agent 启用 code index；可以由 profile 控制。

大致修改方案：修改 `coding_agent.py`，增加 capability enum、registrar 和 profile 测试。新增代码控制在 40 到 70 行。

参考代码：`my_agent/src/agents/coding_agent.py` 当前 WORKSPACE_READ/SHELL/TEST/EDIT capability pack。

## 本模块完成标准

本模块完成时，新 Agent 应该能证明：

1. `CodeIndexProvider` 协议存在且可用 fake provider 测试。
2. `RipgrepIndexProvider` 可以在没有 CodeGraph 时提供基础文本搜索。
3. `CodeGraphIndexProvider` 不可用时不会导致 run 崩溃。
4. code index tools 输出结构化结果。
5. `CodingCapability.CODE_INDEX` 可以注册这些工具。

完成本模块后，下一步进入 PLAN05，把 selected files、mentions 和 code index 查询结果组织成 repo context chunk。

