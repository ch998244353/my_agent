from __future__ import annotations

import asyncio
from threading import Event

import pytest

from agents import (
    Agent,
    AgentMemory,
    ModelSettings,
    Runner,
    RunState,
    ToolCall,
    ToolGuardrailFunctionOutput,
    ToolRegistry,
    function_tool,
    tool_input_guardrail,
)
from agents.contracts import ModelResponse, RunItem
from agents.events import (
    ApprovalRequiredEvent,
    ApprovalResolvedEvent,
    ModelAttemptStartedEvent,
    ModelCompletedEvent,
    ModelFallbackEvent,
    ModelRetryScheduledEvent,
    ModelStartedEvent,
    ModelTextDeltaEvent,
    RunAlreadyFinishedError,
    RunCancelledError,
    RunCancelledEvent,
    RunCompletedEvent,
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    RunStoppedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolRequestedEvent,
    ToolStartedEvent,
)
from agents.model_events import (
    ResponseCompleted,
    ResponseCreated,
    TextDelta,
    TextDone,
    ToolArgumentsDelta,
    ToolArgumentsDone,
    ToolCallStarted,
)
from agents.models import ModelErrorKind, ModelResponseError
from agents.run_config import RunConfig
from agents.run_control import (
    _LoopEventBridge,
    _create_run_runtime,
    _run_agent_loop_with_events,
)


class FinalModel:
    def decide(self, messages, tool_specs):
        return ToolCall("final_answer", {"answer": "done"}, "call-1")


class ResponseFinalModel:
    def get_response(self, messages, tool_specs, model_settings=None):
        return ModelResponse(
            response_id="response-1",
            output=[],
            output_text="done",
            tool_calls=[],
        )


class ResponseSequenceModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get_response(self, messages, tool_specs):
        return next(self.responses)

    def record_tool_output(self, tool_call, output):
        pass


def test_worker_bridge_delivers_direct_and_run_item_events() -> None:
    async def scenario() -> None:
        runtime = _create_run_runtime(RunConfig(event_queue_size=4))
        bridge = _LoopEventBridge(runtime, asyncio.get_running_loop())
        assert bridge.cancellation_token is runtime.token

        started = await asyncio.to_thread(
            bridge.emit,
            RunStartedEvent,
            agent_name="Agent",
        )
        completed = await asyncio.to_thread(
            bridge.emit_item,
            RunItem(
                "model_response",
                1,
                ModelResponse(
                    response_id="response-1",
                    output=[],
                    output_text="done",
                    tool_calls=[],
                ),
            ),
            turn_id="turn-1",
        )
        unmapped = await asyncio.to_thread(
            bridge.emit_item,
            RunItem("verification_result", 2, "passed"),
        )
        terminal = await asyncio.to_thread(
            bridge.emit,
            RunCompletedEvent,
            reached_final_answer=True,
        )

        assert isinstance(completed, ModelCompletedEvent)
        assert unmapped is None
        assert [started.sequence, completed.sequence, terminal.sequence] == [1, 2, 3]
        assert runtime.controller.terminal_event is terminal
        assert [event async for event in runtime.events] == [
            started,
            completed,
            terminal,
        ]
        with pytest.raises(RunAlreadyFinishedError):
            await asyncio.to_thread(
                bridge.emit,
                RunStartedEvent,
                agent_name="Late",
            )

    asyncio.run(scenario())


def test_internal_producer_emits_run_turn_and_terminal_events() -> None:
    async def scenario() -> None:
        runtime = _create_run_runtime()
        agent = Agent(memory=AgentMemory(), model=FinalModel(), name="Planner")

        result = await _run_agent_loop_with_events(
            agent,
            "Finish the task.",
            None,
            runtime,
        )
        events = [event async for event in runtime.events]

        assert result.final_answer == "done"
        assert result.reached_final_answer is True
        assert result.current_turn == 1
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
        assert all(event.turn_id == "turn-1" for event in events[1:])
        assert events[2].model_name == "FinalModel"
        assert events[3].model_name == "FinalModel"
        assert events[4].response_id is None
        assert events[4].tool_call_count == 1
        assert runtime.controller.terminal_event is events[-1]

        response_runtime = _create_run_runtime()
        response_agent = Agent(memory=AgentMemory(), model=ResponseFinalModel())
        response_result = await _run_agent_loop_with_events(
            response_agent,
            "Finish with a model response.",
            None,
            response_runtime,
        )
        response_events = [event async for event in response_runtime.events]

        assert response_result.final_answer == "done"
        assert [type(event) for event in response_events[2:6]] == [
            ModelStartedEvent,
            ModelAttemptStartedEvent,
            ModelTextDeltaEvent,
            ModelCompletedEvent,
        ]
        assert response_events[4].delta == "done"
        assert response_events[5].response_id == "response-1"
        assert response_events[5].tool_call_count == 0
        assert response_result.new_items[0].item_type == "model_response"

        stopped_runtime = _create_run_runtime()
        stopped_agent = Agent(memory=AgentMemory(), model=FinalModel())
        stopped = await _run_agent_loop_with_events(
            stopped_agent,
            "Do not start a turn.",
            RunConfig(max_turns=0),
            stopped_runtime,
        )
        stopped_events = [event async for event in stopped_runtime.events]

        assert stopped.reached_final_answer is False
        assert [type(event) for event in stopped_events] == [
            RunStartedEvent,
            RunStoppedEvent,
        ]
        assert stopped_events[-1].reason == "max_turns_reached"

    asyncio.run(scenario())


