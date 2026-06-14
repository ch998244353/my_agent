from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents.contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from agents.result import RunResult
from agents.trajectory import (
    TrajectoryEvent,
    _event_from_run_item,
    _event_type_from_run_item,
    trajectory_events_from_result,
    write_trajectory_jsonl,
)


def _event(
    event_type: str,
    *,
    step: int | None = 1,
    payload: dict[str, object] | None = None,
    timestamp: str = "2026-06-14T08:00:00Z",
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_type=event_type,
        run_id="run_lesson_1",
        step=step,
        payload=payload or {"message": "ok"},
        timestamp=timestamp,
    )


def test_trajectory_event_serializes_to_dict() -> None:
    event = _event("run_started", step=None, payload={"task": "修复代码"})

    assert event.to_dict() == {
        "event_type": "run_started",
        "run_id": "run_lesson_1",
        "step": None,
        "payload": {"task": "修复代码"},
        "timestamp": "2026-06-14T08:00:00Z",
    }


def test_write_trajectory_jsonl_creates_parent_and_writes_json_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "trajectory.jsonl"
    events = (
        _event("run_started", step=None, payload={"task": "修复代码"}),
        _event("final_output", step=2, payload={"answer": "完成"}),
    )

    write_trajectory_jsonl(path, events)

    raw_text = path.read_text(encoding="utf-8")
    assert "修复代码" in raw_text
    assert "\\u4fee" not in raw_text
    lines = raw_text.splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == [
        "run_started",
        "final_output",
    ]


