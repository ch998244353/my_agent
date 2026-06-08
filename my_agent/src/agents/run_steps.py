# 把“每一轮模型输入准备”和“执行一次模型调用”
# 的逻辑从 agent.py 拆到新模块 run_steps.py。


from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

from .contracts import (
    ChatMessage,
    CodeExecutionResult,
    ModelResponse,
    RunItem,
    StepRecord,
    TOOL_APPROVAL_REQUIRED_METADATA_KEY,
    ToolApprovalRequest,
    ToolCall,
    ToolSpec,
)
from .lifecycle import (
    LifecycleHookSequence,
    emit_handoff,
    emit_llm_end,
    emit_llm_start,
    emit_tool_end,
    emit_tool_start,
)
from .model_settings import ModelSettings
from .models import ModelCallError, call_model_response, format_model_error
from .output import set_structured_final_answer
from .run_context import RunContextWrapper
from .run_state import RunState
from .tool_guardrails import (
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
)
from .tool_runtime import (
    ToolApprovalDecision,
    ToolExecutionLimits,
    ToolExecutionReport,
    clip_tool_text,
    format_tool_error,
    format_tool_observation,
    tool_output_preview,
)
from .tools import FINAL_ANSWER_TOOL_NAME
from .tracing import handoff_span, model_span, record_span_error, tool_span
from .verification import VerificationPolicy, VerificationResult, VerificationRunner

if TYPE_CHECKING:
    from .agent import Agent


def _message_to_trace_dict(message: ChatMessage) -> dict[str, Any]:
    return {"role": message.role, "content": message.content}


# “这一轮要发给模型的输入”，包括消息、工具说明和模型设置
@dataclass(frozen=True)
class TurnInput:
    messages: list[ChatMessage]
    tool_specs: list[ToolSpec]
    model_settings: ModelSettings


# “模型这一轮返回后的结果”。
@dataclass(frozen=True)
class ModelTurnResult:
    response: ModelResponse | None
    tool_calls: list[ToolCall]


@dataclass(frozen=True)
class ProcessedResponse:
    """Classified model turn output before the run loop chooses the next step."""

    model_turn: ModelTurnResult
    model_response: ModelResponse | None
    tool_calls: list[ToolCall]
    handoff_calls: list[ToolCall]
    final_output: Any | None = None
    has_final_output: bool = False


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


# 给“工具执行结果”和“handoff 执行结果”一个稳定返回结构
@dataclass(frozen=True)
class ToolExecutionOutcome:
    action: ToolCall
    result: Any | None
    result_value: Any | None
    observation: str | None
    is_final_answer: bool
    should_stop: bool
    error: str | None = None
    stop_reason: str | None = None


TOOL_APPROVAL_REJECTED_REASON = "tool_approval_rejected"
DEFAULT_TOOL_APPROVAL_REJECTION_MESSAGE = "Tool execution was rejected by the user."


