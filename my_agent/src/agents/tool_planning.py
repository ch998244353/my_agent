from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .contracts import (
    TOOL_APPROVAL_REQUIRED_METADATA_KEY,
    RunItem,
    ToolApprovalRequest,
    ToolCall,
)
from .run_context import ToolApprovalStatus
from .tool_runtime import ToolApprovalDecision
from .tools import ToolNotFoundError

if TYPE_CHECKING:
    from .agent import Agent
    from .run_state import RunState
    from .run_steps import ProcessedResponse


@dataclass(frozen=True)
class ToolExecutionBatch:
    approved_tool_calls: tuple[ToolCall, ...]
    handoff_calls: tuple[ToolCall, ...]
    pending_approval_calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class ToolPlanningApprovalDecision:
    action: ToolCall
    status: ToolApprovalStatus
    approval_decision: ToolApprovalDecision | None = None
    tool_found: bool = True

    @property
    def requires_approval(self) -> bool:
        if self.status == "pending":
            return True
        if self.status in ("approved", "rejected"):
            return False
        return bool(self.approval_decision)


@dataclass(frozen=True)
class ToolExecutionPlan:
    """Tool execution plan for one model turn, including actions that must pause."""

    actions: tuple[ToolCall, ...]
    tool_calls: tuple[ToolCall, ...]
    handoff_calls: tuple[ToolCall, ...]
    pending_approval_calls: tuple[ToolCall, ...] = ()

    @property
    def has_actions(self) -> bool:
        return bool(self.actions)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def has_handoff_calls(self) -> bool:
        return bool(self.handoff_calls)

    @property
    def has_pending_approval(self) -> bool:
        return bool(self.pending_approval_calls)

    @property
    def should_pause(self) -> bool:
        return self.has_pending_approval

    @property
    def approved_tool_calls(self) -> tuple[ToolCall, ...]:
        pending_call_keys = {
            (action.tool_name, action.call_id)
            for action in self.pending_approval_calls
        }
        return tuple(
            action
            for action in self.tool_calls
            if (action.tool_name, action.call_id) not in pending_call_keys
        )

    @property
    def execution_batch(self) -> ToolExecutionBatch:
        return ToolExecutionBatch(
            approved_tool_calls=self.approved_tool_calls,
            handoff_calls=self.handoff_calls,
            pending_approval_calls=self.pending_approval_calls,
        )


def _planning_approval_decision_for(
    agent: Agent,
    run_state: RunState,
    action: ToolCall,
) -> ToolPlanningApprovalDecision:
    status = run_state.context_wrapper.approval_status_for(
        action.tool_name,
        action.call_id,
    )
    if status in ("pending", "approved", "rejected"):
        return ToolPlanningApprovalDecision(action=action, status=status)

    try:
        tool = agent.tool_registry.get(action.tool_name)
    except ToolNotFoundError:
        return ToolPlanningApprovalDecision(
            action=action,
            status=status,
            approval_decision=ToolApprovalDecision(False, call_id=action.call_id),
            tool_found=False,
        )

    return ToolPlanningApprovalDecision(
        action=action,
        status=status,
        approval_decision=tool.requires_approval_for(
            run_state.context_wrapper,
            agent,
            action,
        ),
    )


def _approval_request_reason(approval_decision: ToolApprovalDecision) -> str:
    if approval_decision.error_message is None:
        return "User approval is required before running this tool."
    return (
        "Tool approval check failed before execution: "
        f"{approval_decision.error_type}: {approval_decision.error_message}"
    )


def _record_planned_approval_required(
    run_state: RunState,
    action: ToolCall,
    approval_decision: ToolApprovalDecision | None,
) -> None:
    run_state.context_wrapper.request_tool_call_approval(
        action.tool_name,
        action.call_id,
    )
    if action not in run_state.pending_tool_calls:
        run_state.pending_tool_calls = (*run_state.pending_tool_calls, action)
    run_state.new_items.append(
        RunItem(
            item_type="tool_approval_required",
            step_number=run_state.next_step_number(),
            payload=ToolApprovalRequest(
                tool_name=action.tool_name,
                call_id=action.call_id,
                arguments=dict(action.arguments),
                reason=(
                    _approval_request_reason(approval_decision)
                    if approval_decision is not None
                    else "User approval is required before running this tool."
                ),
            ),
            metadata={
                TOOL_APPROVAL_REQUIRED_METADATA_KEY: True,
                "approval_error_type": (
                    approval_decision.error_type
                    if approval_decision is not None
                    else None
                ),
            },
        )
    )


def build_tool_execution_plan(
    agent: Agent,
    processed_response: ProcessedResponse,
    run_state: RunState,
) -> ToolExecutionPlan:
    actions: list[ToolCall] = []
    tool_calls: list[ToolCall] = []
    handoff_calls: list[ToolCall] = []
    pending_approval_calls: list[ToolCall] = []

    for action in processed_response.model_turn.tool_calls:
        actions.append(action)
        if agent._handoff_target_for(action) is not None:
            handoff_calls.append(action)
            continue
        tool_calls.append(action)
        approval_decision = _planning_approval_decision_for(agent, run_state, action)
        if approval_decision.requires_approval:
            if approval_decision.status == "unknown":
                _record_planned_approval_required(
                    run_state,
                    action,
                    approval_decision.approval_decision,
                )
            pending_approval_calls.append(action)

    return ToolExecutionPlan(
        actions=tuple(actions),
        tool_calls=tuple(tool_calls),
        handoff_calls=tuple(handoff_calls),
        pending_approval_calls=tuple(pending_approval_calls),
    )
