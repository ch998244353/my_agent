# 架构索引

## 项目与验证边界

- **定位**：[FACT] 这是一个教学用、模仿 Agents SDK 风格的 Python runtime；当前直接依赖包含 OpenAI SDK、`psycopg[binary,pool]`、FastAPI、Pydantic 与 Uvicorn，HTTPX 只放在测试依赖组。`pyproject.toml`
- **分析模式**：`INCREMENTAL`。模型 runtime/state/observability、Task Graph events/replay、Planning Tools、Task Runtime 关联、Step 01 计划核心/Runner 恢复与 Step 02 PlanningRun PostgreSQL 持久化已按当前工作树完成复核；基础架构文档最初由 BOOTSTRAP 建立。
- **范围**：[FACT] `src/agents`、`tests`、`pyproject.toml` 及 `my_agent` 内 Git diff；忽略 `.codegraph`、缓存、虚拟环境、构建产物和 `../reference`。
- **最近验证 revision**：`5ef4cf403f510816b3d68d6eb491b42ba061ab02`（HEAD）；另已检查当前 staged/unstaged/untracked diff。
- **CodeGraph 边界**：[FACT] 2026-07-25 强制全量重建后，索引包含 125 个 Python 文件、3961 个节点和 11235 条关系，已收录当前未跟踪的 `model_events.py`、`model_retry.py` 及其测试。`Runner.run_stream` 只构造惰性 `RunStream`，静态 trace 在动态 producer 启动处中断；从真实循环入口可完整追到 `_run_agent_loop_impl -> run_model_turn -> _run_model_turn_impl -> _run_model_turn_async -> run_model_request`。`run_model_request` 的唯一生产 caller 是 `_run_model_turn_async`。`src/agents/runner.py::RunStream._start_once`、`src/agents/model_turn.py::_run_model_turn_async`
- **Task Graph / Planning Tools 复核**：[FACT] 2026-07-31 强制全量重建后，索引包含 131 个 Python 文件、4263 个节点和 12653 条关系，已收录 `task_tools.py` 和最终测试。CodeGraph 显示 `update_task -> _require_owned_task -> _is_in_root_scope`、`update_task -> TaskStore.transition`、`list_tasks -> TaskStore.ready`；`create_task_tools()` 的静态 callers 全是测试，Agent、Runner、RunState、Session 和 CLI 没有默认 factory caller。`src/agents/task_tools.py::create_task_tools`、`src/agents/task_tools.py::update_task`、`src/agents/task_tools.py::list_tasks`
- **Task Runtime / 同步兼容复核**：[FACT] 2026-07-31 强制全量重建后，索引包含 132 个 Python 文件、4351 个节点和 12978 条关系。CodeGraph trace 为 `_run_agent_loop_impl -> _build_result_and_save_session -> build_run_result`；`Runner.run_async -> SyncRunCollector.collect -> RunStream._wait_result` 返回同一 producer 的结果。`RunConfig` impact 包含 Agent-as-tool、Chat、ChatRuntime、Coding 组装和三种 Runner 入口。`src/agents/run_loop.py::_run_agent_loop_impl`、`_build_result_and_save_session`、`src/agents/runner.py::SyncRunCollector.collect`、`Runner.run_async`
- **M01 Chat Backend 复核**：[FACT] 2026-08-02 强制全量重建后，索引包含 140 个文件（139 个 Python、1 个 YAML）、4456 个节点和 13219 条关系，已收录 `chat_backend` 配置、连接池、迁移和测试。CodeGraph 可直接定位 `AppSettings`、`Database`、`MigrationRunner`；原 Agent 主链仍是 `_run_agent_loop_impl -> run_model_turn -> _run_model_turn_impl -> _run_model_turn_async -> run_model_request`，M01 没有增加第二个 Agent loop。`src/agents/chat_backend/settings.py::AppSettings`、`src/agents/chat_backend/database.py::Database`、`src/agents/chat_backend/migration.py::MigrationRunner`
- **M02 Runner 恢复 Lesson 05 复核**：[FACT] 2026-08-03 强制全量重建后，索引包含 143 个文件（142 个 Python、1 个 YAML）、4543 个节点和 13520 条关系。真实 PostgreSQL 集成检查以两套重新创建的 `Database/MessageStore/PostgresSession/Agent` 连续执行 `Runner.run_async()`；第二轮模型收到第一轮 user、普通工具、内置 final-answer 工具和最终 assistant 的完整轨迹，数据库 sequence 连续且无重复。CodeGraph 显示两个新检查直接调用 `Runner.run_async`；生产 Runner 和 Session 源码未修改。当前普通 `model_error` 结果仍经 `RunResult.to_input_list()` 保存本轮 user，成功后再提交的边界留给 Lesson 06。`tests/test_chat_backend_migration.py::MigrationRunnerPostgresTests.test_runner_recovers_postgres_history_with_new_dependencies`、`test_runner_model_error_currently_saves_the_user_input`
- **M02 成功提交 Lesson 06 复核**：[FACT] 2026-08-03 强制全量重建后，索引包含 145 个文件（144 个 Python、1 个 YAML）、4603 个节点和 13678 条关系。`ChatService.send_message()` 先在线程中确认会话，通过零参数 `agent_factory` 取得请求级 Agent 与模型，把 Runner 的 Session 写入留在本轮缓冲；只有 `ChatTurn` 有非空最终答案且缓冲末尾匹配时，才在线程中一次调用 `MessageStore.add_batch()`。该缓冲现已移动到 session 模块并增加可选最终展示文字替换；普通 ChatService 不传替换值，行为不变。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession.commit`、`src/agents/models.py::OpenAIResponsesModel`
- **M02 HTTP 合同 Lesson 07 复核**：[FACT] 2026-08-04 强制全量重建后，索引包含 147 个文件（146 个 Python、1 个 YAML）、4663 个节点和 13787 条关系，已收录 `chat_backend/http.py` 及其检查。Pydantic 模型冻结消息、分页、会话、历史、回复、健康与错误 JSON；普通转换函数只复制允许公开的字段。`register_error_handlers()` 直接使用 FastAPI exception handler，把请求校验、服务、数据库和未知异常映射成同一种安全错误结构；本课没有 app、路由或第二条 Agent loop。`src/agents/chat_backend/http.py::MessageCreate`、`HistoryQuery`、`ErrorResponse`、`register_error_handlers`
- **M02 模块 Lesson 09 最终复核**：[FACT] 2026-08-04 强制全量重建后，索引包含 147 个文件（146 个 Python、1 个 YAML）、4711 个节点和 13907 条关系，已收录 M02 的 Store、PostgresSession、ChatService、`create_app`、lifespan、四条业务 route 与 `main`。CodeGraph 可直接追到 `ChatService.send_message -> Runner.run_async`；异步 producer 处的静态路径会中断，但从真实循环入口仍完整显示 `_run_agent_loop_impl -> run_model_turn -> _run_model_turn_impl -> _run_model_turn_async -> run_model_request`，没有第二条 Agent loop。真实 PostgreSQL Gate 覆盖迁移、约束、回滚、并发 sequence、重建恢复、失败不提交和 HTTP app 重建；全量没有新增失败。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/runner.py::Runner.run_async`、`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/chat_backend/http.py::create_app`
- **M03 长期记忆最终复核**：[FACT] 2026-08-05 强制全量重建后，索引包含 151 个文件（150 个 Python、1 个 YAML）、4920 个节点和 14709 条关系。CodeGraph 直接显示 `ChatService.send_message -> MemoryExtractionService.extract -> Runner.run_async`，并显示 `save_candidates -> MemoryStore.create/supersede`；保存在线程中动态调用的部分再由源码和真实 PostgreSQL Gate 补核。M03 没有修改通用 Runner、SessionLike 或模型重试循环。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/memory_extraction.py::MemoryExtractionService`、`src/agents/chat_backend/memory.py::MemoryStore`
- **M04 检索与知识 RAG 最终复核**：[FACT] 2026-08-05 强制全量重建后，索引包含 156 个文件、5223 个节点和 5061 条关系。CodeGraph 直接显示 `ChatService.send_message -> Runner.run_async`、`prepare_turn_input -> build_turn_context`、`KnowledgeIndexer.reindex -> _prepare/replace_chunks` 与两种 Retrieval Service 到 RRF/预算 helper 的调用；两个 Store 都有同名搜索方法，CodeGraph 将记忆搜索边错误归到 `KnowledgeStore`，因此 `MemoryRetrievalService.retrieve -> MemoryStore.search_keyword/search_vector` 由 `retrieval.py` 的类型和值调用以及模块 Gate 补核。没有第二条 Agent loop。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/model_turn.py::prepare_turn_input`、`src/agents/chat_backend/retrieval.py::MemoryRetrievalService.retrieve`、`KnowledgeRetrievalService.retrieve`、`src/agents/chat_backend/knowledge.py::KnowledgeIndexer.reindex`
- **M05 统一检索契约 Lesson 01 复核**：[FACT] 2026-08-06 强制全量重建后，索引包含 156 个文件、5234 个节点和 5072 条关系，已收录 `RetrievalHit`、`Citation`、`RetrievalBundle` 与 `_build_bundle()`。当前 `_build_bundle()` 只有测试 caller：它按最终候选顺序共同执行条数/字符预算，只给实际进入单个安全 user-role `ContextChunk` 的记忆/知识命中分配连续 marker；尚未访问 Store、执行 RRF、解析回答或接入 ChatService。`src/agents/chat_backend/retrieval.py::RetrievalHit`、`Citation`、`RetrievalBundle`、`_build_bundle`
- **M05 四路统一检索 Lesson 02 复核**：[FACT] 2026-08-06 强制全量重建后，索引包含 156 个文件、5247 个节点和 5085 条关系。`HybridRetrievalService.retrieve()` 已接入 `_normalize_source_ids`、一次 `EmbeddingClient.embed()`、现有 RRF 与 `_build_bundle()`；CodeGraph 因两个 Store 的搜索方法同名，只显示知识 Store 边并把旧同名 `retrieve()` 测试误算为 caller，因此两类 Store 的真实调用与当前无生产 caller 另由源码和测试补核。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`、`tests/test_chat_backend_retrieval.py::HybridRetrievalServiceTests`
- **M05 本轮可信引用 Lesson 03 复核**：[FACT] 2026-08-06 强制全量重建后，索引包含 156 个文件、5255 个节点和 5093 条关系，已收录私有 `_CITATION_MARKER_PATTERN` 与公开 `parse_citations()`。CodeGraph 显示解析函数当前只有四个测试 caller；它只扫描严格 marker、与传入白名单求交并按回答首次出现顺序去重，没有 Store、Session、网络或文本改写调用。`src/agents/chat_backend/retrieval.py::parse_citations`、`tests/test_chat_backend_retrieval.py::CitationParserTests`
- **M05 Chat 与 HTTP 引用 Lesson 04 复核**：[FACT] 2026-08-06 强制全量重建后，索引包含 156 个文件、5265 个节点和 5103 条关系。CodeGraph 直接显示 `ChatService.send_message -> parse_citations / Runner.run_async` 与 `chat_reply_response -> citation_response`；统一检索经 `asyncio.to_thread()` 动态调用，静态 callers 未显示该生产边，因此由 `service.py` 源码和服务测试补核。`ChatReply` 兼容追加 Citation tuple，HTTP 只显式输出五个安全字段；`create_app()` 仍组装旧记忆检索。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::citation_response`、`chat_reply_response`

