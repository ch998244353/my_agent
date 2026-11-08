# 运行流程

Agent loop 与工具编排仍在同步 worker 中运行；每个模型回合在该 worker 内用 `asyncio.run()` 进入唯一异步模型执行器。公共 `RunStream`/`run_async` 通过 worker bridge 接到 asyncio event runtime，`run_sync` 再通过标准库桥复用同一 producer。证据使用 `路径::符号`。

## 1. 启动与配置

**入口**：[FACT] 可直接执行入口为 `src/agents/chat_cli.py::main` 与 `src/agents/coding_cli.py::main`；`pyproject.toml` 未声明 console-script。

1. [FACT] Chat CLI 创建 `ChatRuntime`，其 `run_turn()` 转入 `run_chat_turn()`。`src/agents/chat_cli.py::run_chat_cli`、`src/agents/chat_runtime.py::ChatRuntime.run_turn`、`src/agents/chat.py::run_chat_turn`
2. [FACT] Coding CLI 解析参数，构建 manifest/profile/model，调用 `build_coding_agent()`，再调用 `Agent.run()`。`src/agents/coding_cli.py::run_coding_agent_cli`、`build_coding_cli_setup`、`src/agents/coding_agent.py::build_coding_agent`
3. [FACT] 两条 CLI fresh 路径仍调用 `Agent.run()`；同步入口执行 `asyncio.run(Runner.run_async())`，`run_async()` 再让 `SyncRunCollector` 完整消费一个 `RunStream` 并返回 producer 原 `RunResult`。`src/agents/agent.py::Agent.run`、`src/agents/runner.py::Runner.run_sync`、`run_async`、`SyncRunCollector.collect`
4. [FACT] Coding resume 读取 envelope、重建 setup/`RunState`，然后进入 `resume_agent_loop()`。`src/agents/coding_cli.py::_run_resumed_coding_agent_cli`、`src/agents/run_state.py::RunState.from_snapshot`

SDK 调用方还可直接使用：

```text
Agent.run_stream / Runner.run_stream -> RunStream -> ordered events -> terminal
Agent.run_async  / Runner.run_async  -> SyncRunCollector consumes same RunStream -> RunResult
Agent.run        / Runner.run_sync   -> asyncio.run(run_async) -> RunResult
```

[FACT] `run_sync()` 在已有活动 event loop 的线程中不创建嵌套 loop，而是抛 `RuntimeError` 并提示使用 `await run_async()`。`src/agents/runner.py::Runner.run_sync`

**异常/结果**：[FACT] Coding CLI 在 setup/run/state/trajectory 异常时写 stderr 并返回 `1`。`src/agents/coding_cli.py::run_coding_agent_cli`

### M01 Chat Backend 显式生命周期

[FACT] 当前没有 CLI 或 Agent loop 自动启动数据库；应用组合层需要按下面的顺序显式调用：

```text
AppSettings.from_env()
  -> Database(settings.postgres_dsn)
  -> database.open()
  -> MigrationRunner(database, migrations_path).upgrade()
  -> database.health() / database.connection() / database.transaction()
  -> database.close()
```

1. `AppSettings.from_env()` 先校验必填 DSN、embedding、端口、检索限制和本机 CORS；失败时抛 `SettingsError`，此时还没有连接外部服务。`src/agents/chat_backend/settings.py::AppSettings.from_env`
2. `Database.open()` 等到连接池可用；普通借用走 autocommit，需要多条 SQL 一起成功时改用 `transaction()`。`src/agents/chat_backend/database.py::Database.open`、`connection`、`transaction`
3. `MigrationRunner.upgrade()` 先建立迁移历史表，核对已执行版本的文件和校验值，再让每个新文件与对应历史记录在同一事务中提交。重复执行返回空元组。`src/agents/chat_backend/migration.py::MigrationRunner._read_history`、`upgrade`
4. `database.close()` 由创建连接池的调用方在应用结束时执行；迁移器和健康检查不替调用方管理整个应用生命周期。`src/agents/chat_backend/database.py::Database.close`

[FACT] 这条基础设施流程当前与 Agent 主循环分离，没有修改 `_run_agent_loop_impl -> run_model_turn -> _run_model_turn_impl -> _run_model_turn_async -> run_model_request`。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/model_turn.py::_run_model_turn_async`

### M02 HTTP Chat 合同与可重启应用

[FACT] M02 当时由 `create_app()` 把已冻结的 HTTP 合同接到四条最小路由；M06 已在同一个 app 上增量扩展管理 API，当前流程见本章后面的 M06 小节。HTTP 一直只是既有 Store/Service/Runner 的最外层入口，不是第二条 Agent loop：

```text
JSON / query values
  -> Pydantic request validation
  -> health / create_conversation / send_message / list_messages
  -> ConversationStore / MessageStore / ChatService
  -> explicit response converter or registered exception handler
  -> Pydantic response / ErrorResponse JSON
```

1. `MessageCreate` 去除正文首尾空白并限制为 1..20,000 字符；`HistoryQuery` 固定默认页大小 50、上限 100，并要求游标为正数。`src/agents/chat_backend/http.py::MessageCreate`、`HistoryQuery`
2. 六个普通转换函数逐字段把领域值写入 HTTP schema，不使用 dataclass 自动序列化，所以会话内部 `next_message_sequence` 和检索正文不会出现在返回值。`src/agents/chat_backend/http.py::conversation_response`、`stored_message_response`、`message_page_response`、`citation_response`、`chat_reply_response`、`health_response`
3. 请求校验错误只公开字段位置和 Pydantic 错误类型；业务、数据库与未知异常统一经过 `_error()`，不会把原始输入、SQL、DSN 或供应商异常正文放入响应。未知异常和非数据库持久化不一致会写入标准日志。`src/agents/chat_backend/http.py::_validation_error`、`_http_error`、`_error`
4. `create_app()` 只构造关闭状态的 Database、MigrationRunner、五个数据库 Store、可选 MemoryExtractionService/HybridRetrievalService、ChatService、PlanningService 与 FastAPI app；外部 I/O 推迟到 lifespan。启动时 `database.open -> migration.upgrade` 后后台串行执行 `recover_running()`，不阻塞 HTTP 就绪；退出时先取消并等待应用级任务，再 finally 执行 `database.close`。`src/agents/chat_backend/http.py::create_app`、`lifespan`
5. `/health`、创建会话和历史分页是普通 `def` endpoint，使用同步 Store/Database；聊天是 `async def`，继续 `await ChatService.send_message()`。OpenAPI 明确列出请求、成功响应以及 404/422/500/503 模型。`src/agents/chat_backend/http.py::health`、`create_conversation`、`send_message`、`list_messages`
6. M02 初始 CORS 只允许配置中的本机 origin、GET/POST 和 Content-Type；M06 已把方法集合扩为 GET/POST/PATCH/DELETE，仍不允许凭据或通配符。`main()` 读取设置、建立请求级 Agent factory，并按 host/port 交给 Uvicorn。`src/agents/chat_backend/http.py::create_app`、`main`

### M03 提交后长期记忆提取

[FACT] 长期记忆不是 Session 历史的第二份副本；它只在当前聊天的 user/tool/assistant 批次已经成功提交后，引用真实消息行并尝试生成独立事实：

```text
HTTP message
  -> ChatService.send_message
  -> Runner.run_async(chat Agent + CommitOnSuccessSession)
  -> session.commit(final answer) -> StoredMessage batch
  -> MemorySource(committed conversation/user/assistant ids)
  -> MemoryExtractionService.extract(user text, assistant text)
       -> create_memory_extraction_agent
       -> Runner.run_async(extraction Agent, no SessionLike)
       -> parse_memory_candidates
  -> asyncio.to_thread(save_candidates)
       -> MemoryStore.create | supersede
       -> ExtractionReport
  -> original ChatReply
```

1. 003/004 迁移建立记忆、epoch 与显式替代关系；来源使用同会话复合外键，global 和 conversation 精确 fingerprint 分别由 PostgreSQL 唯一索引兜底。`src/agents/chat_backend/migrations/003_long_term_memories.sql`、`004_memory_supersedes.sql`
2. `MemoryStore` 的查询和单条写入复用现有 connection；update、disable、delete、supersede 用现有 transaction。只有真实变化增加 epoch，重复候选和失败事务不增加。`src/agents/chat_backend/memory.py::MemoryStore`
3. 提取 Agent 没有 Session、工具或 handoff，仍走唯一 `Runner.run_async -> RunStream -> run_model_request` 路径。结构化输出之后还有本地候选复查，不把 JSON Schema 当作唯一信任边界。`src/agents/chat_backend/memory_extraction.py::create_memory_extraction_agent`、`MemoryExtractionService.extract`
4. `ChatService` 从已提交行中要求恰好一个 user，并用最终 assistant 行构造 reply 与 `MemorySource`。没有配置提取服务时直接返回；没有候选时不启动保存线程。`src/agents/chat_backend/service.py::ChatService.send_message`
5. 普通提取或保存异常只记录 conversation/user/assistant UUID 与异常类型，聊天 reply 不回滚；`CancelledError` 保持原有异步取消语义。`src/agents/chat_backend/service.py::ChatService.send_message`
6. `create_app()` 与 `main()` 复用同一个 Database 创建 `MemoryStore`；只有传入 `memory_agent_factory` 才启用提取，HTTP 路由和 JSON 合同没有增加字段。`src/agents/chat_backend/http.py::create_app`、`main`

### M04 记忆自动检索与知识显式检索

```text
ChatService.send_message
  -> confirm conversation
  -> MemoryRetrievalService.retrieve in asyncio.to_thread
       -> MemoryStore.search_keyword
       -> EmbeddingClient.embed(query)
       -> MemoryStore.search_vector
       -> reciprocal_rank_fusion -> whole-item budget -> ContextChunk
  -> dataclasses.replace(base RunConfig, session, combined context_chunks)
  -> Runner.run_async
  -> prepare_turn_input -> build_turn_context
  -> model input only; SessionLike still saves the chat trajectory

KnowledgeIndexer.reindex
  -> _prepare: get source -> deterministic chunks -> batch embedding
  -> KnowledgeStore.replace_chunks: source lock -> atomic publish -> epoch

KnowledgeRetrievalService.retrieve
  -> indexed keyword/vector search -> RRF -> budget -> ContextChunk
  -> caller explicitly puts chunks in RunConfig -> same Runner path
