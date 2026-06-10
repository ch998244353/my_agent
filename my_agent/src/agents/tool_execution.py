from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

from .contracts import (
    CodeExecutionResult,
    RunItem,
    StepRecord,
    TOOL_APPROVAL_REQUIRED_METADATA_KEY,
    ToolApprovalRequest,
    ToolCall,
)
from .lifecycle import LifecycleHookSequence, emit_tool_end, emit_tool_start
from .run_recording import (
    record_final_output,
    record_run_stopped,
    record_tool_input_guardrail,
    record_tool_output_guardrail,
)
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
from .tracing import record_span_error, tool_span

if TYPE_CHECKING:
    from .agent import Agent
    from .run_state import RunState


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


@dataclass(frozen=True)
class ToolResultInfo:
    result_value: Any | None
    observation: str
    is_final_answer: bool
    should_stop: bool


TOOL_APPROVAL_REJECTED_REASON = "tool_approval_rejected"
DEFAULT_TOOL_APPROVAL_REJECTION_MESSAGE = "Tool execution was rejected by the user."


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
    approval_required: ToolApprovalDecision | bool = False
    if approval_status != "approved":
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