- **M05 范围回退复核**：[FACT] 2026-08-07 按新的六课计划移除已加入的额外运行时客户端、query embedding 中间路径、依赖、配置、Compose 服务与对应测试后，CodeGraph 强制全量重建为 156 个文件、5265 个节点和 5103 条关系；`HybridRetrievalService.retrieve()` 已恢复直接调用一次 `EmbeddingClient.embed()`，再沿两个 Store、现有 RRF 和 `_build_bundle()` 运行。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`、`pyproject.toml`、`compose.yaml`
- **M05 默认应用组合 Lesson 05 复核**：[FACT] 2026-08-07 强制全量重建后，索引包含 156 个文件、5267 个节点和 5105 条关系。`create_app()` 现在用同一个关闭状态 `Database` 创建 `MemoryStore` 与 `KnowledgeStore`；有 embedding client 时创建 `HybridRetrievalService` 并以关键字参数交给 `ChatService`，否则保持无检索。CodeGraph 对函数内构造只显示 `register_error_handlers`，两个 Store、Hybrid 与 ChatService 的真实组合由 `http.py` 源码和 HTTP 组合测试补核。`src/agents/chat_backend/http.py::create_app`、`tests/test_chat_backend_http.py::HttpAppTests.test_optional_memory_and_retrieval_composition_is_deferred`
- **M05 模块收尾 Lesson 06 复核**：[FACT] 2026-08-07 强制全量重建后，索引包含 156 个源码文件、5269 个节点和 5107 条直接关系。新增跨层 Gate 从真实 HTTP route 经过默认 Hybrid、四路 Store 调用、一次 embedding、唯一 Runner、成功提交和本轮 Citation 白名单，证明检索正文进入模型但不进入 Session。CodeGraph 直接显示 `HybridRetrievalService.retrieve -> embed / reciprocal_rank_fusion / _build_bundle`、`ChatService.send_message -> Runner.run_async / parse_citations` 与 `chat_reply_response -> citation_response`；线程动态调用、函数内构造和同名 Store 方法仍由源码与 Gate 补核。`tests/test_chat_backend_http.py::HttpAppTests.test_m05_gate_runs_hybrid_context_and_returns_current_citations`、`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`、`src/agents/chat_backend/service.py::ChatService.send_message`
- **M06 管理 API 与系统验收最终复核**：[FACT] 2026-08-07 强制全量重建后，索引包含 158 个文件（157 个 Python、1 个 YAML）、5418 个节点、16396 条关系和 21 个 route 节点；其中生产 `create_app()` 有 19 条 route，其余 route 节点来自测试。会话管理由 `007_conversation_management.sql`、`ConversationStore.create/get/list/update/delete` 和 `ChatService.send_message()` 的共同归档判断闭合；HTTP 在同一个 app 中直接复用既有 Store、Indexer 与唯一 Runner，没有第二条 Agent loop。CodeGraph 直接显示 HTTP `send_message -> ChatService.send_message -> Runner.run_async`、`reindex_knowledge -> KnowledgeIndexer.reindex`，并确认归档错误影响共同聊天入口；函数内 Store 调用仍由源码和 Gate 补核。`src/agents/chat_backend/migrations/007_conversation_management.sql`、`src/agents/chat_backend/conversation.py::ConversationStore`、`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::create_app`
- **本地多模型流式 Web 工作台复核**：[FACT] 2026-08-08 强制重建后的 CodeGraph 为 163 个文件、5655 个节点、17269 条关系和 33 个 route 节点。同一个 FastAPI app 当前有 28 条 API route 和 3 条静态资源 route；除模型预设/Profile 管理、系统钥匙串凭据、兼容适配器与 SSE 外，新增请求级模型列表发现。生产 `main.chat_agent` 在构造 Chat Agent 前先解析当前激活 Profile，避免无 OpenAI 环境变量时默认模型抢先初始化；`configured_agent_factory` 再取得本次请求最终快照。`stream_message` 到模型循环会在嵌套 async generator 和 `RunStream` 惰性 producer 两处打断静态 trace；分段 trace 与源码确认真实链仍是 `stream_message -> ChatService.send_message -> Runner.run_stream -> _run_agent_loop_impl -> run_model_turn -> run_model_request`，没有复制模型循环。`008_message_run_trace.sql` 为助手消息保存安全运行详情；前端按回复折叠显示检索、模型、重试、工具和保存阶段，左栏选择一次全局 Profile 后可连续新建多个会话。`src/agents/chat_backend/http.py::main`、`chat_agent`、`stream_message`、`src/agents/chat_backend/conversation.py::MessageStore.set_run_trace`、`src/agents/chat_backend/static/app.js`
- **Windows 一键启动入口复核**：[FACT] 2026-08-08 强制重建后的 CodeGraph 为 164 个文件、5665 个节点、17280 条关系和 33 个 route 节点。桌面 `启动 MyAgent.cmd` 只调用仓库内 PowerShell 脚本；脚本依次检查或后台启动 Docker Desktop、用既有 Compose healthcheck 启动 PostgreSQL、复用 `/health` 判断 HTTP 服务并在需要时启动 `my-agent-chat-http`，最后打开本机网页。重复执行不创建第二个 HTTP 进程；端口冲突只报 PID，不结束占用者；没有 `down`、数据卷删除、开机自启、关闭开关或新依赖。`scripts/start_myagent.ps1`、`compose.yaml`、`src/agents/chat_backend/http.py::health`
- **Step 01 计划核心与 Runner 恢复最终复核**：[FACT] 2026-08-08 强制重建后的 CodeGraph 为 166 个文件、5799 个节点、17782 条关系和 33 个 route 节点，已收录未跟踪的 `planning.py` 与对应测试。`PlanExecution.materialize()` 只调用输入校验、稳定拓扑遍历和既有 Store 创建；`from_events()` 只调用现有 `replay_task_events()` 与结构核对。`build_planning_agent()` 只调用 `CodingAgentProfile.read_only()` 和 `build_coding_agent()`。`_run_agent_loop_with_events` 的唯一生产 caller 仍是 `RunStream._produce`；fresh/resume 的动态分派由源码与行为 Gate 补核，没有第二个 producer 或消费循环。`src/agents/planning.py::PlanExecution`、`build_planning_agent`、`src/agents/runner.py::RunStream._produce`、`src/agents/run_control.py::_run_agent_loop_with_events`
- **Step 02 PlanningRun 持久化最终复核**：[FACT] 2026-08-08 强制重建后的 CodeGraph 为 168 个文件、5989 个节点、19055 条关系和 33 个 route 节点，已收录 009、`planning_store.py` 与真实 PostgreSQL Gate。静态关系可定位 `PlanStore.approve -> PlanExecution.materialize`、`save_execution/_planning_run_from_row -> PlanExecution.from_events` 和三类现有 codec；它把测试 helper 的同名 `execute()` 误挂到 `save_execution()`，所以该边未写成生产事实，事务 context 与运行时 JSON 解码也继续以直接源码和行为 Gate 为准。`agents.chat_backend` 只转发现有 `ConversationMode` 和九个 Planning 持久化符号，顶层 `agents` 未重复导出。`src/agents/chat_backend/planning_store.py::PlanStore`、`src/agents/chat_backend/__init__.py::__all__`
- **Step 03 Lesson 02 多轮只读草案复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6082 个节点、19305 条关系。`PlanningService.draft()` 组合现有 `PlanStore/MessageStore/ModelProfileStore`、只读 `build_planning_agent()`、唯一 Runner 与成功提交 Session；结构化 question/plan 经 Python 二次校验，计划定义先 CAS 保存，消息历史只提交可读问题或 Markdown。CodeGraph 能定位新服务和 parser，但 `asyncio.to_thread()` 内的 builder/Store 动态调用及同名 `get/cancel` 仍以直接源码和行为 Gate 补核。`src/agents/chat_backend/planning_service.py::PlanningService.draft`、`_parse_draft_output`、`src/agents/planning.py::build_planning_agent`
- **Step 03 Lesson 03 可重试执行状态复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6091 个节点、19431 条关系。它定位到 `_validate_execution_state` 只有行解码和 `save_execution` 两个生产 caller，并收录固定错误码 `_safe_run_error`；SQL 不在索引语言中，因此 010 的追加迁移、009 校验和不变、活动索引和跨连接池 running/paused 读取由直接源码与真实 PostgreSQL Gate 补核。`src/agents/chat_backend/planning_store.py::_validate_execution_state`、`PlanStore.save_execution`、`src/agents/chat_backend/planning_service.py::_safe_run_error`、`src/agents/chat_backend/migrations/010_planning_execution_control.sql`
- **Step 03 Lesson 04 严格串行普通步骤复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6115 个节点、19533 条关系。它能定位 `PlanningService.approve -> _drive_running`，以及 `_drive_running -> PlanExecution.from_events/next_ready/_claim_task/_run_current_step`、`_run_current_step -> Runner.run_stream/RunResult.to_state/TaskStore.transition`；`asyncio.to_thread()` 内的 PlanStore/Profile 调用未形成可靠边，同名 `get/cancel` 仍有误连，因此“RUNNING 先提交、CAS 失败不进 Runner”和冻结资源核对由直接源码及行为 Gate 补核。`src/agents/chat_backend/planning_service.py::PlanningService.approve`、`_drive_running`、`_claim_task`、`_run_current_step`
- **Step 03 Lesson 05 最终固定验证复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6125 个节点、19579 条关系。它能定位 `_drive_running -> _run_final_verification`，以及最终验证对冻结命令、工作区构建、Task 转换和安全错误的静态调用；`asyncio.to_thread()` 内的 `VerificationRunner.run()` 与 PlanStore 保存没有形成完整 trace，因此“先保存 final RUNNING、在线程运行唯一冻结命令、通过后 completed、失败后 paused”的边界由直接源码和行为 Gate 补核。`src/agents/chat_backend/planning_service.py::PlanningService._drive_running`、`_run_final_verification`、`_frozen_test_command`
- **Step 03 Lesson 06 工具审批恢复复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6141 个节点、19662 条关系。它能定位 `PlanningService.decide_tool_approval -> approve_tool_call/reject_tool_call/_resume_current_step/_drive_running`，以及 `_resume_current_step -> Runner.resume_stream`；线程内的 Store CAS 与资源构造仍由直接源码和行为 Gate 补核。审批只按精确 `(tool_name, call_id)` 决定，恢复前核对 v3 snapshot 与唯一 RUNNING Task 的 id/revision，先 CAS 清除审批快照并保存 running，成功后才继续原 RunState。`src/agents/chat_backend/planning_service.py::PlanningService.decide_tool_approval`、`_restore_approval_run`、`_resume_current_step`
- **Step 03 Lesson 07 失败控制与用户命令复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6157 个节点、19757 条关系。它能定位 `retry_current -> PlanExecution.from_events/_drive_running`，以及 `PlanStore.revise/cancel -> _cancel_unfinished_tasks`；`asyncio.to_thread()` 中的 Store 调用仍需直接源码和行为 Gate 补核。重试先以调用方 row revision 把 paused CAS 为 running，再复用同一个串行循环；修订与取消先在原 TaskEvent journal 取消所有未完成任务，已完成结果保留。`src/agents/chat_backend/planning_service.py::PlanningService.retry_current`、`revise`、`cancel`、`src/agents/chat_backend/planning_store.py::_cancel_unfinished_tasks`
- **Step 03 Lesson 08 启动恢复与损坏隔离复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6174 个节点、19851 条关系。它能定位 `recover_running`、`PlanningRecoveryReport` 与 `PlanStore.quarantine_running`，但 `asyncio.to_thread()` 内的候选扫描、隔离写和 `_recover_one -> _drive_running` 动态边仍以直接源码和行为 Gate 补核。恢复只取稳定排序的 running，逐条 await；坏行以可信 UUID 锁行隔离为 failed，临时失败保持 running 并进入 deferred。`src/agents/chat_backend/planning_service.py::PlanningService.recover_running`、`_recover_one`、`src/agents/chat_backend/planning_store.py::PlanStore.quarantine_running`、`list_recoverable_running`
- **Step 03 Lesson 09 公共接口与架构收口复核**：[FACT] 2026-08-09 强制重建后的 CodeGraph 为 171 个文件、6177 个节点、19854 条关系。`PlanningService`、四个冻结安全返回值与两个 Service 错误现从 `agents.chat_backend` 公开，顶层 `agents` 不重复导出；schema、资源解析、Task 领取和串行执行 helper 仍为模块内部实现。CodeGraph 能定位 `recover_running -> _recover_one -> _drive_running -> _run_final_verification`、`_run_final_verification -> VerificationRunner` 及 Runner 入口；线程中的 Store 调用继续由源码、内存 Gate 与真实 PostgreSQL Gate 补核。`src/agents/chat_backend/planning_service.py::__all__`、`PlanningService`、`src/agents/chat_backend/__init__.py::__all__`
- **Planning 真实循环与前端展示升级复核**：[FACT] 2026-08-13 最终强制全量重建后的 CodeGraph 为 171 个文件、6273 个节点、20203 条关系和 40 个 route 节点。Web 应用创建同一个 `PlanStore/PlanningService`，Planning 控制接口经 `PlanningService.approve -> _drive_running -> _run_current_step -> Runner.run_stream` 回到唯一 Agent loop；没有增加 Runner、Worker、EventBus 或第二套任务状态。兼容 Chat Completions 的结构化调用同时发送 JSON Schema 提示和 `response_format=json_object`，服务可严格解码字典或 JSON 字符串，并在每次草案/重试时注入数据库中的权威 objective。HTTP/SSE 只公开安全阶段、持久化快照和固定错误。前端在 drafting 才接受规划正文；计划生成后改为明确引导批准、修订或取消，同时允许切回聊天；失败提交不清空原输入。`src/agents/models.py::OpenAIChatCompletionsModel`、`src/agents/chat_backend/http.py::planning_stream`、`src/agents/chat_backend/planning_service.py::_parse_draft_output`、`src/agents/chat_backend/static/app.js::planningComposerBlockReason`
- **Planning 工具批准闭环复核**：[FACT] 2026-08-14 最终强制全量重建后的 CodeGraph 为 175 个文件、6351 个节点、20477 条关系和 40 个 route 节点。`RunResult.to_state -> RunStateSnapshot(model_state) -> RunState.from_snapshot -> OpenAIChatCompletionsModel.restore_state` 让新建 Chat Completions adapter 在批准工具后恢复供应商私有工具交换；DeepSeek thinking 所需 `reasoning_content` 只回传供应商，不进入 ModelTextDelta、Planning API/SSE 或最终回答。同一模型轮既有安全调用又有待批准调用时，恢复会从最后一条模型响应重建原顺序的完整工具批次；已批准调用按原决定执行，未决定的安全调用重新使用现有审批规则判断并执行，仍需审批的调用继续暂停。普通步骤提示还会带入已完成直接依赖的结果，总预算仍为 20000 字符。真实网页已从草案、计划批准、工具批准、第二轮模型到最终验证 completed 跑通。`src/agents/result.py::RunResultBase.to_state`、`src/agents/run_state.py::_paused_turn_tool_calls`、`RunState.from_snapshot`、`src/agents/run_resume.py::resume_pending_tool_approvals`、`src/agents/chat_backend/planning_service.py::_step_prompt`
- **Planning 完成态前端收口复核**：[FACT] 2026-08-14 最终强制重建后的 CodeGraph 为 175 个文件、6356 个节点、20489 条关系和 40 个 route 节点。前端用一个原生 `details.planning-shell` 投影最新 PlanningSnapshot：活动计划首次打开，进入 completed/cancelled/failed/superseded 后自动收起，用户仍可手动展开；运行进度始终默认折叠。数据库中的计划 Markdown 和安全 `run_trace` 继续保留给 Session/刷新恢复，但与当前 `plan_markdown` 匹配的助手消息不再重复显示；completed 快照在计划面板之后生成一条简短最终回复，正文只取步骤完成数、普通步骤结果摘要和最终验证结果，不新增消息、表或第二套状态。`src/agents/chat_backend/static/app.js::planningShellOpen`、`isPlanningDefinitionMessage`、`renderPlanningFinalReply`、`src/agents/chat_backend/static/index.html::planning-final`

## 目录 → 逻辑模块

| 目录/文件 | 逻辑模块 | 入口或证据 |
|---|---|---|
| `src/agents/{agent,runner,run_config,run_loop,run_state,result}.py` | 通用运行内核与可选任务关联 | `src/agents/agent.py::Agent.run`、`src/agents/run_config.py::RunConfig` |
| `src/agents/{model_turn,models,model_events,model_settings,model_retry}.py`、`contracts.py::ModelRequest` | 模型回合、同步/流式能力边界、Responses 适配与内部容错执行 | `src/agents/model_turn.py::run_model_turn`、`src/agents/model_retry.py::run_model_request` |
| `src/agents/{tools,tool_*,handoffs,run_steps}.py` | 工具、审批、handoff | `src/agents/tool_planning.py::build_tool_execution_plan` |
| `src/agents/{workspace*,environment,shell_tools,edit_tools,coding_*}.py` | Coding workspace 与 CLI | `src/agents/coding_cli.py::run_coding_agent_cli` |
| `src/agents/{memory,context_*,repo_*,selected_files}.py` | session/memory/context | `src/agents/repo_context.py::build_task_repo_context` |
| `src/agents/{task_state,task_events,task_tools}.py` | 独立内存任务图、事件 journal、严格字典转换/重放与显式 Planning Tools | `src/agents/task_state.py::TaskStore`、`src/agents/task_tools.py::create_task_tools` |
| `src/agents/planning.py` | 不可变计划定义、严格 JSON/Markdown 投影、TaskStore 执行视图与两种最小 Coding Agent 组装 | `src/agents/planning.py::PlanDefinition`、`PlanExecution`、`build_planning_agent` |
| `src/agents/chat_backend/{planning_store,planning_service}.py`、`migrations/{009_planning_runs,010_planning_execution_control}.sql` | 会话 plan mode、计划版本/生命周期、CAS 事务、TaskEvent/RunState PostgreSQL 保存、多轮草案、严格串行执行、paused 用户控制与 running 恢复 | `src/agents/chat_backend/planning_store.py::PlanStore`、`PlanningRun`、`src/agents/chat_backend/planning_service.py::PlanningService`、`src/agents/chat_backend/migrations/010_planning_execution_control.sql` |
| `src/agents/chat_backend/`、`compose.yaml` | 环境配置、本地 PostgreSQL、连接池、事务、顺序迁移、对话/消息/PlanningRun 管理、Planning HTTP/SSE 与启动恢复、长期记忆与知识管理、1024 维 embedding、四路统一检索、本轮可信引用、PostgreSQL Session，以及 35 条 API route 和 3 条同源静态 route | `src/agents/chat_backend/embedding.py::EmbeddingClient`、`src/agents/chat_backend/conversation.py::ConversationStore`、`src/agents/chat_backend/planning_store.py::PlanStore`、`src/agents/chat_backend/memory.py::MemoryStore`、`src/agents/chat_backend/knowledge.py::KnowledgeStore`、`KnowledgeIndexer`、`src/agents/chat_backend/retrieval.py::HybridRetrievalService`、`parse_citations`、`src/agents/chat_backend/service.py::ChatService`、`src/agents/chat_backend/http.py::create_app` |
| `src/agents/chat_backend/model_profiles.py`、`static/` | 本机模型 Profile/钥匙串、多厂商兼容配置与同源 Web 工作台 | `src/agents/chat_backend/model_profiles.py::ModelProfileStore`、`model_for_profile`、`src/agents/chat_backend/http.py::stream_message` |
| `src/agents/{event_compat,events,run_control,guardrails,verification,tracing,trajectory,lifecycle}.py` | 事件契约、单 run 控制、旧记录适配、安全、验证与观测 | `src/agents/run_control.py::RunController`、`src/agents/events.py::RunEvent` |
| `tests/` | 行为证据 | 与源文件同名的 `test_*.py` |

## 主入口与核心流程

```text
Agent.run_stream / Runner.run_stream
  -> lazy RunStream -> first read starts _produce
  -> _run_agent_loop_with_events
  -> asyncio.to_thread(run_agent_loop)
  -> ordered RunEvent ... terminal -> RunResult

