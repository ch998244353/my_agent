from __future__ import annotations

import json
from dataclasses import dataclass

from .contracts import ModelResponse, ToolCall
from .models import ModelErrorKind, ModelProtocolError, ModelResponseError


'''
一次 attempt 最多产生一个 response
一个 response 会产生多条 event
每条 event 用 sequence 排序
'''

# 模型流里的每一条数据，都必须带上“属于哪次尝试”和“这是第几条”
@dataclass(frozen=True, kw_only=True)
class ModelEvent:
    attempt_id: str  # 本次 attempt 产生的第几条事件 
    sequence: int  # 表示事件在当前 attempt 中的位置

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty string.")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be an integer greater than or equal to 1.")


#  模型服务已经为这次调用建立了一个“响应对象”，接下来这个响应会不断产生内容
@dataclass(frozen=True, kw_only=True)
class ResponseCreated(ModelEvent):  
    response_id: str  # 模型供应商为“这一次具体生成的回复”创建的服务端编号
    '''
    通常可以用于：记录日志,定位某次服务端响应,查询某个响应,取消某个响应,基于某个响应继续对话 
    但后面这些功能是否支持，要看具体供应商
    '''

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.response_id, str) or not self.response_id.strip():
            raise ValueError("response_id must be a non-empty string.")


# 代码拦住无法定位或无法拼接的内容事件
def _validate_content_fields(
    output_index: int,
    content_index: int,
    value: str,
    *,
    value_name: str,
) -> None:
    if type(output_index) is not int or output_index < 0:
        raise ValueError("output_index must be a non-negative integer.")
    if type(content_index) is not int or content_index < 0:
        raise ValueError("content_index must be a non-negative integer.")
    if not isinstance(value, str):
        raise ValueError(f"{value_name} must be a string.")


# 一小段文字
@dataclass(frozen=True, kw_only=True)
class TextDelta(ModelEvent):
    output_index: int
    content_index: int
    delta: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_content_fields(
            self.output_index,
            self.content_index,
            self.delta,
            value_name="delta",
        )

# 告诉累加器某个普通正文块已经结束
@dataclass(frozen=True, kw_only=True)
class TextDone(ModelEvent):
    output_index: int
    content_index: int
    text: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_content_fields(
            self.output_index,
            self.content_index,
            self.text,
            value_name="text",
        )

# 模型又生成了一小段思考内容
@dataclass(frozen=True, kw_only=True)
class ReasoningDelta(ModelEvent):
    output_index: int
    content_index: int
    delta: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_content_fields(
            self.output_index,
            self.content_index,
            self.delta,
            value_name="delta",
        )
 

@dataclass(frozen=True, kw_only=True)
class ReasoningDone(ModelEvent):
    output_index: int
    content_index: int
    text: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_content_fields(
            self.output_index,
            self.content_index,
            self.text,
            value_name="text",
        )

# 型正在分片生成拒绝回答，例如“抱歉”和“我不能帮助完成该请求”
@dataclass(frozen=True, kw_only=True)
class RefusalDelta(ModelEvent):
    output_index: int
    content_index: int
    delta: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_content_fields(
            self.output_index,
            self.content_index,
            self.delta,
            value_name="delta",
        )


@dataclass(frozen=True, kw_only=True)
class RefusalDone(ModelEvent):
    output_index: int
    content_index: int
    text: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_content_fields(
            self.output_index,
            self.content_index,
            self.text,
            value_name="text",
        )


def _validate_tool_position(output_index: int, item_id: str) -> None:
    if type(output_index) is not int or output_index < 0:
        raise ValueError("output_index must be a non-negative integer.")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("item_id must be a non-empty string.")

# 表示模型准备调用一个工具，但此时还没有完整参数
@dataclass(frozen=True, kw_only=True)
class ToolCallStarted(ModelEvent):
    output_index: int
    item_id: str
    call_id: str
    tool_name: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_tool_position(self.output_index, self.item_id)
        for field_name in ("call_id", "tool_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string.")


# 工具参数分片
@dataclass(frozen=True, kw_only=True)
class ToolArgumentsDelta(ModelEvent):
    output_index: int
    item_id: str
    delta: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_tool_position(self.output_index, self.item_id)
        if not isinstance(self.delta, str):
            raise ValueError("delta must be a string.")

 
@dataclass(frozen=True, kw_only=True)
class ToolArgumentsDone(ModelEvent):
    output_index: int
    item_id: str
    arguments: str

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_tool_position(self.output_index, self.item_id)
        if not isinstance(self.arguments, str):
            raise ValueError("arguments must be a string.")