def test_internal_producer_emits_tool_execution_events() -> None:
    async def run_tool(tool, action):
        registry = ToolRegistry()
        registry.register(tool)
        model = ResponseSequenceModel(
            [
                ModelResponse(
                    response_id="tool-response",
                    output=[],
                    output_text=None,
                    tool_calls=[action],
                ),
                ModelResponse(
                    response_id="final-response",
                    output=[],
                    output_text="done",
                    tool_calls=[],
                ),
            ]
        )
        runtime = _create_run_runtime()
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)

        await _run_agent_loop_with_events(agent, "Use the tool.", None, runtime)
        return [
            event
            async for event in runtime.events
            if isinstance(
                event,
                (
                    ToolRequestedEvent,
                    ToolStartedEvent,
                    ToolCompletedEvent,
                    ToolFailedEvent,
                ),
            )
        ]

    async def scenario() -> None:
        @function_tool
        def echo_text(text: str) -> str:
            """Return the supplied text."""
            return text

        success_events = await run_tool(
            echo_text,
            ToolCall("echo_text", {"text": "hello"}, "call-success"),
        )
        assert [type(event) for event in success_events] == [
            ToolRequestedEvent,
            ToolStartedEvent,
            ToolCompletedEvent,
        ]
        assert {event.call_id for event in success_events} == {"call-success"}

        @function_tool
        def fail_tool() -> str:
            """Raise a tool error."""
            raise RuntimeError("boom")

        failure_events = await run_tool(
            fail_tool,
            ToolCall("fail_tool", {}, "call-failure"),
        )
        assert [type(event) for event in failure_events] == [
            ToolRequestedEvent,
            ToolStartedEvent,
            ToolFailedEvent,
        ]
        assert failure_events[-1].call_id == "call-failure"

        called = False

        @tool_input_guardrail
        def reject_input(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.reject_content("blocked")

        @function_tool(tool_input_guardrails=[reject_input])
        def guarded_tool() -> str:
            """Return only when the guardrail allows execution."""
            nonlocal called
            called = True
            return "unexpected"

        rejected_events = await run_tool(
            guarded_tool,
            ToolCall("guarded_tool", {}, "call-rejected"),
        )
        assert [type(event) for event in rejected_events] == [ToolRequestedEvent]
        assert called is False

    asyncio.run(scenario())


def test_internal_bridge_emits_approval_pause_and_resume_events() -> None:
    async def start_case(call_id):
        calls = []

        @function_tool(needs_approval=True)
        def delete_file(path: str) -> str:
            """Delete one file after approval."""
            calls.append(path)
            return f"deleted {path}"

        action = ToolCall("delete_file", {"path": "notes.txt"}, call_id)
        model = ResponseSequenceModel(
            [
                ModelResponse(
                    response_id=f"{call_id}-tool",
                    output=[],
                    output_text=None,
                    tool_calls=[action],
                ),
                ModelResponse(
                    response_id=f"{call_id}-final",
                    output=[],
                    output_text="done",
                    tool_calls=[],
                ),
            ]
        )
        registry = ToolRegistry()
        registry.register(delete_file)
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)
        runtime = _create_run_runtime()
        result = await _run_agent_loop_with_events(
            agent,
            "Delete notes.txt.",
            None,
            runtime,
        )
        events = [event async for event in runtime.events]
        return agent, result, events, calls

    async def resume_case(status):
        call_id = f"call-{status}"
        agent, first_result, first_events, calls = await start_case(call_id)

        required = [
            event for event in first_events if isinstance(event, ApprovalRequiredEvent)
        ]
        assert len(required) == 1
        assert required[0].call_id == call_id
        assert isinstance(first_events[-1], RunStoppedEvent)
        assert first_events[-1].resumable is True
        assert calls == []

        snapshot = first_result.to_state()
        snapshot["tool_approvals"][0]["status"] = status
        if status == "rejected":
            snapshot["tool_approvals"][0]["rejection_message"] = "not allowed"
        run_state = RunState.from_snapshot(snapshot, agent=agent)
        runtime = _create_run_runtime()
        resumed = await _run_agent_loop_with_events(
            agent,
            "Delete notes.txt.",
            None,
            runtime,
            run_state=run_state,
        )
        events = [event async for event in runtime.events]
        approval_and_tool_events = [
            event
            for event in events
            if isinstance(
                event,
                (
                    ApprovalResolvedEvent,
                    ToolStartedEvent,
                    ToolCompletedEvent,
                ),
            )
        ]
        return approval_and_tool_events, calls

    async def scenario() -> None:
        approved_events, approved_calls = await resume_case("approved")
        assert [type(event) for event in approved_events] == [
            ApprovalResolvedEvent,
            ToolStartedEvent,
            ToolCompletedEvent,
        ]
        assert approved_events[0].decision == "approved"
        assert approved_calls == ["notes.txt"]

        rejected_events, rejected_calls = await resume_case("rejected")
        assert [type(event) for event in rejected_events] == [ApprovalResolvedEvent]
        assert rejected_events[0].decision == "rejected"
        assert rejected_calls == []

    asyncio.run(scenario())