def test_write_trajectory_jsonl_overwrites_by_default_and_appends_when_requested(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trajectory.jsonl"

    write_trajectory_jsonl(path, [_event("run_started")])
    write_trajectory_jsonl(path, [_event("final_output")])
    write_trajectory_jsonl(path, [_event("verification_result")], append=True)

    assert [json.loads(line)["event_type"] for line in path.read_text().splitlines()] == [
        "final_output",
        "verification_result",
    ]


def test_write_trajectory_jsonl_rejects_unnormalized_payload(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.jsonl"
    event = _event("tool_result", payload={"raw": object()})

    with pytest.raises(TypeError):
        write_trajectory_jsonl(path, [event])


@dataclass(frozen=True)
class NestedPayload:
    path: Path
    error: Exception


@pytest.mark.parametrize(
    ("item_type", "expected_event_type"),
    [
        ("model_response", "model_response"),
        ("model_error", "model_error"),
        ("tool_call", "tool_call"),
        ("tool_result", "tool_result"),
        ("tool_error", "tool_error"),
        ("tool_approval_required", "approval_required"),
        ("verification_result", "verification_result"),
        ("verification_skipped", "verification_skipped"),
        ("run_stopped", "run_stopped"),
        ("final_output", "final_output"),
    ],
)
def test_event_type_from_run_item_maps_supported_items(
    item_type: str,
    expected_event_type: str,
) -> None:
    item = RunItem(item_type=item_type, step_number=3, payload="payload")

    assert _event_type_from_run_item(item) == expected_event_type


def test_event_type_from_run_item_maps_rejected_tool_result_to_approval_rejected() -> None:
    item = RunItem(
        item_type="tool_result",
        step_number=4,
        payload="Denied",
        metadata={
            "approval_status": "rejected",
            "tool_execution": {"reason": "tool_approval_rejected"},
        },
    )

    assert _event_type_from_run_item(item) == "approval_rejected"


def test_event_type_from_run_item_maps_unknown_item_to_runtime_item() -> None:
    item = RunItem(item_type="tool_input_guardrail", step_number=5, payload="guarded")

    assert _event_type_from_run_item(item) == "runtime_item"


def test_event_from_run_item_preserves_step_metadata_and_normalized_payload() -> None:
    item = RunItem(
        item_type="model_response",
        step_number=7,
        payload=ModelResponse(
            response_id="resp_1",
            output=[],
            output_text="hello",
            tool_calls=[ToolCall("shell", {"cmd": "pytest"}, "call_1")],
            raw=NestedPayload(Path("repo/file.py"), RuntimeError("boom")),
        ),
        metadata={"workspace": Path("repo"), "tags": {"lesson", "trajectory"}},
    )

    event = _event_from_run_item(
        item,
        run_id="run_2",
        timestamp="2026-06-14T09:00:00Z",
    )

    assert event.event_type == "model_response"
    assert event.run_id == "run_2"
    assert event.step == 7
    assert event.timestamp == "2026-06-14T09:00:00Z"
    assert event.payload["item_type"] == "model_response"
    assert event.payload["payload"]["response_id"] == "resp_1"
    assert event.payload["payload"]["tool_calls"][0]["tool_name"] == "shell"
    assert event.payload["payload"]["raw"]["path"] == "repo/file.py"
    assert event.payload["payload"]["raw"]["error"] == {
        "type": "RuntimeError",
        "message": "boom",
    }
    assert sorted(event.payload["metadata"]["tags"]) == ["lesson", "trajectory"]
    json.dumps(event.to_dict(), ensure_ascii=False)


def test_event_from_run_item_preserves_original_type_for_runtime_items() -> None:
    item = RunItem(
        item_type="handoff",
        step_number=8,
        payload={"target": "reviewer"},
    )

    event = _event_from_run_item(
        item,
        run_id="run_2",
        timestamp="2026-06-14T09:01:00Z",
    )

    assert event.event_type == "runtime_item"
    assert event.payload["original_item_type"] == "handoff"


def test_trajectory_events_from_result_wraps_items_with_start_and_final_summary() -> None:
    model_response = ModelResponse(
        response_id="resp_2",
        output=[],
        output_text="done",
        tool_calls=[],
    )
    result = RunResult(
        final_answer="done",
        step_results=["tool output"],
        reached_final_answer=True,
        steps_taken=2,
        input="Fix the bug",
        current_turn=2,
        max_turns=5,
        max_steps=8,
        raw_responses=(model_response,),
        new_items=(
            RunItem(
                item_type="model_response",
                step_number=1,
                payload=model_response,
            ),
            RunItem(
                item_type="tool_call",
                step_number=1,
                payload=ToolCall("shell", {"cmd": "pytest"}, "call_1"),
            ),
            RunItem(
                item_type="verification_result",
                step_number=2,
                payload="pytest passed",
                metadata={"passed": True, "observation": "1 passed"},
            ),
            RunItem(item_type="final_output", step_number=2, payload="done"),
        ),
    )

    events = trajectory_events_from_result(
        result,
        run_id="run_3",
        task="Fix the bug",
        workspace_root="repo",
        metadata={"profile": "coding"},
    )

    assert [event.event_type for event in events] == [
        "run_started",
        "model_response",
        "tool_call",
        "verification_result",
        "final_output",
    ]
    assert events[0].payload == {
        "task": "Fix the bug",
        "workspace_root": "repo",
        "metadata": {"profile": "coding"},
    }
    final_payload = events[-1].payload
    assert final_payload["final_answer"] == "done"
    assert final_payload["summary"] == {
        "final_answer_present": True,
        "pending_approvals_count": 0,
        "model_turns": 1,
        "tool_steps": 1,
        "verification_summary": {
            "attempts": 1,
            "passed": True,
            "skipped": 0,
            "last_observation": "1 passed",
        },
        "last_response_id": "resp_2",
        "current_turn": 2,
        "steps_taken": 2,
        "has_pending_approvals": False,
    }


def test_trajectory_events_from_result_ends_with_run_stopped_when_stop_evidence_exists() -> None:
    result = RunResult(
        final_answer=None,
        step_results=[],
        reached_final_answer=False,
        steps_taken=1,
        current_turn=1,
        max_turns=5,
        new_items=(
            RunItem(
                item_type="tool_approval_required",
                step_number=1,
                payload=ToolApprovalRequest(
                    tool_name="shell",
                    call_id="call_2",
                    arguments={"cmd": "rm -rf build"},
                    reason="approval required",
                ),
            ),
            RunItem(
                item_type="run_stopped",
                step_number=1,
                payload="tool_approval_required",
                metadata={"reason": "approval required"},
            ),
        ),
    )

    events = trajectory_events_from_result(
        result,
        run_id="run_approval",
        task="Clean build",
    )

    assert [event.event_type for event in events] == [
        "run_started",
        "approval_required",
        "run_stopped",
    ]
    assert events[-1].payload["stop_reason"] == "tool_approval_required"
    assert events[-1].payload["summary"]["has_pending_approvals"] is True
    assert events[-1].payload["summary"]["pending_approvals_count"] == 1
