from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ToolChoice = Literal["auto", "required", "none"]


@dataclass(frozen=True)
class ModelSettings:
    temperature: float | None = None  # 控制模型输出的随机程度
    top_p: float | None = None  # 控制采样范围的参数
    tool_choice: ToolChoice | str | None = None  # 控制模型怎么使用工具
