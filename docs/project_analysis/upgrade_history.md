# MyAgent 功能升级历史

本文件记录 MyAgent 实际新增的运行能力；架构文档自身的维护记录见 [CHANGELOG.md](CHANGELOG.md)。结论以当前源码为准。

## 2026-07-13 ～ 2026-07-14：Step 01 纯事件核心

### 升级结果

[FACT] MyAgent 新增一套独立的运行事件 Ponyfill：它可以描述 run、turn、model、tool、approval、steer 和错误过程，为单次 run 分配连续序号，并把现有 `RunItem` 中语义明确的记录转换成新事件。该能力尚未接管主循环，也没有改变已有状态机、结果、trajectory 或 tracing。`src/agents/events.py`、`src/agents/event_compat.py`

### 本轮代码变更

1. **公共事件元数据与运行错误**
   - [FACT] `RunEvent` 统一保存 `run_id`、`sequence`、UTC `timestamp`、可选 `turn_id`/`step_number` 与固定 `schema_version=1`，并以冻结 dataclass 保证创建后不可修改。`src/agents/events.py::RunEvent`
   - [FACT] 新增 `RunAlreadyFinishedError` 与 `RunCancelledError`，错误对象保留 run id 及终态 sequence/取消原因。`src/agents/events.py::RunAlreadyFinishedError`、`RunCancelledError`

2. **生命周期与处理过程事件**
   - [FACT] run/turn 事件包括 started、completed、stopped、cancelled、failed；四种 run 结束事件组成 `TerminalRunEvent`。`src/agents/events.py::RunStartedEvent`、`TurnStartedEvent`、`TurnCompletedEvent`、`TerminalRunEvent`
   - [FACT] model/tool/approval/steer/error 事件只保存消费者需要定位和展示的小型载荷，不复制完整模型响应或工具结果。`src/agents/events.py::RunEventType`
   - [FACT] `is_terminal_event()` 只把 completed、stopped、cancelled、failed 判为终态，普通 `RunErrorEvent` 不关闭 run。`src/agents/events.py::is_terminal_event`

   | 事件组 | 本轮新增的具体事件 |
   |---|---|
   | run / turn | `RunStartedEvent`、`TurnStartedEvent`、`TurnCompletedEvent` |
   | run 终态 | `RunCompletedEvent`、`RunStoppedEvent`、`RunCancelledEvent`、`RunFailedEvent` |
   | model | `ModelStartedEvent`、`ModelCompletedEvent` |
   | tool | `ToolRequestedEvent`、`ToolStartedEvent`、`ToolCompletedEvent`、`ToolFailedEvent` |
   | approval | `ApprovalRequiredEvent`、`ApprovalResolvedEvent` |
   | steer | `SteerAcceptedEvent`、`SteerAppliedEvent` |
   | 非终态错误 | `RunErrorEvent` |

3. **稳定字典序列化**
   - [FACT] `event_to_dict()` 输出显式 `event_type`、公共字段与具体载荷，把 UTC datetime 转成 `Z` 结尾字符串；当前不提供反序列化或文件 writer。`src/agents/events.py::event_to_dict`

4. **单 run sequence 与终态约束**
   - [FACT] `RunEventReducer` 注入 run id、连续 sequence 和可替换 clock；只有事件构造成功才消耗序号。`src/agents/events.py::RunEventReducer.emit`
   - [FACT] reducer 只保存下一个序号和首个 terminal；首个 terminal 后永久拒绝继续 emit，不保存事件历史，不使用全局 registry。`src/agents/events.py::RunEventReducer`

5. **旧 `RunItem` 单向兼容适配**
   - [FACT] `emit_run_item_event()` 映射 model response/error、tool call/result/error、approval required/rejected、final output 与 run stopped。`src/agents/event_compat.py::emit_run_item_event`
   - [FACT] 没有可靠语义的 guardrail、handoff、verification 等记录返回 `None`；返回 `None` 不消耗 reducer sequence。`src/agents/event_compat.py::emit_run_item_event`
   - [FACT] 工具完成/失败事件的 tool name 和 call id 必须来自 `metadata["tool_execution"]`；适配器不从文本或其他脆弱字段猜测。`src/agents/event_compat.py::_tool_execution_fields`

   | 旧 `RunItem.item_type` | 转换结果 |
   |---|---|
   | `model_response` | `ModelCompletedEvent` |
   | `model_error` | `RunErrorEvent` |
   | `tool_call` | `ToolRequestedEvent` |
   | 成功 `tool_result` | `ToolCompletedEvent` |
   | 审批拒绝 `tool_result` | `ApprovalResolvedEvent(decision="rejected")` |
   | `tool_error` | `ToolFailedEvent` |
   | `tool_approval_required` | `ApprovalRequiredEvent` |
   | `final_output` | `RunCompletedEvent` |
   | `run_stopped` | `RunStoppedEvent` |
   | 其他或无法准确表达的记录 | `None` |

6. **行为检查**
   - [FACT] 新增事件值、校验、终态、序列化、reducer 隔离与旧记录映射的自动检查。`tests/test_events.py`、`tests/test_event_compat.py`
   - [FACT] Step 01 目标回归结果为 `84 passed, 54 subtests passed`；全量结果为 `700 passed, 316 subtests passed, 6 failed`。其中 5 个失败来自中文路径错误导致示例文件不可见，另 1 个是本轮之前已存在的 repo-map 生产接入缺口。
   - [FACT] 2026-07-14 文档同步后再次直接运行两份事件测试，结果为 `30 passed`。

### 架构决策与兼容边界

- [FACT] `events.py` 不依赖 `contracts.py`；需要兼容旧记录时，由 `event_compat.py` 单向依赖 `contracts` 与 `events`，避免事件值对象被旧状态模型绑住。
- [FACT] 现有 `RunItem` 仍是 `RunState.new_items`、`RunResult`、恢复快照与 trajectory 的数据来源；本轮没有替换状态机。
- [FACT] 新事件模块未写入 `src/agents/__init__.py`，也没有被 `run_loop.py`、`run_state.py` 或 `trajectory.py` 导入，因此不是当前公共 package API 或生产事件流。
- [FACT] 未新增第三方依赖、全局状态、事件存储、subscriber 或 async channel。

### 后续明确未实现

- cancellation token、run controller、bounded event channel 与 steer inbox。
- 在真实 run loop 中创建 reducer、发布事件或向消费者提供 stream。
- 事件反序列化、持久化、重放、schema migration 与并发同步。
- guardrail、handoff、verification 等旧记录的精确事件映射。

这些内容必须在后续 Step 中按 MyAgent 真实调用链逐课接入，不能因为事件类型已经定义就视为现有运行能力。

## 2026-07-14：Step 02 单次运行控制、steer 与有界事件通道

### 升级结果

[FACT] MyAgent 在 Step 01 事件核心之上新增独立的单 run 控制层：外部可以创建线程可见的取消令牌和运行 controller、提交 FIFO steer；内部 runtime 可以通过有界 asyncio channel 运输已编号事件，并在取消后先等待合作退出、超时再取消 producer task。该能力尚未接入同步 Agent loop，因此不能描述为现有 `Agent.run()` 已支持流式取消。`src/agents/run_control.py`、`src/agents/run_config.py::RunConfig`

### 本轮代码变更

