# PROJECT_ARCHITECTURE_ANALYSIS

## 0. 阅读范围与关键结论

本文基于当前仓库 `C:\Users\ch\Desktop\ai agent学习\reference\OpenHands-main` 的真实源码结构分析。已先使用 CodeGraph 知识图谱读取项目：索引状态为 2059 个文件、26446 个节点、54626 条边、51.11 MB 数据库，覆盖 Python、TypeScript/TSX、YAML 等主要代码。

最重要的结论：这个仓库不是一个只包含单进程 Agent loop 的 SDK 仓库，而是 OpenHands 的应用层仓库。它主要实现 Web UI、V1 App Server、会话生命周期、沙箱编排、事件持久化、设置/密钥/集成/MCP/技能加载等平台能力。真正的核心 Agent loop、LLM 调用循环、默认工具执行器、Function Tool 实现、部分多智能体执行逻辑位于外部依赖包中，当前仓库通过 `pyproject.toml` 引入：

- `openhands-sdk==1.23.1`
- `openhands-agent-server==1.23.1`
- `openhands-tools==1.23.1`
- `litellm==1.84.1`
- `openai==2.33.0`

因此本文会明确区分：

- **本仓库已实现**：App Server、Frontend、Sandbox lifecycle、事件和回调、配置、技能加载、MCP 代理、Git provider 集成、Enterprise 扩展。
- **本仓库未发现源码**：核心 Agent runner/loop、LLM adapter 内部调用、工具 schema 到执行器的调度实现、OpenAI Agents SDK 风格的 handoff 对象、传统 RAG/vector store。
- **本仓库通过依赖接入**：`Agent`、`LLM`、`ConversationSettings`、`StartConversationRequest`、`Skill`、`LLMSummarizingCondenser`、默认工具集、子 Agent 定义等。

## 1. 项目总体定位

### 1.1 这个项目是什么类型的 Agent

OpenHands 是一个面向软件工程任务的 Autonomous Coding Agent 平台。它不是单纯的聊天机器人，而是一个“Web UI + App Server + 沙箱运行时 + Agent Server + SDK Agent loop”的完整系统。

在这个仓库中，核心定位是：

- 为用户创建隔离的代码工作区或沙箱。
- 把用户请求、仓库信息、LLM 配置、密钥、技能、MCP server、插件参数等组装成 Agent Server 可以运行的 `StartConversationRequest`。
- 将真实 Agent 执行过程产生的事件持久化，并通过 WebSocket/HTTP 展示到前端。
- 管理 GitHub/GitLab/Bitbucket/Azure DevOps 等集成，支持创建 PR/MR。
- 支持 OSS/local、remote runtime、SaaS/enterprise 三种不同部署/运行模式。

### 1.2 主要解决的问题

这个项目解决的是“让 AI Agent 在真实代码仓库中安全、可观测、可恢复地完成软件工程任务”的平台问题，具体包括：

- 会话创建：从用户 prompt、仓库选择、任务卡片、插件启动等入口创建 Agent conversation。
- 隔离执行：通过 Docker/remote/process sandbox 提供代码执行环境。
- 工具与代码执行：把 bash、文件编辑、浏览器、搜索、任务跟踪、MCP、Git provider PR 创建等能力暴露给 Agent。
- 配置和密钥：保存用户 LLM 配置、Secrets、Git provider token，并以受控方式注入沙箱。
- 事件观测：把 Action、Observation、Message、状态更新、Hook 执行、ACP tool call 等事件落库/落文件并推送到前端。
- 多模式 Agent：支持普通 coding agent、planning agent、ACP agent、SDK 子 Agent。
- Enterprise：组织、认证、计费、集成、权限、审计等 SaaS 扩展。

### 1.3 核心能力

核心能力可以分成两层。

应用编排层，也就是当前仓库主要实现的部分：

