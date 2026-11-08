from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from threading import Event, Lock
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from .contracts import RunItem
from .event_compat import emit_run_item_event
from .events import (
    EventT,
    RunAlreadyFinishedError,
    RunCancelledEvent,
    RunCancelledError,
    RunCompletedEvent,
    RunErrorEvent,
    RunEvent,
    RunEventReducer,
    RunFailedEvent,
    RunStartedEvent,
    RunStoppedEvent,
    TerminalRunEvent,
    is_terminal_event,
    utc_now,
)
from .run_config import RunConfig

if TYPE_CHECKING:
    from .agent import Agent
    from .result import RunResult
    from .run_state import RunState


_CLOSE_SENTINEL = object()


class CancellationToken: # 能被不同线程安全读取的取消信号
    def __init__(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string.")
        self._run_id = run_id
        self._cancelled = Event()  # 保存取消信号
        self._lock = Lock()  # 保护需要一起写入的数据
        self._reason: str | None = None
        self._requested_at: datetime | None = None
        self._requested_at_monotonic: float | None = None

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    @property
    def requested_at(self) -> datetime | None:  # 读取取消请求时间
        with self._lock:
            return self._requested_at

    def _get_monotonic_request_time(self) -> float | None:
        with self._lock:
            return self._requested_at_monotonic

    def cancel(self, reason: str) -> bool:  # 这个函数记录第一次取消，接受非空原因，输出这次调用是否真正改变了状态
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string.")
        with self._lock:
            if self._cancelled.is_set():
                return False
            self._reason = reason
            self._requested_at = utc_now()
            self._requested_at_monotonic = perf_counter() #记录当前这一刻的高精度计时器数值
            self._cancelled.set()
            return True

    def wait(self, timeout: float | None = None) -> bool:  # 这个函数让同步 worker 等待取消，接受可选超时秒数，输出等待期间是否发生了取消
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be None or a finite non-negative number.")
        return self._cancelled.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if not self.cancelled:
            return
        reason = self.reason
        assert reason is not None
        raise RunCancelledError(self._run_id, reason)


# 保存 controller 已经接受的一条追加要求
@dataclass(frozen=True)
class SteerMessage:
    steer_id: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.steer_id, str) or not self.steer_id.strip():
            raise ValueError("steer_id must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")


# 管理一次 run 的取消入口、生命周期和待处理 steer
class RunController:
    def __init__(self, run_id: str, token: CancellationToken) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string.")
        if not isinstance(token, CancellationToken):
            raise TypeError("token must be a CancellationToken.")
        if token.run_id != run_id:
            raise ValueError("token must belong to the same run.")
        self._run_id = run_id
        self._token = token
        self._lock = Lock()
        self._finished = False
        self._terminal_event: TerminalRunEvent | None = None
        self._pending_steers: deque[SteerMessage] = deque()
        self._next_steer_number = 1
        self._cancel_wakeup: Callable[[], None] | None = None

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def token(self) -> CancellationToken:
        return self._token

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    @property
    def terminal_event(self) -> TerminalRunEvent | None:
        with self._lock:
            return self._terminal_event

    @property
    # 查看还有多少条追加指令
    def pending_steer_count(self) -> int:
        with self._lock:
            return len(self._pending_steers)

    # 请求取消当前运行
    def cancel(self, reason: str = "user") -> bool:
        with self._lock:
            if self._finished:
                return False
            changed = self._token.cancel(reason)
            wakeup = self._cancel_wakeup if changed else None
        if wakeup is not None:
            wakeup()
        return changed
    
    # 给当前 RunController.._cancel_wakeup 绑定取消信号通知函数
    def _bind_cancel_wakeup(self, wakeup: Callable[[], None]) -> None: 
        if not callable(wakeup):
            raise TypeError("wakeup must be callable.")
        with self._lock:
            if self._cancel_wakeup is not None:
                raise RuntimeError("cancel wakeup is already bound.")
            self._cancel_wakeup = wakeup
            cancelled = self._token.cancelled
        if cancelled:
            wakeup()