```

1. [FACT] `EmbeddingClient` 的批量结果必须与输入数量和顺序一致，每项恰为 1024 个有限数；空批次不发请求。记忆 backfill 和知识索引都在数据库事务外完成网络请求。`src/agents/chat_backend/embedding.py::OpenAIEmbeddingClient.embed`、`src/agents/chat_backend/memory.py::embed_missing_memories`、`src/agents/chat_backend/knowledge.py::KnowledgeIndexer._prepare`
2. [FACT] 记忆关键词与向量查询只读 active 记录；conversation scope 只匹配当前会话，global 可跨会话，向量还要求 embedding model 一致。语义去重只在同 scope、同 kind 的 top-1 相似度达到 0.95 时报告 duplicate；任一向量步骤失败仍保留原精确 fingerprint 路径。`src/agents/chat_backend/memory.py::MemoryStore.search_keyword`、`search_vector`、`find_nearest_active`、`src/agents/chat_backend/memory_extraction.py::MemoryExtractionService.save_candidates`
3. [FACT] `MemoryRetrievalService` 先执行关键词查询，再尝试一次 query embedding 与向量查询；向量路径的契约、校验或 psycopg 失败退回关键词结果，关键词数据库错误继续上抛。RRF 只使用两路名次，预算只保留完整条目。`src/agents/chat_backend/retrieval.py::MemoryRetrievalService.retrieve`、`reciprocal_rank_fusion`、`_select_with_budget`
4. [FACT] `ChatService` 只自动组合记忆检索。未知 conversation 在检索前失败；检索完成后同时保留基础 config 的字段和外部 chunks，并追加本轮记忆 chunks。检索内容不交给 `CommitOnSuccessSession.add_items()`，因此不会进入消息表。`src/agents/chat_backend/service.py::ChatService.send_message`
5. [FACT] 知识来源先以 pending 保存；`KnowledgeIndexer` 全部准备成功后才调用 `replace_chunks()`。发布事务锁 source，重新核对 draft 的原文范围，然后整体替换 chunks、写 indexed/model/indexed_at 并只增加一次 epoch。`src/agents/chat_backend/knowledge.py::KnowledgeStore.create_source`、`KnowledgeIndexer.reindex`、`KnowledgeStore.replace_chunks`
6. [FACT] `KnowledgeRetrievalService` 只查询 indexed 且模型一致的 chunks，可选 source id 过滤，并把标题、chunk id、ordinal 与字符范围写入 priority 46 的 user-role chunk。它不会被 `ChatService` 自动调用；调用方必须显式经 `RunConfig.context_chunks` 进入 Runner。`src/agents/chat_backend/knowledge.py::KnowledgeStore.search_keyword`、`search_vector`、`src/agents/chat_backend/retrieval.py::KnowledgeRetrievalService.retrieve`

### M05 Lesson 02 四路统一检索

```text
HybridRetrievalService.retrieve(text, conversation_id, source_ids)
  -> memory keyword -> knowledge keyword（空 source_ids 时跳过知识）
  -> one query embedding
  -> memory vector / knowledge vector（两路独立降级）
  -> existing RRF with source-prefixed identity
  -> _build_bundle -> one shared ContextChunk + allowed Citations
```

1. [FACT] `HybridRetrievalService` 先规范化问题与知识范围，关键词查询按记忆后知识的顺序执行；任一关键词 Store 错误直接结束本次检索，不会误装成“没有命中”。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`
2. [FACT] 两路关键词完成后只调用一次 `EmbeddingClient.embed((query,))`。请求失败或返回数量不为一时，两个向量路径都跳过；成功时，两类 Store 复用同一查询向量。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`
3. [FACT] 两个向量 Store 各自捕获预期校验或 PostgreSQL 错误，一路失败不影响另一路；`TypeError` 等编程错误继续暴露。四路命中转换成统一安全字段后，用来源前缀 UUID 去重并复用现有 RRF 与唯一 `_build_bundle()`。`src/agents/chat_backend/retrieval.py::_memory_retrieval_hit`、`_knowledge_retrieval_hit`、`HybridRetrievalService.retrieve`
4. [FACT] `ChatService` 已能直接注入 `HybridRetrievalService` 并把 Bundle 送进现有 Runner；Lesson 05 已让 `create_app()` 在 embedding client 存在时默认选择同一服务。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::create_app`

### M05 Lesson 03 本轮 Citation 白名单解析

```text
answer + current RetrievalBundle.citations
  -> strict [M positive integer] / [K positive integer] scan
  -> current marker whitelist intersection
  -> first-occurrence deduplication
  -> tuple of original Citation objects
```

1. [FACT] `parse_citations()` 先拒绝非字符串回答，并把传入 Citation tuple 建成 marker 映射；映射大小缩小时说明白名单 marker 重复，函数直接报错而不是覆盖其中一个来源。`src/agents/chat_backend/retrieval.py::parse_citations`
2. [FACT] 私有预编译正则只找到大写 M/K 加不以 0 开头正整数的完整方括号 marker。扫描按文本顺序进行；未知 marker、已经返回的 marker 和只有来源名称的自然语言不会产生 Citation。`src/agents/chat_backend/retrieval.py::_CITATION_MARKER_PATTERN`、`parse_citations`
3. [FACT] 返回对象直接取自本次传入的白名单；函数不创建 Citation、不读取数据库、不修改回答。Lesson 04 已把同次检索 Bundle 和成功回答连接起来。`tests/test_chat_backend_retrieval.py::CitationParserTests`、`src/agents/chat_backend/service.py::ChatService.send_message`

### M05 Lesson 04 ChatService 与 HTTP 可信引用

```text
HTTP message -> ChatService.send_message
  -> confirm conversation
  -> optional HybridRetrievalService.retrieve in asyncio.to_thread
       -> RetrievalBundle(context_chunks, allowed citations)
  -> dataclasses.replace(base RunConfig, session, combined context_chunks)
  -> one Runner.run_async -> final answer
  -> CommitOnSuccessSession.commit -> stored messages
  -> parse_citations(stored final answer, same request whitelist)
  -> ChatReply(citations)
  -> chat_reply_response -> explicit CitationResponse fields
```

1. [FACT] `ChatService.__init__()` 只在旧参数末尾追加可选统一检索服务；旧记忆检索与统一检索同时存在会立即失败，避免重复查询和重复上下文。旧四参数 `ChatReply` 仍因默认空 tuple 可用。`src/agents/chat_backend/service.py::ChatReply`、`ChatService.__init__`
2. [FACT] 统一检索和旧记忆检索都在事件循环外执行；Bundle 的 chunks 只进入本轮 `RunConfig.context_chunks`。空 Bundle、没有检索或旧路径仍进入同一个 Runner；检索失败在 Runner/提交前转为安全持久化错误，取消原样传播。`src/agents/chat_backend/service.py::ChatService.send_message`
3. [FACT] Citation 解析发生在 Runner 产生 final、消息提交成功并核对最终数据库 assistant 行之后，白名单来自同一次统一检索。之后才构造 ChatReply，再执行既有 best-effort 记忆提取；检索 ContextChunk 从未交给 Session。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession`
4. [FACT] HTTP 使用 `CitationResponse` 与 `citation_response()` 逐字段复制 marker、来源类型、条目 UUID、可选来源 UUID 和标题；回复始终包含 citations 数组，不返回检索正文。`create_app()` 本课仍组合旧 `MemoryRetrievalService`，统一服务的默认应用接入留给新 Lesson 05。`src/agents/chat_backend/http.py::CitationResponse`、`citation_response`、`chat_reply_response`、`create_app`

### M05 Lesson 05 默认应用组合

```text
main -> optional OpenAIEmbeddingClient -> create_app
  -> one Database -> MemoryStore + KnowledgeStore
  -> embedding client present ? HybridRetrievalService : None
  -> ChatService(hybrid_retrieval=...)
  -> app.state -> unchanged database lifespan -> HTTP request
```

1. [FACT] `create_app()` 始终创建两个检索 Store；这些构造函数只保存同一个关闭状态 Database，不执行 SQL。`src/agents/chat_backend/http.py::create_app`、`src/agents/chat_backend/memory.py::MemoryStore.__init__`、`src/agents/chat_backend/knowledge.py::KnowledgeStore.__init__`
2. [FACT] embedding client 缺失时统一检索为 `None`；存在时用现有模型、top-k 和上下文预算创建 `HybridRetrievalService`，再以关键字参数注入 ChatService，旧记忆检索参数保持 `None`。`src/agents/chat_backend/http.py::create_app`、`src/agents/chat_backend/service.py::ChatService.__init__`
3. [FACT] `main()` 仍只由兼容配置 `memory_retrieval_enabled` 决定是否创建 embedding client；字段名未迁移，但开启后的应用行为已扩展为记忆与知识四路统一检索。lifespan、健康检查、路由与 HTTP schema 未改变。`src/agents/chat_backend/http.py::main`、`create_app`

### M05 Lesson 06 跨层 Gate 与最终证据

```text
HTTP POST message
  -> create_app assembled HybridRetrievalService
  -> memory/knowledge keyword + one embedding + memory/knowledge vector
  -> RRF -> RetrievalBundle -> Runner model input
  -> buffered user/tool/assistant commit
  -> current Bundle marker whitelist -> safe HTTP citations
```

1. [FACT] 跨层 Gate 使用真实 `create_app`、route、Hybrid、Runner、提交缓冲与 HTTP 转换，只把外部 PostgreSQL 查询和 embedding 返回值替换成可控值；它检查两个 Store 的四路调用、记忆 conversation id、一次 embedding、模型输入中的两类正文、Session 中无检索正文，以及未知/畸形/重复 marker 不产生额外 Citation。`tests/test_chat_backend_http.py::HttpAppTests.test_m05_gate_runs_hybrid_context_and_returns_current_citations`
2. [FACT] Gate 保留 Runner 既有的 `user -> tool_call -> tool_response -> assistant` 完整轨迹，只要求首条是当前 user、末条是最终 assistant；检索 `ContextChunk` 不属于该轨迹。`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession.commit`、`tests/test_chat_backend_http.py::HttpAppTests.test_m05_gate_runs_hybrid_context_and_returns_current_citations`
3. [FACT] CodeGraph 能直接证明 Hybrid 到 embedding/RRF/bundle、ChatService 到 Runner/引用解析、HTTP 回复到 Citation 转换；`asyncio.to_thread()`、函数内对象构造和两个 Store 的同名方法不能完全静态分辨，因此这些边由源码与 Gate 补核。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`、`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::create_app`、`chat_reply_response`