Runner.resume_stream(existing RunState)
  -> same lazy RunStream / _produce / event runtime
  -> _run_agent_loop_with_events(run_state=...)
  -> asyncio.to_thread(resume_agent_loop)
  -> ordered RunEvent ... terminal -> RunResult

Agent.run_async / Runner.run_async / Runner.resume_async
  -> SyncRunCollector consumes one RunStream -> original RunResult

chat_cli.main / coding_cli.main / Agent.run / Runner.run_sync
  -> asyncio.run(run_async) -> same RunStream producer
  -> optional RunConfig(task_store, task_id)
  -> validate binding before memory/model/session effects
  -> prepare_turn_input -> run_model_turn -> run_model_request
  -> ModelEvent accumulation -> successful ModelExecution
  -> execution plan -> tool | handoff | final | pause
  -> RunResult -> optional session/state/trajectory
```

[FACT] 上述箭头分别由 `src/agents/chat_cli.py::main`、`src/agents/coding_cli.py::main`、`src/agents/agent.py::Agent.run`、`src/agents/run_loop.py::_run_agent_loop_impl` 连接。

## 独立 Task Graph、journal 与 replay

```text
TaskSpec
  -> TaskStore.create / add_dependency / transition / mark_owner_missing
  -> staged frozen TaskNode batch
  -> TaskEvent batch
  -> _apply_event_batch
       -> nodes + seen event ids + append-only in-memory journal
  -> events() -> task_event_to_dict -> caller-owned JSON medium
  -> task_event_from_dict -> replay_task_events -> equivalent new TaskStore
