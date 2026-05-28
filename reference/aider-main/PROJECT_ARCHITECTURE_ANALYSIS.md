# Aider 项目架构分析

## 0. 阅读范围与证据

- 仓库路径：`C:\Users\ch\Desktop\ai agent学习\reference\aider-main`
- 项目入口：`pyproject.toml` 声明命令 `aider = "aider.main:main"`，`aider/__main__.py` 直接调用 `aider.main.main()`
- CodeGraph：已使用 Codex 的 CodeGraph 插件检查并同步索引；`codegraph status` 显示 203 个文件、2880 个符号、7245 条边，`codegraph sync .` 返回 `Already up to date`
- 分析重点：运行时代码、核心 agent loop、模型适配、编辑格式、repo map、命令、lint/test/git 反馈环。`aider/website/`、`tests/fixtures/`、`benchmark/` 作为支撑材料，不作为主运行架构展开

---

## 1. 项目总体定位

### 1.1 这是哪类 Agent

Aider 是一个**终端内 AI Pair Programming / Coding Agent**。它不是 OpenAI Agents SDK 那种通用多 Agent Runner，也不是以函数工具调用为中心的 agent 框架，而是围绕“代码仓库 + 选中文件 + LLM 输出编辑格式 + 本地文件修改 + git/lint/test 反馈”构建的交互式 Coding Agent。

核心抽象是 `aider/coders/base_coder.py` 中的 `Coder`。`Coder` 负责：

- 管理当前会话状态：`cur_messages`、`done_messages`、`abs_fnames`、`abs_read_only_fnames`
- 组装 prompt：系统提示、示例、repo map、只读文件、可编辑文件、当前用户消息
- 调用 LLM：通过 `aider/models.py` 的 `Model.send_completion()` 调用 LiteLLM
- 解析模型输出：不同 `Coder` 子类定义不同编辑格式
- 应用代码修改：`get_edits()`、`apply_edits()`、`apply_updates()`
- 执行反馈闭环：自动 commit、lint、test、shell command suggestion、reflection retry

### 1.2 主要解决的问题

Aider 主要解决“让 LLM 在真实代码仓库中可靠地改代码”的问题：

- 将大型仓库压缩为 repo map，避免必须把所有文件塞进上下文
- 只允许模型编辑用户加入 chat 的文件，降低误改范围
- 使用结构化编辑格式，如 SEARCH/REPLACE、unified diff、patch、whole file
- 通过 git 自动提交、diff、undo 追踪 AI 修改
- 将 lint/test 失败反馈给 LLM，形成修复循环
- 通过 `/add`、`/drop`、`/run`、`/test`、`/lint`、`/ask`、`/architect` 等命令让用户保持控制

### 1.3 核心能力

| 能力 | 关键文件/类/函数 | 说明 |
|---|---|---|
| 终端交互 loop | `aider/coders/base_coder.py::Coder.run()`、`Coder.run_one()` | 从用户输入开始，进入 LLM 调用、解析、应用、反馈 |
| LLM 适配 | `aider/models.py::Model`、`Model.send_completion()`、`aider/llm.py::LazyLiteLLM` | 使用 LiteLLM 连接 OpenAI、Anthropic、OpenRouter、Gemini、Ollama 等 |
| 编辑格式 | `aider/coders/editblock_coder.py::EditBlockCoder`、`udiff_coder.py::UnifiedDiffCoder`、`wholefile_coder.py::WholeFileCoder`、`patch_coder.py::PatchCoder` | 将自然语言响应约束为可解析的代码修改 |
| repo map / 代码检索 | `aider/repomap.py::RepoMap` | tree-sitter 抽取符号，NetworkX PageRank 选择相关代码片段 |
| git 集成 | `aider/repo.py::GitRepo` | 自动 commit、生成 commit message、diff、undo 保护 |
| 命令系统 | `aider/commands.py::Commands` | 动态收集所有 `cmd_*` 方法作为 `/` 命令 |
| lint/test 反馈 | `aider/linter.py::Linter`、`aider/commands.py::cmd_test()` | 将错误包装成可回灌 LLM 的消息 |
| architect/editor 分工 | `aider/coders/architect_coder.py::ArchitectCoder.reply_completed()` | 一个模型产出方案，另一个 editor coder 执行修改 |
| chat history / summarization | `aider/history.py::ChatSummary`、`aider/coders/chat_chunks.py::ChatChunks` | 管理长会话压缩和 prompt 分段 |
| web/docs RAG | `aider/help.py::Help`、`aider/scrape.py::Scraper` | `/help` 使用 llama-index 检索本地文档，`/web` 抓网页转 markdown |

---

## 2. 项目目录结构

### 2.1 顶层结构

| 路径 | 职责 |
|---|---|
| `pyproject.toml` | 包元数据、Python 版本、CLI 入口 `aider.main:main`、动态依赖声明 |
| `requirements.txt`、`requirements/` | 运行和可选功能依赖，核心依赖包括 `litellm`、`gitpython`、`grep-ast`、`tree-sitter`、`networkx`、`prompt_toolkit`、`rich` |
| `aider/` | 主程序包，包含 CLI、Coder、模型、repo map、命令、IO、git、lint、web、voice、GUI |
| `aider/coders/` | Agent 的核心行为层：不同 chat/edit mode 的 `Coder` 子类和 prompt |
| `aider/resources/` | 模型配置资源：`model-settings.yml`、`model-metadata.json` |
| `aider/queries/` | tree-sitter tag 查询文件，用于 repo map 符号抽取 |
| `aider/website/` | 文档网站和内置帮助语料，`aider/help.py` 会读取其中 markdown 构建向量索引 |
| `tests/basic/` | 核心单测，覆盖 main、commands、coder、repomap、models、repo、lint、edit formats |
| `tests/help/`、`tests/scrape/`、`tests/browser/` | 可选帮助、网页抓取、浏览器相关测试 |
| `benchmark/` | SWE Bench / benchmark 相关脚本，不参与主 CLI loop |
| `scripts/` | 发布、文档、统计、网站辅助脚本 |
| `.github/workflows/` | CI、release、Docker、pages 工作流 |
| `.codegraph/` | 本地 CodeGraph 索引数据库目录 |

