# verification - 仓库理解与上下文选择能力总验证要求

本文件用于检查 `teach/PLAN01.md` 到 `teach/PLAN05.md` 全部完成后，`my_agent` 是否真正获得“仓库理解与上下文选择能力”。它不是单个模块的测试清单，而是跨模块验收标准。新 Agent 在开始实现前应该先读本文件，明确最终要证明什么。

最终目标是：当用户提出一个本地代码任务时，`my_agent` 不再只依赖模型猜测和临时工具调用，而是能先建立一份可审计、可测试、可裁剪的仓库上下文。这个上下文应包含 workspace inventory、用户提及候选、selected files、code index 结果和最终 repo context chunk。

## 总体验收意图

本阶段通过的核心判断标准是“链路成立”，而不是“文件存在”。如果某个模块单测通过，但无法被后续模块消费，仍然不能认为阶段完成。

完整链路应该是：

用户任务文本进入系统后，mention detector 识别路径、文件名、测试名和符号候选；workspace inventory 提供安全可见的仓库结构；mention resolver 将候选尽量映射到真实文件；selected files state 保存本轮任务关注文件及其 read-only/editable/mentioned/auto-selected 语义；code index provider 通过 CodeGraph 或 fallback 查询补充线索；repo context builder 组合这些信息；最后 `prepare_turn_input()` 生成的模型输入中出现稳定的 `selected_files` 和 `repo_context` 内容。

验收时必须证明这条链路在 deterministic tests 中成立。不能只靠真实 LLM 对话手工观察。

## PLAN01 验收：Workspace Inventory

`PLAN01.md` 完成后，`my_agent` 必须能生成结构化 workspace 清单。

必须存在或等价实现 `my_agent/src/agents/workspace_inventory.py`。该模块至少应提供 `WorkspaceFileEntry`、`WorkspaceInventory` 和 `build_workspace_inventory`，命名可以略有差异，但职责不能缺失。

验收要求如下：

- inventory 能从 `Workspace` root 生成稳定排序的文件和目录条目。
- 每个条目能表达相对路径、文件/目录类型、文件大小、readable 状态和被排除原因。
- 扫描必须受 `Workspace` 的 allowed paths 和 ignore patterns 约束。
- 大目录必须有 `max_entries`、`max_depth` 或等价截断机制。
- 该模块不能读取文件内容，不能做代码索引，不能做任务相关性判断。
- 原有 `workspace_tools.py` 如需复用 inventory，必须保持既有工具行为兼容。

失败条件包括：直接用裸 `Path.rglob()` 暴露被 workspace 策略排除的路径；输出顺序依赖文件系统随机顺序；扫描无限目录；把文件内容读取逻辑放进 inventory。

## PLAN02 验收：Context Mentions

`PLAN02.md` 完成后，`my_agent` 必须能从用户任务文本中提取文件、路径、测试和符号候选，并能结合 inventory 做安全解析。

必须存在或等价实现 `my_agent/src/agents/context_mentions.py`。该模块至少应提供 `MentionCandidate`、`detect_file_mentions` 和 `resolve_mentions_against_inventory`。

验收要求如下：

- 能识别反引号路径，例如 `` `src/agents/context_chunks.py` ``。
- 能识别 Windows 风格路径和 Posix 风格路径。
- 能识别裸文件名，例如 `context_chunks.py`。
- 能识别常见测试文件或测试名提示，例如 `test_context_chunks.py`、`test repo context`。
- 能识别符号候选，例如 `build_turn_context` 或 `RepoContextBuilder`。
- 能对重复候选去重，并保留候选来源或 reason。
- resolve 过程必须只返回 inventory 中可见且安全的路径。
- 无法解析的符号候选可以保留为 unresolved，不应伪造成文件路径。

失败条件包括：把所有普通英文单词都当作符号；绕过 inventory 直接访问磁盘；中文任务中带路径时识别失败；同一文件因斜杠差异重复出现多次。

## PLAN03 验收：Selected Files State

`PLAN03.md` 完成后，`my_agent` 必须有显式 selected files 状态，用于表达本轮任务关注哪些文件，以及这些文件是只读参考还是允许编辑。

必须存在或等价实现 `my_agent/src/agents/selected_files.py`。该模块至少应提供 `SelectedFile` 和 `SelectedFilesState`。

验收要求如下：

