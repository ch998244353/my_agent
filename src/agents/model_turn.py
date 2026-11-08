# 把“每一轮模型输入准备”和“执行一次模型调用”
# 的逻辑集中到 model_turn.py。

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .context_chunks import build_turn_context
from .contracts import (
    ChatMessage,
    ModelRequest,
    ModelResponse,
    RunItem,
    ToolCall,
    ToolSpec,
)
from .events import (
    ModelAttemptStartedEvent,
    ModelCompletedEvent,
    ModelFallbackEvent,
    ModelRetryScheduledEvent,
    ModelStartedEvent,
    ModelTextDeltaEvent,
    RunCancelledError,
)
from .lifecycle import LifecycleHookSequence, emit_llm_end, emit_llm_start
from .model_retry import (
    ModelAttemptRecord,
    ModelAttemptNotice,
    ModelCancelledError,
    ModelExecution,
    ModelExecutionError,
    run_model_request,
)
from .model_settings import ModelSettings
from .models import ModelCallError
from .model_events import ModelEvent, TextDelta
from .output import set_structured_final_answer
from .run_context import RunContextWrapper
from .run_control import CancellationToken, _LoopEventBridge
from .tracing import NoOpSpan, Span, model_span, record_span_error

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
    execution: ModelExecution | None = None


def _model_name_for_request(model: object) -> str:
    model_name = getattr(model, "model", None)
    if isinstance(model_name, str) and model_name.strip():
        return model_name
    return model.__class__.__name__


def _record_model_execution_span(
    span_context: Span | NoOpSpan,
    *,
    attempts: tuple[ModelAttemptRecord, ...],
    terminal_reason: str,
    response: ModelResponse | None = None,
) -> None:
    final_attempt = attempts[-1] if attempts else None
    retry_delays = [
        attempt.retry_delay_seconds
        for attempt in attempts
        if attempt.retry_delay_seconds is not None
    ]
    fallback = next(
        (
            (previous, current)
            for previous, current in zip(attempts, attempts[1:])
            if previous.reason in ("non_stream_fallback", "model_fallback")
            and (
                previous.model_name != current.model_name
                or previous.mode != current.mode
            )
        ),
        None,
    )
    fallback_reason = fallback[0].reason if fallback else None
    span_context.record.span_data.data.update(
        {
            "request_id": final_attempt.request_id if final_attempt else None,
            "final_attempt_id": final_attempt.attempt_id if final_attempt else None,
            "attempt_count": len(attempts),
            "retry_count": len(retry_delays),
            "final_model": final_attempt.model_name if final_attempt else None,
            "final_mode": final_attempt.mode if final_attempt else None,
            "response_id": response.response_id if response else None,
            "usage": (
                dict(response.usage)
                if response is not None and response.usage is not None
                else None
            ),
            "ttft_seconds": final_attempt.ttft_seconds if final_attempt else None,
            "attempt_duration_seconds": [
                attempt.duration_seconds for attempt in attempts
            ],
            "retry_delay_seconds": retry_delays,
            "non_stream_fallback_used": fallback_reason == "non_stream_fallback",
            "model_fallback_used": fallback_reason == "model_fallback",
            "fallback_reason": fallback_reason,
            "fallback_attempt_id": fallback[1].attempt_id if fallback else None,
            "fallback_from_model": fallback[0].model_name if fallback else None,
            "fallback_to_model": fallback[1].model_name if fallback else None,
            "fallback_from_mode": fallback[0].mode if fallback else None,
            "fallback_to_mode": fallback[1].mode if fallback else None,
            "terminal_reason": terminal_reason,
        }
    )


# 当一次真实模型发送开始时，这段代码向 循环事件 发出 ModelAttemptStartedEvent
def _emit_model_attempt_notice(
    notice: ModelAttemptNotice,
    bridge: _LoopEventBridge,
    *,
    turn_id: str | None,
    step_number: int,
) -> None:
    if notice.notice_type == "attempt_started":
        bridge.emit(
            ModelAttemptStartedEvent,
            turn_id=turn_id,
            step_number=step_number,
            request_id=notice.request_id,
            attempt_id=notice.attempt_id,
            attempt_number=notice.attempt_number,
            model_name=notice.model_name,
            mode=notice.mode,
            reason=notice.reason,
        )
        return
    if notice.notice_type == "retry_scheduled":
        assert notice.error_kind is not None
        assert notice.delay_seconds is not None
        assert notice.reason is not None
        bridge.emit(
            ModelRetryScheduledEvent,
            turn_id=turn_id,
            step_number=step_number,
            request_id=notice.request_id,
            attempt_id=notice.attempt_id,
            attempt_number=notice.attempt_number,
            model_name=notice.model_name,
            mode=notice.mode,
            error_kind=notice.error_kind.value,
            delay_seconds=notice.delay_seconds,
            reason=notice.reason,
        )
        return

    assert notice.previous_model_name is not None
    assert notice.previous_mode is not None
    assert notice.reason is not None
    bridge.emit(
        ModelFallbackEvent,
        turn_id=turn_id,
        step_number=step_number,
        request_id=notice.request_id,
        attempt_id=notice.attempt_id,
        attempt_number=notice.attempt_number,
        from_model=notice.previous_model_name,
        to_model=notice.model_name,
        reason=notice.reason,
        from_mode=notice.previous_mode,
        to_mode=notice.mode,
    )