### 2.2 关键入口文件

| 文件 | 关键符号 | 作用 |
|---|---|---|
| `aider/__main__.py` | `main()` | `python -m aider` 入口，转发到 `aider.main.main()` |
| `aider/main.py` | `main()` | CLI 总入口：解析配置、加载 env、选择模型、初始化 git/repo/coder、启动主 loop |
| `aider/args.py` | `get_parser()` | 使用 `configargparse` 定义所有 CLI/config/env 参数 |
| `aider/coders/base_coder.py` | `Coder` | Agent 主体和运行 loop |
| `aider/coders/__init__.py` | `__all__` | 注册所有 coder 类型，`Coder.create()` 通过这里按 `edit_format` 动态选择子类 |

### 2.3 核心模块文件

| 文件/目录 | 关键符号 | 职责 |
|---|---|---|
| `aider/coders/base_coder.py` | `Coder.create()`、`Coder.run()`、`Coder.send_message()`、`Coder.apply_updates()` | 主 agent、prompt 组装、LLM 调用、编辑应用、反馈环 |
| `aider/coders/editblock_coder.py` | `EditBlockCoder`、`find_original_update_blocks()`、`do_replace()` | SEARCH/REPLACE 编辑格式，默认核心编辑策略之一 |
| `aider/coders/udiff_coder.py` | `UnifiedDiffCoder`、`find_diffs()`、`apply_hunk()` | unified diff 编辑格式 |
| `aider/coders/wholefile_coder.py` | `WholeFileCoder` | 整文件重写编辑格式 |
| `aider/coders/patch_coder.py` | `PatchCoder`、`PatchAction`、`Patch` | 自定义 patch 格式解析和应用 |
| `aider/coders/architect_coder.py` | `ArchitectCoder.reply_completed()` | architect/editor 双模型协作 |
| `aider/coders/context_coder.py` | `ContextCoder.reply_completed()` | 先识别应加入 chat 的文件 |
| `aider/models.py` | `ModelSettings`、`ModelInfoManager`、`Model` | 模型元数据、默认编辑格式、token/cost、LiteLLM 调用 |
| `aider/llm.py` | `LazyLiteLLM` | 延迟导入并配置 LiteLLM |
| `aider/commands.py` | `Commands`、`SwitchCoder` | slash command 分发、模式切换、外部命令、web、lint/test、文件管理 |
| `aider/repomap.py` | `RepoMap`、`Tag` | 仓库符号检索与上下文压缩 |
| `aider/repo.py` | `GitRepo` | Git 仓库发现、ignore、diff、commit、undo 支撑 |
| `aider/linter.py` | `Linter`、`LintResult` | tree-sitter/flake8/custom lint 命令 |
| `aider/history.py` | `ChatSummary` | 长 chat history 摘要 |
| `aider/io.py` | `InputOutput`、`AutoCompleter`、`ConfirmGroup` | 终端 IO、prompt_toolkit 输入、history、确认交互、输出渲染 |
| `aider/help.py` | `Help`、`get_index()` | `/help` 文档检索，基于 llama-index |
| `aider/scrape.py` | `Scraper` | `/web` 抓取网页并转 markdown |
| `aider/watch.py` | `FileWatcher` | IDE/watch 模式，识别源码注释中的 `AI!` / `AI?` |

---

## 3. Agent 主运行流程

### 3.1 启动链路

```text
pyproject.toml console script
  -> aider.main:main
     -> aider/main.py::main()
        -> get_git_root()
        -> args.get_parser()
        -> load_dotenv_files()
        -> register_models()
        -> register_litellm_models()
        -> select_default_model()
        -> models.Model(...)
        -> GitRepo(...)
        -> Commands(...)
        -> ChatSummary(...)
        -> Coder.create(...)
        -> coder.show_announcements()
        -> coder.run()
```

关键文件：

- `pyproject.toml`：`[project.scripts] aider = "aider.main:main"`
- `aider/__main__.py`：模块方式运行时调用 `main()`
- `aider/main.py::main()`：完成配置、模型、repo、commands、summarizer、coder 的装配

### 3.2 Coder 选择机制

`aider/coders/base_coder.py::Coder.create()` 根据 `edit_format` 从 `aider/coders/__init__.py::__all__` 中寻找匹配的 coder class：

```text
main_model.edit_format / args.edit_format
  -> Coder.create(...)
     -> for coder in aider.coders.__all__
        -> coder.edit_format == edit_format
        -> instantiate coder(main_model, io, **kwargs)
```

这使得不同编辑协议成为“策略子类”：

- `EditBlockCoder.edit_format = "diff"`
- `UnifiedDiffCoder.edit_format = "udiff"`
- `WholeFileCoder.edit_format = "whole"`
- `PatchCoder.edit_format = "patch"`
- `AskCoder.edit_format = "ask"`
- `ArchitectCoder.edit_format = "architect"`
- `ContextCoder.edit_format = "context"`

### 3.3 用户输入到输出的完整链路

```text
Coder.run()
  while True:
    if auto_copy_context:
      Commands.cmd_copy_context()
    user_message = Coder.get_input()
      -> InputOutput.get_input()
      -> AutoCompleter 提供文件/命令/符号补全
      -> FileWatcher/ClipboardWatcher 可打断输入并生成消息

    Coder.run_one(user_message, preproc=True)
      -> init_before_message()
      -> preproc_user_input()
         if input starts with "/" or "!":
           Commands.run()
             -> Commands.do_run()
             -> cmd_* method
             -> may raise SwitchCoder
         else:
           check_for_file_mentions()
           check_for_urls()

      while message:
        send_message(message)
          -> append {"role": "user", "content": message} to cur_messages
          -> format_messages()
             -> format_chat_chunks()
                -> system prompt
                -> examples
                -> summarized done_messages
                -> repo map
                -> read-only files
                -> editable chat files
                -> current turn messages
                -> reminder
             -> ChatChunks.all_messages()
          -> check_tokens()
          -> warm_cache()
          -> send(messages)
             -> Model.send_completion()
                -> litellm.completion(**kwargs)
             -> show_send_output_stream() or show_send_output()
          -> add_assistant_reply_to_cur_messages()
          -> check assistant-mentioned filenames
          -> reply_completed()
             -> special modes like architect/context may consume response
          -> apply_updates()
             -> get_edits()
             -> apply_edits_dry_run()
             -> prepare_to_edit()
             -> apply_edits()
          -> auto_commit()
          -> lint_edited()
          -> run_shell_commands()
          -> auto test via Commands.cmd_test()

        if reflected_message exists:
          message = reflected_message
          continue up to max_reflections
        else:
          break
```

