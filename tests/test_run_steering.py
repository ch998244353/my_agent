from __future__ import annotations

import asyncio
from threading import Event

import pytest

from agents import Agent, AgentMemory, RunConfig, ToolCall, ToolRegistry, function_tool
from agents.contracts import ModelResponse
from agents.events import (
    RunAlreadyFinishedError,
    RunCancelledError,
    SteerAcceptedEvent,
    SteerAppliedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
)
from agents.runner import Runner


def test_steers_are_applied_fifo_in_the_next_available_batch() -> None:
    first_tool_entered = Event()
    release_first_tool = Event()
    second_model_entered = Event()
    release_second_model = Event()
    tool_calls = 0

    @function_tool
    def echo_text(text: str) -> str:
        """Return text, pausing the first call for the test."""
        nonlocal tool_calls
        tool_calls += 1
        if tool_calls == 1:
            first_tool_entered.set()
            if not release_first_tool.wait(2):
                raise TimeoutError("first tool was not released")
        return text

    class RecordingModel:
        def __init__(self) -> None:
            self.calls = 0
            self.message_batches = []

        def get_response(self, messages, tool_specs):
            self.calls += 1
            self.message_batches.append(list(messages))
            if self.calls == 1:
                return ModelResponse(
                    response_id="response-tool-1",
                    output=[],
                    output_text=None,
                    tool_calls=[ToolCall("echo_text", {"text": "one"}, "call-1")],
                )
            if self.calls == 2:
                second_model_entered.set()
                if not release_second_model.wait(2):
                    raise TimeoutError("second model call was not released")
                return ModelResponse(
                    response_id="response-tool-2",
                    output=[],
                    output_text=None,
                    tool_calls=[ToolCall("echo_text", {"text": "two"}, "call-2")],
                )
            return ModelResponse(
                response_id="response-final",
                output=[],
                output_text="done",
                tool_calls=[],
            )

        def record_tool_output(self, tool_call, output) -> None:
            pass

    registry = ToolRegistry()
    registry.register(echo_text)
    model = RecordingModel()
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=model, tool_registry=registry),
        "Use the tool twice.",
    )

    async def scenario():
        async def collect():
            events = [event async for event in stream]
            return events, await stream._wait_result()

        collector = asyncio.create_task(collect())
        try:
            assert await asyncio.to_thread(first_tool_entered.wait, 1)
            first_id = stream.controller.steer("first change")
            second_id = stream.controller.steer("second change")
            release_first_tool.set()

            assert await asyncio.to_thread(second_model_entered.wait, 1)
            third_id = stream.controller.steer("third change")
            release_second_model.set()
            events, result = await collector
            return events, result, (first_id, second_id, third_id)
        finally:
            release_first_tool.set()
            release_second_model.set()

    events, result, steer_ids = asyncio.run(scenario())
    accepted = [event for event in events if isinstance(event, SteerAcceptedEvent)]
    applied = [event for event in events if isinstance(event, SteerAppliedEvent)]

    expected_ids = [steer.steer_id for steer in steer_ids]
    assert [event.steer_id for event in accepted] == expected_ids
    assert [event.steer_id for event in applied] == expected_ids
    assert [
        message.content
        for message in model.message_batches[1]
        if message.role == "user"
    ][-2:] == ["first change", "second change"]
    assert "third change" not in [
        message.content for message in model.message_batches[1]
    ]
    assert [
        message.content
        for message in model.message_batches[2]
        if message.role == "user"
    ][-3:] == ["first change", "second change", "third change"]
    assert result.steps_taken == 2

    completed = [i for i, event in enumerate(events) if isinstance(event, TurnCompletedEvent)]
    started = [i for i, event in enumerate(events) if isinstance(event, TurnStartedEvent)]
    first_accepted = events.index(accepted[0])
    first_applied = events.index(applied[0])
    third_accepted = events.index(accepted[2])
    third_applied = events.index(applied[2])
    assert completed[0] < first_accepted < first_applied < started[1]
    assert completed[1] < third_accepted < third_applied < started[2]


def test_turn_limit_discards_steer_before_it_can_be_applied() -> None:
    tool_entered = Event()
    release_tool = Event()

    @function_tool
    def wait_tool() -> str:
        """Wait until the test releases this tool."""
        tool_entered.set()
        if not release_tool.wait(2):
            raise TimeoutError("tool was not released")
        return "done"

    class OneToolModel:
        def get_response(self, messages, tool_specs):
            return ModelResponse(
                response_id="response-tool",
                output=[],
                output_text=None,
                tool_calls=[ToolCall("wait_tool", {}, "call-1")],
            )

        def record_tool_output(self, tool_call, output) -> None:
            pass

    registry = ToolRegistry()
    registry.register(wait_tool)
    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=OneToolModel(), tool_registry=registry),
        "Wait once.",
        RunConfig(max_turns=1),
    )

    async def scenario():
        event_task = asyncio.create_task(_collect(stream))
        try:
            assert await asyncio.to_thread(tool_entered.wait, 1)
            stream.controller.steer("too late")
            release_tool.set()
            return await event_task
        finally:
            release_tool.set()

    events = asyncio.run(scenario())

    assert not any(isinstance(event, (SteerAcceptedEvent, SteerAppliedEvent)) for event in events)
    assert stream.controller.pending_steer_count == 0
    with pytest.raises(RunAlreadyFinishedError):
        stream.controller.steer("after finish")


def test_cancellation_wins_over_a_pending_steer() -> None:
    model_entered = Event()
    release_model = Event()

    class BlockingModel:
        def get_response(self, messages, tool_specs):
            model_entered.set()
            if not release_model.wait(2):
                raise TimeoutError("model was not released")
            return ModelResponse(
                response_id="late-response",
                output=[],
                output_text="late",
                tool_calls=[],
            )

    stream = Runner.run_stream(
        Agent(memory=AgentMemory(), model=BlockingModel()),
        "Wait.",
    )
    events = []

    async def scenario():
        event_task = asyncio.create_task(_collect(stream, events))
        try:
            assert await asyncio.to_thread(model_entered.wait, 1)
            stream.controller.steer("do not apply")
            assert stream.controller.cancel("user stopped") is True
            release_model.set()
            await event_task
        finally:
            release_model.set()

    with pytest.raises(RunCancelledError, match="user stopped"):
        asyncio.run(scenario())

    assert not any(isinstance(event, (SteerAcceptedEvent, SteerAppliedEvent)) for event in events)
    assert stream.controller.pending_steer_count == 0


async def _collect(stream, target=None):
    events = [event async for event in stream]
    if target is not None:
        target.extend(events)
    await stream._wait_result()
    return events