def _validate_optional_request_id(request_id: str | None) -> None:
    if request_id is not None and (
        not isinstance(request_id, str) or not request_id.strip()
    ):
        raise ValueError("request_id must be None or a non-empty string.")

# 保存模型报告的用量快照，例如输入、输出和总 token 数
@dataclass(frozen=True, kw_only=True)
class UsageUpdated(ModelEvent):
    usage: dict[str, object]

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.usage, dict):
            raise ValueError("usage must be a dict.")
        object.__setattr__(self, "usage", dict(self.usage))


# 成功终态事件
@dataclass(frozen=True, kw_only=True)
class ResponseCompleted(ModelEvent):
    response_id: str
    output: list[object]
    raw: object | None = None
    request_summary: dict[str, object] | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.response_id, str) or not self.response_id.strip():
            raise ValueError("response_id must be a non-empty string.")
        if not isinstance(self.output, list):
            raise ValueError("output must be a list.")
        if self.request_summary is not None and not isinstance(
            self.request_summary, dict
        ):
            raise ValueError("request_summary must be None or a dict.")
        _validate_optional_request_id(self.request_id)
        object.__setattr__(self, "output", list(self.output))
        if self.request_summary is not None:
            object.__setattr__(self, "request_summary", dict(self.request_summary))


@dataclass(frozen=True, kw_only=True)
class ResponseFailed(ModelEvent):
    kind: ModelErrorKind
    message: str
    status_code: int | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.kind, ModelErrorKind):
            raise ValueError("kind must be a ModelErrorKind.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")
        if self.status_code is not None and (
            type(self.status_code) is not int or self.status_code < 1
        ):
            raise ValueError("status_code must be None or a positive integer.")
        _validate_optional_request_id(self.request_id)