### 3.4 LLM 调用在哪里发生

LLM 调用链路是：

```text
Coder.run()
  -> Coder.run_one()
     -> Coder.send_message()
        -> Coder.send()
           -> Model.send_completion()
              -> litellm.completion(**kwargs)
```

关键函数：

- `aider/coders/base_coder.py::Coder.send()`：记录 `TO LLM` 日志，调用 `model.send_completion()`，处理 stream/non-stream 输出、cost/token 统计
- `aider/models.py::Model.send_completion()`：构造 LiteLLM 参数，包括 `model`、`stream`、`temperature`、`tools`、`tool_choice`、`extra_params`、`messages`
- `aider/llm.py::LazyLiteLLM`：延迟导入 `litellm`，设置 `drop_params = True`、生产模式等

### 3.5 tool call / final answer / handoff / code execution 如何调度

#### tool call

项目主路径不是现代 Responses API tool-call loop，而是两套机制：

1. 用户命令工具：`aider/commands.py::Commands`
   - 输入以 `/` 或 `!` 开头时，`Coder.preproc_user_input()` 调用 `Commands.run()`
   - `Commands.run()` 动态匹配 `cmd_*` 方法，例如 `cmd_add()`、`cmd_drop()`、`cmd_run()`、`cmd_test()`、`cmd_web()`、`cmd_ask()`、`cmd_architect()`

2. LLM function tool schema：存在但不是主流路径
   - `aider/coders/editblock_func_coder.py::EditBlockFunctionCoder.functions`
   - `aider/coders/wholefile_func_coder.py::WholeFileFunctionCoder.functions`
   - `aider/coders/single_wholefile_func_coder.py::SingleWholeFileFunctionCoder.functions`
   - `Model.send_completion()` 在 `functions is not None` 时发送 LiteLLM `tools=[{"type":"function",...}]` 并强制 `tool_choice`
   - 其中 `EditBlockFunctionCoder`、`WholeFileFunctionCoder` 的 `__init__()` 直接抛 `RuntimeError("Deprecated...")`，说明 function-tool 编辑格式已不是主设计方向

#### final answer

未发现独立的 `FinalAnswer` 类型或 final-answer reducer。模型响应的文本由 `Coder.show_send_output_stream()` / `Coder.show_send_output()` 渲染，并加入 `cur_messages`。

对代码模式而言，assistant 输出通常同时承载：

- 人类可读说明
- 编辑块
- 可选 shell 命令建议

随后 `apply_updates()` 尝试从同一响应中解析并落盘。如果没有解析出编辑，`Coder.auto_commit()` 会返回 `files_content_gpt_no_edits` 之类反馈。

#### handoff

未发现通用 handoff 框架。实际存在两种“模式切换 / 委派”：

- `aider/commands.py::SwitchCoder`：异常对象，携带 `kwargs` 和可选 `placeholder`，由 `aider/main.py::main()` 主 loop 捕获后重新 `Coder.create(**kwargs)`
- `aider/coders/architect_coder.py::ArchitectCoder.reply_completed()`：architect 模型输出方案后，内部创建 editor coder：
  - `editor_model = self.main_model.editor_model or self.main_model`
  - `kwargs["edit_format"] = self.main_model.editor_edit_format`
  - `editor_coder = Coder.create(io=self.io, from_coder=self, ...)`
  - `editor_coder.run(with_message=content, preproc=False)`

#### code execution

未发现 sandbox。代码执行是本地 shell：

- `aider/run_cmd.py::run_cmd()`
- `aider/run_cmd.py::run_cmd_subprocess()`
- `aider/run_cmd.py::run_cmd_pexpect()`
- `aider/commands.py::Commands.cmd_run()`
- `aider/commands.py::Commands.cmd_test()`
- `aider/coders/base_coder.py::Coder.run_shell_commands()`

安全边界主要是用户确认：

- `/run` 是否把输出加入 chat 由 `InputOutput.confirm_ask()` 决定
- LLM 建议 shell command 时，`Coder.handle_shell_commands()` 使用 `explicit_yes_required=True`
- 但执行环境仍是用户本机 shell，没有容器/权限隔离

---

## 4. 核心模块分析

### 4.1 Agent / Runner / Loop

| 项目 | 说明 |
|---|---|
| 核心文件 | `aider/coders/base_coder.py`、`aider/main.py` |
| 核心类 | `Coder` |
| 核心函数 | `Coder.create()`、`Coder.run()`、`Coder.run_one()`、`Coder.send_message()`、`Coder.send()`、`Coder.apply_updates()` |

职责：

- `aider/main.py::main()` 是外层 runner：解析参数、选择模型、创建 repo/commands/summarizer/coder，并捕获 `SwitchCoder` 进行模式切换
- `Coder.run()` 是交互 loop：读取用户输入并调用 `run_one()`
- `Coder.run_one()` 是单请求 loop：处理 reflection，最多 `max_reflections = 3`
- `Coder.send_message()` 是核心 turn 执行器：组装 prompt、调用 LLM、处理响应、应用编辑、触发 lint/test/shell/commit

调用关系：

```text
main.main()
  -> Coder.create()
  -> coder.run()
     -> get_input()
     -> run_one()
        -> preproc_user_input()
        -> send_message()
           -> format_messages()
           -> send()
           -> reply_completed()
           -> apply_updates()
           -> auto_commit()
           -> lint_edited()
           -> run_shell_commands()
           -> cmd_test()
```