def test_cancellation_before_model_call_skips_model_and_emits_cancelled() -> None:
    async def scenario() -> None:
        called = False

        class Model:
            def get_response(self, messages, tool_specs):
                nonlocal called
                called = True
                return ModelResponse(output=[], output_text="late", tool_calls=[])

        runtime = _create_run_runtime()
        runtime.controller.cancel("user")
        agent = Agent(memory=AgentMemory(), model=Model())

        with pytest.raises(RunCancelledError):
            await _run_agent_loop_with_events(agent, "Stop now.", None, runtime)
        events = [event async for event in runtime.events]

        assert called is False
        assert [type(event) for event in events] == [
            RunStartedEvent,
            RunCancelledEvent,
        ]
        assert runtime.controller.terminal_event is events[-1]

    asyncio.run(scenario())


def test_cancellation_discards_late_model_response() -> None:
    async def scenario() -> None:
        entered = Event()
        release = Event()

        class BlockingModel:
            def get_response(self, messages, tool_specs):
                entered.set()
                release.wait()
                return ModelResponse(
                    response_id="late-response",
                    output=[],
                    output_text="late",
                    tool_calls=[],
                )

        runtime = _create_run_runtime()
        agent = Agent(memory=AgentMemory(), model=BlockingModel())
        producer = asyncio.create_task(
            _run_agent_loop_with_events(agent, "Wait.", None, runtime)
        )

        assert await asyncio.to_thread(entered.wait, 1)
        assert runtime.controller.cancel("user") is True
        release.set()
        with pytest.raises(RunCancelledError):
            await producer
        events = [event async for event in runtime.events]

        assert [type(event) for event in events] == [
            RunStartedEvent,
            TurnStartedEvent,
            ModelStartedEvent,
            ModelAttemptStartedEvent,
            RunCancelledEvent,
        ]
        assert not any(isinstance(event, ModelCompletedEvent) for event in events)
        assert not any(isinstance(event, ToolRequestedEvent) for event in events)

    asyncio.run(scenario())


def test_retry_event_arrives_before_backoff_and_cancel_stops_next_attempt() -> None:
    async def scenario() -> None:
        class RetryModel:
            model = "model-a"

            def __init__(self) -> None:
                self.calls = 0

            async def stream_response(self, request):
                self.calls += 1
                raise ModelResponseError(
                    "network unavailable",
                    kind=ModelErrorKind.TRANSPORT,
                )
                yield

        model = RetryModel()
        runtime = _create_run_runtime()
        agent = Agent(
            memory=AgentMemory(),
            model=model,
            model_settings=ModelSettings(
                max_attempts=2,
                backoff_initial=5,
                backoff_max=5,
                jitter_ratio=0,
                retry_budget=5,
                total_timeout=10,
            ),
        )
        producer = asyncio.create_task(
            _run_agent_loop_with_events(agent, "Retry.", None, runtime)
        )
        events = []
        async for event in runtime.events:
            events.append(event)
            if isinstance(event, ModelRetryScheduledEvent):
                assert model.calls == 1
                assert runtime.controller.cancel("user") is True

        with pytest.raises(RunCancelledError):
            await producer
        assert model.calls == 1
        assert [type(event) for event in events[-3:]] == [
            ModelAttemptStartedEvent,
            ModelRetryScheduledEvent,
            RunCancelledEvent,
        ]

    asyncio.run(scenario())