@dataclass(frozen=True)
class HandoffOutcome:
    action: ToolCall
    target_agent_name: str
    task: str
    final_answer: Any | None
    reached_final_answer: bool


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
MODEL_OUTPUT_TEXT_FINAL_SOURCE = "model_output_text"


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
        if (
            run_state.context_wrapper.approval_status_for(
                action.tool_name,
                action.call_id,
            )
            == "pending"
        ):
            pending_approval_calls.append(action)

    return ToolExecutionPlan(
        actions=tuple(actions),
        tool_calls=tuple(tool_calls),
        handoff_calls=tuple(handoff_calls),
        pending_approval_calls=tuple(pending_approval_calls),
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
    handoff_outcome: HandoffOutcome,
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


# 把一次工具执行结果解释成稳定结构
@dataclass(frozen=True)
class ToolResultInfo:
    result_value: Any | None
    observation: str
    is_final_answer: bool
    should_stop: bool


def _field_from_object_or_dict(item: Any, field_name: str) -> Any:
    if isinstance(item, dict):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _is_code_execution_result(result: Any) -> bool:
    return isinstance(result, CodeExecutionResult) or (
        isinstance(result, dict)
        and {"output", "logs", "is_final_answer"}.issubset(result.keys())
    )


def _render_code_observation(result: Any) -> str:
    logs = _field_from_object_or_dict(result, "logs")
    if logs is None:
        logs = ""
    elif not isinstance(logs, str):
        logs = str(logs)

    output = _field_from_object_or_dict(result, "output")
    return f"Execution logs:\n{logs}Last output from code snippet:\n{output}"


# 通工具结果和 Python 执行结果的处理规则不再放在 Agent 类里。
def tool_result_value(result: Any) -> Any:
    if _is_code_execution_result(result):
        return _field_from_object_or_dict(result, "output")
    return result


def render_tool_observation(
    result: Any,
    limits: ToolExecutionLimits | None = None,
) -> str:
    if _is_code_execution_result(result):
        text = _render_code_observation(result)
        limit = (limits or ToolExecutionLimits()).max_output_chars
        return clip_tool_text(text, limit)
    return tool_output_preview(result, limits)


def is_final_answer_result(action: ToolCall, result: Any) -> bool:
    if action.tool_name == FINAL_ANSWER_TOOL_NAME:
        return True
    if _is_code_execution_result(result):
        return bool(_field_from_object_or_dict(result, "is_final_answer"))
    return False


def should_stop_after_tool(
    action: ToolCall,
    is_final_answer: bool,
    tool_use_behavior: str | dict[str, list[str]],
) -> bool:
    if is_final_answer:
        return True
    if tool_use_behavior == "run_llm_again":
        return False
    if tool_use_behavior == "stop_on_first_tool":
        return True
    if isinstance(tool_use_behavior, dict):
        stop_at_tool_names = tool_use_behavior.get("stop_at_tool_names", [])
        return action.tool_name in stop_at_tool_names
    raise ValueError(f"Invalid tool_use_behavior: {tool_use_behavior}")


# 通过工具执行结果获得加工后的 toolresult
def interpret_tool_result(
    action: ToolCall,
    result: Any,
    tool_use_behavior: str | dict[str, list[str]],
    execution_limits: ToolExecutionLimits | None = None,
) -> ToolResultInfo:
    result_value = tool_result_value(result)
    observation = render_tool_observation(result, execution_limits)
    is_final_answer = is_final_answer_result(action, result)
    should_stop = should_stop_after_tool(
        action,
        is_final_answer,
        tool_use_behavior,
    )
    return ToolResultInfo(
        result_value=result_value,
        observation=observation,
        is_final_answer=is_final_answer,
        should_stop=should_stop,
    )

# 如果模型支持 record_tool_output()，就在这里统一回写工具结果。代码执行结果会先转成模型能读懂的 observation
def record_tool_output(
    model: Any,
    action: ToolCall,
    output: Any,
    execution_limits: ToolExecutionLimits | None = None,
    *,
    success: bool = True,
    reason: str | None = None,
    error_type: str | None = None,
) -> None:
    record_output = getattr(model, "record_tool_output", None)
    if record_output is None:
        return
    if success:
        output = render_tool_observation(output, execution_limits)
    record_output(
        action,
        format_tool_observation(
            action.tool_name,
            output,
            success=success,
            reason=reason,
            error_type=error_type,
            limits=execution_limits,
        ),
    )


# 把“准备本轮模型输入”的职责集中到 run_steps.py。
def prepare_turn_input(
    agent: Agent,
    context_wrapper: RunContextWrapper | None = None,
    model_settings: ModelSettings | None = None,
) -> TurnInput:
    context_wrapper = context_wrapper or RunContextWrapper()
    return TurnInput(
        messages=agent._messages_for_model(),
        tool_specs=agent._tool_specs_for_model(context_wrapper),
        model_settings=model_settings or agent.model_settings,
    )


# 执行一次模型调用，并把 model_response 写入 RunState.new_items
def run_model_turn(
    agent: Agent,
    turn_input: TurnInput,
    run_state: RunState,
    step_number: int,
    hooks: LifecycleHookSequence = (),
    trace_include_sensitive_data: bool = True,
) -> ModelTurnResult:
    model_name = agent.model.__class__.__name__
    with model_span(
        model_name,
        agent=agent.name,
        step_number=step_number,
        message_count=len(turn_input.messages),
        tool_count=len(turn_input.tool_specs),
        input=(
            [_message_to_trace_dict(message) for message in turn_input.messages]
            if trace_include_sensitive_data
            else None
        ),
    ) as model_span_ctx:
        try:
            return _run_model_turn_impl(agent, turn_input, run_state, step_number, hooks)
        except Exception as exc:
            if isinstance(exc, ModelCallError):
                record_span_error(model_span_ctx, exc)
                raise
            model_error = ModelCallError(exc)
            record_span_error(model_span_ctx, model_error)
            raise model_error from exc


def _run_model_turn_impl(
    agent: Agent,
    turn_input: TurnInput,
    run_state: RunState,
    step_number: int,
    hooks: LifecycleHookSequence = (),
) -> ModelTurnResult:
    emit_llm_start(hooks, run_state.context_wrapper, agent, turn_input)
    get_response = getattr(agent.model, "get_response", None)
    if callable(get_response):
        model_response = call_model_response(
            agent.model,
            turn_input.messages,
            turn_input.tool_specs,
            turn_input.model_settings,
        )
        emit_llm_end(hooks, run_state.context_wrapper, agent, model_response)
        run_state.new_items.append(
            RunItem(
                item_type="model_response",
                step_number=step_number,
                payload=model_response,
            )
        )
        set_structured_final_answer(
            model_response,
            agent.output_type,
            run_state,
            step_number,
        )
        return ModelTurnResult(
            response=model_response,
            tool_calls=list(model_response.tool_calls),
        )

    action = agent.model.decide(turn_input.messages, turn_input.tool_specs)
    emit_llm_end(hooks, run_state.context_wrapper, agent, None)
    if action is None:
        return ModelTurnResult(response=None, tool_calls=[])
    return ModelTurnResult(response=None, tool_calls=[action])


# 把工具失败后的 RunItem、memory、模型可见错误输出统一放到一个函数里，主循环不再关心错误怎么记录
def record_model_error(
    agent: Agent,
    error_text: BaseException | str,
    run_state: RunState,
    step_number: int,
) -> None:
    if isinstance(error_text, BaseException):
        error_text = format_model_error(error_text)
    run_state.new_items.append(
        RunItem(
            item_type="model_error",
            step_number=step_number,
            payload=error_text,
        )
    )
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            error=error_text,
        )
    )