可复用价值：

- `Coder.create()` 用 `edit_format` 路由到不同子类，是非常实用的策略模式
- `run_one()` 中的 `reflected_message` 机制简单有效：格式错误、lint/test 错误、文件缺失等都能变成下一轮用户消息
- `send_message()` 把一个 turn 的生命周期集中管理，便于理解，但函数较长，若从零实现建议拆成 step objects

### 4.2 Model / LLM Adapter

| 项目 | 说明 |
|---|---|
| 核心文件 | `aider/models.py`、`aider/llm.py`、`aider/resources/model-settings.yml` |
| 核心类 | `ModelSettings`、`ModelInfoManager`、`Model`、`LazyLiteLLM` |
| 核心函数 | `Model.send_completion()`、`Model.token_count()`、`Model.configure_model_settings()`、`register_models()`、`register_litellm_models()` |

职责：

- `ModelSettings` 是模型行为配置数据结构：`edit_format`、`weak_model_name`、`use_repo_map`、`streaming`、`cache_control`、`extra_params` 等
- `ModelInfoManager` 从 LiteLLM、本地 metadata、OpenRouter cache 获取上下文窗口和价格
- `Model` 根据模型名设置默认编辑格式、weak/editor model、token/cost 统计和环境变量验证
- `LazyLiteLLM` 延迟导入 `litellm`，降低启动时间

LLM 调用细节：

- `Model.send_completion()` 构造 LiteLLM 参数
- 如果 `functions` 存在，转换为 OpenAI-style tool schema：`kwargs["tools"] = [{"type":"function","function": function}]`
- 如果是 Ollama，按当前 token 数估算 `num_ctx`
- 如果检测到 `GITHUB_COPILOT_TOKEN`，会走 `github_copilot_token_to_open_ai_key()`
- 最终调用 `litellm.completion(**kwargs)`

可复用价值：

- 模型行为和模型 metadata 分离值得学习
- `weak_model` 用于 summarization/commit message，`editor_model` 用于 architect 模式，是编码 agent 中成本/能力分层的好设计
- LiteLLM 适合作为多供应商统一层，但如果你要严格使用 Responses API 或结构化 tool loop，需要重新设计 adapter

### 4.3 Tool / Function Tool

| 项目 | 说明 |
|---|---|
| 核心文件 | `aider/commands.py`、`aider/coders/*_func_coder.py`、`aider/coders/base_coder.py` |
| 核心类 | `Commands`、`SwitchCoder`、`SingleWholeFileFunctionCoder` |
| 核心函数 | `Commands.run()`、`Commands.do_run()`、`Coder.run_shell_commands()`、`Coder.parse_partial_args()` |

职责：

- Slash commands 是主要工具系统：任何 `Commands.cmd_xxx()` 都自动成为 `/xxx`
- `!cmd` 是 `/run cmd` 的快捷方式，由 `Commands.run()` 识别
- `/web` 抓网页并加入上下文，`/run` 执行 shell，`/test` 执行测试，`/lint` 调用 linter，`/ask` / `/code` / `/architect` 切换或临时运行不同 coder
- Function tool coder 存在但大多 deprecated，不是当前项目主线

关键调用：

```text
Coder.preproc_user_input()
  -> Commands.is_command()
  -> Commands.run()
     -> matching_commands()
     -> do_run()
        -> cmd_*()
```

可复用价值：

- `cmd_*` 自动注册命令非常适合终端 agent
- `SwitchCoder` 用异常从深层命令跳回外层 main loop，是务实但不够类型安全的控制流
- Function tool schema 可参考，但不建议直接照搬 deprecated coder

### 4.4 Memory / Session / Context

| 项目 | 说明 |
|---|---|
| 核心文件 | `aider/coders/base_coder.py`、`aider/coders/chat_chunks.py`、`aider/history.py`、`aider/io.py` |
| 核心类 | `ChatChunks`、`ChatSummary`、`InputOutput` |
| 核心状态 | `Coder.cur_messages`、`Coder.done_messages`、`Coder.abs_fnames`、`Coder.abs_read_only_fnames` |

职责：

- `cur_messages`：当前未归档 turn 的消息
- `done_messages`：已完成历史，必要时会被 summarizer 压缩
- `ChatSummary.summarize_real()`：当 history 超过 token 预算时，递归摘要较旧消息
- `ChatChunks`：将 prompt 分为 system、examples、done、repo、readonly_files、chat_files、cur、reminder
- `InputOutput.append_chat_history()`：将用户输入、工具输出、AI 输出记录到 `.aider.chat.history.md`
- `InputOutput.log_llm_history()`：可选记录完整 LLM 输入/输出到 `--llm-history-file`

上下文组织顺序由 `ChatChunks.all_messages()` 定义：

```text
system
+ examples
+ readonly_files
+ repo
+ done
+ chat_files
+ cur
+ reminder
```

可复用价值：

- `ChatChunks` 是非常值得借鉴的 prompt 分层结构
- 将“可编辑文件”和“只读参考文件”分开，是 coding agent 的关键上下文设计
- history summarization 依赖 LLM，不是强一致记忆；适合聊天上下文压缩，不适合作为长期事实数据库

### 4.5 Prompt / Instruction

| 项目 | 说明 |
|---|---|
| 核心文件 | `aider/coders/base_prompts.py`、`aider/coders/editblock_prompts.py`、`aider/coders/architect_prompts.py`、`aider/prompts.py` |
| 核心类 | `CoderPrompts`、`EditBlockPrompts`、`ArchitectPrompts` |
| 核心函数 | `Coder.fmt_system_prompt()`、`Coder.format_chat_chunks()`、`Coder.get_platform_info()` |

职责：

- 每个 coder 子类绑定自己的 `gpt_prompts`
- `Coder.fmt_system_prompt()` 注入 fence、平台信息、语言偏好、shell command 规则、lazy/overeager reminders
- `EditBlockPrompts` 定义 SEARCH/REPLACE 格式和示例
- `ArchitectPrompts` 让 architect 只描述修改方案，不输出完整代码
- `prompts.commit_system` 用于生成 git commit message
- `prompts.summarize` 用于压缩 chat history

