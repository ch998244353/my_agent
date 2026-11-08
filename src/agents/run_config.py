from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any, Protocol

from .context_chunks import ContextChunk, _validate_context_chunks
from .guardrails import InputGuardrail, OutputGuardrail
from .lifecycle import LifecycleHooks
from .model_settings import ModelSettings
from .tool_runtime import ToolExecutionLimits
from .verification import VerificationPolicy

if TYPE_CHECKING:
    from .task_state import TaskStore


class SessionLike(Protocol):
    def get_items(self, limit: int | None = None) -> list[Any]:
        ...

    def add_items(self, items: list[Any]) -> None:
        ...

    def pop_item(self) -> Any | None:
        ...

    def clear_session(self) -> None:
        ...


@dataclass(frozen=True)
class RunConfig:
    context: Any | None = None
    session: SessionLike | None = None
    metadata: dict[str, Any] | None = None  # 本次运行上下文 的附加信息，会进入 RunContextWrapper
    tracing_disabled: bool = False
    trace_include_sensitive_data: bool = True
    workflow_name: str | None = None
    trace_id: str | None = None
    group_id: str | None = None
    trace_metadata: dict[str, Any] | None = None  # trace 导出记录 的附加信息，会写进 trace export
    hooks: LifecycleHooks | None = None
    input_guardrails: list[InputGuardrail] | None = None
    output_guardrails: list[OutputGuardrail] | None = None
    max_steps: int | None = None
    max_turns: int | None = None
    tool_use_behavior: str | dict[str, list[str]] | None = None
    tool_execution_limits: ToolExecutionLimits | None = None
    model_settings: ModelSettings | None = None
    verification: VerificationPolicy | None = None
    event_queue_size: int = 100   # 最多暂存100条尚未消费的事件
    cancellation_grace_period: float = 0.1  # 取消后默认允许0.1秒的收尾时间
    task_store: TaskStore | None = None  # 任务仓库
    task_id: str | None = None  # 接受任务节点编号
    context_chunks: tuple[ContextChunk, ...] = ()

    def __post_init__(self) -> None:
        _validate_context_chunks(self.context_chunks)
        if type(self.event_queue_size) is not int or self.event_queue_size < 1:
            raise ValueError(
                "event_queue_size must be an integer greater than or equal to 1."
            )

        grace_period = self.cancellation_grace_period
        if (
            isinstance(grace_period, bool)
            or not isinstance(grace_period, (int, float))
            or not isfinite(grace_period)
            or grace_period < 0
        ):
            raise ValueError(
                "cancellation_grace_period must be a finite non-negative number."
            )
        if (self.task_store is None) != (self.task_id is None):
            raise ValueError("task_store and task_id must be provided together.")
        if self.task_id is not None:
            if not isinstance(self.task_id, str):
                raise TypeError("task_id must be a string.")
            if not self.task_id.strip():
                raise ValueError("task_id must be a non-empty string.")