# 给运行中的追加要求编号并放入 FIFO，输入用户文本，输出已经接受的 SteerMessage
    def steer(self, message: str) -> SteerMessage:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string.")
        with self._lock:
            if self._finished:
                terminal = self._terminal_event
                assert terminal is not None
                raise RunAlreadyFinishedError(self._run_id, terminal.sequence)
            steer = SteerMessage(
                steer_id=f"steer-{self._next_steer_number}",
                message=message,
            )
            self._next_steer_number += 1
            self._pending_steers.append(steer)
            return steer


# 让未来的 Agent loop 一次取走当前已经到达的 steer
    def _drain_steers(self) -> tuple[SteerMessage, ...]:
        with self._lock:
            if self._finished:
                raise RuntimeError("Cannot drain steers after the run has finished.")
            pending = tuple(self._pending_steers)
            self._pending_steers.clear()
            return pending


# 收 run 的真实终态事件，并把 controller 永久切换为 finished
    def _finish(self, terminal: TerminalRunEvent) -> bool:
        if not is_terminal_event(terminal):
            raise TypeError("terminal must be a terminal RunEvent.")
        if terminal.run_id != self._run_id:
            raise ValueError("terminal must belong to the same run.")
        with self._lock:
            if self._finished:
                return False
            self._pending_steers.clear()
            self._terminal_event = terminal
            self._finished = True
            return True


# 创建一个只能保存 RunEvent、并且容量固定的异步队列
class _EventChannel:
    def __init__(self, maxsize: int) -> None:
        if type(maxsize) is not int or maxsize < 1:
            raise ValueError("maxsize must be an integer greater than or equal to 1.")
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize=maxsize)
        self._max_depth = 0
        self._terminal_seen = False
        self._consumer_closed = asyncio.Event()
        self._producer_closed = False

    @property
    def maxsize(self) -> int:
        return self._queue.maxsize

    @property
    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def max_depth(self) -> int:
        return self._max_depth

    async def get(self) -> RunEvent:
        item = await self._queue.get()
        if item is _CLOSE_SENTINEL:
            raise StopAsyncIteration
        assert isinstance(item, RunEvent)
        return item

    '''
    消费者已退出，直接抛出错误。
    队列未满，立即写入。
    当前代码执行期间没有 await,不会在检查和写入之间被其他协程打断。
    只有队列真的满了，才进入后面的等待竞争。
    '''
    async def _send_or_consumer_closed(self, item: object) -> None: # 将一个 item 放入队列中
        if self._consumer_closed.is_set():
            raise RuntimeError("event consumer is closed.")
        if not self._queue.full():
            self._queue.put_nowait(item)
            return

        put_task = asyncio.create_task(self._queue.put(item))
        closed_task = asyncio.create_task(self._consumer_closed.wait())
        tasks = (put_task, closed_task)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if closed_task in done:
                raise RuntimeError("event consumer is closed.")
        finally:  # 清理临时任务
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def put(self, event: RunEvent) -> None: # 接受一个已经编号的 RunEvent
        if not isinstance(event, RunEvent):
            raise TypeError("event must be a RunEvent.")
        if self._terminal_seen:
            raise RuntimeError("cannot put an event after a terminal event.")
        if self._producer_closed:
            raise RuntimeError("event channel is closed.")

        await self._send_or_consumer_closed(event)
        self._max_depth = max(self._max_depth, self.qsize)
        if is_terminal_event(event):
            self._terminal_seen = True

    async def close(self) -> bool:
        if self._producer_closed or self._terminal_seen:
            return False
        self._producer_closed = True
        if self._consumer_closed.is_set():
            return True
        try:
            await self._send_or_consumer_closed(_CLOSE_SENTINEL)
        except RuntimeError:
            pass
        return True

#   把 _EventChannel 变成一个“异步可迭代对象”，让外部可以通过 async for 按顺序读取队列里的事件
    async def __aiter__(self) -> AsyncIterator[RunEvent]:
        try:
            while True:
                try:
                    item = await self.get()
                except StopAsyncIteration:
                    return
                yield item   # 把当前事件返回给 async for 的当前这一轮，然后暂停在这里
                if is_terminal_event(item):
                    return
        finally:
            self._producer_closed = True
            self._consumer_closed.set()