```

- [FACT] `TaskStore` 用进程内节点字典、seen-event 字典、journal 列表和同一把 `RLock` 保存事实；`expected_revision` 拒绝旧快照覆盖新状态，默认仍最多 100 个节点。`src/agents/task_state.py::TaskStore`
- [FACT] parent 只表示创建树，dependency 只表示开始前置条件；四个写入口和全部级联先形成完整 `TaskEvent` 批次，再由 `_apply_event_batch()` 在一份工作副本上检查 id、revision、合法变化、引用、环和 limits，全部成功后同时提交 nodes/seen/journal。`src/agents/task_state.py::_reduce_task_event`、`TaskStore._apply_event_batch`
- [FACT] `TaskEvent` 是 frozen schema v1 after-node 事件；严格 converter 拒绝额外/缺失字段、未知 schema/enum、非规范 UTC 和不能无损 JSON 往返的 result。重放保持输入顺序，不排序、不补事件；相同 id/相同内容为 no-op，相同 id/不同内容或 revision gap 明确失败。`src/agents/task_state.py::TaskEvent`、`src/agents/task_events.py::task_event_from_dict`、`replay_task_events`
- [FACT] 包根除原 Task Graph 值与异常外，还公开 schema、`TaskEventType`、`TaskEvent`、四个 converter 和 `replay_task_events()`；reducer、apply hook、journal/seen 容器和 event id factory 保持内部。`src/agents/__init__.py::__all__`
- [FACT] 10,000 个顺序 created 事件在显式 `max_nodes=10000` 后可恢复；本机 Python 3.14.2 / Windows 11 测得 replay `0.012235s`、峰值额外内存 `660800 bytes`（`0.630 MiB`）。新任务创建只检查依赖存在，不执行不可能命中的“从旧图回到尚未入图任务”搜索；更新事件仍执行完整 dependency 环检查。`src/agents/task_state.py::_validate_event_node_graph`、`tests/test_task_events.py::TaskEventReplayTests`
- [FACT] Task Graph 可由显式 Planning Tools 修改，也可由 `RunConfig(task_store, task_id)` 与一次 run 关联；`RunState`、`RunResult` 和 snapshot 只保存 `task_id/task_revision`，不复制 Store、节点或 TaskEvent。ToolExecutionPlan、RunEvent、CLI 和 Session 仍没有 task-specific writer。`src/agents/task_tools.py::create_task_tools`、`src/agents/run_config.py::RunConfig`、`src/agents/run_state.py::RunState`、`src/agents/task_events.py`

## 显式 Planning Tools

```text
调用方持有 TaskStore/root/owner
  -> create_task_tools
  -> create_task / update_task / list_tasks
  -> ToolRegistry.register（调用方显式执行）
  -> 普通 Agent 工具调用链
  -> TaskStore.create / transition / list / ready
  -> TaskEvent journal 或只读节点字典