# 模型返回了终态，但没有正常生成完整结果
@dataclass(frozen=True, kw_only=True)
class ResponseIncomplete(ModelEvent):
    reason: str
    message: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for field_name in ("reason", "message"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string.")
        _validate_optional_request_id(self.request_id)


# 最小诊断结果
@dataclass(frozen=True)
class ModelEventDiagnostics:
    usage_missing: bool
    peak_buffer_chars: int
    max_buffer_chars: int | None = None
    attempted_buffer_chars: int | None = None


# 保存一个内容块当前已经拼出的文字，以及它是否收到结束事件
@dataclass
class _ContentBuffer:
    text: str = ""
    done: bool = False


@dataclass
class _PendingToolCall:
    output_index: int
    call_id: str
    tool_name: str
    arguments: str = ""
    done: bool = False


# 保存一次模型 attempt 的聚合进度
class ModelEventAccumulator:
    def __init__(self, attempt_id: str, *, max_buffer_chars: int) -> None:
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty string.")
        if type(max_buffer_chars) is not int or max_buffer_chars < 1:
            raise ValueError("max_buffer_chars must be a positive integer.")
        self._attempt_id = attempt_id
        self._max_buffer_chars = max_buffer_chars  # 限制正文、reasoning 和 refusal 的总字符数
        self._expected_sequence = 1
        self._response_id: str | None = None
        self._started = False
        self._finished = False
        self._buffered_chars = 0
        self._text_buffers: dict[tuple[int, int], _ContentBuffer] = {}  # 统计三个字典中的总字符数
        self._reasoning_buffers: dict[tuple[int, int], _ContentBuffer] = {}
        self._refusal_buffers: dict[tuple[int, int], _ContentBuffer] = {}
        self._pending_tool_calls: dict[str, _PendingToolCall] = {}   # 没有准备好的toolcall
        self._tool_item_ids_by_call_id: dict[str, str] = {}
        self._completed_tool_calls: list[tuple[int, ToolCall]] = [] # 准备好参数的合法toolcall
        self._usage: dict[str, object] | None = None  # 最后一次 usage 快照
        self._terminal: ResponseCompleted | ResponseFailed | ResponseIncomplete | None = None # 唯一 terminal 事件
        self._peak_buffer_chars = 0  # 本次 attempt 的字符最大值
        self._attempted_buffer_chars: int | None = None
        self._diagnostics: ModelEventDiagnostics | None = None  # 清理后仍可读取的最终诊断结果

    # 告诉调用方下一条事件必须使用哪个编号
    @property
    def expected_sequence(self) -> int:
        return self._expected_sequence

    # 返回 created 事件确认的响应编号
    @property
    def response_id(self) -> str | None:
        return self._response_id

    # 表示聚合器是否已经成功接受第一条 ResponseCreated
    @property
    def started(self) -> bool:
        return self._started

    # 表示当前 attempt 是否已经关闭
    @property
    def finished(self) -> bool:
        return self._finished

    # 查看当前累加结果
    @property
    def buffered_chars(self) -> int:
        return self._buffered_chars

    # 普通正文
    @property
    def text(self) -> str:
        return self._joined_content(self._text_buffers)

    # 思考内容
    @property
    def reasoning(self) -> str:
        return self._joined_content(self._reasoning_buffers)

    # 拒答内容
    @property
    def refusal(self) -> str:
        return self._joined_content(self._refusal_buffers)


    # 已经收到合法 done 的工具调用
    @property
    def completed_tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(
            tool_call
            for _, tool_call in sorted(
                self._completed_tool_calls,
                key=lambda completed: completed[0],
            )
        )

    @property
    def diagnostics(self) -> ModelEventDiagnostics:
        return self._diagnostics or ModelEventDiagnostics(
            usage_missing=self._usage is None,
            peak_buffer_chars=self._peak_buffer_chars,
            max_buffer_chars=self._max_buffer_chars,
            attempted_buffer_chars=self._attempted_buffer_chars,
        )

    # 已经使用的字符数
    @staticmethod
    def _joined_content(buffers: dict[tuple[int, int], _ContentBuffer]) -> str:
        return "".join(buffers[key].text for key in sorted(buffers))

    # 在任何事件改变状态前执行检查
    def _validate_event(self, event: ModelEvent) -> None:
        if self._finished:
            self.abort()
            raise ModelProtocolError(
                "Cannot apply an event after the attempt has finished.",
                attempt_id=self._attempt_id,
            )
        if not isinstance(event, ModelEvent):
            raise ModelProtocolError(
                "event must be a ModelEvent.",
                attempt_id=self._attempt_id,
            )
        if event.attempt_id != self._attempt_id:
            raise ModelProtocolError(
                "event belongs to a different attempt.",
                attempt_id=self._attempt_id,
            )
        if event.sequence != self._expected_sequence:
            raise ModelProtocolError(
                f"Expected sequence {self._expected_sequence}, got {event.sequence}.",
                attempt_id=self._attempt_id,
            )

    def _reserve_buffer_chars(self, value: str) -> None:
        attempted_buffer_chars = self._buffered_chars + len(value)
        if attempted_buffer_chars > self._max_buffer_chars:
            self._attempted_buffer_chars = attempted_buffer_chars
            self.abort()
            raise ModelProtocolError(
                "Model event buffer exceeded max_buffer_chars.",
                kind=ModelErrorKind.BUFFER_OVERFLOW,
                attempt_id=self._attempt_id,
                max_buffer_chars=self._max_buffer_chars,
                attempted_buffer_chars=attempted_buffer_chars,
            )
        self._buffered_chars = attempted_buffer_chars
        self._peak_buffer_chars = max(
            self._peak_buffer_chars,
            self._buffered_chars,
        )

    # 把一片新文字追加到正确内容块
    def _append_content(
        self,
        buffers: dict[tuple[int, int], _ContentBuffer],
        key: tuple[int, int],
        delta: str,
    ) -> None:
        buffer = buffers.get(key)
        if buffer is not None and buffer.done:
            self.abort()
            raise ModelProtocolError(
                "Cannot append content after its done event.",
                attempt_id=self._attempt_id,
            )
        self._reserve_buffer_chars(delta)
        if buffer is None:
            buffer = _ContentBuffer()
            buffers[key] = buffer
        buffer.text += delta

    # 判断内容块是否完整
    def _finish_content(
        self,
        buffers: dict[tuple[int, int], _ContentBuffer],
        key: tuple[int, int],
        text: str,
    ) -> None:
        buffer = buffers.get(key)
        if buffer is None:
            buffer = _ContentBuffer()
            buffers[key] = buffer
        if buffer.done: # 重复收到结束事件，说明模型事件流不可靠
            self.abort()
            raise ModelProtocolError(
                "Content can only receive one done event.",
                attempt_id=self._attempt_id,
            )
        if buffer.text != text: # 最终全文与分片结果不同，说明中间可能丢失或重复了内容
            self.abort()
            raise ModelProtocolError(
                "Done content does not match accumulated content.",
                attempt_id=self._attempt_id,
            )
        buffer.done = True


    # 创建 _PendingToolCall
    def _start_tool_call(self, event: ToolCallStarted) -> None:
        if event.item_id in self._pending_tool_calls:
            self.abort()
            raise ModelProtocolError(
                "Tool item_id can only be started once.",
                attempt_id=self._attempt_id,
            )
        if event.call_id in self._tool_item_ids_by_call_id:
            self.abort()
            raise ModelProtocolError(
                "Tool call_id can only be started once.",
                attempt_id=self._attempt_id,
            )
        if any(
            pending.output_index == event.output_index
            for pending in self._pending_tool_calls.values()
        ):
            self.abort()
            raise ModelProtocolError(
                "Tool output_index can only identify one item.",
                attempt_id=self._attempt_id,
            )
        self._pending_tool_calls[event.item_id] = _PendingToolCall(
            output_index=event.output_index,
            call_id=event.call_id,
            tool_name=event.tool_name,
        )
        self._tool_item_ids_by_call_id[event.call_id] = event.item_id

    def _append_tool_arguments(self, event: ToolArgumentsDelta) -> None:
        pending = self._pending_tool_calls.get(event.item_id)
        if pending is None:
            self.abort()
            raise ModelProtocolError(
                "Tool arguments require a matching ToolCallStarted event.",
                attempt_id=self._attempt_id,
            )
        if pending.output_index != event.output_index:
            self.abort()
            raise ModelProtocolError(
                "Tool arguments changed output_index.",
                attempt_id=self._attempt_id,
            )
        if pending.done:
            self.abort()
            raise ModelProtocolError(
                "Cannot append tool arguments after their done event.",
                attempt_id=self._attempt_id,
            )
        self._reserve_buffer_chars(event.delta)
        pending.arguments += event.delta


    # 处理出完整的toolcall + argument 工具调用event
    def _finish_tool_arguments(self, event: ToolArgumentsDone) -> None:
        pending = self._pending_tool_calls.get(event.item_id)
        if pending is None:
            self.abort()
            raise ModelProtocolError(
                "Tool arguments require a matching ToolCallStarted event.",
                attempt_id=self._attempt_id,
            )
        if pending.output_index != event.output_index:
            self.abort()
            raise ModelProtocolError(
                "Tool arguments changed output_index.",
                attempt_id=self._attempt_id,
            )
        if pending.done:
            if pending.arguments == event.arguments:
                return
            self.abort()
            raise ModelProtocolError(
                "Repeated tool arguments done event changed arguments.",
                attempt_id=self._attempt_id,
            )
        if pending.arguments != event.arguments:
            self.abort()
            raise ModelProtocolError(
                "Done tool arguments do not match accumulated arguments.",
                attempt_id=self._attempt_id,
            )
        try:
            arguments = json.loads(event.arguments)
        except json.JSONDecodeError as exc:
            self.abort()
            raise ModelProtocolError(
                "Tool arguments must be valid JSON.",
                attempt_id=self._attempt_id,
                original=exc,
            ) from exc
        if not isinstance(arguments, dict):
            self.abort()
            raise ModelProtocolError(
                "Tool arguments must be a JSON object.",
                attempt_id=self._attempt_id,
            )
        self._completed_tool_calls.append(
            (
                pending.output_index,
                ToolCall(
                    tool_name=pending.tool_name,
                    arguments=arguments,
                    call_id=pending.call_id,
                ),
            )
        )
        pending.done = True

    def apply(self, event: ModelEvent) -> None:
        self._validate_event(event)
        if isinstance(event, ResponseCreated):
            if self._started:
                raise ModelProtocolError(
                    "ResponseCreated can only be applied once.",
                    attempt_id=self._attempt_id,
                )
            self._response_id = event.response_id
            self._started = True
        elif not self._started:
            raise ModelProtocolError(
                "The first model event must be ResponseCreated.",
                attempt_id=self._attempt_id,
            )
        elif isinstance(event, TextDelta):
            self._append_content(
                self._text_buffers,
                (event.output_index, event.content_index),
                event.delta,
            )
        elif isinstance(event, TextDone):
            self._finish_content(
                self._text_buffers,
                (event.output_index, event.content_index),
                event.text,
            )
        elif isinstance(event, ReasoningDelta):
            self._append_content(
                self._reasoning_buffers,
                (event.output_index, event.content_index),
                event.delta,
            )
        elif isinstance(event, ReasoningDone):
            self._finish_content(
                self._reasoning_buffers,
                (event.output_index, event.content_index),
                event.text,
            )
        elif isinstance(event, RefusalDelta):
            self._append_content(
                self._refusal_buffers,
                (event.output_index, event.content_index),
                event.delta,
            )
        elif isinstance(event, RefusalDone):
            self._finish_content(
                self._refusal_buffers,
                (event.output_index, event.content_index),
                event.text,
            )
        elif isinstance(event, ToolCallStarted):
            self._start_tool_call(event)
        elif isinstance(event, ToolArgumentsDelta):
            self._append_tool_arguments(event)
        elif isinstance(event, ToolArgumentsDone):
            self._finish_tool_arguments(event)
        elif isinstance(event, UsageUpdated):
            self._usage = dict(event.usage)
        elif isinstance(
            event,
            (ResponseCompleted, ResponseFailed, ResponseIncomplete),
        ):
            self._terminal = event
            self._finished = True
        else:
            raise ModelProtocolError(
                "Unsupported model event.",
                attempt_id=self._attempt_id,
            )
        self._expected_sequence += 1

    # 生成最终 ModelResponse
    def finish(self) -> ModelResponse:
        terminal = self._terminal
        if terminal is None:
            if self._finished:
                raise ModelProtocolError(
                    "The model attempt has already been closed.",
                    attempt_id=self._attempt_id,
                )
            self.abort()
            raise ModelProtocolError(
                "Model stream closed before a terminal event.",
                kind=ModelErrorKind.EARLY_CLOSE,
                attempt_id=self._attempt_id,
            )
        if isinstance(terminal, ResponseFailed):
            error = ModelResponseError(
                terminal.message,
                kind=terminal.kind,
                status_code=terminal.status_code,
                request_id=terminal.request_id,
                attempt_id=self._attempt_id,
            )
            self.abort()
            raise error
        if isinstance(terminal, ResponseIncomplete):
            kind = (
                ModelErrorKind.CONTEXT_WINDOW
                if terminal.reason in {"context_window", "context_length_exceeded"}
                else ModelErrorKind.PROTOCOL
            )
            error = ModelResponseError(
                f"{terminal.message} (reason: {terminal.reason})",
                kind=kind,
                request_id=terminal.request_id,
                attempt_id=self._attempt_id,
            )
            self.abort()
            raise error
        if terminal.response_id != self._response_id:
            self.abort()
            raise ModelProtocolError(
                "Completed response_id does not match ResponseCreated.",
                attempt_id=self._attempt_id,
            )
        if any(
            not buffer.done
            for buffers in (
                self._text_buffers,
                self._reasoning_buffers,
                self._refusal_buffers,
            )
            for buffer in buffers.values()
        ):
            self.abort()
            raise ModelProtocolError(
                "Cannot finish while content blocks are still pending.",
                attempt_id=self._attempt_id,
            )
        if any(not pending.done for pending in self._pending_tool_calls.values()):
            self.abort()
            raise ModelProtocolError(
                "Cannot finish while tool arguments are still pending.",
                attempt_id=self._attempt_id,
            )

        response = ModelResponse(
            response_id=terminal.response_id,
            output=list(terminal.output),
            output_text=self.text or None,
            tool_calls=list(self.completed_tool_calls),
            refusal=self.refusal or None,
            raw=terminal.raw,
            usage=dict(self._usage) if self._usage is not None else None,
            request_summary=(
                dict(terminal.request_summary)
                if terminal.request_summary is not None
                else None
            ),
            request_id=terminal.request_id,
        )
        self._capture_diagnostics()
        self._release_temporary_state()
        return response

    def _capture_diagnostics(self) -> None:
        if self._diagnostics is None:
            self._diagnostics = ModelEventDiagnostics(
                usage_missing=self._usage is None,
                peak_buffer_chars=self._peak_buffer_chars,
                max_buffer_chars=self._max_buffer_chars,
                attempted_buffer_chars=self._attempted_buffer_chars,
            )

    # 释放正文、reasoning、拒答、usage、terminal、半截工具参数和已完成工具临时列表，只保留不含用户内容的 diagnostics
    def _release_temporary_state(self) -> None:
        self._response_id = None
        self._started = False
        self._terminal = None
        self._usage = None
        self._buffered_chars = 0
        self._peak_buffer_chars = 0
        self._attempted_buffer_chars = None
        self._text_buffers.clear()
        self._reasoning_buffers.clear()
        self._refusal_buffers.clear()
        self._pending_tool_calls.clear()
        self._tool_item_ids_by_call_id.clear()
        self._completed_tool_calls.clear()


    # 立即丢弃当前 attempt 的可用结果并永久关闭聚合器
    def abort(self) -> None:
        self._capture_diagnostics()
        self._finished = True
        self._release_temporary_state()
