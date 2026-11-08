from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from inspect import Parameter, iscoroutinefunction, signature
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from .contracts import ChatMessage, ModelRequest, ModelResponse, ToolCall, ToolSpec
from .model_settings import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ModelSettings,
)

if TYPE_CHECKING:
    from .model_events import ModelEvent


@runtime_checkable
class ModelAdapter(Protocol):
    """同步运行循环需要的最小模型调用边界。"""

    def get_response(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
    ) -> ModelResponse:
        """执行一次模型请求，并返回标准化的模型响应。"""


@runtime_checkable
class StreamingModelAdapter(Protocol):
    """原生模型流只需把一次请求转换成异步模型事件。"""

    def stream_response(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """返回模型事件异步迭代器，不负责聚合或运行循环。"""


def supports_streaming_model(model: Any) -> bool:
    """只判断模型是否声明了可调用的原生流入口。"""
    return callable(getattr(model, "stream_response", None))


def supports_model_adapter(model: Any) -> bool:
    """判断对象是否提供当前最小模型调用能力。"""
    return any(
        callable(getattr(model, method_name, None))
        for method_name in ("stream_response", "get_response", "decide")
    )


def format_model_error(exc: BaseException) -> str:
    if isinstance(exc, ModelCallError):
        return str(exc)
    if isinstance(exc, ModelResponseError):
        return f"{exc.__class__.__name__}: {exc}"
    error_type = exc.__class__.__name__
    error_message = str(exc) or "<no message>"
    return f"Model call failed during model_call: {error_type}: {error_message}"


class ModelErrorKind(str, Enum):
    TRANSPORT = "transport"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_WINDOW = "context_window"
    QUOTA = "quota"
    STREAM_UNAVAILABLE = "stream_unavailable"
    PROTOCOL = "protocol"
    EARLY_CLOSE = "early_close"
    BUFFER_OVERFLOW = "buffer_overflow"
    CANCELLED = "cancelled"


_HARD_QUOTA_ERROR_CODES = {
    "billing_hard_limit_reached",
    "billing_not_active",
    "insufficient_quota",
    "usage_limit_reached",
}
_CONTEXT_WINDOW_ERROR_CODES = {
    "context_length_exceeded",
    "context_window",
    "context_window_exceeded",
}
_STREAM_UNAVAILABLE_ERROR_CODES = {
    "stream_not_supported",
    "streaming_not_supported",
    "unsupported_stream",
}


class ModelResponseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: ModelErrorKind | None = None,
        status_code: int | None = None,
        error_code: str | None = None,
        retry_after: float | str | None = None,
        request_id: str | None = None,
        attempt_id: str | None = None,
        original: BaseException | None = None,
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after = retry_after
        self.request_id = request_id
        self.attempt_id = attempt_id
        self.original = original
        super().__init__(message)


class ModelResponseParseError(ModelResponseError):
    """模型响应结构或内容无法解析。"""

# 表示模型事件无法组成可信响应
class ModelProtocolError(ModelResponseParseError):
    def __init__(
        self,
        message: str,
        *,
        kind: ModelErrorKind = ModelErrorKind.PROTOCOL,
        attempt_id: str | None = None,
        original: BaseException | None = None,
        max_buffer_chars: int | None = None,
        attempted_buffer_chars: int | None = None,
    ) -> None:
        self.max_buffer_chars = max_buffer_chars
        self.attempted_buffer_chars = attempted_buffer_chars
        super().__init__(
            message,
            kind=kind,
            attempt_id=attempt_id,
            original=original,
        )


class ModelResponseStatusError(ModelResponseError):
    """模型响应状态表示本轮请求没有正常完成。"""


class ModelCallError(ModelResponseError):
    def __init__(self, original: BaseException) -> None:
        super().__init__(format_model_error(original), original=original)


def _header_value(headers: Any, name: str) -> Any:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        lowered_name = name.lower()
        for key, value in items():
            if str(key).lower() == lowered_name:
                return value
    return None


def _is_openai_connection_error(exc: BaseException) -> bool:
    try:
        from openai import APIConnectionError
    except ImportError:
        return False
    return isinstance(exc, APIConnectionError)


def _openai_error_kind(
    *,
    status_code: int | None,
    error_code: str | None,
    stream_open: bool,
    connection_error: bool,
) -> ModelErrorKind | None:
    if error_code in _HARD_QUOTA_ERROR_CODES:
        return ModelErrorKind.QUOTA
    if error_code in _CONTEXT_WINDOW_ERROR_CODES:
        return ModelErrorKind.CONTEXT_WINDOW
    if stream_open and (
        status_code == 404 or error_code in _STREAM_UNAVAILABLE_ERROR_CODES
    ):
        return ModelErrorKind.STREAM_UNAVAILABLE
    if connection_error or status_code in {408, 409}:
        return ModelErrorKind.TRANSPORT
    if status_code == 429:
        return ModelErrorKind.RATE_LIMIT
    if status_code is not None and 500 <= status_code <= 599:
        return ModelErrorKind.SERVER
    if status_code in {401, 403}:
        return ModelErrorKind.AUTHENTICATION
    if status_code is not None and 400 <= status_code <= 499:
        return ModelErrorKind.INVALID_REQUEST
    return None


def classify_openai_model_error(
    exc: BaseException,
    *,
    stream_open: bool = False,
) -> ModelResponseError:
    if isinstance(exc, ModelResponseError):
        return exc

    response = _get_field(exc, "response")
    status_code = next(
        (
            value
            for value in (
                _get_field(exc, "status_code"),
                _get_field(exc, "status"),
                _get_field(response, "status_code"),
                _get_field(response, "status"),
            )
            if type(value) is int
        ),
        None,
    )
    headers = _get_field(response, "headers")
    if headers is None:
        headers = _get_field(exc, "headers")

    body = _get_field(exc, "body")
    problem = _get_field(body, "error")
    if problem is None:
        problem = body
    error_code = _get_field(exc, "code")
    if not isinstance(error_code, str) or not error_code.strip():
        error_code = _get_field(problem, "code")
    if isinstance(error_code, str) and error_code.strip():
        error_code = error_code.strip().lower()
    else:
        error_code = None

    retry_after = _get_field(exc, "retry_after")
    if (
        isinstance(retry_after, bool)
        or not isinstance(retry_after, (int, float, str))
    ):
        retry_after = _header_value(headers, "retry-after")
    if type(retry_after) is int:
        retry_after = float(retry_after)

    request_id = next(
        (
            value
            for value in (
                _get_field(exc, "request_id"),
                _get_field(response, "request_id"),
                _header_value(headers, "x-request-id"),
            )
            if isinstance(value, str) and value.strip()
        ),
        None,
    )

    kind = _openai_error_kind(
        status_code=status_code,
        error_code=error_code,
        stream_open=stream_open,
        connection_error=(
            isinstance(exc, (TimeoutError, ConnectionError))
            or _is_openai_connection_error(exc)
        ),
    )
    message = str(exc).strip() or exc.__class__.__name__
    return ModelResponseError(
        message,
        kind=kind,
        status_code=status_code,
        error_code=error_code,
        retry_after=retry_after,
        request_id=request_id,
        original=exc,
    )


def classify_model_error(exc: BaseException) -> ModelResponseError:
    if isinstance(exc, ModelResponseError):
        return exc
    kind = (
        ModelErrorKind.TRANSPORT
        if isinstance(exc, (TimeoutError, ConnectionError))
        else None
    )
    message = str(exc).strip() or exc.__class__.__name__
    return ModelResponseError(message, kind=kind, original=exc)


@dataclass(frozen=True)
class ResponseStatePolicy:
    mode: Literal["manual_items", "previous_response_id"] = "previous_response_id"
    store: bool = True
    include: tuple[str, ...] = ()


def _accepts_model_settings(get_response: Any) -> bool:
    try:
        parameters = signature(get_response).parameters
    except (TypeError, ValueError):
        return True

    return "model_settings" in parameters or any(
        parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def call_model_response(
    model: Any,
    messages: list[ChatMessage],
    tool_specs: list[ToolSpec],
    model_settings: ModelSettings,
) -> ModelResponse:
    get_response = getattr(model, "get_response", None)
    if not callable(get_response):
        raise TypeError("Model does not provide a callable get_response().")

    if _accepts_model_settings(get_response):
        return get_response(
            messages,
            tool_specs,
            model_settings=model_settings,
        )
    return get_response(messages, tool_specs)


def _model_response_to_events(
    response: ModelResponse,
    request: ModelRequest,
) -> tuple[ModelEvent, ...]:
    """把完整同步响应展开成可重新聚合的标准模型事件。"""
    from .model_events import (
        RefusalDelta,
        RefusalDone,
        ResponseCompleted,
        ResponseCreated,
        TextDelta,
        TextDone,
        ToolArgumentsDelta,
        ToolArgumentsDone,
        ToolCallStarted,
        UsageUpdated,
    )

    response_id = response.response_id or request.request_id
    events: list[ModelEvent] = [
        ResponseCreated(
            attempt_id=request.attempt_id,
            sequence=1,
            response_id=response_id,
        )
    ]
    if response.output_text:
        events.append(
            TextDelta(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=0,
                content_index=0,
                delta=response.output_text,
            )
        )
        events.append(
            TextDone(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=0,
                content_index=0,
                text=response.output_text,
            )
        )
    if response.refusal:
        events.append(
            RefusalDelta(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=0,
                content_index=0,
                delta=response.refusal,
            )
        )
        events.append(
            RefusalDone(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=0,
                content_index=0,
                text=response.refusal,
            )
        )
    for output_index, tool_call in enumerate(response.tool_calls):
        arguments = json.dumps(tool_call.arguments)
        item_id = f"sync-tool-{output_index}"
        events.append(
            ToolCallStarted(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=output_index,
                item_id=item_id,
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
            )
        )
        events.append(
            ToolArgumentsDelta(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=output_index,
                item_id=item_id,
                delta=arguments,
            )
        )
        events.append(
            ToolArgumentsDone(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                output_index=output_index,
                item_id=item_id,
                arguments=arguments,
            )
        )
    if response.usage is not None:
        events.append(
            UsageUpdated(
                attempt_id=request.attempt_id,
                sequence=len(events) + 1,
                usage=response.usage,
            )
        )
    events.append(
        ResponseCompleted(
            attempt_id=request.attempt_id,
            sequence=len(events) + 1,
            response_id=response_id,
            output=response.output,
            raw=response.raw,
            request_summary=response.request_summary,
            request_id=response.request_id,
        )
    )
    return tuple(events)



async def stream_sync_model_response(
    model: Any,
    request: ModelRequest,
) -> AsyncIterator[ModelEvent]:
    """在线程中调用同步模型，再逐条产出标准模型事件。"""
    get_response = getattr(model, "get_response", None)
    if callable(get_response):
        response = await asyncio.to_thread(
            call_model_response,
            model,
            list(request.messages),
            list(request.tool_specs),
            request.model_settings,
        )
    else:
        decide = getattr(model, "decide", None)
        if not callable(decide):
            raise TypeError(
                "Model does not provide a callable get_response() or decide()."
            )
        tool_call = await asyncio.to_thread(
            decide,
            list(request.messages),
            list(request.tool_specs),
        )
        if tool_call is not None and not isinstance(tool_call, ToolCall):
            raise TypeError("decide() must return ToolCall or None.")
        response = ModelResponse(
            response_id=None,
            output=[],
            output_text=None,
            tool_calls=[] if tool_call is None else [tool_call],
        )

    for event in _model_response_to_events(response, request):
        yield event


async def run_non_stream_model_response(
    model: Any,
    request: ModelRequest,
) -> ModelResponse:
    """只调用一次同一模型的完整响应入口，不安排重试。"""
    get_response_for_request = getattr(model, "get_response_for_request", None)
    if callable(get_response_for_request):
        if iscoroutinefunction(get_response_for_request):
            response = await get_response_for_request(request)
        else:
            response = await asyncio.to_thread(get_response_for_request, request)
    else:
        response = await asyncio.to_thread(
            call_model_response,
            model,
            list(request.messages),
            list(request.tool_specs),
            request.model_settings,
        )
    if not isinstance(response, ModelResponse):
        raise TypeError("Model response must be a ModelResponse.")
    return response


async def stream_model_response(
    model: Any,
    request: ModelRequest,
) -> AsyncIterator[ModelEvent]:
    stream_response = getattr(model, "stream_response", None)
    if callable(stream_response):
        events = stream_response(request)
    else:
        events = stream_sync_model_response(model, request)

    try:
        async for event in events:
            yield event
    finally:
        close = getattr(events, "aclose", None)
        if callable(close):
            await close()


def _next_stream_item(iterator: Any) -> tuple[bool, Any | None]:
    """读取一条同步流事件，并把正常结束转换成普通返回值。"""
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


def _close_openai_stream(stream: Any) -> None:
    """关闭同步 OpenAI 流并释放底层连接。"""
    stream.close()


# 保存一条 OpenAI 函数调用从开始到结束所需的最少信息
@dataclass
class _OpenAIToolItemState:
    output_index: int
    call_id: str
    name: str
    arguments: str | None = None
    item_done: bool = False


@dataclass
# 记住同一次模型尝试中的两套序号，然后把每条 OpenAI 事件转换成零条或多条 MyAgent 事件
class _OpenAIEventMapper:
    """把 OpenAI 原始流事件转换成连续编号的 MyAgent 事件。"""

    attempt_id: str
    request_summary: dict[str, object] | None = None
    last_provider_sequence: int = 0
    next_sequence: int = 1
    ignored_event_count: int = 0
    _response_created: bool = field(default=False, init=False)
    _reasoning_positions: dict[tuple[int, str, int], int] = field(
        default_factory=dict
    )
    _tool_items: dict[str, _OpenAIToolItemState] = field(default_factory=dict)

    def _failure_details(
        self,
        problem: Any,
    ) -> tuple[ModelErrorKind, str, str | None]:
        message = _get_field(problem, "message")
        code = _get_field(problem, "code")
        if not isinstance(message, str) or not message.strip():
            raise ModelProtocolError(
                "OpenAI failure must contain a message.",
                attempt_id=self.attempt_id,
            )
        if code is not None and not isinstance(code, str):
            raise ModelProtocolError(
                "OpenAI failure code must be a string or None.",
                attempt_id=self.attempt_id,
            )
        normalized_code = (
            code.strip().lower()
            if isinstance(code, str) and code.strip()
            else None
        )
        kind = _openai_error_kind(
            status_code=None,
            error_code=normalized_code,
            stream_open=False,
            connection_error=False,
        )
        return kind or ModelErrorKind.SERVER, message, normalized_code

    def _tool_string(self, item: Any, field_name: str) -> str:
        value = _get_field(item, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ModelProtocolError(
                f"OpenAI function call {field_name} must be a non-empty string.",
                attempt_id=self.attempt_id,
            )
        return value

    # 读取工具调用在响应输出中的位置
    def _tool_output_index(self, raw_event: Any) -> int:
        output_index = _get_field(raw_event, "output_index")
        if type(output_index) is not int or output_index < 0:
            raise ModelProtocolError(
                "OpenAI function call output_index must be a non-negative integer.",
                attempt_id=self.attempt_id,
            )
        return output_index


    # 让参数分片找到前面登记过的工具调用
    def _started_tool(
        self,
        raw_event: Any,
    ) -> tuple[str, int, _OpenAIToolItemState]:
        item_id = self._tool_string(raw_event, "item_id")
        output_index = self._tool_output_index(raw_event)
        state = self._tool_items.get(item_id)
        if state is None:
            raise ModelProtocolError(
                "Function call arguments require response.output_item.added first.",
                attempt_id=self.attempt_id,
            )
        if state.output_index != output_index:
            raise ModelProtocolError(
                "Function call arguments changed output_index.",
                attempt_id=self.attempt_id,
            )
        return item_id, output_index, state


    # 把 OpenAI 的推理位置转换成 MyAgent 使用的唯一位置
    def _reasoning_content_index(
        self,
        output_index: int,
        content_kind: str,
        provider_index: int,
    ) -> int:
        key = (output_index, content_kind, provider_index)
        content_index = self._reasoning_positions.get(key)
        if content_index is None:
            content_index = sum(
                position[0] == output_index for position in self._reasoning_positions
            )
            self._reasoning_positions[key] = content_index
        return content_index


    # 将一堆 openai传来的原始数据 转换成本地event事件
    def map(self, raw_event: Any) -> tuple[ModelEvent, ...]:
        from .model_events import (
            ReasoningDelta,
            ReasoningDone,
            RefusalDelta,
            RefusalDone,
            ResponseCompleted,
            ResponseCreated,
            ResponseFailed,
            ResponseIncomplete,
            TextDelta,
            TextDone,
            ToolArgumentsDelta,
            ToolArgumentsDone,
            ToolCallStarted,
            UsageUpdated,
        )

        event_type = _get_field(raw_event, "type")
        provider_sequence = _get_field(raw_event, "sequence_number")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ModelProtocolError(
                "OpenAI stream event type must be a non-empty string.",
                attempt_id=self.attempt_id,
            )
        if type(provider_sequence) is not int or provider_sequence < 1:
            raise ModelProtocolError(
                "OpenAI stream sequence_number must be a positive integer.",
                attempt_id=self.attempt_id,
            )
        if provider_sequence <= self.last_provider_sequence:
            raise ModelProtocolError(
                "OpenAI stream sequence_number must increase.",
                attempt_id=self.attempt_id,
            )
        self.last_provider_sequence = provider_sequence

        if event_type == "response.created":
            response_id = _get_field(_get_field(raw_event, "response"), "id")
            if not isinstance(response_id, str) or not response_id.strip():
                raise ModelProtocolError(
                    "response.created must contain a response id.",
                    attempt_id=self.attempt_id,
                )
            event = ResponseCreated(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                response_id=response_id,
            )
            self._response_created = True
            self.next_sequence += 1
            return (event,)

        if event_type == "error":
            kind, message, error_code = self._failure_details(raw_event)
            if not self._response_created:
                raise ModelResponseError(
                    message,
                    kind=kind,
                    error_code=error_code,
                    attempt_id=self.attempt_id,
                )
            event = ResponseFailed(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                kind=kind,
                message=message,
            )
            self.next_sequence += 1
            return (event,)

        if event_type == "response.failed":
            response = _get_field(raw_event, "response")
            kind, message, _ = self._failure_details(_get_field(response, "error"))
            event = ResponseFailed(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                kind=kind,
                message=message,
                request_id=response_request_id(response),
            )
            self.next_sequence += 1
            return (event,)

        if event_type == "response.incomplete":
            response = _get_field(raw_event, "response")
            reason = _get_field(_get_field(response, "incomplete_details"), "reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ModelProtocolError(
                    "OpenAI incomplete response must contain a reason.",
                    attempt_id=self.attempt_id,
                )
            event = ResponseIncomplete(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                reason=reason,
                message="OpenAI response was incomplete.",
                request_id=response_request_id(response),
            )
            self.next_sequence += 1
            return (event,)


        # 确定一片文字应该写到哪里
        if event_type in (
            "response.output_text.delta",
            "response.output_text.done",
            "response.reasoning_text.delta",
            "response.reasoning_text.done",
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done",
            "response.refusal.delta",
            "response.refusal.done",
        ):
            output_index = _get_field(raw_event, "output_index")  # 表示第几个输出项
            is_summary = event_type.startswith("response.reasoning_summary_text.")
            provider_index_name = "summary_index" if is_summary else "content_index"
            provider_index = _get_field(raw_event, provider_index_name)
            if type(output_index) is not int or output_index < 0:
                raise ModelProtocolError(
                    "OpenAI content output_index must be a non-negative integer.",
                    attempt_id=self.attempt_id,
                )
            if type(provider_index) is not int or provider_index < 0:
                raise ModelProtocolError(
                    f"OpenAI content {provider_index_name} must be a non-negative integer.",
                    attempt_id=self.attempt_id,
                )

            content_index = provider_index
            if event_type.startswith("response.reasoning_"):
                content_index = self._reasoning_content_index(
                    output_index,
                    "summary" if is_summary else "text",
                    provider_index,
                )


            # 从不同事件里取出实际文字
            if event_type.endswith(".delta"):
                value_name = "delta"
            elif event_type == "response.refusal.done":
                value_name = "refusal"
            else:
                value_name = "text"
            value = _get_field(raw_event, value_name)
            if not isinstance(value, str):
                raise ModelProtocolError(
                    f"OpenAI content {value_name} must be a string.",
                    attempt_id=self.attempt_id,
                )


            # 转换不同类型的分片
            if event_type == "response.output_text.delta":
                event = TextDelta(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    output_index=output_index,
                    content_index=content_index,
                    delta=value,
                )
            elif event_type == "response.output_text.done":
                event = TextDone(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    output_index=output_index,
                    content_index=content_index,
                    text=value,
                )
            elif event_type in (
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
            ):
                event = ReasoningDelta(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    output_index=output_index,
                    content_index=content_index,
                    delta=value,
                )
            elif event_type in (
                "response.reasoning_text.done",
                "response.reasoning_summary_text.done",
            ):
                event = ReasoningDone(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    output_index=output_index,
                    content_index=content_index,
                    text=value,
                )
            elif event_type == "response.refusal.delta":
                event = RefusalDelta(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    output_index=output_index,
                    content_index=content_index,
                    delta=value,
                )
            else:
                event = RefusalDone(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    output_index=output_index,
                    content_index=content_index,
                    text=value,
                )
            self.next_sequence += 1
            return (event,)

        if event_type == "response.output_item.added":
            item = _get_field(raw_event, "item")
            if _get_field(item, "type") != "function_call":
                return ()
            output_index = self._tool_output_index(raw_event)
            item_id = self._tool_string(item, "id")
            call_id = self._tool_string(item, "call_id")
            name = self._tool_string(item, "name")
            if item_id in self._tool_items:
                raise ModelProtocolError(
                    "Function call item id must be unique.",
                    attempt_id=self.attempt_id,
                )
            if any(state.call_id == call_id for state in self._tool_items.values()):
                raise ModelProtocolError(
                    "Function call call_id must be unique.",
                    attempt_id=self.attempt_id,
                )
            if any(
                state.output_index == output_index
                for state in self._tool_items.values()
            ):
                raise ModelProtocolError(
                    "Function call output_index must be unique.",
                    attempt_id=self.attempt_id,
                )
            self._tool_items[item_id] = _OpenAIToolItemState(
                output_index=output_index,
                call_id=call_id,
                name=name,
            )
            event = ToolCallStarted(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                output_index=output_index,
                item_id=item_id,
                call_id=call_id,
                tool_name=name,
            )
            self.next_sequence += 1
            return (event,)

        if event_type == "response.function_call_arguments.delta":
            item_id, output_index, state = self._started_tool(raw_event)
            delta = _get_field(raw_event, "delta")
            if not isinstance(delta, str):
                raise ModelProtocolError(
                    "Function call argument delta must be a string.",
                    attempt_id=self.attempt_id,
                )
            if state.arguments is not None or state.item_done:
                raise ModelProtocolError(
                    "Cannot append function call arguments after completion.",
                    attempt_id=self.attempt_id,
                )
            event = ToolArgumentsDelta(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                output_index=output_index,
                item_id=item_id,
                delta=delta,
            )
            self.next_sequence += 1
            return (event,)

        if event_type == "response.function_call_arguments.done":
            item_id, output_index, state = self._started_tool(raw_event)
            name = self._tool_string(raw_event, "name")
            arguments = _get_field(raw_event, "arguments")
            if not isinstance(arguments, str):
                raise ModelProtocolError(
                    "Function call arguments must be a string.",
                    attempt_id=self.attempt_id,
                )
            if state.name != name:
                raise ModelProtocolError(
                    "Function call name changed before arguments completed.",
                    attempt_id=self.attempt_id,
                )
            if state.item_done:
                raise ModelProtocolError(
                    "Function call arguments completed after output item done.",
                    attempt_id=self.attempt_id,
                )
            if state.arguments is not None:
                if state.arguments == arguments:
                    return ()
                raise ModelProtocolError(
                    "Repeated function call arguments changed their value.",
                    attempt_id=self.attempt_id,
                )
            state.arguments = arguments
            event = ToolArgumentsDone(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                output_index=output_index,
                item_id=item_id,
                arguments=arguments,
            )
            self.next_sequence += 1
            return (event,)

        if event_type == "response.output_item.done":
            item = _get_field(raw_event, "item")
            if _get_field(item, "type") != "function_call":
                return ()
            output_index = self._tool_output_index(raw_event)
            item_id = self._tool_string(item, "id")
            state = self._tool_items.get(item_id)
            if state is None:
                raise ModelProtocolError(
                    "Function call output item completed before it started.",
                    attempt_id=self.attempt_id,
                )
            call_id = self._tool_string(item, "call_id")
            name = self._tool_string(item, "name")
            arguments = _get_field(item, "arguments")
            if not isinstance(arguments, str):
                raise ModelProtocolError(
                    "Completed function call arguments must be a string.",
                    attempt_id=self.attempt_id,
                )
            if (
                state.output_index != output_index
                or state.call_id != call_id
                or state.name != name
            ):
                raise ModelProtocolError(
                    "Completed function call identity does not match its start event.",
                    attempt_id=self.attempt_id,
                )
            if state.arguments is None:
                raise ModelProtocolError(
                    "Function call output item completed before arguments done.",
                    attempt_id=self.attempt_id,
                )
            if state.arguments != arguments:
                raise ModelProtocolError(
                    "Completed function call arguments changed their value.",
                    attempt_id=self.attempt_id,
                )
            state.item_done = True
            return ()

        if event_type == "response.completed" and any(
            state.arguments is None or not state.item_done
            for state in self._tool_items.values()
        ):
            raise ModelProtocolError(
                "Response completed before every function call item finished.",
                attempt_id=self.attempt_id,
            )

        if event_type != "response.completed":
            self.ignored_event_count += 1
            return ()

        response = _get_field(raw_event, "response")
        response_id = _get_field(response, "id")
        output = _get_field(response, "output")
        if not isinstance(response_id, str) or not response_id.strip():
            raise ModelProtocolError(
                "response.completed must contain a response id.",
                attempt_id=self.attempt_id,
            )
        if not isinstance(output, list | tuple):
            raise ModelProtocolError(
                "response.completed output must be a list.",
                attempt_id=self.attempt_id,
            )

        events: list[ModelEvent] = []
        usage = response_usage(response)
        if usage is not None:
            events.append(
                UsageUpdated(
                    attempt_id=self.attempt_id,
                    sequence=self.next_sequence,
                    usage=usage,
                )
            )
            self.next_sequence += 1
        events.append(
            ResponseCompleted(
                attempt_id=self.attempt_id,
                sequence=self.next_sequence,
                response_id=response_id,
                output=list(output),
                raw=response,
                request_summary=self.request_summary,
                request_id=response_request_id(response),
            )
        )
        self.next_sequence += 1
        return tuple(events)


# 把你自己定义的 ToolSpec 转换成 OpenAI Responses API 可识别的工具格式。
def tool_spec_to_openai_tool(tool_spec: ToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for argument in tool_spec.arguments:
        argument_schema = _argument_to_openai_schema(argument)
        argument_schema["description"] = argument.description

        properties[argument.name] = argument_schema
        if argument.required:
            required.append(argument.name)

    return {
        "type": "function",
        "name": tool_spec.name,
        "description": tool_spec.description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": True,
    }


def _argument_to_openai_schema(argument: Any) -> dict[str, Any]:
    return dict(argument.schema)

# ChatMessage 转换成 OpenAI Responses API 的 input 消息格式
def chat_message_to_response_input(message: ChatMessage) -> dict[str, str]:
    if message.role in {"system", "user", "assistant"}:
        return {"role": message.role, "content": message.content}
    if message.role == "tool_call":
        return {"role": "assistant", "content": message.content}
    return {"role": "user", "content": message.content}


def response_instructions_from_messages(messages: list[ChatMessage]) -> str | None:
    instructions = [
        message.content
        for message in messages
        if message.role == "system" and message.content
    ]
    if not instructions:
        return None
    return "\n\n".join(instructions)


def response_input_from_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
    return [
        chat_message_to_response_input(message)
        for message in messages
        if message.role != "system"
    ]


def response_state_store_enabled(
    state_policy: ResponseStatePolicy,
    model_settings: ModelSettings | None,
) -> bool:
    if model_settings is not None and model_settings.store is not None:
        return model_settings.store
    return state_policy.store


# 构造请求参数
def build_response_request_kwargs(
    *,
    model: str,
    messages: list[ChatMessage],
    tool_specs: list[ToolSpec],
    pending_tool_outputs: list[dict[str, str]],
    previous_response_id: str | None,
    state_policy: ResponseStatePolicy,
    response_schema: dict[str, Any] | None,
    response_schema_name: str,
    response_schema_strict: bool,
    model_settings: ModelSettings | None,
) -> dict[str, Any]:
    store_enabled = response_state_store_enabled(state_policy, model_settings)
    # 普通聊天依赖本地 history；工具结果续接只有在策略允许且服务端保存状态时才用 previous_response_id。
    use_previous_response_id = (
        state_policy.mode == "previous_response_id"
        and store_enabled
        and previous_response_id is not None
        and bool(pending_tool_outputs)
    )
    if use_previous_response_id:
        response_input: list[dict[str, Any]] = list(pending_tool_outputs)
    else:
        response_input = response_input_from_messages(messages)

    tools = [tool_spec_to_openai_tool(tool_spec) for tool_spec in tool_specs]
    request_kwargs: dict[str, Any] = {
        "model": model,
        "input": response_input,
        "tools": tools,
        "tool_choice": "auto",
    }

    instructions = response_instructions_from_messages(messages)
    if instructions is not None:
        request_kwargs["instructions"] = instructions
    if response_schema is not None:
        request_kwargs["text"] = response_schema_to_text_format(
            name=response_schema_name,
            schema=response_schema,
            strict=response_schema_strict,
        )
    _apply_model_settings(request_kwargs, model_settings)
    if "include" not in request_kwargs and state_policy.include:
        request_kwargs["include"] = list(state_policy.include)
    if not state_policy.store and "store" not in request_kwargs:
        request_kwargs["store"] = False

    if use_previous_response_id:
        request_kwargs["previous_response_id"] = previous_response_id
    return request_kwargs


def _get_field(item: Any, field_name: str) -> Any:
    if isinstance(item, dict):
        return item.get(field_name)
    field_value = getattr(item, field_name, None)
    if field_value is not None:
        return field_value

    model_dump = getattr(item, "model_dump", None)
    if not callable(model_dump):
        return None
    dumped = model_dump()
    if isinstance(dumped, dict):
        return dumped.get(field_name)
    return None


def _iter_items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, list | tuple):
        return tuple(value)
    return (value,)


def _iter_response_output(response: Any) -> tuple[Any, ...]:
    return _iter_items(_get_field(response, "output"))


def _iter_output_content(output_item: Any) -> tuple[Any, ...]:
    return _iter_items(_get_field(output_item, "content"))


def parse_tool_call_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ModelResponseParseError(
                f"Invalid tool call arguments JSON: {exc.msg}."
            ) from exc
    elif isinstance(arguments, dict):
        parsed_arguments = dict(arguments)
    else:
        raise ModelResponseParseError(
            "Tool call arguments must be a JSON object or JSON string."
        )

    if not isinstance(parsed_arguments, dict):
        raise ModelResponseParseError("Tool call arguments must be a JSON object.")
    return parsed_arguments


def response_item_type(item: Any) -> str | None:
    item_type = _get_field(item, "type")
    if isinstance(item_type, str) and item_type:
        return item_type
    return None


def _response_problem_text(problem: Any) -> str | None:
    if problem is None:
        return None
    if isinstance(problem, str):
        return problem
    for field_name in ("message", "reason", "code", "type"):
        field_value = _get_field(problem, field_name)
        if isinstance(field_value, str) and field_value:
            return field_value
    return str(problem)


def validate_response_status(response: Any) -> None:
    status = _get_field(response, "status")
    error_text = _response_problem_text(_get_field(response, "error"))
    incomplete_text = _response_problem_text(_get_field(response, "incomplete_details"))

    if status in (None, "completed") and error_text is None and incomplete_text is None:
        return

    details = [text for text in (error_text, incomplete_text) if text]
    detail_text = f": {'; '.join(details)}" if details else ""
    if isinstance(status, str) and status:
        raise ModelResponseStatusError(
            f"Model response status was {status!r}{detail_text}."
        )
    raise ModelResponseStatusError(f"Model response was not completed{detail_text}.")


def function_call_item_to_tool_call(item: Any) -> ToolCall:
    tool_name = _get_field(item, "name")
    if not isinstance(tool_name, str) or not tool_name:
        raise ModelResponseParseError("Function tool call is missing string name.")

    call_id = _get_field(item, "call_id")
    if not isinstance(call_id, str) or not call_id:
        raise ModelResponseParseError("Function tool call is missing string call_id.")

    try:
        parsed_arguments = parse_tool_call_arguments(_get_field(item, "arguments"))
    except ModelResponseParseError as exc:
        raise ModelResponseParseError(
            f"Function tool call {tool_name!r} has invalid arguments: {exc}"
        ) from exc

    return ToolCall(
        tool_name=tool_name,
        arguments=parsed_arguments,
        call_id=call_id,
    )


# 从模型返回的 output item 中解析出 ToolCall；如果不是 function_call 就返回 None。
def response_item_to_tool_call(item: Any) -> ToolCall | None:
    if response_item_type(item) != "function_call":
        return None
    return function_call_item_to_tool_call(item)


def response_items_to_tool_calls(items: list[Any]) -> list[ToolCall]:
    return [
        function_call_item_to_tool_call(item)
        for item in items
        if response_item_type(item) == "function_call"
    ]


def tool_call_response_call_id(tool_call: ToolCall) -> str:
    call_id = getattr(tool_call, "call_id", None)
    if not isinstance(call_id, str) or not call_id.strip():
        tool_name = getattr(tool_call, "tool_name", "<unknown>")
        raise ModelResponseParseError(
            f"Tool call {tool_name!r} is missing string call_id."
        )
    return call_id


# 具执行结果转换成 OpenAI Responses API 需要的 function_call_output 格式
def tool_call_output_to_response_input(tool_call: ToolCall, output: Any) -> dict[str, str]:
    call_id = tool_call_response_call_id(tool_call)
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": str(output),
    }


def response_schema_to_text_format(
    name: str,
    schema: dict[str, Any],
    strict: bool = True,
) -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "schema": schema,
            "strict": strict,
        }
    }


