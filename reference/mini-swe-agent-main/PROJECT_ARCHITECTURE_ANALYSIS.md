# mini-swe-agent 项目架构分析

> 分析对象：`C:\Users\ch\Desktop\ai agent学习\reference\mini-swe-agent-main`  
> 分析方式：已按项目要求使用 CodeGraph 加速阅读。当前 CodeGraph 状态：133 个文件、1630 个符号、3035 条调用/依赖边，数据库位于 `.codegraph/codegraph.db`。同时结合关键源码文件逐项核对，本文不是 README 级别概述。

## 1. 项目总体定位

### 1.1 这是什么类型的 Agent

`mini-swe-agent` 是一个极简 Coding Agent / Software Engineering Agent。它的核心目标不是提供复杂工具生态，而是用一个短小的 agent loop 驱动 LLM 反复执行 shell 命令、观察输出、继续下一步，直到完成代码任务。

项目的最小运行形态由三类组件组成：

- Agent：`src/minisweagent/agents/default.py` 中的 `DefaultAgent`
- Model：`src/minisweagent/models/litellm_model.py` 中的 `LitellmModel` 或其他适配器
- Environment：`src/minisweagent/environments/local.py` 中的 `LocalEnvironment` 或 Docker/Singularity 等沙箱环境

它本质上是一个“bash-only coding agent”。即使默认版本支持 OpenAI/LiteLLM 风格 native tool call，它暴露给模型的工具也只有一个 `bash` 函数工具，最终所有能力都通过命令行完成。

### 1.2 主要解决什么问题

项目主要解决三类问题：

1. 本地或沙箱内自动完成软件工程任务  
   例如阅读仓库、修改代码、运行测试、生成 patch、提交最终结果。

2. SWE-bench / ProgramBench 等 benchmark 批量评测  
   通过 `src/minisweagent/run/benchmarks/swebench.py` 和 `src/minisweagent/run/benchmarks/programbench.py` 构建容器环境、并发运行实例、保存轨迹与预测文件。

3. 提供一个可读、可改、可测试的 Agent 基线  
   其架构刻意保持线性消息历史、无复杂状态机、无长期 shell session，便于复现、调试、fine-tuning 和扩展。

### 1.3 核心能力

- 线性 Agent loop：`DefaultAgent.run()` 持续调用 `step()`，直到最后一条消息 `role == "exit"`。
- LLM 调用抽象：`Model` 协议定义 `query()`、`format_message()`、`format_observation_messages()`。
- bash 工具调用：`BASH_TOOL` / `BASH_TOOL_RESPONSE_API` 只定义 `command` 参数。
- 文本正则动作解析：`LitellmTextbasedModel` / `OpenRouterTextbasedModel` 支持从 triple backtick 中解析命令。
- 多模型供应商：LiteLLM、OpenRouter、Portkey、Requesty。
- 多执行环境：Local、Docker、Singularity、Bubblewrap、SWE-ReX Docker、SWE-ReX Modal、Contree。
- 人在回路：`InteractiveAgent` 支持 confirm / yolo / human 三种模式。
- 轨迹保存与浏览：`DefaultAgent.save()` 保存 JSON trajectory，`TrajectoryInspector` 用 Textual 浏览。
- benchmark runner：支持 SWE-bench、single SWE-bench instance、ProgramBench。
- 配置系统：YAML + CLI key-value merge + `.env` 全局配置。

## 2. 项目目录结构

### 2.1 顶层结构

| 路径 | 职责 | 关键性 |
|---|---|---|
| `pyproject.toml` | Python package 元数据、依赖、CLI entry points、ruff/pytest 配置 | 入口和依赖定义 |
| `README.md` | 项目定位、安装与使用介绍 | 背景说明 |
| `src/minisweagent/` | 核心源码包 | 最关键 |
| `tests/` | 单元测试与 CLI 集成测试 | 理解边界行为很有价值 |
| `docs/` | 文档站点源码，含高级说明、模型/环境 reference | 辅助理解 |
| `.github/workflows/` | CI、release、docs 构建 | 工程设施 |
| `.codegraph/` | CodeGraph 索引数据库 | 本次阅读辅助 |

### 2.2 核心源码目录

| 路径 | 职责 |
|---|---|
| `src/minisweagent/__init__.py` | 定义版本、全局 config 路径、加载 `.env`、核心 `Protocol`：`Model`、`Environment`、`Agent` |
| `src/minisweagent/__main__.py` | `python -m minisweagent` 入口，调用 `minisweagent.run.mini.app` |
| `src/minisweagent/agents/` | Agent loop 与交互式 agent |
| `src/minisweagent/models/` | LLM 适配器、测试模型、模型选择工厂 |
| `src/minisweagent/models/utils/` | action 解析、observation 格式化、retry、cache control、多模态展开、展示文本抽取 |
| `src/minisweagent/environments/` | 命令执行环境与沙箱实现 |
| `src/minisweagent/config/` | 内置 YAML prompt/config |
| `src/minisweagent/run/` | CLI runner、hello world、benchmark runner、trajectory inspector |
| `src/minisweagent/utils/` | 日志、递归 merge 等通用工具 |
| `src/minisweagent/exceptions.py` | Agent 控制流异常 |

### 2.3 关键入口文件

| 入口 | 文件 | 说明 |
|---|---|---|
| `mini` / `mini-swe-agent` CLI | `src/minisweagent/run/mini.py` | 默认交互式本地运行入口 |
| `python -m minisweagent` | `src/minisweagent/__main__.py` | 调用 `run.mini.app()` |
| Python binding 最小示例 | `src/minisweagent/run/hello_world.py` | 手动组装 `DefaultAgent(LitellmModel, LocalEnvironment)` |
| extra CLI | `src/minisweagent/run/utilities/mini_extra.py` | 分发 `config`、`inspect`、`swebench`、`programbench` 子命令 |
| SWE-bench batch | `src/minisweagent/run/benchmarks/swebench.py` | 并发跑 benchmark，保存 `preds.json` 和 traj |
| SWE-bench single | `src/minisweagent/run/benchmarks/swebench_single.py` | 单实例评测 |
| ProgramBench | `src/minisweagent/run/benchmarks/programbench.py` | ProgramBench 批量运行与 submission tar 复制 |
| trajectory inspector | `src/minisweagent/run/utilities/inspector.py` | Textual UI 浏览轨迹 |

