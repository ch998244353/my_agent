# 兼容旧的 agents.run_steps 导入路径，并暂存尚未迁移的 handoff/recording 逻辑。


from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .contracts import (
    RunItem,
    StepRecord,
    ToolCall,
)
from .lifecycle import (
    LifecycleHookSequence,
    emit_handoff,
)
from .model_turn import (
    ModelTurnResult,
    TurnInput,
    _message_to_trace_dict,
    _run_model_turn_impl,
    prepare_turn_input,
    run_model_turn,
)
from .run_recording import (
    MODEL_OUTPUT_TEXT_FINAL_SOURCE,
    record_final_output,
    record_model_error,
    record_model_text_final_output,
    record_run_stopped,
    record_tool_call,
    record_tool_input_guardrail,
    record_tool_output_guardrail,
    run_verification_after_tool,
)
from .run_state import RunState
from .tool_execution import (
    DEFAULT_TOOL_APPROVAL_REJECTION_MESSAGE,
    TOOL_APPROVAL_REJECTED_REASON,
    ToolExecutionOutcome,
    ToolResultInfo,
    execute_tool_call,
    interpret_tool_result,
    record_tool_error,
    record_tool_approval_rejected,
    record_tool_approval_required,
    record_tool_output,
)
from .tool_planning import ToolExecutionPlan, build_tool_execution_plan
from .turn_resolution import (
    MODEL_RETURNED_NO_TOOL_CALL,
    NextStep,
    NextStepFinalOutput,
    NextStepHandoff,
    NextStepPendingApproval,
    NextStepRunAgain,
    NextStepStopped,
    ProcessedResponse,
    SingleStepResult,
    _plain_text_final_output,
    process_model_turn,
    resolve_final_output_step,
    resolve_handoff_step,
    resolve_model_response_step,
    resolve_no_tool_call_step,
    resolve_pending_approval_step,
    resolve_tool_final_output_step,
    resolve_tool_run_again_step,
)
from .tracing import handoff_span

if TYPE_CHECKING:
    from .agent import Agent


@dataclass(frozen=True)
class HandoffOutcome:
    action: ToolCall
    target_agent_name: str
    task: str
    final_answer: Any | None
    reached_final_answer: bool


def execute_handoff(
    agent: Agent,
    action: ToolCall,
    target_agent: Agent,
    run_state: RunState,
    step_number: int,
    hooks: LifecycleHookSequence = (),
) -> HandoffOutcome:
    task = action.arguments.get("task")
    if task is None:
        task = agent.memory.task or ""
    elif not isinstance(task, str):
        task = str(task)
    with handoff_span(
        agent.name,
        target_agent.name,
        task=task,
        call_id=action.call_id,
    ) as handoff_span_ctx:
        outcome = _execute_handoff_impl(
            agent,
            action,
            target_agent,
            task,
            run_state,
            step_number,
            hooks,
        )
        handoff_span_ctx.record.span_data.data["final_answer"] = outcome.final_answer
        handoff_span_ctx.record.span_data.data["reached_final_answer"] = (
            outcome.reached_final_answer
        )
        return outcome


def _execute_handoff_impl(
    agent: Agent,
    action: ToolCall,
    target_agent: Agent,
    task: str,
    run_state: RunState,
    step_number: int,
    hooks: LifecycleHookSequence = (),
) -> HandoffOutcome:

    emit_handoff(hooks, run_state.context_wrapper, agent, target_agent)
    target_result = target_agent.run(task)
    run_state.record_tool_step()
    run_state.final_answer = target_result.final_answer
    run_state.reached_final_answer = target_result.reached_final_answer
    run_state.new_items.append(
        RunItem(
            item_type="handoff",
            step_number=step_number,
            payload=target_result,
            metadata={
                "target_agent": target_agent.name,
                "task": task,
            },
        )
    )
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            tool_calls=[action],
            observation=(
                f"Handoff to {target_agent.name} returned:\n"
                f"{target_result.final_answer}"
            ),
            is_final_answer=target_result.reached_final_answer,
        )
    )
    if target_result.reached_final_answer:
        run_state.new_items.append(
            RunItem(
                item_type="final_output",
                step_number=step_number,
                payload=target_result.final_answer,
                metadata={"source_agent": target_agent.name},
            )
        )

    return HandoffOutcome(
        action=action,
        target_agent_name=target_agent.name,
        task=task,
        final_answer=target_result.final_answer,
        reached_final_answer=target_result.reached_final_answer,
    )