### M06 管理 API、故障矩阵与真实服务闭环

```text
HTTP JSON / query
  -> strict Pydantic request/query schema
  -> conversation routes -> existing ConversationStore
  -> message POST -> ChatService -> one Runner path
  -> message GET -> existing MessageStore.page
  -> memory routes -> existing MemoryStore
  -> knowledge routes -> existing KnowledgeStore / optional KnowledgeIndexer
  -> explicit safe response converter or error handler
```

1. [FACT] 007 给既有 conversation 增加非空 `title` 和 `active/archived`；`ConversationStore.list()` 按 `(created_at,id)` 倒序使用排他游标，`update()` 只允许 title/status，`delete()` 交给现有外键级联清除消息与来源记忆。`src/agents/chat_backend/migrations/007_conversation_management.sql`、`src/agents/chat_backend/conversation.py::ConversationStore.list`、`update`、`delete`
2. [FACT] `POST /conversations` 允许无 body；会话、记忆和知识列表都要求 `before_created_at/before_id` 同时给出，消息历史继续使用 `before_sequence`。严格请求基类拒绝额外字段，PATCH 转换函数拒绝没有任何允许字段的补丁。`src/agents/chat_backend/http.py::_StrictRequest`、`CursorQuery`、`ConversationPatchRequest`、`MemoryPatchRequest`
3. [FACT] 人工记忆创建把现有 conversation/user/assistant UUID 转成 `MemorySource`，由现有 Store 和数据库复合外键复查真实来源；精确重复返回同一记录和 `created=false`，不会增加 epoch。PATCH 不允许修改来源或 scope。`src/agents/chat_backend/http.py::memory_create_from_request`、`memory_write_response`、`src/agents/chat_backend/memory.py::MemoryStore.create`
4. [FACT] 知识 route 只接收 title、纯文本与 JSON metadata。embedding client 存在时，`create_app()` 用现有 Store 创建同步 `KnowledgeIndexer`；reindex 全部准备成功后才原子替换 chunks，失败保留旧索引。没有 embedding client 时返回稳定 `503 embedding_unavailable`，不伪造成功。`src/agents/chat_backend/http.py::KnowledgeCreateRequest`、`create_app`、`reindex_knowledge`、`src/agents/chat_backend/knowledge.py::KnowledgeIndexer.reindex`
5. [FACT] `ChatService.send_message()` 在线程中读取 conversation 后、检索与 Runner 前检查 archived；因此 HTTP 和直接 Python 调用都返回同一领域错误，HTTP 映射为 409。未知资源为 404，领域输入为 422，数据库暂不可用为 503，未知异常记录日志并返回固定 500。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::_http_error`
6. [FACT] 当前生产 app 有 19 条 route；`/health` 只调用 PostgreSQL health。CORS 只允许配置中的本机 origin、GET/POST/PATCH/DELETE 和 Content-Type，不允许 credentials 或通配符；OpenAPI 使用明确的请求、响应和错误 schema。`src/agents/chat_backend/http.py::create_app`、`health`
7. [FACT] 无外网场景用确定性模型/embedding 和真实 PostgreSQL/ASGI client 贯通重启、记忆管理、知识 reindex、四路检索、`ContextChunk`、回答与本轮 Citation；故障矩阵覆盖半轮提交、提取失败、未知引用、预算丢弃、迁移/批次回滚、错误 embedding、reindex 失败和 PostgreSQL 中断。`tests/test_chat_backend_http.py`、`tests/test_chat_backend_migration.py`、`tests/test_chat_backend_system.py`
8. [FACT] 显式真实服务 Gate 启动 Uvicorn 监听临时本机 TCP 端口，使用真实 PostgreSQL 和迁移完成创建/修改会话、多轮聊天与重启、记忆、知识重建、混合检索/引用、归档和删除；模型与 embedding 仍为确定性替身。真实 OpenAI 需要单独开关与凭据，本次未设置凭据，因此没有运行。`tests/test_chat_backend_system.py`

## 2. Fresh Agent loop

**入口**：[FACT] `src/agents/run_loop.py::run_agent_loop`

1. [FACT] 建立 trace、task、agent span，进入 `_run_agent_loop_impl()`。`src/agents/run_loop.py::run_agent_loop`
2. [FACT] fresh/resume 先解析可选 `RunConfig.task_store/task_id`：完整绑定通过 `TaskStore.get()` 得到节点 id/revision，bound resume 还必须匹配原 id；失败发生在 memory、context、lifecycle、模型、工具和 session 业务副作用前。`src/agents/run_loop.py::_resolve_task_binding`、`_run_agent_loop_impl`
3. [FACT] 校验后，fresh run 才向 `AgentMemory` 写 task，解析其他 `RunConfig` 覆盖值，创建 `RunContextWrapper` 和带可选 task link 的 `RunState`，触发 `on_agent_start`。`src/agents/run_loop.py::_run_agent_loop_impl`、`_create_run_context`、`src/agents/lifecycle.py::emit_agent_start`
4. [FACT] 输入 guardrail tripwire 会记录停止原因并立即构建结果；否则 fresh workspace run 尝试构建 repo context。`src/agents/run_loop.py::_run_input_guardrails`、`_run_agent_loop_impl`、`src/agents/repo_context.py::build_task_repo_context`
5. [FACT] 每轮先检查 max steps/turns，再构造 `TurnInput`，并将 session 历史前置。`src/agents/run_state.py::RunState.next_limit_reason`、`src/agents/model_turn.py::prepare_turn_input`、`src/agents/run_loop.py::_prepend_session_messages`
6. [FACT] 回合结果被分类为 final、普通工具、handoff、pending approval 或 stopped；普通 action 按模型返回顺序串行处理。`src/agents/turn_resolution.py::process_model_turn`、`src/agents/tool_planning.py::build_tool_execution_plan`、`src/agents/run_loop.py::_run_agent_loop_impl`
7. [FACT] 所有结果出口先在 `_build_result_and_save_session()` 重新读取绑定节点 revision，再调用 `build_run_result()` 并写回可选 session；最终读取失败时不会保存带旧 revision 的结果。`src/agents/run_loop.py::_build_result_and_save_session`、`src/agents/run_state.py::build_run_result`

**状态变化**：[FACT] `current_turn` 在模型调用前递增；工具、guardrail、最终输出和停止原因作为 `RunItem` 累积。`src/agents/run_state.py::RunState.record_model_turn`、`src/agents/run_recording.py::record_run_stopped`

**模型异常**：[FACT] loop 记录 `model_error`、触发 error hook 并停止。`src/agents/run_loop.py::_run_agent_loop_impl`

## 3. 模型调用与最终输出

**入口**：[FACT] `src/agents/model_turn.py::run_model_turn`

1. [FACT] `prepare_turn_input()` 通过 `build_turn_context()` 合并 instructions、memory、selected files、repo context 与 `RunContextWrapper.context_chunks`，按 priority 排序后读取可用工具 schema。`src/agents/model_turn.py::prepare_turn_input`、`src/agents/context_chunks.py::build_turn_context`、`src/agents/agent.py::Agent._tool_specs_for_model`
2. [FACT] `_run_model_turn_async()` 为本轮建立稳定 request id 与首个 attempt id，把 stream、sync `get_response` 和 legacy `decide` 模型都交给 `run_model_request()`；CodeGraph 的唯一生产 caller 正是该函数。`src/agents/model_turn.py::_run_model_turn_async`
3. [FACT] attempt notice 经内部 `_emit_model_attempt_notice()` 转为 started、retry scheduled 或 model fallback `RunEvent`；`run_loop` 不读取 `ModelEvent`、notice 或 OpenAI 原始载荷。`src/agents/model_turn.py::_emit_model_attempt_notice`
4. [FACT] 只有执行器返回成功 `ModelExecution` 后，回合层才写入最终 `ModelResponse` 和工具调用；失败/取消直接离开回合，因此部分工具参数不会进入 execution plan 或 handler。`src/agents/model_turn.py::_run_model_turn_impl`、`src/agents/run_loop.py::_run_agent_loop_impl`
5. [FACT] 非空模型文本且没有工具调用可成为 final output；输出 guardrail 会在最终模型文本、最终工具结果和 handoff 结果上执行。`src/agents/turn_resolution.py::process_model_turn`、`src/agents/run_loop.py::_run_output_guardrails`

生产模型容错路径：

```text
run_model_turn
  -> _run_model_turn_async
  -> run_model_request(model, base ModelRequest)
  -> RetryPolicy + total deadline + 稳定 request id
  -> token check -> attempt_started notice
  -> _consume_stream_attempt
       -> stream_model_response
       -> 每次 anext 都经 _await_with_limits
       -> token/timeout check -> ModelEventAccumulator.apply / finish
  -> 成功：succeeded record -> ModelExecution
  -> 普通异常：classify_model_error -> failed record
       -> STREAM_UNAVAILABLE + 显式开关
            -> 新 attempt_id / mode=non_stream
            -> fallback notice(reason/non-stream, from stream, to non_stream)
            -> run_non_stream_model_response（一次）
            -> 成功即返回；失败即结束
       -> RATE_LIMIT/SERVER + 主模型次数或累计等待预算耗尽
            -> 同 request id / 新 attempt_id / 配置的备用 model name
            -> fallback notice(reason/model, from/to model, stream→stream)
            -> stream attempt（一次）
            -> 成功即返回；失败即结束
       -> compute_retry_delay
       -> 无等待：ModelExecutionError
       -> 有等待：retry_scheduled notice
            -> _sleep_with_cancel -> token/deadline check -> 下一 attempt
  -> token 取消：关闭流，传播兼容 RunCancelledError 的 ModelCancelledError(attempts)
  -> 回到 run_model_turn
       -> 成功：RunState + model span + ModelTurnResult
       -> 最终失败：attempts + 稳定 terminal reason + model span error
       -> 取消：已有 attempts 写入 RunState/model span，terminal reason=cancelled
```

它消费的统一模型流路径：

```text
stream_model_response(model, ModelRequest)
  -> callable stream_response: 直接转发原生 ModelEvent
  -> 否则 stream_sync_model_response
       -> worker thread 调用 get_response 或 decide
       -> _model_response_to_events
       -> created -> content/tool/usage -> completed