## 3. Agent 主运行流程

### 3.1 从用户输入到最终输出的完整链路

以默认 CLI `mini` 为例：

1. 用户运行 CLI  
   入口是 `src/minisweagent/run/mini.py::main()`，CLI 参数包括 `--model`、`--agent-class`、`--environment-class`、`--task`、`--yolo`、`--cost-limit`、`--config`、`--output`。

2. 首次配置检查  
   `main()` 调用 `configure_if_first_time()`，来自 `src/minisweagent/run/utilities/config.py`。如果 `MSWEA_CONFIGURED` 不存在，会提示配置默认模型和 API key。

3. 构建配置  
   `main()` 对每个 `config_spec` 调用 `get_config_from_spec()`，再追加 CLI 覆盖项，并用 `recursive_merge()` 合并。  
   相关文件：
   - `src/minisweagent/config/__init__.py::get_config_from_spec()`
   - `src/minisweagent/utils/serialize.py::recursive_merge()`

4. 获取任务文本  
   如果 `--task` 没有提供，`main()` 调用 `_multiline_prompt()` 读取用户输入。  
   相关文件：`src/minisweagent/agents/utils/prompt_user.py`

5. 实例化组件  
   - `get_model(config=config["model"])` 创建模型
   - `get_environment(config["environment"], default_type="local")` 创建环境
   - `get_agent(model, env, config["agent"], default_type="interactive")` 创建 agent

6. 进入 Agent loop  
   `agent.run(run_task)` 进入 `DefaultAgent.run()` 或 `InteractiveAgent.run()` 继承的主循环。

7. 初始化消息  
   `DefaultAgent.run()` 渲染 `system_template` 和 `instance_template`，用 `model.format_message()` 生成 system/user 初始消息。

8. 每轮 step  
   `DefaultAgent.step()` 调用 `self.query()` 获取模型响应，再调用 `self.execute_actions(message)` 执行动作并追加 observation。

9. LLM 调用  
   `DefaultAgent.query()` 调用 `self.model.query(self.messages)`。真实 API 调用发生在各模型适配器的 `_query()`，例如：
   - `LitellmModel._query()` 调用 `litellm.completion(...)`
   - `LitellmResponseModel._query()` 调用 `litellm.responses(...)`
   - `OpenRouterModel._query()` 调用 `requests.post("https://openrouter.ai/api/v1/chat/completions", ...)`
   - `PortkeyModel._query()` 调用 `self.client.chat.completions.create(...)`

10. 解析 action  
    模型适配器把响应解析为 `message["extra"]["actions"]`。默认 tool-call 路线使用 `parse_toolcall_actions()`，Responses API 路线使用 `parse_toolcall_actions_response()`，文本路线使用 `parse_regex_actions()`。

11. 执行命令  
    `DefaultAgent.execute_actions()` 对每个 action 调用 `self.env.execute(action)`。例如 `LocalEnvironment.execute()` 用 `subprocess.run(shell=True)` 执行命令。

12. 格式化 observation  
    执行输出被传给 `model.format_observation_messages()`，生成下一轮要喂给模型的 tool/user observation 消息。

13. 完成检测  
    环境的 `_check_finished()` 检查命令输出第一行是否为 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 且 returncode 为 0。若成立，抛出 `Submitted`，携带 `role="exit"` 消息。

14. loop 退出  
    `DefaultAgent.run()` 捕获 `InterruptAgentFlow`，把异常携带的消息追加到 `self.messages`。如果最后一条消息 `role == "exit"`，退出循环并返回 `extra`，其中通常包含 `exit_status` 与 `submission`。

15. 保存轨迹  
    `DefaultAgent.run()` 每轮 finally 中调用 `self.save(self.config.output_path)`，保存 JSON trajectory。

### 3.2 核心 loop 的流程图式文字

```text
CLI / Python binding
  -> load YAML config + CLI override + env defaults
  -> instantiate Model
  -> instantiate Environment
  -> instantiate Agent
  -> agent.run(task)

DefaultAgent.run(task)
  -> extra_template_vars["task"] = task
  -> messages = [
       model.format_message(role="system", rendered system_template),
       model.format_message(role="user", rendered instance_template)
     ]
  -> while True:
       try:
         step()
       except InterruptAgentFlow as e:
         add_messages(*e.messages)
       except Exception as e:
         handle_uncaught_exception(e)
         raise
       finally:
         save(output_path)

       if messages[-1]["role"] == "exit":
         break

DefaultAgent.step()
  -> message = query()
  -> execute_actions(message)

DefaultAgent.query()
  -> enforce step/cost/wall-time limits
  -> n_calls += 1
  -> message = model.query(messages)
       -> adapter prepares messages
       -> adapter calls provider API
       -> adapter parses tool calls / text action into extra.actions
       -> adapter calculates cost
  -> cost += message.extra.cost
  -> add_messages(message)
  -> return message

DefaultAgent.execute_actions(message)
  -> actions = message.extra.actions
  -> outputs = [env.execute(action) for action in actions]
       -> run shell command locally or in sandbox
       -> capture stdout/stderr/returncode/exception_info
       -> if first output line is COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT:
            raise Submitted(exit message)
  -> observation_messages = model.format_observation_messages(message, outputs, template_vars)
  -> add_messages(*observation_messages)
```