- V1 FastAPI App Server：`openhands/app_server/app.py`、`openhands/app_server/v1_router.py`
- 会话生命周期：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`
- 沙箱生命周期：`openhands/app_server/sandbox/*`
- 事件存储和回调：`openhands/app_server/event/*`、`openhands/app_server/event_callback/*`
- 设置、密钥、用户上下文：`openhands/app_server/settings/*`、`openhands/app_server/secrets/*`、`openhands/app_server/user/*`
- MCP server/proxy：`openhands/app_server/mcp/mcp_router.py`
- 技能/微代理加载：`openhands/app_server/app_conversation/skill_loader.py`、`skills/*`
- 前端事件渲染和 WebSocket：`frontend/src/contexts/conversation-websocket-context.tsx`、`frontend/src/stores/use-event-store.ts`

Agent 执行层，当前仓库通过依赖使用：

- `openhands.sdk.Agent`
- `openhands.sdk.LLM`
- `openhands.sdk.ConversationSettings`
- `openhands.sdk.StartConversationRequest`
- `openhands.tools.get_default_tools`
- `openhands.tools.get_planning_tools`
- `openhands.agent_server` 暴露的 `/api/conversations`、`/api/conversations/{id}/events`、WebSocket、`/api/skills` 等接口

## 2. 项目目录结构

| 路径 | 职责 | 关键文件/模块 |
| --- | --- | --- |
| `openhands/app_server/` | 当前 V1 应用服务核心。负责 FastAPI 应用、路由、会话、沙箱、事件、设置、MCP、用户、集成。 | `app.py`、`v1_router.py`、`config.py` |
| `openhands/app_server/app_conversation/` | App 层 conversation 生命周期：创建任务、启动沙箱、准备仓库、构造 SDK request、调用 agent-server。 | `live_status_app_conversation_service.py`、`app_conversation_router.py`、`app_conversation_models.py`、`app_conversation_service_base.py` |
| `openhands/app_server/sandbox/` | 沙箱抽象和 Docker/remote/process 三类实现。 | `sandbox_service.py`、`docker_sandbox_service.py`、`remote_sandbox_service.py`、`process_sandbox_service.py`、`sandbox_models.py`、`session_auth.py` |
| `openhands/app_server/event/` | Agent 事件读取、搜索、计数、持久化抽象。 | `event_service.py`、`event_service_base.py`、`event_router.py` |
| `openhands/app_server/event_callback/` | Webhook 收到 agent-server 事件后保存事件、更新会话、执行回调。 | `webhook_router.py`、`event_callback_models.py`、`sql_event_callback_service.py`、`set_title_callback_processor.py` |
| `openhands/app_server/settings/` | 用户设置、LLM profile、Agent settings、conversation settings 的持久化和校验。 | `settings_models.py`、`settings_router.py`、`llm_profiles.py` |
| `openhands/app_server/secrets/` | 自定义 secrets、Git provider token、序列化隐藏值。 | `secrets_models.py`、`secrets_router.py` |
| `openhands/app_server/user/` | 用户上下文、技能 API、用户 profile/settings。 | `user_models.py`、`user_router.py`、`skills_router.py` |
| `openhands/app_server/mcp/` | FastMCP server，代理 Tavily，提供创建 PR/MR 的 MCP tool。 | `mcp_router.py` |
| `openhands/app_server/integrations/` | Git provider、插件、suggested task、resolver prompt 等集成逻辑。 | `service_types.py`、`templates/` |
| `openhands/server/` | 旧入口兼容层，实际转到 `openhands.app_server.app`。 | `listen.py`、`__main__.py` |
| `frontend/` | React 前端，负责会话创建、WebSocket、事件展示、设置 UI、沙箱恢复、计划模式等。 | `src/contexts/conversation-websocket-context.tsx`、`src/api/conversation-service/v1-conversation-service.api.ts`、`src/stores/use-event-store.ts` |
| `frontend/src/types/v1/core/` | 前端对 Agent 事件、Action、Observation、State 的 TypeScript 类型镜像。 | `base/action.ts`、`base/observation.ts`、`events/action-event.ts`、`events/observation-event.ts` |
| `enterprise/` | SaaS/Enterprise 扩展：auth、billing、org、integrations、数据库迁移、enterprise server。 | `saas_server.py`、`server/`、`storage/`、`integrations/`、`migrations/` |
| `skills/` | 内置 skills/microagents 文档和提示内容。 | `README.md`、`agent_memory.md`、其他 `.md` skills |
| `tests/unit/` | 后端单元测试。 | `test_*.py` |
| `openhands-ui/` | UI 组件库/设计系统相关代码。 | `components.json`、`src/` |
| `pyproject.toml` | Python 依赖和工具配置；可见 SDK/agent-server/tools 外部依赖版本。 | `openhands-sdk`、`openhands-agent-server`、`openhands-tools` |
| `.github/` | GitHub Actions、PR/issue automation、CI。 | `workflows/`、`actions/` |
| `.codegraph/` | CodeGraph 本地知识图谱数据库。 | `codegraph.db` |

### 2.1 关键入口文件

- 后端 V1 App 入口：`openhands/app_server/app.py`
- 后端路由聚合：`openhands/app_server/v1_router.py`
- 旧后端兼容入口：`openhands/server/listen.py`、`openhands/server/__main__.py`
- Enterprise 入口：`enterprise/saas_server.py`
- 创建 conversation 的 API：`openhands/app_server/app_conversation/app_conversation_router.py`
- 会话启动主服务：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`
- 前端创建会话 API：`frontend/src/api/conversation-service/v1-conversation-service.api.ts`
- 前端创建会话 hook：`frontend/src/hooks/mutation/use-create-conversation.ts`
- 前端 WebSocket Provider：`frontend/src/contexts/conversation-websocket-context.tsx`
- 事件 store：`frontend/src/stores/use-event-store.ts`

## 3. Agent 主运行流程

### 3.1 总体链路

OpenHands 的主链路可以理解为：

```text
用户输入 / 启动任务
  -> frontend 创建 conversation
  -> V1 App Server 接收 POST /api/v1/conversations
  -> LiveStatusAppConversationService 创建 start task
  -> 启动或选择 sandbox
  -> 在 sandbox 内准备 workspace / clone repo / setup.sh / git hooks / skills
  -> 组装 SDK StartConversationRequest
  -> POST 到 sandbox 内的 agent-server /api/conversations
  -> agent-server 内部运行 Agent loop（本仓库未包含源码）
  -> agent-server 执行 LLM 调用和工具调用
  -> agent-server 通过 webhook / WebSocket 产生 Event
  -> App Server 保存 Event、执行 callback、更新 conversation info
  -> frontend WebSocket / history 查询读取事件
  -> Chat UI 渲染 Action / Observation / Message / Final answer
```

当前仓库最核心的“桥接点”是 `openhands/app_server/app_conversation/live_status_app_conversation_service.py`。它把用户态设置和 App Server 状态转换成 SDK/Agent Server 需要的 request。

### 3.2 会话启动流程

入口是 `openhands/app_server/app_conversation/app_conversation_router.py` 的 `start_app_conversation()`：

1. 前端通过 `frontend/src/api/conversation-service/v1-conversation-service.api.ts` 的 `V1ConversationService.createConversation()` 发起创建。
2. 后端 `start_app_conversation()` 接收 `AppConversationStartRequest`。
3. 路由调用 `app_conversation_service.start_app_conversation(start_request)`。
4. 该方法先返回一个 `AppConversationStartTask`，让前端可以拿到 `task_id` 并轮询启动进度。
5. 路由用 `_consume_remaining()` 在后台继续消费 async generator，避免 HTTP 请求一直阻塞。

实际启动逻辑在 `LiveStatusAppConversationService._start_app_conversation()`：

```text
_start_app_conversation(request)
  -> 获取 user_id / email
  -> 继承 parent conversation 配置（如果有）
  -> 创建 AppConversationStartTask
  -> _wait_for_sandbox_start(task)
  -> 获取 SandboxInfo 和 AGENT_SERVER exposed URL
  -> _seed_sandbox_profiles(...)
  -> 计算 conversation_id / working_dir
  -> 创建 AsyncRemoteWorkspace(host=agent_server_url, api_key=session_api_key)
  -> run_setup_scripts(...)
       -> clone_or_init_git_repo(...)
       -> maybe_run_setup_script(.openhands/setup.sh)
       -> maybe_setup_git_hooks(.openhands/pre-commit.sh)
       -> _load_skills_and_update_agent(...)
  -> _build_start_conversation_request_for_user(...)
  -> POST {agent_server_url}/api/conversations
  -> 解析 ConversationInfo
  -> 保存 AppConversationInfo
  -> 注册 SetTitleCallbackProcessor
  -> 标记 AppConversationStartTaskStatus.READY
  -> _process_pending_messages(...)
```

对应文件和函数：

- `openhands/app_server/app_conversation/live_status_app_conversation_service.py`
  - `LiveStatusAppConversationService.start_app_conversation()`
  - `LiveStatusAppConversationService._start_app_conversation()`
  - `LiveStatusAppConversationService._build_start_conversation_request_for_user()`
  - `LiveStatusAppConversationService._configure_llm()`
  - `LiveStatusAppConversationService._configure_llm_and_mcp()`
  - `LiveStatusAppConversationService._apply_server_agent_overrides()`
  - `LiveStatusAppConversationService._build_acp_start_conversation_request()`
  - `LiveStatusAppConversationService._process_pending_messages()`
- `openhands/app_server/app_conversation/app_conversation_service_base.py`
  - `run_setup_scripts()`
  - `clone_or_init_git_repo()`
  - `maybe_run_setup_script()`
  - `maybe_setup_git_hooks()`
  - `_load_skills_and_update_agent()`
  - `_create_condenser()`

### 3.3 用户后续消息流程

会话已启动后，用户消息通常走 WebSocket。核心前端文件是 `frontend/src/contexts/conversation-websocket-context.tsx`：

- `ConversationWebSocketProvider` 负责建立主 conversation WebSocket。
- `buildWebSocketUrl()` 位于 `frontend/src/utils/websocket-url.ts`，把 HTTP conversation URL 转成 `/sockets/events/{conversationId}`。
- `sendMessage()` 如果 WebSocket 可用，则直接发送 JSON。
- 如果 WebSocket 不可用，则调用 `PendingMessageService.queueMessage()` 暂存消息。

后端也提供 HTTP 代理发送消息的路径：`openhands/app_server/app_conversation/app_conversation_router.py` 的 `send_message_to_conversation()`：

```text
send_message_to_conversation(conversation_id, AppSendMessageRequest)
  -> 读取 AppConversationInfo
  -> 获取 sandbox
  -> 要求 sandbox.status == RUNNING
  -> 查找 AGENT_SERVER exposed URL
  -> POST {agent_server_url}/api/conversations/{conversation_id}/events
       body: role / content / run
       header: X-Session-API-Key
```

如果 sandbox 状态是：

- `MISSING`：返回 410，表示归档/沙箱缺失。
- `ERROR`：返回 503。
- `STARTING`/`PAUSED` 等非运行态：返回 409。

### 3.4 LLM 调用在哪里发生

本仓库未发现主 Agent loop 中直接调用 OpenAI/LiteLLM 的源码。当前仓库做的是配置和转发：

- `LiveStatusAppConversationService._configure_llm()` 构造 SDK 的 `LLM` 对象，设置 `model`、`base_url`、`api_key`、`usage_id='agent'`。
- `LiveStatusAppConversationService._apply_server_agent_overrides()` 为 `agent.llm` 和 condenser LLM 注入 `litellm_extra_body.metadata`。
- `_build_start_conversation_request_for_user()` 把 `LLM` 放进 `OpenHandsAgentSettings`/`ConversationSettings` 并生成 `StartConversationRequest`。
- `LiveStatusAppConversationService._start_app_conversation()` 把该 request POST 到 sandbox 内 `agent-server`。

实际 LLM 调用在 sandbox 内 `openhands-agent-server` + `openhands-sdk` + `litellm/openai` 中完成。当前仓库只包含依赖声明和配置组装，不包含 LLM adapter 的内部 loop。

### 3.5 tool call / final answer / handoff / code execution 如何调度

#### Tool call

本仓库未发现工具执行调度器源码。工具调用由外部 agent-server/SDK 执行，本仓库主要做三件事：

1. 在启动 request 中注册工具：
   - `get_default_tools(enable_browser=True, enable_sub_agents=...)`
   - `get_planning_tools(plan_path=...)`
   - `SwitchLLMTool`
   - MCP server 配置
2. 在前端声明和渲染工具事件类型：
   - `frontend/src/types/v1/core/base/action.ts`
   - `frontend/src/types/v1/core/events/action-event.ts`
   - `frontend/src/types/v1/core/base/observation.ts`
   - `frontend/src/types/v1/core/events/observation-event.ts`
3. 通过 webhook 保存工具执行事件：
   - `openhands/app_server/event_callback/webhook_router.py`

常见工具 action/observation 类型包括：

- `ExecuteBashAction` / `ExecuteBashObservation`
- `TerminalAction` / `TerminalObservation`
- `FileEditorAction` / `FileEditorObservation`
- `StrReplaceEditorAction`
- `TaskTrackerAction` / `TaskTrackerObservation`
- `PlanningFileEditorAction` / `PlanningFileEditorObservation`
- `BrowserAction`
- `GlobAction`
- `GrepAction`
- `MCPToolAction` / `MCPToolObservation`
- `FinishAction` / `FinishObservation`
- `ThinkAction` / `ThinkObservation`

#### Final answer

final answer 在事件层通常体现为：

- `FinishAction`
- `FinishObservation`
- `MessageEvent`

前端渲染逻辑在 `frontend/src/components/v1/chat/event-message.tsx`：

- `FinishAction` 渲染为 `FinishEventMessage`。
- `MessageEvent` 渲染为 assistant/user chat message。
- `ActionEvent` 与对应 `ObservationEvent` 会被组合渲染为可折叠工具块或普通消息。

#### Handoff

本仓库未发现 OpenAI Agents SDK 风格的显式 `handoff` 对象或 handoff runner。存在三种类似 handoff 的模式：

1. Planning agent 子会话：`frontend/src/hooks/use-handle-plan-click.ts` 创建 `agentType: "plan"` 的 sub-conversation。
2. Planning -> Code 的模式切换：`frontend/src/hooks/use-handle-build-plan-click.ts` 发送类似 “Execute the plan based on the .agents_tmp/PLAN.md file.” 的指令。
3. SDK 子 Agent：`LiveStatusAppConversationService._build_start_conversation_request_for_user()` 在 `enable_sub_agents` 时注入 `agent_definitions = list(get_registered_agent_definitions())`，但实际子 Agent 调度在外部 SDK/tools。

#### Code execution

代码执行发生在 sandbox 内，不在 App Server 进程中直接执行用户代码。当前仓库负责编排 sandbox：

- `openhands/app_server/sandbox/docker_sandbox_service.py`
- `openhands/app_server/sandbox/remote_sandbox_service.py`
- `openhands/app_server/sandbox/process_sandbox_service.py`

App Server 只在准备阶段通过 `AsyncRemoteWorkspace` 执行少量 setup 命令，例如 clone repo、运行 `.openhands/setup.sh`、安装 `.openhands/pre-commit.sh` 钩子。Agent loop 产生的 bash/file/browser 等工具执行在 sandbox 内的 agent-server/tools 侧完成。

### 3.6 核心 loop 的流程图式描述

以下是从本仓库能观察到的完整 loop 边界：

```text
App Server / Frontend 层：

用户输入
  -> frontend sendMessage()
  -> WebSocket 或 HTTP proxy
  -> agent-server /api/conversations/{id}/events

agent-server / SDK 层（本仓库未包含源码，外部依赖实现）：

while conversation not terminal:
  1. 从 session/context/memory 中构造 LLM messages
  2. 调用 LLM adapter（SDK LLM + LiteLLM/OpenAI）
  3. 如果模型输出 final answer:
       emit MessageEvent / FinishAction / FinishObservation
       update ConversationExecutionStatus terminal
       break
  4. 如果模型输出 tool call:
       emit ActionEvent(thought, action, tool_call_id, ...)
       dispatch tool executor
       在 sandbox 内执行 bash/file/browser/mcp/etc.
       emit ObservationEvent(action_id, observation)
       将 observation 追加回上下文
  5. 如果触发 condenser / memory compaction:
       调用 LLMSummarizingCondenser 或相关压缩逻辑
  6. 如果触发 hook / callback:
       emit HookExecutionEvent 或执行 agent-server hook

App Server / Frontend 层：

agent-server webhook / websocket
  -> openhands/app_server/event_callback/webhook_router.py 保存事件
  -> SQLEventCallbackService.execute_callbacks()
  -> SetTitleCallbackProcessor 等 callback
  -> frontend ConversationWebSocketProvider 接收事件
  -> useEventStore.addEvent()
  -> event-message.tsx 渲染
```

## 4. 核心模块分析

### 4.1 Agent / Runner / Loop

**本仓库状态：部分存在，核心 loop 未发现。**

本仓库没有发现主 Agent runner/loop 的源码。可见的是对外部 SDK Agent 的构造和 agent-server 的启动请求。

职责：

- 根据用户设置、LLM、工具、技能、上下文生成可执行 Agent request。
- 把 conversation 交给 sandbox 内的 agent-server。
- 接收 agent-server 执行过程中产生的事件。

关键类/函数：

- `LiveStatusAppConversationService._build_start_conversation_request_for_user()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  负责构造 SDK `ConversationSettings`、`Agent`、`StartConversationRequest`。

- `LiveStatusAppConversationService._build_acp_start_conversation_request()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  ACP Agent 分支，适用于 Claude Code/Codex/Gemini CLI 类 Agent。

- `OpenHandsAgentSettings.create_agent()`  
  路径：外部依赖 `openhands-sdk`，当前仓库只调用，不包含实现。

- `ConversationSettings.create_request()`  
  路径：外部依赖 `openhands-sdk`，当前仓库只调用，不包含实现。

调用关系：

```text
app_conversation_router.start_app_conversation()
  -> LiveStatusAppConversationService.start_app_conversation()
  -> LiveStatusAppConversationService._start_app_conversation()
  -> LiveStatusAppConversationService._build_start_conversation_request_for_user()
  -> configured_agent_settings.create_agent()
  -> ConversationSettings.create_request(StartConversationRequest, agent=...)
  -> POST agent-server /api/conversations
```

可复用价值：

- 值得学习的是“应用层不直接运行 loop，而是构造 request 后交给隔离 agent-server”的分层方式。
- 对从零实现 Coding Agent 而言，可以先借鉴它的 request-building、状态机、沙箱启动和事件流，而不是一开始照搬完整外部 SDK。

### 4.2 Model / LLM Adapter

**本仓库状态：配置层存在，实际 adapter/调用 loop 未发现。**

职责：

- 从用户设置中解析模型、base URL、API key。
- 区分 OpenHands managed provider 和用户自定义 provider。
- 为 LLM 调用注入 metadata，便于追踪 conversation/user/model。
- 支持多个 LLM profile，并允许 `SwitchLLMTool` 在运行中切换 active model。

关键类/函数：

- `LiveStatusAppConversationService._configure_llm()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  构造 SDK `LLM(model, base_url, api_key, usage_id='agent')`。

- `LiveStatusAppConversationService._configure_llm_and_mcp()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  同时组装 LLM 和 MCP server 配置。

- `LiveStatusAppConversationService._apply_server_agent_overrides()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  给 agent LLM 和 condenser LLM 设置 server-side prompt kwargs、metadata。

- `resolve_provider_llm_base_url()`  
  路径：`openhands/app_server/utils/llm.py`

- `get_llm_metadata()`、`should_set_litellm_extra_body()`  
  路径：`openhands/app_server/utils/llm_metadata.py`

- `DefaultLLMModelService` / `LLMModelService`  
  路径：`openhands/app_server/config_api/default_llm_model_service.py`、`openhands/app_server/config_api/llm_model_service.py`

调用关系：

```text
_build_start_conversation_request_for_user()
  -> _configure_llm_and_mcp()
       -> _configure_llm()
       -> _add_system_mcp_servers()
       -> _merge_custom_mcp_config()
  -> configured_agent_settings.create_agent()
  -> _apply_server_agent_overrides(agent, ...)
```

可复用价值：

- LLM 配置对象应当和用户设置、profile、运行 metadata 分离。
- LLM API key 不应在前端和普通日志中明文流动；可以通过 settings/secrets/context 注入。
- 对自己的 Agent 项目，建议直接参考这套“LLM profile + active profile + request-time injection + metadata”的设计。

### 4.3 Tool / Function Tool

**本仓库状态：MCP tool 和前端事件类型存在，默认 Function Tool/执行器源码未发现。**

职责：

- 把可调用能力暴露给 Agent。
- 支持默认 coding tools、planning tools、MCP tools、PR/MR creation tools、Switch LLM tool、子 Agent tools。
- 将工具调用和结果以事件形式展示给用户。

关键类/函数：

- `get_default_tools()`  
  路径：外部依赖 `openhands-tools`，当前仓库调用。

- `get_planning_tools()`  
  路径：外部依赖 `openhands-tools`，当前仓库调用。

- `register_builtins_agents()`、`get_registered_agent_definitions()`  
  路径：外部依赖 `openhands-tools` / `openhands-sdk`，当前仓库调用。

- `SwitchLLMTool`  
  路径：外部依赖 `openhands-tools`，当前仓库在 `_build_start_conversation_request_for_user()` 中加入。

- `mcp_server.tool()` 注册的 MCP tool  
  路径：`openhands/app_server/mcp/mcp_router.py`  
  包括 `create_pr`、`create_mr`、`create_bitbucket_pr`、`create_bitbucket_data_center_pr`、`create_azure_devops_pr`。

- `ActionEvent`  
  路径：`frontend/src/types/v1/core/events/action-event.ts`

- `ObservationEvent`  
  路径：`frontend/src/types/v1/core/events/observation-event.ts`

调用关系：

```text
_build_start_conversation_request_for_user()
  -> get_default_tools(...) 或 get_planning_tools(...)
  -> optional register_builtins_agents(...)
  -> optional SwitchLLMTool(...)
  -> ConversationSettings.create_request(...)
  -> agent-server 内部调度工具执行（本仓库未发现）
  -> ActionEvent / ObservationEvent
  -> webhook_router.on_event()
  -> frontend event-message.tsx
```

可复用价值：

- 值得借鉴的是工具调用的“事件化”：Action 与 Observation 分开，前端可以通过 `action_id` 关联渲染。
- 不建议直接照搬外部默认工具集，因为实现不在当前仓库；更适合作为行为协议参考。

### 4.4 Memory / Session / Context

**本仓库状态：session/context/skill memory 存在，完整长期记忆/RAG memory 未发现。**

职责：

- 管理 conversation ID、parent/sub conversation、start task、sandbox session key。
- 管理用户 settings、agent context、secrets、skills。
- 通过 condenser 控制上下文长度。
- 通过 `.openhands/skills` / `.openhands/microagents` 做 prompt-based memory。

关键类/函数：

- `AppConversationInfo`、`AppConversationStartTask`  
  路径：`openhands/app_server/app_conversation/app_conversation_models.py`

- `ConversationSettings.from_persisted()`  
  路径：`openhands/app_server/settings/settings_models.py`

- `AgentContext`  
  路径：外部依赖 `openhands-sdk`，当前仓库创建并注入 `system_message_suffix`、`secrets`、`skills`。

- `AppConversationServiceBase._create_condenser()`  
  路径：`openhands/app_server/app_conversation/app_conversation_service_base.py`  
  创建 `LLMSummarizingCondenser`。

- `AppConversationServiceBase._load_skills_and_update_agent()`  
  路径：`openhands/app_server/app_conversation/app_conversation_service_base.py`

- `load_skills_from_agent_server()`  
  路径：`openhands/app_server/app_conversation/skill_loader.py`

- `PendingMessageService`  
  前端路径：`frontend/src/api/pending-message-service/pending-message-service.api.ts`  
  后端路径：`openhands/app_server/pending_messages/pending_message_service.py`

调用关系：

```text
Settings / Secrets / UserInfo
  -> _build_start_conversation_request_for_user()
  -> AgentContext(system_message_suffix, secrets, skills)
  -> ConversationSettings
  -> agent-server conversation session

frontend WebSocket 不可用
  -> PendingMessageService.queueMessage()
  -> _process_pending_messages()
  -> POST agent-server events
```

可复用价值：

- “conversation session”和“sandbox session”分离值得学习：conversation 是产品对象，sandbox 是执行资源。
- pending message 机制对网络不稳定/沙箱启动中的用户输入很实用。
- 技能/微代理作为轻量 memory 机制，适合从零项目早期实现。

### 4.5 Prompt / Instruction

**本仓库状态：应用层 prompt/instruction 存在，底层 system prompt 模板部分在外部依赖或运行时模板中。**

职责：

- 根据 agent 类型注入不同系统指令。
- 为 planning agent 限制行为范围。
- 为 integration/suggested task 生成任务 prompt。
- 加载 repository/user/org/sandbox skills 到 AgentContext。

关键文件/函数：

- `PLANNING_AGENT_INSTRUCTION`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  明确要求 planning agent 只能创建 `PLAN.md`，不要执行代码。

- `_apply_server_agent_overrides()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  对 plan agent 设置 `system_prompt_filename='system_prompt_planning.j2'` 和 `plan_structure`。

- `_construct_initial_message_with_plugin_params()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`

- `_apply_suggested_task()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`

- `openhands/app_server/integrations/templates/suggested_task/*.j2`

- `skills/README.md`、`skills/agent_memory.md`

调用关系：

```text
User task / integration suggested task / plugin params
  -> _apply_suggested_task()
  -> _construct_initial_message_with_plugin_params()
  -> AgentContext(system_message_suffix=...)
  -> ConversationSettings.initial_message
  -> agent-server Agent loop
```

可复用价值：

- Prompt 不应该散落在 UI 里；应集中在服务层和模板层。
- Planning agent 用强约束 instruction + 专用工具集，是从零 coding agent 很值得参考的设计。

### 4.6 Multi-agent / Handoff

**本仓库状态：多 Agent 接入存在，显式 handoff runner 未发现。**

职责：

- 支持 planning agent 与 coding agent 的协作。
- 支持 SDK sub-agent definitions。
- 支持 ACP external agent。

关键类/函数：

- `AgentType`  
  路径：`openhands/app_server/app_conversation/app_conversation_models.py`

- `LiveStatusAppConversationService._build_start_conversation_request_for_user()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
  根据 `agent_type == AgentType.PLAN` 切换 planning tools 和 prompt。

- `LiveStatusAppConversationService._build_acp_start_conversation_request()`  
  路径：`openhands/app_server/app_conversation/live_status_app_conversation_service.py`

- `useHandlePlanClick()`  
  路径：`frontend/src/hooks/use-handle-plan-click.ts`

- `ConversationWebSocketProvider` 的 planning WebSocket 逻辑  
  路径：`frontend/src/contexts/conversation-websocket-context.tsx`

- `AppConversationInfo.parent_conversation_id`、`sub_conversation_ids`  
  路径：`openhands/app_server/app_conversation/app_conversation_models.py`

调用关系：

```text
用户点击 Plan
  -> useHandlePlanClick()
  -> useCreateConversation(parentConversationId, agentType="plan")
  -> 后端创建 plan sub-conversation
  -> frontend 同时连接 main conversation 和 planning conversation WebSocket
  -> 用户点击 Build Plan
  -> code conversation 收到执行 PLAN.md 的消息
```

可复用价值：

- Planning agent 作为单独 sub-conversation，比在同一个 loop 中混杂规划和执行更容易观察和回滚。
- 对自己的项目，可以先借鉴“planner 只写计划文件，builder 再执行计划”的模式。
- 不建议早期照搬 ACP、多 provider、多子 Agent registry，迁移复杂度高。

### 4.7 Guardrail / Validation

**本仓库状态：应用层 guardrail/validation 存在，模型输出安全评估/工具风险判定实现未发现。**

职责：

- 限制 secrets 数量、名称和值大小。
- 防止用户覆盖内部系统 secrets。
- 校验 sandbox session key 和用户所有权。
- 限制 sandbox 状态下可执行的 API。
- 通过 Pydantic model 校验 settings/schema。
- 前端展示 action 的 `security_risk` 和 `critic_result`。

关键类/函数：

- `validate_secret_name()`、`validate_secrets_dict()`  
  路径：`openhands/app_server/constants.py`

- `BLOCKED_SECRET_NAMES`、`BLOCKED_SECRET_PREFIXES`、`OVERRIDABLE_SYSTEM_SECRETS`  
  路径：`openhands/app_server/constants.py`

- `Settings.update()`、`validate_agent_settings()`  
  路径：`openhands/app_server/settings/settings_models.py`

- `validate_session_key()`、`validate_session_key_ownership()`  
  路径：`openhands/app_server/sandbox/session_auth.py`

- `RateLimitMiddleware`、`LocalhostCORSMiddleware`、`CacheControlMiddleware`  
  路径：`openhands/app_server/middleware.py`

- `ActionEvent.security_risk`、`ActionEvent.critic_result`  
  路径：`frontend/src/types/v1/core/events/action-event.ts`

调用关系：

```text
API request
  -> FastAPI Depends / UserContext
  -> session_auth.validate_session_key(...)
  -> service/router state checks
  -> Pydantic model validation
  -> sandbox / event / settings operation
```

可复用价值：

- 从零 Agent 项目优先实现 secrets allow/deny list、session key ownership、sandbox state gate。
- 模型输出安全评估在当前仓库只是事件字段呈现，核心实现未发现，不适合作为主要参考。

### 4.8 Tracing / Logging / Hook

**本仓库状态：事件追踪、回调、analytics、LLM metadata 存在。**

职责：

- 保存 agent-server 推送的所有事件。
- 根据事件更新 conversation 状态、统计、标题、active model。
- 支持 callback processor。
- 为 LLM 调用注入 metadata。
- 前端通过 event store 构建可观察执行轨迹。

关键类/函数：

- `EventService`  
  路径：`openhands/app_server/event/event_service.py`

- `EventServiceBase.search_events()`、`EventServiceBase.save_event()`  
  路径：`openhands/app_server/event/event_service_base.py`

- `webhook_router.on_event()`  
  路径：`openhands/app_server/event_callback/webhook_router.py`

- `EventCallbackProcessor`、`EventCallback`、`EventCallbackStatus`  
  路径：`openhands/app_server/event_callback/event_callback_models.py`

- `SQLEventCallbackService.execute_callbacks()`  
  路径：`openhands/app_server/event_callback/sql_event_callback_service.py`

- `SetTitleCallbackProcessor`  
  路径：`openhands/app_server/event_callback/set_title_callback_processor.py`

- `useEventStore.addEvent()`  
  路径：`frontend/src/stores/use-event-store.ts`

- `ConversationWebSocketProvider.handleMainMessage()`  
  路径：`frontend/src/contexts/conversation-websocket-context.tsx`

调用关系：

```text
agent-server emits events
  -> POST /api/v1/webhooks/events/{conversation_id}
  -> webhook_router.on_event()
  -> event_service.save_event()
  -> conversation info updates
  -> event_callback_service.execute_callbacks()
  -> frontend websocket receives same/new events
  -> useEventStore.addEvent()
  -> event-message.tsx renders trace
```

可复用价值：

- Action/Observation/Message/State 事件化是 Coding Agent 可观测性的核心。
- callback processor 抽象很适合扩展标题生成、通知、审计、自动 PR 元数据等异步动作。

### 4.9 RAG / Retrieval

**本仓库状态：传统 RAG/vector retrieval 未发现。**

当前仓库未发现 embedding、vector database、retriever pipeline、chunk index 等传统 RAG 模块。存在以下 retrieval-like 机制：

- Skills/microagents：`skills/README.md`、`openhands/app_server/app_conversation/skill_loader.py`  
  通过 frontmatter trigger 和 project/user/org/sandbox 作用域把知识注入 prompt。

- Tavily MCP proxy：`openhands/app_server/mcp/mcp_router.py`  
  如果配置 `tavily_api_key`，App Server 代理 Tavily MCP，避免 API key 暴露给 sandbox。

- 文件/代码搜索工具事件类型：`GlobAction`、`GrepAction`  
  路径：`frontend/src/types/v1/core/base/action.ts`，实际工具实现位于外部依赖。

- Git provider/repository search：散布在 `openhands/app_server/integrations/` 和 enterprise integrations 中，用于仓库选择和 issue/PR 任务上下文。

可复用价值：

- 从零实现 Coding Agent 时，早期可以先做“技能/规则文件 + grep/glob + Web search MCP”，不必先上 vector DB。
- 如果目标是 repo-aware coding agent，基于文件搜索和触发式 skills 往往比通用 RAG 更直接。

### 4.10 Code execution / Sandbox

**本仓库状态：沙箱生命周期完整存在，Agent 工具执行器位于外部依赖/沙箱内。**

职责：

- 创建、恢复、暂停、删除 sandbox。
- 为每个 sandbox 生成 session API key。
- 暴露 agent-server、VS Code、worker 等端口 URL。
- 在会话启动前准备 repository/workspace。
- 限制只有拥有 session key 的 agent-server/sandbox 能调用敏感 API。

关键类/函数：

- `SandboxService`  
  路径：`openhands/app_server/sandbox/sandbox_service.py`

- `DockerSandboxService.start_sandbox()`  
  路径：`openhands/app_server/sandbox/docker_sandbox_service.py`

- `RemoteSandboxService.start_sandbox()`  
  路径：`openhands/app_server/sandbox/remote_sandbox_service.py`

- `ProcessSandboxService`  
  路径：`openhands/app_server/sandbox/process_sandbox_service.py`

- `SandboxInfo`、`SandboxStatus`、`ExposedUrl`、`AGENT_SERVER`  
  路径：`openhands/app_server/sandbox/sandbox_models.py`

- `SandboxSpecInfo`、`SandboxSpecService`  
  路径：`openhands/app_server/sandbox/sandbox_spec_models.py`、`openhands/app_server/sandbox/sandbox_spec_service.py`

- `AppConversationServiceBase.clone_or_init_git_repo()`  
  路径：`openhands/app_server/app_conversation/app_conversation_service_base.py`

- `AppConversationServiceBase.run_setup_scripts()`  
  路径：`openhands/app_server/app_conversation/app_conversation_service_base.py`

调用关系：

```text
_start_app_conversation()
  -> _wait_for_sandbox_start()
  -> SandboxService.start_sandbox()
  -> SandboxInfo.exposed_urls[AGENT_SERVER]
  -> AsyncRemoteWorkspace(host=agent_server_url, api_key=session_api_key)
  -> clone_or_init_git_repo()
  -> maybe_run_setup_script()
  -> maybe_setup_git_hooks()
  -> POST agent-server /api/conversations
```

可复用价值：

- 强烈建议参考“App Server 只编排，代码执行在 sandbox/agent-server 内”的安全边界。
- `SandboxStatus` 状态机、`session_api_key`、`ExposedUrl` 模型都适合直接借鉴。

### 4.11 Config / Schema / Type system

**本仓库状态：完整存在。**

职责：

- 通过 `AppServerConfig` 聚合所有服务 injector。
- 通过 Pydantic model 定义后端 schema。
- 通过 TypeScript 类型镜像前端事件和 API。
- 通过 env var 切换 sandbox provider、storage provider、enterprise SaaS server config。

关键类/函数：

- `AppServerConfig`  
  路径：`openhands/app_server/config.py`

- `config_from_env()`、`get_global_config()`  
  路径：`openhands/app_server/config.py`

- `Injector`、`InjectorState`  
  路径：`openhands/app_server/services/injector.py`

- `Settings`、`GETSettingsModel`、`SandboxGroupingStrategy`  
  路径：`openhands/app_server/settings/settings_models.py`

- `AppConversationStartRequest`、`AppConversationInfo`、`AppConversation`  
  路径：`openhands/app_server/app_conversation/app_conversation_models.py`

- `Action`、`Observation`、`ActionEvent`、`ObservationEvent`、`MessageEvent`  
  路径：`frontend/src/types/v1/core/*`

调用关系：

```text
app.py startup
  -> get_global_config()
  -> config_from_env()
  -> AppServerConfig(injectors...)
  -> FastAPI Depends(injector.depends)
  -> routers/services use concrete implementations
```

可复用价值：

- Injector 模式让 OSS/local/remote/enterprise 的实现切换集中在 config 层。
- 后端 Pydantic schema + 前端 TypeScript event schema 的组合很适合复杂 Agent UI。

## 5. 关键数据结构

| 数据结构 | 文件路径 | 作用 |
| --- | --- | --- |
| `AppServerConfig` | `openhands/app_server/config.py` | App Server 的服务装配中心，包含 event、sandbox、settings、user、jwt、web client 等 injector。 |
| `Injector[T]` / `InjectorState` | `openhands/app_server/services/injector.py` | FastAPI DI 抽象；统一 `inject()`、`context()`、`depends()` 生命周期。 |
| `AppConversationStartRequest` | `openhands/app_server/app_conversation/app_conversation_models.py` | 创建会话的入参，包含初始任务、仓库、agent type、插件、parent conversation 等。 |
| `AppConversationStartTask` | `openhands/app_server/app_conversation/app_conversation_models.py` | 会话启动异步任务，前端用它轮询 sandbox/repo/setup/ready 状态。 |
| `AppConversationStartTaskStatus` | `openhands/app_server/app_conversation/app_conversation_models.py` | 启动状态枚举，如 `WAITING_FOR_SANDBOX`、`PREPARING_REPOSITORY`、`STARTING_CONVERSATION`、`READY`、`ERROR`。 |
| `AppConversationInfo` | `openhands/app_server/app_conversation/app_conversation_models.py` | App 层 conversation 元数据：conversation id、sandbox id、LLM model、agent kind、git 信息、parent/sub conversation。 |
| `AppConversation` | `openhands/app_server/app_conversation/app_conversation_models.py` | 面向 API 返回的 conversation 聚合对象。 |
| `AgentType` | `openhands/app_server/app_conversation/app_conversation_models.py` | 区分普通 coding agent、planning agent 等模式。 |
| `ConversationTrigger` | `openhands/app_server/app_conversation/app_conversation_models.py` | 标识 conversation 创建触发来源。 |
| `SandboxInfo` | `openhands/app_server/sandbox/sandbox_models.py` | sandbox 实例信息：id、status、session API key、exposed URLs、created_by_user_id。 |
| `SandboxStatus` | `openhands/app_server/sandbox/sandbox_models.py` | sandbox 状态枚举：`STARTING`、`RUNNING`、`PAUSED`、`ERROR`、`MISSING`。 |
| `ExposedUrl` | `openhands/app_server/sandbox/sandbox_models.py` | sandbox 内服务对外暴露 URL/port/name，例如 `AGENT_SERVER`、`VSCODE`。 |
| `SandboxSpecInfo` | `openhands/app_server/sandbox/sandbox_spec_models.py` | sandbox 镜像、命令、工作目录、环境变量、端口等规格。 |
| `Settings` | `openhands/app_server/settings/settings_models.py` | 用户设置总模型，包含 agent settings、conversation settings、LLM profile、secrets 序列化策略。 |
| `SandboxGroupingStrategy` | `openhands/app_server/settings/settings_models.py` | 控制多个 conversation 如何共享或隔离 sandbox workspace。 |
| `Secrets` | `openhands/app_server/secrets/secrets_models.py` | 用户 secrets 和 provider tokens 的不可变模型，默认隐藏值。 |
| `UserInfo` | `openhands/app_server/user/user_models.py` | 用户上下文和设置聚合。 |
| `EventService` | `openhands/app_server/event/event_service.py` | 事件存储抽象，定义读取、搜索、计数、保存接口。 |
| `EventCallbackProcessor` | `openhands/app_server/event_callback/event_callback_models.py` | 事件回调处理器抽象，按 `event_kind` 触发。 |
| `EventCallback` | `openhands/app_server/event_callback/event_callback_models.py` | 持久化的 callback 注册记录。 |
| `EventCallbackStatus` | `openhands/app_server/event_callback/event_callback_models.py` | callback 是否 active/disabled 等状态。 |
| `Action` | `frontend/src/types/v1/core/base/action.ts` | 前端对可执行动作的 union type，如 bash、file editor、browser、MCP、finish。 |
| `Observation` | `frontend/src/types/v1/core/base/observation.ts` | 前端对工具执行结果的 union type。 |
| `ActionEvent` | `frontend/src/types/v1/core/events/action-event.ts` | 模型选择某个动作时产生的事件，包含 thought、tool call、risk、summary 等。 |
| `ObservationEvent` | `frontend/src/types/v1/core/events/observation-event.ts` | 环境返回工具结果时产生的事件，使用 `action_id` 关联 action。 |
| `MessageEvent` | `frontend/src/types/v1/core/events/message-event.ts` | 用户/助手消息事件，包含 `llm_message`、activated microagents、critic result。 |
| `OpenHandsEvent` | `frontend/src/types/v1/core/events/index.ts` | 前端统一事件 union。 |
| `ConversationWebSocketProvider` | `frontend/src/contexts/conversation-websocket-context.tsx` | 前端 WebSocket 状态和事件接收中心，不是纯数据结构但承担 session/event runtime 状态。 |
| `Agent` | 外部依赖 `openhands-sdk` | 当前仓库只创建/传递，未发现源码。 |
| `LLM` | 外部依赖 `openhands-sdk` | 当前仓库只配置，实际调用 adapter 未发现源码。 |
| `ConversationSettings` | 外部依赖 `openhands-sdk` | 当前仓库用于生成 `StartConversationRequest`。 |
| `StartConversationRequest` | 外部依赖 `openhands-sdk` | POST 给 agent-server 的核心启动请求。 |
| `Skill` | 外部依赖 `openhands-sdk` | 当前仓库通过 skill loader 组装并注入 `AgentContext`。 |
| `LLMSummarizingCondenser` | 外部依赖 `openhands-sdk` | 当前仓库创建 condenser 配置，实际压缩逻辑未发现源码。 |

## 6. 可参考模块清单

| 模块名称 | 文件路径 | 解决的问题 | 设计亮点 | 适合借鉴的地方 | 迁移难度 |
| --- | --- | --- | --- | --- | --- |
| App Server 启动与路由聚合 | `openhands/app_server/app.py`、`openhands/app_server/v1_router.py` | 把多个服务模块聚合成统一 FastAPI 应用。 | App 初始化、MCP mount、middleware、router 分层清晰。 | 学习如何把 Agent 平台拆成独立 router/service。 | 低 |
| 服务注入配置 | `openhands/app_server/config.py`、`openhands/app_server/services/injector.py` | OSS/local/remote/enterprise 多环境切换。 | `Injector` + `AppServerConfig` 把实现选择集中到 env/config。 | 从零项目可以用较轻量版本管理 storage/sandbox/user provider。 | 中 |
| Conversation 启动状态机 | `openhands/app_server/app_conversation/live_status_app_conversation_service.py` | 长耗时会话启动、沙箱准备、仓库 clone、agent-server 启动。 | Async generator 先返回 task，再后台推进状态。 | 强烈建议学习；适合任何需要异步启动 Agent workspace 的项目。 | 中 |
| Conversation API 路由 | `openhands/app_server/app_conversation/app_conversation_router.py` | 对外提供创建、发送消息、查询 conversation 等 API。 | 路由只做校验/转发，复杂逻辑在 service。 | 学习 router/service 分离。 | 低 |
| Sandbox 抽象 | `openhands/app_server/sandbox/sandbox_service.py`、`sandbox_models.py` | 屏蔽 Docker/remote/process 三种运行环境差异。 | `SandboxInfo`、`SandboxStatus`、`ExposedUrl` 统一建模。 | 从零 coding agent 最值得优先参考。 | 中 |
| Docker Sandbox | `openhands/app_server/sandbox/docker_sandbox_service.py` | 本地 Docker 隔离运行 Agent。 | session key、env 注入、端口暴露、volume mount、label 管理。 | 可参考状态机和安全边界，不建议直接全量照搬。 | 高 |
| Remote Sandbox | `openhands/app_server/sandbox/remote_sandbox_service.py` | 调远程 runtime API 创建云沙箱。 | App Server 只保存元数据，runtime 负责实际容器。 | 如果要做 SaaS Agent，可参考 API 边界。 | 高 |
| Session API Key 校验 | `openhands/app_server/sandbox/session_auth.py` | 防止非 sandbox/非 owner 调用敏感 API。 | 同时校验 session key、sandbox 状态、用户归属。 | 值得直接学习，迁移成本低。 | 低 |
| Workspace 准备流程 | `openhands/app_server/app_conversation/app_conversation_service_base.py` | clone repo、运行 setup、安装 hooks、加载 skills。 | 把准备工作拆成可观测状态。 | 自己的 coding agent 也需要类似 preflight。 | 中 |
| LLM 配置组装 | `openhands/app_server/app_conversation/live_status_app_conversation_service.py` | 将用户 LLM profile 转成 SDK `LLM`。 | profile、base URL、metadata、condenser LLM 分离。 | 适合直接参考配置层设计。 | 中 |
| MCP server/proxy | `openhands/app_server/mcp/mcp_router.py` | 给 Agent 暴露 PR/MR 创建和 Tavily 等 MCP 工具。 | API key 留在 App Server，不直接暴露给 sandbox。 | 非常值得借鉴 MCP proxy 安全边界。 | 中 |
| Skills/microagents 加载 | `openhands/app_server/app_conversation/skill_loader.py`、`skills/README.md` | 将项目/用户/组织知识注入 Agent context。 | 多来源合并、trigger、覆盖去重。 | 从零 Agent 早期可用它替代复杂 RAG。 | 中 |
| Pending messages | `openhands/app_server/pending_messages/`、`frontend/src/api/pending-message-service/pending-message-service.api.ts` | WebSocket 不可用或沙箱未 ready 时不丢消息。 | 启动完成后 `_process_pending_messages()` 顺序发送。 | 适合借鉴，用户体验收益高。 | 低 |
| Event 持久化 | `openhands/app_server/event/event_service.py`、`event_service_base.py` | 保存和查询 Agent 运行轨迹。 | Action/Observation/Message/State 统一事件流。 | Coding Agent 可观测性必学模块。 | 中 |
| Webhook 事件接收 | `openhands/app_server/event_callback/webhook_router.py` | 接收 agent-server 事件并更新 App 状态。 | 保存事件、更新 stats/model/status、触发 callbacks。 | 很适合作为“Agent runtime -> App”边界参考。 | 中 |
| Callback processor | `openhands/app_server/event_callback/event_callback_models.py`、`sql_event_callback_service.py` | 对特定事件触发异步副作用。 | `event_kind` 过滤、持久化状态、并发执行。 | 可迁移到通知、审计、自动标题、PR comment。 | 中 |
| 前端 WebSocket Provider | `frontend/src/contexts/conversation-websocket-context.tsx` | 接收实时事件、处理连接状态、planning 子会话。 | 主会话和 planning 会话并行处理，断线 pending message。 | 对 Agent UI 很有参考价值。 | 中 |
| 前端事件 Store | `frontend/src/stores/use-event-store.ts` | 前端去重、排序、转换 UI events。 | 事件 ID 去重，原始事件和 UI 事件分层。 | 适合直接参考。 | 低 |
| 事件渲染组件 | `frontend/src/components/v1/chat/event-message.tsx` | 把不同 Agent 事件渲染成聊天/工具块。 | Action/Observation 关联渲染，特殊处理 finish/think/error。 | 如果要做 Agent UI，应重点看。 | 中 |
| Settings schema | `openhands/app_server/settings/settings_models.py` | 用户设置、Agent settings、conversation settings 的版本化校验。 | Pydantic diff update、secret serializer、active profile reconcile。 | 配置复杂后值得参考。 | 中 |
| Secrets model | `openhands/app_server/secrets/secrets_models.py`、`openhands/app_server/constants.py` | 安全保存和序列化 secrets。 | 默认隐藏值、名称黑名单、大小限制。 | 值得直接参考。 | 低 |
| Planning agent 模式 | `live_status_app_conversation_service.py`、`frontend/src/hooks/use-handle-plan-click.ts` | 先规划再执行代码。 | planning agent 只写 PLAN.md，code agent 再执行计划。 | 从零 coding agent 很值得借鉴。 | 中 |
| ACP agent 接入 | `LiveStatusAppConversationService._build_acp_start_conversation_request()` | 接入外部 CLI/ACP Agent。 | 把 ACP agent 当成另一种 settings/create_request 分支。 | 适合高级阶段参考，早期不建议照搬。 | 高 |
| Enterprise server 扩展 | `enterprise/saas_server.py`、`enterprise/server/` | SaaS auth/org/billing/integrations。 | 通过导入 base app 后增量 include routers。 | 架构上可参考，业务上不建议早期复制。 | 高 |

## 7. 与我自己的 Agent 升级相关的建议

### 7.1 最值得优先学习的模块

1. **事件模型和可观测性**
   - `frontend/src/types/v1/core/events/action-event.ts`
   - `frontend/src/types/v1/core/events/observation-event.ts`
   - `openhands/app_server/event/event_service.py`
   - `openhands/app_server/event_callback/webhook_router.py`

   原因：Coding Agent 的可调试性来自完整 event trace。先把 Action、Observation、Message、State 建模清楚，比先做复杂 UI 更重要。

2. **Sandbox 边界**
   - `openhands/app_server/sandbox/sandbox_models.py`
   - `openhands/app_server/sandbox/session_auth.py`
   - `openhands/app_server/sandbox/docker_sandbox_service.py`

   原因：coding agent 必须执行代码。安全、隔离、状态管理和 session key 是底层基础。

3. **Conversation 启动状态机**
   - `openhands/app_server/app_conversation/live_status_app_conversation_service.py`
   - `openhands/app_server/app_conversation/app_conversation_service_base.py`

   原因：真实 coding agent 启动过程很长，包括 sandbox、clone repo、setup、skills、agent-server。这个项目把每一步状态化，值得学习。

4. **Settings / Secrets / LLM profile**
   - `openhands/app_server/settings/settings_models.py`
   - `openhands/app_server/secrets/secrets_models.py`
   - `LiveStatusAppConversationService._configure_llm()`

   原因：多模型、多 provider、用户密钥、安全隐藏值是 Agent 产品早晚会遇到的问题。

5. **Planning agent 模式**
   - `PLANNING_AGENT_INSTRUCTION`
   - `frontend/src/hooks/use-handle-plan-click.ts`
   - `frontend/src/hooks/use-handle-build-plan-click.ts`

   原因：从零项目可以先实现“规划文件 + 执行计划”来提升复杂任务稳定性。

### 7.2 适合直接参考设计的模块

- `ActionEvent` / `ObservationEvent` / `MessageEvent` 的事件协议。
- `SandboxStatus` / `SandboxInfo` / `ExposedUrl` 的沙箱模型。
- `session_auth.validate_session_key()` 的 session ownership 校验思想。
- `PendingMessageService` 的断线/未 ready 消息缓存。
- `EventCallbackProcessor` 的回调扩展点。
- `MCP proxy` 的安全边界：API key 留在 server，sandbox 只访问受控代理。
- `skills`/`microagents` 的轻量知识注入机制。
- `AppServerConfig` + `Injector` 的 provider 切换方式。

### 7.3 暂时不建议照搬的模块

- **完整 Enterprise 体系**：`enterprise/` 涉及组织、计费、权限、迁移、第三方集成，复杂度高，只有 SaaS 阶段需要。
- **Remote Sandbox 全量实现**：`remote_sandbox_service.py` 依赖外部 runtime API 和云资源生命周期，早期可先用本地 Docker。
- **ACP Agent 接入**：适合兼容 Claude Code/Codex/Gemini CLI 类 Agent，但从零项目初期会增加协议和进程管理复杂度。
- **多 provider Git 集成全量支持**：GitHub/GitLab/Bitbucket/Azure DevOps 都支持很重。早期可只做 GitHub。
- **外部 SDK 的默认工具体系**：当前仓库没有源码，不能直接从这里复制；建议只参考事件协议和工具类别。
- **复杂 LLM profile/switching**：早期先支持单 provider + 单 profile，等产品化后再引入 `SwitchLLMTool` 类能力。

## 8. 明确未发现的模块

以下模块在当前仓库源码中未发现完整实现，需要查看外部依赖或其他仓库：

| 模块 | 当前仓库状态 | 说明 |
| --- | --- | --- |
| 核心 Agent runner/loop | 未发现 | 只看到 `create_agent()`、`create_request()` 和对 agent-server 的 HTTP 调用；loop 位于 `openhands-sdk` / `openhands-agent-server`。 |
| LLM adapter 内部调用 | 未发现 | 当前仓库只构造 `LLM` 配置；实际 LiteLLM/OpenAI 调用不在本仓库。 |
| Function Tool decorator/schema executor | 未发现 | 本仓库有 MCP tools 和事件类型，但默认工具注册/执行实现来自 `openhands-tools`。 |
| 工具调度器 | 未发现 | Action 到具体 bash/file/browser/MCP executor 的调度不在本仓库。 |
| 浏览器工具执行实现 | 未发现 | 前端有 browser action/observation 类型，执行器源码不在本仓库。 |
| 文件编辑工具执行实现 | 未发现 | 前端有 file editor action/observation 类型，执行器源码不在本仓库。 |
| bash/terminal 工具执行实现 | 未发现 | App Server 只做 setup 命令；Agent 工具执行器在 sandbox/agent-server 外部依赖中。 |
| OpenAI Agents SDK 风格 handoff 对象 | 未发现 | 仅有 planning sub-conversation、SDK sub-agent definitions、ACP 分支。 |
| 传统 RAG/vector store | 未发现 | 没有 embedding/vector DB/retriever pipeline；只有 skills、Tavily MCP、grep/glob 类检索能力。 |
| 模型输出 guardrail/critic 实现 | 未发现 | 前端事件有 `security_risk`、`critic_result` 字段，但判定逻辑未在本仓库发现。 |
| condenser 内部摘要算法 | 未发现 | 当前仓库创建 `LLMSummarizingCondenser`，实现位于外部 SDK。 |

## 9. 快速复习索引

如果另一个 Codex 或开发者只想快速进入代码，可以按这个顺序阅读：

1. `pyproject.toml`  
   先确认本仓库依赖了 `openhands-sdk`、`openhands-agent-server`、`openhands-tools`，避免误以为核心 loop 在当前仓库。

2. `openhands/app_server/app.py`、`openhands/app_server/v1_router.py`  
   理解后端服务入口和路由组织。

3. `openhands/app_server/app_conversation/app_conversation_router.py`  
   看 `start_app_conversation()` 和 `send_message_to_conversation()`，理解 API 边界。

4. `openhands/app_server/app_conversation/live_status_app_conversation_service.py`  
   重点看 `_start_app_conversation()` 和 `_build_start_conversation_request_for_user()`。这是应用层到 Agent Server 的核心桥。

5. `openhands/app_server/app_conversation/app_conversation_service_base.py`  
   看 `run_setup_scripts()`、`clone_or_init_git_repo()`、`maybe_run_setup_script()`、`maybe_setup_git_hooks()`，理解 workspace 准备。

6. `openhands/app_server/sandbox/sandbox_models.py`、`docker_sandbox_service.py`、`remote_sandbox_service.py`、`session_auth.py`  
   理解执行环境、安全边界和 session key。

7. `openhands/app_server/event_callback/webhook_router.py`、`openhands/app_server/event/event_service.py`  
   理解 agent-server 如何把执行过程回传给 App Server。

8. `frontend/src/contexts/conversation-websocket-context.tsx`、`frontend/src/stores/use-event-store.ts`、`frontend/src/components/v1/chat/event-message.tsx`  
   理解前端如何接收和展示 Agent 轨迹。

9. `frontend/src/types/v1/core/base/action.ts`、`frontend/src/types/v1/core/base/observation.ts`、`frontend/src/types/v1/core/events/action-event.ts`、`frontend/src/types/v1/core/events/observation-event.ts`  
   理解 Agent 行为协议。

10. `openhands/app_server/settings/settings_models.py`、`openhands/app_server/secrets/secrets_models.py`、`openhands/app_server/constants.py`  
    理解配置、密钥、校验和安全限制。

## 10. 一句话架构总结

OpenHands 当前仓库的核心价值不是单个 Agent loop，而是把一个外部 SDK/agent-server 驱动的 Coding Agent 产品化：它负责创建安全沙箱、准备代码仓库、注入 LLM/工具/技能/密钥、接收并持久化 Agent 事件、通过前端实时展示执行轨迹，并把规划、MCP、Git 集成、Enterprise 能力组织成一个可运营的平台。