```

- [FACT] `create_task_tools()` 只返回固定顺序的三个普通 `FunctionTool`；factory 不创建 Store/root、不注册工具，也不改变通用 Agent loop。`src/agents/task_tools.py::create_task_tools`
- [FACT] 闭包绑定 `root_task_id` 与 `owner_id`：create 的 parent/dependency 必须在 root 子树；update 还要求 owner 匹配并把 CAS revision 原样交给 Store；模型不能传 owner 或自定义 task id。`src/agents/task_tools.py::_is_in_root_scope`、`_require_owned_task`、`create_task_tools`
- [FACT] list 保留 Store 顺序，只返回 root 子树内同 owner 节点；普通查询用 `TaskStore.list()`，ready 查询用 `TaskStore.ready()`，调用前后 journal 不变。所有结果复用 `task_node_to_dict()`。`src/agents/task_tools.py::create_task_tools`
- [FACT] 工具错误继续由 `FunctionTool.execute()` 包装，并沿现有 tool error observation 让模型恢复；TaskEvent 留在 Store，不进入 RunEvent、RunState 或 RunResult。`src/agents/tools.py::FunctionTool.execute`、`src/agents/tool_execution.py::_execute_tool_call_impl`
- [FACT] 包根只新增公开 factory；scope helper、状态输入别名、result sentinel 和三个闭包 handler 保持私有。`src/agents/__init__.py::__all__`、`src/agents/task_tools.py`

## 计划定义、TaskStore 投影与专用 Agent 组装

```text
PlanStepSpec tuple -> PlanDefinition
  -> strict JSON v1 | review-only Markdown
  -> PlanExecution.materialize(owner_id)
       -> stable topological creation into a new TaskStore
  -> next_ready() reads TaskStore status in definition order
  -> TaskEvent tuple -> replay_task_events -> PlanExecution.from_events

build_planning_agent -> CodingAgentProfile.read_only -> build_coding_agent
build_plan_step_agent -> CodingAgentProfile.edit_local -> build_coding_agent
```

- [FACT] `PlanDefinition` 固定 2–12 个步骤，最后一个且只有一个 final verification；前向依赖合法，稳定 Kahn 遍历只改变节点创建顺序，不改变定义顺序。最终验证必须直接依赖全部普通叶子。`src/agents/planning.py::PlanStepSpec`、`PlanDefinition`、`_topological_steps`
- [FACT] schema v1 字典转换严格拒绝缺失/额外字段、错误类型和未知版本；Markdown 只供人审阅，不反向解析执行。`src/agents/planning.py::plan_definition_to_dict`、`plan_definition_from_dict`、`render_plan_markdown`
- [FACT] 每次 `materialize()` 内部创建一个全新 `TaskStore`，`step_id == task_id`，执行状态、revision、result 和 error 只从 Store 读取；`from_events()` 复用现有 typed event replay 并拒绝缺失、额外或结构不符的节点。没有 plan root、额外 id 映射、自动状态机或数据库。`src/agents/planning.py::PlanExecution`
- [FACT] 两个 Agent builder 只选择现有 `read_only()`/`edit_local()` profile 并调用一次 `build_coding_agent()`；都不注册 Planning Tools，步骤正文与已完成结果仍由未来调用方经 Runner task/context 传入。`src/agents/planning.py::build_planning_agent`、`build_plan_step_agent`

## PlanningRun PostgreSQL 持久化、版本与恢复读取

```text
Conversation(default)
  -> PlanStore.create_draft
       -> lock conversation -> version N -> safe snapshots
       -> PlanningRun(drafting) + Conversation(plan)
  -> submit_for_approval -> awaiting_approval
  -> approve -> PlanExecution.materialize(owner=run_id) -> running journal
  -> save_execution
       -> PlanExecution.from_events(owner=run_id)
       -> running boundary/current task | awaiting_tool_approval | paused
       -> completed | failed
  -> revise -> old superseded + version N+1 drafting
  -> cancel -> append cancelled TaskEvents + Conversation(default)

new connection -> list_recoverable_running -> strict decode/replay -> caller resumes
```

- [FACT] 009 只增加一个 `planning_runs` 表和 conversation mode；010 以追加迁移加入 `paused` 并替换状态组合约束和活动唯一索引，不修改 009。`version` 是会话内历史序号，`row_revision` 是单行 CAS；删除 conversation 会级联删除版本。`src/agents/chat_backend/migrations/009_planning_runs.sql`、`010_planning_execution_control.sql`
- [FACT] `PlanStore` 的公共方法直接复用现有 `Database.connection/transaction`，没有 Repository、Protocol、ORM、UnitOfWork、缓存或 worker。`get_latest()` 只读会话最新版本且包含终态；普通修改继续锁行并核对 expected revision；唯一例外 `quarantine_running()` 不解码损坏正文，只锁可信 run id、确认状态仍为 running 后原地改 failed 并恢复会话 default。`src/agents/chat_backend/planning_store.py::PlanStore`、`get_latest`、`_lock_run`、`_update_run`、`PlanStore.quarantine_running`
- [FACT] 静态计划事实只来自 `PlanDefinition`；步骤状态、结果和 revision 只来自 TaskEvent 重放后的 `TaskNode`；审批暂停只来自现有 `RunStateSnapshot`；会话入口选择只来自 `Conversation.mode`。Planning 行不保存 session items、独立 approvals、current task 或 TaskNode 子表。`src/agents/chat_backend/planning_store.py::PlanningRun`、`src/agents/chat_backend/conversation.py::ConversationMode`
- [FACT] 创建草案保存 `ModelProfile.public_dict()` 和 `WorkspaceManifest.metadata()`；批准时由 `PlanExecution.materialize()` 唯一生成初始事件；保存/读取时由三类现有 codec 与固定 `owner_id=str(run_id)` 重放复核。坏行统一转成带 run id 的 `CorruptPlanningRunError`。`src/agents/chat_backend/planning_store.py::PlanStore.create_draft`、`approve`、`_planning_run_from_row`
- [FACT] `running` 可表示步骤间安全边界，也可保留恰一个已持久化的 RUNNING 当前任务；`paused` 与 `awaiting_tool_approval` 不进入启动恢复。`PlanningService.recover_running()` 只扫描 running，遇到安全边界或已有 RUNNING Task 都复用 `_drive_running()`；候选逐条 await，不用 gather。HTTP lifespan 在迁移完成后把这一次恢复作为后台任务启动，不阻塞服务接收请求；关闭应用时取消并等待仍在运行的后台任务。损坏行隔离与 at-least-once 边界不变。`src/agents/chat_backend/http.py::lifespan`、`src/agents/chat_backend/planning_service.py::PlanningService.recover_running`、`_recover_one`、`_drive_running`
- [FACT] 子包根除既有 `ConversationMode`、Planning 持久化值与错误外，还公开 `PlanningService`、`PlanningSnapshot` 及两个结构化子投影、三个结果/进度值和两个 Service 错误；`PLANNING_DRAFT_SCHEMA`、执行 helper、原始 RunState 与资源解析函数仍只在模块内使用。顶层 `agents` 继续只公开进程内规划领域值，不复制 Chat Backend 服务或数据库接口。`src/agents/chat_backend/__init__.py::__all__`、`src/agents/chat_backend/planning_service.py::__all__`、`src/agents/__init__.py::__all__`
- [FACT] Planning 草案仍走唯一 Runner 事件流；HTTP 把安全模型轮次、工具请求/开始/完成、解析保存和完成阶段写入既有助手消息 `run_trace`，刷新后最多恢复 200 条记录。前端只把这些记录放在默认折叠的“运行进度（不包含模型私有推理）”中；同一助手消息保存的计划 Markdown 不再与结构化 PlanningSnapshot 重复显示。旧规划没有历史事件时明确说明无法还原，不伪造思维链。`src/agents/chat_backend/planning_service.py::PlanningDraftOutcome`、`src/agents/chat_backend/http.py::planning_stream`、`src/agents/chat_backend/static/app.js::beginPlanningDraft`、`isPlanningDefinitionMessage`

## 显式 run 关联、snapshot v4 与结果收集

```text
RunConfig(task_store, task_id)
  -> _resolve_task_binding（fresh/resume 副作用前读取节点）
  -> RunState(task_id, task_revision)
  -> optional Planning Tool updates the same TaskStore
  -> _build_result_and_save_session（结果/session 前刷新 revision）
  -> RunResult(task_id, task_revision)
  -> to_state -> snapshot v4

