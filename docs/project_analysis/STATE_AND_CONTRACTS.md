# 状态与契约

## 状态生命周期

| 状态 | 生命周期与证据 |
|---|---|
| `AgentMemory` | [FACT] Agent 实例内的当前 task/steps；转为下一轮 `ChatMessage`。`src/agents/memory.py::AgentMemory` |
| `RunContextWrapper` | [FACT] 单次 run 的 context、usage、metadata、不可变外部 `ContextChunk` 元组和工具审批表。`src/agents/run_context.py::RunContextWrapper` |
| `RunState` | [FACT] 单次 fresh/resume 的可变循环状态，保存计数、limits、pending calls、guardrail 结果、最终答案、全部模型 attempt、最后模型终止原因，以及可选的 task id/revision 同步点。`src/agents/run_state.py::RunState` |
| `RunResult` | [FACT] 从 `RunState` 构建的结束结果，冻结 attempt tuple 和可选 task id/revision；结果可转 state snapshot v4，生成后不会随 Store 后续变化。`src/agents/run_state.py::build_run_result`、`src/agents/result.py::RunResultBase.to_state` |
| `TaskNode` | [FACT] 独立任务图中的冻结节点快照；每次状态或依赖变化生成 revision+1 的新值，旧快照不原地修改。`src/agents/task_state.py::TaskNode` |
| `TaskEvent` | [FACT] schema v1 的冻结 after-node 变化事实；事件 id、task id、前后 revision、类型、UTC 时间、reason 与完整新节点创建后不可修改。`src/agents/task_state.py::TaskEvent` |
| `TaskStore` | [FACT] 进程内任务事实容器；用 nodes/seen-event 字典、journal 列表、`RLock`、CAS 和批量预计算保持一致。Store 本身不进入 `RunState`、`RunResult` 或 snapshot，只有被绑定节点的 id/revision 标量进入。`src/agents/task_state.py::TaskStore`、`src/agents/run_state.py::RunState` |
| `AgentSession` / `JsonSession` | [FACT] 跨 run 的 turn 历史；后者本地 JSON 持久化。`src/agents/memory.py::AgentSession`、`JsonSession` |
| `Conversation` | [FACT] 数据库会话行的冻结快照，保存 UUID、创建时间、下一条消息序号、规范化标题、`active/archived` 状态和 `default/plan` 模式；由 `ConversationStore` 的固定列投影构造。mode 不在通用 `ConversationPatch` 中，只由 Planning 生命周期事务切换。`src/agents/chat_backend/conversation.py::Conversation`、`ConversationMode`、`ConversationStore` |
| `ConversationStore` | [FACT] 持有既有 `Database`，创建/读取会话、用 `(created_at,id)` 稳定分页、修改 title/status 或删除会话；本身不缓存状态，也不管理连接池生命周期。`src/agents/chat_backend/conversation.py::ConversationStore` |
| `StoredMessage` | [FACT] 数据库消息行的冻结快照，保存消息/会话 UUID、单调序号、当前 `MessageRole`、正文和创建时间；由 `MessageStore` 从 INSERT/SELECT 返回行构造。`src/agents/chat_backend/conversation.py::StoredMessage`、`MessageStore` |
| `MessagePage` | [FACT] 冻结的消息页，保存 sequence 正序的 `StoredMessage` 元组和可选排他 `before_sequence`；有更早消息时，游标等于本页最小 sequence。`src/agents/chat_backend/conversation.py::MessagePage`、`MessageStore.page` |
| `MessageStore` | [FACT] 持有既有 `Database`；批量写入领取连续 sequence，列表读取全部或最近 N 条，分页按排他 sequence 游标向更早历史移动，也能原子弹出最后消息或清空消息；本身不缓存状态。`src/agents/chat_backend/conversation.py::MessageStore` |
| `PostgresSession` | [FACT] 持有会话 UUID 与既有 `MessageStore`；以同步四方法满足结构化 `SessionLike`，在 `StoredMessage` 与 `ChatMessage` 之间转换，不缓存历史、不管理数据库生命周期。`src/agents/chat_backend/postgres_session.py::PostgresSession` |
| `CommitOnSuccessSession` | [FACT] 单次 Runner 调用的消息缓冲；读取委托 PostgreSQL，Runner 保存时只累积本轮消息，最终答案匹配后才允许一次提交，并可在提交前只替换最后一条 assistant 展示文字。`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession` |
| `ChatReply` | [FACT] 成功提交后冻结的返回值，保存 conversation id、最终 assistant 数据库 message id、sequence、正文和本轮实际引用；引用 tuple 默认空，旧四参数构造兼容。`src/agents/chat_backend/service.py::ChatReply` |
| `ChatService` | [FACT] 长生命周期组合对象，保存零参数 Agent 工厂、两个 Store、可选基础 RunConfig、记忆提取服务，以及互斥的旧记忆检索或统一检索服务；每次调用取得请求级 Agent 与模型，并产生新的 PostgresSession 和本轮提交缓冲。`src/agents/chat_backend/service.py::ChatService` |
| `PlanningRun` | [FACT] 数据库中一版计划的冻结完整快照；保存会话/version/row revision/九态生命周期、definition、完整 TaskEvent tuple、可选审批恢复 snapshot、无密钥 model/workspace 字典和时间。`src/agents/chat_backend/planning_store.py::PlanningRun` |
| `PlanningVersion` | [FACT] 历史列表使用的冻结摘要；不携带 definition、事件、run snapshot、模型/工作区快照或错误正文。`src/agents/chat_backend/planning_store.py::PlanningVersion` |
| `PlanStore` | [FACT] 持有既有 `Database`，用参数化 SQL、行锁、事务和 row revision CAS 保存版本/执行边界并读取恢复候选；本身不缓存计划、不管理连接池、不调用模型或 Runner。`src/agents/chat_backend/planning_store.py::PlanStore` |
| `CancellationToken` | [FACT] 单 run 的线程可见取消状态；首次取消固定 reason/requested_at，重复取消不覆盖。`src/agents/run_control.py::CancellationToken.cancel` |
| `RunController` | [FACT] 单 run 的取消入口、finished/terminal 状态和待处理 steer FIFO。`src/agents/run_control.py::RunController` |
| `RunStream` | [FACT] 公共惰性、单消费者异步 handle；保存单 run runtime、producer/cancel/drain task，公开只读 controller。`src/agents/runner.py::RunStream` |
| `_RunRuntime` | [FACT] 内部单 run bundle，保存 reducer/token/controller/channel、取消宽限期和私有运输统计；不注册到全局。`src/agents/run_control.py::_RunRuntime`、`_create_run_runtime` |
| `ModelEventAccumulator` | [FACT] 单次模型 attempt 的临时聚合状态；保存 expected sequence、内容/工具 buffer、usage 与唯一 terminal，`finish()`/`abort()` 后释放字符串和 pending 状态，只保留 usage 缺失、字符峰值、缓冲上限与可选越界尝试值。`src/agents/model_events.py::ModelEventAccumulator`、`ModelEventDiagnostics` |
| `ModelExecution` / `ModelExecutionError` | [FACT] 单次逻辑模型请求的成功或最终失败结果；两者都携带全部已发生 `ModelAttemptRecord`，只有成功对象携带可提交的最终 `ModelResponse`。`src/agents/model_retry.py::ModelExecution`、`ModelExecutionError` |
| `OpenAIResponsesModel` 响应状态 | [FACT] 流式与一次性非流请求前记录 input/tools/request summary；`last_ignored_provider_event_count` 保存最近 attempt 的未知非关键事件数量，非流 attempt 重置为 0。请求模型等于 adapter 默认模型时才可复用旧 `previous_response_id`。完整成功才更新 `last_response`、`last_output_text` 并清空 `pending_tool_outputs`；备用模型成功将 stored previous id 清为 None，失败、断流、overflow 或 consumer close 保留上一份 response 状态。`src/agents/models.py::OpenAIResponsesModel.stream_response`、`get_response_for_request` |

## 数据和接口契约

- [FACT] 消息、工具、模型和事件的核心模型是 `ChatMessage`、`ToolSpec`、`ToolArgument`、`ToolCall`、`ModelResponse`、`RunItem`。`src/agents/contracts.py::ChatMessage`、`ToolSpec`、`ToolCall`、`ModelResponse`、`RunItem`
- [FACT] `SessionLike` 要求同步 `get_items/add_items/pop_item/clear_session` 四个方法。`src/agents/run_config.py::SessionLike`
- [FACT] model adapter 的同步契约是 `get_response(messages, tool_specs)`，兼容 helper 会在实现接受时附加 `model_settings`；原生流契约独立为 `stream_response(ModelRequest) -> AsyncIterator[ModelEvent]`。内部 `stream_model_response()` 优先原生流，否则在线程中适配同步 `get_response`/`decide`；生产 `model_turn` 现在构造 `ModelRequest` 并调用唯一 `run_model_request()`。`src/agents/models.py::ModelAdapter`、`StreamingModelAdapter`、`call_model_response`、`stream_model_response`、`src/agents/model_retry.py::run_model_request`、`src/agents/model_turn.py::_run_model_turn_async`
- [FACT] `function_tool()` 从 Python 签名、标注、docstring 生成 schema；发给 OpenAI 的 function schema 为 strict object 且禁止额外字段。`src/agents/tools.py::function_tool`、`src/agents/models.py::tool_spec_to_openai_tool`
- [FACT] 本地工具参数验证只检查缺失/未知名称，不按 JSON schema 验证值类型。`src/agents/tool_runtime.py::validate_tool_arguments`

## Task Graph Core 契约

| 当前状态 | 允许进入 |
|---|---|
| `pending` | `running`、`blocked`、`cancelled` |
| `running` | `completed`、`failed`、`blocked`、`cancelled` |
| `blocked` | `pending`、`cancelled` |
| `completed`、`failed`、`cancelled` | 无 |

- [FACT] `TaskSpec` 与 `TaskNode` 使用 frozen dataclass；`dependency_ids` 和 `blocked_by` 固定为 tuple。冻结只阻止字段重新赋值，不深度冻结任意 `result` 对象。`src/agents/task_state.py::TaskSpec`、`TaskNode`
- [FACT] 新节点从 `pending/revision=1` 开始。成功状态或依赖变化 revision 加 1；Store 更新必须提供最新 `expected_revision`，冲突抛 `TaskConflictError` 且零提交。`src/agents/task_state.py::TaskStore.create`、`transition`、`add_dependency`
- [FACT] `created_at`、`started_at`、`finished_at` 必须是 UTC aware datetime；首次 running 写 started，completed/failed/cancelled 写 finished。completed 必须显式给 result，failed/blocked 必须给非空 error。`src/agents/task_state.py::transition_task_node`
- [FACT] parent 表示“谁创建了谁”，dependency 表示“谁完成后我才能开始”。parent 深度受 `max_depth` 限制；dependency 必须存在且不能形成环。`src/agents/task_state.py::TaskStore.create`、`add_dependency`
- [FACT] `ready()` 只返回 pending 且全部依赖 completed 的节点；blocked 恢复 pending 也必须重新检查全部依赖。Store 不因依赖后来完成而自动恢复节点。`src/agents/task_state.py::TaskStore.ready`、`transition`
- [FACT] 依赖进入 failed/blocked/cancelled 会阻塞直接和间接下游；父任务取消会取消未终态后代；owner missing 会阻塞其 pending/running 任务并继续传播。terminal 节点不被覆盖，parent 取消优先于 dependency blocked。`src/agents/task_state.py::TaskStore.transition`、`mark_owner_missing`
- [FACT] 多节点变化先写入 staged dict，并按“主节点、parent BFS、dependency BFS”的首次出现顺序生成完整事件批次；批次失败时 nodes、seen events 和 journal 都不变。同一节点一次操作最多增加一个 revision。`src/agents/task_state.py::TaskStore._stage_node`、`_apply_event_batch`
- [FACT] 该 Store 只保证单进程内存一致性，没有数据库、跨进程锁、自动调度或自动冲突重试。Planning Tools 可显式修改它；run loop 也可通过 `RunConfig` 显式读取一个绑定节点，但不会自动创建 root、注册工具或把 TaskEvent 复制进 RunEvent。`src/agents/task_state.py::TaskStore`、`src/agents/task_tools.py::create_task_tools`、`src/agents/run_loop.py::_resolve_task_binding`

### TaskEvent、journal 与 replay 契约