设计亮点：

- prompt 与解析器成对出现：`EditBlockPrompts` 对应 `EditBlockCoder.get_edits()`，`UnifiedDiffPrompts` 对应 `UnifiedDiffCoder.get_edits()`
- prompt 明确约束“只能编辑加入 chat 的文件”，同时 repo map 明确标记为 read-only
- `get_platform_info()` 将系统、shell、日期、git、lint/test 命令注入 prompt，让模型建议命令更贴近环境

### 4.6 Multi-agent / Handoff

结论：**未发现通用多 Agent / handoff 框架**。存在一个实用的 architect/editor 双阶段模式。

| 文件 | 关键符号 | 行为 |
|---|---|---|
| `aider/commands.py` | `SwitchCoder` | 用异常通知外层 main loop 切换 coder/mode/model |
| `aider/main.py` | `except SwitchCoder as switch` | `Coder.create(io=io, from_coder=coder, **switch.kwargs)` |
| `aider/coders/architect_coder.py` | `ArchitectCoder.reply_completed()` | architect 输出方案后创建 editor coder 执行 |
| `aider/coders/context_coder.py` | `ContextCoder.reply_completed()` | 识别需要编辑的文件，并用 `reflected_message` 重试 |

Architect 流程：

```text
用户请求
  -> ArchitectCoder 使用 architect prompt 生成修改说明
  -> reply_completed()
     -> 创建 editor_coder
     -> editor_coder.run(with_message=architect_output, preproc=False)
     -> editor coder 按 editor_edit_format 实际改文件
```

可复用价值：

- “planner/architect 不直接改代码，editor 执行修改”的拆分很值得学习
- `SwitchCoder` 适合小型 CLI，但从零实现更建议用显式 state transition，而不是异常控制流

### 4.7 Guardrail / Validation

结论：**未发现独立 Guardrail 框架**。项目存在大量工程化 validation/安全检查。

关键检查点：

| 文件 | 符号 | 作用 |
|---|---|---|
| `aider/coders/base_coder.py` | `Coder.check_tokens()` | 发送前检查上下文窗口 |
| `aider/coders/base_coder.py` | `Coder.allowed_to_edit()` | 限制编辑范围，要求用户确认未加入 chat 的文件 |
| `aider/coders/base_coder.py` | `Coder.prepare_to_edit()` | 应用前统一检查每个 edit 的目标文件 |
| `aider/coders/base_coder.py` | `Coder.apply_updates()` | 捕获格式错误并转成 `reflected_message` |
| `aider/sendchat.py` | `sanity_check_messages()`、`ensure_alternating_roles()` | 检查/修正消息角色交替 |
| `aider/linter.py` | `Linter.lint()` | 语法、flake8、自定义 lint |
| `aider/models.py` | `Model.validate_environment()` | 检查 API key/环境变量 |
| `aider/main.py` | `sanity_check_repo()` | 检查 git repo 状态 |
| `aider/coders/base_coder.py` | JSON schema validation for `functions` | function schema 的 Draft7 校验 |

可复用价值：

- 将“编辑失败”作为下一轮 LLM 输入，是 coding agent 中非常实用的 self-repair guardrail
- `allowed_to_edit()` 的用户确认机制值得保留
- 若你要做更自主的 agent，需要补充 policy guardrails、权限隔离、tool approval state、可审计执行计划

### 4.8 Tracing / Logging / Hook

| 项目 | 说明 |
|---|---|
| 核心文件 | `aider/analytics.py`、`aider/io.py`、`aider/report.py`、`aider/coders/base_coder.py` |
| 核心类 | `Analytics`、`InputOutput` |
| 核心函数 | `Analytics.event()`、`InputOutput.log_llm_history()`、`report_uncaught_exceptions()`、`Coder.calculate_and_show_tokens_and_cost()` |

职责：

- `Analytics.event()` 记录 launched、command、message_send、repo、exit 等事件，可输出到 PostHog 或本地 JSONL
- `InputOutput.append_chat_history()` 记录 chat transcript
- `InputOutput.log_llm_history()` 记录发给 LLM 的完整 messages 和 LLM response
- `Coder.calculate_and_show_tokens_and_cost()` 统计 token/cost/cache hit
- `aider/report.py::report_uncaught_exceptions()` 注册全局异常 handler，生成 GitHub issue 文本

结论：

- 未发现 OpenTelemetry、span tree、trace processor 这类标准 tracing 框架
- 有足够实用的日志/成本/事件埋点，但如果用于复杂 agent 编排，需要更结构化的 trace model

### 4.9 RAG / Retrieval

Aider 有两类检索。

#### 代码仓库检索：RepoMap

| 文件 | 关键符号 |
|---|---|
| `aider/repomap.py` | `RepoMap`、`Tag`、`get_tags_raw()`、`get_ranked_tags()`、`get_ranked_tags_map()`、`to_tree()` |

流程：

```text
tracked files
  -> tree-sitter query 抽取 def/ref Tag
  -> 建立 identifier -> defining files / referencing files
  -> NetworkX MultiDiGraph
  -> PageRank 排序相关符号/文件
  -> TreeContext 渲染关键代码结构
  -> 压缩到 max_map_tokens
```

特色：

- 使用 `mentioned_fnames`、`mentioned_idents` 个性化排序
- chat 中已有文件不重复进入 repo map
- diskcache 缓存 tags，避免每次重扫

#### 文档帮助检索：Help

| 文件 | 关键符号 |
|---|---|
| `aider/help.py` | `Help`、`get_index()`、`get_package_files()` |

流程：

```text
aider/website/*.md
  -> llama_index MarkdownNodeParser
  -> HuggingFaceEmbedding("BAAI/bge-small-en-v1.5")
  -> VectorStoreIndex persist 到 ~/.aider/caches/help.<version>
  -> /help question
  -> retriever.retrieve(question)
  -> 拼成 <doc from_url="...">...</doc> 上下文
```

