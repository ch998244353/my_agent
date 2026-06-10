from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .contracts import ModelResponse, RunItem, ToolCall
from .run_recording import (
    MODEL_OUTPUT_TEXT_FINAL_SOURCE,
    record_model_text_final_output,
)
from .tool_execution import ToolExecutionOutcome
from .tool_planning import ToolExecutionPlan

if TYPE_CHECKING:
    from .agent import Agent
    from .model_turn import ModelTurnResult
    from .run_state import RunState


@dataclass(frozen=True)
class ProcessedResponse:
    """Classified model turn output before the run loop chooses the next step."""

    model_turn: ModelTurnResult
    model_response: ModelResponse | None
    tool_calls: list[ToolCall]
    handoff_calls: list[ToolCall]
    final_output: Any | None = None
    has_final_output: bool = False


# 状态机下一步：得到最终输出，主循环可以结束。
@dataclass(frozen=True)
class NextStepFinalOutput:
    """State-machine outcome carrying the final output that ends the run."""

    final_output: Any | None


# 状态机下一步：工具结果已经写回模型上下文，主循环需要再次调用模型。
@dataclass(frozen=True)
class NextStepRunAgain:
    """State-machine outcome telling the run loop to run again after tool results."""

    reason: str = "tool_results"


# 状态机下一步：发生 handoff，主循环应切换或委托给目标 agent。
@dataclass(frozen=True)
class NextStepHandoff:
    """State-machine outcome for handoff to a target agent."""

    target_agent: Agent


# 状态机下一步：工具调用需要等待用户审批，主循环应暂停而不是执行工具或返回最终输出。
@dataclass(frozen=True)
class NextStepPendingApproval:
    """State-machine outcome for tool calls paused on approval."""

    pending_approval_calls: tuple[ToolCall, ...]
    reason: str = "tool_approval_required"


# 状态机下一步：没有后续动作，主循环记录停止原因后结束。
@dataclass(frozen=True)
class NextStepStopped:
    """State-machine outcome that records a stop reason without final output."""

    reason: str


NextStep = (
    NextStepFinalOutput
    | NextStepRunAgain
    | NextStepHandoff
    | NextStepPendingApproval
    | NextStepStopped
)


MODEL_RETURNED_NO_TOOL_CALL = "model_returned_no_tool_call"


def _plain_text_final_output(
    model_response: ModelResponse | None,
    *,
    tool_calls: list[ToolCall],
    handoff_calls: list[ToolCall],
    has_final_output: bool,
) -> str | None:
    if has_final_output:
        return None
    if tool_calls or handoff_calls:
        return None
    if model_response is None or model_response.output_text is None:
        return None
    if not model_response.output_text.strip():
        return None
    return model_response.output_text


@dataclass(frozen=True)
class SingleStepResult:
    model_turn: ModelTurnResult | None
    next_step: NextStep
    generated_items: tuple[RunItem, ...] = ()


# 处理出 processed response 模型一次分类后的返回结果
def process_model_turn(
    agent: Agent,
    model_turn: ModelTurnResult,
    run_state: RunState,
    step_number: int,
) -> ProcessedResponse:
    tool_calls: list[ToolCall] = []
    handoff_calls: list[ToolCall] = []
    for tool_call in model_turn.tool_calls:
        if agent._handoff_target_for(tool_call) is not None:
            handoff_calls.append(tool_call)
        else:
            tool_calls.append(tool_call)
    plain_text_final = _plain_text_final_output(
        model_turn.response,
        tool_calls=tool_calls,
        handoff_calls=handoff_calls,
        has_final_output=run_state.reached_final_answer,
    )
    if plain_text_final is not None:
        record_model_text_final_output(run_state, step_number, plain_text_final)
    return ProcessedResponse(
        model_turn=model_turn,
        model_response=model_turn.response,
        tool_calls=tool_calls,
        handoff_calls=handoff_calls,
        final_output=run_state.final_answer if run_state.reached_final_answer else None,
        has_final_output=run_state.reached_final_answer,
    )


# 根据 ProcessedResponse.has_final_output 决定是否返回最终输出步骤
def resolve_final_output_step(
    processed_response: ProcessedResponse,
) -> SingleStepResult | None:
    if not processed_response.has_final_output:
        return None
    return SingleStepResult(
        model_turn=processed_response.model_turn,
        next_step=NextStepFinalOutput(processed_response.final_output),
    )


def resolve_no_tool_call_step(
    processed_response: ProcessedResponse,
) -> SingleStepResult | None:
    if processed_response.has_final_output:
        return None
    if processed_response.tool_calls or processed_response.handoff_calls:
        return None
    return SingleStepResult(
        model_turn=processed_response.model_turn,
        next_step=NextStepStopped(MODEL_RETURNED_NO_TOOL_CALL),
    )


def resolve_pending_approval_step(
    model_turn: ModelTurnResult,
    execution_plan: ToolExecutionPlan,
) -> SingleStepResult | None:
    if not execution_plan.should_pause:
        return None
    return SingleStepResult(
        model_turn=model_turn,
        next_step=NextStepPendingApproval(execution_plan.pending_approval_calls),
    )


def resolve_model_response_step(
    processed_response: ProcessedResponse,
) -> SingleStepResult | None:
    return resolve_final_output_step(processed_response) or resolve_no_tool_call_step(
        processed_response
    )


def resolve_tool_final_output_step(
    model_turn: ModelTurnResult,
    tool_outcome: ToolExecutionOutcome,
) -> SingleStepResult | None:
    if not tool_outcome.should_stop:
        return None
    if not tool_outcome.is_final_answer:
        return SingleStepResult(
            model_turn=model_turn,
            next_step=NextStepStopped(tool_outcome.stop_reason or "tool_stopped"),
        )
    return SingleStepResult(
        model_turn=model_turn,
        next_step=NextStepFinalOutput(tool_outcome.result_value),
    )


def resolve_handoff_step(
    model_turn: ModelTurnResult,
    handoff_outcome: Any,
    target_agent: Agent,
) -> SingleStepResult:
    return SingleStepResult(
        model_turn=model_turn,
        next_step=NextStepHandoff(target_agent),
    )


def resolve_tool_run_again_step(
    model_turn: ModelTurnResult,
    tool_outcome: ToolExecutionOutcome,
) -> SingleStepResult | None:
    if tool_outcome.should_stop:
        return None
    return SingleStepResult(
        model_turn=model_turn,
        next_step=NextStepRunAgain(),
    )