def test_model_fallback_event_precedes_backup_attempt() -> None:
    async def scenario() -> None:
        class FallbackModel:
            model = "model-a"

            def __init__(self) -> None:
                self.requests = []

            async def stream_response(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    raise ModelResponseError(
                        "rate limited",
                        kind=ModelErrorKind.RATE_LIMIT,
                        status_code=429,
                    )
                response_id = "response-backup"
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
                    delta="done",
                )
                yield TextDone(
                    attempt_id=request.attempt_id,
                    sequence=3,
                    output_index=0,
                    content_index=0,
                    text="done",
                )
                yield ResponseCompleted(
                    attempt_id=request.attempt_id,
                    sequence=4,
                    response_id=response_id,
                    output=[],
                    request_id=request.request_id,
                )

        model = FallbackModel()
        stream = Runner.run_stream(
            Agent(
                memory=AgentMemory(),
                model=model,
                model_settings=ModelSettings(
                    max_attempts=1,
                    fallback_model="model-b",
                ),
            ),
            "Fallback.",
        )
        events = [event async for event in stream]
        result = await stream._wait_result()
        model_events = [
            event
            for event in events
            if isinstance(
                event,
                (ModelAttemptStartedEvent, ModelFallbackEvent),
            )
        ]

        assert result.final_answer == "done"
        assert next(
            event for event in events if isinstance(event, ModelStartedEvent)
        ).model_name == "model-a"
        assert [type(event) for event in model_events] == [
            ModelAttemptStartedEvent,
            ModelFallbackEvent,
            ModelAttemptStartedEvent,
        ]
        assert [event.model_name for event in model_events[::2]] == [
            "model-a",
            "model-b",
        ]
        assert model_events[1].from_model == "model-a"
        assert model_events[1].to_model == "model-b"
        assert model_events[1].from_mode == "stream"
        assert model_events[1].to_mode == "stream"
        assert model_events[1].reason == "model_fallback"
        assert model_events[1].attempt_id == result.model_attempts[1].attempt_id
        assert sum(isinstance(event, ModelCompletedEvent) for event in events) == 1
        assert [attempt.model_name for attempt in result.model_attempts] == [
            "model-a",
            "model-b",
        ]
        assert result.last_model_terminal_reason == "completed"
        snapshot = result.to_state()
        assert len(snapshot["model_attempts"]) == 2
        assert snapshot["last_model_terminal_reason"] == "completed"

    asyncio.run(scenario())


def test_non_stream_fallback_event_matches_attempt_and_snapshot() -> None:
    async def scenario() -> None:
        class FallbackModel:
            model = "model-a"

            async def stream_response(self, request):
                raise ModelResponseError(
                    "streaming is unavailable",
                    kind=ModelErrorKind.STREAM_UNAVAILABLE,
                )
                yield

            async def get_response_for_request(self, request):
                return ModelResponse(
                    response_id="response-fallback",
                    output=[],
                    output_text="done",
                    tool_calls=[],
                )

        stream = Runner.run_stream(
            Agent(
                memory=AgentMemory(),
                model=FallbackModel(),
                model_settings=ModelSettings(
                    allow_non_stream_fallback=True,
                ),
            ),
            "Fallback.",
        )
        events = [event async for event in stream]
        result = await stream._wait_result()
        fallback = next(
            event for event in events if isinstance(event, ModelFallbackEvent)
        )

        assert fallback.reason == "non_stream_fallback"
        assert fallback.from_model == fallback.to_model == "model-a"
        assert fallback.from_mode == "stream"
        assert fallback.to_mode == "non_stream"
        assert fallback.attempt_id == result.model_attempts[1].attempt_id
        assert [attempt.reason for attempt in result.model_attempts] == [
            fallback.reason,
            fallback.reason,
        ]
        snapshot = result.to_state()
        assert snapshot["model_attempts"][1]["attempt_id"] == fallback.attempt_id
        assert snapshot["model_attempts"][1]["reason"] == fallback.reason

    asyncio.run(scenario())