可复用价值：

- RepoMap 是本项目最值得学习的 RAG 模块
- Help RAG 适合产品文档问答，不是 coding agent 主循环必需

### 4.10 Code execution / Sandbox

结论：**未发现 sandbox**。

存在本地命令执行：

| 文件 | 符号 | 作用 |
|---|---|---|
| `aider/run_cmd.py` | `run_cmd()` | 选择 pexpect 或 subprocess 执行命令 |
| `aider/run_cmd.py` | `run_cmd_subprocess()` | `subprocess.Popen(..., shell=True)` 实时输出 |
| `aider/run_cmd.py` | `run_cmd_pexpect()` | 交互式 shell 执行 |
| `aider/commands.py` | `cmd_run()` | 用户显式 `/run` 或 `!` |
| `aider/commands.py` | `cmd_test()` | 执行测试，失败输出可回灌 chat |
| `aider/coders/base_coder.py` | `run_shell_commands()`、`handle_shell_commands()` | 执行 LLM 建议的 shell block，需用户确认 |

风险：

- 命令在用户本机、项目 root 下执行
- 没有容器、seccomp、filesystem sandbox、network sandbox
- 对“自主 Coding Agent”来说，应把这层替换为可审计 sandbox executor

### 4.11 Config / Schema / Type system

| 文件 | 关键符号 | 作用 |
|---|---|---|
| `aider/args.py` | `get_parser()` | CLI 参数、YAML config、env var |
| `aider/args_formatter.py` | `YamlHelpFormatter`、`DotEnvFormatter`、`MarkdownHelpFormatter` | 生成配置示例/帮助 |
| `aider/models.py` | `ModelSettings` | 模型行为 schema |
| `aider/resources/model-settings.yml` | YAML records | 内置模型默认行为 |
| `aider/resources/model-metadata.json` | JSON metadata | 上下文窗口和成本补充 |
| `aider/coders/patch_coder.py` | `ActionType`、`Chunk`、`PatchAction`、`Patch` | patch 解析的数据类型 |
| `aider/linter.py` | `LintResult` | lint 结果数据结构 |
| `aider/exceptions.py` | `ExInfo`、`LiteLLMExceptions` | LLM 异常分类和 retry 策略 |

特点：

- 主要使用 Python dataclass、namedtuple、dict message，不是强类型全链路 schema
- OpenAI-style function schema 只用于旧 function coder，并用 `jsonschema.Draft7Validator.check_schema()` 校验
- 配置系统很完整：CLI、`.aider.conf.yml`、`.env`、环境变量、model settings、metadata、alias

---

## 5. 关键数据结构

| 名称 | 类型 | 文件路径 | 运行过程中的作用 |
|---|---|---|---|
| `Coder` | class | `aider/coders/base_coder.py` | Agent 主体，保存文件上下文、消息、模型、repo、commands，并执行完整 turn loop |
| `UnknownEditFormat` | exception | `aider/coders/base_coder.py` | `Coder.create()` 找不到匹配 `edit_format` 时抛出 |
| `FinishReasonLength` | exception | `aider/coders/base_coder.py` | 模型输出到达 length finish reason 时触发续写或报 token limit |
| `ChatChunks` | dataclass | `aider/coders/chat_chunks.py` | Prompt 分段容器，定义 LLM messages 的拼接顺序和 cache control |
| `ModelSettings` | dataclass | `aider/models.py` | 模型行为配置，包括 edit format、repo map、streaming、cache、reasoning 参数 |
| `ModelInfoManager` | class | `aider/models.py` | 管理模型 metadata cache，整合 LiteLLM、本地 JSON、OpenRouter |
| `Model` | class | `aider/models.py` | 具体模型实例，负责 token count、环境校验、weak/editor model、LLM 调用 |
| `OpenRouterModelManager` | class | `aider/openrouter.py` | 缓存 OpenRouter model list，并转换为 LiteLLM 风格 metadata |
| `Commands` | class | `aider/commands.py` | Slash command dispatcher，所有 `cmd_*` 自动成为命令 |
| `SwitchCoder` | exception | `aider/commands.py` | 命令或模式切换时，从内层跳回 `main()` 外层 loop 重建 coder |
| `InputOutput` | class | `aider/io.py` | 终端输入输出、确认交互、history、LLM history、markdown 渲染 |
| `ConfirmGroup` | dataclass-like class | `aider/io.py` | 一组确认问题共享 all/skip/don't ask again 偏好 |
| `AutoCompleter` | class | `aider/io.py` | 文件名、命令、代码 token 补全 |
| `ChatSummary` | class | `aider/history.py` | 压缩长 chat history，使用 weak/main model 生成摘要 |
| `GitRepo` | class | `aider/repo.py` | Git 仓库封装，负责 tracked files、ignore、diff、commit、commit message |
| `RepoMap` | class | `aider/repomap.py` | 仓库代码 RAG，抽取符号并生成压缩 repo map |
| `Tag` | namedtuple | `aider/repomap.py` | tree-sitter/pygments 抽取的符号记录：文件、行号、名称、def/ref |
| `LintResult` | dataclass | `aider/linter.py` | lint 输出和相关行号 |
| `ExInfo` | dataclass | `aider/exceptions.py` | LiteLLM 异常的名称、是否 retry、用户说明 |
| `LiteLLMExceptions` | class | `aider/exceptions.py` | 将 LiteLLM exception class 映射到 `ExInfo` |
| `ActionType` | enum | `aider/coders/patch_coder.py` | patch 动作：ADD、DELETE、UPDATE |
| `Chunk` | dataclass | `aider/coders/patch_coder.py` | patch update 中的删除/插入片段 |
| `PatchAction` | dataclass | `aider/coders/patch_coder.py` | 单个文件的 patch 操作 |
| `Patch` | dataclass | `aider/coders/patch_coder.py` | 多文件 patch action 集合 |
| `FileWatcher` | class | `aider/watch.py` | watch 模式中监听文件变化并抽取 AI 注释 |
| `Analytics` | class | `aider/analytics.py` | 匿名事件、成本、命令、退出原因等埋点 |

