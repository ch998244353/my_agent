from datetime import datetime, timezone
from copy import deepcopy
import json

import pytest

from agents.contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from agents.event_compat import emit_run_item_event
from agents.events import (
    ApprovalRequiredEvent,
    ApprovalResolvedEvent,
    ModelCompletedEvent,
    ModelStartedEvent,
    RunAlreadyFinishedError,
    RunCompletedEvent,
    RunErrorEvent,
    RunEventReducer,
    RunStartedEvent,
    RunStoppedEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolRequestedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    event_to_dict,
)


TIMESTAMP = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)


def reducer(run_id: str = "run-1") -> RunEventReducer:
    return RunEventReducer(run_id, clock=lambda: TIMESTAMP)


def tool_metadata(
    *,
    success: bool,
    error_type: str | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "tool_execution": {
            "tool_name": "search",
            "call_id": "call-1",
            "success": success,
            "error_type": error_type,
            "reason": reason,
        }
    }


def test_maps_model_records_without_copying_full_response() -> None:
    event_reducer = reducer()
    response = ModelResponse(
        response_id="response-1",
        output=[{"large": "raw output"}],
        output_text="answer",
        tool_calls=[ToolCall("search", {"query": "weather"}, "call-1")],
    )

    completed = emit_run_item_event(
        event_reducer,
        RunItem("model_response", 2, response),
        turn_id="turn-1",
    )
    error = emit_run_item_event(
        event_reducer,
        RunItem("model_error", 3, "model unavailable"),
        turn_id="turn-1",
    )

    assert isinstance(completed, ModelCompletedEvent)
    assert completed.response_id == "response-1"
    assert completed.tool_call_count == 1
    assert completed.step_number == 2
    assert isinstance(error, RunErrorEvent)
    assert error.message == "model unavailable"
    assert (completed.sequence, error.sequence) == (1, 2)


def test_maps_tool_request_result_and_error_from_real_metadata() -> None:
    event_reducer = reducer()
    call = ToolCall("search", {"query": "weather"}, "call-1")

    requested = emit_run_item_event(
        event_reducer,
        RunItem("tool_call", 1, call),
    )
    completed = emit_run_item_event(
        event_reducer,
        RunItem("tool_result", 2, "sunny", tool_metadata(success=True)),
    )
    failed = emit_run_item_event(
        event_reducer,
        RunItem(
            "tool_error",
            3,
            "search failed",
            tool_metadata(success=False, error_type="TimeoutError"),
        ),
    )

    assert isinstance(requested, ToolRequestedEvent)
    assert not isinstance(requested, ToolCompletedEvent)
    assert isinstance(completed, ToolCompletedEvent)
    assert isinstance(failed, ToolFailedEvent)
    assert failed.error_type == "TimeoutError"
    assert [requested.sequence, completed.sequence, failed.sequence] == [1, 2, 3]


def test_maps_approval_request_and_rejected_result() -> None:
    event_reducer = reducer()
    request = ToolApprovalRequest(
        tool_name="search",
        call_id="call-1",
        arguments={"query": "weather"},
        reason="Needs network access.",
    )
    rejected_metadata = tool_metadata(
        success=False,
        error_type="tool_approval_rejected",
        reason="tool_approval_rejected",
    )
    rejected_metadata["approval_status"] = "rejected"

    required = emit_run_item_event(
        event_reducer,
        RunItem("tool_approval_required", 1, request),
    )
    rejected = emit_run_item_event(
        event_reducer,
        RunItem("tool_result", 2, "Denied", rejected_metadata),
    )

    assert isinstance(required, ApprovalRequiredEvent)
    assert required.reason == "Needs network access."
    assert isinstance(rejected, ApprovalResolvedEvent)
    assert rejected.decision == "rejected"


def test_guardrail_rejected_tool_result_does_not_claim_tool_completed() -> None:
    event_reducer = reducer()
    metadata = tool_metadata(
        success=False,
        error_type="input_guardrail_rejected",
        reason="input_guardrail_rejected",
    )

    event = emit_run_item_event(
        event_reducer,
        RunItem("tool_result", 1, "Rejected by guardrail", metadata),
    )

    assert event is None
    assert event_reducer.next_sequence == 1


def test_maps_final_and_stopped_items_to_distinct_terminals() -> None:
    final_reducer = reducer("run-final")
    stopped_reducer = reducer("run-stopped")

    final = emit_run_item_event(
        final_reducer,
        RunItem("final_output", 4, {"answer": "done"}),
    )
    stopped = emit_run_item_event(
        stopped_reducer,
        RunItem("run_stopped", 2, "tool_approval_required"),
    )

    assert isinstance(final, RunCompletedEvent)
    assert final.reached_final_answer is True
    assert isinstance(stopped, RunStoppedEvent)
    assert stopped.resumable is True
    assert final_reducer.terminal_event is final
    assert stopped_reducer.terminal_event is stopped