# 把一次 Agent 回合变成统一模型请求
async def _run_model_turn_async(
    agent: Agent,
    turn_input: TurnInput,
    *,
    cancellation_token: CancellationToken | None,
    on_notice: Callable[[ModelAttemptNotice], None] | None,
    on_event: Callable[[ModelEvent], None] | None,
) -> ModelExecution:
    request_id = f"request-{uuid4().hex}"
    request = ModelRequest(
        model_name=_model_name_for_request(agent.model),
        messages=tuple(turn_input.messages),
        tool_specs=tuple(turn_input.tool_specs),
        model_settings=turn_input.model_settings,
        request_id=request_id,
        attempt_id=f"{request_id}:attempt:1",
    )
    return await run_model_request(
        agent.model,
        request,
        cancellation_token=cancellation_token,
        on_notice=on_notice,
        on_event=on_event,
    )


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
    *,
    _event_bridge: _LoopEventBridge | None = None,
    turn_id: str | None = None,
) -> ModelTurnResult:
    model_name = _model_name_for_request(agent.model)
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
            result = _run_model_turn_impl(
                agent,
                turn_input,
                run_state,
                step_number,
                hooks,
                _event_bridge=_event_bridge,
                turn_id=turn_id,
            )
        except RunCancelledError as exc:
            attempts = (
                exc.attempts if isinstance(exc, ModelCancelledError) else ()
            )
            run_state.record_model_cancelled(attempts)
            _record_model_execution_span(
                model_span_ctx,
                attempts=attempts,
                terminal_reason="cancelled",
            )
            raise
        except Exception as exc:
            if isinstance(exc, ModelExecutionError):
                run_state.record_model_execution_error(exc)
                assert run_state.last_model_terminal_reason is not None
                _record_model_execution_span(
                    model_span_ctx,
                    attempts=exc.attempts,
                    terminal_reason=run_state.last_model_terminal_reason,
                )
            if isinstance(exc, ModelCallError):
                record_span_error(model_span_ctx, exc)
                raise
            model_error = ModelCallError(exc)
            record_span_error(model_span_ctx, model_error)
            raise model_error from exc
        execution = result.execution
        assert execution is not None
        run_state.record_model_execution(execution)
        assert run_state.last_model_terminal_reason is not None
        _record_model_execution_span(
            model_span_ctx,
            attempts=execution.attempts,
            terminal_reason=run_state.last_model_terminal_reason,
            response=execution.response,
        )
        return result


def _run_model_turn_impl(
    agent: Agent,
    turn_input: TurnInput,
    run_state: RunState,
    step_number: int,
    hooks: LifecycleHookSequence = (),
    *,
    _event_bridge: _LoopEventBridge | None = None,
    turn_id: str | None = None,
) -> ModelTurnResult:
    if _event_bridge is not None:
        _event_bridge.raise_if_cancelled()
    emit_llm_start(hooks, run_state.context_wrapper, agent, turn_input)
    if _event_bridge is not None:
        _event_bridge.raise_if_cancelled()
        _event_bridge.emit(
            ModelStartedEvent,
            turn_id=turn_id,
            step_number=step_number,
            model_name=_model_name_for_request(agent.model),
        )
    legacy_decide_only = (
        not callable(getattr(agent.model, "stream_response", None))
        and not callable(getattr(agent.model, "get_response", None))
        and callable(getattr(agent.model, "decide", None))
    )
    on_notice = None
    on_event = None
    cancellation_token = None
    if _event_bridge is not None:
        cancellation_token = _event_bridge.cancellation_token
        on_notice = lambda notice: _emit_model_attempt_notice(
            notice,
            _event_bridge,
            turn_id=turn_id,
            step_number=step_number,
        )
        on_event = lambda event: (
            _event_bridge.emit(
                ModelTextDeltaEvent,
                turn_id=turn_id,
                step_number=step_number,
                attempt_id=event.attempt_id,
                delta=event.delta,
            )
            if isinstance(event, TextDelta) and event.delta
            else None
        )
    execution = asyncio.run(
        _run_model_turn_async(
            agent,
            turn_input,
            cancellation_token=cancellation_token,
            on_notice=on_notice,
            on_event=on_event,
        )
    )
    if _event_bridge is not None:
        try:
            _event_bridge.raise_if_cancelled()
        except RunCancelledError as exc:
            raise ModelCancelledError(
                exc.run_id,
                exc.reason,
                execution.attempts,
            ) from exc

    model_response = execution.response
    if legacy_decide_only:
        emit_llm_end(hooks, run_state.context_wrapper, agent, None)
        tool_calls = list(model_response.tool_calls)
        if _event_bridge is not None:
            _event_bridge.emit(
                ModelCompletedEvent,
                turn_id=turn_id,
                step_number=step_number,
                response_id=None,
                tool_call_count=len(tool_calls),
            )
        return ModelTurnResult(
            response=None,
            tool_calls=tool_calls,
            execution=execution,
        )

    emit_llm_end(hooks, run_state.context_wrapper, agent, model_response)
    item = RunItem(
        item_type="model_response",
        step_number=step_number,
        payload=model_response,
    )
    run_state.new_items.append(item)
    if _event_bridge is not None:
        _event_bridge.emit_item(item, turn_id=turn_id)
    set_structured_final_answer(
        model_response,
        agent.output_type,
        run_state,
        step_number,
    )
    return ModelTurnResult(
        response=model_response,
        tool_calls=list(model_response.tool_calls),
        execution=execution,
    )