- [FACT] `TaskEventType` 只有 `created` 和 `updated`；created 必须是 `pending/revision=1/previous_revision=0`，updated 必须沿当前 revision 前进一步且只能表达 Store 现有命令允许的状态、追加依赖或 blocker 合并。`src/agents/task_state.py::TaskEventType`、`_reduce_task_event`
- [FACT] `create()`、`transition()`、`add_dependency()`、`mark_owner_missing()` 以及父取消/坏依赖/owner missing 级联都先生成 `TaskEvent`，再进入同一个 `_apply_event_batch()`；命令的返回值和稳定遍历顺序不变。`src/agents/task_state.py::_task_event_from_change`、`TaskStore`
- [FACT] `_apply_event_batch()` 将输入固定为 tuple，在同一把 `RLock` 内只建立一份 nodes/seen 工作副本；相同 event id/相同内容为 no-op，相同 id/不同内容、revision gap、坏引用、非法变化、环或 limit 会在真实提交前失败。`src/agents/task_state.py::TaskStore._apply_event_batch`、`_reduce_task_event`
- [FACT] `events()` 返回不可变 tuple 快照；事件对象和节点对象本身冻结，但任意 `result` 仍只保证浅层不可重新赋值。`src/agents/task_state.py::TaskStore.events`
- [FACT] node/event converter 要求字段集合完全相等，schema 必须为 1，enum 必须已知，UTC 时间必须是规范 `Z` 字符串；`result` 只有在 JSON 往返后类型和值均不变时才允许导出。Store 本身仍接受任意 Python result。`src/agents/task_events.py::task_node_to_dict`、`task_event_from_dict`
- [FACT] `replay_task_events()` 按调用方顺序把 typed events 一批应用到新 Store；它不排序、不猜测缺失事件、不接受普通字典，也不返回失败中的局部 Store。`src/agents/task_events.py::replay_task_events`
- [FACT] 创建新任务时，旧图不可能已经存在回到这个新 id 的合法边，因此 created 只检查 dependency 存在；update 仍执行完整 dependency 环搜索。10k 顺序 created 事件在显式提高 `max_nodes` 后，本机测得 `0.012235s` 和 `660800 peak bytes`。`src/agents/task_state.py::_validate_event_node_graph`
- [FACT] converter 只是内存值转换，不是 writer；当前没有 TaskEvent Session/file/database backend、subscriber、checkpoint、compaction 或 migration registry。`src/agents/task_events.py`

### Planning Tools 契约

- [FACT] 公共 `create_task_tools(store, *, root_task_id, owner_id)` 要求既有 root 和非空 owner，固定返回 create/update/list 三个 `FunctionTool`；factory 不写 Store、不注册工具。`src/agents/task_tools.py::create_task_tools`
- [FACT] create 的默认 parent 是绑定 root，显式 parent 和全部 dependency 必须位于 root 子树；owner 由闭包固定。通过边界检查后只构造一个 `TaskSpec` 并调用一次 `TaskStore.create()`。`src/agents/task_tools.py::_is_in_root_scope`、`create_task_tools`
- [FACT] update 要求目标位于 root 子树且 owner 匹配，把 `expected_revision` 原样交给 `TaskStore.transition()`；私有 sentinel 区分省略 result 与显式 `None`，不自动读取新 revision 或重试。`src/agents/task_tools.py::_require_owned_task`、`create_task_tools`
- [FACT] list 的 status 与 ready_only 互斥，只返回 root 子树中同 owner 节点，保留 Store 原顺序并复用 `task_node_to_dict()`；查询不追加 journal。`src/agents/task_tools.py::create_task_tools`
- [FACT] Store/scope/输入错误统一由现有 `FunctionTool.execute()` 包装为 `ToolExecutionError`；run loop 把它记录为可恢复的 tool error，不把 TaskEvent 复制进 RunEvent、RunState 或 RunResult。`src/agents/tools.py::FunctionTool.execute`、`src/agents/tool_execution.py::_execute_tool_call_impl`
- [FACT] 包根只公开 factory；root/owner helper、状态输入别名、result sentinel 和闭包 handler 不在 `agents.__all__`。`src/agents/__init__.py::__all__`

### PlanDefinition 与 PlanExecution 契约

- [FACT] `PlanStepSpec` 与 `PlanDefinition` 都是 frozen dataclass，并把 dependency/steps 输入转成 tuple。文本字段必须为非空字符串；bool 必须是真正的 bool，不能用整数冒充；单步拒绝重复依赖和自依赖。`src/agents/planning.py::PlanStepSpec`、`PlanDefinition`
- [FACT] 完整计划必须有 2–12 步、唯一 step id、至少一个普通步骤，并以唯一 final verification 作为最后定义项；依赖必须存在且无环。最终步骤必须直接依赖每个普通叶子节点。允许依赖后面声明的步骤。`src/agents/planning.py::PlanDefinition.__post_init__`、`_topological_steps`
- [FACT] `PlanValidationError` 只保存稳定 rule code 和可选 step/dependency id，字符串内容不回显 instruction 或 completion criteria。`src/agents/planning.py::PlanValidationError`
- [FACT] 计划字典 schema 固定为 1，字段集合必须完全相等，字符串/list/bool/int 使用严格类型检查；未知版本、缺失/额外字段和错误类型均拒绝。Markdown 是单向审阅投影，不是恢复格式。`src/agents/planning.py::plan_definition_from_dict`、`plan_definition_to_dict`、`render_plan_markdown`
- [FACT] 每个 `PlanExecution` 独占一份新 `TaskStore`，`step_id == task_id`，节点无 parent，owner 由 materialize/from_events 调用固定。definition 不保存运行状态；`TaskStore` 仍是 status/revision/result/error/CAS/journal 的唯一事实来源。`src/agents/planning.py::PlanExecution`
- [FACT] `from_events()` 复用 typed `TaskEvent` replay，重放后必须与 definition 的任务集合、title、dependencies、owner 和 parent 结构完全相等；不接受半张图或额外任务。`src/agents/planning.py::PlanExecution.from_events`、`_validate_execution_store`

### PlanningRun 持久化、版本与状态契约

| 状态 | 允许的进入方式 | 必须同时满足 |
|---|---|---|
| `drafting` | `create_draft()`；或 `revise()` 创建新版本 | 无 definition/events/snapshot/error；占用会话活动槽 |
| `awaiting_approval` | drafting 经 `submit_for_approval()` | 有 definition；无 events/snapshot/error；占用活动槽 |
| `running` | awaiting approval 经 `approve()`；或执行保存经 `save_execution()` | 有 definition/events；无 snapshot/error；零个 RUNNING 时仍有 pending/blocked，或恰一个 RUNNING 当前任务；无 FAILED；占用活动槽 |
| `awaiting_tool_approval` | running/approval pause 经 `save_execution()` | 有 definition/events；恰一个 RUNNING 节点；snapshot task link 与它一致且含 pending approval；占用活动槽 |
| `paused` | running 经 `save_execution()` | 有 definition/events；恰一个 RUNNING 节点；无 snapshot；非空安全 last_error；占用活动槽，等待用户命令 |
| `completed` | running/approval pause 经 `save_execution()` | 全部 TaskNode completed；无 snapshot/error；释放活动槽和 conversation plan mode |
| `failed` | running/approval pause 经 `save_execution()` | 可正常回放时全部 TaskNode completed/cancelled、非空 last_error；无 snapshot；释放活动槽和 plan mode |
| `cancelled` | 任一活动状态经 `cancel()` | 无 snapshot/error；已有事件时全部非终态节点先转 cancelled；释放活动槽和 plan mode |
| `superseded` | awaiting approval 经 `revise()` | 旧版保留 definition 和可选历史 events；有 events 时节点只可 completed/cancelled；无 snapshot/error；同事务生成下一版 drafting |

- [FACT] `Conversation.mode` 只回答普通聊天还是规划流程；`PlanningRun.status` 回答一版计划处在哪个生命周期；重放后的 `TaskNode` 回答每一步的状态、结果和 revision；`RunStateSnapshot` 只回答工具审批暂停恢复；`row_revision` 只拒绝同一计划行的旧写入；`version` 只排列同一会话的计划历史。这些事实不互相复制。`src/agents/chat_backend/conversation.py::ConversationMode`、`src/agents/chat_backend/planning_store.py::PlanningRun`、`src/agents/task_state.py::TaskNode`、`src/agents/run_state.py::RunStateSnapshot`
- [FACT] 009 建表后，010 只追加替换状态 CHECK 和活动唯一索引：`paused` 进入活动集合，running 恢复索引仍只匹配 `status='running'`，009 文件及已记录 SHA-256 不变。SQL 检查 JSON 顶层和字段粗组合，TaskEvent 的 RUNNING 数量由 Python 回放后检查。`src/agents/chat_backend/migrations/009_planning_runs.sql`、`010_planning_execution_control.sql`
- [FACT] 所有修改先 `SELECT ... FOR UPDATE` 并核对调用方 `expected_row_revision`，随后 UPDATE 仍带 `WHERE row_revision = old` 并把 revision 加一；不存在、旧 revision 和非法状态分别抛 `PlanningRunNotFoundError`、`PlanningConflictError` 与 `InvalidPlanningTransition`。`src/agents/chat_backend/planning_store.py::_lock_run`、`_update_run`
- [FACT] TaskEvent journal 在执行期间只允许追加：新事件 tuple 必须以已保存 tuple 为精确前缀，解码和保存都用 `PlanExecution.from_events(definition, events, owner_id=str(run_id))` 复核结构与固定 owner。不能删除、重排、替换旧事件或把别的计划任务混入。`src/agents/chat_backend/planning_store.py::PlanStore.save_execution`、`_planning_run_from_row`
- [FACT] `awaiting_tool_approval` 直接保存现有 `RunStateSnapshot`，不另建 pending approvals 或 current task 字段；其他状态不保存 snapshot。消息历史仍只在 `MessageStore/PostgresSession`，planning 行不复制 session items。`src/agents/chat_backend/planning_store.py::_validate_state_combination`、`src/agents/chat_backend/conversation.py::MessageStore`
- [FACT] `create_draft()` 只持久化 `ModelProfile.public_dict()` 和 `WorkspaceManifest.metadata()` 的字典副本；API key、环境变量值和运行时对象不进入 planning 行。`src/agents/chat_backend/planning_store.py::PlanStore.create_draft`、`src/agents/chat_backend/model_profiles.py::ModelProfile.public_dict`、`src/agents/workspace_manifest.py::WorkspaceManifest.metadata`
- [FACT] 固定列投影先检查 UUID、正数、UTC、文本和 JSON 容器，再调用现有三类 codec 和 PlanExecution replay；任何坏 definition/event/snapshot/owner/状态组合统一抛带 run id 的 `CorruptPlanningRunError`。`src/agents/chat_backend/planning_store.py::_planning_run_from_row`、`_planning_version_from_row`
- [FACT] `running` 可保存步骤边界或唯一 RUNNING 当前节点；`paused` 复用同一 TaskEvent journal 保留当前节点，不增加 `current_task_id`。恢复扫描仍只取 running，暂停不会自动重试；外部副作用与完成事件之间仍有 at-least-once 崩溃窗口。`src/agents/chat_backend/planning_store.py::_validate_execution_state`、`PlanStore.list_recoverable_running`

### Run 与任务节点关联契约

- [FACT] `RunConfig.task_store` 与 `task_id` 都是可选值，但必须同时提供；id 必须是非空白字符串。配置只保存调用方给出的 Store 引用和节点 id，不建立 wrapper、Protocol 或自动 root。`src/agents/run_config.py::RunConfig.__post_init__`
- [FACT] fresh run 在读取 session/memory、触发 lifecycle、调用模型或工具前执行一次 `TaskStore.get()`；未知节点保留 `TaskNotFoundError`。resume 若 snapshot 已绑定，则必须给相同 id 的 Store；缺 Store 或 id 不匹配抛 `ValueError`。`src/agents/run_loop.py::_resolve_task_binding`、`_run_agent_loop_impl`
- [FACT] resume 开始时把 Store 当前 revision 写入同步点；completed、stopped、model failure 和 approval pause 都在统一结果构造处再读取一次同一节点 revision。该 revision 只表示绑定节点自身，不代表整个任务图。`src/agents/run_loop.py::_build_result_and_save_session`、`src/agents/run_state.py::build_run_result`
- [FACT] `RunState`、`RunResult` 和 v4 snapshot 只保存成对的 `task_id/task_revision`；不保存 `TaskStore`、`TaskNode`、`TaskEvent`、controller 或事件流。旧 snapshot 没有关联，调用方也可以在 resume 时显式提供一对新绑定。`src/agents/run_state.py::RunStateSnapshot`、`RunState.from_snapshot`、`src/agents/result.py::RunResultBase.to_state`
- [FACT] `Runner.resume_stream/resume_async/resume_sync` 要求真正的 `RunState`，并继续使用该 state 的原始 input。三个入口共享 fresh 的 `RunStream`、collector、事件 runtime、取消与异常传播；没有独立 ResumeRunStream。`src/agents/runner.py::Runner`、`RunStream`