def _apply_model_settings(
    request_kwargs: dict[str, Any],
    model_settings: ModelSettings | None,
) -> None:
    if model_settings is None:
        return
    if model_settings.temperature is not None:
        request_kwargs["temperature"] = model_settings.temperature
    if model_settings.top_p is not None:
        request_kwargs["top_p"] = model_settings.top_p
    if model_settings.tool_choice is not None:
        request_kwargs["tool_choice"] = model_settings.tool_choice
    if model_settings.parallel_tool_calls is not None:
        request_kwargs["parallel_tool_calls"] = model_settings.parallel_tool_calls
    if model_settings.max_output_tokens is not None:
        request_kwargs["max_output_tokens"] = model_settings.max_output_tokens
    if model_settings.store is not None:
        request_kwargs["store"] = model_settings.store
    if model_settings.reasoning is not None:
        request_kwargs["reasoning"] = model_settings.reasoning
    if model_settings.verbosity is not None:
        text_format = request_kwargs.setdefault("text", {})
        text_format["verbosity"] = model_settings.verbosity
    if model_settings.response_include is not None:
        request_kwargs["include"] = list(model_settings.response_include)


# 从模型 response 中提取最终文本 output_text；如果没有直接字段，就从 output.content.text 里拼接。
def response_output_text(response: Any) -> str | None:
    output_text = _get_field(response, "output_text")
    if isinstance(output_text, str):
        return output_text

    text_parts: list[str] = []
    for output_item in _iter_response_output(response):
        for content_item in _iter_output_content(output_item):
            text = _get_field(content_item, "text")
            if isinstance(text, str):
                text_parts.append(text)

    if not text_parts:
        return None
    return "\n".join(text_parts)


