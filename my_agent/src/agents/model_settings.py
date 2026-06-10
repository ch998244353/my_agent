from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any, Literal


ToolChoice = Literal["auto", "required", "none"]
Verbosity = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ModelSettings:
    temperature: float | None = None  # 控制模型输出的随机程度
    top_p: float | None = None  # 控制采样范围的参数
    tool_choice: ToolChoice | str | None = None  # 控制模型怎么使用工具
    parallel_tool_calls: bool | None = None  # 控制模型是否允许同轮发起多个工具调用
    max_output_tokens: int | None = None  # 控制模型最多输出多少 token
    store: bool | None = None  # 控制模型响应是否允许服务端存储
    reasoning: dict[str, Any] | None = None  # 透传 Responses API 的 reasoning 设置
    verbosity: Verbosity | None = None  # 控制支持模型的输出详细程度
    response_include: tuple[str, ...] | None = None  # 指定 Responses API 额外返回的 output 数据

    def resolve(self, override: ModelSettings | None) -> ModelSettings:
        if override is None:
            return self

        changes = {
            field.name: getattr(override, field.name)
            for field in fields(self)
            if getattr(override, field.name) is not None
        }
        return replace(self, **changes)
