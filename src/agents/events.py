from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import ClassVar, Literal, TypeAlias, TypeVar


EVENT_SCHEMA_VERSION = 1


class RunAlreadyFinishedError(RuntimeError):
    def __init__(self, run_id: str, terminal_sequence: int) -> None:
        self.run_id = run_id
        self.terminal_sequence = terminal_sequence
        super().__init__(
            f"Run {run_id!r} already finished at sequence {terminal_sequence}."
        )


class RunCancelledError(RuntimeError):
    def __init__(self, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"Run {run_id!r} was cancelled: {reason}")


@dataclass(frozen=True, kw_only=True)
class RunEvent:
    EVENT_TYPE: ClassVar[str] = "run_event"

    run_id: str
    sequence: int  # 表示本次运行中的事件顺序
    timestamp: datetime
    turn_id: str | None = None
    step_number: int | None = None
    schema_version: int = EVENT_SCHEMA_VERSION   # 默认使用当前事件版本

    @property
    def event_type(self) -> str:
        return self.EVENT_TYPE

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string.")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be an integer greater than or equal to 1.")
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.utcoffset() != timedelta(0)
        ):
            raise ValueError("timestamp must be a UTC-aware datetime.")
        if self.turn_id is not None and (
            not isinstance(self.turn_id, str) or not self.turn_id.strip()
        ):
            raise ValueError("turn_id must be None or a non-empty string.")
        if self.step_number is not None and (
            type(self.step_number) is not int or self.step_number < 0
        ):
            raise ValueError("step_number must be None or a non-negative integer.")
        if (
            type(self.schema_version) is not int
            or self.schema_version != EVENT_SCHEMA_VERSION
        ):
            raise ValueError(
                f"Unsupported event schema version: {self.schema_version!r}."
            )


EventT = TypeVar("EventT", bound=RunEvent)


# 表示一次 Agent run 已经建立
@dataclass(frozen=True, kw_only=True)
class RunStartedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "run_started"

    agent_name: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.agent_name, str) or not self.agent_name.strip():
            raise ValueError("agent_name must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class TurnStartedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "turn_started"

    turn_number: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.turn_number) is not int or self.turn_number < 1:
            raise ValueError("turn_number must be a positive integer.")


@dataclass(frozen=True, kw_only=True)
class TurnCompletedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "turn_completed"

    turn_number: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.turn_number) is not int or self.turn_number < 1:
            raise ValueError("turn_number must be a positive integer.")


@dataclass(frozen=True, kw_only=True)
class RunCompletedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "run_completed"

    reached_final_answer: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.reached_final_answer) is not bool:
            raise ValueError("reached_final_answer must be a boolean.")


@dataclass(frozen=True, kw_only=True)
class RunStoppedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "run_stopped"

    reason: str
    resumable: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string.")
        if type(self.resumable) is not bool:
            raise ValueError("resumable must be a boolean.")


@dataclass(frozen=True, kw_only=True)
class RunCancelledEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "run_cancelled"

    reason: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class RunFailedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "run_failed"

    error_type: str
    message: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.error_type, str) or not self.error_type.strip():
            raise ValueError("error_type must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")


TerminalRunEvent: TypeAlias = (
    RunCompletedEvent | RunStoppedEvent | RunCancelledEvent | RunFailedEvent
)


def is_terminal_event(event: RunEvent) -> bool:
    return isinstance(
        event,
        (RunCompletedEvent, RunStoppedEvent, RunCancelledEvent, RunFailedEvent),
    )


@dataclass(frozen=True, kw_only=True)
class ModelStartedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "model_started"

    model_name: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name must be a non-empty string.")


# 一次真实的模型发送已经开始
@dataclass(frozen=True, kw_only=True)
class ModelAttemptStartedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "model_attempt_started"

    request_id: str
    attempt_id: str
    attempt_number: int
    model_name: str
    mode: Literal["stream", "non_stream"]
    reason: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("request_id", "attempt_id", "model_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer.")
        if self.mode not in ("stream", "non_stream"):
            raise ValueError("mode must be 'stream' or 'non_stream'.")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("reason must be None or a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ModelTextDeltaEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "model_text_delta"

    attempt_id: str
    delta: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.attempt_id, str) or not self.attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty string.")
        if not isinstance(self.delta, str) or not self.delta:
            raise ValueError("delta must be a non-empty string.")



