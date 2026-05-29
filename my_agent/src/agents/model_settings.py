from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Literal


ToolChoice = Literal["auto", "required", "none"]


@dataclass(frozen=True)
class ModelSettings:
    temperature: float | None = None  # 控制模型输出的随机程度
    top_p: float | None = None  # 控制采样范围的参数
    tool_choice: ToolChoice | str | None = None  # 控制模型怎么使用工具
    max_output_tokens: int | None = None  # 控制模型最多输出多少 token
    store: bool | None = None  # 控制模型响应是否允许服务端存储

    def resolve(self, override: ModelSettings | None) -> ModelSettings:
        if override is None:
            return self

        changes = {
            field.name: getattr(override, field.name)
            for field in fields(self)
            if getattr(override, field.name) is not None
        }
        return replace(self, **changes)