1. **线程可见的幂等取消信号**
   - [FACT] `CancellationToken` 使用 `threading.Event` 保存取消状态、用 `Lock` 保护首次 reason/requested_at；重复取消返回 False 且不覆盖首次数据。`src/agents/run_control.py::CancellationToken`
   - [FACT] 同步 worker 可通过 `wait(timeout)` 观察取消，并在安全边界用 `raise_if_cancelled()` 抛出带 run id/reason 的 `RunCancelledError`。`src/agents/run_control.py::CancellationToken.wait`、`raise_if_cancelled`

2. **单 run controller 与 steer FIFO**
   - [FACT] `RunController` 绑定同 run id 的 token，统一提供 cancel、finished/terminal 查询和 steer 入口。`src/agents/run_control.py::RunController`
   - [FACT] `SteerMessage` 为不可变值；controller 生成连续 `steer-N`，内部 `_drain_steers()` 按 FIFO 原子取走当前批次，finished 后拒绝新 steer。`src/agents/run_control.py::SteerMessage`、`RunController.steer`、`_drain_steers`

3. **有界事件运输与关闭传播**
   - [FACT] `_EventChannel` 直接使用 `asyncio.Queue(maxsize)`，队列满时 producer 等待，不丢弃、合并或重新编号事件。`src/agents/run_control.py::_EventChannel`
   - [FACT] 满队列写入同时等待 queue 空位与 consumer-closed；结束路径取消并 await 临时 tasks，避免 pending waiter。`src/agents/run_control.py::_EventChannel._send_or_consumer_closed`
   - [FACT] terminal 先 yield 再结束；无 terminal 时 `close()` 发送不暴露给消费者的内部 sentinel。consumer 显式 `aclose()` 会反向唤醒 producer；Python 的裸 `async for ... break` 不保证立即关闭 iterator。`src/agents/run_control.py::_EventChannel.close`、`__aiter__`

4. **单 run runtime 与取消宽限期**
   - [FACT] `_create_run_runtime()` 每次生成不同 UUID，并组合独立 reducer、token、controller 和 channel；没有全局 registry 或缓存。`src/agents/run_control.py::_create_run_runtime`
   - [FACT] `_RunRuntime.emit()` 复用 reducer 分配序号，await channel 后返回同一事件；terminal 的 channel 发送尝试完成后在 finally 中 finish controller。`src/agents/run_control.py::_RunRuntime.emit`
   - [FACT] `shutdown_after_cancel()` 要求 token 已取消，先等待配置宽限期，超时后 `Task.cancel()` 并 gather；reducer 尚无终态时补发 `RunCancelledEvent`。`src/agents/run_control.py::_RunRuntime.shutdown_after_cancel`

5. **配置与公共接口**
   - [FACT] `RunConfig.event_queue_size` 默认 100、至少 1；`cancellation_grace_period` 默认 0.1 秒、必须有限且非负，二者均拒绝 bool。`src/agents/run_config.py::RunConfig.__post_init__`
   - [FACT] 包顶层只公开 `CancellationToken`、`RunController`、`SteerMessage`；`_EventChannel`、`_RunRuntime`、factory 和 sentinel 保持内部。`src/agents/__init__.py::__all__`

6. **行为检查**
   - [FACT] 控制层检查覆盖取消幂等与跨线程可见性、steer FIFO、channel 背压/关闭/consumer exit、runtime 隔离、terminal 顺序、grace 内合作退出和超时取消。`tests/test_run_control.py`、`tests/test_event_channel.py`
   - [FACT] 公共 API 与 Step 01/02 定向结果为 `96 passed, 348 subtests passed`；全量为 `732 passed, 348 subtests passed, 6 failed`。5 个失败来自既存示例文件缺失，另 1 个是既存 repo-map 生产接入缺口。

### 架构决策与兼容边界

- [FACT] 同步 token/controller 与 asyncio channel/runtime 分开；当前不承诺跨线程直接操作 asyncio.Queue。`src/agents/run_control.py`
- [FACT] reducer 仍是 sequence 和 terminal 的唯一来源；channel 不保存事件历史，runtime 不复制序号。`src/agents/events.py::RunEventReducer`、`src/agents/run_control.py::_RunRuntime.emit`
- [FACT] 取消异步 task 不能硬停吞掉 `CancelledError` 的 coroutine，也不能终止已开始运行的同步工具线程。`src/agents/run_control.py::_RunRuntime.shutdown_after_cancel`、`src/agents/tool_runtime.py::run_with_timeout`
- [FACT] `Agent.run()`、`Runner.run_sync()`、`_run_agent_loop_impl()`、CLI、session、state、result、trajectory 和 tracing 行为未修改。工作树 production imports 只发现 `agents.__init__` 导入三个公共控制值。

### 后续明确未实现

- `Agent.run_stream()`、`Runner.run_stream()`、stream handle 与同步 loop/async channel 的线程桥接。
- 在 model、tool、approval、handoff、guardrail 和 steer-safe-boundary 中 emit/check/drain。
- 多消费者、事件重放/持久化、drop policy、优先级和全局 runtime 管理。

下一 Step 必须先建立真实 stream producer 与清理 wrapper，再把控制检查逐个接入同步主循环；不能仅因内部 runtime 已存在就公开 channel 或宣称 CLI 已支持中断。

## 2026-07-19：Step 03 同步循环事件桥、合作取消与唯一终态

### 升级结果

[FACT] MyAgent 已把现有同步 Agent loop 接到内部 asyncio 事件 runtime：producer 在线程中运行原循环，bridge 把 run、turn、model、tool、approval 与错误事件送回有界 channel。模型和普通工具新增调用前后取消检查，迟到的响应/工具结果不会写入状态；producer 将完成、暂停、取消、失败收为互斥的唯一终态。该能力仍是内部接线，不代表已有公共 `run_stream()` API。`src/agents/run_control.py::_run_agent_loop_with_events`

### 本轮代码变更

1. **同步 loop 到 asyncio runtime 的桥接**
   - [FACT] `_LoopEventBridge` 使用 `run_coroutine_threadsafe()` 发布直接事件和 `RunItem` 映射事件；`_run_agent_loop_with_events()` 使用 `asyncio.to_thread()` 保持事件 loop 不被同步模型/工具阻塞。`src/agents/run_control.py::_LoopEventBridge`、`_run_agent_loop_with_events`

2. **主循环、模型、工具和审批事件**
   - [FACT] fresh loop 发布 turn start/completed；模型发布 started/completed；普通工具发布 requested/started/completed/failed；审批发布 required/resolved。旧 `RunItem`、RunResult、session 与 hooks 仍是原有状态来源。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/model_turn.py::_run_model_turn_impl`、`src/agents/tool_execution.py::_execute_tool_call_impl`、`src/agents/tool_planning.py::build_tool_execution_plan`

3. **合作取消安全边界**
   - [FACT] loop 在模型输入和工具 requested 前检查 token；模型/工具实现又在真实调用前和返回后检查。后置检查早于 state item、模型工具输出、completed event 与下一次调用。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/model_turn.py::_run_model_turn_impl`、`src/agents/tool_execution.py::_execute_tool_call_impl`
   - [FACT] `RunCancelledError` 在模型包装、模型错误记录和工具错误记录之前单独重新抛出，最终只产生取消终态。`src/agents/model_turn.py::run_model_turn`、`src/agents/run_loop.py::_run_agent_loop_impl`

