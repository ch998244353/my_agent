# 把“每一轮模型输入准备”和“执行一次模型调用”
# 的逻辑从 agent.py 拆到新模块 run_steps.py。


from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .contracts import (
    ChatMessage,
    CodeExecutionResult,
    ModelResponse,
    RunItem,
    StepRecord,
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
from .output import set_structured_final_answer
from .run_context import RunContextWrapper
from .run_state import RunState
from .tool_guardrails import (
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
)
from .tools import FINAL_ANSWER_TOOL_NAME

if TYPE_CHECKING:
    from .agent import Agent


# “这一轮要发给模型的输入”，包括消息和工具说明
@dataclass(frozen=True)
class TurnInput:
    messages: list[ChatMessage]
    tool_specs: list[ToolSpec]


# “模型这一轮返回后的结果”。
@dataclass(frozen=True)
class ModelTurnResult:
    response: ModelResponse | None
    tool_calls: list[ToolCall]


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


@dataclass(frozen=True)
class HandoffOutcome:
    action: ToolCall
    target_agent_name: str
    task: str
    final_answer: Any | None
    reached_final_answer: bool


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


def render_tool_observation(result: Any) -> str:
    if _is_code_execution_result(result):
        return _render_code_observation(result)
    return str(result)


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
) -> ToolResultInfo:
    result_value = tool_result_value(result)
    observation = render_tool_observation(result)
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
def record_tool_output(model: Any, action: ToolCall, output: Any) -> None:
    record_output = getattr(model, "record_tool_output", None)
    if record_output is None:
        return
    if _is_code_execution_result(output):
        output = render_tool_observation(output)
    record_output(action, output)


# 把“准备本轮模型输入”的职责集中到 run_steps.py。
def prepare_turn_input(
    agent: Agent,
    context_wrapper: RunContextWrapper | None = None,
) -> TurnInput:
    context_wrapper = context_wrapper or RunContextWrapper()
    return TurnInput(
        messages=agent._messages_for_model(),
        tool_specs=agent._tool_specs_for_model(context_wrapper),
    )


# 执行一次模型调用，并把 model_response 写入 RunState.new_items
def run_model_turn(
    agent: Agent,
    turn_input: TurnInput,
    run_state: RunState,
    step_number: int,
    hooks: LifecycleHookSequence = (),
) -> ModelTurnResult:
    emit_llm_start(hooks, run_state.context_wrapper, agent, turn_input)
    get_response = getattr(agent.model, "get_response", None)
    if callable(get_response):
        model_response = get_response(turn_input.messages, turn_input.tool_specs)
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
    error_text: str,
    run_state: RunState,
    step_number: int,
) -> None:
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
    error_text: str,
    run_state: RunState,
    step_number: int,
) -> ToolExecutionOutcome:
    run_state.record_tool_step()
    run_state.new_items.append(
        RunItem(
            item_type="tool_error",
            step_number=step_number,
            payload=error_text,
        )
    )
    record_tool_output(agent.model, action, f"Error: {error_text}")
    agent.memory.add_step(
        StepRecord(
            step_number=step_number,
            tool_calls=[action],
            error=error_text,
        )
    )
    return ToolExecutionOutcome(
        action=action,
        result=None,
        result_value=None,
        observation=None,
        is_final_answer=False,
        should_stop=False,
        error=error_text,
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


def execute_tool_call(
    agent: Agent,
    action: ToolCall,
    run_state: RunState,
    step_number: int,
    tool_use_behavior: str | dict[str, list[str]],
    hooks: LifecycleHookSequence = (),
    finalize_output: bool = True,
) -> ToolExecutionOutcome:
    emit_tool_start(hooks, run_state.context_wrapper, agent, action)
    rejection_metadata: dict[str, Any] = {}
    tool = agent.tool_registry.get(action.tool_name)
    if not tool.is_enabled_for(run_state.context_wrapper, agent):
        raise RuntimeError(f"Tool '{action.tool_name}' is disabled.")
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
        result_info = ToolResultInfo(
            result_value=result,
            observation=str(result),
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
            result_info = ToolResultInfo(
                result_value=result,
                observation=str(result),
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
    result_metadata = {"observation": result_info.observation}
    result_metadata.update(rejection_metadata)
    run_state.new_items.append(
        RunItem(
            item_type="tool_result",
            step_number=step_number,
            payload=result_info.result_value,
            metadata=result_metadata,
        )
    )
    record_tool_output(agent.model, action, result)
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
