from __future__ import annotations

import asyncio
from threading import Event
from time import perf_counter

import pytest

import agents.run_loop as run_loop_module
import agents.runner as runner_module
from agents import (
    Agent,
    AgentMemory,
    ModelErrorKind,
    ModelResponseError,
    ModelSettings,
    ResponseCompleted,
    ResponseCreated,
    RunConfig,
    TextDelta,
    TextDone,
    ToolCall,
    ToolRegistry,
    function_tool,
)
from agents.contracts import ModelResponse
from agents.events import (
    ModelAttemptStartedEvent,
    ModelCompletedEvent,
    ModelStartedEvent,
    RunCancelledError,
    RunCancelledEvent,
    RunCompletedEvent,
    RunStartedEvent,
    ToolCompletedEvent,
    ToolRequestedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    is_terminal_event,
)
from agents.runner import Runner


class CountingFinalModel:
    def __init__(self, answer: str = "done") -> None:
        self.answer = answer
        self.calls = 0

    def decide(self, messages, tool_specs):
        self.calls += 1
        return ToolCall("final_answer", {"answer": self.answer}, "call-final")


class ResponseSequenceModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = iter(responses)
        self.calls = 0

    def get_response(self, messages, tool_specs):
        self.calls += 1
        return next(self._responses)

    def record_tool_output(self, tool_call, output) -> None:
        pass


class NativeStreamFinalModel:
    def __init__(self, answer: str = "native done") -> None:
        self.answer = answer
        self.calls = 0

    async def stream_response(self, request):
        self.calls += 1
        response_id = f"{request.request_id}:response"
        yield ResponseCreated(
            attempt_id=request.attempt_id,
            sequence=1,
            response_id=response_id,
        )
        yield TextDelta(
            attempt_id=request.attempt_id,
            sequence=2,
            output_index=0,
            content_index=0,
            delta=self.answer,
        )
        yield TextDone(
            attempt_id=request.attempt_id,
            sequence=3,
            output_index=0,
            content_index=0,
            text=self.answer,
        )
        yield ResponseCompleted(
            attempt_id=request.attempt_id,
            sequence=4,
            response_id=response_id,
            output=[],
        )


async def failing_producer(agent, task, config, runtime, *, run_state=None):
    try:
        await runtime.emit(RunStartedEvent, agent_name=agent.name)
        raise RuntimeError("producer failed")
    finally:
        await runtime.events.close()


def test_run_stream_is_lazy_and_emits_one_ordered_terminal_sequence() -> None:
    model = CountingFinalModel()
    stream = Runner.run_stream(Agent(memory=AgentMemory(), model=model), "Finish.")

    assert model.calls == 0

    async def scenario():
        events = [event async for event in stream]
        return events, await stream._wait_result()

    events, result = asyncio.run(scenario())

    assert model.calls == 1
    assert [type(event) for event in events] == [
        RunStartedEvent,
        TurnStartedEvent,
        ModelStartedEvent,
        ModelAttemptStartedEvent,
        ModelCompletedEvent,
        ToolRequestedEvent,
        ToolStartedEvent,
        ToolCompletedEvent,
        TurnCompletedEvent,
        RunCompletedEvent,
    ]
    assert [event.sequence for event in events] == list(range(1, 11))
    assert sum(is_terminal_event(event) for event in events) == 1
    assert stream.controller.terminal_event is events[-1]
    assert len(result.model_attempts) == 1
    assert result.last_model_terminal_reason == "completed"
    assert len(result.to_state()["model_attempts"]) == 1


def test_native_stream_model_works_through_async_and_stream_entries() -> None:
    async_model = NativeStreamFinalModel("async native")
    stream_model = NativeStreamFinalModel("stream native")

    async def scenario():
        async_result = await Runner.run_async(
            Agent(memory=AgentMemory(), model=async_model),
            "Async native.",
        )
        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=stream_model),
            "Stream native.",
        )
        events = [event async for event in stream]
        return async_result, await stream._wait_result(), events

    async_result, stream_result, events = asyncio.run(scenario())

    assert async_model.calls == 1
    assert stream_model.calls == 1
    assert async_result.final_answer == "async native"
    assert stream_result.final_answer == "stream native"
    assert len(async_result.raw_responses) == 1
    assert len(stream_result.raw_responses) == 1
    assert async_result.model_attempts[0].mode == "stream"
    assert stream_result.model_attempts[0].mode == "stream"
    assert sum(isinstance(event, ModelAttemptStartedEvent) for event in events) == 1
    assert sum(isinstance(event, ModelCompletedEvent) for event in events) == 1
    assert sum(is_terminal_event(event) for event in events) == 1


