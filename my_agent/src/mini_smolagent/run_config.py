from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .guardrails import InputGuardrail, OutputGuardrail
from .lifecycle import LifecycleHooks
from .model_settings import ModelSettings


@dataclass(frozen=True)
class RunConfig:
    context: Any | None = None
    metadata: dict[str, Any] | None = None
    hooks: LifecycleHooks | None = None
    input_guardrails: list[InputGuardrail] | None = None
    output_guardrails: list[OutputGuardrail] | None = None
    max_steps: int | None = None
    max_turns: int | None = None
    tool_use_behavior: str | dict[str, list[str]] | None = None
    model_settings: ModelSettings | None = None