### 3.3 tool call / final answer / handoff / code execution 调度

| 行为 | 调度位置 | 真实实现 |
|---|---|---|
| LLM 调用 | `DefaultAgent.query()` | `self.model.query(self.messages)` |
| tool call 解析 | 各模型适配器 `_parse_actions()` | `actions_toolcall.py`、`actions_toolcall_response.py`、`actions_text.py` |
| tool call 执行 | `DefaultAgent.execute_actions()` | `self.env.execute(action)` |
| code execution | 各 `Environment.execute()` | `subprocess.run`、`docker exec`、`singularity exec`、SWE-ReX、Contree |
| observation 生成 | `model.format_observation_messages()` | 根据模型 API 形态生成 `tool` / `user` / `function_call_output` |
| final answer | 各环境 `_check_finished()` | 命令输出第一行 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 触发 `Submitted` |
| handoff | 未发现正式模块 | 没有 OpenAI Agents SDK 式 handoff；只有 `InteractiveAgent` 的“用户追加新任务”继续循环 |

## 4. 核心模块分析

### 4.1 Agent / Runner / Loop

#### 职责

Agent 模块负责调度模型调用、动作执行、observation 追加、限制检查、轨迹保存。它不关心具体 LLM provider，也不关心命令在哪里执行。

#### 关键文件

- `src/minisweagent/agents/default.py`
- `src/minisweagent/agents/interactive.py`
- `src/minisweagent/agents/__init__.py`
- `src/minisweagent/run/mini.py`
- `src/minisweagent/run/hello_world.py`
- `src/minisweagent/run/benchmarks/swebench.py`
- `src/minisweagent/run/benchmarks/programbench.py`

#### 关键类 / 函数

- `AgentConfig`：系统 prompt、实例 prompt、step/cost/wall-time 限制、输出路径。
- `DefaultAgent.__init__()`：注入 `model` 与 `env`。
- `DefaultAgent.run()`：主 loop。
- `DefaultAgent.step()`：单轮调度。
- `DefaultAgent.query()`：限制检查 + LLM 调用 + 成本累计。
- `DefaultAgent.execute_actions()`：执行 actions 并追加 observations。
- `DefaultAgent.serialize()` / `save()`：输出 trajectory。
- `InteractiveAgent`：扩展 `DefaultAgent`，加入终端展示、确认、human mode、slash commands。
- `get_agent_class()` / `get_agent()`：按配置动态 import agent 类。

#### 调用关系

`run/mini.py::main()` -> `get_agent()` -> `DefaultAgent` 或 `InteractiveAgent` -> `run()` -> `step()` -> `query()` -> `model.query()` -> `execute_actions()` -> `env.execute()` -> `model.format_observation_messages()`。

#### 可复用价值

高。`DefaultAgent` 将 agent loop 压缩到非常少的状态：`messages`、`cost`、`n_calls`、`extra_template_vars`。如果你从零写 Coding Agent，这个 loop 是最值得先读的模块。

### 4.2 Model / LLM Adapter

#### 职责

Model 模块负责适配不同 LLM API，并把模型响应统一转成 agent 可执行的 `message.extra.actions`。

#### 关键文件

- `src/minisweagent/models/__init__.py`
- `src/minisweagent/models/litellm_model.py`
- `src/minisweagent/models/litellm_textbased_model.py`
- `src/minisweagent/models/litellm_response_model.py`
- `src/minisweagent/models/openrouter_model.py`
- `src/minisweagent/models/openrouter_textbased_model.py`
- `src/minisweagent/models/openrouter_response_model.py`
- `src/minisweagent/models/portkey_model.py`
- `src/minisweagent/models/portkey_response_model.py`
- `src/minisweagent/models/requesty_model.py`
- `src/minisweagent/models/extra/roulette.py`
- `src/minisweagent/models/test_models.py`

#### 关键类 / 函数

- `Model` 协议：`src/minisweagent/__init__.py`
- `get_model()`：解析模型名称、模型类、Anthropic cache control 默认设置。
- `get_model_class()`：从 `_MODEL_CLASS_MAPPING` 或完整 import path 得到模型类。
- `GlobalModelStats`：全局成本/调用次数统计与限制。
- `LitellmModel.query()`：默认 chat completions 工具调用路径。
- `LitellmResponseModel.query()`：Responses API 路径。
- `LitellmTextbasedModel._parse_actions()`：从文本中用 regex 抽取命令。
- `OpenRouterModel` / `PortkeyModel` / `RequestyModel`：不同 provider 适配。
- `RouletteModel` / `InterleavingModel`：meta-model，用于随机或按序切换子模型。
- `DeterministicModel` / `DeterministicToolcallModel` / `DeterministicResponseAPIToolcallModel`：测试模型。

#### 调用关系

`DefaultAgent.query()` 调用 `model.query(messages)`。每个模型的 `query()` 通常执行：

1. `_prepare_messages_for_api()`
2. `_query()` 调用真实 provider
3. `_calculate_cost()`
4. `GLOBAL_MODEL_STATS.add(cost)`
5. `_parse_actions()`
6. 返回带 `extra.actions`、`extra.cost`、`extra.timestamp` 的 message

#### 可复用价值

高。这个项目的亮点是“动作解析属于 Model adapter，而不是 Agent”。这样 Agent loop 无需区分 toolcall、Responses API 或文本正则格式。

### 4.3 Tool / Function Tool

#### 职责

项目没有通用工具注册系统，也没有任意 function tool runtime。工具被刻意限制为一个 bash 工具。

#### 关键文件

- `src/minisweagent/models/utils/actions_toolcall.py`
- `src/minisweagent/models/utils/actions_toolcall_response.py`
- `src/minisweagent/models/utils/actions_text.py`

#### 关键结构 / 函数