# 本次运行的内部运行环境
@dataclass
class _RunRuntime:
    run_id: str
    reducer: RunEventReducer
    token: CancellationToken  # 取消令牌
    controller: RunController  # 事件队列
    events: _EventChannel
    cancellation_grace_period: float  # 允许生产者任务自行结束的最长等待时间
    _max_publish_latency_seconds: float = 0.0   # 本次运行中，单次事件从开始发送，到成功写入事件队列所花费的最长时间
    _cancel_to_terminal_seconds: float | None = None  # 从请求取消到取消终止事件成功入队的耗时

    async def _deliver(self, event: EventT) -> EventT:
        publish_started = perf_counter()
        try:
            await self.events.put(event)
            delivered_at = perf_counter()
            self._max_publish_latency_seconds = max(
                self._max_publish_latency_seconds,
                delivered_at - publish_started,
            )
            if isinstance(event, RunCancelledEvent):
                cancel_started = self.token._get_monotonic_request_time()
                if cancel_started is not None:
                    self._cancel_to_terminal_seconds = max(
                        0.0,
                        delivered_at - cancel_started,
                    )
        finally:
            if is_terminal_event(event):
                self.controller._finish(event)
        return event

    def metrics_snapshot(self) -> dict[str, int | float]:
        metrics: dict[str, int | float] = {
            "max_publish_latency_seconds": self._max_publish_latency_seconds,
            "max_queue_depth": self.events.max_depth,
        }
        if self._cancel_to_terminal_seconds is not None:
            metrics["cancel_to_terminal_seconds"] = (
                self._cancel_to_terminal_seconds
            )
        return metrics

    async def emit(  # 统一创建并发送事件
        self,
        event_cls: type[EventT],
        /,
        *,
        turn_id: str | None = None,
        step_number: int | None = None,
        **payload: object,
    ) -> EventT:
        event = self.reducer.emit(
            event_cls,
            turn_id=turn_id,
            step_number=step_number,
            **payload,
        )
        return await self._deliver(event)

    async def emit_item(
        self,
        item: RunItem,
        *,
        turn_id: str | None = None,
    ) -> RunEvent | None:
        event = emit_run_item_event(self.reducer, item, turn_id=turn_id)
        if event is None:
            return None
        return await self._deliver(event)

    async def shutdown_after_cancel(
        self,
        producer_task: asyncio.Task[object],
    ) -> None:
        if not self.token.cancelled:
            raise RuntimeError("run must be cancelled before shutdown.")

        done, _ = await asyncio.wait(
            (producer_task,),
            timeout=self.cancellation_grace_period,
        )
        if producer_task not in done and not self.reducer.finished:
            producer_task.cancel()
        await asyncio.gather(producer_task, return_exceptions=True)

        if not self.reducer.finished:
            reason = self.token.reason
            assert reason is not None
            await self.emit(RunCancelledEvent, reason=reason)


# 让同步线程安全地调用异步事件循环中的 _RunRuntime 方法
@dataclass(frozen=True) 
class _LoopEventBridge:
    runtime: _RunRuntime  # 一次 Agent 运行所使用的运行时对象
    loop: asyncio.AbstractEventLoop  # 负责运行 runtime.emit() 等异步协程的事件循环

    @property
    def cancellation_token(self) -> CancellationToken:
        return self.runtime.token

    # 让同步 worker 发布直接事件
    def emit(
        self,
        event_cls: type[EventT],
        /,
        *,
        turn_id: str | None = None,
        step_number: int | None = None,
        **payload: object,
    ) -> EventT:
        return asyncio.run_coroutine_threadsafe(
            self.runtime.emit(
                event_cls,
                turn_id=turn_id,
                step_number=step_number,
                **payload,
            ),
            self.loop,
        ).result()

    # 将 item 转换为事件 发送
    def emit_item(
        self,
        item: RunItem,
        *,
        turn_id: str | None = None,
    ) -> RunEvent | None:
        return asyncio.run_coroutine_threadsafe(
            self.runtime.emit_item(item, turn_id=turn_id),
            self.loop,
        ).result()

    def raise_if_cancelled(self) -> None:
        self.runtime.token.raise_if_cancelled()

    # 取出 RunController 中已经排队的消息
    def drain_steers(self) -> tuple[SteerMessage, ...]:
        return self.runtime.controller._drain_steers()