4. **失败与唯一终态**
   - [FACT] `record_model_error()` 同步发布 `RunErrorEvent`；producer 随后发布相邻 `RunFailedEvent` 并返回失败结果。意外 producer 异常先发布 error/failed，再重新抛出原异常。`src/agents/run_recording.py::record_model_error`、`src/agents/run_control.py::_run_agent_loop_with_events`
   - [FACT] normal result、`run_stopped`、取消和失败分别选择 completed、stopped、cancelled、failed，reducer/controller 共同拒绝第二终态。`src/agents/events.py::RunEventReducer`、`src/agents/run_control.py::_RunRuntime._deliver`

5. **行为检查**
   - [FACT] 新增线程信号驱动的确定性检查，覆盖调用前取消、模型返回后取消、工具返回后取消、模型失败相邻事件与意外 producer 异常重抛。`tests/test_run_event_bridge.py`
   - [FACT] 相关定向回归为 `197 passed, 76 subtests passed`；全量为 `741 passed, 348 subtests passed, 6 failed`。5 个失败仍来自既存示例路径问题，另 1 个仍是既存 repo-map 接入缺口。

### 兼容边界与后续未实现

- [FACT] 公共 `Agent.run()`、`Runner.run_sync()`、`RunResult`、session、snapshot、trajectory 和 hook 接口未改为异步或流式；事件 producer、bridge、channel 与 runtime 保持内部。`src/agents/agent.py::Agent.run`、`src/agents/__init__.py::__all__`
- [FACT] 已运行中的同步模型/工具不能被硬杀；取消在它们返回后的第一个安全边界生效，并丢弃迟到结果。`src/agents/model_turn.py::_run_model_turn_impl`、`src/agents/tool_execution.py::_execute_tool_call_impl`
- 尚未实现公共 stream handle/iterator、异步 Runner、CLI 事件消费、steer drain、多消费者、事件持久化/重放、drop policy 或全局 runtime registry。

## 2026-07-21：模型流 Step 01——事件契约与安全聚合

### 升级结果

[FACT] MyAgent 在现有同步模型调用之下新增供应商无关的单 attempt 事件边界：adapter 可以描述 created、正文/reasoning/refusal、工具参数、usage 与 completed/failed/incomplete，聚合器校验身份、顺序、唯一终态和共享字符上限后返回现有 `ModelResponse`。同步生产调用链未切换到该协议。`src/agents/contracts.py::ModelRequest`、`src/agents/model_events.py::ModelEventAccumulator`、`src/agents/models.py::StreamingModelAdapter`

### 本轮代码变更

1. **请求与事件身份**
   - [FACT] `ModelRequest` 保存模型名、tuple 化消息/工具集合、模型设置、request id 与 attempt id；不做无需求的深拷贝。`src/agents/contracts.py::ModelRequest`
   - [FACT] 所有模型事件继承冻结的 `ModelEvent(attempt_id, sequence)`；累加器要求首事件 created、同 attempt 且 sequence 连续。`src/agents/model_events.py::ModelEvent`、`ResponseCreated`、`ModelEventAccumulator._validate_event`

2. **内容、工具参数与共享缓冲**
   - [FACT] text/reasoning/refusal 按双索引拼接并核对 done 全文；三类内容和工具参数共用 `max_buffer_chars`。`src/agents/model_events.py::_append_content`、`_finish_content`、`_reserve_buffer_chars`
   - [FACT] 工具 start 固定 item/call/name/位置；done 只有与累计字符串一致、`json.loads()` 成功且顶层为 dict 时才提交现有 `ToolCall`。partial、非法 JSON、数组/标量、重复或冲突 identity 始终零提交。`src/agents/model_events.py::_start_tool_call`、`_finish_tool_arguments`

3. **usage、唯一终态与结果清理**
   - [FACT] usage 使用最后一次顶层快照；只有唯一 completed terminal、匹配 response id 且不存在 pending 内容/工具时，`finish()` 才构造现有 `ModelResponse`。`src/agents/model_events.py::UsageUpdated`、`ResponseCompleted`、`ModelEventAccumulator.finish`
   - [FACT] failed/incomplete 保留稳定错误事实；无 terminal 为 early close。成功、失败、overflow 或 abort 后都会释放文本、参数、usage、terminal 和临时工具列表，diagnostics 只保留 usage_missing 与字符峰值。`src/agents/models.py::ModelErrorKind`、`src/agents/model_events.py::ModelEventDiagnostics`、`abort`

4. **同步/流式能力边界与公共 API**
   - [FACT] 原同步 `ModelAdapter` 保持不变；独立 `StreamingModelAdapter` 只声明 `stream_response(ModelRequest) -> AsyncIterator[ModelEvent]`。`models.py` 仅在 `TYPE_CHECKING` 下导入 `ModelEvent`，避免运行时循环。`src/agents/models.py::ModelAdapter`、`StreamingModelAdapter`
   - [FACT] `supports_model_adapter()` 接受 stream、sync get_response 或 legacy decide；`supports_streaming_model()` 只检查 callable，不创建 iterator。`src/agents/models.py::supports_model_adapter`、`supports_streaming_model`
   - [FACT] 包根公开 adapter 实现所需的请求、具体事件、流式 Protocol、能力检查和稳定错误；`ModelEventAccumulator`、diagnostics 与内部 buffer/pending 状态不公开。`src/agents/__init__.py::__all__`

5. **行为检查**
   - [FACT] Step 定向回归为 `103 passed, 401 subtests passed`；全量为 `806 passed, 481 subtests passed, 6 failed`，`python -m compileall -q src tests` 通过。6 个失败均为既存问题：5 个示例路径加载失败、1 个 repo-map 接入断言。

### 架构决策与兼容边界

- [FACT] `model_events.py` 不导入 OpenAI SDK、RunState、RunEvent runtime、tool registry 或工具执行；它只复用 contracts 结果和 models 错误事实。
- [FACT] 当前 `_run_model_turn_impl()` 仍优先调用同步 `get_response`，否则调用 legacy `decide`；它没有检查或调用 `stream_response`。`src/agents/model_turn.py::_run_model_turn_impl`
- [FACT] `OpenAIResponsesModel` 尚未实现 `stream_response()`；本轮没有新增网络请求、retry、timeout、fallback、sync-to-async bridge 或 token delta UI。
- [FACT] 模型 `ModelEvent` 与对外运行 `RunEvent` 是两个不同层级，本轮没有建立二者转换、持久化、tracing 或 state snapshot 接线。

### 后续明确未实现

- OpenAI Responses streaming event 到标准模型事件的映射。
- model turn 对 stream/sync 的选择、attempt retry/fallback/timeout 与取消。
- 模型分片到 RunEvent、状态快照、tracing 指标和 CLI 展示的接线。

## 2026-07-22：模型流 Step 02——OpenAI Responses 适配与同步兼容桥

### 升级结果

[FACT] `OpenAIResponsesModel` 现在可以把同步 Python SDK 返回的 Responses 事件流转换成供应商无关的 `ModelEvent`；旧的同步 `get_response()` 和 legacy `decide()` 模型也可以经内部兼容桥产生同一事件序列。统一入口已经就绪，但生产 `model_turn` 仍未消费它。`src/agents/models.py::OpenAIResponsesModel.stream_response`、`stream_model_response`

### 本轮代码变更