Runner.run_stream -> RunStream
Runner.run_async -> SyncRunCollector.collect(RunStream)
Runner.run_sync -> asyncio.run(run_async)
Runner.resume_stream -> same RunStream(run_state=...)
Runner.resume_async -> SyncRunCollector.collect(resume_stream)
Runner.resume_sync -> asyncio.run(resume_async)
```

- [FACT] `RunConfig.task_store/task_id` 必须同时存在或同时为空；配置构造只校验字段组合，fresh/resume 真正开始时才用 `TaskStore.get()` 读取节点。未知节点保留 `TaskNotFoundError`，并早于 memory、lifecycle、模型、工具和 session 业务副作用。`src/agents/run_config.py::RunConfig.__post_init__`、`src/agents/run_loop.py::_resolve_task_binding`、`_run_agent_loop_impl`
- [FACT] bound resume 必须提供相同 task id 的 Store；未绑定旧 snapshot 可以继续未绑定，也可以通过完整 `RunConfig` 开始关联。恢复起点和结果出口都读取 Store 当前单节点 revision；该值不是整图版本。`src/agents/run_loop.py::_resolve_task_binding`、`_build_result_and_save_session`、`src/agents/run_state.py::RunState`
- [FACT] snapshot writer 输出 v4；reader 接受 legacy、v1、v2、v3、v4。v2–v4 恢复模型 attempt 字段，v3/v4 恢复 task link，只有 v4 恢复模型 adapter 显式导出的私有状态；snapshot 不保存 Store、TaskNode、TaskEvent、controller 或事件流。`src/agents/run_state.py::RUN_STATE_SNAPSHOT_SCHEMA_VERSION`、`run_state_snapshot_from_dict`、`src/agents/result.py::RunResultBase.to_state`
- [FACT] `SyncRunCollector` 不保存事件、不重建结果，也不捕获并包装 producer 异常；`Runner.run_async()` 只把惰性 `RunStream` 交给它。包根新增这一项稳定导出，channel/runtime/bridge 与 `_wait_result` 保持私有。`src/agents/runner.py::SyncRunCollector.collect`、`Runner.run_async`、`src/agents/__init__.py::__all__`
- [FACT] `Runner.resume_stream/resume_async/resume_sync` 复用同一 `RunStream`、collector、事件 runtime、取消 monitor 和终态分类；`RunStream` 只多保存私有恢复状态，worker 调用点在既有 `run_agent_loop()` 与 `resume_agent_loop()` 之间选择。`src/agents/runner.py::Runner`、`RunStream`、`src/agents/run_control.py::_run_agent_loop_with_events`

## 模型 attempt 事件与流式能力边界

- [FACT] `ModelRequest` 固定一次模型尝试的消息、工具说明、设置、request id 与 attempt id；顶层消息和工具集合转为 tuple。`src/agents/contracts.py::ModelRequest`
- [FACT] `model_events.py` 定义供应商无关的 created、正文/reasoning/refusal、工具参数、usage 与 completed/failed/incomplete 事件；`ModelEventAccumulator` 校验 attempt、sequence、唯一终态、共享字符上限和完整工具 JSON object。越界先记录上限与加入当前分片后的尝试值，再释放临时内容；成功时返回现有 `ModelResponse`。`src/agents/model_events.py::ModelEvent`、`ModelEventDiagnostics`、`ModelEventAccumulator._reserve_buffer_chars`、`finish`
- [FACT] 同步 `ModelAdapter` 仍只声明 `get_response`；独立 `StreamingModelAdapter` 只声明 `stream_response(ModelRequest) -> AsyncIterator[ModelEvent]`。`models.py` 对 `ModelEvent` 的引用只位于 `TYPE_CHECKING`，不存在 `models -> model_events -> models` 运行时循环。`src/agents/models.py::ModelAdapter`、`StreamingModelAdapter`
- [FACT] `supports_streaming_model()` 只检查 callable `stream_response`；`supports_model_adapter()` 接受 stream、sync `get_response` 或 legacy `decide` 任一种能力。生产 `_run_model_turn_impl()` 现在把三类模型都转换成 `ModelRequest`，并由 `_run_model_turn_async()` 调用唯一 `run_model_request()`；legacy `decide` 只在成功后保留原有“不写伪造 raw response”的兼容结果。`src/agents/models.py::supports_streaming_model`、`supports_model_adapter`、`src/agents/model_turn.py::_run_model_turn_impl`、`_run_model_turn_async`
- [FACT] `OpenAIResponsesModel.stream_response()` 与 `get_response_for_request()` 复用同一个请求 builder 和分项 `openai.Timeout`；前者以 `stream=True` 建立事件流，后者以 `stream=False` 执行一次完整响应，两者的一次性 client 都固定 `max_retries=0`。未知非关键流事件只增加最近 attempt 的忽略计数，不保存原始对象或改变标准事件序列。只有请求模型名等于 adapter 默认模型时才复用 `previous_response_id`；备用模型使用完整输入，成功后清空 stored previous id 和待回传工具结果。`src/agents/models.py::_OpenAIEventMapper`、`OpenAIResponsesModel.stream_response`、`get_response_for_request`、`_openai_timeout`
- [FACT] 内部 `stream_model_response()` 优先原生 `stream_response`，否则由 `stream_sync_model_response()` 在线程中调用现有 `get_response`/legacy `decide`，并把完整 `ModelResponse` 展开为可重新聚合的事件；转发入口退出时会关闭可关闭的底层异步流。该统一入口未从包顶层公开，由独立 `run_model_request()` 消费。`src/agents/models.py::_model_response_to_events`、`stream_sync_model_response`、`stream_model_response`、`src/agents/model_retry.py::_consume_stream_attempt`
- [FACT] 公共 `run_model_request()` 是当前唯一 retry 编排循环：它建立覆盖全部 attempts/backoff/fallback 的 total deadline，并在 attempt、每次事件读取和等待边界复用现有 `CancellationToken`。流创建明确不可用时可增加一个同模型 `non_stream` attempt；主模型 RATE_LIMIT/SERVER 因次数或累计等待预算耗尽时，可用同一 request id、新 attempt id 和配置的不同备用模型再发一次 stream attempt。两种 fallback 都在目标 attempt 真正开始前通知统一 reason、目标 attempt id、from/to model 和 from/to mode；失败立即结束，transport/auth/quota/context/invalid/protocol/overflow/cancel 不会换模型。生产 `model_turn` 已调用该执行器；`run_loop` 不读取供应商事件或 notice 载荷。`src/agents/model_retry.py::RetryPolicy.decide`、`run_model_request`、`src/agents/model_turn.py::_emit_model_attempt_notice`
- [FACT] 包顶层公开模型 adapter 所需的请求/事件/错误，以及 `RetryAction`、`RetryDecision`、`RetryPolicy`、attempt notice/record、`ModelCancelledError`、execution/error 和 `run_model_request`；Retry-After、delay、id、等待、流消费与 accumulator/buffer helper 保持内部。`src/agents/__init__.py::__all__`

## 运行状态、提交边界与模型观测

- [FACT] 失败 attempt 的正文和工具参数只存在于单 attempt 聚合器；只有 `run_model_request()` 返回成功 `ModelExecution` 后，`run_model_turn()` 才把最终 `ModelResponse` 写入 `RunState`，之后 `run_loop` 才建立 execution plan。因此失败或取消 attempt 的部分工具参数不会进入 handler。`src/agents/model_retry.py::_consume_stream_attempt`、`src/agents/model_turn.py::_run_model_turn_impl`、`src/agents/run_loop.py::_run_agent_loop_impl`
- [FACT] `RunState` 和 `RunResult` 保存全部 `ModelAttemptRecord` 与最后模型终止原因；成功为 `completed`，最终模型错误使用稳定 `ModelErrorKind.value`，无法分类时为 `unknown`，取消为 `cancelled`。模型取消通过兼容 `RunCancelledError` 的 `ModelCancelledError` 携带取消前已经发生的 records；发送前仍为空，读流中为 cancelled attempt，退避中保留 failed attempt，提交前取消可保留 succeeded attempt。`src/agents/model_retry.py::ModelCancelledError`、`src/agents/run_state.py::RunState.record_model_execution`、`record_model_execution_error`、`record_model_cancelled`、`src/agents/result.py::RunResultBase`
- [FACT] `RunResult.to_state()` 当前写 snapshot schema v4，保存 `model_attempts`、`last_model_terminal_reason`、成对的 `task_id/task_revision` 与模型显式导出的可选私有状态；reader 接受 legacy、v1、v2、v3、v4，v2–v4 恢复模型字段，v3/v4 恢复 task link，只有 v4 恢复私有模型状态，未知版本报错。`src/agents/run_state.py::RUN_STATE_SNAPSHOT_SCHEMA_VERSION`、`run_state_snapshot_from_dict`、`src/agents/result.py::RunResultBase.to_state`
- [FACT] 每个 model span 保存 request/final attempt identity、attempt/retry 数量、最终 model/mode、response id、usage、TTFT、attempt 耗时、retry 等待和 terminal reason；保留两种 fallback 布尔标记，并从相邻 `ModelAttemptRecord` 推导一次实际 fallback 的 reason、目标 attempt id、from/to model 与 from/to mode。未发生时六个定位字段均为 None。这些是现有 tracing data，不是新的 metrics 服务。`src/agents/model_turn.py::_record_model_execution_span`

## 事件与单 run 控制状态

- [FACT] `src/agents/events.py` 已包含不可变事件、稳定字典序列化、连续 sequence 和唯一 terminal reducer；模型 attempt 另有 started、retry scheduled、fallback 三种事件。`ModelFallbackEvent` 兼容旧构造，并可携带 from/to model、from/to mode、统一 reason 与目标 attempt id；同模型 stream→non_stream 也使用该既有事件类型。`src/agents/events.py::ModelFallbackEvent`、`event_to_dict`、`RunEventReducer`
- [FACT] `src/agents/event_compat.py` 只执行 `RunItem -> RunEvent` 单向适配，旧 `RunItem` 仍供 result、snapshot 与 trajectory 使用。`src/agents/event_compat.py::emit_run_item_event`
- [FACT] `src/agents/run_control.py` 已提供线程可见的 `CancellationToken`、单 run `RunController`、steer FIFO、有界 `_EventChannel` 和内部 `_RunRuntime`；factory 不缓存或注册 runtime。`src/agents/run_control.py::CancellationToken`、`RunController`、`_EventChannel`、`_create_run_runtime`
- [FACT] `RunConfig` 除事件容量、取消宽限期和成对的 `task_store/task_id` 外，还可携带不可变 `context_chunks` 元组；默认空元组保持旧运行行为。`_create_run_context()` 原样复制它，`build_turn_context()` 在既有上下文后追加并继续按 priority 排序。`src/agents/run_config.py::RunConfig.__post_init__`、`src/agents/run_loop.py::_create_run_context`、`src/agents/context_chunks.py::build_turn_context`
- [FACT] 包顶层公开 `RunStream`、事件类型/错误/序列化函数与 `CancellationToken`、`RunController`、`SteerMessage`；`_EventChannel`、`_RunRuntime`、`_LoopEventBridge`、notice mapper、factory、cancel wakeup 和 metrics snapshot 保持内部。`src/agents/__init__.py::__all__`、`src/agents/model_turn.py::_emit_model_attempt_notice`
- [FACT] `Agent` 公开 fresh sync、async 和 stream 入口；`Runner` 额外公开对应三种 resume 入口。六条 Runner 路径最终只运行同一个 `_run_agent_loop_with_events()` producer。async 入口委托公开 `SyncRunCollector`，同步入口在活动 event loop 中快速报错并提示使用对应 async 方法。`src/agents/agent.py::Agent.run_stream`、`Agent.run_async`、`src/agents/runner.py::SyncRunCollector`、`Runner`
- [FACT] `RunStream` 是惰性、单消费者异步迭代对象，公开只读 controller，并以 `aclose()`/异步上下文管理器完成提前退出清理。`src/agents/runner.py::RunStream`
- [FACT] steer 在上一 turn 完成、取消与 limit 检查之后、下一次模型输入之前按 FIFO 写入 memory；未应用 steer 在 terminal 时清空。`src/agents/run_loop.py::_apply_pending_steers`、`src/agents/run_control.py::RunController._finish`
- [FACT] `event_stream` span 保存最大 publish latency、最大 queue depth 和可选 cancel-to-terminal 秒数；统计按 runtime 隔离，tracing disabled 不通知 processor。`src/agents/runner.py::RunStream._produce`、`src/agents/run_control.py::_RunRuntime.metrics_snapshot`

## M03 长期记忆、结构化提取与提交边界

```text
ChatService.send_message
  -> Runner.run_async(chat Agent + buffered PostgresSession)
  -> commit user/tool/assistant batch
  -> MemorySource from committed user/assistant ids
  -> MemoryExtractionService.extract
       -> Runner.run_async(extraction Agent, no SessionLike)
  -> asyncio.to_thread(save_candidates)
  -> MemoryStore.create | supersede
  -> unchanged ChatReply