def response_refusal_text(response: Any) -> str | None:
    for output_item in _iter_response_output(response):
        if _get_field(output_item, "type") != "message":
            continue
        for content_item in _iter_output_content(output_item):
            if _get_field(content_item, "type") != "refusal":
                continue
            refusal = _get_field(content_item, "refusal")
            if isinstance(refusal, str) and refusal:
                return refusal
    return None


def _metadata_to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _metadata_to_plain(item) for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return _metadata_to_plain(dumped)
    return value


def response_usage(response: Any) -> dict[str, Any] | None:
    usage = _get_field(response, "usage")
    if usage is None:
        return None
    usage_payload = _metadata_to_plain(usage)
    if isinstance(usage_payload, dict):
        return usage_payload

    usage_summary: dict[str, Any] = {}
    for field_name in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_tokens_details",
        "output_tokens_details",
    ):
        field_value = _get_field(usage, field_name)
        if field_value is not None:
            usage_summary[field_name] = _metadata_to_plain(field_value)
    return usage_summary or None


def response_request_id(response: Any) -> str | None:
    for field_name in ("_request_id", "request_id"):
        request_id = _get_field(response, field_name)
        if isinstance(request_id, str) and request_id:
            return request_id
    return None


def _count_request_items(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list | tuple):
        return len(value)
    return 1