- `BASH_TOOL`：Chat Completions 风格 function tool，名称 `bash`，参数 `command: string`。
- `BASH_TOOL_RESPONSE_API`：Responses API 风格 function tool。
- `parse_toolcall_actions()`：解析 OpenAI/LiteLLM tool calls。
- `parse_toolcall_actions_response()`：解析 Responses API `function_call` items。
- `parse_regex_actions()`：文本模式下从 `action_regex` 抽取命令。
- `format_toolcall_observation_messages()`：把执行输出转成 tool result。
- `format_observation_messages()`：文本模式下把执行输出转成 user observation。

#### 调用关系

模型适配器在 `_query()` 请求中传入 `tools=[BASH_TOOL]` 或 `tools=[BASH_TOOL_RESPONSE_API]`。响应回来后 `_parse_actions()` 调用解析函数，输出 `{"command": "...", "tool_call_id": "..."}` 形式 action。Agent 不直接理解 provider 原始 tool call。

#### 可复用价值

高。对 Coding Agent 而言，把工具面缩小为 bash 可以大幅降低框架复杂度。缺点是缺少 typed tools、权限系统、工具级超时/审计等高级能力。

### 4.4 Memory / Session / Context

#### 发现情况

未发现独立长期 memory、session store、conversation compaction、vector memory 或数据库化 session 模块。

#### 现有机制

- `DefaultAgent.messages`：当前运行的完整线性消息历史。
- `DefaultAgent.extra_template_vars`：给 prompt/observation 模板使用的上下文变量，如 task、instance、模型统计。
- `DefaultAgent.save()`：保存 trajectory JSON，可用于后续查看，但不是在线 memory。
- `prompt_user.py` 中 `FileHistory(global_config_dir / "interactive_history.txt")`：只保存交互式终端输入历史，不是 Agent memory。
- `TrajectoryInspector`：读取历史轨迹用于人类浏览，不参与 agent 推理。

#### 可复用价值

中。线性消息历史非常适合最小实现和调试，但如果你的 Agent 要长时间运行，需要补充压缩、摘要、检索或 session 持久化。

### 4.5 Prompt / Instruction

#### 职责

Prompt 由 YAML 配置驱动，通过 Jinja2 模板渲染。Agent 本身只负责渲染，不硬编码任务说明。

#### 关键文件

- `src/minisweagent/config/default.yaml`
- `src/minisweagent/config/mini.yaml`
- `src/minisweagent/config/mini_textbased.yaml`
- `src/minisweagent/config/benchmarks/swebench.yaml`
- `src/minisweagent/config/benchmarks/programbench.yaml`
- `src/minisweagent/agents/default.py::_render_template()`
- `src/minisweagent/agents/default.py::get_template_vars()`

#### 关键设计

- `system_template` 与 `instance_template` 分离。
- `get_template_vars()` 合并 agent config、environment vars、model config、运行统计、额外变量。
- observation 模板也由 model config 控制，例如输出过长时只暴露 head/tail。
- benchmark prompt 可以单独定制，例如 SWE-bench 要求生成 patch，ProgramBench 要求避免源码搜索和二进制反编译。

#### 可复用价值

高。把系统指令、任务指令、observation 渲染都放入 YAML，让同一 loop 可服务本地任务和 benchmark。

### 4.6 Multi-agent / Handoff

#### 发现情况

未发现正式 multi-agent/handoff 框架。

项目中有两个容易误解为 multi-agent 的点：

- `src/minisweagent/models/extra/roulette.py::RouletteModel` / `InterleavingModel`：它们是多模型选择，不是多个 agent 协作。
- `InteractiveAgent._check_for_new_task_or_submit()`：用户可以在 agent 想退出时添加新任务，继续同一个 agent loop，不是 handoff。

#### 可复用价值

低到中。如果你需要真正的 planner/worker/reviewer 多 agent，这个项目只能提供“单 agent loop + 多模型切换”的参考。

### 4.7 Guardrail / Validation

#### 职责

项目没有独立 guardrail 框架，但有几类实用验证与控制流保护。

#### 关键文件

- `src/minisweagent/exceptions.py`
- `src/minisweagent/models/utils/actions_toolcall.py`
- `src/minisweagent/models/utils/actions_toolcall_response.py`
- `src/minisweagent/models/utils/actions_text.py`
- `src/minisweagent/agents/default.py`
- `src/minisweagent/agents/interactive.py`
- `src/minisweagent/config/benchmarks/programbench.yaml`

#### 关键类 / 函数

- `FormatError`：模型输出无法解析为 action 时抛出。
- `LimitsExceeded` / `TimeExceeded`：step、cost、wall-time 限制。
- `Submitted`：任务完成控制流。
- `parse_toolcall_actions()`：校验必须有 tool call、必须是 `bash`、arguments 必须是 JSON、必须含 `command`。
- `parse_regex_actions()`：文本模式下必须正好一个 action。
- `InteractiveAgent._ask_confirmation_or_interrupt()`：confirm mode 下人工确认。
- benchmark prompt 的规则约束：例如 ProgramBench 禁止查源码、禁止包装原始 binary、禁止反编译。

#### 可复用价值

中。这里的 guardrail 偏工程控制流，不是模型安全/内容安全框架。适合借鉴“format error 作为 user message 回灌模型重新尝试”的设计。

### 4.8 Tracing / Logging / Hook

#### 职责

项目没有 OpenTelemetry/OpenAI Agents SDK 风格 tracing，也没有 hook manager。主要依赖日志与 trajectory。

#### 关键文件

- `src/minisweagent/utils/log.py`
- `src/minisweagent/agents/default.py::serialize()` / `save()`
- `src/minisweagent/run/utilities/inspector.py`
- `src/minisweagent/run/benchmarks/utils/batch_progress.py`
- `src/minisweagent/run/benchmarks/utils/common.py`

#### 现有能力

