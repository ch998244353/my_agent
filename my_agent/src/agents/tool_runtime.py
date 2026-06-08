from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections.abc import Iterable, Mapping  
# Iterable 表示这个对象可以被遍历 -> isinstance([1, 2, 3], Iterable) = true
# Mapping 这个对象是“键值对映射”结构 -> isinstance({"name": "agent"}, Mapping)) = True

from dataclasses import dataclass
from pprint import pformat
from typing import Any, Callable, Literal


ToolTimeoutBehavior = Literal["error_as_result", "raise_exception"]


@dataclass(frozen=True)
class ToolExecutionLimits:
    max_output_chars: int = 8_000
    max_error_chars: int = 2_000
    timeout_seconds: float | None = None
    timeout_behavior: ToolTimeoutBehavior = "error_as_result"


class ToolTimeoutError(TimeoutError):
    def __init__(self, tool_name: str, timeout_seconds: float):
        self.tool_name = tool_name
        self.timeout_seconds = timeout_seconds
        super().__init__(
            default_tool_timeout_error_message(
                tool_name=tool_name,
                timeout_seconds=timeout_seconds,
            )
        )


def default_tool_timeout_error_message(
    *,
    tool_name: str,
    timeout_seconds: float,
) -> str:
    return f"Tool '{tool_name}' timed out after {timeout_seconds:g} seconds."