def test_non_stream_fallback_completes_once_through_public_stream() -> None:
    class NonStreamFallbackModel:
        model = "model-a"

        def __init__(self) -> None:
            self.stream_calls = 0
            self.non_stream_calls = 0

        async def stream_response(self, request):
            self.stream_calls += 1
            raise ModelResponseError(
                "stream unavailable",
                kind=ModelErrorKind.STREAM_UNAVAILABLE,
            )
            yield

        def get_response_for_request(self, request):
            self.non_stream_calls += 1
            return ModelResponse(
                response_id="response-fallback",
                output=[],
                output_text="fallback done",
                tool_calls=[],
            )

    model = NonStreamFallbackModel()
    stream = Runner.run_stream(
        Agent(
            memory=AgentMemory(),
            model=model,
            model_settings=ModelSettings(allow_non_stream_fallback=True),
        ),
        "Fallback to a complete response.",
    )

    async def scenario():
        events = [event async for event in stream]
        return events, await stream._wait_result()

    events, result = asyncio.run(scenario())
    attempt_events = [
        event for event in events if isinstance(event, ModelAttemptStartedEvent)
    ]

    assert model.stream_calls == 1
    assert model.non_stream_calls == 1
    assert result.final_answer == "fallback done"
    assert [attempt.mode for attempt in result.model_attempts] == [
        "stream",
        "non_stream",
    ]
    assert [event.mode for event in attempt_events] == ["stream", "non_stream"]
    assert attempt_events[-1].reason == "non_stream_fallback"
    assert len(result.raw_responses) == 1
    assert sum(isinstance(event, ModelCompletedEvent) for event in events) == 1
    assert len(result.to_state()["model_attempts"]) == 2


def test_run_stream_emits_tool_events_before_the_next_model_turn() -> None:
    @function_tool
    def echo_text(text: str) -> str:
        """Return the supplied text."""
        return text

    registry = ToolRegistry()
    registry.register(echo_text)
    model = ResponseSequenceModel(
        [
            ModelResponse(
                response_id="response-tool",
                output=[],
                output_text=None,
                tool_calls=[ToolCall("echo_text", {"text": "hello"}, "call-echo")],
            ),
            ModelResponse(
                response_id="response-final",
                output=[],
                output_text="done",
                tool_calls=[],
            ),
        ]
    )
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=model, tool_registry=registry),
        "Use the tool.",
    )

    async def scenario():
        return [event async for event in stream]

    events = asyncio.run(scenario())
    event_types = [type(event) for event in events]

    assert event_types.index(ToolRequestedEvent) < event_types.index(ToolStartedEvent)
    assert event_types.index(ToolStartedEvent) < event_types.index(ToolCompletedEvent)
    assert event_types.index(ToolCompletedEvent) < event_types.index(RunCompletedEvent)


def test_run_stream_rejects_a_second_iterator() -> None:
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=CountingFinalModel()),
        "Finish.",
    )

    assert stream.__aiter__() is stream
    with pytest.raises(RuntimeError, match="only be iterated once"):
        stream.__aiter__()


def test_concurrent_run_streams_keep_run_ids_and_sequences_isolated() -> None:
    async def collect(answer: str):
        model = CountingFinalModel(answer)
        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=model),
            f"Return {answer}.",
        )
        events = [event async for event in stream]
        return model, stream, events

    async def scenario():
        return await asyncio.gather(collect("one"), collect("two"))

    runs = asyncio.run(scenario())
    run_ids = {events[0].run_id for _, _, events in runs}

    assert len(run_ids) == 2
    for model, stream, events in runs:
        assert model.calls == 1
        assert {event.run_id for event in events} == {stream.controller.run_id}
        assert [event.sequence for event in events] == list(
            range(1, len(events) + 1)
        )
        assert sum(is_terminal_event(event) for event in events) == 1


def test_aclose_before_iteration_does_not_start_the_run() -> None:
    model = CountingFinalModel()
    stream = Runner.run_stream(Agent(memory=AgentMemory(), model=model), "Finish.")

    async def scenario():
        await stream.aclose()
        await stream.aclose()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())

    assert model.calls == 0


def test_async_context_drains_after_break_without_cancelling_the_run() -> None:
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=CountingFinalModel()),
        "Finish.",
        RunConfig(event_queue_size=1),
    )

    async def scenario():
        async with stream:
            async for _ in stream:
                break
        await stream.aclose()

    asyncio.run(scenario())

    assert stream.controller.terminal_event is not None
    assert not stream.controller.token.cancelled
    assert stream._producer_task is not None and stream._producer_task.done()
    assert stream._cancel_task is not None and stream._cancel_task.done()
    assert stream._drain_task is not None and stream._drain_task.done()


def test_async_context_preserves_consumer_error_during_cleanup(monkeypatch) -> None:
    monkeypatch.setattr(runner_module, "_run_agent_loop_with_events", failing_producer)
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=CountingFinalModel()),
        "Fail.",
        RunConfig(event_queue_size=1),
    )

    async def scenario():
        async with stream:
            async for _ in stream:
                raise ValueError("consumer failed")

    with pytest.raises(ValueError, match="consumer failed"):
        asyncio.run(scenario())


