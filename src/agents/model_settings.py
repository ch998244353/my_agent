from __future__ import annotations

from dataclasses import dataclass, fields, replace
from math import isfinite
from typing import Any, Literal


ToolChoice = Literal["auto", "required", "none"]
Verbosity = Literal["low", "medium", "high"]

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 60.0
DEFAULT_TOTAL_TIMEOUT = 120.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_INITIAL = 0.5
DEFAULT_BACKOFF_MAX = 8.0
DEFAULT_JITTER_RATIO = 0.2
DEFAULT_RETRY_BUDGET = 30.0
DEFAULT_MAX_STREAM_BUFFER_CHARS = 1_000_000
DEFAULT_ALLOW_NON_STREAM_FALLBACK = False
DEFAULT_FALLBACK_MODEL: str | None = None


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
    connect_timeout: float | None = None  # 建立模型连接最多等待多少秒
    read_timeout: float | None = None  # 读取模型响应最多等待多少秒
    total_timeout: float | None = None  # 整次模型请求最多运行多少秒
    max_attempts: int | None = None  # 最多发送次数，包含第一次请求
    backoff_initial: float | None = None  # 第一次重试前的基础等待秒数
    backoff_max: float | None = None  # 单次重试等待的秒数上限
    jitter_ratio: float | None = None  # 重试等待时间的随机浮动比例
    retry_budget: float | None = None  # 所有重试等待累计可用的秒数
    max_stream_buffer_chars: int | None = None  # 单次流式响应最多累计的字符数
    allow_non_stream_fallback: bool | None = None  # 是否允许改用一次非流式请求
    fallback_model: str | None = None  # 主模型重试耗尽后使用的备用模型

    def __post_init__(self) -> None:
        positive_numbers = ("connect_timeout", "read_timeout", "total_timeout")
        non_negative_numbers = ("backoff_initial", "backoff_max", "retry_budget")

        for name in (*positive_numbers, *non_negative_numbers, "jitter_ratio"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number.")
            if not isfinite(value):
                raise ValueError(f"{name} must be finite.")

        for name in positive_numbers:
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than 0.")

        for name in non_negative_numbers:
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be greater than or equal to 0.")

        if self.jitter_ratio is not None and not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1.")

        for name in ("max_attempts", "max_stream_buffer_chars"):
            value = getattr(self, name)
            if value is None:
                continue
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer.")
            if value < 1:
                raise ValueError(f"{name} must be greater than or equal to 1.")

        if (
            self.allow_non_stream_fallback is not None
            and type(self.allow_non_stream_fallback) is not bool
        ):
            raise TypeError("allow_non_stream_fallback must be a boolean.")

        if self.fallback_model is not None:
            if not isinstance(self.fallback_model, str):
                raise TypeError("fallback_model must be a string.")
            if not self.fallback_model.strip():
                raise ValueError("fallback_model must not be blank.")

        backoff_initial = (
            self.backoff_initial
            if self.backoff_initial is not None
            else DEFAULT_BACKOFF_INITIAL
        )
        backoff_max = (
            self.backoff_max
            if self.backoff_max is not None
            else DEFAULT_BACKOFF_MAX
        )
        if backoff_initial > backoff_max:
            raise ValueError("backoff_initial must not exceed backoff_max.")

        retry_budget = (
            self.retry_budget
            if self.retry_budget is not None
            else DEFAULT_RETRY_BUDGET
        )
        total_timeout = (
            self.total_timeout
            if self.total_timeout is not None
            else DEFAULT_TOTAL_TIMEOUT
        )
        if retry_budget > total_timeout:
            raise ValueError("retry_budget must not exceed total_timeout.")

    def resolve(self, override: ModelSettings | None) -> ModelSettings:
        if override is None:
            return self

        changes = {
            field.name: getattr(override, field.name)
            for field in fields(self)
            if getattr(override, field.name) is not None
        }
        return replace(self, **changes)