# 停止原因记录函数
def record_run_stopped(
    run_state: RunState,
    step_number: int,
    reason: str,
) -> None:
    run_state.new_items.append(
        RunItem(
            item_type="run_stopped",
            step_number=step_number,
            payload=reason,
        )
    )


# 工具调用事件记录函数
def record_tool_call(
    run_state: RunState,
    action: ToolCall,
    step_number: int,
) -> None:
    run_state.new_items.append(
        RunItem(
            item_type="tool_call",
            step_number=step_number,
            payload=action,
        )
    )


def record_tool_input_guardrail(
    run_state: RunState,
    guardrail_result: Any,
    step_number: int,
) -> None:
    run_state.tool_input_guardrail_results.append(guardrail_result)
    run_state.new_items.append(
        RunItem(
            item_type="tool_input_guardrail",
            step_number=step_number,
            payload=guardrail_result,
            metadata={
                "guardrail_name": guardrail_result.guardrail_name,
                "behavior": guardrail_result.output.behavior,
            },
        )
    )


def record_tool_output_guardrail(
    run_state: RunState,
    guardrail_result: Any,
    step_number: int,
) -> None:
    run_state.tool_output_guardrail_results.append(guardrail_result)
    run_state.new_items.append(
        RunItem(
            item_type="tool_output_guardrail",
            step_number=step_number,
            payload=guardrail_result,
            metadata={
                "guardrail_name": guardrail_result.guardrail_name,
                "behavior": guardrail_result.output.behavior,
            },
        )
    )


def record_tool_error(
    agent: Agent,
    action: ToolCall,
    error_text: BaseException | str,
    run_state: RunState,
    step_number: int,
    default_execution_limits: ToolExecutionLimits | None = None,
) -> ToolExecutionOutcome:
    tool_limits = default_execution_limits
    try:
        tool_limits = agent.tool_registry.get(action.tool_name).execution_limits or tool_limits
    except Exception:
        pass
    error_type = type(error_text).__name__ if isinstance(error_text, BaseException) else "ToolError"
    formatted_error = format_tool_error(
        action.tool_name,
        error_text,
        error_type=error_type,
        limits=tool_limits,
    )
    execution_report = ToolExecutionReport(
        tool_name=action.tool_name,
        call_id=action.call_id,
        success=False,
        output_preview=formatted_error,
        error_type=error_type,
        reason=error_type,
    )
    run_state.record_tool_step()
    run_state.new_items.append(
        RunItem(
            item_type="tool_error",
            step_number=step_number,
            payload=formatted_error,
            metadata={
                "observation": formatted_error,
                "tool_execution": execution_report.to_metadata(),
            },
        )
    )
    record_tool_output(
        agent.model,
        action,
        error_text,
        tool_limits,
        success=False,
        reason=error_type,
        error_type=error_type,
    )
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            tool_calls=[action],
            error=formatted_error,
        )
    )
    return ToolExecutionOutcome(
        action=action,
        result=None,
        result_value=None,
        observation=None,
        is_final_answer=False,
        should_stop=False,
        error=formatted_error,
    )


