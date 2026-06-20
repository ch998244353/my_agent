from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .contracts import ToolCall
from .lifecycle import LifecycleHookSequence
from .run_recording import run_verification_after_tool
from .run_state import RunState
from .run_steps import ToolExecutionOutcome, execute_tool_call
from .tool_runtime import ToolExecutionLimits
from .verification import VerificationPolicy, VerificationRunner

if TYPE_CHECKING:
    from .agent import Agent


@dataclass(frozen=True)
class ResumeToolApprovalResult:
    outcomes: tuple[ToolExecutionOutcome, ...]
    remaining_tool_calls: tuple[ToolCall, ...]

    @property
    def has_pending_approvals(self) -> bool:
        return bool(self.remaining_tool_calls)


def _remaining_tool_calls_from(
    tool_calls: tuple[ToolCall, ...],
    start_index: int,
) -> tuple[ToolCall, ...]:
    return tool_calls[start_index:]


def resume_pending_tool_approvals(
    agent: Agent,
    run_state: RunState,
    *,
    tool_use_behavior: str | dict[str, list[str]] = "run_llm_again",
    hooks: LifecycleHookSequence = (),
    finalize_output: bool = True,
    trace_include_sensitive_data: bool = True,
    default_execution_limits: ToolExecutionLimits | None = None,
    verification_policy: VerificationPolicy | None = None,
    verification_runner: VerificationRunner | None = None,
) -> ResumeToolApprovalResult:
    outcomes: list[ToolExecutionOutcome] = []
    remaining_tool_calls: list[ToolCall] = []
    pending_tool_calls = run_state.pending_tool_calls

    for index, action in enumerate(pending_tool_calls):
        approval_status = run_state.context_wrapper.approval_status_for(
            action.tool_name,
            action.call_id,
        )
        if approval_status in ("pending", "unknown"):
            remaining_tool_calls.extend(
                _remaining_tool_calls_from(pending_tool_calls, index)
            )
            break

        step_number = run_state.next_step_number()
        outcome = execute_tool_call(
            agent,
            action,
            run_state,
            step_number=step_number,
            tool_use_behavior=tool_use_behavior,
            hooks=hooks,
            finalize_output=finalize_output,
            trace_include_sensitive_data=trace_include_sensitive_data,
            default_execution_limits=default_execution_limits,
        )
        outcomes.append(outcome)
        if approval_status == "approved":
            run_verification_after_tool(
                agent,
                action,
                run_state,
                step_number,
                verification_policy,
                verification_runner,
            )

    run_state.pending_tool_calls = tuple(remaining_tool_calls)
    return ResumeToolApprovalResult(
        outcomes=tuple(outcomes),
        remaining_tool_calls=run_state.pending_tool_calls,
    )