```

- [FACT] 003 创建长期记忆、来源复合外键、scope-aware 精确 fingerprint 唯一索引与单行 epoch；004 追加显式 `supersedes_id` 关系。数据库约束拒绝未知枚举、非法 confidence、空正文、坏 fingerprint、悬空/跨会话来源和自我替代；`MemoryStore.supersede()` 另外拒绝跨 scope 或跨 conversation 替代。`src/agents/chat_backend/migrations/003_long_term_memories.sql`、`004_memory_supersedes.sql`、`src/agents/chat_backend/memory.py::MemoryStore.supersede`
- [FACT] `MemoryStore` 复用现有同步 `Database.connection()/transaction()`；支持 get、稳定游标分页、create-or-existing、update、disable、delete 与原子 supersede。只有真实状态变化增加 epoch，精确重复和失败事务不增加。`src/agents/chat_backend/memory.py::MemoryStore`
- [FACT] 提取 Agent 仍使用现有 `Runner.run_async()`，只接收本轮 user/assistant 正文，没有 Session、工具或 handoff；结构化字典在落库前还要经过候选数量、字段、长度、UUID、规范化和明显凭据检查。`src/agents/chat_backend/memory_extraction.py::create_memory_extraction_agent`、`MemoryExtractionService.extract`、`parse_memory_candidates`
- [FACT] 聊天消息提交成功后才构造可信 `MemorySource` 并尝试提取；普通提取/保存错误只记录消息 UUID 与异常类型，保留原 `ChatReply`，`CancelledError` 继续传播。`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] `agents.chat_backend` 公开稳定记忆值、Store、领域异常、`MemoryCandidate`、`ExtractionReport`、提取服务和 Agent 构造函数；规范化、行转换、fingerprint、schema 解析、输入拼接和明显凭据模式不从包根导出。`src/agents/chat_backend/__init__.py::__all__`
- [FACT] M03 完成时只做 SHA-256 规范化后的精确去重和有限明显凭据拒绝；当时没有语义去重、embedding、检索或提示注入。当前能力以以下 M04 章节为准。`src/agents/chat_backend/memory.py`、`src/agents/chat_backend/memory_extraction.py`

## M04 记忆检索、知识索引与上下文注入

```text
memory: user query -> keyword + embedding/vector -> RRF -> budget -> ContextChunk
       -> ChatService -> RunConfig.context_chunks -> existing Runner

knowledge: source -> deterministic chunks -> batch embedding -> atomic replace
         -> keyword + vector -> RRF -> budget -> ContextChunk
         -> explicit RunConfig -> existing Runner
```