def record_tool_approval_required(
    action: ToolCall,
    approval_decision: ToolApprovalDecision,
    run_state: RunState,
    step_number: int,
) -> ToolExecutionOutcome:
    reason = "User approval is required before running this tool."
    if approval_decision.error_message is not None:
        reason = (
            "Tool approval check failed before execution: "
            f"{approval_decision.error_type}: {approval_decision.error_message}"
        )
    approval_request = ToolApprovalRequest(
        tool_name=action.tool_name,
        call_id=action.call_id,
        arguments=dict(action.arguments),
        reason=reason,
    )
    run_state.context_wrapper.request_tool_call_approval(action.tool_name, action.call_id)
    run_state.new_items.append(
        RunItem(
            item_type="tool_approval_required",
            step_number=step_number,
            payload=approval_request,
            metadata={
                TOOL_APPROVAL_REQUIRED_METADATA_KEY: True,
                "approval_error_type": approval_decision.error_type,
            },
        )
    )
    record_run_stopped(run_state, step_number, "tool_approval_required")
    return ToolExecutionOutcome(
        action=action,
        result=None,
        result_value=approval_request,
        observation=None,
        is_final_answer=False,
        should_stop=True,
        stop_reason="tool_approval_required",
    )


def record_tool_approval_rejected(
    agent: Agent,
    action: ToolCall,
    rejection_message: str | None,
    run_state: RunState,
    step_number: int,
    execution_limits: ToolExecutionLimits | None = None,
) -> ToolExecutionOutcome:
    result = rejection_message or DEFAULT_TOOL_APPROVAL_REJECTION_MESSAGE
    observation = format_tool_observation(
        action.tool_name,
        result,
        success=False,
        reason=TOOL_APPROVAL_REJECTED_REASON,
        error_type=TOOL_APPROVAL_REJECTED_REASON,
        limits=execution_limits,
    )
    execution_report = ToolExecutionReport(
        tool_name=action.tool_name,
        call_id=action.call_id,
        success=False,
        output_preview=observation,
        error_type=TOOL_APPROVAL_REJECTED_REASON,
        reason=TOOL_APPROVAL_REJECTED_REASON,
    )
    run_state.record_tool_step()
    run_state.new_items.append(
        RunItem(
            item_type="tool_result",
            step_number=step_number,
            payload=result,
            metadata={
                "observation": observation,
                "tool_execution": execution_report.to_metadata(),
                TOOL_APPROVAL_REQUIRED_METADATA_KEY: False,
                "approval_status": "rejected",
            },
        )
    )
    record_tool_output(
        agent.model,
        action,
        result,
        execution_limits,
        success=False,
        reason=TOOL_APPROVAL_REJECTED_REASON,
        error_type=TOOL_APPROVAL_REJECTED_REASON,
    )
    agent.memory.add_step(
        StepRecord(step_number=step_number, tool_calls=[action], observation=observation)
    )
    return ToolExecutionOutcome(
        action=action,
        result=result,
        result_value=result,
        observation=observation,
        is_final_answer=False,
        should_stop=False,
    )