@pytest.mark.parametrize(
    "item_type",
    [
        "tool_input_guardrail",
        "tool_output_guardrail",
        "handoff",
        "input_guardrail",
        "output_guardrail",
        "verification_result",
        "verification_skipped",
    ],
)
def test_unmapped_items_return_none_without_consuming_sequence(item_type: str) -> None:
    event_reducer = reducer()

    event = emit_run_item_event(
        event_reducer,
        RunItem(item_type, 1, "payload"),
    )

    assert event is None
    assert event_reducer.next_sequence == 1


def test_rejects_invalid_known_payload_and_missing_tool_metadata() -> None:
    event_reducer = reducer()
    item = RunItem("tool_call", 1, "not a ToolCall")

    with pytest.raises(TypeError):
        emit_run_item_event(event_reducer, item)
    with pytest.raises(ValueError):
        emit_run_item_event(
            event_reducer,
            RunItem("tool_result", 1, "output"),
        )

    assert event_reducer.next_sequence == 1
    assert item.payload == "not a ToolCall"


def test_finished_reducer_error_is_preserved_through_compatibility_mapping() -> None:
    event_reducer = reducer()
    emit_run_item_event(event_reducer, RunItem("final_output", 1, "done"))

    with pytest.raises(RunAlreadyFinishedError):
        emit_run_item_event(
            event_reducer,
            RunItem("model_error", 2, "late error"),
        )


def test_step01_event_core_combines_direct_and_compatibility_events() -> None:
    event_reducer = reducer("run-combined")
    call = ToolCall("search", {"query": "weather"}, "call-1")
    response = ModelResponse(
        response_id="response-1",
        output=[],
        output_text=None,
        tool_calls=[call],
    )
    approval = ToolApprovalRequest(
        tool_name="shell",
        call_id="call-2",
        arguments={"command": "dir"},
        reason="Needs approval.",
    )
    items = [
        RunItem("model_response", 1, response),
        RunItem("tool_call", 2, call),
        RunItem("tool_result", 2, "sunny", tool_metadata(success=True)),
        RunItem("tool_approval_required", 3, approval),
        RunItem("run_stopped", 3, "tool_approval_required"),
    ]
    original_items = deepcopy(items)

    events = [
        event_reducer.emit(RunStartedEvent, agent_name="Assistant"),
        event_reducer.emit(
            TurnStartedEvent,
            turn_id="turn-1",
            turn_number=1,
        ),
        event_reducer.emit(
            ModelStartedEvent,
            turn_id="turn-1",
            model_name="gpt-example",
        ),
    ]
    event = emit_run_item_event(event_reducer, items[0], turn_id="turn-1")
    assert event is not None
    events.append(event)
    events.append(
        event_reducer.emit(
            TurnCompletedEvent,
            turn_id="turn-1",
            turn_number=1,
        )
    )
    event = emit_run_item_event(event_reducer, items[1], turn_id="turn-1")
    assert event is not None
    events.append(event)
    events.append(
        event_reducer.emit(
            ToolStartedEvent,
            turn_id="turn-1",
            step_number=2,
            tool_name="search",
            call_id="call-1",
        )
    )
    event = emit_run_item_event(event_reducer, items[2], turn_id="turn-1")
    assert event is not None
    events.append(event)
    events.append(
        event_reducer.emit(
            RunErrorEvent,
            turn_id="turn-1",
            step_number=2,
            error_type="RecoverableError",
            message="retrying",
        )
    )
    for item in items[3:]:
        event = emit_run_item_event(event_reducer, item, turn_id="turn-1")
        assert event is not None
        events.append(event)

    serialized = [event_to_dict(event) for event in events]
    assert [event.sequence for event in events] == list(range(1, 12))
    assert {event.run_id for event in events} == {"run-combined"}
    assert {payload["timestamp"] for payload in serialized} == {
        "2026-07-11T10:00:00Z"
    }
    assert json.loads(json.dumps(serialized)) == serialized
    assert event_reducer.terminal_event is events[-1]
    assert event_reducer.finished is True
    assert items == original_items

    with pytest.raises(RunAlreadyFinishedError):
        event_reducer.emit(TurnStartedEvent, turn_number=2)
    assert event_reducer.next_sequence == 12