---

## 6. 可参考模块清单

| 模块名称 | 文件路径 | 解决的问题 | 设计亮点 | 适合借鉴的地方 | 迁移难度 |
|---|---|---|---|---|---|
| 主 Agent Loop | `aider/coders/base_coder.py::Coder.run()`、`run_one()`、`send_message()` | 单 turn 从输入到 LLM、编辑、验证、反馈 | 用 `reflected_message` 统一重试入口 | 从零实现 Coding Agent 时优先学习 turn 生命周期 | 中 |
| Coder 策略注册 | `aider/coders/base_coder.py::Coder.create()`、`aider/coders/__init__.py` | 不同编辑模式动态选择 | `edit_format` -> subclass | 把编辑协议、chat mode、模型能力解耦 | 低 |
| Prompt 分层 | `aider/coders/chat_chunks.py::ChatChunks` | 控制上下文顺序和 cacheable prompt | system/examples/repo/files/current/reminder 分块 | 很适合复用到自己的 prompt builder | 低 |
| SEARCH/REPLACE 编辑格式 | `aider/coders/editblock_coder.py::EditBlockCoder` | 让 LLM 输出可应用的小块修改 | 精确匹配、失败反馈、shell block 抽取 | 借鉴错误反馈和小块修改协议 | 中 |
| Unified Diff 编辑格式 | `aider/coders/udiff_coder.py::UnifiedDiffCoder` | 使用 diff 表达修改 | flexible hunk application | 可作为高级编辑协议参考 | 中 |
| Patch 编辑格式 | `aider/coders/patch_coder.py::PatchCoder` | 多文件 add/delete/update/move | dataclass 表达 patch AST | 适合设计自己的结构化 patch DSL | 高 |
| RepoMap | `aider/repomap.py::RepoMap` | 大仓库上下文压缩 | tree-sitter + PageRank + TreeContext | 最值得学习的代码检索模块 | 高 |
| Git 封装 | `aider/repo.py::GitRepo` | 自动 commit、diff、undo、ignore | commit message 由 weak/main model 生成 | coding agent 必备，可直接参考流程 | 中 |
| Lint/Test 反馈闭环 | `aider/linter.py::Linter`、`aider/coders/base_coder.py::lint_edited()`、`aider/commands.py::cmd_test()` | 修改后发现并修复错误 | 错误文本 + 代码上下文回灌 LLM | 非常值得直接参考 | 中 |
| 命令系统 | `aider/commands.py::Commands` | 终端 agent 控制面 | `cmd_*` 自动注册、命令补全 | 适合 CLI agent 直接借鉴 | 低 |
| 模型适配 | `aider/models.py::Model`、`aider/llm.py::LazyLiteLLM` | 多供应商模型调用和 metadata | LiteLLM + model settings | 可参考配置层；调用层需按你的 API 重写 | 中 |
| Architect/Editor 分工 | `aider/coders/architect_coder.py::ArchitectCoder` | 用强模型规划、editor 模型执行 | planner 输出作为 editor 输入 | 适合复杂任务拆分 | 中 |
| Context mode | `aider/coders/context_coder.py::ContextCoder` | 先判断要编辑哪些文件 | repo map 放大 + reflection | 适合“先选文件再改”的 agent | 低 |
| Chat history summarization | `aider/history.py::ChatSummary` | 长会话压缩 | weak/main model fallback | 可作为短期记忆压缩参考 | 低 |
| Web/doc RAG | `aider/help.py::Help`、`aider/scrape.py::Scraper` | `/help` 和 `/web` 上下文增强 | 本地文档向量索引 + 网页 markdown | 可选能力，不是核心 coding loop | 中 |
| Watch 模式 | `aider/watch.py::FileWatcher` | IDE 注释驱动 agent | 识别 `AI!`/`AI?` 并抽上下文 | 适合 IDE 集成 | 中 |
| Analytics/日志 | `aider/analytics.py`、`aider/io.py` | 使用统计、LLM history、成本 | 本地/远端双通道 | 可借鉴成本统计和 LLM history | 低 |
| 本地 shell 执行 | `aider/run_cmd.py`、`aider/commands.py::cmd_run()` | 执行测试、命令、工具 | 实时输出和可选择回灌 chat | 仅借鉴交互设计，不建议照搬安全模型 | 中 |

---

## 7. 从零实现 Coding Agent 的升级建议

### 7.1 最值得优先学习的模块

1. `aider/coders/base_coder.py::Coder.send_message()`
   - 这是最完整的 turn lifecycle：prompt、LLM、响应、编辑、commit、lint/test、reflection 都在这里串起来。

2. `aider/coders/chat_chunks.py::ChatChunks`
   - 从零实现时，应先设计上下文分层，而不是直接拼一个巨大的 prompt 字符串。

3. `aider/coders/editblock_coder.py::EditBlockCoder`
   - SEARCH/REPLACE 是成本低、可解释、易反馈的编辑协议。即使将来使用 AST patch，也应该理解这个协议解决了什么问题。

4. `aider/repomap.py::RepoMap`
   - 大仓库 coding agent 的核心难题是“该看哪些代码”。RepoMap 是本仓库最有学习价值的模块。

5. `aider/repo.py::GitRepo` + `aider/linter.py::Linter`
   - Coding Agent 的可靠性来自可回滚和可验证，git/lint/test 是基本闭环。

6. `aider/models.py::Model`
   - 学习模型能力配置如何影响编辑格式、repo map、streaming、temperature、weak/editor model。

### 7.2 适合直接参考设计的模块

- Prompt 分层：`ChatChunks`
- Coder 子类策略：`Coder.create()` + `edit_format`
- Reflection loop：`Coder.run_one()` + `reflected_message`
- 编辑权限检查：`Coder.allowed_to_edit()`、`Coder.prepare_to_edit()`
- 修改后反馈：`Coder.apply_updates()` 捕获 `ValueError` 并回灌 LLM
- lint/test 失败修复：`Coder.lint_edited()`、`Commands.cmd_test()`
- Git 自动 commit 和 undo 提示：`GitRepo.commit()`、`Coder.auto_commit()`、`Commands.raw_cmd_undo()`
- Slash command 自动注册：`Commands.get_commands()`、`Commands.run()`
- Context mode：`ContextCoder` 的“先找文件”模式