- `add_file_handler()` 给 `minisweagent` logger 添加文件输出。
- `DefaultAgent.serialize()` 保存完整 `messages`、模型配置、环境配置、成本、API 调用次数、版本、exit status、submission。
- `TrajectoryInspector` 将轨迹按 step 分页浏览。
- `RunBatchProgressManager` 用 rich live UI 展示批量实例状态和 exit status 汇总。
- `ProgressTrackingAgent.step()` 在每步前更新 progress manager。

#### Hook 情况

`DefaultAgent.query()` 的 docstring 写了 “Override to add hooks”，实际没有 hook registry。扩展方式是继承 `DefaultAgent` 并 override `query()`、`step()`、`execute_actions()` 或 `add_messages()`。

#### 可复用价值

中到高。trajectory 保存非常值得参考；正式 tracing/hook 需要自己补。

### 4.9 RAG / Retrieval

#### 发现情况

未发现 RAG、retrieval、embedding、vector store、语义检索模块。

`docs/data/all_models.txt` 中出现 embedding 模型名称，但只是模型列表数据，不构成 RAG 功能。

#### 可复用价值

低。若你的 Agent 需要项目索引/代码语义搜索，应另行集成 CodeGraph、ripgrep、LSP 或向量检索。

### 4.10 Code execution / Sandbox

#### 职责

环境模块将 `{"command": "..."}` action 执行为 shell 命令，并返回统一输出结构。

#### 关键文件

- `src/minisweagent/environments/local.py`
- `src/minisweagent/environments/docker.py`
- `src/minisweagent/environments/singularity.py`
- `src/minisweagent/environments/extra/bubblewrap.py`
- `src/minisweagent/environments/extra/swerex_docker.py`
- `src/minisweagent/environments/extra/swerex_modal.py`
- `src/minisweagent/environments/extra/contree.py`
- `src/minisweagent/environments/__init__.py`

#### 关键类 / 函数

- `Environment` 协议：`execute()`、`get_template_vars()`、`serialize()`。
- `LocalEnvironment.execute()`：本机 `subprocess.run(shell=True)`。
- `DockerEnvironment._start_container()`：启动长期 sleep 容器。
- `DockerEnvironment.execute()`：`docker exec -w <cwd> ... bash -lc <command>`。
- `SingularityEnvironment._build_sandbox()`：构建 writable sandbox。
- `BubblewrapEnvironment.execute()`：通过 `bwrap` 隔离运行。
- `SwerexDockerEnvironment` / `SwerexModalEnvironment`：基于 SWE-ReX runtime。
- `ContreeEnvironment.execute()`：基于 contree SDK session。
- 各环境 `_check_finished()`：统一 final submission 检测。

#### 关键设计

- 没有持久 shell session；每个 action 都是独立命令。
- 命令状态不自动持久化，prompt 明确提醒用户每次都是新 subshell。
- stdout 与 stderr 合并，便于模型观察。
- timeout 和 exception 被结构化为 `returncode=-1` 与 `exception_info`。

#### 可复用价值

高。沙箱接口极简，适合直接迁移为自己的 `CommandExecutor` 层。

### 4.11 Config / Schema / Type system

#### 职责

配置系统负责用 YAML、CLI override 和环境变量组装 agent/model/environment 参数。类型系统用 Pydantic BaseModel 与 Python Protocol 维持轻量约束。

#### 关键文件

- `src/minisweagent/__init__.py`
- `src/minisweagent/config/__init__.py`
- `src/minisweagent/utils/serialize.py`
- `src/minisweagent/run/mini.py`
- `src/minisweagent/agents/default.py`
- 各 `*Config` 类文件

#### 关键类 / 函数

- `Model` / `Environment` / `Agent` Protocol。
- `AgentConfig` / `InteractiveAgentConfig`。
- `LitellmModelConfig`、`OpenRouterModelConfig`、`PortkeyModelConfig`、`RequestyModelConfig`。
- `LocalEnvironmentConfig`、`DockerEnvironmentConfig`、`SingularityEnvironmentConfig` 等。
- `get_config_path()`：配置文件查找。
- `_key_value_spec_to_nested_dict()`：把 `model.model_kwargs.temperature=0` 转为嵌套 dict。
- `get_config_from_spec()`：加载 YAML 或 key-value spec。
- `recursive_merge()`：递归合并配置，跳过 `UNSET`。
- `get_agent_class()` / `get_model_class()` / `get_environment_class()`：短名映射或完整 import path。

#### 可复用价值

高。该设计很适合个人 Agent：核心类只接受 `**kwargs`，Pydantic config 做校验，工厂函数负责短名和动态 import。

## 5. 关键数据结构

