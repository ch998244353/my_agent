from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import TracebackType
from typing import TYPE_CHECKING

from .events import RunCancelledEvent, RunEvent, is_terminal_event
from .result import RunResult
from .run_config import RunConfig
from .run_control import (
    RunController,
    _create_run_runtime,
    _run_agent_loop_with_events,
)
from .run_loop import _run_trace
from .run_state import RunState
from .tracing import custom_span

if TYPE_CHECKING:
    from .agent import Agent


# 位于公共 Runner 入口和内部 runtime 之间。它不执行模型逻辑，只保存本次运行所需的对象，并把内部事件逐条交给调用方
class RunStream(AsyncIterator[RunEvent]):
    def __init__(
        self,
        agent: Agent,
        task: str,
        config: RunConfig | None = None,
        *,
        _resume_state: RunState | None = None,
    ) -> None:
        self._agent = agent
        self._task = task
        self._config = config
        self._resume_state = _resume_state
        self._runtime = _create_run_runtime(config)
        self._producer_task: asyncio.Task[RunResult] | None = None   # 执行 Agent 主循环的异步任务
        self._cancel_event = asyncio.Event()   # 异步取消通知事件,标志是否取消监控任务
        self._cancel_task: asyncio.Task[None] | None = None  # 保存取消监控任务
        self._drain_task: asyncio.Task[None] | None = None  # 保存事件排空任务
        self._iterator_claimed = False  # 表示这个 Stream 是否已经被某个异步迭代器占用, 保证 RunStream 只能被迭代一次，避免两个消费者分别拿走不同事件
        self._finished = False # agent运行是否结束
        self._closed = False # 该stream是否可以被读取

    @property
    def controller(self) -> RunController:
        return self._runtime.controller

    # 避免两个消费者分别拿走一部分事件，造成事件缺失
    def __aiter__(self) -> RunStream:
        if self._iterator_claimed:
            raise RuntimeError("RunStream can only be iterated once.")
        self._iterator_claimed = True
        return self

    async def __aenter__(self) -> RunStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            await self.aclose()
        except BaseException:
            if exc_type is None:
                raise
        return False

    # 第一次读取事件时创建 producer task,producer ,并完成
    '''
    取得当前正在运行的事件循环。
    把 Controller 的通知安全地转交给这个循环。
    同时启动 producer 和取消信号等待任务。
    '''
    def _start_once(self) -> asyncio.Task[RunResult]:
        if self._producer_task is None:
            loop = asyncio.get_running_loop()
            cancel_event = self._cancel_event
            self._runtime.controller._bind_cancel_wakeup(
                lambda: loop.call_soon_threadsafe(cancel_event.set)
            )
            self._producer_task = asyncio.create_task(
                self._produce()
            )
            self._cancel_task = asyncio.create_task(
                self._monitor_cancel(self._producer_task)
            )
        return self._producer_task

    async def _produce(self) -> RunResult: # 在 Agent 开始生产事件前建立 event_stream span
        with _run_trace(self._agent, self._config):
            with custom_span("event_stream") as event_stream_span:
                try:
                    return await _run_agent_loop_with_events(
                        self._agent,
                        self._task,
                        self._config,
                        self._runtime,
                        run_state=self._resume_state,
                    )
                except asyncio.CancelledError:
                    if (
                        self._runtime.token.cancelled
                        and not self._runtime.reducer.finished
                    ):
                        reason = self._runtime.token.reason
                        assert reason is not None
                        await self._runtime.emit(RunCancelledEvent, reason=reason)
                    raise
                finally:
                    event_stream_span.record.span_data.data.update(
                        self._runtime.metrics_snapshot()
                    )

    # 等待取消或正常完成
    async def _monitor_cancel(
        self,
        producer_task: asyncio.Task[RunResult],
    ) -> None:
        cancel_wait = asyncio.create_task(self._cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                (producer_task, cancel_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done:
                await self._runtime.shutdown_after_cancel(producer_task)
        finally:
            if not cancel_wait.done():
                cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)


    # RunStream 的核心消费函数
    async def __anext__(self) -> RunEvent:
        if self._finished or self._closed:  # 这个流是否已经到达终点
            raise StopAsyncIteration
        if not self._iterator_claimed:
            self._iterator_claimed = True
        producer_task = self._start_once()  # 确保生产者任务只启动一次，并返回对应的 asyncio.Task
        try:
            event = await self._runtime.events.get()  # 从事件队列中取出下一个事件；如果当前没有事件，就暂停等待
        except StopAsyncIteration:
            self._finished = True
            raise
        if is_terminal_event(event):  # 读到 terminal 后等待 producer 收尾，再结束后续迭代
            self._finished = True
            tasks = tuple(
                task
                for task in (producer_task, self._cancel_task)
                if task is not None
            )
            await asyncio.gather(*tasks, return_exceptions=True)
        return event


    # 取出所有queue中的事件,发现terminal标记结束
    async def _drain_to_terminal(self) -> None:
        try:
            while True:
                event = await self._runtime.events.get()
                if is_terminal_event(event):
                    self._finished = True
                    return
        except StopAsyncIteration:
            self._finished = True


    # 等待已经启动的 Agent 主循环，并直接取回它生成的原始 RunResult
    async def _wait_result(self) -> RunResult:
        producer_task = self._producer_task
        if producer_task is None:
            raise RuntimeError("RunStream has not started.")
        await asyncio.gather(producer_task, return_exceptions=True)
        try:
            return producer_task.result()
        except asyncio.CancelledError:
            self._runtime.token.raise_if_cancelled()
            raise

    async def result(self) -> RunResult:
        """Return the producer's original result after this stream has started."""
        return await self._wait_result()


    # 禁止外部读取 stream 事件, 排空所有事件 , 等待 agent 正常结束 , close 表示 该runstrwam不可读取
    async def aclose(self) -> None:

        # 1.保证只关闭一次,不会重复创建 _drain_task,_closed 关闭的是 RunStream 对外提供事件的能力
        if self._closed:
            return
        self._closed = True

        # 2.producer_task is None 说明 _start_once() 从未执行。
        producer_task = self._producer_task
        if producer_task is None:
            self._finished = True
            return

        # 3.如果外部提前关闭，就启动排空任务
        if not self._finished:
            self._drain_task = asyncio.create_task(self._drain_to_terminal())

        # 4.等待所有后台任务结束
        tasks = tuple(
            task
            # 1.Agent 主循环  2._cancel_task  3._drain_task
            for task in (producer_task, self._cancel_task, self._drain_task)
            if task is not None
        )
        await asyncio.gather(*tasks, return_exceptions=True) # 表示即使某个任务抛出异常，也先等待其他任务结束

        # 5.把后台任务的异常重新抛出来
        await self._wait_result()
        if self._cancel_task is not None:
            self._cancel_task.result()
        if self._drain_task is not None:
            self._drain_task.result()


class SyncRunCollector:
    async def collect(self, stream: RunStream) -> RunResult:
        async with stream:
            async for _event in stream:
                pass
            return await stream._wait_result()


class Runner:
    @classmethod
    def run_stream(
        cls,
        agent: Agent,
        task: str,
        config: RunConfig | None = None,
    ) -> RunStream:
        return RunStream(agent, task, config)

    @classmethod
    def resume_stream(
        cls,
        agent: Agent,
        run_state: RunState,
        config: RunConfig | None = None,
    ) -> RunStream:
        if not isinstance(run_state, RunState):
            raise TypeError("run_state must be a RunState.")
        task = run_state.input if isinstance(run_state.input, str) else ""
        return RunStream(
            agent,
            task,
            config,
            _resume_state=run_state,
        )

    @classmethod
    async def run_async(
        cls,
        agent: Agent,
        task: str,
        config: RunConfig | None = None,
    ) -> RunResult:
        # 创建事件流 → 读掉所有事件 → 取得 Agent 结果 → 自动清理 → 返回结果
        return await SyncRunCollector().collect(cls.run_stream(agent, task, config))

    @classmethod
    async def resume_async(
        cls,
        agent: Agent,
        run_state: RunState,
        config: RunConfig | None = None,
    ) -> RunResult:
        return await SyncRunCollector().collect(
            cls.resume_stream(agent, run_state, config)
        )

    @classmethod
    def run_sync(
        cls,
        agent: Agent,
        task: str,
        config: RunConfig | None = None,
    ) -> RunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cls.run_async(agent, task, config))
        raise RuntimeError(
            "Runner.run_sync() cannot be called from a running event loop; "
            "use 'await Runner.run_async(...)' instead."
        )

    @classmethod
    def resume_sync(
        cls,
        agent: Agent,
        run_state: RunState,
        config: RunConfig | None = None,
    ) -> RunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cls.resume_async(agent, run_state, config))
        raise RuntimeError(
            "Runner.resume_sync() cannot be called from a running event loop; "
            "use 'await Runner.resume_async(...)' instead."
        )
