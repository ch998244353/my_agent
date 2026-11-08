from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import RunItem
from .result import RunResult
from .tool_runtime import clip_tool_text


_FINAL_VERIFICATION_OBSERVATION_CHARS = 1200


_RUN_ITEM_EVENT_TYPES: dict[str, str] = {
    "model_response": "model_response",
    "model_error": "model_error",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "tool_error": "tool_error",
    "tool_approval_required": "approval_required",
    "verification_result": "verification_result",
    "verification_skipped": "verification_skipped",
    "run_stopped": "run_stopped",
    "final_output": "final_output",
}

_TERMINAL_ITEM_TYPES = {"final_output", "run_stopped"}

# 一条运行轨迹事件
@dataclass(frozen=True)
class TrajectoryEvent:
    event_type: str
    run_id: str
    step: int | None
    payload: Mapping[str, object]
    timestamp: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "step": self.step,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


# 生成 trajectory 事件时间戳。它没有参数，返回 UTC ISO 字符串。它参与 _event_from_run_item() 调用：当调用方没有传入固定 timestamp 时，自动给事件补当前时间
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# 接收一个 RunItem，返回布尔值。它处理的业务是：用户拒绝工具审批时，旧运行时不会生成新类型，而是写成 tool_result
def _is_rejected_approval_result(item: RunItem) -> bool:
    if item.item_type != "tool_result":
        return False
    tool_execution = item.metadata.get("tool_execution")
    tool_reason = (
        tool_execution.get("reason")
        if isinstance(tool_execution, Mapping)
        else None
    )
    return (
        item.metadata.get("approval_status") == "rejected"
        or tool_reason == "tool_approval_rejected"
    )


# 接收 RunItem，返回 trajectory 事件类型字符串
def _event_type_from_run_item(item: RunItem) -> str:
    if _is_rejected_approval_result(item):
        return "approval_rejected"
    return _RUN_ITEM_EVENT_TYPES.get(item.item_type, "runtime_item")


# 接收任意 payload，返回 JSON-safe 对象。它处理的业务是把 ModelResponse、ToolCall、路径、异常、集合、嵌套字典等运行时对象转成可写 JSONL 的数据。它被 _event_from_run_item() 调用
def _normalize_trajectory_payload(value: Any) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    if is_dataclass(value):
        return _normalize_trajectory_payload(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_trajectory_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize_trajectory_payload(item) for item in value]
    if isinstance(value, set | frozenset):
        return [_normalize_trajectory_payload(item) for item in value]
    return str(value)


def _verification_summary_payload(summary: object | None) -> object:
    if summary is None:
        return None
    last_observation = summary.last_observation
    return {
        "attempts": summary.attempts,
        "passed": summary.passed,
        "skipped": summary.skipped,
        "last_observation": None
        if last_observation is None
        else clip_tool_text(
            last_observation,
            _FINAL_VERIFICATION_OBSERVATION_CHARS,
        ),
    }


# 接收一个 RunItem、运行 ID 和可选时间戳，返回 TrajectoryEvent
def _event_from_run_item(
    item: RunItem,
    *,
    run_id: str,
    timestamp: str | None = None,
) -> TrajectoryEvent:
    event_type = _event_type_from_run_item(item)
    payload: dict[str, object] = {
        "item_type": item.item_type,
        "payload": _normalize_trajectory_payload(item.payload),
        "metadata": _normalize_trajectory_payload(item.metadata),
    }
    if event_type == "runtime_item":
        payload["original_item_type"] = item.item_type

    return TrajectoryEvent(
        event_type=event_type,
        run_id=run_id,
        step=item.step_number,
        payload=payload,
        timestamp=timestamp or _utc_now(),
    )


# 接收 RunResult，返回摘要字典。它处理的是整次运行的业务概览：有没有最终答案、是否还等审批、模型调用了几轮、工具执行了几步、校验是否通过，以及最后 response id
def _result_summary_payload(result: RunResult) -> dict[str, object]:
    summary: dict[str, object] = {
        "final_answer_present": result.final_answer is not None
        or result.reached_final_answer,
        "pending_approvals_count": len(result.pending_approvals),
        "model_turns": len(result.raw_responses),
        "tool_steps": len(result.step_results),
        "verification_summary": _verification_summary_payload(
            result.verification_summary
        ),
        "last_response_id": result.last_response_id,
        "current_turn": result.current_turn,
        "steps_taken": result.steps_taken,
        "has_pending_approvals": result.has_pending_approvals,
    }
    return summary


def state_saved_event(
    run_id: str,
    state_path: Path | str,
    pending_count: int,
    *,
    timestamp: str | None = None,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_type="state_saved",
        run_id=run_id,
        step=None,
        payload={
            "state_path": _normalize_trajectory_payload(state_path),
            "pending_count": pending_count,
        },
        timestamp=timestamp or _utc_now(),
    )


