from __future__ import annotations

from collections.abc import Mapping

from .contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from .events import (
    ApprovalRequiredEvent,
    ApprovalResolvedEvent,
    ModelCompletedEvent,
    RunCompletedEvent,
    RunErrorEvent,
    RunEvent,
    RunEventReducer,
    RunStoppedEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolRequestedEvent,
)


def _tool_execution_fields(
    item: RunItem,
) -> tuple[str, str, Mapping[str, object]]:
    tool_execution = item.metadata.get("tool_execution")
    if not isinstance(tool_execution, Mapping):
        raise ValueError(
            f"{item.item_type} metadata must contain tool_execution."
        )
    tool_name = tool_execution.get("tool_name")
    call_id = tool_execution.get("call_id")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("tool_execution.tool_name must be a non-empty string.")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("tool_execution.call_id must be a non-empty string.")
    return tool_name, call_id, tool_execution


# 接收 reducer、旧 RunItem 和可选 turn_id，返回已经编号的事件；没有准确映射时返回 None
def emit_run_item_event(
    reducer: RunEventReducer,
    item: RunItem,
    *,
    turn_id: str | None = None,
) -> RunEvent | None:
    if not isinstance(reducer, RunEventReducer):
        raise TypeError("reducer must be a RunEventReducer.")
    if not isinstance(item, RunItem):
        raise TypeError("item must be a RunItem.")

    if item.item_type == "model_response":
        if not isinstance(item.payload, ModelResponse):
            raise TypeError("model_response payload must be a ModelResponse.")
        return reducer.emit(
            ModelCompletedEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            response_id=item.payload.response_id,
            tool_call_count=len(item.payload.tool_calls),
        )

    if item.item_type == "model_error":
        if not isinstance(item.payload, str):
            raise TypeError("model_error payload must be a string.")
        return reducer.emit(
            RunErrorEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            error_type="ModelError",
            message=item.payload,
        )

    if item.item_type == "tool_call":
        if not isinstance(item.payload, ToolCall):
            raise TypeError("tool_call payload must be a ToolCall.")
        return reducer.emit(
            ToolRequestedEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            tool_name=item.payload.tool_name,
            call_id=item.payload.call_id,
        )

    if item.item_type in ("tool_result", "tool_error"):
        tool_name, call_id, tool_execution = _tool_execution_fields(item)
        if item.item_type == "tool_result":
            rejected = (
                item.metadata.get("approval_status") == "rejected"
                or tool_execution.get("reason") == "tool_approval_rejected"
            )
            if rejected:
                return reducer.emit(
                    ApprovalResolvedEvent,
                    turn_id=turn_id,
                    step_number=item.step_number,
                    tool_name=tool_name,
                    call_id=call_id,
                    decision="rejected",
                )
            if tool_execution.get("success") is False:
                return None
            return reducer.emit(
                ToolCompletedEvent,
                turn_id=turn_id,
                step_number=item.step_number,
                tool_name=tool_name,
                call_id=call_id,
            )

        if not isinstance(item.payload, str):
            raise TypeError("tool_error payload must be a string.")
        error_type = tool_execution.get("error_type")
        if not isinstance(error_type, str) or not error_type.strip():
            raise ValueError(
                "tool_execution.error_type must be a non-empty string."
            )
        return reducer.emit(
            ToolFailedEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            tool_name=tool_name,
            call_id=call_id,
            error_type=error_type,
            message=item.payload,
        )

    if item.item_type == "tool_approval_required":
        if not isinstance(item.payload, ToolApprovalRequest):
            raise TypeError(
                "tool_approval_required payload must be a ToolApprovalRequest."
            )
        return reducer.emit(
            ApprovalRequiredEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            tool_name=item.payload.tool_name,
            call_id=item.payload.call_id,
            reason=item.payload.reason,
        )

    if item.item_type == "final_output":
        return reducer.emit(
            RunCompletedEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            reached_final_answer=True,
        )

    if item.item_type == "run_stopped":
        if not isinstance(item.payload, str):
            raise TypeError("run_stopped payload must be a string.")
        return reducer.emit(
            RunStoppedEvent,
            turn_id=turn_id,
            step_number=item.step_number,
            reason=item.payload,
            resumable=item.payload == "tool_approval_required",
        )

    return None