# 给任意 operation 加 timeout 控制 ,  operation 上传执行 tool 的那段代码
def run_with_timeout(
    *,
    tool_name: str,
    operation: Callable[[], Any],
    timeout_seconds: float | None,
    timeout_behavior: ToolTimeoutBehavior = "error_as_result",
) -> Any:
    timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
    _validate_timeout_behavior(timeout_behavior)
    if timeout_seconds is None:
        return operation()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(operation)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        timeout_error = ToolTimeoutError(tool_name, timeout_seconds)
        if timeout_behavior == "raise_exception":
            raise timeout_error from exc
        return default_tool_timeout_error_message(
            tool_name=tool_name,
            timeout_seconds=timeout_seconds,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _normalize_timeout_seconds(timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
        raise TypeError("Tool timeout_seconds must be a positive number or None.")
    timeout_seconds = float(timeout_seconds)
    if not math.isfinite(timeout_seconds):
        raise ValueError("Tool timeout_seconds must be finite.")
    if timeout_seconds <= 0:
        raise ValueError("Tool timeout_seconds must be greater than 0.")
    return timeout_seconds


def _validate_timeout_behavior(timeout_behavior: ToolTimeoutBehavior) -> None:
    if timeout_behavior not in ("error_as_result", "raise_exception"):
        raise ValueError(
            "Tool timeout_behavior must be one of: error_as_result, raise_exception"
        )


@dataclass(frozen=True)
class ToolExecutionReport:
    tool_name: str
    call_id: str
    success: bool
    output_preview: str | None = None
    error_type: str | None = None
    reason: str | None = None
    elapsed_seconds: float | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "success": self.success,
            "output_preview": self.output_preview,
            "error_type": self.error_type,
            "reason": self.reason,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True)
class ToolApprovalDecision:
    requires_approval: bool
    call_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __bool__(self) -> bool:
        return self.requires_approval


@dataclass(frozen=True)
class ToolArgumentValidationResult:
    missing_arguments: tuple[str, ...] = ()
    unexpected_arguments: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.missing_arguments and not self.unexpected_arguments


def validate_tool_arguments(
    *,
    allowed_arguments: Iterable[str],
    required_arguments: Iterable[str],
    provided_arguments: Mapping[str, Any],
) -> ToolArgumentValidationResult:
    provided_names = set(provided_arguments)
    allowed_names = set(allowed_arguments)
    return ToolArgumentValidationResult(
        missing_arguments=tuple(
            name for name in required_arguments if name not in provided_names
        ),
        unexpected_arguments=tuple(sorted(provided_names - allowed_names)),
    )


def format_tool_argument_error(
    validation: ToolArgumentValidationResult,
) -> str | None:
    if validation.missing_arguments:
        missing_text = ", ".join(validation.missing_arguments)
        return f"Missing required arguments: {missing_text}"
    if validation.unexpected_arguments:
        unexpected_text = ", ".join(validation.unexpected_arguments)
        return f"Unexpected arguments: {unexpected_text}"
    return None


def format_tool_error(
    tool_name: str,
    error: Any,
    *,
    error_type: str | None = None,
    limits: ToolExecutionLimits | None = None,
) -> str:
    if error_type is None:
        error_type = type(error).__name__ if isinstance(error, BaseException) else "ToolError"

    if isinstance(error, BaseException):
        detail = getattr(error, "message", None) or str(error) or type(error).__name__
    else:
        detail = error

    max_error_chars = (limits or ToolExecutionLimits()).max_error_chars
    return format_tool_observation(
        tool_name,
        detail,
        success=False,
        reason=error_type,
        error_type=error_type,
        limits=limits,
        max_chars=max_error_chars,
    )


def format_tool_observation(
    tool_name: str,
    output: Any,
    *,
    success: bool = True,
    reason: str | None = None,
    error_type: str | None = None,
    limits: ToolExecutionLimits | None = None,
    max_chars: int | None = None,
) -> str:
    if success:
        return tool_output_preview(output, limits, max_chars=max_chars)

    resolved_reason = reason or error_type or "ToolError"
    detail_preview = tool_output_preview(output, limits, max_chars=max_chars)
    return (
        f"Tool '{tool_name}' observation\n"
        "status: error\n"
        f"reason: {resolved_reason}\n"
        f"detail: {detail_preview}"
    )


def requires_tool_approval(
    needs_approval: Any,
    context_wrapper: Any,
    agent: Any,
    tool_call: Any,
) -> ToolApprovalDecision:
    call_id = _tool_call_id(tool_call)
    if needs_approval is None:
        return ToolApprovalDecision(False, call_id=call_id)
    if isinstance(needs_approval, bool):
        return ToolApprovalDecision(needs_approval, call_id=call_id)
    if not callable(needs_approval):
        return ToolApprovalDecision(
            True,
            call_id=call_id,
            error_type="TypeError",
            error_message=(
                "Invalid needs_approval value: expected bool or callable, "
                f"got {type(needs_approval).__name__}."
            ),
        )

    try:
        arguments = _tool_call_arguments(tool_call)
        return ToolApprovalDecision(
            bool(needs_approval(context_wrapper, arguments, call_id)),
            call_id=call_id,
        )
    except Exception as exc:
        return ToolApprovalDecision(
            True,
            call_id=call_id,
            error_type=type(exc).__name__,
            error_message=str(exc) or type(exc).__name__,
        )


def _tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, Mapping):
        candidate = tool_call.get("call_id") or tool_call.get("id")
    else:
        candidate = getattr(tool_call, "call_id", None) or getattr(tool_call, "id", None)
    return str(candidate) if candidate is not None else None


def _tool_call_arguments(tool_call: Any) -> dict[str, Any]:
    if isinstance(tool_call, Mapping):
        arguments = tool_call.get("arguments", {})
    else:
        arguments = getattr(tool_call, "arguments", {})
    if isinstance(arguments, Mapping):
        return dict(arguments)
    return {}


def clip_tool_text(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text

    visible_chars = max(0, max_chars)
    visible_text = text[:visible_chars]
    omitted_chars = len(text) - len(visible_text)
    return (
        f"{visible_text}\n\n"
        f"[tool output truncated: omitted {omitted_chars} of {len(text)} characters]"
    )


def tool_output_preview(
    output: Any,
    limits: ToolExecutionLimits | None = None,
    *,
    max_chars: int | None = None,
) -> str:
    if isinstance(output, str):
        text = output
    elif isinstance(output, BaseException):
        text = f"{type(output).__name__}: {output}"
    else:
        try:
            text = pformat(output, sort_dicts=True)
        except Exception as exc:
            text = f"<unprintable {type(output).__name__}: {type(exc).__name__}>"

    limit = max_chars
    if limit is None:
        limit = (limits or ToolExecutionLimits()).max_output_chars
    return clip_tool_text(text, limit)