OpenAIResponsesModel.stream_response
  -> 复用 build_response_request_kwargs(stream=True)
  -> 请求模型等于 adapter 默认模型：可复用 previous_response_id
  -> 请求模型不同：previous_response_id=None，发送完整输入
  -> openai.Timeout(connect/read/write/pool) + max_retries=0
  -> worker thread 创建/读取同步 SDK stream
  -> _OpenAIEventMapper：未知非关键事件只累加 ignored_event_count
  -> 标准事件进入 ModelEventAccumulator.apply -> yield
  -> buffer 越界：记录 max/attempted chars -> 清空临时内容 -> 抛出安全协议错误
  -> finish 成功后提交 adapter response 状态；备用模型成功则清空 stored previous id
  -> 正常、异常或 consumer aclose 都关闭 SDK stream，并保存最近 attempt 的忽略计数

OpenAIResponsesModel.get_response_for_request
  -> 复用 build_response_request_kwargs(stream=False)
  -> 同一 openai.Timeout + max_retries=0
  -> worker thread 只创建一次完整响应
  -> 校验、解析全部成功后提交 adapter response 状态
```

[FACT] 包根已公开 `run_model_request()`；生产 `model_turn.py` 导入并调用它，但仍不导入 OpenAI provider 事件。模型正文分片不转换成公共 `RunEvent`，只把 attempt 生命周期 notice 转成三种小型运行事件。`src/agents/__init__.py`、`src/agents/model_retry.py::run_model_request`、`src/agents/model_turn.py::_run_model_turn_async`、`_emit_model_attempt_notice`

[FACT] total deadline 覆盖事件创建/读取、backoff、一次 non-stream fallback 与一次 model fallback；token 在 attempt 前、每次异步操作完成前后和 sleep 前后检查。两种 fallback 都复用剩余时限和唯一 retry loop，且不会互相链式触发。asyncio 可及时停止等待并忽略迟到同步结果，但不能强杀已经运行的 SDK/custom adapter 线程或撤销其外部副作用。`src/agents/model_retry.py::_await_with_limits`、`_sleep_with_cancel`、`run_model_request`

**异常路径**：[FACT] 非 `ModelCallError` 会包装为该错误并写入 model span。`src/agents/model_turn.py::run_model_turn`

**状态与观测**：[FACT] 成功 execution、最终 `ModelExecutionError` 或 `ModelCancelledError` 的 attempts 会追加到 `RunState`；结果继续带到 `RunResult` 和 schema v4 snapshot。v3 在 v2 模型字段之外增加可选 task link，v4 再增加可选供应商私有 `model_state`。读流取消记录当前 cancelled attempt，退避取消保留失败记录和实际等待，完整响应后提交前取消保留成功 attempt。`src/agents/model_retry.py::run_model_request`、`src/agents/run_state.py::_model_attempt_to_state`、`src/agents/model_turn.py::_record_model_execution_span`

## 4. 工具注册、审批与执行

**入口**：[FACT] `src/agents/tool_planning.py::build_tool_execution_plan`

1. [FACT] Agent 将 registry schema 与 handoff schema 合并为模型可见工具。`src/agents/agent.py::Agent._tool_specs_for_model`
2. [FACT] plan 对普通工具查询审批状态/策略；需要审批的调用写入 `RunState.pending_tool_calls` 和 `new_items`。`src/agents/tool_planning.py::_planning_approval_decision_for`、`_record_planned_approval_required`
3. [FACT] 同一批只要有 pending approval，loop 停为 `tool_approval_required`，不执行同批其他普通工具。`src/agents/run_loop.py::_run_agent_loop_impl`
4. [FACT] 普通执行顺序为启用/审批检查、工具 guardrail、registry execute、输出 guardrail、记录 observation/模型 tool output、可选 verification。`src/agents/tool_execution.py::_execute_tool_call_impl`、`src/agents/run_recording.py::run_verification_after_tool`

Planning Tools 沿用同一条普通工具路径，没有 run loop 特例：

```text
调用方创建 TaskStore/root
  -> create_task_tools(store, root_task_id, owner_id)
  -> for tool in tools: ToolRegistry.register(tool)
  -> Agent._tool_specs_for_model
  -> ToolCall -> ToolRegistry.execute -> FunctionTool.execute
  -> create_task / update_task / list_tasks 闭包
  -> TaskStore 公共方法
  -> task_node_to_dict -> observation
```

- [FACT] create/update 成功时由 Store 写 `TaskEvent`；list 只读。Store、root 和 owner 都来自调用方，未注册时 schema 和任务副作用都不存在。`src/agents/task_tools.py::create_task_tools`、`src/agents/task_state.py::TaskStore.events`
- [FACT] scope、owner、CAS、状态与 result/error 错误不在工具层改写；`FunctionTool.execute()` 统一包装后，现有执行链记录 tool error 并允许下一模型回合继续。`src/agents/tools.py::FunctionTool.execute`、`src/agents/tool_execution.py::_execute_tool_call_impl`

**异常路径**：[FACT] 普通工具异常记录为 tool error 后继续；工具 guardrail 的 raise 路径会向上抛出。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/tool_execution.py::_execute_tool_call_impl`

**恢复**：[FACT] `resume_pending_tool_approvals()` 仅按状态顺序执行 approved/rejected pending 调用，遇到 pending/unknown 保留剩余调用并再次暂停。`src/agents/run_resume.py::resume_pending_tool_approvals`

## 5. Session、memory 与上下文

**入口**：[FACT] `src/agents/run_loop.py::_run_agent_loop_impl`

1. [FACT] loop 在模型前调用 `SessionLike.get_items()` 并前置消息，结束后把结果转换为 session items 写回。`src/agents/run_loop.py::_session_messages`、`_save_result_to_session`
2. [FACT] `AgentSession` 按 `SessionTurn` 保存历史，并可将旧 turn 压缩为 `MemorySummary` 系统消息。`src/agents/memory.py::AgentSession`、`MemoryCompressor.compact`
3. [FACT] `JsonSession` 在调用时 load-modify-save 本地 JSON；文件不存在表示空 session。`src/agents/memory.py::JsonSession._load`、`JsonSession._save`
4. [FACT] 调用方把 `PostgresSession` 放入 `RunConfig.session` 后，既有读取路径经 `get_items -> MessageStore.list` 取得 PostgreSQL 历史，既有保存路径经 `add_items -> MessageStore.add_batch` 写回；真实集成检查已用重新创建的连接池、Store、Session 和 Agent 验证第二轮模型收到第一轮完整 user/tool/final-answer/assistant 轨迹。`pop_item/clear_session` 是 Session 的显式操作，不由 loop 自动调用。`src/agents/chat_backend/postgres_session.py::PostgresSession`、`src/agents/run_loop.py::_session_messages`、`_save_result_to_session`、`tests/test_chat_backend_migration.py::MigrationRunnerPostgresTests.test_runner_recovers_postgres_history_with_new_dependencies`
5. [FACT] `_save_result_to_session()` 对所有正常返回的 `RunResult` 调用 `session_items_from_result()`；`RunResult.to_input_list()` 总是先加入本轮 user，并投影 tool call、tool result、tool error 和 final output，其中 `tool_error` 转成 `tool_response`。普通 `model_error` 结果直接使用持久 Session 时仍会保存 user 而没有 assistant，真正向上抛出的异常才不会进入该保存点。`src/agents/run_loop.py::_save_result_to_session`、`src/agents/result.py::RunResult.to_input_list`
6. [FACT] `ChatService.send_message()` 在线程中确认会话存在，可选在线程中取得旧记忆 chunks 或统一 Bundle，再创建 `PostgresSession` 与本轮 `CommitOnSuccessSession`。它用 `dataclasses.replace()` 同时替换 session、保留基础 config 的其他字段和外部 chunks、追加检索 chunks，然后把请求级 Agent 交给同一 `Runner.run_async()`。Runner 保存只进入消息缓冲，不包含上下文 chunks；Service 确认非空最终答案与缓冲末尾一致后才一次提交，并只从同次 Bundle 白名单解析已提交回答的引用。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession.commit`
7. [FACT] fresh workspace run 从 inventory、mentions、selected files 构造 repo context，随后以 context chunk 加入模型消息。`src/agents/repo_context.py::build_task_repo_context`、`src/agents/context_chunks.py::_repo_context_chunk`

**恢复边界**：[FACT] 通用 Agent resume 仍只恢复 `RunState` 快照；Planning 由 `PlanStore` 保存步骤边界和审批快照，没有完整调用栈 checkpoint。HTTP 应用 lifespan 现会在数据库迁移完成后创建后台任务，串行调用一次 `PlanningService.recover_running()`；它不阻塞 HTTP 启动，关闭应用时会取消并等待仍在运行的后台任务。独立使用 `ChatService` 时仍须由外层传入已可用的 Store 和请求级 Agent 工厂。`src/agents/run_state.py::RunState.from_snapshot`、`src/agents/chat_backend/planning_service.py::PlanningService.recover_running`、`src/agents/chat_backend/http.py::lifespan`

## 6. Handoff、guardrail、tracing 与并发

- **Handoff**：[FACT] model handoff tool 映射到目标 Agent；执行时同步 `target_agent.run(task)`，将 child result 写回父 state/memory。`src/agents/handoffs.py::handoff_target_for`、`src/agents/run_steps.py::_execute_handoff_impl`
- **Guardrail**：[FACT] agent input/output tripwire 停止 loop；工具 `reject_content` 产生 observation，`raise_exception` 抛专用异常。`src/agents/run_loop.py::_run_input_guardrails`、`_run_output_guardrails`、`src/agents/tool_guardrails.py::ToolGuardrailFunctionOutput`
- **Tracing**：[FACT] run/turn/model/tool/handoff/guardrail 分别创建 span；model span 写 attempt/retry/fallback、usage、TTFT 和 terminal reason，公共 stream 额外创建 `event_stream` span并写入运输指标。processor dispatcher 是进程级同步对象，并逐个隔离 processor 异常。`src/agents/run_loop.py::_run_trace`、`src/agents/model_turn.py::_record_model_execution_span`、`src/agents/runner.py::RunStream._produce`、`src/agents/tracing.py::SynchronousMultiTracingProcessor._forward`
- **并发/取消**：[FACT] 公共 async API 已存在，但单次 run 内的模型 loop 和工具批次仍串行；producer 用 `asyncio.to_thread()` 运行同步 loop，由线程桥把事件送回 asyncio runtime。同步工具 timeout 仍使用单 worker executor。`src/agents/run_control.py::_run_agent_loop_with_events`、`src/agents/tool_runtime.py::run_with_timeout`
- **[INFERENCE]** timeout 后 handler 仍可能继续产生副作用，因为 `future.cancel()` 不保证终止已运行线程。依据：`src/agents/tool_runtime.py::run_with_timeout`

## 7. 公共 RunStream、合作取消、steer 与终态

```text
Agent.run_stream / Runner.run_stream
  -> construct lazy RunStream + isolated runtime
  -> first iteration starts producer + cancel monitor
  -> event_stream span
  -> _run_agent_loop_with_events
       -> RunStartedEvent
       -> asyncio.to_thread(run_agent_loop, _event_bridge=bridge)
       -> turn/model/tool/approval/steer events
       -> RunResult + session save
       -> exactly one: completed | stopped | cancelled | failed
  -> span metrics + original RunResult