- 能添加 selected file，并记录 path、mode、reason 和来源。
- 能区分 read-only、editable、mentioned、auto-selected 或等价语义。
- 重复添加同一路径时结果稳定，不应产生重复项。
- drop/remove 后，该文件不应继续进入 prompt context。
- selected files 的排序必须稳定。
- 不能把 read-only 文件自动升级成 editable。
- 必须能与 `RunContext` 或等价运行态对象衔接，让后续 context chunk 可以读取。

失败条件包括：selected files 只是临时 list，跨函数无法传递；mode 字段语义混乱；删除文件后 prompt 仍出现；selected files 模块直接读写文件内容。

## PLAN04 验收：Code Index Provider 与工具

`PLAN04.md` 完成后，`my_agent` 必须有统一代码索引查询接口，并能将其安全包装成模型可用工具。

必须存在或等价实现 `my_agent/src/agents/code_index.py` 和 `my_agent/src/agents/code_index_tools.py`。前者至少应包含 `CodeIndexProvider` 或等价协议；后者至少应包含 `create_code_index_tools` 或等价工具注册函数。

验收要求如下：

- provider 能表达 query files、query text、query symbols 或等价能力。
- CodeGraph 可用时，可以通过 provider 调用 CodeGraph 查询。
- CodeGraph 不可用、索引缺失或查询失败时，必须返回 unavailable 或 fallback 结果，不能抛出未捕获异常导致 Agent run 失败。
- fallback provider 至少能基于 workspace 安全边界做简单文本或路径搜索。
- 工具层输出必须有稳定 schema，适合模型阅读，也适合测试断言。
- 工具测试必须能使用 fake provider，不依赖真实 CodeGraph 环境。
- 所有查询结果必须受 workspace allowed paths 和 ignore patterns 约束。

失败条件包括：把本机 CodeGraph 当作必然存在；工具 handler 中直接写大量搜索逻辑而不是调用 provider；测试必须依赖真实索引；索引结果暴露 workspace 外部路径。

## PLAN05 验收：Repo Context Chunk Integration

`PLAN05.md` 完成后，前四个模块必须被串联成真实进入模型输入的 repo context。

必须存在或等价实现 `my_agent/src/agents/repo_context.py`，并修改 `my_agent/src/agents/context_chunks.py`、`my_agent/src/agents/model_turn.py`、`my_agent/src/agents/run_context.py` 或等价入口，使 repo context 和 selected files 能进入 `prepare_turn_input()` 的 messages。

验收要求如下：

- `RepoContextBuilder` 能接收 inventory、mentions、selected files 和 code index 结果。
- 输出应分成稳定 section，例如 selected files、mentioned files、symbols、text hits、inventory summary。
- 内容必须去重，同一路径不能在多个 section 中无意义重复堆叠。
- 输出必须有 max sections、max entries 或 max chars 控制。
- `context_chunks.py` 必须能渲染 `selected_files` 和 `repo_context` chunk。
- `prepare_turn_input()` 的测试必须能证明最终 messages 中出现了 repo context。
- 同一输入多次构建时输出顺序必须稳定。

失败条件包括：repo context builder 在构建时无限读取文件；chunk 渲染依赖真实 LLM；selected files 和 repo context 只存在于对象中但没有进入模型输入；上下文顺序不可预测。

## 必须存在的测试覆盖

完成所有计划后，至少应有以下测试文件或等价测试覆盖：

- `my_agent/tests/test_workspace_inventory.py` 覆盖 inventory 数据结构、ignore、allowed paths、截断和稳定排序。
- `my_agent/tests/test_context_mentions.py` 覆盖路径、文件名、测试名、符号名、去重、中文任务和 unresolved 候选。
- `my_agent/tests/test_selected_files.py` 覆盖 add、drop、list、mode、reason、重复添加和排序稳定。
- `my_agent/tests/test_code_index.py` 覆盖 provider 协议、fallback 行为、unavailable 行为和 workspace 安全边界。
- `my_agent/tests/test_code_index_tools.py` 覆盖 tool schema、fake provider 输出和错误降级。
- `my_agent/tests/test_repo_context.py` 覆盖 section 顺序、去重、截断、selected/mention/index 串联。
- `my_agent/tests/test_context_chunks.py` 覆盖 selected files 和 repo context 出现在模型输入中。

测试不要求只能使用这些文件名，但覆盖点不能减少。核心逻辑必须用 fake workspace、fake inventory 或 fake provider 做 deterministic tests。