## 模型 attempt 事件契约

- [FACT] 每个 `ModelEvent` 必须携带非空 attempt id 和从 1 开始的 sequence；累加器拒绝跨 attempt、乱序、错误首事件、重复/冲突终态和终态后事件。`src/agents/model_events.py::ModelEvent`、`ModelEventAccumulator.apply`
- [FACT] text/reasoning/refusal 按 `(output_index, content_index)` 聚合；工具调用按 item/call id 聚合，只有与分片全文一致且解析为 JSON object 的 done 才生成现有 `ToolCall`。`src/agents/model_events.py::_append_content`、`_finish_content`、`_finish_tool_arguments`
- [FACT] 内容与工具参数共用 `max_buffer_chars`；append 前计算加入当前分片后的字符数。超限时 `ModelProtocolError` 与 diagnostics 只保存上限和尝试值，不保存原始内容；随后清空本 attempt 的正文、reasoning、拒答与工具临时数据。`src/agents/models.py::ModelProtocolError`、`src/agents/model_events.py::_reserve_buffer_chars`、`abort`
- [FACT] usage 采用最后一次顶层字典快照；唯一 completed terminal 且无 pending 状态时，`finish()` 返回现有 `ModelResponse`。reasoning 不写入 `output_text`；diagnostics 还保留 usage 是否缺失和字符峰值。`src/agents/model_events.py::UsageUpdated`、`ModelEventDiagnostics`、`ModelEventAccumulator.finish`
- [FACT] 顶层 API 公开 provider adapter 所需的请求、具体事件、流式 Protocol、能力检查与错误；累加器和内部状态保持子模块边界。`src/agents/__init__.py::__all__`
- [FACT] OpenAI mapper 校验 provider `sequence_number` 单调递增，并转换 created、正文/reasoning/refusal、函数调用参数、usage、completed/failed/incomplete/error；未知非关键事件只递增 `ignored_event_count` 并返回空事件，不保存原始对象。OpenAI 原始事件名字只存在于 `models.py`。`src/agents/models.py::_OpenAIEventMapper`
- [FACT] 同步兼容桥用完整正文/拒答各一组 delta+done，用 `json.dumps()` 把每个现有 `ToolCall.arguments` 写成一组 start+delta+done，并保留 usage、output、raw 和 request metadata。缺失供应商 response id 时仅用 `ModelRequest.request_id` 完成内部 created/completed 配对。`src/agents/models.py::_model_response_to_events`

## 模型错误事实

- [FACT] `ModelResponseError` 保存稳定 kind、HTTP status、provider error code、原始 Retry-After、request/attempt identity 和原始异常；`QUOTA` 与普通 `RATE_LIMIT` 分开，`STREAM_UNAVAILABLE` 只表示创建流阶段的明确能力错误。`src/agents/models.py::ModelErrorKind`、`ModelResponseError`
- [FACT] OpenAI 分类顺序为 hard quota/context → stream-open unavailable → connection/408/409 → 429/5xx → auth/剩余 4xx → unknown，避免宽泛 4xx 吞掉可恢复或可降级事实；分类不读取普通错误 message 猜 code。`src/agents/models.py::classify_openai_model_error`、`_openai_error_kind`
- [FACT] `OpenAIResponsesModel.stream_response()` 只在创建原始流和读取下一条原始事件的边界分类 SDK/兼容异常；已有 `ModelResponseError`、协议错误、task 取消和其他 `BaseException` 保持原语义，adapter 本身不 retry。`src/agents/models.py::OpenAIResponsesModel.stream_response`

## 模型重试决策契约

- [FACT] 公共 `RetryAction` 有 `RETRY`、`FAIL`、`NON_STREAM_FALLBACK` 与 `MODEL_FALLBACK`；冻结的公共 `RetryDecision` 保存 action 和稳定 reason。`RetryPolicy`、attempt notice/record、execution/error 与 `run_model_request` 也从包顶层公开；delay/parser/id/wait/consume helper 保持内部。`src/agents/model_retry.py::RetryAction`、`RetryDecision`、`src/agents/__init__.py::__all__`
- [FACT] `RetryPolicy` 将 attempt、backoff、jitter、retry budget、total timeout 与非流降级开关的稀疏设置换算成有效值；`decide()` 只把 TRANSPORT、RATE_LIMIT、SERVER、EARLY_CLOSE 视为可重试。`STREAM_UNAVAILABLE` 只有在开关明确开启时返回 `NON_STREAM_FALLBACK`；RATE_LIMIT/SERVER 只有重试次数或累计等待预算耗尽且调用方确认备用模型可用时才返回 `MODEL_FALLBACK`，其余 kind 和未知 kind 始终失败。`src/agents/model_retry.py::RetryPolicy`
- [FACT] `parse_retry_after()` 接受非负秒数或带时区 HTTP-date；`compute_retry_delay()` 优先使用合法 Retry-After，否则计算 capped exponential backoff 与对称 jitter。合法服务端等待不受 backoff cap/jitter 影响，但所有等待都受累计 retry budget 与 `elapsed + delay < total_timeout` 限制。`src/agents/model_retry.py::parse_retry_after`、`compute_retry_delay`
- [FACT] `ModelAttemptRecord` 与 `ModelAttemptNotice` 保存稳定 identity、模式、结果、耗时、错误和等待事实；fallback notice 还保存 previous model/mode。两类 fallback 的源 record 与目标 record 使用同一 canonical reason，目标 attempt 的 outcome/error 另行说明成功或失败。`ModelExecution` 要求最终 attempt 成功，`ModelExecutionError` 要求最终 attempt 失败。`src/agents/model_retry.py::ModelAttemptRecord`、`ModelAttemptNotice`、`run_model_request`
- [FACT] 成功 attempt 还保存 response id、usage 快照与首个有意义流事件的 TTFT；这些字段随同一 `ModelAttemptRecord` 进入 state、result、snapshot 和 model span，不由运行层重新计算。`src/agents/model_retry.py::ModelAttemptRecord`、`src/agents/run_state.py::_model_attempt_to_state`、`src/agents/model_turn.py::_record_model_execution_span`
- [FACT] `run_model_request()` 保持 base request 不变，用 `{request_id}:attempt:{number}` 生成每次发送 identity；native stream 记录为 `stream`，sync/legacy 兼容桥及显式完整响应记录为 `non_stream`。流创建明确不可用时可在同一循环内切换一次同模型非流 attempt；RATE_LIMIT/SERVER 因次数或累计等待预算耗尽时可用同一 request id、新 attempt id 和不同备用 model name 发一次 stream attempt。任一 fallback 失败立即结束；非流路径不会再换模型，备用模型也不会 retry。`src/agents/model_retry.py::_attempt_id`、`_consume_stream_attempt`、`run_model_request`
- [FACT] 请求级 `max_stream_buffer_chars` 优先于模型实例 `max_buffer_chars`，两者都未设置才使用默认值；OpenAI 原生流内部和执行器外层聚合器使用同一优先级。任一层异常或外部 task 取消都会 abort 聚合器并关闭可关闭的异步流。`src/agents/model_retry.py::run_model_request`、`_consume_stream_attempt`、`src/agents/models.py::OpenAIResponsesModel.stream_response`、`stream_model_response`
- [FACT] `run_model_request()` 用一次 deadline 限制全部事件读取和 backoff；`_await_with_limits()` 取消未完成操作，在 event loop 上短周期检查现有 token，并把 deadline 耗尽变成 `TRANSPORT/total_timeout`。`_sleep_with_cancel()` 复用同一规则，不创建第二个 retry loop。`src/agents/model_retry.py::_await_with_limits`、`_sleep_with_cancel`、`run_model_request`
- [FACT] token 取消不经过模型 classifier，也不生成 failed record。模型尚未发送时，`ModelCancelledError` 携带空 attempts；读流中取消时追加一个 `outcome="cancelled"` 的真实 attempt；退避中取消时保留此前失败 attempt 与实际等待时间；完整响应返回但提交前取消时保留成功 attempt 的 response id、usage 与 TTFT。`ModelCancelledError` 继承 `RunCancelledError`，旧 catch 逻辑不变；`RunState` 与 model span 使用异常携带的同一组不可变 records，并把终止原因写为 `cancelled`。`src/agents/model_retry.py::ModelCancelledError`、`run_model_request`、`src/agents/model_turn.py::run_model_turn`、`src/agents/run_state.py::RunState.record_model_cancelled`
- [FACT] 已启动的同步 `get_response`/legacy/OpenAI SDK 线程不能被 task cancellation 强杀；协程会及时停止等待并忽略迟到结果，外部副作用仍由 adapter 承担。OpenAI 流式与一次性非流请求都受 SDK connect/read timeout 限制且关闭 SDK 自动 retry。`src/agents/models.py::stream_sync_model_response`、`OpenAIResponsesModel._open_response_stream`、`get_response_for_request`、`src/agents/model_retry.py::_await_with_limits`

## 运行事件契约

- [FACT] `RunEvent` 保存 `run_id`、连续序号位置、UTC 时间、可选 turn/step 和固定 schema version；具体事件对象冻结后不可修改。`src/agents/events.py::RunEvent`
- [FACT] `ModelAttemptStartedEvent` 保存真实发送的 request/attempt identity、模型、模式和可选原因；`ModelRetryScheduledEvent` 增加已分类错误、等待秒数和原因；`ModelFallbackEvent` 保存目标 attempt id、统一 reason、切换前后模型和模式。from/to mode 为可选默认字段，旧构造仍兼容；生产事件始终填写。三者不携带正文或工具参数。`src/agents/events.py::ModelFallbackEvent`、`src/agents/model_turn.py::_emit_model_attempt_notice`
- [FACT] `RunCompletedEvent`、`RunStoppedEvent`、`RunCancelledEvent`、`RunFailedEvent` 是当前四种终态；`RunErrorEvent` 不是终态。`src/agents/events.py::TerminalRunEvent`、`is_terminal_event`
- [FACT] `event_to_dict()` 保留可选空字段，显式加入 `event_type`，只转换 `timestamp`；当前没有字典反序列化或文件写入。事件由公共 `RunStream` 输出。`src/agents/events.py::event_to_dict`、`src/agents/runner.py::RunStream`
- [FACT] `RunEventReducer.emit()` 在事件构造成功后才递增单 run sequence；首个终态永久关闭该 reducer，不提供 reset、历史列表、锁或全局 registry。`src/agents/events.py::RunEventReducer.emit`
- [FACT] terminal 后再次 emit 抛出携带 run id/terminal sequence 的 `RunAlreadyFinishedError`；token 在合作取消检查点抛出携带 run id/reason 的 `RunCancelledError`。`src/agents/events.py::RunAlreadyFinishedError`、`RunCancelledError`、`src/agents/run_control.py::CancellationToken.raise_if_cancelled`
- [FACT] `emit_run_item_event()` 保留 `RunItem` 不变，并通过 reducer 映射 model response/error、tool call、成功 tool result、tool error、审批、final 和 stop；非审批的 `success=False` tool result 返回 `None`，`tool_call` 只表示 requested。`src/agents/event_compat.py::emit_run_item_event`
- [FACT] 工具结果与错误的定位只读取 `metadata["tool_execution"]`；缺少 tool name/call id 时失败，不从文本推断。`src/agents/event_compat.py::_tool_execution_fields`

## 配置来源与覆盖