Runner.resume_stream(agent, run_state, config)
  -> construct the same lazy RunStream + isolated runtime
  -> first iteration starts the same producer + cancel monitor
  -> _run_agent_loop_with_events(run_state=run_state)
       -> RunStartedEvent
       -> asyncio.to_thread(resume_agent_loop, run_state, ...)
       -> the same event bridge and terminal classification
  -> span metrics + original RunResult

Runner.run_async / Runner.resume_async
  -> SyncRunCollector.collect(fresh or resume stream)
  -> consume every event without caching
  -> RunStream._wait_result -> producer original RunResult
```

- [FACT] `RunStream` 只能被一个异步迭代器占用；第一次读取才启动 producer。完整消费自动收口，提前 break 必须使用 `async with` 或显式 `aclose()` 以启动 drain 并等待后台 task。`src/agents/runner.py::RunStream.__aiter__`、`_start_once`、`aclose`
- [FACT] `_LoopEventBridge` 在同步 worker 中调用 `run_coroutine_threadsafe()`，把直接事件或 `RunItem` 适配事件交回 runtime 所属 event loop；sequence 和首个终态仍只由 reducer 决定。`src/agents/run_control.py::_LoopEventBridge`、`src/agents/events.py::RunEventReducer.emit`
- [FACT] 每轮准备模型输入前检查取消；模型调用前和返回后再次检查。后置检查发生在 llm end hook、`model_response` item 和 completed 事件之前，因此迟到响应不会进入 state。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/model_turn.py::_run_model_turn_impl`
- [FACT] 每个普通工具在 requested 前检查取消，执行函数在 started/handler 前及 handler 返回后再检查；后置检查发生在 result 解释、`tool_result` item、模型回写和 completed 事件之前。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/tool_execution.py::_execute_tool_call_impl`
- [FACT] `RunCancelledError` 在模型包装和 loop 通用错误分支之前单独处理，因此取消只产生 `RunCancelledEvent`，不会伪装成 model/tool error。`src/agents/model_turn.py::run_model_turn`、`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/run_control.py::_run_agent_loop_with_events`
- [FACT] loop 已记录的模型错误先通过 `record_model_error()` 发布 `RunErrorEvent`，producer 随后发布相邻 `RunFailedEvent` 并返回失败结果；意外 producer 异常发布同一事件对后重新抛出原异常。`src/agents/run_recording.py::record_model_error`、`src/agents/run_control.py::_run_agent_loop_with_events`
- [FACT] controller/reducer 只接受首个终态。completed、stopped、cancelled、failed 分支互斥，terminal 入队尝试后 controller 即永久 finished。`src/agents/run_control.py::_run_agent_loop_with_events`、`_RunRuntime._deliver`
- [FACT] `controller.steer()` 只同步排队；当前 turn 完成、取消/limit 检查通过后，loop 一次取走当前批次，按 FIFO 写为 user messages，再开始下一模型 turn。steer 不增加工具 step。`src/agents/run_control.py::RunController.steer`、`src/agents/run_loop.py::_apply_pending_steers`
- [FACT] `controller.cancel()` 唤醒 asyncio monitor；grace 内允许合作检查收口，超时取消 producer task。已开始的同步 handler/thread 不能被 `Task.cancel()` 强杀，迟到结果只会在后置检查处被丢弃。`src/agents/runner.py::RunStream._monitor_cancel`、`src/agents/run_control.py::_RunRuntime.shutdown_after_cancel`
- [FACT] `_RunRuntime._deliver()` 统计 emit 到成功入队的最长时间和最大队列深度；取消终态另统计首次 cancel 到 terminal 入队。三个值在 producer 结束时写入 `event_stream` span，每个 runtime 独立。`src/agents/run_control.py::_RunRuntime.metrics_snapshot`、`src/agents/runner.py::RunStream._produce`
- [FACT] `SyncRunCollector.collect()` 使用流本身的异步上下文、单消费者迭代和 `_wait_result()`；它不保存事件、不从 terminal event 重建结果，也不包装取消或 producer 异常。`src/agents/runner.py::SyncRunCollector.collect`
- [FACT] `RunStream` 只增加私有 `_run_state`；`Runner.resume_stream()` 在创建流前检查 `RunState`，真正 worker 启动仍发生在首次消费。`_run_agent_loop_with_events()` 只在 worker 调用点选择既有 fresh/resume 函数，事件队列、reducer、bridge、取消 monitor、collector 和终态分支没有复制。`src/agents/runner.py::RunStream`、`Runner.resume_stream`、`src/agents/run_control.py::_run_agent_loop_with_events`

公共边界：包顶层公开 stream、collector、事件、错误、转换函数和 controller，但不公开 `_wait_result`、channel/runtime/bridge/factory/metrics snapshot。CLI 尚未迁移为事件消费者；事件不持久化、不反序列化，child handoff 内部事件不合并进父 stream。`src/agents/__init__.py::__all__`、`src/agents/run_steps.py::_execute_handoff_impl`

## 8. RunResult、任务关联与 snapshot v4

```text
run_model_turn
  -> RunState.model_attempts / last_model_terminal_reason
  -> RunState.task_id / task_revision
  -> build_run_result
  -> RunResult.model_attempts / last_model_terminal_reason / task link
  -> RunResult.to_state
  -> RunStateSnapshot schema_version=4
  -> coding state JSON（仅调用方要求保存时）
```

- [FACT] writer 只输出 v4；reader 接受 legacy 无版本、v1、v2、v3、v4。v2–v4 恢复模型 attempts，v3/v4 恢复成对 task link，只有 v4 恢复可选 `model_state`；旧格式忽略其版本尚未定义的字段，未知版本拒绝。`src/agents/run_state.py::run_state_snapshot_to_dict`、`run_state_snapshot_from_dict`
- [FACT] snapshot 保存纯数据形式的 attempt 字段和两个可选任务标量，不保存模型、TaskStore、TaskNode、TaskEvent、事件流、controller 或 tracing 对象。`src/agents/run_state.py::_model_attempt_to_state`、`RunStateSnapshot`
- [FACT] 这是已有 pending approval 恢复面的向后兼容扩展，不是完整 checkpoint、事件历史或重放系统。`src/agents/result.py::RunResultBase.to_state`、`src/agents/run_state.py::RunState.from_snapshot`
- [FACT] bound v3/v4 只解析时不需要 Store；真正 `resume_agent_loop()` 时必须提供包含相同 task id 的 Store，并以 Store 当前 revision 更新同步点。legacy/v1/v2 或未绑定 v3/v4 可保持未绑定，也可在 resume 起点显式开始关联。v4 带 `model_state` 时必须由恢复用 Agent 的模型 adapter 明确支持 `restore_state()`，否则安全拒绝恢复。`src/agents/run_state.py::RunState.from_snapshot`、`src/agents/run_loop.py::_resolve_task_binding`

## 9. 独立 Task Graph 写入、journal 与 replay

```text
TaskStore.create / transition / add_dependency / mark_owner_missing
  -> 按现有规则预计算完整 TaskNode 批次
  -> _task_event_from_change
  -> _apply_event_batch
       -> 一份 working nodes + working seen events
       -> _reduce_task_event 顺序校验每条事件
       -> 全部成功后同时提交 nodes / seen / journal
  -> events() tuple
  -> task_event_to_dict
  -> 调用方拥有的 JSON 介质（MyAgent 当前不实现）
  -> task_event_from_dict
  -> replay_task_events
  -> 新的等价内存 TaskStore
```

- [FACT] 四个 Store 写入口和 replay 都经过 `_apply_event_batch()`；相同事件重复出现为 no-op，内容冲突、revision 断档、非法状态变化、坏引用、环和资源上限在真实提交前失败。`src/agents/task_state.py::TaskStore._apply_event_batch`
- [FACT] parent cancel、dependency block 和 owner missing 的事件顺序继续使用现有“主节点优先，再按 Store 创建顺序 BFS”规则，不按 task id 排序。`src/agents/task_state.py::TaskStore.transition`、`mark_owner_missing`
- [FACT] replay 输入必须已经是 `TaskEvent`；持久字典需要先经严格 `task_event_from_dict()` 解码。replay 不修复或重排事件。`src/agents/task_events.py::task_event_from_dict`、`replay_task_events`
- [FACT] Planning Tools 可把该 Store 显式接入第 4 节的普通工具链；`RunConfig` 还可把一次 run 关联到已有节点，RunState/Result 只保存标量 link。Agent 不自动创建 Store/root 或注册工具，Session、RunEvent 和 CLI writer 仍不保存 TaskEvent。`src/agents/task_tools.py::create_task_tools`、`src/agents/run_config.py::RunConfig`、`src/agents/task_events.py`

## 10. 计划定义、TaskStore 投影与恢复

```text
PlanStepSpec tuple
  -> PlanDefinition validates fields + DAG + final leaf coverage
  -> plan_definition_to_dict / plan_definition_from_dict (schema v1)
  -> render_plan_markdown (review only)
  -> PlanExecution.materialize(owner_id)
       -> stable topological step order
       -> new TaskStore
       -> TaskStore.create(TaskSpec, task_id=step_id)
  -> next_ready reads TaskStore.ready and chooses definition order

TaskStore.events
  -> task_event_to_dict -> caller-owned medium
  -> task_event_from_dict -> typed TaskEvent tuple
  -> PlanExecution.from_events
       -> replay_task_events
       -> exact step/task structure check