@pytest.mark.parametrize("finish_first_arguments", [False, True])
def test_failed_attempt_tool_arguments_never_reach_the_handler(
    finish_first_arguments,
) -> None:
    async def scenario() -> None:
        calls = []

        @function_tool
        def counted_tool(text: str) -> str:
            """Record and return the final tool argument."""
            calls.append(text)
            return text

        class RetryToolModel:
            model = "model-a"

            def __init__(self) -> None:
                self.requests = []

            async def stream_response(self, request):
                self.requests.append(request)
                first_attempt = len(self.requests) == 1
                response_id = (
                    "response-stale" if first_attempt else "response-final"
                )
                item_id = "item-stale" if first_attempt else "item-final"
                call_id = "call-stale" if first_attempt else "call-final"
                arguments = (
                    '{"text":"stale"}' if first_attempt else '{"text":"done"}'
                )
                yield ResponseCreated(
                    attempt_id=request.attempt_id,
                    sequence=1,
                    response_id=response_id,
                )
                yield ToolCallStarted(
                    attempt_id=request.attempt_id,
                    sequence=2,
                    output_index=0,
                    item_id=item_id,
                    call_id=call_id,
                    tool_name="counted_tool",
                )
                yield ToolArgumentsDelta(
                    attempt_id=request.attempt_id,
                    sequence=3,
                    output_index=0,
                    item_id=item_id,
                    delta=arguments,
                )
                if first_attempt:
                    if finish_first_arguments:
                        yield ToolArgumentsDone(
                            attempt_id=request.attempt_id,
                            sequence=4,
                            output_index=0,
                            item_id=item_id,
                            arguments=arguments,
                        )
                    return
                yield ToolArgumentsDone(
                    attempt_id=request.attempt_id,
                    sequence=4,
                    output_index=0,
                    item_id=item_id,
                    arguments=arguments,
                )
                yield ResponseCompleted(
                    attempt_id=request.attempt_id,
                    sequence=5,
                    response_id=response_id,
                    output=[],
                    request_id=request.request_id,
                )

        registry = ToolRegistry()
        registry.register(counted_tool)
        model = RetryToolModel()
        stream = Runner.run_stream(
            Agent(
                memory=AgentMemory(),
                model=model,
                model_settings=ModelSettings(
                    max_attempts=2,
                    backoff_initial=0,
                    backoff_max=0,
                    jitter_ratio=0,
                    retry_budget=1,
                    total_timeout=10,
                ),
                tool_registry=registry,
                tool_use_behavior="stop_on_first_tool",
            ),
            "Use the tool.",
        )
        events = [event async for event in stream]
        result = await stream._wait_result()
        boundary_events = [
            event
            for event in events
            if isinstance(
                event,
                (
                    ModelAttemptStartedEvent,
                    ModelRetryScheduledEvent,
                    ModelCompletedEvent,
                    ToolRequestedEvent,
                    ToolStartedEvent,
                    ToolCompletedEvent,
                ),
            )
        ]

        assert result.final_answer == "done"
        assert calls == ["done"]
        assert len(model.requests) == 2
        assert len(result.model_attempts) == 2
        assert len(result.raw_responses) == 1
        assert len(result.to_state()["model_attempts"]) == 2
        assert [type(event) for event in boundary_events] == [
            ModelAttemptStartedEvent,
            ModelRetryScheduledEvent,
            ModelAttemptStartedEvent,
            ModelCompletedEvent,
            ToolRequestedEvent,
            ToolStartedEvent,
            ToolCompletedEvent,
        ]
        assert next(
            event
            for event in boundary_events
            if isinstance(event, ToolRequestedEvent)
        ).call_id == "call-final"

    asyncio.run(scenario())