| 配置 | 生效规则与证据 |
|---|---|
| 运行总配置 | [FACT] `RunConfig` 集中定义 context/session/context_chunks/tracing/hooks/guardrails/limits/model/verification。`context_chunks` 必须是只含 `ContextChunk` 的 tuple，默认空元组。`src/agents/run_config.py::RunConfig`、`src/agents/context_chunks.py::_validate_context_chunks` |
| 任务关联 | [FACT] `task_store/task_id` 必须同时为空或同时提供；完整绑定随 `dataclasses.replace()` 保留，真正的节点存在性与 resume 匹配在 loop 入口检查。`src/agents/run_config.py::RunConfig.__post_init__`、`src/agents/run_loop.py::_resolve_task_binding` |
| 事件与取消策略 | [FACT] `event_queue_size=100` 必须是至少 1 的非 bool int；`cancellation_grace_period=0.1` 必须是有限、非负的非 bool 数值。`src/agents/run_config.py::RunConfig.__post_init__` |
| steps 与工具行为 | [FACT] `RunConfig` 非空值覆盖 Agent 默认。`src/agents/run_loop.py::_resolve_max_steps`、`_resolve_tool_use_behavior` |
| 模型设置 | [FACT] run 级非 `None` 字段覆盖 Agent 设置。`src/agents/model_settings.py::ModelSettings.resolve` |
| hooks/guardrails | [FACT] hooks 为 RunConfig 后 Agent；agent guardrails 先、RunConfig guardrails 后。`src/agents/run_loop.py::_collect_lifecycle_hooks`、`_collect_input_guardrails`、`_collect_output_guardrails` |
| Coding CLI | [FACT] argparse → `CodingCliConfig` → manifest/profile/setup → 可选 verification/session。`src/agents/coding_cli.py::parse_coding_cli_args`、`build_coding_cli_setup` |

## Chat Backend 配置与 PostgreSQL 契约

- [FACT] `AppSettings` 是冻结值；`from_env()` 只从传入映射或 `os.environ` 读取一次，必填项缺失或格式、范围、字段关系无效时抛 `SettingsError`。它不读取 `.env` 文件，也不连接 PostgreSQL 或模型服务。`src/agents/chat_backend/settings.py::AppSettings`、`from_env`
- [FACT] `HealthStatus` 只接受非空服务名以及 `ready`、`unavailable`、`disabled` 三种状态；`Database.health()` 用 `SELECT 1` 判断 PostgreSQL，捕获 psycopg `Error` 后返回不可用状态，不把凭据或原始异常放进 detail。`src/agents/chat_backend/settings.py::HealthStatus`、`src/agents/chat_backend/database.py::Database.health`
- [FACT] `Database` 创建关闭状态的 `ConnectionPool`，普通连接为 autocommit；`transaction()` 在借来的同一连接上显式开始事务，代码块成功时提交、异常时回滚。连接池的 `open()`/`close()` 由最外层调用方成对管理。`src/agents/chat_backend/database.py::Database.__init__`、`connection`、`transaction`
- [FACT] 迁移文件名固定为三位版本号加小写名称，内容必须是非空 UTF-8；`schema_migrations` 保存版本、SHA-256 和数据库时间。已应用文件缺失或校验值变化会停止升级，每个新 SQL 与历史行在同一事务内提交。`src/agents/chat_backend/migration.py::_MIGRATION_NAME`、`MigrationRunner._discover_migrations`、`_read_history`、`upgrade`
- [FACT] `agents.chat_backend` 公开设置/健康、数据库/迁移、会话状态与 mode/游标/页/补丁/归档错误、会话消息 Store、长期记忆、知识值与 Store、三个 Retrieval Service、统一命中/Bundle/Citation/解析值、`KnowledgeIndexer`、`PostgresSession`、对话服务、PlanningRun/Version/Status/PlanStore/五个持久化错误，以及 `PlanningService`、四个安全返回值、两个 Service 错误、记忆提取入口和 `create_app()`；embedding 协议与实现从其模块导入。内部提交缓冲、Planning 行转换/CAS helper、草案 schema、执行 helper、HTTP schema/route 闭包、`main()`、规范化和查询 helper 不属于兼容承诺。`src/agents/chat_backend/__init__.py::__all__`、`src/agents/chat_backend/planning_service.py::__all__`、`src/agents/chat_backend/embedding.py`
- [FACT] 001 确保 `vector` 扩展；002 创建会话/消息；003/004 创建长期记忆与替代；005 增加记忆向量约束；006 创建知识来源、分块与 epoch；007 补 conversation title/status；008 增加安全运行详情；009 增加 mode 与 planning runs；010 追加 paused、可持久化当前任务的组合约束和活动索引。`PostgresSession` 仍只读写会话消息。`src/agents/chat_backend/migrations/001_vector_extension.sql`、`009_planning_runs.sql`、`010_planning_execution_control.sql`
- [FACT] `ConversationStore.create()` 规范化 1..200 字符标题并用 `INSERT ... RETURNING` 取得数据库事实；`get()` 按 UUID 读取；`list()` 使用 `(created_at,id)` 倒序排他游标、可选状态过滤和 `page_size + 1`；`update()` 只接受非空 `ConversationPatch`；`delete()` 由数据库外键级联完成清理。所有 SQL 参数化，Store 不缓存结果。`src/agents/chat_backend/conversation.py::ConversationStore.create`、`get`、`list`、`update`、`delete`
- [FACT] `ConversationNotFoundError` 是携带 conversation UUID 的 `LookupError`；读取、修改、删除无结果时抛出它。`ConversationArchivedError` 是共同聊天入口在检索和 Runner 前发现 archived 状态时抛出的稳定领域错误；数据库异常仍保持原类型或由 ChatService 映射为安全持久化错误。`src/agents/chat_backend/conversation.py::ConversationNotFoundError`、`ConversationArchivedError`、`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] `MessageStore.add_batch()` 把输入固定为 tuple；空批次直接返回。非空批次用一次父行 UPDATE 原子领取 `len(items)` 个 sequence，再在同一事务逐条参数化 `INSERT ... RETURNING`。并发 writer 获得不重叠的连续区间；失败回滚计数器和整批消息。`src/agents/chat_backend/conversation.py::MessageStore.add_batch`
- [FACT] `MessageStore.list()` 的无 limit 查询按 sequence 正序；有 limit 时 SQL 先倒序取最新 N 条，再在 Python 中反转。`page()` 以 `sequence < before_sequence` 排他过滤，取 `page_size + 1` 判断是否还有更早记录，最终页仍按 sequence 正序。limit 必须为正，page size 最大 100。`src/agents/chat_backend/conversation.py::MessageStore.list`、`page`、`_read_messages`
- [FACT] `MessageStore.pop_last()` 在事务中先确认父会话存在，再用一条参数化 `DELETE` 子查询按最大 sequence 删除并返回消息；空会话返回 `None`。`clear()` 删除该会话全部消息但保留父会话及其计数器，因此已用 sequence 不会复用。`src/agents/chat_backend/conversation.py::MessageStore.pop_last`、`clear`
- [FACT] `PostgresSession.get_items()` 对非正 limit 直接返回空列表，其他情况调用 Store 并映射为正序 `ChatMessage`；`add_items()` 复用 `session_item_to_message()` 后批量写入；`pop_item/clear_session` 直接对应 Store。它无需继承 `SessionLike`，调用方可显式放入 `RunConfig.session`。`src/agents/chat_backend/postgres_session.py::PostgresSession`、`src/agents/run_config.py::SessionLike`
- [FACT] 真实 Runner + PostgreSQL 检查重建了连接池、Store、Session、Agent 和模型对象，只复用数据库与 conversation UUID；第二轮模型输入等于第一轮已提交完整轨迹加第二轮 user，最终数据库 sequence 连续且每条只保存一次。这是重新创建进程级依赖的等价检查，不声称实际启动了第二个操作系统进程。`tests/test_chat_backend_migration.py::MigrationRunnerPostgresTests.test_runner_recovers_postgres_history_with_new_dependencies`
- [FACT] `RunResult.to_input_list()` 无条件把非空 input 转成 user，把 `tool_call`、`tool_result`、`tool_error`、`final_output` 投影成其余 session 消息；失败工具的 `tool_error` 以 `tool_response` 保存，闭合对应调用。`model_error` 不投影，因此直接使用持久 Session 的普通模型错误结果仍留下单独 user；本课不修改 Runner 保存语义。`src/agents/result.py::RunResult.to_input_list`、`src/agents/run_loop.py::_save_result_to_session`
- [FACT] `CommitOnSuccessSession.add_items()` 只转成并积累 `ChatMessage`；`commit(final_answer, stored_answer=None)` 拒绝重复提交，也拒绝空批次、非 assistant 结尾、正文与最终答案不一致或空白替换。可选替换只改末条 assistant，检查通过后才调用一次 `MessageStore.add_batch()`。`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession`
- [FACT] `ChatService.send_message()` 去除首尾空白，拒绝空正文和超过 20,000 字符的正文；先在线程中读取会话，archived 立即抛 `ConversationArchivedError`，active 才继续检索和本轮 Runner。未知会话保持 `ConversationNotFoundError`，运行失败映射为 `ChatRunError`，读写失败映射为带安全正文和原异常链的 `ChatPersistenceError`。`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] 每次服务调用都执行零参数 `agent_factory()`，取得新的 Agent 与模型适配器；对话历史只由本次 `PostgresSession.get_items()` 提供。基础 `RunConfig` 存在时用一次 `dataclasses.replace()` 替换 session，并把原外部 chunks 与本轮旧记忆或统一检索 chunks 合成新 tuple；其他字段保持不变。旧记忆检索与统一检索不能同时注入。`src/agents/chat_backend/service.py::ChatService.__init__`、`send_message`、`src/agents/models.py::OpenAIResponsesModel`
- [FACT] Runner 阶段的 `CancelledError` 原样传播且缓冲不提交；数据库提交通过 `asyncio.to_thread()` 避免阻塞事件循环，但工作线程里的同步事务一旦开始，取消等待不能强制中止它。本模块没有同会话串行队列或幂等键。`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] `MessageCreate` 去除首尾空白并限制 1..20,000 字符；`HistoryQuery` 保留正数 `before_sequence`。会话/记忆/知识列表使用 `CursorQuery` 的 `page_size + before_created_at + before_id`，两个时间游标必须同时出现；会话列表可按 status 过滤。所有请求继承严格 Pydantic 配置，额外字段拒绝。`src/agents/chat_backend/http.py::MessageCreate`、`HistoryQuery`、`CursorQuery`、`ConversationListQuery`
- [FACT] HTTP 响应 schema 与领域 dataclass 分开，转换函数逐字段复制允许公开的数据；`ConversationResponse` 不公开内部 next sequence，记忆不公开 fingerprint/embedding，知识不公开 chunk 向量，Citation 不带检索正文。`src/agents/chat_backend/http.py::ConversationResponse`、`MemoryResponse`、`KnowledgeResponse`、`CitationResponse`
- [FACT] 所有 HTTP 错误使用 `ErrorResponse` 的 `code/message/details?` 形状。请求校验 details 只保留 location/type；归档为 409、资源无结果为 404、领域输入为 422、数据库不可用为 503、未知异常记录日志并返回固定 500。响应不包含原始输入、SQL、DSN 或异常正文。`src/agents/chat_backend/http.py::ErrorResponse`、`_validation_error`、`_http_error`
- [FACT] `register_error_handlers()` 只把校验和既有异常类型绑定到传入 app；`CancelledError` 继承 `BaseException`，不在注册的 `Exception` 处理范围。`agents.chat_backend` 现在公开唯一 HTTP 构造入口 `create_app()`，不公开每个 schema、route 闭包或 `main()`。`src/agents/chat_backend/http.py::register_error_handlers`、`src/agents/chat_backend/__init__.py::__all__`
- [FACT] `create_app(settings, agent_factory, run_config=None, memory_agent_factory=None, embedding_client=None, model_profile_store=None)` 构造 Database、MigrationRunner、五个数据库 Store、可选 MemoryExtractionService、HybridRetrievalService/KnowledgeIndexer、ChatService 和 PlanningService；构造阶段不打开连接、不迁移、不调用 Agent factory/embedding。lifespan 在线程中 open/upgrade，后台串行启动 `recover_running()`，退出时取消并等待应用级任务后 finally close。`src/agents/chat_backend/http.py::create_app`、`lifespan`
- [FACT] 生产 HTTP app 有 35 条 API route 和 3 条静态 route，其中 Planning 占最新查询、草案、批准、工具审批、重试、修订和取消 7 条。同步数据库 route 为普通 `def`，聊天和 Planning SSE route 为 `async def`；知识 reindex 缺少 indexer 时返回稳定 503。`src/agents/chat_backend/http.py::create_app`、`send_message`、`planning_stream`、`reindex_knowledge`
- [FACT] CORS 仅在 `cors_origins` 非空时加入，允许精确本机 origin、GET/POST/PATCH/DELETE 和 Content-Type，拒绝 credentials/通配符。console script `my-agent-chat-http` 指向 `main()`，后者按设置 host/port 启动 Uvicorn，并用零参数 `chat_agent` 工厂保持每条聊天创建新 Agent；该工厂先解析激活 Profile，再构造 Agent。`src/agents/chat_backend/settings.py::_origins`、`src/agents/chat_backend/http.py::create_app`、`main`、`pyproject.toml::project.scripts`
- [FACT] Windows 启动器固定使用当前本机 Compose 默认值 `myagent/myagent-local/55432`，把相同值导出为 `MYAGENT_POSTGRES_DSN`，并把已解析项目根导出为必填 `MYAGENT_WORKSPACE_ROOT` 后启动 HTTP 服务；模型 API Key 仍只来自系统钥匙串。健康判定同时要求 `/health` 的 `service=postgres` 与 `status=ready`，所以重复点击不会启动第二个服务。运行日志位于 `%LOCALAPPDATA%/MyAgent/logs`，仓库和浏览器存储不保存启动日志。`scripts/start_myagent.ps1`、`compose.yaml`、`src/agents/chat_backend/http.py::health`
- [FACT] 启动器只启动不关闭：不注册 Windows 服务或开机任务，不执行 `down`、`Remove-Item`、`Stop-Process` 或 `--remove-orphans`。若 8000 已监听但 MyAgent health 不成立，返回失败并保留占用进程。`scripts/start_myagent.ps1`
- [FACT] M06 最终 Gate 覆盖 001..007、会话管理/级联、消息批次与并发、记忆和知识管理、统一检索/引用、安全错误、浏览器/OpenAPI 合同、故障注入及真实 PostgreSQL/Uvicorn/TCP HTTP 闭环。目标为 `39 passed, 1 skipped, 182 subtests passed`，相关回归 `286 passed, 1 skipped, 900 subtests passed`，真实服务 `3 passed`；真实 OpenAI 因无凭据未运行。`tests/test_chat_backend_migration.py`、`tests/test_chat_backend_http.py`、`tests/test_chat_backend_system.py`

## Chat Backend 长期记忆契约

- [FACT] `MemoryKind` 只接受 fact/preference/instruction，`MemoryScope` 只接受 global/conversation，`MemoryStatus` 只接受 active/disabled。`MemoryRecord` 是数据库确认后的冻结值，包含来源、fingerprint、时间和可选 `supersedes_id`。`src/agents/chat_backend/memory.py::MemoryRecord`
- [FACT] `MemorySource` 的 conversation/user/assistant UUID 必须引用同一个 conversation 的已提交 user 与 assistant 消息；应用层先校验，003 的两个复合外键再做最终防线。`src/agents/chat_backend/memory.py::_validate_source`、`src/agents/chat_backend/migrations/003_long_term_memories.sql`
- [FACT] 正文做 NFC 和空白归一化，tags 去重排序，confidence 必须是有限 0..1；fingerprint 只用标准库 SHA-256 对 scope 与规范化正文计算，因此 kind、tags 和 confidence 不会把同一正文变成另一条 active 事实。global 精确事实全局唯一，conversation 精确事实只在来源 conversation 内唯一；这不是语义去重。`src/agents/chat_backend/memory.py::_normalize_content`、`_normalize_tags`、`memory_fingerprint`
- [FACT] `MemoryStore.create()` 在唯一冲突时返回现有记录且不增加 epoch；update/disable/delete 只有真实变化才增加，supersede 在一个事务里创建新记录、停用旧记录、写关系并只增加一次。任何失败都不留下半成品或 epoch 变化。`src/agents/chat_backend/memory.py::MemoryStore.create`、`update`、`disable`、`delete`、`supersede`
- [FACT] `MemoryStore.list()` 使用 `(created_at, id)` 排他游标，page size 为 1..100；status/scope/source 过滤由参数化 SQL 完成，不使用 OFFSET。`src/agents/chat_backend/memory.py::MemoryStore.list`
- [FACT] `MemoryCandidate` 在结构化模型输出之后仍要通过固定字段、最多 8 条、kind/scope、正文/tags/confidence、可选 UUID 和明显凭据检查。`MEMORY_EXTRACTION_SCHEMA`、解析 helper 与正则不从子包根公开。`src/agents/chat_backend/memory_extraction.py::parse_memory_candidates`、`src/agents/chat_backend/__init__.py::__all__`
- [FACT] `MemoryExtractionService.extract()` 只接收本轮两段正文，使用无 Session 的现有 Runner；取消传播，其余失败转成不带原文的 `MemoryExtractionError`。`save_candidates()` 保持输入顺序，领域拒绝进入 `ExtractionReport`，基础设施异常继续上抛。`src/agents/chat_backend/memory_extraction.py::MemoryExtractionService`
- [FACT] `ChatService.send_message()` 只在消息批次提交并核对真实 user/assistant 行后提取；普通提取/保存失败不会撤销已提交聊天或改变 reply，日志不写正文、候选、DSN、SQL 或供应商异常正文。`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] 上述 003/004 与提取合同是 M03 基线；当前已经有 M04 的 embedding、保守语义去重、记忆检索/提示注入和独立知识 RAG，具体不变量见下一节。`src/agents/chat_backend/embedding.py`、`retrieval.py`、`knowledge.py`