def test_aclose_propagates_producer_error_without_consumer_error(monkeypatch) -> None:
    monkeypatch.setattr(runner_module, "_run_agent_loop_with_events", failing_producer)
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=CountingFinalModel()),
        "Fail.",
        RunConfig(event_queue_size=1),
    )

    async def scenario():
        async with stream:
            async for _ in stream:
                break

    with pytest.raises(RuntimeError, match="producer failed"):
        asyncio.run(scenario())


def test_cancel_before_iteration_wakes_monitor_and_skips_model() -> None:
    model = CountingFinalModel()
    stream = Runner.run_stream(Agent(memory=AgentMemory(), model=model), "Stop.")
    assert stream.controller.cancel("before-start") is True
    events = []

    async def scenario():
        async with stream:
            events.extend([event async for event in stream])

    with pytest.raises(RunCancelledError, match="before-start"):
        asyncio.run(scenario())

    assert model.calls == 0
    assert [type(event) for event in events] == [
        RunStartedEvent,
        RunCancelledEvent,
    ]
    assert stream.controller.terminal_event is events[-1]
    assert stream._cancel_task is not None and stream._cancel_task.done()


def test_resume_stream_preserves_producer_errors_and_cancellation(monkeypatch) -> None:
    resume_agent_loop = run_loop_module.resume_agent_loop

    def fail_resume(*args, **kwargs):
        raise RuntimeError("resume producer failed")

    monkeypatch.setattr(run_loop_module, "resume_agent_loop", fail_resume)
    failing_stream = Runner.resume_stream(
        Agent(memory=AgentMemory(), model=CountingFinalModel()),
        runner_module.RunState(input="Resume failure."),
    )

    with pytest.raises(RuntimeError, match="resume producer failed"):
        asyncio.run(runner_module.SyncRunCollector().collect(failing_stream))

    monkeypatch.setattr(run_loop_module, "resume_agent_loop", resume_agent_loop)
    model = CountingFinalModel()
    cancelled_stream = Runner.resume_stream(
        Agent(memory=AgentMemory(), model=model),
        runner_module.RunState(input="Resume cancellation."),
    )
    assert cancelled_stream.controller.cancel("resume cancelled") is True

    with pytest.raises(RunCancelledError, match="resume cancelled"):
        asyncio.run(runner_module.SyncRunCollector().collect(cancelled_stream))

    assert model.calls == 0


def test_zero_grace_cancel_from_worker_thread_forces_one_terminal() -> None:
    entered = Event()
    release = Event()

    class BlockingModel:
        def get_response(self, messages, tool_specs):
            entered.set()
            release.wait()
            return ModelResponse(output=[], output_text="late", tool_calls=[])

    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=BlockingModel()),
        "Wait.",
        RunConfig(event_queue_size=2, cancellation_grace_period=0),
    )
    events = []

    async def scenario():
        async def cancel_from_worker():
            def wait_and_cancel():
                assert entered.wait(1)
                return stream.controller.cancel("timeout")

            assert await asyncio.to_thread(wait_and_cancel) is True

        cancel_task = asyncio.create_task(cancel_from_worker())
        try:
            async with stream:
                events.extend([event async for event in stream])
        finally:
            release.set()
            await cancel_task

    with pytest.raises(RunCancelledError, match="timeout"):
        asyncio.run(scenario())

    assert sum(isinstance(event, RunCancelledEvent) for event in events) == 1
    assert not any(isinstance(event, ModelCompletedEvent) for event in events)
    assert stream.controller.finished
    assert stream._producer_task is not None and stream._producer_task.cancelled()
    assert stream._cancel_task is not None and stream._cancel_task.done()


def test_one_hundred_cancel_monitors_do_not_wait_in_worker_threads() -> None:
    async def cancel_one() -> float:
        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=CountingFinalModel()),
            "Stop.",
        )
        started = perf_counter()
        assert stream.controller.cancel("load") is True
        try:
            async with stream:
                _ = [event async for event in stream]
        except RunCancelledError:
            pass
        return perf_counter() - started

    async def scenario():
        return await asyncio.gather(*(cancel_one() for _ in range(100)))

    durations = sorted(asyncio.run(scenario()))

    assert durations[94] < 1


def test_run_async_returns_the_same_producer_result_without_running_twice(
    monkeypatch,
) -> None:
    model = CountingFinalModel("async done")
    agent = Agent(memory=AgentMemory(), model=model)
    stream = Runner.run_stream(agent, "Finish asynchronously.")
    monkeypatch.setattr(
        Runner,
        "run_stream",
        classmethod(lambda cls, agent, task, config=None: stream),
    )

    result = asyncio.run(Runner.run_async(agent, "Finish asynchronously."))

    assert model.calls == 1
    assert result is stream._producer_task.result()
    assert result.final_answer == "async done"
    assert result.reached_final_answer is True
    assert result.current_turn == 1