def test_exhausted_attempts_never_execute_partial_tool_arguments() -> None:
    async def scenario() -> None:
        calls = []

        @function_tool
        def counted_tool(text: str) -> str:
            """Record a tool call if one reaches the handler."""
            calls.append(text)
            return text

        class FailingToolModel:
            model = "model-a"

            def __init__(self) -> None:
                self.calls = 0

            async def stream_response(self, request):
                self.calls += 1
                item_id = f"item-{self.calls}"
                yield ResponseCreated(
                    attempt_id=request.attempt_id,
                    sequence=1,
                    response_id=f"response-{self.calls}",
                )
                yield ToolCallStarted(
                    attempt_id=request.attempt_id,
                    sequence=2,
                    output_index=0,
                    item_id=item_id,
                    call_id=f"call-{self.calls}",
                    tool_name="counted_tool",
                )
                yield ToolArgumentsDelta(
                    attempt_id=request.attempt_id,
                    sequence=3,
                    output_index=0,
                    item_id=item_id,
                    delta='{"text":"partial',
                )

        registry = ToolRegistry()
        registry.register(counted_tool)
        model = FailingToolModel()
        stream = Runner.run_stream(
            Agent(
                memory=AgentMemory(),
                model=model,
                model_settings=ModelSettings(
                    max_attempts=2,
                    backoff_initial=0,
                    backoff_max=0,
                    jitter_ratio=0,
                    retry_budget=1,
                    total_timeout=10,
                ),
                tool_registry=registry,
            ),
            "Use the tool.",
        )
        events = [event async for event in stream]
        result = await stream._wait_result()

        assert result.new_items[-1].item_type == "model_error"
        assert calls == []
        assert model.calls == 2
        assert len(result.model_attempts) == 2
        assert all(
            attempt.outcome == "failed" for attempt in result.model_attempts
        )
        assert result.last_model_terminal_reason == "early_close"
        assert len(result.to_state()["model_attempts"]) == 2
        assert sum(isinstance(event, ModelAttemptStartedEvent) for event in events) == 2
        assert sum(isinstance(event, ModelRetryScheduledEvent) for event in events) == 1
        assert not any(
            isinstance(
                event,
                (ModelCompletedEvent, ToolRequestedEvent, ToolStartedEvent),
            )
            for event in events
        )

    asyncio.run(scenario())


def test_cancellation_discards_late_tool_result() -> None:
    async def scenario() -> None:
        entered = Event()
        release = Event()

        @function_tool
        def blocking_tool() -> str:
            """Wait until the test releases this tool."""
            entered.set()
            release.wait()
            return "late"

        registry = ToolRegistry()
        registry.register(blocking_tool)
        model = ResponseSequenceModel(
            [
                ModelResponse(
                    response_id="tool-response",
                    output=[],
                    output_text=None,
                    tool_calls=[ToolCall("blocking_tool", {}, "call-blocking")],
                ),
                ModelResponse(
                    response_id="unexpected-response",
                    output=[],
                    output_text="unexpected",
                    tool_calls=[],
                ),
            ]
        )
        runtime = _create_run_runtime()
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)
        producer = asyncio.create_task(
            _run_agent_loop_with_events(agent, "Use the tool.", None, runtime)
        )

        assert await asyncio.to_thread(entered.wait, 1)
        assert runtime.controller.cancel("user") is True
        release.set()
        with pytest.raises(RunCancelledError):
            await producer
        events = [event async for event in runtime.events]

        assert isinstance(events[-1], RunCancelledEvent)
        assert any(isinstance(event, ToolStartedEvent) for event in events)
        assert not any(
            isinstance(event, (ToolCompletedEvent, ToolFailedEvent))
            for event in events
        )

    asyncio.run(scenario())


def test_model_error_is_followed_by_one_failed_terminal() -> None:
    async def scenario() -> None:
        class FailingModel:
            def get_response(self, messages, tool_specs):
                raise ValueError("bad response")

        runtime = _create_run_runtime()
        agent = Agent(memory=AgentMemory(), model=FailingModel())

        result = await _run_agent_loop_with_events(agent, "Fail.", None, runtime)
        events = [event async for event in runtime.events]

        assert result.new_items[-1].item_type == "model_error"
        assert [type(event) for event in events[-2:]] == [
            RunErrorEvent,
            RunFailedEvent,
        ]
        assert sum(
            isinstance(event, (RunCompletedEvent, RunStoppedEvent, RunCancelledEvent, RunFailedEvent))
            for event in events
        ) == 1

    asyncio.run(scenario())


def test_unexpected_producer_error_emits_error_and_failed_then_reraises(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        def fail_loop(*args, **kwargs):
            raise LookupError("broken producer")

        monkeypatch.setattr("agents.run_loop.run_agent_loop", fail_loop)
        runtime = _create_run_runtime()
        agent = Agent(memory=AgentMemory(), model=FinalModel())

        with pytest.raises(LookupError, match="broken producer"):
            await _run_agent_loop_with_events(agent, "Fail.", None, runtime)
        events = [event async for event in runtime.events]

        assert [type(event) for event in events] == [
            RunStartedEvent,
            RunErrorEvent,
            RunFailedEvent,
        ]
        assert events[-1].error_type == "LookupError"

    asyncio.run(scenario())