## M04 Embedding、检索与知识状态契约

- [FACT] `EMBEDDING_DIMENSIONS` 唯一固定为 1024；一个 embedding 是 1024 个有限 float 的 tuple，一个 batch 保持输入数量和 response index 顺序。空输入返回空 tuple；空文本、重复/越界 index、少项、多项、错维度、NaN 或 Infinity 使整批失败。`src/agents/chat_backend/embedding.py::EMBEDDING_DIMENSIONS`、`OpenAIEmbeddingClient.embed`
- [FACT] 记忆向量状态由 `embedding/embedding_model/embedded_at` 三项组成，必须同时为空或同时存在。`MemoryEmbeddingTarget` 携带 id、正文和 fingerprint；`save_embeddings()` 在同一事务逐条锁定并重新核对 active/fingerprint，任一冲突整批失败且 epoch 不变。`src/agents/chat_backend/migrations/005_memory_embeddings.sql`、`src/agents/chat_backend/memory.py::MemoryEmbeddingTarget`、`MemoryStore.save_embeddings`
- [FACT] 记忆检索查询必须是非空字符串且 conversation id 为 UUID；limit 为正的非 bool int。关键词 SQL 转义 `%`、`_` 和 `\`，只返回 active 且 scope 可见记录；向量 SQL 还要求 embedding model 一致，并按精确 cosine distance、id 排序。`src/agents/chat_backend/memory.py::_keyword_pattern`、`MemoryStore.search_keyword`、`search_vector`
- [FACT] `ContextChunk` 是冻结值；`RunConfig.context_chunks` 与 `RunContextWrapper.context_chunks` 拒绝 list、非 chunk 和 `has_content()` 为假的条目。`build_turn_context()` 在既有 chunks 后追加外部 tuple，再通过 `TurnContextBundle.to_messages()` 的既有 priority 排序生成消息。检索 chunks 只存在于当前 run context，不写入 `SessionLike`。`src/agents/context_chunks.py::ContextChunk`、`_validate_context_chunks`、`build_turn_context`
- [FACT] `KnowledgeSource` 是唯一原文事实，状态只为 pending/indexed；`KnowledgeChunkDraft` 必须有从 0 连续的 ordinal、合法 `[start,end)`，并满足原文切片等于正文。metadata 必须是 JSON object 且序列化后不超过 16 KiB。`src/agents/chat_backend/knowledge.py::KnowledgeSource`、`KnowledgeChunkDraft`、`_normalize_metadata`
- [FACT] `KnowledgeStore.replace_chunks()` 在事务前校验全部 draft/vector/model，事务内锁 source 并再次校验原文范围；成功后旧 chunks 被整体替换，source 变为 indexed，model/indexed_at 一起写入并只增加一次 epoch。任一失败由事务回滚到旧索引状态。`src/agents/chat_backend/knowledge.py::_prepare_chunk_replacement`、`KnowledgeStore.replace_chunks`
- [FACT] RRF 固定使用 `1/(60+rank)`，rank 从 1 开始，同一 identity 每路只取首次出现；并列按最佳名次和稳定 identity。两种 Retrieval Service 都只选择完整条目；记忆 chunk priority 47，知识 chunk priority 46。`src/agents/chat_backend/retrieval.py::reciprocal_rank_fusion`、`_select_with_budget`
- [FACT] M04 完成时，知识只在调用方显式调用 `KnowledgeRetrievalService` 并把结果放进 `RunConfig.context_chunks` 时进入模型；`ChatService` 不自动加入知识，当时没有四路最终融合、citation、文章比例、reranker、query rewrite、ANN、外部向量库、自动采集或后台 reindex。当前 M05 增量契约见下一节。`src/agents/chat_backend/retrieval.py::KnowledgeRetrievalService`、`src/agents/chat_backend/service.py::ChatService`

## M05 Lesson 01 统一检索输出契约

- [FACT] `RetrievalHit` 是冻结值；`source_type` 只允许 memory/knowledge，`item_id` 必须是 UUID，正文必须非空。memory 的 `source_id/title` 必须同时为空；knowledge 必须有 UUID source id 和非空标题。`src/agents/chat_backend/retrieval.py::RetrievalHit.__post_init__`
- [FACT] `Citation` 只含 `marker/source_type/item_id/source_id/title`，没有检索正文；`RetrievalBundle` 只含不可变 `context_chunks/citations` tuple。三个值从 `agents.chat_backend` 公开，私有 `_build_bundle()` 不公开。`src/agents/chat_backend/retrieval.py::Citation`、`RetrievalBundle`、`src/agents/chat_backend/__init__.py`
- [FACT] `_build_bundle()` 不排序、不访问 Store，也不执行 RRF；它只消费调用方给出的当前顺序。全局条数限制按选中命中数计算，字符限制覆盖安全说明、分隔符、marker、标题和正文；超长条目被完整跳过，后续条目仍可尝试。`src/agents/chat_backend/retrieval.py::_build_bundle`
- [FACT] marker 只在命中真正装入时提交，memory 与 knowledge 分别从 `[M1]`、`[K1]` 连续编号。非空结果生成一个 name 为 `hybrid_retrieval`、source 为 `hybrid.retrieval`、priority 46 的 user-role `ContextChunk`；空结果不生成只有说明文字的消息。`src/agents/chat_backend/retrieval.py::_build_bundle`
- [FACT] Lesson 01 完成时尚无生产源码 caller；Lesson 02 让统一检索服务复用该构建函数，Lesson 04 让它经 ChatService 进入 RunConfig 与 HTTP 回复，Lesson 05 又把同一服务接入默认 `create_app()`。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`、`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/http.py::create_app`

### Lesson 02 四路统一检索合同

- [FACT] `HybridRetrievalService.retrieve(text, conversation_id, source_ids=None)` 返回 `RetrievalBundle`。问题必须是非空字符串；`top_k` 必须是正整数且不能是 bool；模型非空，两个上下文预算是非负整数；知识范围复用 `_normalize_source_ids()`。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.__init__`、`retrieve`
- [FACT] 查询顺序固定为记忆关键词、知识关键词、一次 query embedding、记忆向量、知识向量；空知识范围只跳过知识关键词和知识向量，不跳过记忆，也不跳过记忆需要的 query embedding。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`
- [FACT] Store 返回值只复制 `RetrievalHit` 所需安全字段；记忆 identity 为 `memory:<memory_id>`，知识 identity 为 `knowledge:<chunk_id>`，所以相同 UUID 不碰撞。四个结果列表复用 `reciprocal_rank_fusion()`，融合后只调用一次 `_build_bundle()`，不存在来源比例或独立预算。`src/agents/chat_backend/retrieval.py::_memory_retrieval_hit`、`_knowledge_retrieval_hit`、`HybridRetrievalService.retrieve`
- [FACT] Embedding 请求或数量合同错误会同时关闭两路向量并保留关键词。之后两路向量各自只吞掉对应校验和 `psycopg.Error`；关键词错误、未知数据形状与编程错误继续抛出。旧 `MemoryRetrievalService`、`KnowledgeRetrievalService` 行为不变。`src/agents/chat_backend/retrieval.py::HybridRetrievalService.retrieve`
- [FACT] `HybridRetrievalService` 是 `agents.chat_backend` 稳定公开接口，既能由调用方直接注入 ChatService，也已成为 `create_app()` 在 embedding client 存在时的默认检索服务。旧独立 Retrieval Service 与 ChatService 旧注入参数保持兼容。`src/agents/chat_backend/__init__.py`、`src/agents/chat_backend/service.py::ChatService`、`src/agents/chat_backend/http.py::create_app`