| 数据结构 | 文件路径 | 类型 | 运行中的作用 |
|---|---|---|---|
| `Model` | `src/minisweagent/__init__.py` | `Protocol` | 约束模型适配器必须实现查询、消息格式化、observation 格式化、模板变量、序列化 |
| `Environment` | `src/minisweagent/__init__.py` | `Protocol` | 约束执行环境必须实现 `execute()`、模板变量、序列化 |
| `Agent` | `src/minisweagent/__init__.py` | `Protocol` | 约束 agent 必须实现 `run()` 与 `save()` |
| `AgentConfig` | `src/minisweagent/agents/default.py` | `BaseModel` | 主 loop 配置：prompt、step/cost/wall-time 限制、输出路径 |
| `DefaultAgent.messages` | `src/minisweagent/agents/default.py` | `list[dict]` | 完整线性对话轨迹，也是下一轮传给 LLM 的上下文 |
| `InteractiveAgentConfig` | `src/minisweagent/agents/interactive.py` | `BaseModel` | confirm/yolo/human 模式、白名单、退出确认 |
| `BASH_TOOL` | `src/minisweagent/models/utils/actions_toolcall.py` | `dict` | Chat Completions function tool schema |
| `BASH_TOOL_RESPONSE_API` | `src/minisweagent/models/utils/actions_toolcall_response.py` | `dict` | Responses API function tool schema |
| action dict | 多处 | `dict` | 通常为 `{"command": "...", "tool_call_id": "..."}`，Agent 只读取 `command` 给环境执行 |
| execution output dict | 多处环境 | `dict` | 通常为 `{"output": str, "returncode": int, "exception_info": str, "extra": dict}` |
| `LitellmModelConfig` | `src/minisweagent/models/litellm_model.py` | `BaseModel` | LiteLLM 模型名、kwargs、成本追踪、observation 模板、format error 模板 |
| `LitellmTextbasedModelConfig` | `src/minisweagent/models/litellm_textbased_model.py` | `BaseModel` | 在 LiteLLM 基础上增加 `action_regex` |
| `LitellmResponseModelConfig` | `src/minisweagent/models/litellm_response_model.py` | `BaseModel` | Responses API LiteLLM 配置 |
| `OpenRouterModelConfig` | `src/minisweagent/models/openrouter_model.py` | `BaseModel` | OpenRouter API 配置 |
| `PortkeyModelConfig` | `src/minisweagent/models/portkey_model.py` | `BaseModel` | Portkey chat completions 配置 |
| `PortkeyResponseAPIModelConfig` | `src/minisweagent/models/portkey_response_model.py` | `BaseModel` | Portkey Responses API 配置 |
| `RequestyModelConfig` | `src/minisweagent/models/requesty_model.py` | `BaseModel` | Requesty API 配置 |
| `GlobalModelStats` | `src/minisweagent/models/__init__.py` | class | 全局成本和调用次数统计，可由环境变量设置全局限制 |
| `RouletteModelConfig` | `src/minisweagent/models/extra/roulette.py` | `BaseModel` | 多模型随机切换配置 |
| `InterleavingModelConfig` | `src/minisweagent/models/extra/roulette.py` | `BaseModel` | 多模型固定序列切换配置 |
| `LocalEnvironmentConfig` | `src/minisweagent/environments/local.py` | `BaseModel` | 本地 cwd/env/timeout |
| `DockerEnvironmentConfig` | `src/minisweagent/environments/docker.py` | `BaseModel` | Docker 镜像、cwd、env、timeout、run args、interpreter |
| `SingularityEnvironmentConfig` | `src/minisweagent/environments/singularity.py` | `BaseModel` | Singularity image、sandbox build、exec args |
| `BubblewrapEnvironmentConfig` | `src/minisweagent/environments/extra/bubblewrap.py` | `BaseModel` | Bubblewrap wrapper args、cwd/env/timeout |
| `SwerexDockerEnvironmentConfig` | `src/minisweagent/environments/extra/swerex_docker.py` | `BaseModel` | SWE-ReX Docker image/cwd/timeout |
| `SwerexModalEnvironmentConfig` | `src/minisweagent/environments/extra/swerex_modal.py` | `BaseModel` | Modal sandbox 参数 |
| `ContreeEnvironmentConfig` | `src/minisweagent/environments/extra/contree.py` | `BaseModel` | Contree image/session/cwd/env/interpreter |
| `ExecutionResult` | `src/minisweagent/environments/extra/contree.py` | `TypedDict` | Contree 执行输出结构 |
| `InterruptAgentFlow` | `src/minisweagent/exceptions.py` | Exception | 用异常携带要追加的消息，打断正常 step |
| `Submitted` | `src/minisweagent/exceptions.py` | Exception | 任务完成信号 |
| `LimitsExceeded` | `src/minisweagent/exceptions.py` | Exception | step/cost 限制触发 |
| `TimeExceeded` | `src/minisweagent/exceptions.py` | Exception | wall-time 限制触发 |
| `UserInterruption` | `src/minisweagent/exceptions.py` | Exception | 交互式用户打断或拒绝 |
| `FormatError` | `src/minisweagent/exceptions.py` | Exception | 模型输出格式错误，回灌给模型重试 |
| trajectory dict | `DefaultAgent.serialize()` | `dict` | 保存 `info`、`messages`、`trajectory_format`，供 inspector 和 benchmark 使用 |
| `RunBatchProgressManager` | `src/minisweagent/run/benchmarks/utils/batch_progress.py` | class | 批量 benchmark 进度 UI 和 exit status 汇总 |

## 6. 可参考模块清单