```

- [FACT] 计划定义允许依赖后面声明的步骤；稳定 Kahn 遍历只为满足 `TaskStore.create()` 的已存在依赖要求而决定创建顺序，并列节点仍按 definition 顺序。对外 Markdown 和 `next_ready()` 继续遵守 definition 顺序。`src/agents/planning.py::_topological_steps`、`PlanExecution.materialize`、`next_ready`
- [FACT] `materialize()` 先在内部新建 Store，全部节点创建成功后才返回 `PlanExecution`；调用方不能传入 Store，因此中途失败不会污染外部任务图。步骤没有额外 plan root，`step_id` 就是 `task_id`。`src/agents/planning.py::PlanExecution.materialize`
- [FACT] `from_events()` 只接受 typed events，复用 `replay_task_events()` 得到新 Store，再检查节点集合、summary、dependencies、owner 和无 parent。状态、revision、result 与 error 不复制到 planning 字段。`src/agents/planning.py::PlanExecution.from_events`、`_validate_execution_store`
- [FACT] planning/step Agent builder 只选择现有 read-only/edit-local profile；规划 Agent 可读取/search，步骤 Agent 可编辑/运行命令，二者都没有 Planning Tools。builder 自身不注入任务正文；`PlanningService._step_prompt()` 在批准后的普通步骤运行时传入该步 instruction、completion criteria、step id，以及已完成直接依赖的结果。依赖结果总预算为 20000 字符并按直接依赖平均截断，不扫描无关步骤。`src/agents/planning.py::build_planning_agent`、`build_plan_step_agent`、`src/agents/chat_backend/planning_service.py::_step_prompt`

## 11. 本地 Web 流式聊天与模型 Profile

```text
Desktop 启动 MyAgent.cmd
  -> scripts/start_myagent.ps1
  -> docker info
       -> unavailable: hidden Docker Desktop start -> wait <= 180s
  -> docker compose up -d --wait postgres
  -> GET /health
       -> ready: reuse existing my-agent-chat-http
       -> unavailable + port free: hidden my-agent-chat-http -> wait <= 60s
       -> unavailable + port occupied: report PID and stop startup
  -> open http://127.0.0.1:8000/

Browser provider/Base URL/API Key
  -> POST /model-profiles/models
  -> request-scoped draft or saved Profile snapshot
  -> OpenAI-compatible models.list() in a worker thread
  -> chat-capable model IDs only -> datalist/automatic selection

Browser POST /conversations/{id}/messages/stream
  -> stream_message emits start/status
  -> ChatService.send_message(on_event=queue.put)
  -> main.chat_agent resolves active Profile before build_chat_agent
  -> request-scoped active ModelProfile snapshot
  -> Runner.run_stream (one producer)
  -> _run_agent_loop_impl
  -> run_model_turn -> run_model_request
  -> ModelTextDeltaEvent/status -> SSE delta/status
  -> successful CommitOnSuccessSession.commit
  -> SSE done(final ChatReplyResponse)

browser disconnect / Stop
  -> task cancellation
  -> RunStream.controller.cancel("client_disconnected")
  -> unfinished turn is not committed
```

- [FACT] `delta.reset` 在每个 `(step_number, attempt_id)` 的首个文本分片为 true，前端据此清除失败 attempt 的临时文本；`done.content` 是数据库提交后的最终校准值。`src/agents/chat_backend/http.py::stream_message`、`src/agents/chat_backend/static/app.js::streamMessage`
- [FACT] `stream_message` 同步收集 `_run_status()` 的安全投影，回复提交后追加 `done` 并以助手消息 ID 写入 `run_trace`；历史接口和 SSE `done` 都返回该列表。浏览器为每条助手消息创建一个原生折叠区，生成中展开、完成后收起，刷新后从持久化历史恢复；旧消息不伪造轨迹。`src/agents/chat_backend/http.py::stream_message`、`src/agents/chat_backend/conversation.py::MessageStore.set_run_trace`、`src/agents/chat_backend/static/app.js::makeRunTrace`
- [FACT] 页面纵向滚动只发生在 `chat-stage`：工作区中间轨道允许收缩并保留 composer，DOM 顺序固定为 empty/messages/planning-panel/planning-final，所以结构化计划位于历史消息之后，completed 快照生成的简短最终回复又位于计划之后。历史加载、聊天 delta 与需要关注的 Planning revision 都直接把该容器定位到真实 `scrollHeight`；不使用 `scrollIntoView(start)` 或平滑滚动，内容高度变化时仍保持底部锚点。用户消息行反向排列并靠右，助手消息保持默认左向排列。`src/agents/chat_backend/static/index.html`、`src/agents/chat_backend/static/app.css`、`src/agents/chat_backend/static/app.js::loadMessages`、`scrollPlanningToLatest`、`revealPlanningUpdate`
- [FACT] Profile 激活是进程级全局选择，但 `agent_factory` 每次聊天开始时取得一次不可变 Profile 值并创建模型，因此运行中的请求不受后续切换影响。`src/agents/chat_backend/http.py::configured_agent_factory`、`src/agents/chat_backend/model_profiles.py::ModelProfileStore.active`
- [FACT] 前端不把 Profile 绑定到 conversation：左栏选择器只调用既有激活接口，新建任意数量会话时复用当前激活配置；切换后只影响随后开始的消息。右栏的 Profile 表单默认折叠，仅负责新增、修改、测试和删除配置。`src/agents/chat_backend/static/app.js::renderConversationProfile`、`activateProfile`、`src/agents/chat_backend/static/index.html`
- [FACT] 生产 `main()` 的 Chat factory 在 `build_chat_agent()` 之前解析激活 Profile；这避免默认 `OpenAIResponsesModel` 在第三方模型替换前因缺少 `OPENAI_API_KEY` 而初始化失败。Profile 不存在或 keyring 不可用时才保留原默认模型路径。`src/agents/chat_backend/http.py::main`、`chat_agent`、`src/agents/chat_runtime.py::build_chat_agent`
- [FACT] Responses Profile 使用原适配器；兼容 Profile 使用 Chat Completions 适配器。工具结果通过 adapter 的 per-request 对话轨迹回传；从序列化 RunState 换成新 Chat Completions 实例恢复审批时，公共 run loop 先把 pending calls 交给 adapter 幂等补回 assistant `tool_calls`，再执行工具并追加 `role=tool`，避免向厂商发送没有前置调用的孤立工具结果。Agent 有 `output_type` 时，两种流/非流入口共用的请求构造会增加 JSON Schema 系统提示和 `response_format=json_object`，普通文本请求不增加。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/models.py::OpenAIChatCompletionsModel.restore_pending_tool_calls`、`record_tool_output`、`build_chat_completion_request_kwargs`
- [FACT] Models API 不调用 Agent、Runner 或数据库；草稿 Key 只用于本次厂商请求，已保存 Profile 则从 keyring 取得不可变快照。失败返回安全分类提示，原始厂商异常不进入浏览器。`src/agents/chat_backend/http.py::list_model_profile_models`、`_profile_error_message`
- [FACT] 一键启动脚本只组合现有 Docker Compose、`my-agent-chat-http` 与 `/health`，不进入 HTTP/ChatService/Runner 业务链。`-NoBrowser` 只跳过最后的页面打开，供自动验收；脚本不提供 stop、`docker compose down`、进程强杀或数据卷删除。`scripts/start_myagent.ps1`、`compose.yaml`、`src/agents/chat_backend/http.py::health`

## 12. PlanningRun 版本事务、执行边界与恢复读取

创建草案和会话模式切换共用一笔事务：

```text
PlanStore.create_draft(conversation_id, objective, model_profile, manifest)
  -> validate Python inputs before database access
  -> transaction
       -> SELECT conversation status/mode FOR UPDATE
       -> reject archived / existing active planning run
       -> allocate MAX(version) + 1
       -> ModelProfile.public_dict + WorkspaceManifest.metadata
       -> INSERT drafting PlanningRun
       -> UPDATE Conversation.mode = plan
  -> strict row decode -> PlanningRun
```

- [FACT] 会话行锁让同一 conversation 的版本分配和 mode 切换串行；`planning_runs_active_conversation_key` 再拒绝并发活动行。唯一索引冲突只在确认约束名后转换成 `ActivePlanningRunError`，其他 PostgreSQL 唯一错误保持原类型。`src/agents/chat_backend/planning_store.py::PlanStore.create_draft`、`src/agents/chat_backend/migrations/009_planning_runs.sql`
- [FACT] 创建时只保存 Profile 公开投影和 Manifest metadata；这些字典不含 API key 或环境变量值。`src/agents/chat_backend/model_profiles.py::ModelProfile.public_dict`、`src/agents/workspace_manifest.py::WorkspaceManifest.metadata`

草案提交、批准与物化：

```text
submit_for_approval(run_id, expected_row_revision, definition)
  -> transaction -> SELECT PlanningRun FOR UPDATE -> CAS/status check
  -> plan_definition_to_dict -> awaiting_approval + row_revision+1

approve(run_id, expected_row_revision)
  -> transaction -> lock/CAS/status check
  -> PlanExecution.materialize(definition, owner_id=str(run_id))
  -> TaskStore.events -> task_event_to_dict
  -> running + complete initial journal + row_revision+1
```

- [FACT] 调用方不能把自己组装的初始事件交给 `approve()`；step/task id、依赖、owner 和初始 revision 只有 `PlanExecution.materialize()` 一条生成路径。`src/agents/chat_backend/planning_store.py::PlanStore.approve`

修订与取消：

```text
revise(old_run, expected_revision, feedback)
  -> transaction -> lock/CAS -> require awaiting_approval
  -> old status = superseded, row_revision+1
  -> INSERT version+1 drafting with supersedes_version + revision_note
  -> keep Conversation.mode = plan

cancel(active_run, expected_revision, reason)
  -> transaction -> lock/CAS
  -> optional PlanExecution.from_events(owner_id=str(run_id))
  -> cancel every non-terminal TaskNode through TaskStore.transition
  -> save append-only journal, clear snapshot/error, status=cancelled
  -> Conversation.mode = default
```

- [FACT] 修订的两行写入在任一步失败时一起回滚，不留下 superseded 旧版却没有新草案的半条版本链。取消保留已终态节点，并把其余节点通过现有 Store 规则转成 cancelled 后保存完整事件序列。`src/agents/chat_backend/planning_store.py::PlanStore.revise`、`cancel`

步骤边界、审批暂停和终态保存：