### Lesson 03 本轮引用解析合同

- [FACT] `_CITATION_MARKER_PATTERN` 是模块私有的预编译标准库正则，只扫描方括号内的大写 M/K 和不以 0 开头的正整数；`[M0]`、`[K01]`、小写、负数、缺括号或尾随字符不会命中。`src/agents/chat_backend/retrieval.py::_CITATION_MARKER_PATTERN`
- [FACT] `parse_citations(answer, citations)` 要求 answer 是字符串；citations 是当前请求允许列表。函数先建立 `marker -> Citation` 映射，重复 marker 会抛出 `ValueError`，不会悄悄覆盖。`src/agents/chat_backend/retrieval.py::parse_citations`
- [FACT] 正则按回答从左到右扫描；marker 必须同时存在于当前映射且尚未返回，最终 tuple 顺序由回答首次出现位置决定。返回的是传入白名单中的原对象，不根据 marker 构造新 Citation，所以未知或旧请求 marker 无法获得来源数据。`src/agents/chat_backend/retrieval.py::parse_citations`
- [FACT] 解析函数不修改 answer，不访问 Store、Session 或网络，也不根据标题和自然语言归因。函数从 `agents.chat_backend` 公开，正则保持私有；Lesson 04 已在聊天成功提交并核对最终消息后调用它。`src/agents/chat_backend/__init__.py`、`src/agents/chat_backend/service.py::ChatService.send_message`

### Lesson 04 Chat 与 HTTP 引用合同

- [FACT] `ChatReply.citations` 是放在旧四字段之后的默认空 tuple，因此旧位置参数构造不变。`ChatService.__init__()` 同样只在旧参数末尾追加 `hybrid_retrieval`；它与 `memory_retrieval` 同时存在时构造立即失败。`src/agents/chat_backend/service.py::ChatReply`、`ChatService.__init__`
- [FACT] 同一 `RetrievalBundle` 同时提供本轮上下文和 Citation 白名单。chunks 只合入 `RunConfig.context_chunks`；Citation 只在 Runner final、提交和最终数据库行检查全部成功后解析，之后才构造 reply 和运行 best-effort 记忆提取。`src/agents/chat_backend/service.py::ChatService.send_message`
- [FACT] `CitationResponse` 只公开 marker、source type、item id、可选 source id 和标题；`citation_response()` 明确逐字段复制，`ChatReplyResponse` 默认空 list。领域 Citation 以后增加字段不会自动扩大 HTTP 响应。`src/agents/chat_backend/http.py::CitationResponse`、`citation_response`、`ChatReplyResponse`
- [FACT] Lesson 04 没有改变 `create_app()` 的依赖组合、lifespan、路由数量、健康检查或 Session 存储；默认 HTTP 仍使用旧 `MemoryRetrievalService`。新 Lesson 05 会只替换应用组合。`src/agents/chat_backend/http.py::create_app`

### Lesson 05 默认应用组合合同

- [FACT] `create_app()` 创建的 `MemoryStore`、`KnowledgeStore` 和可选 `HybridRetrievalService` 使用同一个 Database；统一检索实例同时保存到 `app.state.hybrid_retrieval` 并传给 `ChatService._hybrid_retrieval`。`app.state.memory_retrieval` 不再建立，旧类本身未删除。`src/agents/chat_backend/http.py::create_app`
- [FACT] `embedding_client is None` 是唯一关闭条件；非空时沿用 `embedding_model/retrieval_top_k/max_context_chunks/max_context_chars`。构造只保存对象和校验普通值，不访问数据库或 embedding provider。`src/agents/chat_backend/http.py::create_app`、`src/agents/chat_backend/retrieval.py::HybridRetrievalService.__init__`
- [FACT] `memory_retrieval_enabled` 保留旧名称；`main()` 用它决定是否创建 `OpenAIEmbeddingClient`，开启后 `create_app()` 组合的是统一检索。配置、lifespan、健康检查、路由和 Session 合同均未变化。`src/agents/chat_backend/settings.py::AppSettings`、`src/agents/chat_backend/http.py::main`、`create_app`

### Lesson 06 最终跨层合同

- [FACT] 默认 HTTP 请求的允许引用集合与送入 Runner 的 `RetrievalBundle` 来自同一次检索；只有回答中严格匹配该集合的 marker 才进入 `ChatReply` 和 HTTP，未知、畸形及重复 marker 不会扩大结果。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/retrieval.py::parse_citations`、`src/agents/chat_backend/http.py::chat_reply_response`
- [FACT] 检索资料只通过 `RunConfig.context_chunks` 进入当轮模型输入；成功提交保留 Runner 产生的 user、工具和最终 assistant 轨迹，不保存检索 `ContextChunk`。跨层 Gate 同时检查了模型可见与 Session 不可见两侧。`src/agents/chat_backend/service.py::ChatService.send_message`、`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession.commit`、`tests/test_chat_backend_http.py::HttpAppTests.test_m05_gate_runs_hybrid_context_and_returns_current_citations`
- [FACT] M05 最终没有增加缓存、外部向量库、第二个 Session 或第二条 Agent loop；真实 PostgreSQL 与 OpenAI embedding smoke 仍是条件检查，本次因 DSN、凭据和 Docker daemon 不可用而未运行，不能写成通过。

## 单 run 控制不变量

- [FACT] `CancellationToken.cancel()` 只有首次调用返回 True，并同时写原因、UTC 展示时间和 `perf_counter()` 耗时起点；`wait()` 可供同步 worker 观察，非法 timeout 在等待前失败。`src/agents/run_control.py::CancellationToken`
- [FACT] controller 与 token 必须拥有相同 run id；steer 以 `steer-N` 连续编号并按 FIFO 原子 drain，finished 后拒绝 steer。`src/agents/run_control.py::RunController.__init__`、`steer`、`_drain_steers`
- [FACT] `_EventChannel` 只接收已编号 `RunEvent`，不修改事件；满队列等待并记录不超过 maxsize 的最大深度，terminal 必须先 yield 再结束，terminal/close 后拒绝继续 put。`src/agents/run_control.py::_EventChannel.put`、`max_depth`、`__aiter__`
- [FACT] consumer 显式关闭 iterator 会 set consumer-closed，满队列 put 的等待竞争取消并 await 临时 task；单独 `async for ... break` 不保证立即触发这一 finally。`src/agents/run_control.py::_EventChannel._send_or_consumer_closed`、`__aiter__`
- [FACT] `_RunRuntime.emit()` 先让 reducer 构造事件，再经共同 `_deliver()` await channel；只统计成功入队的最长 publish latency，terminal 无论入队成功或 consumer 已退出，controller 都在 finally 中 finish。`src/agents/run_control.py::_RunRuntime.emit`、`_deliver`
- [FACT] `shutdown_after_cancel()` 要求 token 已取消；先按配置 grace 等待 producer，超时后 cancel 并 gather，最后只在 reducer 尚无终态时补发取消事件。`src/agents/run_control.py::_RunRuntime.shutdown_after_cancel`
- [FACT] token/controller 使用线程同步原语；channel/runtime 的 asyncio 对象仍只在所属 loop 使用，worker 通过 `_LoopEventBridge` 的 `run_coroutine_threadsafe()` 跨线程发布。`src/agents/run_control.py::CancellationToken`、`_LoopEventBridge`
- [FACT] 合作取消检查位于每轮模型输入前、模型返回后、每个普通工具请求前、handler 前与 handler 返回后；迟到的模型响应或工具结果不会写入 `RunState.new_items`。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/model_turn.py::_run_model_turn_impl`、`src/agents/tool_execution.py::_execute_tool_call_impl`
- [FACT] `RunCancelledError` 必须先于通用异常分支处理；取消不会调用 `record_model_error()` 或 `record_tool_error()`。模型错误返回失败 `RunResult`，并产生相邻的 `RunErrorEvent`、`RunFailedEvent`；意外 producer 异常产生同一事件对后重新抛出原异常。`src/agents/run_control.py::_run_agent_loop_with_events`、`src/agents/run_loop.py::_run_agent_loop_impl`
- [FACT] steer 只在 turn 完成、取消与 limit 检查之后、下一模型输入之前应用；当前批次写进一个 `StepRecord.messages`，不增加 `RunState.steps_taken`。terminal 会清空未应用 steer。`src/agents/run_loop.py::_apply_pending_steers`、`src/agents/run_control.py::RunController._finish`
- [FACT] `RunStream` 只能被一个 consumer 迭代；提前退出的可靠清理契约是 `async with` 或显式 `aclose()`。裸 `async for ... break` 不承诺自动关闭。`src/agents/runner.py::RunStream.__aiter__`、`aclose`
- [FACT] `SyncRunCollector.collect()` 完整消费同一 stream，并直接返回 producer 写入的原 `RunResult`；它不缓存事件、不重建结果，也不包装 producer 异常或取消。`Runner.run_async()` 只把 `run_stream()` 交给该 collector；`run_sync()` 继续用 `asyncio.run()` 桥接，并在当前线程已有 event loop 时抛 `RuntimeError`。`src/agents/runner.py::SyncRunCollector.collect`、`Runner.run_async`、`run_sync`
- [FACT] 指标键为 `max_publish_latency_seconds`、`max_queue_depth` 和仅取消时存在的 `cancel_to_terminal_seconds`；全部属于单个 runtime，并写入私有 `event_stream` span data，不是公共 metrics API。`src/agents/run_control.py::_RunRuntime.metrics_snapshot`、`src/agents/runner.py::RunStream._produce`

## Approval、guardrail 与错误契约