def resume_started_event(
    run_id: str,
    state_path: Path | str,
    approvals: int,
    rejections: int,
    *,
    timestamp: str | None = None,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_type="resume_started",
        run_id=run_id,
        step=None,
        payload={
            "state_path": _normalize_trajectory_payload(state_path),
            "approvals": approvals,
            "rejections": rejections,
        },
        timestamp=timestamp or _utc_now(),
    )


def approval_decision_event(
    run_id: str,
    tool_name: str,
    call_id: str,
    decision: str,
    reason: str | None,
    *,
    timestamp: str | None = None,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_type="approval_decision",
        run_id=run_id,
        step=None,
        payload={
            "tool_name": tool_name,
            "call_id": call_id,
            "decision": decision,
            "reason": reason,
        },
        timestamp=timestamp or _utc_now(),
    )


# 生成轨迹第一条事件。它接收运行 ID、任务、workspace、metadata 和时间戳，返回 TrajectoryEvent。业务上它说明“这次 coding agent 是为哪个任务、在哪个目录启动的”
def _run_started_event(
    *,
    run_id: str,
    task: str,
    workspace_root: str | None,
    metadata: Mapping[str, object] | None,
    timestamp: str,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        event_type="run_started",
        run_id=run_id,
        step=None,
        payload={
            "task": task,
            "workspace_root": workspace_root,
            "metadata": _normalize_trajectory_payload(metadata or {}),
        },
        timestamp=timestamp,
    )


# 返回最后一个匹配的 RunItem 或 None
def _last_run_item(result: RunResult, item_type: str) -> RunItem | None:
    for item in reversed(result.new_items):
        if item.item_type == item_type:
            return item
    return None


# 接收 RunResult、运行 ID 和时间戳，返回最终答案事件。它把最终回答和运行摘要合并到最后一条 trajectory 里，方便后续看文件末尾就知道这次任务是否产出答案
def _final_output_event(
    result: RunResult,
    *,
    run_id: str,
    timestamp: str,
) -> TrajectoryEvent:
    final_item = _last_run_item(result, "final_output")
    return TrajectoryEvent(
        event_type="final_output",
        run_id=run_id,
        step=final_item.step_number if final_item is not None else result.steps_taken,
        payload={
            "final_answer": _normalize_trajectory_payload(result.final_answer),
            "summary": _result_summary_payload(result),
        },
        timestamp=timestamp,
    )


# 接收 RunResult、运行 ID 和时间戳，返回停止事件或 None。它处理的业务是 agent 没有最终答案但有暂停证据，例如等待工具审批；没有停止证据时不伪造事件
def _run_stopped_event(
    result: RunResult,
    *,
    run_id: str,
    timestamp: str,
) -> TrajectoryEvent | None:
    stopped_item = _last_run_item(result, "run_stopped")
    if stopped_item is None:
        return None
    return TrajectoryEvent(
        event_type="run_stopped",
        run_id=run_id,
        step=stopped_item.step_number,
        payload={
            "stop_reason": _normalize_trajectory_payload(stopped_item.payload),
            "metadata": _normalize_trajectory_payload(stopped_item.metadata),
            "summary": _result_summary_payload(result),
        },
        timestamp=timestamp,
    )


# 把一次完整运行转成轨迹：先写开始事件，再写中间运行事件，最后根据结果选择最终答案或暂停事件
def trajectory_events_from_result(
    result: RunResult,
    *,
    run_id: str,
    task: str,
    workspace_root: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> tuple[TrajectoryEvent, ...]:
    timestamp = _utc_now()
    events: list[TrajectoryEvent] = [
        _run_started_event(
            run_id=run_id,
            task=task,
            workspace_root=workspace_root,
            metadata=metadata,
            timestamp=timestamp,
        )
    ]
    events.extend(
        _event_from_run_item(item, run_id=run_id, timestamp=timestamp)
        for item in result.new_items
        if item.item_type not in _TERMINAL_ITEM_TYPES
    )

    if result.final_answer is not None or result.reached_final_answer:
        events.append(
            _final_output_event(result, run_id=run_id, timestamp=timestamp)
        )
    else:
        stopped_event = _run_stopped_event(
            result,
            run_id=run_id,
            timestamp=timestamp,
        )
        if stopped_event is not None:
            events.append(stopped_event)

    return tuple(events)


# 把所有事件序列化成 JSON 字符串，再创建父目录并写入文件
def write_trajectory_jsonl(
    path: Path,
    events: Iterable[TrajectoryEvent],
    *,
    append: bool = False,
) -> None:
    lines = [json.dumps(event.to_dict(), ensure_ascii=False) for event in events]

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as file:
        for line in lines:
            file.write(line)
            file.write("\n")