### 7.3 暂时不建议照搬的模块

- deprecated function tool coder：
  - `EditBlockFunctionCoder.__init__()` 和 `WholeFileFunctionCoder.__init__()` 已直接抛 `RuntimeError("Deprecated...")`
  - 如果你的 agent 使用 Responses API/tool calls，应重新设计 tool abstraction

- 本地 shell 执行模型：
  - `run_cmd_subprocess(..., shell=True)` 适合用户确认的 CLI 工具
  - 对自主 agent 不够安全，应引入 sandbox、权限策略、命令 allowlist、超时、文件系统隔离

- 过长的 `Coder.send_message()`：
  - 逻辑完整但职责过多
  - 新项目建议拆成 `TurnBuilder`、`ModelCaller`、`ResponseProcessor`、`EditApplier`、`Verifier`

- Streamlit GUI：
  - `aider/gui.py` 是实验性浏览器界面，不是核心 agent 架构

- Analytics：
  - `aider/analytics.py` 对产品运营有用，但从零实现 agent 初期不必优先做

- 全量模型兼容配置：
  - `aider/models.py` 有大量历史模型和供应商兼容逻辑
  - 新项目应先支持少数模型，抽象稳定后再扩展

### 7.4 建议的自研 Coding Agent 架构路线

```text
阶段 1：最小可用
  - AgentState: messages + selected files + repo root
  - PromptBuilder: system + files + current user request
  - ModelAdapter: 单一模型调用
  - EditProtocol: SEARCH/REPLACE 或 patch
  - EditApplier: dry-run + apply + error feedback

阶段 2：可靠性
  - Git checkpoint / undo
  - lint/test runner
  - reflection loop
  - allowed_to_edit / approval policy
  - LLM history logging

阶段 3：上下文能力
  - RepoMap / symbol index
  - file mention detection
  - read-only context
  - history summarization

阶段 4：复杂任务
  - planner/editor handoff
  - context-selection mode
  - sandbox executor
  - structured tracing
  - tool call scheduler
```

---

## 8. 模块存在性对照

| 用户关心模块 | 本项目是否存在 | 说明 |
|---|---|---|
| Agent / Runner / Loop | 存在 | `Coder` + `main()`，但不是通用 Runner 框架 |
| Model / LLM Adapter | 存在 | `Model` + LiteLLM |
| Tool / Function Tool | 部分存在 | Slash commands 是主工具；function tool coder 存在但多数 deprecated |
| Memory / Session / Context | 存在 | `cur_messages`、`done_messages`、`ChatSummary`、`ChatChunks` |
| Prompt / Instruction | 存在 | 每个 coder 子类绑定 prompt class |
| Multi-agent / Handoff | 部分存在 | `ArchitectCoder` 和 `SwitchCoder`，未发现通用 handoff 框架 |
| Guardrail / Validation | 部分存在 | 工程 validation 很多；未发现独立 guardrail 框架 |
| Tracing / Logging / Hook | 部分存在 | analytics、LLM history、chat history；未发现 OpenTelemetry/span framework |
| RAG / Retrieval | 存在 | `RepoMap` 代码检索，`Help` 文档向量检索 |
| Code execution / Sandbox | 部分存在 | 本地 shell 执行存在；sandbox 未发现 |
| Config / Schema / Type system | 存在 | configargparse、model settings、dataclass、部分 JSON schema |

---

## 9. 快速复习：核心 loop 一页版

```text
入口:
  aider/__main__.py
    -> aider/main.py::main()

初始化:
  args.py::get_parser()
  models.py::Model()
  repo.py::GitRepo()
  commands.py::Commands()
  history.py::ChatSummary()
  base_coder.py::Coder.create()

交互:
  Coder.run()
    -> InputOutput.get_input()
    -> Coder.run_one()

预处理:
  Coder.preproc_user_input()
    -> Commands.run() if "/" or "!"
    -> check_for_file_mentions()
    -> check_for_urls()

构建上下文:
  Coder.format_messages()
    -> Coder.format_chat_chunks()
      -> ChatChunks(system, examples, done, repo, readonly_files, chat_files, cur, reminder)
      -> RepoMap.get_repo_map()

模型调用:
  Coder.send()
    -> Model.send_completion()
      -> litellm.completion()

响应处理:
  show_send_output_stream() / show_send_output()
  add_assistant_reply_to_cur_messages()
  reply_completed()

代码修改:
  apply_updates()
    -> subclass.get_edits()
    -> apply_edits_dry_run()
    -> prepare_to_edit()
    -> subclass.apply_edits()

反馈:
  auto_commit()
  lint_edited()
  run_shell_commands()
  Commands.cmd_test()
  if errors:
    reflected_message = errors
    run_one() repeats
```

---

## 10. 总结判断

Aider 的架构核心不是“复杂 agent 框架”，而是一个非常务实的 Coding Agent：

- 用 `Coder` 管 turn loop
- 用 `Model` 适配 LLM
- 用 `ChatChunks` 管上下文
- 用 `RepoMap` 解决大仓库检索
- 用 edit format 子类解决“LLM 输出如何可靠落盘”
- 用 git/lint/test/reflection 形成工程反馈闭环
- 用 `Commands` 保持用户在控制回路中

如果你正在升级自己的 Coding Agent，最值得借鉴的是：

1. `ChatChunks` 式 prompt 分层
2. `EditBlockCoder` 式可反馈编辑协议
3. `RepoMap` 式代码检索
4. `Coder.run_one()` 式 reflection loop
5. `GitRepo` + `Linter` + `cmd_test()` 式验证闭环
6. `ArchitectCoder` 式 planner/editor 分工

最需要补强的是：

- sandbox
- 结构化 tracing
- 显式 tool scheduler
- 更强类型的 run state / step state
- 现代 Responses API tool-call loop