## 推荐验证命令

在完成每个 PLAN 后，先运行对应局部测试。全部计划完成后，在 `my_agent` 目录下运行：

```powershell
cd "C:\Users\ch\Desktop\ai agent学习\my_agent"
python -m pytest -q
```

为了快速定位链路问题，也建议运行：

```powershell
python -m pytest tests/test_workspace_inventory.py tests/test_context_mentions.py tests/test_selected_files.py -q
python -m pytest tests/test_code_index.py tests/test_code_index_tools.py tests/test_repo_context.py -q
python -m pytest tests/test_context_chunks.py -q
```

通过标准是：新增核心测试全部通过，原有全量测试也通过。不允许通过跳过新增核心测试来制造通过结果。

## 端到端验收场景

下面这些场景应能通过单测或小型集成测试证明，不需要真实调用 LLM。

场景一：文件树理解。

输入任务是“查看 runner 和 run_state 相关代码”。inventory 应能列出 `src/agents/run_loop.py`、`src/agents/run_state.py` 或当前仓库中实际存在的相近候选。若文件不存在，测试应使用 fixture 创建等价结构，而不是硬编码不存在路径。

场景二：文件提及。

输入任务是“修改 `src/agents/context_chunks.py` 中 repo_context 的顺序”。mention detector 应命中该路径，resolver 应确认它在 workspace inventory 中可见，selected files 可以把它加入 mentioned 或 editable 状态。

场景三：符号提及。

输入任务是“找出 build_turn_context 如何生成消息”。mention detector 应识别 `build_turn_context` 作为 symbol candidate。code index provider 应返回候选位置，或者在索引不可用时返回明确 unavailable/fallback 结果。

场景四：selected files 注入。

selected files 包含 `src/agents/context_chunks.py` 时，`prepare_turn_input()` 生成的 messages 中应出现 selected files 摘要。摘要应包含路径和 mode/reason，但不需要包含完整文件内容。

场景五：repo context 注入。

给定 fake inventory、fake mentions、fake selected files 和 fake code index results，`RepoContextBuilder` 应生成 repo context；`context_chunks.py` 应将其渲染；`prepare_turn_input()` 的 messages 中应出现 repo context 内容，并且不重复、不超长。

场景六：CodeGraph 不可用。

临时禁用 CodeGraph 或使用无索引测试目录时，code index 工具应返回 unavailable 或 fallback 搜索结果。Agent run 不应因为索引不可用而崩溃。

## 严格失败条件

出现以下任一情况，本阶段不能验收通过：

- inventory、mention resolve、selected files、repo context 中任何文件访问绕过 `Workspace` 安全策略。
- repo context 构建会读取无限文件或输出无限长度内容。
- selected files 将 read-only 和 editable 混为一谈。
- CodeGraph 不可用导致未捕获异常或 Agent run 崩溃。
- context chunk 顺序不稳定，同一输入无法得到同一 messages 顺序。
- 新增核心逻辑只能靠真实 LLM 手工测试，无法用 deterministic tests 验证。
- 为了实现本阶段而大规模重写 `my_agent` 现有架构，破坏既有 chat、workspace tools 或 coding agent profile 行为。
- 参考项目代码被整段复制进 `my_agent`，但没有按当前项目边界重新建模。

## 阶段完成定义

当以下条件全部满足时，才能认为“仓库理解与上下文选择能力”阶段完成：

1. `WorkspaceInventory` 能稳定描述当前 workspace 文件结构。
2. `context_mentions` 能从用户任务中提取路径、文件名、测试名和符号候选。
3. `SelectedFilesState` 能维护本轮任务关注文件，并保留 mode 与 reason。
4. `CodeIndexProvider` 能提供统一索引查询接口，CodeGraph 不可用时有可控降级。
5. `RepoContextBuilder` 能综合 selected files、mentions、inventory 和 index results 生成 repo context。
6. `context_chunks.py` 能渲染 selected files 和 repo context。
7. `prepare_turn_input()` 生成的模型输入包含稳定的 selected files 和 repo context chunk。
8. 所有新增测试和原有全量测试通过。

满足这些标准后，`my_agent` 才具备进入下一阶段 Coding Agent 升级的基础。下一阶段可以继续设计安全编辑、patch apply、测试运行、修复循环和任务规划，但不应在本阶段提前实现这些能力。