def test_run_async_drains_tool_events_and_executes_the_tool_once() -> None:
    tool_calls: list[str] = []

    @function_tool
    def echo_text(text: str) -> str:
        """Return the supplied text."""
        tool_calls.append(text)
        return text

    registry = ToolRegistry()
    registry.register(echo_text)
    model = ResponseSequenceModel(
        [
            ModelResponse(
                response_id="response-tool",
                output=[],
                output_text=None,
                tool_calls=[ToolCall("echo_text", {"text": "hello"}, "call-echo")],
            ),
            ModelResponse(
                response_id="response-final",
                output=[],
                output_text="done",
                tool_calls=[],
            ),
        ]
    )

    result = asyncio.run(
        Runner.run_async(
            Agent(memory=AgentMemory(), model=model, tool_registry=registry),
            "Use the tool.",
            RunConfig(event_queue_size=1),
        )
    )

    assert model.calls == 2
    assert tool_calls == ["hello"]
    assert result.final_answer == "done"
    assert any(item.item_type == "tool_result" for item in result.new_items)


def test_run_async_returns_approval_pause_and_model_error_results() -> None:
    approval_calls: list[str] = []

    def delete_file(path: str) -> str:
        approval_calls.append(path)
        return path

    registry = ToolRegistry()
    registry.register(function_tool(delete_file, needs_approval=True))
    approval_model = ResponseSequenceModel(
        [
            ModelResponse(
                response_id="response-approval",
                output=[],
                output_text=None,
                tool_calls=[
                    ToolCall(
                        "delete_file",
                        {"path": "notes.txt"},
                        "call-approval",
                    )
                ],
            )
        ]
    )

    approval_result = asyncio.run(
        Runner.run_async(
            Agent(
                memory=AgentMemory(),
                model=approval_model,
                tool_registry=registry,
            ),
            "Delete notes.",
        )
    )

    class FailingModel:
        def __init__(self) -> None:
            self.calls = 0

        def get_response(self, messages, tool_specs):
            self.calls += 1
            raise ValueError("bad response")

    failing_model = FailingModel()
    failed_result = asyncio.run(
        Runner.run_async(
            Agent(memory=AgentMemory(), model=failing_model),
            "Fail.",
        )
    )

    assert approval_model.calls == 1
    assert approval_calls == []
    assert approval_result.has_pending_approvals is True
    assert approval_result.reached_final_answer is False
    assert failing_model.calls == 1
    assert failed_result.new_items[-1].item_type == "model_error"


def test_run_async_preserves_unexpected_producer_error(monkeypatch) -> None:
    def fail_loop(*args, **kwargs):
        raise RuntimeError("producer failed")

    monkeypatch.setattr(run_loop_module, "run_agent_loop", fail_loop)

    with pytest.raises(RuntimeError, match="producer failed"):
        asyncio.run(
            Runner.run_async(
                Agent(memory=AgentMemory(), model=CountingFinalModel()),
                "Fail.",
            )
        )


def test_run_async_preserves_explicit_cancellation(monkeypatch) -> None:
    model = CountingFinalModel()
    agent = Agent(memory=AgentMemory(), model=model)
    stream = Runner.run_stream(agent, "Stop.")
    assert stream.controller.cancel("async cancel") is True
    monkeypatch.setattr(
        Runner,
        "run_stream",
        classmethod(lambda cls, agent, task, config=None: stream),
    )

    with pytest.raises(RunCancelledError, match="async cancel"):
        asyncio.run(Runner.run_async(agent, "Stop."))

    assert model.calls == 0


def test_run_sync_calls_run_async_once_and_preserves_its_result(monkeypatch) -> None:
    calls: list[tuple[str, RunConfig | None]] = []
    expected = object()

    async def fake_run_async(cls, agent, task, config=None):
        calls.append((task, config))
        return expected

    monkeypatch.setattr(Runner, "run_async", classmethod(fake_run_async))
    config = RunConfig(max_turns=1)

    result = Runner.run_sync(
        Agent(memory=AgentMemory(), model=CountingFinalModel()),
        "Bridge.",
        config,
    )

    assert result is expected
    assert calls == [("Bridge.", config)]


def test_run_sync_rejects_a_running_event_loop_before_starting_model() -> None:
    model = CountingFinalModel()
    agent = Agent(memory=AgentMemory(), model=model)

    async def scenario():
        with pytest.raises(RuntimeError, match="await Runner.run_async"):
            Runner.run_sync(agent, "Wrong entrypoint.")

    asyncio.run(scenario())

    assert model.calls == 0