1. **OpenAI 流请求与资源关闭**
   - [FACT] 流请求复用 `build_response_request_kwargs()` 并设置 `stream=True`；SDK 自动 retry 通过 `with_options(max_retries=0)` 关闭。阻塞的 create、next 和 close 均使用 `asyncio.to_thread()`。`src/agents/models.py::OpenAIResponsesModel._open_response_stream`、`_next_stream_item`、`_close_openai_stream`
   - [FACT] 正常完成、协议/服务端错误、early close、buffer overflow 和 consumer `aclose()` 都关闭 SDK stream；关闭错误不会覆盖已经发生的主异常。`src/agents/models.py::OpenAIResponsesModel.stream_response`

2. **Responses 事件转换**
   - [FACT] `_OpenAIEventMapper` 校验 provider 序号单调递增，并为内部事件重新分配连续 sequence。它转换 created/completed、正文、reasoning text/summary、拒答、function call 参数、usage、failed/incomplete 与顶层 error。`src/agents/models.py::_OpenAIEventMapper`
   - [FACT] 多个函数调用按 item id、call id、name 和 output index 核对；参数 done 与最终 output item 必须和累计字符串一致。reasoning text 与 summary 使用独立内部位置，避免 provider 索引碰撞。`src/agents/models.py::_OpenAIToolItemState`、`_OpenAIEventMapper.map`

3. **成功提交边界**
   - [FACT] 每条映射事件先进入 `ModelEventAccumulator.apply()` 再交给消费者；只有 `finish()` 成功才更新 `last_response`、`last_output_text`、`previous_response_id` 并清空 `pending_tool_outputs`。失败时保留上一份 response 状态。`src/agents/models.py::OpenAIResponsesModel.stream_response`
   - [FACT] `max_buffer_chars` 默认一百万且必须是正整数，为正文、reasoning、拒答和工具参数提供共同字符上限。`src/agents/models.py::OpenAIResponsesModel.__post_init__`

4. **sync/legacy 兼容桥**
   - [FACT] `_model_response_to_events()` 用单组 delta+done 表达完整正文/拒答，用 `json.dumps()` 和 start+delta+done 表达每个现有工具调用，并保留 usage、output、raw 与 request metadata。缺少 response id 时仅用 request id 完成内部配对。`src/agents/models.py::_model_response_to_events`
   - [FACT] `stream_sync_model_response()` 在线程中复用 `call_model_response()`，只有 `decide()` 时构造最小 `ModelResponse`；`stream_model_response()` 只选择“原生流优先，否则同步桥”。没有增加 factory、registry、retry 或配置占位。`src/agents/models.py::stream_sync_model_response`、`stream_model_response`

5. **行为与架构检查**
   - [FACT] 目标回归为 `146 passed, 106 subtests passed`；全量为 `832 passed, 515 subtests passed, 6 failed`，`python -m compileall -q src tests` 通过。6 个失败与开课前一致：5 个 Windows 中文路径示例加载问题、1 个 repo-map 生产接入缺口。
   - [FACT] CodeGraph 全量重建后，`stream_model_response` 的生产 callers 为零，当前调用者仅为验收测试；固定 provider 事件字符串只存在于 `models.py`。

### 架构决策与后续未实现

- [FACT] `models.py` 对 `ModelEvent` 仍只在 `TYPE_CHECKING` 下引用，具体事件和累加器在函数内部导入，未形成模块加载期循环。
- [FACT] `stream_model_response()` 保持内部符号，未加入包顶层 `__all__`；真实下游接入前不扩大公共 API。
- [FACT] `model_turn.py`、`run_loop.py`、`RunState`、公共 `RunEvent`、CLI 和工具执行时机均未改变。
- retry、Retry-After、backoff、timeout、非流 fallback、模型 fallback、分片到公共运行事件和 CLI 实时展示留给后续 Step。

## 2026-07-24：模型流 Step 03——有限重试、超时、取消与显式降级

### 升级结果

[FACT] MyAgent 新增可独立调用的异步 `run_model_request()`：一次逻辑请求使用稳定 request id 和连续 attempt id，在唯一 retry loop 中消费统一 `ModelEvent`、聚合响应、分类失败、有限等待并保存全部 attempt records。它已从包根公开，但生产同步 `model_turn` 与 Agent loop 尚未调用。`src/agents/model_retry.py::run_model_request`、`src/agents/__init__.py`、`src/agents/model_turn.py::_run_model_turn_impl`

### 本轮代码变更

1. **配置、错误事实与纯决定**
   - [FACT] `ModelSettings` 用 `None` 保持分层覆盖语义，并增加 connect/read/total timeout、attempt、backoff、jitter、retry budget、stream buffer 与两种 fallback 设置；有效默认值在执行策略中换算。`src/agents/model_settings.py::ModelSettings`、`RetryPolicy.__init__`
   - [FACT] OpenAI 与通用异常只在 adapter 边界转换成稳定 kind；`RetryPolicy.decide()` 读取 kind、attempt、累计等待和显式 fallback 条件，不 sleep、不调用模型。`src/agents/models.py::classify_openai_model_error`、`classify_model_error`、`src/agents/model_retry.py::RetryPolicy.decide`

2. **等待、预算、身份与结果**
   - [FACT] Retry-After 支持非负秒数和带时区 HTTP-date；无合法值时使用带 cap/jitter 的指数退避。候选等待不得越过累计 retry budget 或 total timeout。`src/agents/model_retry.py::parse_retry_after`、`compute_retry_delay`
   - [FACT] `ModelAttemptRecord`、`ModelAttemptNotice`、`ModelExecution` 与 `ModelExecutionError` 保存实际发送身份、模式、结果、耗时、错误、等待和最终结果；集合在边界转成 tuple。`src/agents/model_retry.py`

3. **唯一执行循环、总时限与取消**
   - [FACT] `run_model_request()` 是唯一 retry/fallback 编排循环；attempt、每次事件读取和 backoff 共享一次 monotonic deadline，并复用现有 `CancellationToken`。取消直接传播 `RunCancelledError`，不伪装成模型失败。`src/agents/model_retry.py::run_model_request`、`_await_with_limits`、`_sleep_with_cancel`
   - [FACT] 已开始的同步 SDK/custom adapter 线程不能被 asyncio 强杀；调用协程停止等待并忽略迟到结果，OpenAI 请求另受 connect/read timeout 限制。`src/agents/models.py::stream_sync_model_response`、`OpenAIResponsesModel._open_response_stream`、`get_response_for_request`

4. **两种一次性 fallback**
   - [FACT] 只有创建流阶段明确返回 `STREAM_UNAVAILABLE` 且开关开启时，才新增一次同模型 non-stream attempt；任何部分事件出现后都不进入该路径，fallback 自身失败立即结束。`src/agents/model_retry.py::_consume_stream_attempt`、`run_model_request`
   - [FACT] 主模型只有在 RATE_LIMIT/SERVER 因次数或累计等待预算耗尽时，才可切换到一个不同的显式备用模型；备用 attempt 不 retry。OpenAI 备用请求不携带主模型 `previous_response_id`，成功后清空 stored previous id，且不修改 adapter 默认模型名。`src/agents/model_retry.py::RetryPolicy.decide`、`run_model_request`、`src/agents/models.py::OpenAIResponsesModel`

5. **公共接口与静态闭环**
   - [FACT] 包根公开 `RetryAction`、`RetryDecision`、`RetryPolicy`、attempt notice/record、execution/error 和 `run_model_request`；Retry-After、delay、id、wait、stream consume helper 保持内部。`src/agents/__init__.py::__all__`
   - [FACT] CodeGraph 当前索引 130 个 Python 文件、3745 个节点和 9844 条关系，但未收录未跟踪的 `model_retry.py`；当前磁盘 AST/import 检查补核 `run_model_request()` 内只有一个 while、没有递归，`models.py` 不导入 retry 编排，`model_turn.py` 不导入或调用新入口。