- [FACT] `EmbeddingClient.embed()` 冻结批量数量、顺序、1024 维和有限数契约；OpenAI 实现只发一次批量请求并按 response index 复原顺序。应用代码不依赖 Python `pgvector` 包，向量通过参数化字符串加 `%s::vector` 交给 PostgreSQL。`src/agents/chat_backend/embedding.py::EmbeddingClient`、`OpenAIEmbeddingClient.embed`、`embedding_vector_literal`
- [FACT] 005 为记忆增加三项同空同有的向量状态；backfill 在事务外请求 embedding，保存时以 active 状态和 fingerprint 重新核对整批。记忆关键词/向量查询先过滤 active、scope、conversation 和 embedding model；提取服务的同 scope、同 kind、top-1 相似度 `>= 0.95` 只报告 duplicate，失败时仍走原 SHA-256 精确去重。`src/agents/chat_backend/migrations/005_memory_embeddings.sql`、`src/agents/chat_backend/memory.py::embed_missing_memories`、`MemoryStore.search_keyword`、`search_vector`、`src/agents/chat_backend/memory_extraction.py::MemoryExtractionService`
- [FACT] `reciprocal_rank_fusion()` 只融合名次，不混算 ILIKE 与 cosine 数值；记忆检索在完整条目预算内生成 priority 47 的 user-role chunk。`ChatService` 先确认 conversation，再在线程中检索，并用 `dataclasses.replace()` 同时保留基础配置、外部 chunks 与本轮 Session；检索正文不写入 `SessionLike` 或消息表。`src/agents/chat_backend/retrieval.py::reciprocal_rank_fusion`、`MemoryRetrievalService.retrieve`、`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] 006 保存知识来源、分块和单行 epoch；`split_knowledge_text()` 用标准库给出连续 ordinal 与原文 `[start,end)` 范围。`KnowledgeIndexer` 在事务外分批生成全部向量，`replace_chunks()` 锁 source 后在一个事务里删除旧块、插入新块、标记 indexed 并增加一次 epoch。`src/agents/chat_backend/migrations/006_knowledge_sources_and_chunks.sql`、`src/agents/chat_backend/knowledge.py::split_knowledge_text`、`KnowledgeIndexer.reindex`、`KnowledgeStore.replace_chunks`
- [FACT] 知识搜索只读 indexed 且 embedding model 一致的分块，可选 source id 过滤；`KnowledgeRetrievalService` 复用同一 RRF 和完整条目预算，生成 priority 46、包含标题/chunk/ordinal/字符范围的 user-role chunk。它不会被 `ChatService` 自动调用，调用方必须显式放进 `RunConfig.context_chunks`。`src/agents/chat_backend/knowledge.py::KnowledgeStore.search_keyword`、`search_vector`、`src/agents/chat_backend/retrieval.py::KnowledgeRetrievalService.retrieve`
- [FACT] M04 没有实现最终记忆/知识四路统一融合、citation map、文章比例、reranker、query rewrite、ANN、外部向量库、自动知识采集或后台 reindex；这些仍属于后续模块。`src/agents/chat_backend/retrieval.py`、`http.py`
- [FACT] M05 新增公开 `HybridRetrievalService`：先查记忆/知识关键词，只生成一次 query embedding，再分别尝试两路向量查询，用 `memory:id` / `knowledge:chunk_id` identity 交给现有 RRF，最后只调用一次 `_build_bundle()`。Embedding 整体失败时保留关键词；单路向量的已知校验或 PostgreSQL 错误只丢弃该路；关键词错误和编程错误继续暴露。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`
- [FACT] M05 新增公开纯函数 `parse_citations(answer, citations)`，并已接入 `ChatService/HTTP`：标准库正则只接受本轮白名单中的严格 marker，按回答首次出现顺序去重；HTTP 只输出 Citation 的五个安全字段。`create_app()` 已默认组合 `KnowledgeStore` 与统一检索，旧独立 Retrieval Service 仍可由其他调用方直接使用。`src/agents/chat_backend/retrieval.py::parse_citations`、`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::chat_reply_response`、`create_app`
- [FACT] M06 用 007 为既有会话补上非空 `title` 和 `active/archived` 状态；会话、记忆和知识列表共享 `(created_at,id)` 排他游标，消息历史保留既有 `before_sequence`。归档会话在 `ChatService.send_message()` 中于检索和 Runner 前返回 `ConversationArchivedError`；删除会话由现有外键级联删除消息及其来源记忆。`src/agents/chat_backend/migrations/007_conversation_management.sql`、`src/agents/chat_backend/conversation.py::ConversationStore.list`、`delete`、`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] M06 HTTP 仍由一个 `create_app()` 组合：可选 embedding client 同时启用统一检索和同步 `KnowledgeIndexer`；缺失时 reindex 返回稳定 `503 embedding_unavailable`。CORS 只允许配置中的本机 origin、GET/POST/PATCH/DELETE 和 Content-Type；`/health` 只报告 PostgreSQL。当前没有登录权限、独立前端、文件上传、后台任务、SSE、部署、监控或备份。`src/agents/chat_backend/http.py::create_app`、`reindex_knowledge`、`health`
- [FACT] M06 目标 Gate 为 `39 passed, 1 skipped, 182 subtests passed`；相关回归为 `286 passed, 1 skipped, 900 subtests passed`；显式真实 PostgreSQL/Uvicorn/TCP HTTP smoke 为 `3 passed`。全量为 `1275 passed, 1 skipped, 1425 subtests passed, 6 failed`，6 个失败仍是 5 个 Windows 中文路径示例加载问题和 1 个既有 repo-map 断言，没有新增失败。`compileall` 与 `pip check` 通过；真实 OpenAI 因未设置凭据而未运行。
- [FACT] M04 目标矩阵为 `162 passed, 246 subtests passed`；相关回归为 `89 passed, 26 skipped, 91 subtests passed`。全量为 `1215 passed, 26 skipped, 1220 subtests passed, 6 failed`，仍是 5 个 Windows 中文路径示例加载问题和 1 个既有 repo-map 断言。`compileall`、`pip check`、diff 与行尾空格检查通过。Docker daemon、PostgreSQL DSN、真实模型开关和 OpenAI 凭据均不可用，因此本轮真实 PostgreSQL 与真实 OpenAI smoke 明确未运行。

## 导航

- [模块边界与影响入口](MODULE_CARDS.md)
- [逐步运行流和异常路径](RUNTIME_FLOWS.md)
- [状态、协议、持久化和不变量](STATE_AND_CONTRACTS.md)
- [关键符号到流程的映射](SYMBOL_MAP.md)
- [后续增量维护规则](MAINTENANCE.md)
- [本次及后续分析记录](CHANGELOG.md)
- [MyAgent 功能升级历史](upgrade_history.md)
- [PlanningRun 模块边界](MODULE_CARDS.md#19-planningrun-持久化版本事务与恢复读取)
- [PlanningRun 完整事务流](RUNTIME_FLOWS.md#12-planningrun-版本事务执行边界与恢复读取)
- [PlanningRun 状态与唯一事实来源](STATE_AND_CONTRACTS.md#planningrun-持久化版本与状态契约)
- [PlanningRun 关键符号](SYMBOL_MAP.md)

## UNKNOWN / NEEDS_REVIEW

- [UNKNOWN] 源码未说明用户实际如何启动两个 CLI；未声明 package script。`src/agents/chat_cli.py::main`、`src/agents/coding_cli.py::main`
- [UNKNOWN] 未发现仓库内的 OpenAI API 凭据或自定义 base URL。`src/agents/models.py::OpenAIResponsesModel.__post_init__`
- [NEEDS_REVIEW] repo-map/symbol API 已在工作树出现，但未接入 `build_task_repo_context()` 的生产调用链。`src/agents/repo_map.py::rank_repo_map_candidates`、`src/agents/repo_context.py::build_task_repo_context`
- [NEEDS_REVIEW] `RunState.handoff_depth` 未见递增/限制调用点。`src/agents/run_state.py::RunState`
- [NEEDS_REVIEW] Chat/Coding CLI 仍调用同步 `Agent.run()`，尚未直接展示或消费公共事件流；这不影响 SDK 调用方使用 `Agent.run_stream()`。`src/agents/chat_runtime.py::ChatRuntime.run_turn`、`src/agents/coding_cli.py::run_coding_agent_cli`
- [NEEDS_REVIEW] child handoff 继续同步返回 child `RunResult`，child 内部事件不会合并进父 `RunStream`。`src/agents/run_steps.py::_execute_handoff_impl`
- [NEEDS_REVIEW] 公共 `RunEvent` 目前只投影 attempt 生命周期，不包含正文 token delta；Chat/Coding CLI 也仍未直接消费事件流，因此不能描述为 CLI 已逐 token 展示。`src/agents/model_turn.py::_emit_model_attempt_notice`、`src/agents/chat_runtime.py::ChatRuntime.run_turn`、`src/agents/coding_cli.py::run_coding_agent_cli`
- [NEEDS_REVIEW] TaskEvent 仍没有 Session writer，JsonSession 不保存或重建 TaskStore；CLI 没有 task view，SubAgent/handoff 没有任务图自动调度。当前 run binding 只关联调用方显式提供的既有节点。`src/agents/memory.py::JsonSession`、`src/agents/coding_cli.py::run_coding_agent_cli`、`src/agents/run_steps.py::_execute_handoff_impl`