| 模块名称 | 文件路径 | 解决的问题 | 设计亮点 | 适合借鉴的地方 | 迁移难度 |
|---|---|---|---|---|---|
| 最小 Agent Loop | `src/minisweagent/agents/default.py` | 驱动 LLM、执行 action、保存轨迹 | 状态少，流程线性，易调试 | 从零实现 Coding Agent 时直接参考 `run()`/`step()`/`query()`/`execute_actions()` | 低 |
| 交互式 Agent | `src/minisweagent/agents/interactive.py` | 人工确认、手动命令、打断恢复 | confirm/yolo/human 三模式清晰 | 给危险命令执行加人工确认 | 中 |
| 模型协议 | `src/minisweagent/__init__.py` | 解耦 Agent 与 LLM provider | `Protocol` 足够轻，不强迫继承 | 定义自己的 `ModelAdapter` 接口 | 低 |
| LiteLLM 适配器 | `src/minisweagent/models/litellm_model.py` | 统一多 provider Chat Completions | action 解析和 observation 格式化都在 model 层 | 快速接入 OpenAI/Anthropic/Gemini 等 | 中 |
| Responses API 适配 | `src/minisweagent/models/litellm_response_model.py` | 支持 Responses API tool call 格式 | flatten response output，输出 `function_call_output` | 如果使用 GPT-5/Responses API，可参考结构 | 中 |
| Tool call 解析 | `src/minisweagent/models/utils/actions_toolcall.py` | 把 provider tool call 变成 bash action | 小而完整的格式校验 | 复用“解析失败变成 FormatError 消息”思路 | 低 |
| Responses tool call 解析 | `src/minisweagent/models/utils/actions_toolcall_response.py` | 适配 Responses API `function_call` item | 与 Chat Completions 分离 | 多 API 格式并存时值得参考 | 中 |
| 文本 action 解析 | `src/minisweagent/models/utils/actions_text.py` | 不支持 native tool call 的模型仍可用 | regex + exactly-one action | 兼容弱模型或本地模型 | 低 |
| 本地执行环境 | `src/minisweagent/environments/local.py` | 本机 shell 执行 | subprocess 独立执行，结构化输出 | 直接作为最小 executor 参考 | 低 |
| Docker 沙箱 | `src/minisweagent/environments/docker.py` | 隔离执行、benchmark 环境 | 长期容器 + 每步 `docker exec` | 给 Coding Agent 加沙箱的优先参考 | 中 |
| Singularity 沙箱 | `src/minisweagent/environments/singularity.py` | HPC/无 Docker 场景 | 构建 writable sandbox | 如果目标用户用 Singularity，可参考 | 中 |
| Bubblewrap 沙箱 | `src/minisweagent/environments/extra/bubblewrap.py` | Linux 轻量隔离 | wrapper args 可配置 | 轻量 sandbox 思路 | 中 |
| SWE-ReX 环境 | `src/minisweagent/environments/extra/swerex_docker.py`, `src/minisweagent/environments/extra/swerex_modal.py` | 标准化远程/容器 runtime | 复用 SWE-ReX deployment | 需要云沙箱时参考 | 高 |
| 配置加载 | `src/minisweagent/config/__init__.py` | YAML 和 CLI key-value 统一 | `a.b.c=value` 转嵌套 dict | CLI 可配置 Agent 的低成本实现 | 低 |
| 递归配置合并 | `src/minisweagent/utils/serialize.py` | 多配置层合并 | `UNSET` 允许 CLI 不覆盖默认值 | 配置覆盖逻辑值得直接借鉴 | 低 |
| Agent/Model/Env 工厂 | `src/minisweagent/agents/__init__.py`, `src/minisweagent/models/__init__.py`, `src/minisweagent/environments/__init__.py` | 通过短名或 import path 实例化组件 | 扩展无需改 CLI 主逻辑 | 插件化/可替换组件基础 | 低 |
| trajectory 保存 | `src/minisweagent/agents/default.py::serialize()` | 调试、复现、评测输出 | 保存消息、配置、成本、版本、结果 | 必须优先借鉴，方便复盘 Agent 行为 | 低 |
| trajectory inspector | `src/minisweagent/run/utilities/inspector.py` | 浏览 agent 运行轨迹 | 兼容多种消息格式 | 初期可先用 JSON，后续再做 UI | 中 |
| benchmark runner | `src/minisweagent/run/benchmarks/swebench.py` | 并发跑 SWE-bench | 线程池、环境构建、结果文件、轨迹保存 | 做评测平台时参考 | 中 |
| batch progress UI | `src/minisweagent/run/benchmarks/utils/batch_progress.py` | 批量任务进度可视化 | rich Live + YAML summary | 长任务监控可参考 | 中 |
| deterministic test models | `src/minisweagent/models/test_models.py` | 不调用真实 LLM 测 agent loop | 构造文本/toolcall/Responses 三类输出 | 写 Agent 单测时非常值得借鉴 | 低 |
| retry 策略 | `src/minisweagent/models/utils/retry.py` | LLM 临时错误重试 | tenacity + abort exceptions | 简单可靠的 provider 重试层 | 低 |
| cache control | `src/minisweagent/models/utils/cache_control.py` | Anthropic prompt caching 标记 | 默认给最后一条消息加 ephemeral | 若使用 Anthropic，可参考 | 中 |
| 多模型切换 | `src/minisweagent/models/extra/roulette.py` | 随机/按序切换模型 | meta-model 包装普通 model | 做 ensemble 或 A/B 测试可参考 | 低 |

## 7. 与我自己的 Agent 升级相关的建议

### 7.1 最值得优先学习的模块

1. `src/minisweagent/agents/default.py`  
   优先理解 `DefaultAgent.run()`、`step()`、`query()`、`execute_actions()`。这是项目最核心的抽象：Agent 只做调度，不做 provider 细节和执行环境细节。

2. `src/minisweagent/models/utils/actions_toolcall.py`  
   学习如何把模型 tool call 转成统一 action，以及格式错误如何变成可追加到消息历史的 `FormatError`。

3. `src/minisweagent/environments/local.py` 和 `src/minisweagent/environments/docker.py`  
   学习“无长期 shell session”的执行模型、timeout 捕获、stdout/stderr 合并、完成信号检测。

4. `src/minisweagent/config/mini.yaml`  
   学习如何把 coding workflow、命令执行规则、提交协议、输出裁剪写成可替换 prompt。

5. `src/minisweagent/models/test_models.py`  
   学习如何不依赖真实 LLM 测试 agent loop。

### 7.2 适合直接参考设计的模块

- 线性消息历史：直接用 `messages: list[dict]` 作为上下文和轨迹，早期不要上复杂 memory。
- `Model` / `Environment` / `Agent` 三协议分层：适合自己的 Agent 做最小可扩展架构。
- `Environment.execute(action)` 返回统一结构：后续可以替换成本地、Docker、远程沙箱。
- `Submitted` / `FormatError` 等控制流异常：用异常携带消息回到 loop，代码简洁。
- YAML prompt + Jinja2 模板：把 prompt 从代码中抽离出来，适合快速试验。
- trajectory JSON：调试 Agent 必备，应作为第一版就内建的能力。
- deterministic model 测试：保证 loop、格式化、异常、完成信号不依赖真实 API。

