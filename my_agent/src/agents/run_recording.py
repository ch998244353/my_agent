from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import RunItem, StepRecord, ToolCall
from .models import format_model_error
from .verification import VerificationPolicy, VerificationResult, VerificationRunner

if TYPE_CHECKING:
    from .agent import Agent
    from .run_state import RunState


MODEL_OUTPUT_TEXT_FINAL_SOURCE = "model_output_text"


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