6. **行为检查**
   - [FACT] Step 03 最终定向回归为 `194 passed, 559 subtests passed`；全量为 `882 passed, 629 subtests passed, 6 failed`，`python -m compileall -q src tests` 通过。6 个失败均为既存问题：5 个 Windows 中文路径示例加载失败、1 个 repo-map 生产接入断言。

### 架构调整与未实现范围

- [FACT] 原计划若在本 Step 直接让同步 `model_turn` 调用异步执行器，会同时改变 worker/event-loop 桥、取消传播和模型分片事件投影；因此本 Step 只交付独立公共组件，生产接线留给后续 Step。
- [FACT] 新 OpenAI stream 与一次性 non-stream 入口使用 `max_retries=0`；既有生产同步 `OpenAIResponsesModel.get_response()` 尚未迁移，不能宣称 `Agent.run()` 已获得本 Step 的 retry/timeout/fallback 行为。
- 未实现多个备用模型、provider 路由、fallback 链、attempt 到公共 `RunEvent`/RunState/snapshot/tracing 的投影，也未修改工具、handoff、guardrail 或 CLI。

后续 Step 应直接调用现有 `run_model_request()` 并消费 records/notices，解决同步生产回合到异步执行器的接线；不应重新实现错误分类、等待计算或 fallback 决策。

## 2026-07-24 ～ 2026-07-25：模型流 Step 04——生产接入、状态贯通与可观测性

### 升级结果

[FACT] MyAgent 已把 Step 03 的独立模型执行器接入真实 Agent loop。每个生产模型回合都建立一个逻辑 `ModelRequest`，由唯一 `run_model_request()` 选择原生流、同步兼容、有限重试和显式 fallback；attempt 生命周期进入公共 `RunStream`，最终 attempts、usage、TTFT 与终止原因进入 `RunState`、`RunResult`、snapshot v2 和 model span。失败或取消 attempt 的部分工具参数不会越过完整响应提交边界。`src/agents/model_turn.py::_run_model_turn_async`、`src/agents/run_state.py::RunState`、`src/agents/model_turn.py::_record_model_execution_span`

### 九课交付

1. **Attempt 通知与公共运行事件**
   - [FACT] 新增 `ModelAttemptStartedEvent`、`ModelRetryScheduledEvent`、`ModelFallbackEvent`，分别表达真实发送开始、已经决定的重试等待和模型切换；事件只带定位、模式、错误、等待和切换事实，不复制正文或工具参数。`src/agents/events.py`
   - [FACT] 内部 `_emit_model_attempt_notice()` 把现有 `ModelAttemptNotice` 映射到 worker bridge；notice 为兼容继续公开，新 runtime 消费者优先读取 `RunEvent`。`src/agents/model_turn.py::_emit_model_attempt_notice`

2. **生产模型回合接入唯一执行器**
   - [FACT] `_run_model_turn_async()` 为每轮生成 request id/首个 attempt id，并调用 `run_model_request()`；`_run_model_turn_impl()` 在同步 worker 中以 `asyncio.run()` 执行该协程。CodeGraph 复核的执行器唯一生产 caller 是 `_run_model_turn_async()`。`src/agents/model_turn.py`
   - [FACT] `run_loop` 不导入或读取供应商事件、`ModelEvent` 或 `ModelAttemptNotice`，只接收最终 `ModelTurnResult`。provider mapping 仍留在 `models.py`。`src/agents/run_loop.py::_run_agent_loop_impl`、`src/agents/models.py::_OpenAIEventMapper`