### 7.3 暂时不建议照搬的模块

- `InteractiveAgent` 的终端 UI 细节  
  如果你的第一目标是自动化 Coding Agent，先实现非交互 loop，再加 confirm/human mode。

- 所有 provider adapter 全量支持  
  初期先支持一个主模型 API。该项目 provider 很多，但复制所有适配器会扩大维护面。

- SWE-bench / ProgramBench runner  
  如果还没有稳定 agent loop，benchmark runner 会引入 datasets、Docker image、并发、结果文件等复杂度。

- `SwerexModalEnvironment` / `ContreeEnvironment`  
  云沙箱和第三方 SDK 集成适合后期扩展，第一版建议先 Local + Docker。

- `RouletteModel` / `InterleavingModel`  
  多模型切换有研究价值，但对第一版 Coding Agent 不是核心。

- Textual trajectory inspector  
  轨迹保存必须有，UI 可以后置。先保证 JSON 可读、可复现。

### 7.4 如果我从零设计自己的 Coding Agent，推荐路线

1. 第一阶段：复刻最小闭环  
   实现 `AgentLoop`、`ModelAdapter`、`CommandEnvironment`、`messages`、`trajectory save`、`Submitted` 完成信号。

2. 第二阶段：增加安全和稳定性  
   加入 Docker 沙箱、命令超时、输出裁剪、成本/step 限制、人工确认。

3. 第三阶段：提高可观测性  
   引入 structured trajectory、step-level 日志、运行指标、失败原因分类。

4. 第四阶段：增强上下文能力  
   再加入 code index、retrieval、文件编辑专用工具、memory/compaction。

5. 第五阶段：做评测与多模型实验  
   参考 SWE-bench runner、deterministic test model、Roulette/Interleaving model。

## 8. 按用户要求逐项核对

### 8.1 CodeGraph 使用情况

已使用 CodeGraph：

- `codegraph_status`：确认索引存在，133 files / 1630 nodes / 3035 edges。
- `codegraph_files`：读取项目文件结构。
- `codegraph_context`：定位 Agent loop、model adapter、环境、配置入口。
- `codegraph_explore`：读取 `DefaultAgent`、`InteractiveAgent`、配置与核心符号源码。
- `codegraph_trace`：确认 `run -> step -> query` 与 `step -> execute_actions` 的主调用链。

### 8.2 明确“未发现”的模块

| 模块 | 结论 |
|---|---|
| 独立 Runner 类 | 未发现。runner 是 CLI 函数与 benchmark 函数，不是 `Runner` 类。 |
| 正式 handoff / 多 Agent 协作 | 未发现。只有多模型 meta-model 和用户追加任务继续循环。 |
| 长期 Memory / Session store | 未发现。只有线性 `messages`、trajectory JSON、prompt history。 |
| RAG / Retrieval / Vector DB | 未发现。 |
| 独立 Guardrail 框架 | 未发现。只有 format validation、limits、人机确认和 prompt 规则。 |
| 正式 tracing/span/hook framework | 未发现。主要是 logging、trajectory、progress UI；hook 通过继承 override。 |
| 通用 function tool registry | 未发现。只有 `bash` 一个工具。 |

### 8.3 真实代码结构要点

这个项目的核心架构可以概括为：

```text
配置层
  YAML / CLI key-value / env
  -> recursive_merge
  -> get_model / get_environment / get_agent

运行层
  DefaultAgent.run
  -> messages 初始化
  -> while loop
  -> query model
  -> execute bash actions
  -> append observations
  -> save trajectory
  -> exit on Submitted / LimitsExceeded / TimeExceeded

适配层
  Model adapter:
    provider API <-> mini message.extra.actions
  Environment adapter:
    action.command <-> structured execution output
```

最重要的设计取舍：

- Agent loop 不直接解析 provider 原始响应；解析责任在 Model。
- Agent loop 不直接执行 shell；执行责任在 Environment。
- 没有长期 shell session；每条 action 独立执行。
- `messages` 既是 prompt 上下文，也是 trajectory。
- final answer 不是 LLM 直接返回的自然语言，而是环境通过特殊命令输出触发的 `Submitted`。
- 扩展点主要靠协议 + 工厂 + 继承 override，而不是复杂插件系统。

## 9. 重点源码阅读索引

### 必读

- `src/minisweagent/agents/default.py`
- `src/minisweagent/models/litellm_model.py`
- `src/minisweagent/models/utils/actions_toolcall.py`
- `src/minisweagent/environments/local.py`
- `src/minisweagent/environments/docker.py`
- `src/minisweagent/config/mini.yaml`
- `src/minisweagent/run/mini.py`

### 进阶

- `src/minisweagent/agents/interactive.py`
- `src/minisweagent/models/litellm_response_model.py`
- `src/minisweagent/models/utils/actions_toolcall_response.py`
- `src/minisweagent/run/benchmarks/swebench.py`
- `src/minisweagent/run/benchmarks/programbench.py`
- `src/minisweagent/run/utilities/inspector.py`

### 测试辅助阅读

- `tests/agents/test_default.py`
- `tests/agents/test_interactive.py`
- `tests/models/test_actions_toolcall.py`
- `tests/models/test_test_models.py`
- `tests/environments/test_local.py`
- `tests/run/test_cli_integration.py`

## 10. 一句话总结

`mini-swe-agent` 的核心价值不在于工具多，而在于把 Coding Agent 拆成极少的稳定接口：`Agent` 负责循环，`Model` 负责把 LLM 响应变成 action，`Environment` 负责执行 bash，`messages` 负责上下文和轨迹。对从零实现自己的 Coding Agent 来说，最值得迁移的是这个极简分层、线性 loop、bash-only action、独立执行环境和 trajectory-first 调试方式。