# 在 worker 线程中运行原本的同步 run_agent_loop()，同时把 Agent 运行过程转换成异步事件，发送给前端或其他消费者
async def _run_agent_loop_with_events(
    agent: Agent,
    task: str,
    config: RunConfig | None,
    runtime: _RunRuntime,
    *,
    run_state: RunState | None = None,
) -> RunResult:
    from .run_loop import resume_agent_loop, run_agent_loop

    # 在异步主循环发出 RunStartedEvent
    await runtime.emit(RunStartedEvent, agent_name=agent.name)   
    bridge = _LoopEventBridge(runtime, asyncio.get_running_loop())

    # 把同步 Agent 循环放进 worker 线程
    try:
        if run_state is None:
            result = await asyncio.to_thread(
                run_agent_loop,
                agent,
                task,
                config,
                _event_bridge=bridge,
            )
        else:
            result = await asyncio.to_thread(
                resume_agent_loop,
                agent,
                run_state,
                config,
                _event_bridge=bridge,
            )
        runtime.token.raise_if_cancelled()

        turn_id = f"turn-{result.current_turn}" if result.current_turn else None
        model_error = next(
            (item for item in reversed(result.new_items) if item.item_type == "model_error"),
            None,
        )
        if model_error is not None:
            assert isinstance(model_error.payload, str)
            await runtime.emit(
                RunFailedEvent,
                turn_id=turn_id,
                step_number=model_error.step_number,
                error_type="ModelError",
                message=model_error.payload,
            )
            return result

        stop_item = next(
            (item for item in reversed(result.new_items) if item.item_type == "run_stopped"),
            None,
        )
        if not result.reached_final_answer and stop_item is not None:
            assert isinstance(stop_item.payload, str)
            await runtime.emit(
                RunStoppedEvent,
                turn_id=turn_id,
                step_number=stop_item.step_number,
                reason=stop_item.payload,
                resumable=stop_item.payload == "tool_approval_required",
            )
        else:
            final_item = next(
                (item for item in reversed(result.new_items) if item.item_type == "final_output"),
                None,
            )
            await runtime.emit(
                RunCompletedEvent,
                turn_id=turn_id,
                step_number=final_item.step_number if final_item is not None else None,
                reached_final_answer=result.reached_final_answer,
            )
        return result
    except RunCancelledError as exc:
        if not runtime.reducer.finished:
            await runtime.emit(RunCancelledEvent, reason=exc.reason)
        raise
    except Exception as exc:  # 无论 Agent 正常完成、失败还是 producer 意外退出，都会关闭事件通道
        if not runtime.reducer.finished:
            error_type = type(exc).__name__
            message = str(exc) or error_type
            await runtime.emit(
                RunErrorEvent,
                error_type=error_type,
                message=message,
            )
            await runtime.emit(
                RunFailedEvent,
                error_type=error_type,
                message=message,
            )
        raise
    finally:
        # 强制取消时 terminal 由 monitor 补发，不能在它之前关闭队列
        if runtime.reducer.finished:
            await runtime.events.close()


def _create_run_runtime(config: RunConfig | None = None) -> _RunRuntime:
    resolved_config = config if config is not None else RunConfig()
    run_id = uuid4().hex
    token = CancellationToken(run_id)
    return _RunRuntime(
        run_id=run_id,
        reducer=RunEventReducer(run_id),
        token=token,
        controller=RunController(run_id, token),
        events=_EventChannel(resolved_config.event_queue_size),
        cancellation_grace_period=resolved_config.cancellation_grace_period,
    )