def _request_summary(request_kwargs: dict[str, Any]) -> dict[str, Any]:
    text_format = request_kwargs.get("text")
    return {
        "model": request_kwargs.get("model"),
        "input_count": _count_request_items(request_kwargs.get("input")),
        "tool_count": len(request_kwargs.get("tools") or []),
        "has_schema": isinstance(text_format, dict) and "format" in text_format,
        "uses_previous_response_id": "previous_response_id" in request_kwargs,
        "has_instructions": "instructions" in request_kwargs,
        "store": request_kwargs.get("store"),
        "include_count": _count_request_items(request_kwargs.get("include")),
    }


def tool_spec_to_chat_completion_tool(tool_spec: ToolSpec) -> dict[str, Any]:
    """Return the common OpenAI-compatible function-tool shape."""
    function = tool_spec_to_openai_tool(tool_spec)
    function.pop("type", None)
    function.pop("strict", None)
    return {"type": "function", "function": function}


def _openai_timeout(model_settings: ModelSettings) -> Any:
    from openai import Timeout

    connect_timeout = (
        model_settings.connect_timeout
        if model_settings.connect_timeout is not None
        else DEFAULT_CONNECT_TIMEOUT
    )
    read_timeout = (
        model_settings.read_timeout
        if model_settings.read_timeout is not None
        else DEFAULT_READ_TIMEOUT
    )
    return Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )


def _model_response_from_openai(
    response: Any,
    request_summary: dict[str, Any],
) -> ModelResponse:
    validate_response_status(response)
    output = list(_iter_response_output(response))
    return ModelResponse(
        response_id=_get_field(response, "id"),
        output=output,
        output_text=response_output_text(response),
        tool_calls=response_items_to_tool_calls(output),
        refusal=response_refusal_text(response),
        raw=response,
        usage=response_usage(response),
        request_summary=dict(request_summary),
        request_id=response_request_id(response),
    )


@dataclass
class OpenAIResponsesModel:
    model: str = "gpt-5.4"
    client: Any | None = None
    response_schema: dict[str, Any] | None = None
    response_schema_name: str = "final_response"
    response_schema_strict: bool = True
    max_buffer_chars: int = 1_000_000
    last_response: Any | None = None
    last_input: list[dict[str, Any]] | None = None
    last_tools: list[dict[str, Any]] | None = None
    last_output_text: str | None = None
    last_request_summary: dict[str, Any] | None = None
    last_ignored_provider_event_count: int = 0
    previous_response_id: str | None = None
    pending_tool_outputs: list[dict[str, str]] = field(default_factory=list)
    state_policy: ResponseStatePolicy = field(default_factory=ResponseStatePolicy)

    def __post_init__(self) -> None:
        if type(self.max_buffer_chars) is not int or self.max_buffer_chars < 1:
            raise ValueError("max_buffer_chars must be a positive integer.")
        if self.client is not None:
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIResponsesModel needs the openai package. "
                "Install it before using the real API client."
            ) from exc

        self.client = OpenAI()

    def record_tool_output(self, tool_call: ToolCall, output: Any) -> None:
        self.pending_tool_outputs.append(
            tool_call_output_to_response_input(tool_call, output)
        )

    async def _open_response_stream(self, request: ModelRequest) -> Any:
        """根据一次模型请求创建 OpenAI 原始事件流。"""
        request_kwargs = build_response_request_kwargs(
            model=request.model_name,
            messages=list(request.messages),
            tool_specs=list(request.tool_specs),
            pending_tool_outputs=self.pending_tool_outputs,
            previous_response_id=(
                self.previous_response_id
                if request.model_name == self.model
                else None
            ),
            state_policy=self.state_policy,
            response_schema=self.response_schema,
            response_schema_name=self.response_schema_name,
            response_schema_strict=self.response_schema_strict,
            model_settings=request.model_settings,
        )
        request_kwargs["stream"] = True
        self.last_input = list(request_kwargs["input"])
        self.last_tools = list(request_kwargs["tools"])
        self.last_request_summary = _request_summary(request_kwargs)

        if self.client is None:
            raise RuntimeError("OpenAIResponsesModel client is not initialized.")

        # 创建一个max_retries=0的客户端副本  第一次请求失败后，不再额外发送第二次
        client = self.client.with_options(
            max_retries=0,
            timeout=_openai_timeout(request.model_settings),
        )
        # 向事件循环提交 client.responses.create
        return await asyncio.to_thread(client.responses.create, **request_kwargs)



    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """逐条验证并产出模型事件，完整成功后才提交响应状态。"""
        from .model_events import ModelEventAccumulator

        accumulator = ModelEventAccumulator(
            request.attempt_id,
            max_buffer_chars=(
                request.model_settings.max_stream_buffer_chars
                if request.model_settings.max_stream_buffer_chars is not None
                else self.max_buffer_chars
            ),
        )
        stream = None
        mapper = None
        self.last_ignored_provider_event_count = 0
        try:
            try:
                stream = await self._open_response_stream(request)
            except Exception as exc:
                classified = classify_openai_model_error(exc, stream_open=True)
                if classified is exc:
                    raise
                raise classified from exc
            mapper = _OpenAIEventMapper(
                attempt_id=request.attempt_id,
                request_summary=self.last_request_summary,
            )
            iterator = iter(stream)
            while True:
                try:
                    has_item, raw_event = await asyncio.to_thread(
                        _next_stream_item,
                        iterator,
                    )
                except Exception as exc:
                    classified = classify_openai_model_error(exc)
                    if classified is exc:
                        raise
                    raise classified from exc
                if not has_item:
                    break
                for event in mapper.map(raw_event):
                    accumulator.apply(event)
                    yield event

            response = accumulator.finish()
            self.last_response = response.raw
            self.last_output_text = response.output_text
            self.previous_response_id = (
                response.response_id
                if request.model_name == self.model
                else None
            )
            self.pending_tool_outputs.clear()
        except BaseException:
            accumulator.abort()
            if stream is not None:
                try:
                    await asyncio.to_thread(_close_openai_stream, stream)
                except BaseException:
                    pass
            raise
        else:
            await asyncio.to_thread(_close_openai_stream, stream)
        finally:
            if mapper is not None:
                self.last_ignored_provider_event_count = mapper.ignored_event_count

    async def get_response_for_request(self, request: ModelRequest) -> ModelResponse:
        """用完整请求做一次非流调用，成功后才提交响应状态。"""
        self.last_ignored_provider_event_count = 0
        request_kwargs = build_response_request_kwargs(
            model=request.model_name,
            messages=list(request.messages),
            tool_specs=list(request.tool_specs),
            pending_tool_outputs=self.pending_tool_outputs,
            previous_response_id=(
                self.previous_response_id
                if request.model_name == self.model
                else None
            ),
            state_policy=self.state_policy,
            response_schema=self.response_schema,
            response_schema_name=self.response_schema_name,
            response_schema_strict=self.response_schema_strict,
            model_settings=request.model_settings,
        )
        request_kwargs["stream"] = False
        self.last_input = list(request_kwargs["input"])
        self.last_tools = list(request_kwargs["tools"])
        self.last_request_summary = _request_summary(request_kwargs)

        if self.client is None:
            raise RuntimeError("OpenAIResponsesModel client is not initialized.")

        client = self.client.with_options(
            max_retries=0,
            timeout=_openai_timeout(request.model_settings),
        )
        try:
            response = await asyncio.to_thread(
                client.responses.create,
                **request_kwargs,
            )
        except Exception as exc:
            classified = classify_openai_model_error(exc)
            if classified is exc:
                raise
            raise classified from exc

        model_response = _model_response_from_openai(
            response,
            self.last_request_summary,
        )
        self.last_response = response
        self.last_output_text = model_response.output_text
        self.previous_response_id = (
            (model_response.response_id or self.previous_response_id)
            if request.model_name == self.model
            else None
        )
        self.pending_tool_outputs.clear()
        return model_response

    def decide(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
    ) -> ToolCall | None:
        model_response = self.get_response(messages, tool_specs)
        if model_response.tool_calls:
            return model_response.tool_calls[0]
        return None
    

    # 统一执行一次模型请求，并返回完整 ModelResponse
    def get_response(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
        model_settings: ModelSettings | None = None,
    ) -> ModelResponse:
        self.last_ignored_provider_event_count = 0
        request_kwargs = build_response_request_kwargs(
            model=self.model,
            messages=messages,
            tool_specs=tool_specs,
            pending_tool_outputs=self.pending_tool_outputs,
            previous_response_id=self.previous_response_id,
            state_policy=self.state_policy,
            response_schema=self.response_schema,
            response_schema_name=self.response_schema_name,
            response_schema_strict=self.response_schema_strict,
            model_settings=model_settings,
        )
        self.last_input = list(request_kwargs["input"])
        self.last_tools = list(request_kwargs["tools"])
        self.last_request_summary = _request_summary(request_kwargs)

        if self.client is None:
            raise RuntimeError("OpenAIResponsesModel client is not initialized.")

        response = self.client.responses.create(**request_kwargs)
        self.last_response = response
        model_response = _model_response_from_openai(
            response,
            self.last_request_summary,
        )
        self.last_output_text = model_response.output_text
        self.previous_response_id = (
            model_response.response_id or self.previous_response_id
        )
        self.pending_tool_outputs.clear()
        return model_response