# 当前失败已经确认，并将在等待后重新发送
@dataclass(frozen=True, kw_only=True)
class ModelRetryScheduledEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "model_retry_scheduled"

    request_id: str
    attempt_id: str
    attempt_number: int
    model_name: str
    mode: Literal["stream", "non_stream"]
    error_kind: str
    delay_seconds: float
    reason: str

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in (
            "request_id",
            "attempt_id",
            "model_name",
            "error_kind",
            "reason",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer.")
        if self.mode not in ("stream", "non_stream"):
            raise ValueError("mode must be 'stream' or 'non_stream'.")
        if (
            isinstance(self.delay_seconds, bool)
            or not isinstance(self.delay_seconds, (int, float))
            or not isfinite(self.delay_seconds)
            or self.delay_seconds < 0
        ):
            raise ValueError("delay_seconds must be finite and non-negative.")


# 模型从哪里切换到了哪里
@dataclass(frozen=True, kw_only=True)
class ModelFallbackEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "model_fallback"

    request_id: str
    attempt_id: str
    attempt_number: int
    from_model: str
    to_model: str
    reason: str
    from_mode: Literal["stream", "non_stream"] | None = None
    to_mode: Literal["stream", "non_stream"] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in (
            "request_id",
            "attempt_id",
            "from_model",
            "to_model",
            "reason",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer.")
        for name in ("from_mode", "to_mode"):
            value = getattr(self, name)
            if value is not None and value not in ("stream", "non_stream"):
                raise ValueError(f"{name} must be 'stream', 'non_stream', or None.")


@dataclass(frozen=True, kw_only=True)
class ModelCompletedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "model_completed"

    response_id: str | None
    tool_call_count: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.response_id is not None and (
            not isinstance(self.response_id, str) or not self.response_id.strip()
        ):
            raise ValueError("response_id must be None or a non-empty string.")
        if type(self.tool_call_count) is not int or self.tool_call_count < 0:
            raise ValueError("tool_call_count must be a non-negative integer.")


@dataclass(frozen=True, kw_only=True)
class ToolRequestedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "tool_requested"

    tool_name: str
    call_id: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ToolStartedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "tool_started"

    tool_name: str
    call_id: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ToolCompletedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "tool_completed"

    tool_name: str
    call_id: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ToolFailedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "tool_failed"

    tool_name: str
    call_id: str
    error_type: str
    message: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string.")
        if not isinstance(self.error_type, str) or not self.error_type.strip():
            raise ValueError("error_type must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ApprovalRequiredEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "approval_required"

    tool_name: str
    call_id: str
    reason: str | None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string.")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("reason must be None or a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ApprovalResolvedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "approval_resolved"

    tool_name: str
    call_id: str
    decision: Literal["approved", "rejected"]

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise ValueError("call_id must be a non-empty string.")
        if self.decision not in ("approved", "rejected"):
            raise ValueError("decision must be 'approved' or 'rejected'.")


@dataclass(frozen=True, kw_only=True)
class SteerAcceptedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "steer_accepted"

    steer_id: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.steer_id, str) or not self.steer_id.strip():
            raise ValueError("steer_id must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class SteerAppliedEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "steer_applied"

    steer_id: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.steer_id, str) or not self.steer_id.strip():
            raise ValueError("steer_id must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class RunErrorEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "run_error"

    error_type: str
    message: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.error_type, str) or not self.error_type.strip():
            raise ValueError("error_type must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")


RunEventType: TypeAlias = (
    RunStartedEvent
    | TurnStartedEvent
    | TurnCompletedEvent
    | RunCompletedEvent
    | RunStoppedEvent
    | RunCancelledEvent
    | RunFailedEvent
    | ModelStartedEvent
    | ModelAttemptStartedEvent
    | ModelTextDeltaEvent
    | ModelRetryScheduledEvent
    | ModelFallbackEvent
    | ModelCompletedEvent
    | ToolRequestedEvent
    | ToolStartedEvent
    | ToolCompletedEvent
    | ToolFailedEvent
    | ApprovalRequiredEvent
    | ApprovalResolvedEvent
    | SteerAcceptedEvent
    | SteerAppliedEvent
    | RunErrorEvent
)


def event_to_dict(event: RunEvent) -> dict[str, object]:
    if not isinstance(event, RunEvent):
        raise TypeError("event must be a RunEvent.")
    payload = {field.name: getattr(event, field.name) for field in fields(event)}
    payload["timestamp"] = event.timestamp.isoformat().replace("+00:00", "Z")
    return {"event_type": event.event_type, **payload}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 每次 Agent run 创建一个 reducer。它接收运行编号和可选时钟，内部只保存下一个序号与首个终态，不复制 RunState、审批状态、模型结果或事件历史
class RunEventReducer:
    def __init__(
        self,
        run_id: str,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        self._run_id = run_id
        self._clock = clock
        self._next_sequence = 1
        self._terminal_event: TerminalRunEvent | None = None

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def terminal_event(self) -> TerminalRunEvent | None:
        return self._terminal_event

    @property
    def finished(self) -> bool:
        return self._terminal_event is not None


    # emit() 接收具体事件类、可选 turn/step 位置和该事件的载荷，输出已经填写公共元数据的不可变事件
    def emit(
        self,
        event_cls: type[EventT],
        /,
        *,
        turn_id: str | None = None,
        step_number: int | None = None,
        **payload: object,
    ) -> EventT:
        if self._terminal_event is not None:
            raise RunAlreadyFinishedError(
                self._run_id,
                self._terminal_event.sequence,
            )
        if not isinstance(event_cls, type) or not issubclass(event_cls, RunEvent):
            raise TypeError("event_cls must be a RunEvent class.")
        event = event_cls(
            run_id=self._run_id,
            sequence=self._next_sequence,
            timestamp=self._clock(),
            turn_id=turn_id,
            step_number=step_number,
            **payload,
        )
        self._next_sequence += 1
        if is_terminal_event(event):
            self._terminal_event = event
        return event
