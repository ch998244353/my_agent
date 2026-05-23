from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .guardrails import InputGuardrail, OutputGuardrail
from .lifecycle import LifecycleHooks
from .model_settings import ModelSettings


@dataclass(frozen=True)
class RunConfig:
    context: Any | None = None
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
    model_settings: ModelSettings | None = None