```text
PlanStore.save_execution(run_id, expected_revision, execution, status, ...)
  -> validate target-specific snapshot/error inputs
  -> transaction -> lock/CAS current PlanningRun
  -> require saved definition == execution.definition
  -> PlanExecution.from_events(all events, owner_id=str(run_id))
  -> require old stored events are exact prefix of new events
  -> validate task statuses and optional task link
  -> task_event_to_dict / optional run_state_snapshot_to_dict
  -> update PlanningRun + row_revision+1
  -> completed/failed also set Conversation.mode = default
```

- [FACT] `running` 允许两种保存点：零个 RUNNING 且仍有 pending/blocked 的步骤间边界，或恰一个已经持久化的 RUNNING 当前任务；两种形状都拒绝 FAILED 任务。`paused` 必须保留恰一个 RUNNING 任务、清空 snapshot 并保存非空安全错误。`awaiting_tool_approval` 同样恰有一个 RUNNING 任务，但 snapshot 的 `task_id/task_revision` 必须指向它并至少含一个 pending approval。`src/agents/chat_backend/planning_store.py::_validate_execution_state`、`PlanStore.save_execution`
- [FACT] `completed` 要求所有任务 completed；不可恢复的计划级 `failed` 要求可正常回放的任务全部 completed/cancelled，并保存非空 `last_error`，普通可重试失败改存 paused 而不产生 FAILED TaskEvent。取消仍使用独立 `cancel()`。`src/agents/chat_backend/planning_store.py::_validate_execution_state`、`PlanStore.save_execution`

新连接恢复只读取安全边界：

```text
new Database pool / connection
  -> PlanStore.list_recoverable_running(limit)
  -> rows WHERE status = running ORDER BY updated_at, id
  -> strict PlanningRun row decode
       -> PlanDefinition codec
       -> TaskEvent codec
       -> PlanExecution.from_events(owner_id=str(run_id))
  -> caller receives a boundary or persisted current task
```

- [FACT] 查询只返回 `running`：不返回需要用户决定是否重试的 `paused`，也不返回需要审批的 `awaiting_tool_approval` 或终态。即使保存了当前 RUNNING 任务，进程仍可能在外部副作用之后、完成事件保存之前退出，因此合同是 at-least-once，不是 exactly-once。`src/agents/chat_backend/planning_store.py::PlanStore.list_recoverable_running`、`save_execution`
- [FACT] `create_app()` 现在用同一个数据库实例创建 `PlanStore/PlanningService`，HTTP 草案、批准、工具审批、重试、修订、取消和 lifespan 恢复都调用这一个服务；步骤执行继续回到现有 Runner。`src/agents/chat_backend/http.py::create_app`、`src/agents/chat_backend/planning_service.py::PlanningService.draft`、`approve`、`recover_running`

## 13. PlanningService 多轮只读草案、严格串行执行与工具审批恢复

```text
PlanningService.draft(conversation_id, content, expected_revision?)
  -> PlanStore.get_active
  -> new: active ModelProfile + current WorkspaceManifest
       -> PlanStore.create_draft (freeze public snapshots)
     follow-up: frozen profile id + current key
       -> compare provider/protocol/base URL/model + manifest metadata
  -> model_for_profile -> build_planning_agent(output_type=PLANNING_DRAFT_SCHEMA)
       -> system instructions include persisted PlanningRun.objective
  -> PostgresSession + CommitOnSuccessSession
  -> existing Runner.run_async or one Runner.run_stream consumer
  -> _parse_draft_output(result.final_output: Mapping or JSON string)
       -> question: commit readable question; keep drafting
       -> plan: plan_definition_from_dict
            -> PlanStore.submit_for_approval CAS
            -> commit render_plan_markdown; awaiting_approval
```

```text
PlanningService.decide_tool_approval(run_id, row_revision, tool_name, call_id, decision)
  -> read awaiting_tool_approval PlanningRun and compare row revision
  -> PlanExecution.from_events -> require exactly one RUNNING ordinary task
  -> rebuild frozen step Agent + runtime workspace context
  -> RunState.from_snapshot(v4) -> restore private model state and verify exact task id/revision
  -> approve/reject exact (tool_name, call_id)
  -> PlanStore.save_execution(status=running, run_state=None) CAS
  -> Runner.resume_stream(same RunState)
       -> compatible Chat Completions adapter restores pending assistant tool_calls
          and provider-required reasoning_content
       -> RunState restores the whole paused model-turn tool batch in original order
       -> resume_pending_tool_approvals executes decided calls and rechecks safe siblings
       -> record_tool_output appends matching role=tool messages
       -> pending approval: save new v4 snapshot
       -> valid final text: complete current task -> continue _drive_running
       -> model/tool/invalid result: keep task RUNNING -> paused
```

- [FACT] Planning 只负责重建冻结 Agent 和 RunState；Chat Completions 的消息配对与供应商私有状态恢复位于共享 run loop/adapter 边界，CLI 和其他 `resume_agent_loop()` 调用也沿用。`RunState.from_snapshot()` 先调用 adapter 的 `restore_state()`，再由 `restore_pending_tool_calls()` 以 call id 补齐缺失的 assistant 调用；DeepSeek thinking 模式要求的 `reasoning_content` 随私有工具交换回传，但不转换为正文事件或公开结果。暂停轮如果同时包含安全调用和待批准调用，`_paused_turn_tool_calls()` 从最后一条模型响应恢复完整原顺序批次；`resume_pending_tool_approvals()` 对 Context 中仍为 unknown 的调用重新使用现有工具审批规则，只有当前仍安全才执行，仍需审批或判断失败就继续暂停。它不持久化第二份审批状态。`src/agents/run_state.py::_paused_turn_tool_calls`、`RunState.from_snapshot`、`src/agents/run_resume.py::resume_pending_tool_approvals`、`src/agents/models.py::OpenAIChatCompletionsModel.restore_state`

- [FACT] 同步 Store/keyring/manifest 读取通过 `asyncio.to_thread()` 离开事件循环；Runner 仍使用唯一 producer 和结果，不从 `RunEvent` 重建最终输出。进度回调只把原事件补上 planning run id，草案阶段没有 task id。`src/agents/chat_backend/planning_service.py::PlanningService.draft`、`PlanningProgressEvent`
- [FACT] Runner 先把本轮 user 与结构化 final 暂存在 Session；服务用 `str(result.final_output)` 核对缓冲末条，再只替换最后一条 assistant。question 保存普通追问，plan 保存现有 renderer 生成的 Markdown，结构化字典不进入消息历史。浏览器取得同一计划的 PlanningSnapshot 后会隐藏这条 Markdown 消息，只显示一份结构化计划；持久化历史本身不删除。`src/agents/result.py::RunResult.to_input_list`、`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession.commit`、`src/agents/chat_backend/static/app.js::isPlanningDefinitionMessage`
- [FACT] PlanningRun 中已保存的 objective 每次都写进草案 Agent 的系统指令；当前 content 只作为首次目标、补充答案、重试请求或修订反馈。即使上一次模型结果无效而 Session 没有提交，本次重试仍保留原目标。解析器只额外接受可由 `json.loads()` 解成对象的字符串，数组、Markdown 和宽松字段仍拒绝。`src/agents/chat_backend/planning_service.py::PlanningService.draft`、`_parse_draft_output`

```text
PlanningService.approve(run_id, expected_row_revision)
  -> asyncio.to_thread(PlanStore.approve) -> materialized running journal
  -> _drive_running while loop
       -> PlanExecution.from_events(latest journal)
       -> existing RUNNING task, otherwise PlanExecution.next_ready()
       -> final verification:
            TaskStore.transition(PENDING -> RUNNING) when needed
            -> asyncio.to_thread(PlanStore.save_execution, running)
            -> compare current Manifest metadata with frozen snapshot
            -> read frozen default_test_command
            -> LocalEnvironment(current env + restricted workspace)
            -> asyncio.to_thread(VerificationRunner.run, one-command policy)
                 -> passed: RUNNING -> COMPLETED -> save completed
                 -> failed/error: keep Task RUNNING -> save paused + fixed error
       -> ordinary pending task:
            TaskStore.transition(PENDING -> RUNNING)
            -> asyncio.to_thread(PlanStore.save_execution, running)
            -> resolve frozen Profile/current key + Manifest metadata
            -> build_plan_step_agent
            -> RunConfig(task_store=execution.task_store, task_id=current)
            -> existing Runner.run_stream(_step_prompt)
                 -> pending approval: RunResult.to_state -> v4 snapshot
                    -> save awaiting_tool_approval
                 -> valid final text: RUNNING -> COMPLETED
                    -> save running -> next loop
                 -> model/tool/invalid result: keep Task RUNNING
                    -> save paused + fixed safe last_error
```

- [FACT] `next_ready()` 是唯一普通步骤选择器；服务没有复制依赖排序。每次成功完成后都从刚保存的完整 journal 重新构造 `PlanExecution`，因此后续领取使用最新 Task revision 和 Planning row revision。`src/agents/chat_backend/planning_service.py::PlanningService._drive_running`、`src/agents/planning.py::PlanExecution.next_ready`
- [FACT] Task 的 RUNNING 事件在 Runner 构造前已经经 `PlanStore.save_execution()` 提交；CAS/状态失败会在任何模型或工具调用前离开。普通步骤输入除自身指令外，只带入已完成直接依赖的非空结果，并在 20000 字符总预算内截断；完成文本去空白后也必须非空且不超过 20000 字符。工具错误即使随后出现 final output 也保存 paused。`src/agents/chat_backend/planning_service.py::_step_prompt`、`PlanningService._claim_task`、`_run_current_step`
- [FACT] Profile id/provider/protocol/base URL/model/has-api-key 与完整 Manifest metadata 必须和创建时快照一致；当前 key 与 env 值只在运行时使用而不参与比较或持久化。资源漂移发生在 Task 领取后，所以该 Task 保持 RUNNING，PlanningRun 保存固定 `model_step_failed` 摘要。`src/agents/chat_backend/planning_service.py::PlanningService._resolve_frozen_resources`、`_safe_run_error`
- [FACT] 待审批分支保存现有 v4 RunState，并由 Store 核对 task id/revision；安全快照投影审批键、现有安全摘要和 `decide_tool_approval` 动作，不公开完整参数或私有 `model_state`。决定入口要求精确 `(tool_name, call_id)`，从 snapshot 恢复原 RunState、供应商工具交换、工作区 context 和 previous response id，先用调用方 row revision CAS 清除快照并保存 running，成功后才调用 `Runner.resume_stream()`。最终验证仍不调用普通步骤 Agent、Runner 或模型。`src/agents/chat_backend/planning_service.py::PlanningService._run_current_step`、`_snapshot_from_run`、`decide_tool_approval`、`_restore_approval_run`、`_resume_current_step`、`_run_final_verification`
- [FACT] fresh 与 resume 流都用 `_collect_step_stream()` 转发同一种进度事件，并用 `_save_step_result()` 保存审批暂停、文本完成或安全失败，因此没有复制第二套结果状态机。审批恢复成功后若当前步骤完成，仍回到同一个 `_drive_running()` 严格串行循环。`src/agents/chat_backend/planning_service.py::PlanningService._collect_step_stream`、`_save_step_result`、`_drive_running`
- [FACT] final 与普通步骤共用 `_claim_task()`，所以外部命令之前已持久化 RUNNING。当前 manifest metadata 必须与冻结快照完全一致，但环境变量值取当前 manifest.env 且不落库。VerificationRunner 必须返回恰一条同命令结果；通过时只保存短摘要并让 Store 完成计划/重置会话，失败或异常只保存固定错误并 paused，不复制命令输出。`src/agents/chat_backend/planning_service.py::PlanningService._resolve_frozen_manifest`、`_frozen_test_command`、`_run_final_verification`、`src/agents/chat_backend/planning_store.py::PlanStore.save_execution`
- [FACT] 进程在 final RUNNING 保存后、命令前退出，或命令完成后、completed 保存前退出，后续 running 恢复都可能再次执行命令；这是明确的 at-least-once，不是 exactly-once。`src/agents/chat_backend/planning_service.py::PlanningService._claim_task`、`_run_final_verification`

