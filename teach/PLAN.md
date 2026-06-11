# PLAN - 仓库理解与上下文选择能力总教学计划

本文件是新对话里的 Agent 接手本阶段教学实现时的总入口。先读本文件，再读 `verification.md`，最后按 `PLAN01.md` 到 `PLAN05.md` 的顺序推进。不要直接跳到某个模块写代码，也不要从参考项目里大段复制实现。

本阶段的目标是把 `my_agent` 从“有一些基础工具的对话 Agent”升级成“能围绕本地代码仓库建立任务上下文的 Coding Agent 基础框架”。当前阶段只做仓库理解和上下文选择，不做完整代码编辑闭环，不做 RAG，不做外部代码索引插件。

## 当前阶段的核心判断

这一阶段不是让 Agent 一次性拥有所有 Coding Agent 能力，而是先补齐最基础的代码阅读链路：

用户任务文本进入系统后，Agent 先知道 workspace 里有哪些文件；然后识别用户提到的路径、文件名、测试名和符号；再维护 selected files；接着用受控的文件读取、grep-like 搜索、行号片段、轻量 Python outline 和相关文件推荐补充线索；最后把这些线索整理成稳定的 repo context，注入模型输入。

这条链路应该完全基于当前项目的 `Workspace` 安全边界和 read-only tools。CodeGraph、LSP、tree-sitter、向量数据库、长期索引服务以后可以作为独立插件或高级检索模块出现，但不进入本阶段。

## 新 Agent 阅读顺序

接手时按这个顺序读：

1. `teach/PLAN.md`，理解总体目标和边界。
2. `teach/verification.md`，先知道最终验收标准。
3. `teach/PLAN01.md`，实现 workspace inventory。
4. `teach/PLAN02.md`，实现文件、测试和符号提及识别。
5. `teach/PLAN03.md`，实现 selected files 状态。
6. `teach/PLAN04.md`，实现文件代码读取与轻量仓库检索工具。
7. `teach/PLAN05.md`，把前四步串成 repo context chunk。

每开始一个 PLAN，只阅读该 PLAN 里列出的 `my_agent` 文件和参考项目文件。不要一次性打开所有参考源码。

## 当前 my_agent 基线

开始实现前至少理解这些文件：

- `my_agent/src/agents/workspace.py`：workspace root、allowed paths、ignore patterns 和路径安全边界。
- `my_agent/src/agents/workspace_tools.py`：现有 list/read/search workspace 工具。
- `my_agent/src/agents/context_chunks.py`：模型上下文块渲染层。
- `my_agent/src/agents/model_turn.py`：`prepare_turn_input()` 是检查 context 是否进入模型输入的关键入口。
- `my_agent/src/agents/run_context.py`：运行态上下文，后续 selected files 和 repo context 应从这里或等价对象进入 turn 构建流程。
- `my_agent/src/agents/coding_agent.py`：Coding Agent profile 和 capability pack 聚合入口。

当前已有的结构优势是：安全边界、工具层、上下文层和 agent profile 已经分开。升级时要沿用这个分层。

## 参考项目使用原则

`reference/openaiagent/` 是主要框架参考。它主要提供工具 schema、run context、tool context、能力边界和框架组织方式的参考。

`reference/aider-main/` 是 Coding Agent 能力参考。重点看它如何处理 repo 文件、只读/可编辑文件、chat chunks 和 repo map 思想。本阶段只学习“先选文件、再读片段、再组织上下文”，不复制完整 repo map 算法。

`reference/mini-swe-agent-main/` 是轻量 Coding Agent 运行方式参考。重点看 Agent、Model、Environment 的解耦，以及本地环境调用边界。

`reference/OpenHands-main/` 是 workspace/session/file store 边界参考。重点看它如何隔离会话和文件访问，不要复制大型服务架构。

阅读任何参考项目源码前，先读该项目的 `PROJECT_ARCHITECTURE_ANALYSIS.md`。

## 五个 PLAN 的职责

`PLAN01.md` 建立 `WorkspaceInventory`。它只回答“仓库里有什么，这些路径是否安全可见”，不读取文件内容，不做任务相关性判断。

`PLAN02.md` 建立 `context_mentions`。它从用户任务中识别路径、文件名、测试名和符号候选，并结合 inventory 尽量解析到真实文件。

`PLAN03.md` 建立 `SelectedFilesState`。它显式维护本轮任务关注文件，区分 read-only、editable、mentioned、auto-selected 等语义。

`PLAN04.md` 建立文件代码读取与轻量仓库检索工具。它增强现有 `WORKSPACE_READ` 能力，提供行号读取、grep-like 搜索、文件候选查找、Python outline 和相关文件推荐。它不接入 CodeGraph，不建立代码索引服务。

`PLAN05.md` 建立 `RepoContextBuilder` 和 repo context chunk。它把 inventory、mentions、selected files 和 PLAN04 的读取/搜索结果组织成稳定上下文，并进入 `prepare_turn_input()`。

不要改变这个顺序。PLAN05 必须最后做，因为它依赖前四个模块的输出。

## 实施纪律

每节课新增业务代码尽量不超过 80 行，不含测试。超过时优先拆成数据结构、核心逻辑、工具包装和集成测试。

每个 PLAN 的新增业务代码总量目标是 300 到 2000 行，不含测试和复用代码。少于 300 行通常说明验收不足；超过 2000 行通常说明把后续阶段能力提前塞进来了。

所有路径处理都必须经过 `Workspace` 安全边界。任何 inventory、mention resolve、selected files、workspace code reader、repo context 文件访问都不能绕过 allowed paths 和 ignore patterns。

核心逻辑必须能用 deterministic tests 验证，不能依赖真实 LLM 输出、真实 shell 环境或外部索引服务。

## 本阶段不做什么

本阶段不做：

- CodeGraph 集成。
- LSP 集成。
- tree-sitter 全语言解析。
- 向量 RAG。
- 长期记忆 RAG。
- SQLite FTS 或持久化索引。
- 自动 patch。
- 安全编辑策略。
- 测试运行和失败修复循环。
- Git diff workflow。
- 多 Agent 协作。

这些能力以后可以分阶段添加。当前阶段的唯一目标是让 Agent 能稳定、可测、可解释地理解本地仓库上下文。

## 阶段完成定义

当 `verification.md` 中的所有验收标准通过时，本阶段完成。最关键的证明是：

- inventory 能稳定描述仓库结构。
- mention detector 能识别并解析用户提到的文件、测试和符号候选。
- selected files 能维护本轮任务关注文件。
- workspace code tools 能只读地读取行号片段、搜索文本、查找文件候选、生成 Python outline、推荐相关文件。
- repo context 能综合这些信息生成稳定 chunk。
- `prepare_turn_input()` 生成的 messages 中确实包含 selected files 和 repo context。

满足这些条件后，`my_agent` 才适合进入下一阶段：安全编辑、patch apply、测试运行和失败反馈修复闭环。