# 在只负责执行工具、为agent记录结果、推进状态；“结果怎么理解”交给 interpret_tool_result()。
def record_final_output(
    run_state: RunState,
    step_number: int,
    final_answer: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    run_state.final_answer = final_answer
    run_state.reached_final_answer = True
    run_state.new_items.append(
        RunItem(
            item_type="final_output",
            step_number=step_number,
            payload=final_answer,
            metadata=metadata or {},
        )
    )


def record_model_text_final_output(
    run_state: RunState,
    step_number: int,
    final_answer: str,
) -> None:
    record_final_output(
        run_state,
        step_number,
        final_answer,
        metadata={"source": MODEL_OUTPUT_TEXT_FINAL_SOURCE},
    )


def _render_verification_observation(
    results: tuple[VerificationResult, ...],
    policy: VerificationPolicy,
) -> str:
    return "\n\n".join(
        result.to_observation(policy.max_output_chars)
        for result in results
    )


def _verification_attempts_taken(run_state: RunState) -> int:
    return sum(
        1
        for item in run_state.new_items
        if item.item_type == "verification_result"
    )


def _render_verification_skipped_observation(
    action: ToolCall,
    policy: VerificationPolicy,
) -> str:
    return "\n".join(
        [
            "Verification skipped",
            "reason: max_attempts_reached",
            f"trigger_tool: {action.tool_name}",
            f"max_attempts: {policy.max_attempts}",
        ]
    )


def _record_verification_skipped(
    agent: Agent,
    action: ToolCall,
    run_state: RunState,
    step_number: int,
    policy: VerificationPolicy,
) -> None:
    observation = _render_verification_skipped_observation(action, policy)
    run_state.new_items.append(
        RunItem(
            item_type="verification_skipped",
            step_number=step_number,
            payload=observation,
            metadata={
                "trigger_tool": action.tool_name,
                "reason": "max_attempts_reached",
                "max_attempts": policy.max_attempts,
            },
        )
    )
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            observation=observation,
        )
    )


def run_verification_after_tool(
    agent: Agent,
    action: ToolCall,
    run_state: RunState,
    step_number: int,
    policy: VerificationPolicy | None,
    runner: VerificationRunner | None,
) -> tuple[VerificationResult, ...]:
    if policy is None or runner is None:
        return ()
    if not policy.should_run_after_tool(action.tool_name):
        return ()
    if _verification_attempts_taken(run_state) >= policy.max_attempts:
        _record_verification_skipped(agent, action, run_state, step_number, policy)
        return ()

    results = runner.run(policy)
    if not results:
        return ()

    observation = _render_verification_observation(results, policy)
    passed = all(result.passed for result in results)
    run_state.new_items.append(
        RunItem(
            item_type="verification_result",
            step_number=step_number,
            payload=results,
            metadata={
                "trigger_tool": action.tool_name,
                "passed": passed,
                "observation": observation,
            },
        )
    )
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            observation=observation,
        )
    )
    return results


def execute_tool_call(
    agent: Agent,
    action: ToolCall,
    run_state: RunState,
    step_number: int,
    tool_use_behavior: str | dict[str, list[str]],
    hooks: LifecycleHookSequence = (),
    finalize_output: bool = True,
    trace_include_sensitive_data: bool = True,
    default_execution_limits: ToolExecutionLimits | None = None,
) -> ToolExecutionOutcome:
    with tool_span(
        action.tool_name,
        agent=agent.name,
        step_number=step_number,
        call_id=action.call_id,
        arguments=action.arguments if trace_include_sensitive_data else None,
    ) as tool_span_ctx:
        try:
            outcome = _execute_tool_call_impl(
                agent,
                action,
                run_state,
                step_number,
                tool_use_behavior,
                hooks,
                finalize_output,
                default_execution_limits,
            )
        except Exception as exc:
            record_span_error(tool_span_ctx, exc)
            raise
        if trace_include_sensitive_data:
            tool_span_ctx.record.span_data.data["output"] = outcome.result_value
        return outcome