3. **完整响应后的唯一工具提交**
   - [FACT] 每个 attempt 先在自己的 `ModelEventAccumulator` 中聚合；只有 completed、identity/sequence 正确且工具参数为完整 JSON object 时才产生 `ModelResponse`。最终成功 execution 返回后，回合层才把 tool calls 交给 `process_model_turn()` 和 execution plan。`src/agents/model_events.py::ModelEventAccumulator.finish`、`src/agents/run_loop.py::_run_agent_loop_impl`
   - [FACT] 该边界与 OpenAI Function Calling Streaming 的事件语义一致：`response.function_call_arguments.delta` 需要聚合，`done` 提供完整参数；MyAgent 进一步在执行前核对全文并解析为 object。[OpenAI Function Calling：Streaming](https://developers.openai.com/api/docs/guides/function-calling#streaming)
   - [FACT] 验收修复第二课让正文、reasoning、拒答和工具参数在 append 前统一计算尝试字符数；越界错误与最终 diagnostics 只留下上限/尝试值，随后释放全部临时内容。`src/agents/models.py::ModelProtocolError`、`src/agents/model_events.py::ModelEventDiagnostics`、`ModelEventAccumulator._reserve_buffer_chars`
   - [FACT] `_OpenAIEventMapper` 对未知非关键 provider 事件只递增整数并返回空事件；`OpenAIResponsesModel` 在成功和失败路径都保存最近 attempt 的计数，非流 attempt 重置为零，不改变 response 提交门槛。`src/agents/models.py::_OpenAIEventMapper`、`OpenAIResponsesModel.stream_response`

4. **运行中的 attempts、usage 与终止状态**
   - [FACT] `ModelAttemptRecord` 增加成功 response id、usage 快照和 TTFT；`RunState.record_model_execution()` 追加成功链并记录 `completed`，最终失败保存全部 records 和稳定错误 kind。验收修复新增兼容 `RunCancelledError` 的 `ModelCancelledError`：发送前取消携带空 attempts，读流取消保存 cancelled attempt，退避取消保留 failed attempt 与实际等待，完整响应后提交前取消保留 succeeded attempt；`RunState` 与 model span 使用异常携带的同一组 records，并记录 `cancelled`。`src/agents/model_retry.py::ModelAttemptRecord`、`ModelCancelledError`、`src/agents/run_state.py::RunState.record_model_cancelled`、`src/agents/model_turn.py::run_model_turn`

5. **RunResult 与 Snapshot v2**
   - [FACT] `RunResultBase` 公开不可变 `model_attempts` 与 `last_model_terminal_reason`，`build_run_result()` 从 state 复制它们。`src/agents/result.py::RunResultBase`、`src/agents/run_state.py::build_run_result`
   - [FACT] snapshot writer 只输出 schema v2，保存 attempts/terminal reason 的纯数据；reader 接受 legacy 无版本、v1 和 v2，v1/legacy 为新增字段提供空默认，未知版本拒绝。`src/agents/run_state.py::RUN_STATE_SNAPSHOT_SCHEMA_VERSION`、`run_state_snapshot_from_dict`

6. **Model span 的 TTFT、耗时与 fallback 观测**
   - [FACT] model span 保存 request/final attempt identity、attempt/retry 数量、最终 model/mode、response id、usage、TTFT、每次 attempt 耗时、retry 等待、non-stream/model fallback 标记和 terminal reason；失败继续写结构化 span error。`src/agents/model_turn.py::_record_model_execution_span`、`src/agents/tracing.py::record_span_error`
   - [FACT] 验收修复第三课让 model 与 non-stream fallback 都在目标 attempt 真正开始前发布 `ModelFallbackEvent`；事件、相邻 attempt records 与 snapshot 使用同一 canonical reason、目标 attempt id、from/to model 和 from/to mode。span 从相邻 records 推导同一组 `fallback_*` 定位字段，未发生时全部为 None；snapshot 继续使用 v2。`src/agents/model_retry.py::run_model_request`、`src/agents/events.py::ModelFallbackEvent`、`src/agents/model_turn.py::_record_model_execution_span`

7. **同步模型、legacy decide 与公共入口兼容**
   - [FACT] native stream、同步 `get_response` 和 legacy `decide` 都进入同一 execution/state/span 流程；legacy 仍只返回工具调用，不新增伪造 raw `ModelResponse`。`src/agents/model_turn.py::_run_model_turn_impl`
   - [FACT] `Agent`/`Runner` 的 sync、async、stream 入口继续复用同一 `RunStream` producer 和同一 `RunResult` 类型。`src/agents/agent.py::Agent`、`src/agents/runner.py::Runner`

8. **集成验收**
   - [FACT] 场景覆盖单次成功、retry、model/non-stream fallback、fallback 失败/未发生、失败 attempt 的工具零提交、全部失败、读取/等待时取消、snapshot v1/v2、敏感 tracing 与 sync/decide 兼容。三课验收修复后的目标矩阵为 `263 passed, 550 subtests passed`；全量为 `911 passed, 675 subtests passed, 6 failed`，6 个基线失败未增加。

9. **公共接口与架构终审**
   - [FACT] 包根继续公开 `Agent`/`Runner`/`RunStream`、RunEvent 与三种 attempt 事件、ModelEvent/StreamingModelAdapter、retry/attempt/execution 类型、`run_model_request`、`RunResult` 和 `RunStateSnapshot`。`src/agents/__init__.py::__all__`
   - [FACT] accumulator、OpenAI mapper、delay/parser、流消费/等待/sleep helper、runtime/channel/bridge 与 notice mapper 保持内部；公共边界测试锁住该集合。`tests/test_public_api.py`
   - [FACT] 2026-07-25 CodeGraph 强制全量重建后包含 125 个 Python 文件、3978 个节点和 11314 条关系；完整静态链为 `_run_agent_loop_impl -> run_model_turn -> _run_model_turn_impl -> _run_model_turn_async -> run_model_request`。`run_model_request()` 的唯一生产 caller 是 `_run_model_turn_async()`；其余 callers 均为验收测试。`Runner.run_stream` 到该链之间由惰性 `RunStream._start_once()` 动态启动 producer。

### 架构决策与未实现范围

- [FACT] `ModelEvent` 仍是 adapter/attempt 内部流，公共 `RunEvent` 只投影 attempt 生命周期；没有正文 token delta 的 commit/retract 协议。
- [FACT] Chat/Coding CLI 仍通过同步结果接口运行，没有逐 token 消费 `RunStream`；事件不持久化、不反序列化、不重放。
- [FACT] snapshot v2 是现有审批恢复面的扩展，不是完整 Agent checkpoint；不保存 provider events、模型对象、controller、stream、span 或事件历史。
- [FACT] 没有增加 fallback 链、多个备用模型、provider 路由、全局 metrics 服务或第二套 retry loop。

后续 token 预算/上下文压缩可以读取 per-attempt usage 与 context 错误；后续 CLI streaming 可以消费公共 attempt `RunEvent`，但正文实时展示仍需独立设计提交/撤回语义。

## 2026-07-25 ～ 2026-07-26：Task Graph Core Step 01

### 升级结果

[FACT] MyAgent 新增一套独立的进程内任务图核心。直接 SDK 调用方可以创建冻结任务节点、改变状态、增加 dependency、查询 ready 节点和阻塞根因，并在 revision 冲突、依赖坏结果、父任务取消或 owner missing 时得到稳定结果。该能力已经从 `agents` 包根公开，但尚未接入 Agent loop、RunState、ToolExecutionPlan、RunEvent、CLI 或 Session。`src/agents/task_state.py`、`src/agents/__init__.py`

### 九课交付

1. **任务值与错误事实**
   - [FACT] `TaskStatus` 固定 pending、running、completed、failed、blocked、cancelled 六个字符串；`TaskSpec` 和 `TaskNode` 为 frozen dataclass，依赖和 blocker 转为 tuple。`src/agents/task_state.py::TaskStatus`、`TaskSpec`、`TaskNode`
   - [FACT] 八个稳定异常保留 task id、revision、dependency 状态、cycle path 或 limit 定位字段。`src/agents/task_state.py::TaskStateError`

2. **纯状态变化**
   - [FACT] 合法方向为 pending→running/blocked/cancelled、running→completed/failed/blocked/cancelled、blocked→pending/cancelled；终态不可回退。时间、result、error 和 revision 在纯函数中统一计算。`src/agents/task_state.py::transition_task_node`

3. **内存 Store 与 parent 树**
   - [FACT] `TaskStore` 使用一个字典和一把 `RLock`，默认最多 100 个节点、parent 深度 8；创建失败不写入半个节点。`src/agents/task_state.py::TaskStore.create`

4. **CAS 状态更新**
   - [FACT] `transition()` 要求最新 `expected_revision`；两个线程用同一 revision 更新时只有一个成功，另一个抛 `TaskConflictError`。`src/agents/task_state.py::TaskStore.transition`

5. **dependency DAG**
   - [FACT] `add_dependency()` 只允许修改 pending/blocked 节点，拒绝自身、重复、缺失和直接/间接环；parent 不参与 dependency 环检查。`src/agents/task_state.py::TaskStore.add_dependency`

6. **可运行与阻塞查询**
   - [FACT] `ready()` 只返回 pending 且依赖全部 completed 的节点；`blocking_chain()` 按 BFS 创建顺序返回唯一 blocker；owner missing 批量阻塞其 pending/running 节点。`src/agents/task_state.py::TaskStore.ready`、`blocking_chain`、`mark_owner_missing`

7. **原子多节点级联**
   - [FACT] 父任务取消会取消未终态后代，failed/blocked/cancelled dependency 会阻塞直接和间接下游。主节点、parent BFS 和 dependency BFS 先写 staged view，全部成功后再提交；同一节点一次操作只增加一个 revision。`src/agents/task_state.py::TaskStore.transition`

8. **综合验收**
   - [FACT] S01-V-001～008 覆盖最小创建、状态表、ready、环拒绝、父取消、坏依赖/owner missing、两线程 CAS 和限制/缺引用零提交。`tests/test_task_state.py`

9. **公共接口与架构终审**
   - [FACT] 包根公开 `TaskStatus`、`TaskSpec`、`TaskNode`、`TaskStore` 和八个稳定异常；`transition_task_node`、字典、锁、写入、DFS/BFS 与 staged helper 保持内部。`src/agents/__init__.py::__all__`、`tests/test_public_api.py`
   - [FACT] 2026-07-26 CodeGraph 强制全量重建后包含 127 个 Python 文件、4084 个节点和 11837 条关系；未发现 `TaskStore` 生产 caller。`_replace_node()` 只由 `create()`、`add_dependency()`、`_commit_nodes()` 和故障注入测试调用。
   - [FACT] Task Graph + public API 为 `54 passed, 476 subtests passed`；Agents/Tools/RunState 回归为 `63 passed, 4 subtests passed`；全量为 `947 passed, 783 subtests passed, 6 failed`，6 个既有失败未增加。

### 架构决策与未实现范围

- [FACT] parent 是创建树，dependency 是执行 DAG，两种边不混用。
- [FACT] 默认节点上限 100，因此直接扫描反向依赖和后代；只有上限显著增加且性能测试证明需要时才增加索引。
- [FACT] frozen 只保证字段和 tuple 不可重新赋值，不深度冻结任意 result 载荷。
- [FACT] Step 01 交付时只有进程内锁和 CAS，没有数据库、跨进程锁、自动冲突重试、TaskEvent、journal、replay、scheduler 或 owner watcher。
- [FACT] 当时确定后续 TaskEvent 必须复用同一个 Store 和原子提交边界，不能绕过 Store 或建立第二张任务图；该约束已由下面的 Step 02 实现。

## 2026-07-26：Task Graph Step 02——TaskEvent、原子 journal 与幂等 replay

### 升级结果

[FACT] MyAgent 的独立任务图现在会为每个真实节点变化留下 frozen `TaskEvent`，能严格转成 JSON-compatible 字典并读回，还能按原顺序在新 `TaskStore` 中重建等价任务图。节点、seen event id 和 journal 在同一把 `RLock` 内整批提交；完全重复的事件是 no-op，冲突、乱序、断档、坏引用、非法变化和 limits 会在真实状态改变前失败。该能力已从 `agents` 包根公开，但仍未接入 Agent loop、RunState、RunEvent、Session、CLI 或持久化 backend。`src/agents/task_state.py`、`src/agents/task_events.py`

### 六课交付

1. **TaskEvent 值契约**
   - [FACT] `TASK_EVENT_SCHEMA_VERSION=1`；`TaskEventType` 只有 `created/updated`。冻结的 `TaskEvent` 保存 event/task identity、previous/current revision、UTC timestamp、reason 和完整 after-node，构造时核对节点 id/revision。`src/agents/task_state.py::TaskEvent`

2. **严格字典转换**
   - [FACT] node/event converter 要求字段集合完全匹配，拒绝未知 schema/enum、非规范 UTC 和不能无损 JSON 往返的 `result`。Store 仍允许任意 Python result，限制只存在于导出边界。`src/agents/task_events.py::task_node_to_dict`、`task_event_from_dict`

3. **单一 reducer 与批量归并**
   - [FACT] `_reduce_task_event()` 集中检查 event id 幂等/冲突、revision、created 新鲜度、update 合法变化、引用、parent 深度、dependency 环和节点上限。`_apply_event_batch()` 只建立一次 working nodes/seen 副本，整批成功后才替换真实 nodes/seen 并追加 journal。`src/agents/task_state.py::_reduce_task_event`、`TaskStore._apply_event_batch`

4. **四个 Store 写入口原子产生日志**
   - [FACT] `create()`、`transition()`、`add_dependency()`、`mark_owner_missing()` 和全部级联变化先生成事件，再进入同一个 apply 边界；父取消、坏依赖和 owner missing 的事件顺序继续保持主节点优先与 Store 创建顺序 BFS。旧的直接 `_replace_node/_commit_nodes` 写路径已删除。`src/agents/task_state.py::TaskStore`

5. **按原顺序幂等重放**
   - [FACT] `replay_task_events()` 把 typed event iterable 固定为 tuple，在带调用方 limits 的空 Store 中整批应用并返回结果。它不排序、不补缺失事件、不接受普通字典，也不包装 reducer 的稳定错误。恢复后 `list/ready/blocking_chain/events` 与原 Store 等价。`src/agents/task_events.py::replay_task_events`

6. **公开 API、10k 验收与性能收口**
   - [FACT] 包根公开 TaskEvent schema/type/value、四个 converter 和 replay；reducer、event builder、apply hook、journal/seen 容器和 factories 保持内部。`src/agents/__init__.py::__all__`
   - [FACT] 10k 链式 created 事件暴露出旧实现会从每个既有 dependency 沿整链寻找“尚未入图的新 task id”。合法旧图不可能引用未来任务，因此 created 现在只检查依赖存在；update 仍执行完整环搜索，安全规则未降低。`src/agents/task_state.py::_validate_event_node_graph`
   - [FACT] 本机 Python 3.14.2（MSC v.1944 64 bit）/ Windows 11 `10.0.26100` 对 10,000 个事件的纯 replay 测得 `0.012235s`，`tracemalloc` 峰值额外内存 `660800 bytes`（`0.630 MiB`）；输入事件在测量前构造，默认 `max_nodes=100` 仍拒绝该批次。`tests/test_task_events.py::TaskEventReplayTests`

### 验证与 CodeGraph 证据

- [FACT] Task Graph/Event/Public API 定向回归为 `86 passed, 530 subtests passed`。
- [FACT] Public API/RunEvent/RunState 回归为 `57 passed, 459 subtests passed`。
- [FACT] 全量为 `979 passed, 837 subtests passed, 6 failed`；6 个基线失败未增加，其中 5 个是 Windows 中文目录被错误解码后的 examples 路径，1 个是既存 repo-map 生产接入断言。
- [FACT] 2026-07-26 强制全量重建 CodeGraph 后为 `129 files / 4177 nodes / 12289 edges`。`_apply_event_batch()` 的生产 caller 恰好是四个 Store 写入口和 `replay_task_events()`；replay 当前 7 个 caller 全是测试。未发现 TaskStore/TaskEvent 进入 Agent、Runner、RunState、RunResult、Session、RunEvent 或 CLI。

### 架构决策与未实现范围

- [FACT] 依赖方向固定为 `task_events -> task_state`；TaskEvent 值和 reducer 留在 `task_state.py`，converter/replay 留在 `task_events.py`，没有循环 import 或第三个共享模型文件。
- [FACT] `TaskStore.events()` 返回内存 tuple 快照；converter 只返回字典，不写文件。没有 Session/file/SQLite writer、subscriber、event bus、checkpoint、compaction、migration registry 或跨进程 exactly-once。
- [FACT] Step 02 交付时 Task Graph 仍是 SDK 可独立使用的能力，不是 Agent 自动规划状态；当时约定后续 Planning Tools 必须调用公开 Store 命令和 `task_node_to_dict()`，不得调用 reducer、直接追加 journal 或假设 Session 已保存事件。该约束已由下面的 Step 03 实现。

## 2026-07-31：Task Graph Step 03——显式 Planning Tools

### 升级结果

[FACT] 调用方现在可以把既有 `TaskStore`、root task id 和 owner id 交给公共 `create_task_tools()`，得到固定顺序的 create/update/list 三个 `FunctionTool`，再按需注册进普通 `ToolRegistry`。模型能在授权 root 子树内创建固定 owner 的任务、通过 CAS 改变自己任务的状态，并按状态或 ready 条件读取可见节点；节点转换和所有状态/journal 规则继续复用现有实现。`src/agents/task_tools.py::create_task_tools`

### 六课交付

1. **root/owner 安全边界**
   - [FACT] `_is_in_root_scope()` 只沿 `TaskStore.get()` 返回的 parent 链向上查 root；`_require_owned_task()` 在 root scope 后核对固定 owner，不读取 Store 内部字典。`src/agents/task_tools.py::_is_in_root_scope`、`_require_owned_task`

2. **create_task**
   - [FACT] factory 用闭包固定 Store/root/owner。create 的默认 parent 是 root，显式 parent 和 dependency 必须留在 root 子树；通过后只构造一次 `TaskSpec`、调用一次 `TaskStore.create()` 并复用节点 converter。`src/agents/task_tools.py::create_task_tools`

3. **update_task**
   - [FACT] update 要求 scope 和 owner 匹配，将模型给出的 revision 原样交给 `TaskStore.transition()`；私有 sentinel 保留省略 result 与显式 `None` 的区别，不实现自动重试或 summary/图关系修改。`src/agents/task_tools.py::create_task_tools`

4. **list_tasks**
   - [FACT] status 与 ready_only 互斥；普通查询复用 `TaskStore.list()`，ready 查询复用 `TaskStore.ready()`，随后按 owner/root 过滤并保留 Store 顺序。查询不改变 journal。`src/agents/task_tools.py::create_task_tools`

5. **真实 Agent 工具流**
   - [FACT] 调用方用普通循环注册三个工具后，fake model 已通过现有 Agent loop 完成 create→update/list→final；成功继续产生 requested/started/completed，Store 错误进入既有 tool error observation 后模型可恢复。未注册时模型 schema 与任务副作用均为空。`src/agents/tools.py::ToolRegistry.register`、`src/agents/tool_execution.py::_execute_tool_call_impl`

6. **公共接口与架构闭环**
   - [FACT] `agents.__all__` 只新增 `create_task_tools`；scope helper、状态输入别名、result sentinel 和三个闭包 handler 保持内部。`src/agents/__init__.py::__all__`
   - [FACT] 2026-07-31 CodeGraph 强制全量重建后为 `131 files / 4263 nodes / 12653 edges`，已收录 factory 和三个闭包。`update_task -> _require_owned_task -> _is_in_root_scope`、`update_task -> transition`、`list_tasks -> ready` 可查询；factory 静态 callers 全是测试，证明默认 Agent 未自动接入。

### 验证

- [FACT] Task Graph/Event/Planning Tools 定向回归为 `103 passed, 189 subtests passed`。
- [FACT] Tools/Agent/RunStream/Public API 回归为 `82 passed, 389 subtests passed`。
- [FACT] `python -m compileall -q src tests` 通过。
- [FACT] 全量为 `1016 passed, 885 subtests passed, 6 failed`；仍是五个 Windows 中文路径示例加载问题和一个既存 repo-map 接入断言，没有新增失败。

### 架构决策与未实现范围

- [FACT] Planning Tools 是普通、显式注册的 `FunctionTool`，没有新增基类、registrar、错误映射层、查询对象或第二套节点投影。
- [FACT] `TaskStore` 仍是状态、CAS、result/error、级联和 TaskEvent journal 的唯一事实来源；工具层不读取 `_nodes`、不调用 reducer、不直接写 journal。
- [FACT] 当前没有自动注册、RunState/RunResult task link、CLI task view、Session/文件持久化、SubAgent 或 scheduler；这些边界留给后续独立设计。

## 2026-07-31：Task Runtime Step 04——显式 run 关联、snapshot v3 与同步兼容

### 升级结果

[FACT] 调用方现在可以在 `RunConfig` 中同时给出 `TaskStore` 与 `task_id`，让 fresh/resume run 在任何 memory、lifecycle、模型、工具或 session 副作用前确认节点。运行结束或暂停时，统一结果构造点再次读取同一节点，把当时的 revision 冻结进 `RunResult`；结果可写成 snapshot v3 并恢复。`SyncRunCollector` 只完整消费现有 `RunStream` 并返回 producer 原结果，三种 Runner 入口继续共用同一生产链。`src/agents/run_config.py`、`src/agents/run_loop.py`、`src/agents/run_state.py`、`src/agents/result.py`、`src/agents/runner.py`

### 七课交付

1. **RunConfig 显式绑定**
   - [FACT] 新增可选 `task_store/task_id`，要求同时为空或同时提供；id 拒绝非字符串和空白值。类型引用留在 `TYPE_CHECKING`，没有 wrapper 或自动 root。`src/agents/run_config.py::RunConfig`

2. **RunState 关联与副作用前校验**
   - [FACT] `RunState` 保存可选 task id/revision。fresh 入口先 `TaskStore.get()`；bound resume 必须给相同 id 的 Store并以当前 revision 更新同步点，旧 snapshot 可保持未绑定或由调用方显式开始绑定。`src/agents/run_loop.py::_resolve_task_binding`、`_run_agent_loop_impl`

3. **RunResult 最终 revision**
   - [FACT] completed、stopped、model failure 与 approval pause 共用 `_build_result_and_save_session()`；该处在构造结果前读取一次最新节点 revision。revision 只表示被绑定节点自身，结果生成后不随 Store 变化。`src/agents/run_loop.py::_build_result_and_save_session`、`src/agents/run_state.py::build_run_result`

4. **Snapshot schema v3**
   - [FACT] writer 输出 v3，reader 接受 legacy、v1、v2、v3并拒绝未知版本；v2/v3 恢复 model attempts，只有 v3 恢复成对 task link。snapshot 不保存 Store、TaskNode、TaskEvent、controller 或事件流。`src/agents/run_state.py::RunStateSnapshot`、`run_state_snapshot_from_dict`

5. **最薄 SyncRunCollector**
   - [FACT] 公共 `collect(stream)` 完整迭代已有 stream，再调用私有 `_wait_result()` 取 producer 原对象；不缓存事件、不重建结果，也不包装异常或取消。`src/agents/runner.py::SyncRunCollector.collect`

6. **Runner 与公共兼容**
   - [FACT] `run_async()` 只把 `run_stream()` 交给 collector；`run_sync()` 的 `asyncio.run()` 和活动 event loop 错误保持不变，`run_stream()` 继续惰性启动。包根只新增 collector 导出；现有 `dataclasses.replace()` 配置路径保留 task 绑定字段。`src/agents/runner.py::Runner`、`src/agents/__init__.py::__all__`

7. **组合验收、CodeGraph 与架构文档**
   - [FACT] 离线组合场景贯通 Store/node、显式绑定、Planning Tool 更新、最终 revision、v3 snapshot、恢复和三种入口；没有新增第二个 reducer、task-specific RunEvent 或持久化层。强制重建 CodeGraph 后为 `132 files / 4351 nodes / 12978 edges`，结果链仍是 `_run_agent_loop_impl -> _build_result_and_save_session -> build_run_result`，collector 只调用现有 stream 等待点。

### 验证

- [FACT] Task Graph/Event/Planning/runtime 定向矩阵为 `114 passed, 202 subtests passed`。
- [FACT] RunState/Result/Runner/RunStream/AgentConfig/Chat/Public API 定向矩阵为 `132 passed, 450 subtests passed`；两组不重叠，合计 `246 passed, 652 subtests passed`。
- [FACT] 全量为 `1044 passed, 922 subtests passed, 6 failed`；仍是五个 Windows 中文路径示例加载问题和一个既存 repo-map 接入断言，没有新增失败。

### 架构决策与未实现范围

- [FACT] `TaskStore` 仍是节点、CAS、级联和 TaskEvent journal 的唯一事实来源；run 只读一个节点，并在 state/result/snapshot 中保存 id/revision 两个标量。
- [FACT] Planning Tools 仍由调用方显式创建和注册；run 绑定不会自动建立 root、owner 或工具。
- [FACT] 当前没有 Session TaskEvent writer、JsonSession task schema、CLI task view、SubAgent 调度、数据库、跨进程锁或任务图全局 revision；这些需求出现后再单独设计。