- [FACT] 审批键是 `(tool_name, call_id)`，状态是 pending/approved/rejected，并可导入/导出 snapshot。`src/agents/run_context.py::RunContextWrapper._approval_key`、`export_tool_approvals`
- [FACT] 审批回调异常或非法结果按“需审批”处理；resume 只执行已决定的 pending 调用。`src/agents/tool_runtime.py::requires_tool_approval`、`src/agents/run_resume.py::resume_pending_tool_approvals`
- [FACT] agent-level tripwire 停止运行；output tripwire 清除该步骤 final output。`src/agents/run_loop.py::_run_input_guardrails`、`_run_output_guardrails`、`_clear_final_output`
- [FACT] 工具 guardrail 可拒绝为 observation 或抛异常；工具超时可返回文本或抛 `ToolTimeoutError`。`src/agents/tool_execution.py::_execute_tool_call_impl`、`src/agents/tool_runtime.py::run_with_timeout`
- [FACT] 模型异常终止 loop 并在内部事件 producer 中落为 failed；普通工具异常记录后继续；取消异常不进入这两条错误记录路径；handoff 异常可向调用方传播。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/run_control.py::_run_agent_loop_with_events`、`src/agents/run_steps.py::_execute_handoff_impl`

## 持久化边界

| 介质 | 写入者与契约 |
|---|---|
| session JSON | [FACT] `JsonSession._save()` 写 `version: 1` + session。`src/agents/memory.py::JsonSession._save` |
| coding state JSON | [FACT] envelope 保存 setup 元数据、审批摘要、`RunResult.to_state()` v4 和验证配置；只间接保存可选 task id/revision，不保存 Store 或 TaskEvent。`src/agents/coding_state.py::CodingRunStateStore.save_pending_result`、`src/agents/result.py::RunResultBase.to_state` |
| trajectory JSONL | [FACT] 从 `RunResult.new_items` 派生事件，按 fresh/resume 覆盖或追加。`src/agents/trajectory.py::trajectory_events_from_result`、`write_trajectory_jsonl` |
| trace JSONL | [FACT] 已注册 `JSONLTracingExporter` 时逐项追加。`src/agents/tracing.py::JSONLTracingExporter.export` |
| TaskEvent（无内置介质） | [FACT] `task_event_to_dict()` 只返回 JSON-compatible 字典，实际保存位置和写入时机由调用方决定；现有 Session/coding state/trajectory/trace writer 均不消费它。`src/agents/task_events.py::task_event_to_dict` |
| PlanningRun（PostgreSQL） | [FACT] `PlanStore` 把 PlanDefinition schema v1、完整 TaskEvent journal、可选 RunState snapshot 和安全运行快照写入 009 的 planning row；Markdown 仍只用于审阅，不能恢复执行。`src/agents/chat_backend/planning_store.py::PlanStore`、`src/agents/chat_backend/migrations/009_planning_runs.sql` |

- [INFERENCE] 这些 writer 都直接写目标文件，未发现原子替换或文件锁；并发写同一路径存在冲突风险。依据：上述四个写入符号。
- [UNKNOWN] 未发现 PlanningRun retention policy、自动归档或应用层加密；数据库传输/磁盘加密由部署环境决定。检索范围：`src/agents/chat_backend`

## 模型容错配置

- [FACT] `ModelSettings` 的 connect/read/total timeout、attempt、backoff、jitter、retry budget、stream buffer 和两种 fallback 设置都使用 `None` 表示“当前配置层未覆盖”；`resolve()` 仍只复制 override 中的非 `None` 字段。`src/agents/model_settings.py::ModelSettings`、`ModelSettings.resolve`
- [FACT] `ModelSettings.__post_init__()` 在构造和 `replace()` 合并时拒绝 bool 冒充数字、非有限数、越界值、空白备用模型，以及 backoff/budget 与有效默认值的矛盾关系。`src/agents/model_settings.py::ModelSettings.__post_init__`
- [FACT] 公共 `run_model_request()` 已消费 attempt、delay、budget、硬 total deadline、可选 token、stream buffer、一次同模型非流降级和一个显式备用模型名；OpenAI 流式/非流入口共享 connect/read 有效值形成的公开 `openai.Timeout`，write 沿用 read、pool 沿用 connect，并固定 `max_retries=0`。生产模型回合已调用该执行器，但仍不支持多个备用模型、路由或 provider 切换。`src/agents/model_retry.py::run_model_request`、`src/agents/models.py::OpenAIResponsesModel._open_response_stream`、`get_response_for_request`、`src/agents/model_turn.py::_run_model_turn_async`

## 跨模块不变量与待复核

### 模型 Profile、SSE 与浏览器安全契约

- [FACT] `ModelProfile` 是不可变快照，公开 JSON 只有 id/name/provider/protocol/model/base URL/active/has_api_key；API Key 只写入系统 keyring，创建或删除涉及索引与凭据两步时会在第二步失败后回滚第一步。`src/agents/chat_backend/model_profiles.py::ModelProfile`、`ModelProfileStore.create`、`update`、`delete`
- [FACT] 前端 Profile 草稿测试只发送 provider/model/base URL/API Key；后端也兼容完整表单已有的可选 name，但仍拒绝其他未知字段。name 只用于草稿快照，不进入模型请求。`src/agents/chat_backend/static/app.js::testProfile`、`src/agents/chat_backend/http.py::ModelProfileTestRequest`、`test_model_profile`
- [FACT] 模型发现只返回字符串 ID，不返回 Key 或厂商原始对象；同步 SDK 列表在线程中执行，关闭自动重试并设 10 秒超时。当前聊天工作台按名称排除明显 embedding/image/audio/rerank 等非聊天模型；兼容接口没有统一能力元数据，因此手动 ID 保留为回退。`src/agents/chat_backend/http.py::_list_model_ids`、`list_model_profile_models`
- [FACT] Profile 测试与模型发现把已分类错误映射为固定的鉴权、额度、限流、连接、厂商服务或请求错误提示；供应商原始异常、request id 和 Key 不进入响应。`src/agents/chat_backend/http.py::_profile_error_message`
- [FACT] 生产 `main.chat_agent()` 在 `build_chat_agent()` 前解析激活 Profile；已配置第三方模型时不会先初始化默认 OpenAI client。`configured_agent_factory` 仍在每个请求开始时取得最终不可变 Profile 值并 clone，因此运行中的模型不随随后激活操作改变。`src/agents/chat_backend/http.py::main`、`chat_agent`、`configured_agent_factory`
- [FACT] Profile base URL 拒绝凭据、query、fragment；远端只允许 HTTPS，HTTP 只允许 localhost、127.0.0.1 或 `::1`，满足 Ollama 本机调用而不允许明文远端密钥。`src/agents/chat_backend/model_profiles.py::_validate_base_url`
- [FACT] `ModelTextDeltaEvent` 保留 run/turn/step/request/attempt identity 和纯文本 delta；它只用于实时展示，不参与 RunResult、snapshot 或 session 重建。`src/agents/events.py::ModelTextDeltaEvent`、`src/agents/model_turn.py::_run_model_turn_async`
- [FACT] SSE 只发送 `start/status/delta/done/error`；`done` 必须在 `ChatService` 返回后发送，`error` 使用已有安全 `ErrorResponse`，供应商异常和密钥不出现在事件载荷。`src/agents/chat_backend/http.py::_sse`、`stream_message`、`_http_error`
- [FACT] `conversation_messages.run_trace` 是 JSON 数组，默认空列表，只接受至多 200 条白名单阶段、1–500 字符消息和可选整数 step；只允许写入助手消息。`StoredMessageResponse` 与 `ChatReplyResponse` 公开同一安全投影，旧消息以空列表兼容。`src/agents/chat_backend/migrations/008_message_run_trace.sql`、`src/agents/chat_backend/conversation.py::_normalize_run_trace`、`MessageStore.set_run_trace`、`src/agents/chat_backend/http.py::RunTraceEntryResponse`
- [FACT] 静态页面只同源请求；模型输出解析只识别段落、换行和围栏代码块并使用 `textContent`，不执行模型 HTML。API Key 保存成功后立即清空输入框，且脚本不使用 localStorage/sessionStorage/console。`src/agents/chat_backend/static/app.js::appendRichText`、`profilePayload`、`bindEvents`
- [FACT] Profile 仍是进程级全局激活状态，不增加 conversation 外键或第二套配置状态；一个已保存 Profile 可以创建并服务任意多个会话，模型选择只在每次消息开始时快照。`src/agents/chat_backend/model_profiles.py::ModelProfileStore.active`、`src/agents/chat_backend/http.py::configured_agent_factory`、`src/agents/chat_backend/static/app.js::renderConversationProfile`
- [FACT] 浏览器布局保持单一视口高度：`workspace` 中间行可收缩，`chat-stage` 独立纵向滚动且不允许横向溢出，composer 不随历史消息移出视口；该滚动条使用 `overflow-y: scroll`、14px WebKit 轨道/滑块和标准 `scrollbar-color` 始终提供可见位置。用户消息在消息列右侧反向排列，窄屏宽度受父容器限制。`src/agents/chat_backend/static/app.css`

- [FACT] workspace typed accessor 只接受字典 context 中类型正确的对象；`Workspace` 再执行 root/allowed/ignore 检查。`src/agents/run_context.py::RunContextWrapper._context_value`、`src/agents/workspace.py::Workspace.ensure_readable_path`
- [FACT] `RunStateSnapshot` writer 的 schema version 为 4；reader 接受 legacy 无版本、v1、v2、v3 与 v4。v2–v4 恢复 model attempts，v3/v4 恢复成对 task link，只有 v4 恢复可选的供应商私有 `model_state`，未知版本报错。`last_agent`/context 仍由恢复调用者重新提供。`src/agents/run_state.py::RUN_STATE_SNAPSHOT_SCHEMA_VERSION`、`run_state_snapshot_from_dict`、`RunState.from_snapshot`
- [FACT] snapshot attempt 字典保存 request/attempt identity、number、model、mode、outcome、duration、error/status、retry delay/reason、response id、usage 和 TTFT；可选 task id/revision 只定位一个节点。v4 的 `model_state` 仅供同一模型 adapter 恢复供应商协议状态；snapshot 仍不保存 provider event、TaskStore、TaskNode、TaskEvent、聚合器、controller、stream 或 span 对象。`src/agents/run_state.py::_model_attempt_to_state`、`RunStateSnapshot`
- [FACT] model span 保留 `non_stream_fallback_used`/`model_fallback_used`，并从相邻 attempts 推导一次实际 fallback 的 `fallback_reason`、`fallback_attempt_id`、`fallback_from/to_model`、`fallback_from/to_mode`；无相邻切换时这些定位字段均为 None。`src/agents/model_turn.py::_record_model_execution_span`
- [INFERENCE] snapshot 是 pending approval 的最小恢复点，而不是完整 Agent checkpoint；它不携带 Agent 对象或完整 session/context 实体。依据：`src/agents/run_state.py::RunStateSnapshot`、`src/agents/coding_cli.py::_run_state_from_state_envelope`
- [UNKNOWN] `handoff_depth` 已在 state 中声明，但未确认有递增或限制逻辑。`src/agents/run_state.py::RunState`
- [FACT] `trace_include_sensitive_data=False` 隐藏模型/工具部分敏感载荷，但 task/agent/handoff span 仍接收 task 文本。`src/agents/model_turn.py::run_model_turn`、`src/agents/tool_execution.py::execute_tool_call`、`src/agents/tracing.py::task_span`、`agent_span`、`handoff_span`
- [FACT] model span 的运行数据来自最终 `ModelAttemptRecord` 集合，包含 request/final attempt、attempt/retry 数量、最终 model/mode、response id、usage、TTFT、attempt duration、retry delay、fallback 标记和 terminal reason；失败同时保留原有结构化 span error。`src/agents/model_turn.py::_record_model_execution_span`、`src/agents/tracing.py::record_span_error`

### PlanningService 草案与安全投影

- [FACT] 稳定公共调用面包含 `PlanningService` 的 `draft/approve/decide_tool_approval/retry_current/revise/cancel/get_snapshot/get_latest_snapshot/recover_running`，`PlanningSnapshot`、两个结构化子投影、三个结果/进度值和两个安全错误。Service 可从 `agents.chat_backend` 导入，执行循环、Task 领取、schema、RunState 与资源 helper 只留在实现模块；顶层 `agents` 不重复导出。`src/agents/chat_backend/planning_service.py::__all__`、`src/agents/chat_backend/__init__.py::__all__`、`src/agents/__init__.py::__all__`
- [FACT] `PlanningSnapshot/PlanningStepSnapshot/PlanningApprovalSnapshot/PlanningDraftOutcome/PlanningRecoveryReport/PlanningProgressEvent` 都是冻结值；`PlanningDraftOutcome` 额外携带刚提交的助手消息 ID，供 HTTP 把安全运行轨迹写回既有 `conversation_messages.run_trace`，不复制正文或状态。快照不包含 model/workspace 原始快照、API key、env 值、完整 RunState、原始工具参数或模型响应。`src/agents/chat_backend/planning_service.py::PlanningDraftOutcome`、`PlanningSnapshot`、`_snapshot_from_run`
- [FACT] `PLANNING_DRAFT_SCHEMA` 约束根字段和计划字段形状；Chat Completions adapter 仅在 Agent 声明该 output type 时增加 JSON Schema 系统提示和 `response_format=json_object`。服务接受 SDK 已解码 Mapping 或可严格解成 object 的 JSON 字符串，仍要求 question/definition 二选一，并把 plan 交给 `plan_definition_from_dict()`；数组、Markdown、额外字段和非法计划继续拒绝。无效模型结果统一成为固定 `PlanningRunError`，不把原始结构或 `PlanValidationError` 正文放进公开错误。`src/agents/models.py::build_chat_completion_request_kwargs`、`src/agents/chat_backend/planning_service.py::PLANNING_DRAFT_SCHEMA`、`_parse_draft_output`、`PlanningService.draft`
- [FACT] `PlanningRun.objective` 是草案目标的权威值，每次 draft、补充、重试或修订调用都会把它注入规划 Agent 的系统指令。当前用户 content 不能替换这个目标；这覆盖了 `CommitOnSuccessSession` 在无效模型结果时不提交本轮消息的正常边界。`src/agents/chat_backend/planning_service.py::PlanningService.draft`、`src/agents/chat_backend/postgres_session.py::CommitOnSuccessSession`
- [FACT] `_safe_run_error()` 只接受五个固定内部错误码并返回短消息，不接受任意异常正文；`_public_run_error()` 让这些已知安全消息进入公开计划/步骤投影，任意其他已保存文本仍统一显示 `planning run could not continue`。`src/agents/chat_backend/planning_service.py::_safe_run_error`、`_public_run_error`、`_snapshot_from_run`
- [FACT] 新草案冻结 `ModelProfile.public_dict()` 与 `WorkspaceManifest.metadata()`；后续草案和普通步骤按冻结 id 取得当前密钥，只比较 id/provider/protocol/base URL/model/has-api-key 与完整 metadata。最终验证不读取模型，只核对完整 manifest metadata 并使用冻结 default command；profile active/name、密钥值和 env 值不参与漂移比较，当前 env 值只进入命令进程。`src/agents/chat_backend/planning_service.py::PlanningService._resolve_frozen_resources`、`_resolve_frozen_manifest`、`_frozen_test_command`、`_run_final_verification`、`src/agents/chat_backend/planning_store.py::PlanStore.create_draft`
- [FACT] question 不修改 PlanningRun row revision；plan 先通过 `submit_for_approval()` CAS 保存 definition，再提交 Markdown 消息。若后一阶段消息写入失败，权威计划仍为 awaiting approval，调用方收到安全持久化错误而不是伪造回滚。`src/agents/chat_backend/planning_service.py::PlanningService.draft`
- [FACT] `approve()` 先让 Store CAS 物化任务，再由 `_claim_task()` 把唯一 ready Task 写成 RUNNING 并第二次 CAS 保存；只有这两次成功后才调用 Runner。并发旧 revision 在外部执行前失败；步骤选择继续复用 `PlanExecution.next_ready()` 的 definition 顺序。`src/agents/chat_backend/planning_service.py::PlanningService.approve`、`_drive_running`、`_claim_task`
- [FACT] 普通步骤只接受去空白后 1..20000 字符的 `RunResult.final_output`；成功结果进入 TaskEvent，模型异常、非终答、超长结果或任一 tool_error 都保留 RUNNING Task 并把计划保存为 paused。`CancelledError` 原样传播，当前 running 保存点留给后续恢复。`src/agents/chat_backend/planning_service.py::PlanningService._run_current_step`
- [FACT] pending approval 经现有 `RunResult.to_state()` 与 v4 reader 得到 `RunStateSnapshot`，Store 再核对 snapshot task link；公开快照保留兼容用的 pending `(tool_name, call_id)`，并增加由现有安全函数生成的工具摘要。Chat Completions adapter 的私有 `model_state` 可保存工具交换所需的 `reasoning_content`，批准后先恢复再续跑；该字段不进入 Planning API、SSE、运行进度或最终回答。Shell/Test 摘要含命令、cwd 和风险，Patch 只含路径/操作，不返回完整 patch；其他工具只含工具名、调用 id 和原因。决定时必须命中精确键；reject 原因去空白后为 1..2000 字符，approve 不接受 rejection message。`src/agents/chat_backend/planning_service.py::_snapshot_from_run`、`PlanningService.decide_tool_approval`、`src/agents/models.py::OpenAIChatCompletionsModel.export_state`、`src/agents/approval_summaries.py::approval_summary_for_tool_call`
- [FACT] `awaiting_tool_approval -> running` 的 CAS 在 `Runner.resume_stream()` 前提交，并把数据库 snapshot 清空；CAS 失败不会执行工具。恢复结果可保存新的审批 snapshot、完成当前 Task 后继续下一步，或让当前 Task 保持 RUNNING 并 paused。fresh/resume 共用 `_save_step_result()`，没有第二份步骤结果规则。`src/agents/chat_backend/planning_service.py::PlanningService.decide_tool_approval`、`_resume_current_step`、`_save_step_result`、`src/agents/chat_backend/planning_store.py::PlanStore.save_execution`
- [FACT] final verification 和普通步骤一样必须先成为已保存的 RUNNING。唯一冻结命令通过且返回一条同命令结果时，final 才 COMPLETED、run 才 completed；命令失败保存 `planning verification failed`，环境/返回异常或 metadata 漂移保存 `planning verification could not run`，两者都保持 final RUNNING、run paused。完整输出、异常正文和 env 值都不进入 PlanningRun 或公开快照。`src/agents/chat_backend/planning_service.py::PlanningService._run_final_verification`、`_safe_run_error`
- [FACT] `retry_current()` 只允许 `paused -> running`，TaskEvent journal 和唯一 RUNNING Task 不变；转换先以调用方 `row_revision` 提交，之后普通步骤 fresh 执行，final 只执行冻结验证。Store 的 paused 来源也只允许保存到 running，防止借控制入口跳到其他状态。`src/agents/chat_backend/planning_service.py::PlanningService.retry_current`、`src/agents/chat_backend/planning_store.py::PlanStore.save_execution`
- [FACT] `revise()` 可从 awaiting approval/running/awaiting tool approval/paused 出发；旧版已完成 TaskEvent/result 原样保留，未完成节点追加 CANCELLED 事件，旧 run 变 superseded，新 run 以 version+1 和 row_revision=1 进入 drafting，并复用原安全资源快照。`src/agents/chat_backend/planning_store.py::PlanStore.revise`、`_cancel_unfinished_tasks`
- [FACT] `cancel()` 可关闭任一活动状态，取消未完成节点、清空审批 snapshot 和 last_error，并在同一事务把 Conversation.mode 还原 default。控制入口的原因/反馈去空白后必须为 1..20000 字符；Store 错误保持既有类型，其他异常只映射成固定安全错误。`src/agents/chat_backend/planning_service.py::_require_control_text`、`PlanningService.cancel`、`src/agents/chat_backend/planning_store.py::PlanStore.cancel`
- [FACT] `recover_running(limit)` 的 limit 必须是 1..100 的真正 int；候选唯一来自 `status='running' ORDER BY updated_at,id`。paused、awaiting tool approval 和终态不会自动执行；同步数据库调用放入线程，但外层逐条 await。`src/agents/chat_backend/planning_service.py::PlanningService.recover_running`、`src/agents/chat_backend/planning_store.py::PlanStore.list_recoverable_running`
- [FACT] `PlanningRecoveryReport` 只保存 scanned 数量和 recovered/isolated/deferred UUID tuple。正常离开 running 或并发方已收口记 recovered；确认损坏且隔离成功记 isolated；临时执行/数据库错误且仍需后续处理记 deferred，不包含错误正文、snapshot、工具参数或秘密。`src/agents/chat_backend/planning_service.py::PlanningRecoveryReport`、`PlanningService.recover_running`
- [FACT] `quarantine_running()` 是唯一不构造完整 PlanningRun 的写路径：它只接受可信 UUID，锁 raw row 并要求状态仍是 running，然后改 failed、写固定安全摘要、清 snapshot、递增 row revision，并在同事务恢复 Conversation.default。它不回放、修复或覆盖损坏 TaskEvent。`src/agents/chat_backend/planning_store.py::PlanStore.quarantine_running`

### Planning Web、配置与并发合同

- [FACT] `AppSettings.from_env()` 要求 `MYAGENT_WORKSPACE_ROOT` 指向已存在目录并保存绝对路径；可选 `MYAGENT_WORKSPACE_TEST_COMMAND` 必须是非空文本，未设置时为 `python -m pytest`。`create_app()` 把两者写入默认 `WorkspaceManifest`，计划创建时冻结该命令。直接构造 `AppSettings` 保留 `Path(".")` 与 `python -m pytest` 默认值。Windows 启动器写入项目根，并在调用方未设置验证命令时使用 `python scripts/verify_current_baseline.py`；该脚本只放行全绿或恰好属于登记集合的既有失败，任何新增失败、pytest 崩溃或无法解析的结果都返回非零。`src/agents/chat_backend/settings.py::AppSettings.from_env`、`src/agents/chat_backend/http.py::create_app`、`scripts/start_myagent.ps1`、`scripts/verify_current_baseline.py`
- [FACT] `run_test_command` 的工具 schema 会列出精确测试命令白名单；模型若要运行其他测试必须改用需要审批的 shell 工具，执行策略本身没有放宽。`run_shell_command` 的 schema 同时声明当前是 Windows `cmd.exe` 还是 POSIX shell，并要求优先使用独立 `cwd` 参数，避免在 Windows 生成 `/workspace`、`$(...)` 或 `head`。`src/agents/shell_tools.py::create_test_command_tool`、`create_shell_command_tool`
- [FACT] 普通 Planning 步骤的 Runner 输入包含自身 step id、instruction、completion criteria，以及所有已完成直接依赖的非空字符串结果；依赖结果总预算为 20000 字符并按直接依赖数量平均截断。缺失、未完成或无有效结果的直接依赖会在模型调用前使该步骤安全暂停，不会让模型自行重查无关文件。`src/agents/chat_backend/planning_service.py::_step_prompt`、`PlanningService._run_current_step`
- [FACT] `PlanStore.get_latest(conversation_id)` 按 version/id 降序读取一行，不过滤状态，因此刷新后既能恢复活动计划，也能展示 completed/cancelled/superseded 等终态；它不公开版本列表。`src/agents/chat_backend/planning_store.py::PlanStore.get_latest`、`src/agents/chat_backend/planning_service.py::PlanningService.get_latest_snapshot`
- [FACT] HTTP 修改入口全部要求正整数 `expected_row_revision`；活动 draft 的后续回答也必须携带当前 revision。CAS 不一致映射 409 `planning_conflict`，不存在映射 404，非法状态映射 409，Pydantic/Service 输入错误映射 422，数据库/模型/Planning 暂不可用映射 503；SSE error 只发送稳定码与安全提示。`src/agents/chat_backend/http.py::PlanningControlRequest`、`require_planning_action`、`require_planning_draft`、`_planning_error_payload`
- [FACT] `ConversationResponse.mode` 是刷新时默认输入模式的权威值，但用户可在非流式运行期间显式切换聊天/规划；活动计划不会隐藏或取消。只有无活动计划或 drafting 接受规划正文，其余活动状态显示明确阻塞原因。提交成功才清空输入，失败保留原文。Planning 草案的最多 200 条安全 status 写入该草案助手消息并可在刷新后恢复，但浏览器只在默认折叠的运行进度区显示；旧计划若没有记录，只说明无法还原，不伪造事件或模型私有推理。`src/agents/chat_backend/http.py::planning_stream`、`src/agents/chat_backend/static/app.js::planningComposerBlockReason`、`syncPlanningProgressFromMessages`
- [FACT] `planning-panel` 位于历史消息之后，内部只有一个原生 `details.planning-shell`：活动计划首次展开，进入 completed/cancelled/failed/superseded 后自动收起，用户手动开合状态在同一 run/status 重绘间保留。计划 Markdown 助手消息仍保存在 PostgreSQL/Session，但前端检测到同一 `plan_markdown` 后不再重复渲染。`planning-final` 位于面板之后，只在 completed 时用快照生成一条简短最终回复，不伪造持久消息。需要用户关注的 revision 仍把 `chat-stage.scrollTop` 设为真实 `scrollHeight`。公开步骤错误只放行固定安全错误，任意异常正文仍折叠成 `planning run could not continue`。`src/agents/chat_backend/static/index.html::planning-panel`、`planning-final`、`src/agents/chat_backend/static/app.js::planningShellOpen`、`isPlanningDefinitionMessage`、`renderPlanningFinalReply`
- [FACT] 计划批准和单次工具审批会在请求发出时显示持久的 `planningNotice`，操作完成后依据最新 snapshot 区分“已继续执行”“已完成”“仍有其他审批”与“同一 call id 未更新”。`runPlanningAction()` 必须返回 `streamPlanning()` 的布尔结果，不能只等待后丢弃结果；否则成功审批也会被前端误判成失败并清空提示。`src/agents/chat_backend/static/app.js::approvePlanning`、`decideToolApproval`、`runPlanningAction`
- [FACT] RunState 快照只把当前未解决的审批放入公开审批列表，但 `new_items` 会保留历史审批事件、工具结果和最后模型响应。恢复旧快照后，若历史审批在当前 Context 中为 `unknown`，`RunResult` 会用 `tool_result/tool_error.metadata.tool_execution` 的 `(tool_name, call_id)` 判断该调用是否已经收口；已经存在匹配结果的调用不能再次出现在 `pending_approvals`。暂停轮同时含安全调用和待批准调用时，`RunState.from_snapshot()` 从最后模型响应恢复完整原顺序工具批次，而不是只恢复审批子集；恢复执行对 unknown 调用重新用当前工具策略判断，仍需审批或判断异常就暂停，安全调用才执行。`src/agents/result.py::RunResultBase._approval_request_is_pending`、`src/agents/run_state.py::_paused_turn_tool_calls`、`RunState.from_snapshot`、`src/agents/run_resume.py::resume_pending_tool_approvals`
- [FACT] lifespan 只用一个应用级 `set[asyncio.Task]` 保存恢复和断线后继续运行的 Planning task；没有新协调器类。浏览器断开不取消 Planning task，应用关闭会取消并等待集合中的剩余任务，然后关闭数据库。该合同仍是单进程内的，不提供跨 Uvicorn worker 的实时事件订阅。`src/agents/chat_backend/http.py::track_background`、`planning_stream`、`lifespan`
