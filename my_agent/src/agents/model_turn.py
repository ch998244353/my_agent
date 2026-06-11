# 把“每一轮模型输入准备”和“执行一次模型调用”
# 的逻辑集中到 model_turn.py。

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .context_chunks import build_turn_context
from .contracts import ChatMessage, ModelResponse, RunItem, ToolCall, ToolSpec
from .lifecycle import LifecycleHookSequence, emit_llm_end, emit_llm_start
from .model_settings import ModelSettings
from .models import ModelCallError, call_model_response
from .output import set_structured_final_answer
from .run_context import RunContextWrapper
from .tracing import model_span, record_span_error

if TYPE_CHECKING:
    from .agent import Agent
    from .run_state import RunState


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


# 把“准备本轮模型输入”的职责集中到 model_turn.py。
def prepare_turn_input(
    agent: Agent,
    context_wrapper: RunContextWrapper | None = None,
    model_settings: ModelSettings | None = None,
) -> TurnInput:
    context_wrapper = context_wrapper or RunContextWrapper()
    turn_context = build_turn_context(agent, context_wrapper=context_wrapper)
    return TurnInput(
        messages=turn_context.to_messages(),
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