def _execute_tool_call_impl(
    agent: Agent,
    action: ToolCall,
    run_state: RunState,
    step_number: int,
    tool_use_behavior: str | dict[str, list[str]],
    hooks: LifecycleHookSequence = (),
    finalize_output: bool = True,
    default_execution_limits: ToolExecutionLimits | None = None,
) -> ToolExecutionOutcome:
    emit_tool_start(hooks, run_state.context_wrapper, agent, action)
    tool_started_at = perf_counter()
    rejection_metadata: dict[str, Any] = {}
    tool = agent.tool_registry.get(action.tool_name)
    execution_limits = tool.execution_limits or default_execution_limits
    if not tool.is_enabled_for(run_state.context_wrapper, agent):
        raise RuntimeError(f"Tool '{action.tool_name}' is disabled.")
    approval_status = run_state.context_wrapper.approval_status_for(
        action.tool_name,
        action.call_id,
    )
    if approval_status == "rejected":
        return record_tool_approval_rejected(
            agent,
            action,
            run_state.context_wrapper.rejection_message_for(
                action.tool_name,
                action.call_id,
            ),
            run_state,
            step_number,
            execution_limits,
        )
    # 真正的 interruption/resume 会在后续模块实现；本课只记录审批需求。
    approval_required = tool.requires_approval_for(
        run_state.context_wrapper,
        agent,
        action,
    )
    if approval_required:
        return record_tool_approval_required(
            action,
            approval_required,
            run_state,
            step_number,
        )
    for guardrail in tool.tool_input_guardrails:
        if not guardrail.is_enabled_for(run_state.context_wrapper, agent, action):
            continue
        guardrail_result = guardrail.run(run_state.context_wrapper, agent, action)
        record_tool_input_guardrail(run_state, guardrail_result, step_number)
        behavior = guardrail_result.output.behavior
        if behavior == "allow":
            continue
        if behavior == "raise_exception":
            raise ToolInputGuardrailTripwireTriggered(guardrail_result)
        result = guardrail_result.output.message or "Tool input rejected by guardrail."
        rejection_reason = "input_guardrail_rejected"
        result_info = ToolResultInfo(
            result_value=result,
            observation=format_tool_observation(
                action.tool_name,
                result,
                success=False,
                reason=rejection_reason,
                error_type=rejection_reason,
                limits=execution_limits,
            ),
            is_final_answer=False,
            should_stop=False,
        )
        rejection_metadata = {
            "rejected_by": guardrail_result.guardrail_name,
            "guardrail_stage": "input",
            "guardrail_name": guardrail_result.guardrail_name,
            "guardrail_behavior": behavior,
        }
        break
    else:  # 没有被 break 截断, 进入else分支
        result = agent.tool_registry.execute(action.tool_name, action.arguments)
        result_info = interpret_tool_result(
            action,
            result,
            tool_use_behavior,
            execution_limits,
        )
        for guardrail in tool.tool_output_guardrails:
            if not guardrail.is_enabled_for(run_state.context_wrapper, agent, action, result):
                continue
            guardrail_result = guardrail.run(run_state.context_wrapper, agent, action, result)
            record_tool_output_guardrail(run_state, guardrail_result, step_number)
            behavior = guardrail_result.output.behavior
            if behavior == "allow":
                continue
            if behavior == "raise_exception":
                raise ToolOutputGuardrailTripwireTriggered(guardrail_result)
            result = guardrail_result.output.message or "Tool output rejected by guardrail."
            rejection_reason = "output_guardrail_rejected"
            result_info = ToolResultInfo(
                result_value=result,
                observation=format_tool_observation(
                    action.tool_name,
                    result,
                    success=False,
                    reason=rejection_reason,
                    error_type=rejection_reason,
                    limits=execution_limits,
                ),
                is_final_answer=False,
                should_stop=False,
            )
            rejection_metadata = {
                "rejected_by": guardrail_result.guardrail_name,
                "guardrail_stage": "output",
                "guardrail_name": guardrail_result.guardrail_name,
                "guardrail_behavior": behavior,
            }
            break

    run_state.record_tool_step()
    error_type = None
    if rejection_metadata:
        error_type = f"{rejection_metadata['guardrail_stage']}_guardrail_rejected"
    execution_report = ToolExecutionReport(
        tool_name=action.tool_name,
        call_id=action.call_id,
        success=not rejection_metadata,
        output_preview=result_info.observation,
        error_type=error_type,
        reason=error_type,
        elapsed_seconds=perf_counter() - tool_started_at,
    )
    result_metadata = {
        "observation": result_info.observation,
        "tool_execution": execution_report.to_metadata(),
        TOOL_APPROVAL_REQUIRED_METADATA_KEY: approval_required,
    }
    result_metadata.update(rejection_metadata)
    run_state.new_items.append(
        RunItem(
            item_type="tool_result",
            step_number=step_number,
            payload=result_info.result_value,
            metadata=result_metadata,
        )
    )
    record_tool_output(
        agent.model,
        action,
        result,
        execution_limits,
        success=not rejection_metadata,
        reason=error_type,
        error_type=error_type,
    )
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            tool_calls=[action],
            observation=result_info.observation,
            is_final_answer=result_info.should_stop,
        )
    )

    if result_info.should_stop and finalize_output:
        record_final_output(run_state, step_number, result_info.result_value)

    emit_tool_end(
        hooks,
        run_state.context_wrapper,
        agent,
        action,
        result_info.result_value,
    )

    return ToolExecutionOutcome(
        action=action,
        result=result,
        result_value=result_info.result_value,
        observation=result_info.observation,
        is_final_answer=result_info.is_final_answer,
        should_stop=result_info.should_stop,
    )


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