@dataclass
class _ChatToolState:
    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    started: bool = False
    item_id: str = ""


def _chat_messages(
    messages: list[ChatMessage],
    tool_exchange: list[dict[str, Any]],
    response_schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [
        {"role": message.role, "content": message.content}
        for message in messages
        if message.role in {"system", "user", "assistant"}
    ]
    if response_schema is not None:
        schema_instruction = {
            "role": "system",
            "content": (
                "Return the final answer as one JSON object matching this JSON "
                "Schema. Do not wrap the JSON in Markdown:\n"
                + json.dumps(
                    response_schema,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        }
        insert_at = next(
            (
                index
                for index, message in enumerate(result)
                if message["role"] != "system"
            ),
            len(result),
        )
        result.insert(insert_at, schema_instruction)
    result.extend(dict(message) for message in tool_exchange)
    return result


def build_chat_completion_request_kwargs(
    *,
    model: str,
    messages: list[ChatMessage],
    tool_specs: list[ToolSpec],
    tool_exchange: list[dict[str, Any]],
    response_schema: dict[str, Any] | None,
    model_settings: ModelSettings,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": _chat_messages(messages, tool_exchange, response_schema),
    }
    if response_schema is not None:
        kwargs["response_format"] = {"type": "json_object"}
    if tool_specs:
        kwargs["tools"] = [
            tool_spec_to_chat_completion_tool(tool_spec)
            for tool_spec in tool_specs
        ]
        kwargs["tool_choice"] = model_settings.tool_choice or "auto"
    if model_settings.temperature is not None:
        kwargs["temperature"] = model_settings.temperature
    if model_settings.top_p is not None:
        kwargs["top_p"] = model_settings.top_p
    if model_settings.parallel_tool_calls is not None and tool_specs:
        kwargs["parallel_tool_calls"] = model_settings.parallel_tool_calls
    if model_settings.max_output_tokens is not None:
        kwargs["max_tokens"] = model_settings.max_output_tokens
    return kwargs


def _chat_usage(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    plain = _metadata_to_plain(value)
    if isinstance(plain, dict):
        return plain
    usage = {
        name: _metadata_to_plain(_get_field(value, name))
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_tokens_details",
            "completion_tokens_details",
        )
        if _get_field(value, name) is not None
    }
    return usage or None


def _chat_tool_call(value: Any) -> ToolCall:
    function = _get_field(value, "function")
    name = _get_field(function, "name")
    call_id = _get_field(value, "id")
    if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
        raise ModelResponseParseError("Chat completion tool call is incomplete.")
    return ToolCall(
        tool_name=name,
        call_id=call_id,
        arguments=parse_tool_call_arguments(_get_field(function, "arguments")),
    )


@dataclass
class OpenAIChatCompletionsModel:
    """OpenAI-compatible Chat Completions adapter for third-party providers."""

    model: str
    api_key: str
    base_url: str
    client: Any | None = None
    response_schema: dict[str, Any] | None = None
    response_schema_name: str = "final_response"
    response_schema_strict: bool = True
    max_buffer_chars: int = 1_000_000
    _tool_exchange: list[dict[str, Any]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string.")
        if not isinstance(self.api_key, str) or not self.api_key:
            raise ValueError("api_key must be a non-empty string.")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must be a non-empty string.")
        if type(self.max_buffer_chars) is not int or self.max_buffer_chars < 1:
            raise ValueError("max_buffer_chars must be a positive integer.")
        if self.client is None:
            from openai import OpenAI

            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=0,
            )

    def record_tool_output(self, tool_call: ToolCall, output: Any) -> None:
        self._tool_exchange.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.call_id,
                "content": str(output),
            }
        )

    def restore_pending_tool_calls(
        self,
        tool_calls: Iterable[ToolCall],
    ) -> None:
        existing_call_ids = {
            raw_call.get("id")
            for message in self._tool_exchange
            if message.get("role") == "assistant"
            for raw_call in message.get("tool_calls", ())
            if isinstance(raw_call, dict)
        }
        missing_calls = [
            tool_call
            for tool_call in tool_calls
            if tool_call.call_id not in existing_call_ids
        ]
        if missing_calls:
            self._commit_tool_calls(None, missing_calls)

    def export_state(self) -> dict[str, Any]:
        return {"tool_exchange": deepcopy(self._tool_exchange)}

    def restore_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise TypeError("Chat completion model state must be a mapping.")
        tool_exchange = state.get("tool_exchange", [])
        if not isinstance(tool_exchange, list) or not all(
            isinstance(message, Mapping) for message in tool_exchange
        ):
            raise ValueError("Chat completion tool exchange is invalid.")
        self._tool_exchange = deepcopy(
            [dict(message) for message in tool_exchange]
        )

    def _kwargs(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        kwargs = build_chat_completion_request_kwargs(
            model=request.model_name,
            messages=list(request.messages),
            tool_specs=list(request.tool_specs),
            tool_exchange=self._tool_exchange,
            response_schema=self.response_schema,
            model_settings=request.model_settings,
        )
        kwargs["stream"] = stream
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    def _commit_tool_calls(
        self,
        output_text: str | None,
        tool_calls: list[ToolCall],
        reasoning_content: str | None = None,
    ) -> None:
        if not tool_calls:
            self._tool_exchange.clear()
            return
        message: dict[str, Any] = {
            "role": "assistant",
            "content": output_text,
            "tool_calls": [
                    {
                        "id": tool_call.call_id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": json.dumps(
                                tool_call.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for tool_call in tool_calls
                ],
        }
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        self._tool_exchange.append(message)

    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        from .model_events import (
            ModelEventAccumulator,
            ResponseCompleted,
            ResponseCreated,
            ResponseIncomplete,
            TextDelta,
            TextDone,
            ToolArgumentsDelta,
            ToolArgumentsDone,
            ToolCallStarted,
            UsageUpdated,
        )

        if self.client is None:
            raise RuntimeError("OpenAIChatCompletionsModel client is not initialized.")
        accumulator = ModelEventAccumulator(
            request.attempt_id,
            max_buffer_chars=(
                request.model_settings.max_stream_buffer_chars
                or self.max_buffer_chars
            ),
        )
        client = self.client.with_options(
            max_retries=0,
            timeout=_openai_timeout(request.model_settings),
        )
        stream = None
        sequence = 1
        response_id = f"chat-{request.attempt_id}"
        text = ""
        reasoning_content = ""
        tools: dict[int, _ChatToolState] = {}
        usage: dict[str, Any] | None = None
        finish_reason: str | None = None

        def emit(event: ModelEvent) -> ModelEvent:
            accumulator.apply(event)
            return event

        try:
            try:
                stream = await asyncio.to_thread(
                    client.chat.completions.create,
                    **self._kwargs(request, stream=True),
                )
            except Exception as exc:
                classified = classify_openai_model_error(exc, stream_open=True)
                if classified is exc:
                    raise
                raise classified from exc
            yield emit(
                ResponseCreated(
                    attempt_id=request.attempt_id,
                    sequence=sequence,
                    response_id=response_id,
                )
            )
            sequence += 1
            iterator = iter(stream)
            while True:
                try:
                    has_item, chunk = await asyncio.to_thread(_next_stream_item, iterator)
                except Exception as exc:
                    classified = classify_openai_model_error(exc)
                    if classified is exc:
                        raise
                    raise classified from exc
                if not has_item:
                    break
                chunk_usage = _chat_usage(_get_field(chunk, "usage"))
                if chunk_usage is not None:
                    usage = chunk_usage
                choices = _iter_items(_get_field(chunk, "choices"))
                if not choices:
                    continue
                choice = choices[0]
                finish = _get_field(choice, "finish_reason")
                if isinstance(finish, str):
                    finish_reason = finish
                delta = _get_field(choice, "delta")
                reasoning_delta = _get_field(delta, "reasoning_content")
                if isinstance(reasoning_delta, str):
                    reasoning_content += reasoning_delta
                content = _get_field(delta, "content")
                if isinstance(content, str) and content:
                    text += content
                    yield emit(
                        TextDelta(
                            attempt_id=request.attempt_id,
                            sequence=sequence,
                            output_index=0,
                            content_index=0,
                            delta=content,
                        )
                    )
                    sequence += 1
                for raw_tool in _iter_items(_get_field(delta, "tool_calls")):
                    index = _get_field(raw_tool, "index")
                    if type(index) is not int or index < 0:
                        raise ModelProtocolError("Chat tool call index is invalid.")
                    state = tools.setdefault(index, _ChatToolState(index=index))
                    call_id = _get_field(raw_tool, "id")
                    function = _get_field(raw_tool, "function")
                    name = _get_field(function, "name")
                    arguments = _get_field(function, "arguments")
                    if isinstance(call_id, str):
                        state.call_id += call_id
                    if isinstance(name, str):
                        state.name += name
                    if isinstance(arguments, str):
                        state.arguments += arguments
                    if not state.started and state.call_id and state.name:
                        state.started = True
                        state.item_id = f"chat-tool-{index}"
                        yield emit(
                            ToolCallStarted(
                                attempt_id=request.attempt_id,
                                sequence=sequence,
                                output_index=index,
                                item_id=state.item_id,
                                call_id=state.call_id,
                                tool_name=state.name,
                            )
                        )
                        sequence += 1
                        if state.arguments:
                            yield emit(
                                ToolArgumentsDelta(
                                    attempt_id=request.attempt_id,
                                    sequence=sequence,
                                    output_index=index,
                                    item_id=state.item_id,
                                    delta=state.arguments,
                                )
                            )
                            sequence += 1
                    elif state.started and isinstance(arguments, str) and arguments:
                        yield emit(
                            ToolArgumentsDelta(
                                attempt_id=request.attempt_id,
                                sequence=sequence,
                                output_index=index,
                                item_id=state.item_id,
                                delta=arguments,
                            )
                        )
                        sequence += 1
            if text:
                yield emit(
                    TextDone(
                        attempt_id=request.attempt_id,
                        sequence=sequence,
                        output_index=0,
                        content_index=0,
                        text=text,
                    )
                )
                sequence += 1
            for index in sorted(tools):
                state = tools[index]
                if not state.started:
                    raise ModelProtocolError("Chat tool call is incomplete.")
                yield emit(
                    ToolArgumentsDone(
                        attempt_id=request.attempt_id,
                        sequence=sequence,
                        output_index=index,
                        item_id=state.item_id,
                        arguments=state.arguments,
                    )
                )
                sequence += 1
            if usage is not None:
                yield emit(
                    UsageUpdated(
                        attempt_id=request.attempt_id,
                        sequence=sequence,
                        usage=usage,
                    )
                )
                sequence += 1
            terminal: ModelEvent
            if finish_reason in {"length", "content_filter"}:
                terminal = ResponseIncomplete(
                    attempt_id=request.attempt_id,
                    sequence=sequence,
                    reason=finish_reason,
                    message="Chat completion did not finish normally.",
                )
            else:
                terminal = ResponseCompleted(
                    attempt_id=request.attempt_id,
                    sequence=sequence,
                    response_id=response_id,
                    output=[],
                    request_summary={
                        "model": request.model_name,
                        "message_count": len(request.messages),
                        "tool_count": len(request.tool_specs),
                        "protocol": "chat_completions",
                    },
                )
            yield emit(terminal)
            response = accumulator.finish()
            self._commit_tool_calls(
                response.output_text,
                response.tool_calls,
                reasoning_content or None,
            )
        except BaseException:
            accumulator.abort()
            raise
        finally:
            if stream is not None:
                try:
                    await asyncio.to_thread(_close_openai_stream, stream)
                except BaseException:
                    pass

    async def get_response_for_request(self, request: ModelRequest) -> ModelResponse:
        if self.client is None:
            raise RuntimeError("OpenAIChatCompletionsModel client is not initialized.")
        client = self.client.with_options(
            max_retries=0,
            timeout=_openai_timeout(request.model_settings),
        )
        try:
            completion = await asyncio.to_thread(
                client.chat.completions.create,
                **self._kwargs(request, stream=False),
            )
        except Exception as exc:
            classified = classify_openai_model_error(exc)
            if classified is exc:
                raise
            raise classified from exc
        choices = _iter_items(_get_field(completion, "choices"))
        if not choices:
            raise ModelResponseParseError("Chat completion returned no choices.")
        message = _get_field(choices[0], "message")
        content = _get_field(message, "content")
        reasoning_content = _get_field(message, "reasoning_content")
        output_text = content if isinstance(content, str) and content else None
        tool_calls = [
            _chat_tool_call(item)
            for item in _iter_items(_get_field(message, "tool_calls"))
        ]
        response = ModelResponse(
            response_id=_get_field(completion, "id") or request.request_id,
            output=[],
            output_text=output_text,
            tool_calls=tool_calls,
            raw=completion,
            usage=_chat_usage(_get_field(completion, "usage")),
            request_summary={
                "model": request.model_name,
                "message_count": len(request.messages),
                "tool_count": len(request.tool_specs),
                "protocol": "chat_completions",
            },
            request_id=response_request_id(completion),
        )
        self._commit_tool_calls(
            response.output_text,
            response.tool_calls,
            reasoning_content if isinstance(reasoning_content, str) else None,
        )
        return response

    def get_response(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
        model_settings: ModelSettings | None = None,
    ) -> ModelResponse:
        request = ModelRequest(
            model_name=self.model,
            messages=tuple(messages),
            tool_specs=tuple(tool_specs),
            model_settings=model_settings or ModelSettings(),
            request_id="sync-chat-request",
            attempt_id="sync-chat-request:attempt:1",
        )
        return asyncio.run(self.get_response_for_request(request))