```text
PlanningService.retry_current(paused run, expected_row_revision)
  -> read and replay the same TaskEvent journal
  -> PlanStore.save_execution(paused -> running) with caller CAS
  -> _drive_running(existing RUNNING task)
       -> ordinary task: fresh Runner.run_stream
       -> final task: VerificationRunner.run(frozen command)
```

- [FACT] paused 不保存可恢复 RunState，因此普通步骤重试是同一个 Task 的新一次模型运行，不走 `resume_stream()`；final 重试也不建模型。CAS 在外部工作前完成，旧 revision 不会触发模型、工具或验证。`src/agents/chat_backend/planning_service.py::PlanningService.retry_current`、`_drive_running`

```text
PlanningService.revise(active run, expected_row_revision, feedback)
  -> PlanStore.revise
       -> replay existing journal
       -> cancel every unfinished task, keep completed results
       -> old run = superseded; new version = drafting
  -> PlanningService.draft(new run revision, feedback)
       -> existing message history + read-only planning agent
```

- [FACT] 修订来源可为 awaiting approval、running、awaiting tool approval 或 paused；新版本继承原来不含密钥的模型/工作区快照。服务直接复用现有 `draft()`，没有增加只被调用一次的第二条草案路径。`src/agents/chat_backend/planning_service.py::PlanningService.revise`、`draft`、`src/agents/chat_backend/planning_store.py::PlanStore.revise`

```text
PlanningService.cancel(active run, expected_row_revision, reason)
  -> PlanStore.cancel
       -> replay journal and append CANCELLED events for unfinished tasks
       -> preserve COMPLETED results
       -> clear approval snapshot, set cancelled, conversation mode = default
```

- [FACT] 修订和取消共用 `_cancel_unfinished_tasks()`；父任务取消可能级联子任务，所以 helper 每次转换前重新读取节点，避免使用过期 revision。已在外部运行但尚未保存的工作不能被这里强杀；它后续用旧 row revision/status 保存时会被 Store 拒绝。`src/agents/chat_backend/planning_store.py::_cancel_unfinished_tasks`、`PlanStore.revise`、`cancel`

```text
startup caller -> await PlanningService.recover_running(limit)
  -> PlanStore.list_recoverable_running(updated_at, id; running only)
       -> corrupt row with trusted UUID
            -> PlanStore.quarantine_running(lock raw row)
            -> failed + safe error + Conversation.default
            -> rescan with remaining limit
       -> valid candidates
            -> for each candidate, await _recover_one
                 -> re-read latest row
                 -> no longer running: recovered/skip external work
                 -> still running: _drive_running
                      -> boundary: claim next_ready
                      -> current ordinary Task: fresh Runner
                      -> current final Task: frozen verification
                 -> left running after temporary error: deferred
```

- [FACT] 外层只有普通 `while` 与 `for`，没有 `asyncio.gather`、队列、常驻 worker 或无限轮询；同步 scan/get/quarantine 通过 `asyncio.to_thread()` 执行，但每条候选仍按数据库顺序 await。`src/agents/chat_backend/planning_service.py::PlanningService.recover_running`、`_recover_one`
- [FACT] 正常 `get/list_recoverable_running` 继续严格解码完整 PlanningRun；只有已经得到可信 UUID 的损坏 running 行走 `quarantine_running()`。该事务只读取 id/conversation/status，锁行确认仍是 running，再写 failed、固定 `planning recovery failed`、清 snapshot、递增 row revision 并恢复会话 default，不改写坏 TaskEvent。`src/agents/chat_backend/planning_store.py::PlanStore.quarantine_running`、`_planning_run_from_row`
- [FACT] RUNNING 保存后到外部调用前、外部副作用后到 COMPLETED 保存前、验证结束到 completed 保存前、审批 snapshot 清除后到 resume 完成前，进程退出都会让新进程再次 fresh 执行当前步骤或验证；恢复明确是 at-least-once。`src/agents/chat_backend/planning_service.py::PlanningService._recover_one`、`_drive_running`
- [FACT] 外部调用方只需要从 `agents.chat_backend` 导入 Service、六个安全投影/结果值和两个稳定 Service 错误；`_drive_running/_claim_task/_cancel_unfinished_tasks`、草案 schema、原始 RunState 和资源 helper 不构成公共调用入口。`src/agents/chat_backend/__init__.py::__all__`、`src/agents/chat_backend/planning_service.py::__all__`

## 14. Planning HTTP/SSE、断线继续与前端投影

```text
browser mode=plan
  -> POST /conversations/{id}/planning/draft/stream
  -> planning_stream starts one background PlanningService operation
       -> status: safe RunEvent stage + planning/task ids
       -> snapshot: emit only after persisted row_revision changes
       -> done: final structured snapshot; draft also returns kind/content/message_id/run_trace
       -> browser disconnect: stop queue writes, keep operation running
  -> GET /conversations/{id}/planning
       -> PlanStore.get_latest, including terminal versions
       -> PlanDefinition + TaskEvent replay -> ordered step projection
       -> pending RunState approvals -> approval_summary_for_tool_call
  -> frontend renders structured cards with textContent
       -> chat reply + planning details show up to 200 safe status events
       -> completed draft trace persists on its assistant message and survives refresh
       -> polls drafting/running each second
```

- [FACT] 所有控制请求都带 `expected_row_revision`。HTTP 在启动 SSE 前检查当前 revision 与 `allowed_actions`；过期页面返回 `planning_conflict`，前端在流结束或错误后重新读取最新快照，不自动重放原操作。`src/agents/chat_backend/http.py::require_planning_action`、`require_planning_draft`、`src/agents/chat_backend/static/app.js::streamPlanning`
- [FACT] `PlanningSnapshot.steps` 始终按 `PlanDefinition.steps` 固定顺序生成，状态、阻塞、结果和错误来自 `TaskEvent` 重放；无事件的 awaiting approval 统一投影 pending。审批公开值只保留工具名、调用 id、安全摘要和原因，不公开原始参数、完整 patch、API key、env 值、模型响应或 `RunStateSnapshot`。`src/agents/chat_backend/planning_service.py::_snapshot_from_run`、`src/agents/approval_summaries.py::approval_summary_for_tool_call`
- [FACT] SSE 客户端断开后把连接标志置 false，后续进度回调不再向无人消费的队列写入，但应用级 task 引用保留到操作完成；完成回调移除引用并记录异常。用户主动 cancel 仍走 Planning CAS 和 TaskEvent 收口，不能撤销已经发生的外部副作用。`src/agents/chat_backend/http.py::planning_stream`、`track_background`、`src/agents/chat_backend/planning_service.py::PlanningService.cancel`
- [FACT] 前端只有原生 DOM/fetch/AbortController/setInterval，没有框架或轮询依赖。规划正文提交后，聊天区立即建立临时用户/助手消息，运行详情默认折叠；同一批 status 同步进入规划面板。draft 成功后 HTTP 用 `PlanningDraftOutcome.message_id` 把最多 200 条白名单阶段写回助手消息，刷新后前端用 `plan_markdown` 识别并隐藏这份重复正文，以结构化 PlanningSnapshot 作为唯一可见计划。计划外层用原生 `details` 折叠，活动态首次展开，进入终态自动收起；completed 快照在其后生成一条未落库的简短最终回复。失败仍保留输入原文。所有内容只经 `textContent`，不包含模型私有推理。`src/agents/chat_backend/http.py::planning_stream`、`src/agents/chat_backend/static/app.js::beginPlanningDraft`、`planningShellOpen`、`isPlanningDefinitionMessage`、`renderPlanningFinalReply`
- [FACT] 点击计划或工具批准后，前端先显示“审批已提交/正在批准”，再等待原 SSE 完成。`runPlanningAction()` 把 `streamPlanning()` 的成功布尔值返回给调用者；工具操作结束后用原 `(tool_name, call_id)` 对比最新 snapshot。原 call id 消失但仍有审批卡表示本次已经生效、Agent 又请求了下一项工具，而不是按钮失效。`src/agents/chat_backend/static/app.js::approvePlanning`、`decideToolApproval`、`showPlanningNotice`
- [FACT] 多工具审批逐个恢复时，已执行调用的历史 `tool_approval_required` 仍保留在 `new_items`，同时匹配的 `tool_result/tool_error` 带有 `tool_execution.tool_name/call_id`。下一次 `build_run_result()` 遇到 Context 中未知的历史审批时先检查该结果证据，存在匹配结果即判定已收口，只把真正 pending 的调用写入下一版 RunState；否则旧调用会在 A→C→A 的审批循环中复活。`src/agents/result.py::RunResultBase._approval_request_is_pending`、`src/agents/tool_execution.py::execute_tool_call`、`src/agents/run_state.py::build_run_result`
