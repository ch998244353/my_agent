from __future__ import annotations

import asyncio
import random
import time
from contextlib import aclosing
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from enum import Enum
from math import isfinite, ldexp
from typing import Awaitable, Callable, Literal, TypeVar

from .contracts import ModelRequest, ModelResponse
from .events import RunCancelledError
from .model_events import (
    ModelEvent,
    ModelEventAccumulator,
    ReasoningDelta,
    RefusalDelta,
    TextDelta,
    ToolCallStarted,
)
from .model_settings import (
    DEFAULT_BACKOFF_INITIAL,
    DEFAULT_BACKOFF_MAX,
    DEFAULT_JITTER_RATIO,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_STREAM_BUFFER_CHARS,
    DEFAULT_RETRY_BUDGET,
    DEFAULT_TOTAL_TIMEOUT,
    ModelSettings,
)
from .models import (
    ModelErrorKind,
    ModelResponseError,
    classify_model_error,
    run_non_stream_model_response,
    stream_model_response,
    supports_streaming_model,
)
from .run_control import CancellationToken


_RETRYABLE_ERROR_KINDS = {
    ModelErrorKind.TRANSPORT,
    ModelErrorKind.RATE_LIMIT,
    ModelErrorKind.SERVER,
    ModelErrorKind.EARLY_CLOSE,
}

AsyncSleep = Callable[[float], Awaitable[None]]
AwaitResult = TypeVar("AwaitResult")
_CANCEL_POLL_SECONDS = 0.01


def _validate_non_empty_string(name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string.")
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_attempt_number(value: object) -> None:
    if type(value) is not int:
        raise TypeError("attempt_number must be an integer.")
    if value < 1:
        raise ValueError("attempt_number must be greater than or equal to 1.")


def _validate_non_negative_number(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number.")
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative.")


class RetryAction(str, Enum):
    RETRY = "retry"
    FAIL = "fail"
    NON_STREAM_FALLBACK = "non_stream_fallback"
    MODEL_FALLBACK = "model_fallback"


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    reason: str


# 保存“是否还能重试”以及原因
@dataclass(frozen=True)
class RetryDelayDecision:
    delay_seconds: float | None
    reason: str


# 记录表示一次真实发送发生了什么
@dataclass(frozen=True)
class ModelAttemptRecord:
    request_id: str
    attempt_id: str
    attempt_number: int
    model_name: str
    mode: Literal["stream", "non_stream"]
    outcome: Literal["succeeded", "failed", "cancelled"]
    duration_seconds: float
    error_kind: ModelErrorKind | None = None
    status_code: int | None = None
    retry_delay_seconds: float | None = None
    reason: str | None = None
    response_id: str | None = None
    usage: dict[str, object] | None = None
    ttft_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "attempt_id", "model_name"):
            _validate_non_empty_string(name, getattr(self, name))
        _validate_attempt_number(self.attempt_number)
        if self.mode not in ("stream", "non_stream"):
            raise ValueError("mode must be 'stream' or 'non_stream'.")
        if self.outcome not in ("succeeded", "failed", "cancelled"):
            raise ValueError("outcome must be 'succeeded', 'failed', or 'cancelled'.")
        _validate_non_negative_number("duration_seconds", self.duration_seconds)
        if self.error_kind is not None and not isinstance(
            self.error_kind, ModelErrorKind
        ):
            raise TypeError("error_kind must be a ModelErrorKind or None.")
        if self.status_code is not None and (
            type(self.status_code) is not int
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("status_code must be an HTTP status code or None.")
        if self.retry_delay_seconds is not None:
            _validate_non_negative_number(
                "retry_delay_seconds", self.retry_delay_seconds
            )
        if self.reason is not None:
            _validate_non_empty_string("reason", self.reason)
        if self.response_id is not None:
            _validate_non_empty_string("response_id", self.response_id)
        if self.usage is not None:
            if not isinstance(self.usage, dict):
                raise TypeError("usage must be a dictionary or None.")
            object.__setattr__(self, "usage", dict(self.usage))
        if self.ttft_seconds is not None:
            _validate_non_negative_number("ttft_seconds", self.ttft_seconds)


class ModelCancelledError(RunCancelledError):
    def __init__(
        self,
        run_id: str,
        reason: str,
        attempts: tuple[ModelAttemptRecord, ...] = (),
    ) -> None:
        attempt_values = tuple(attempts)
        if not all(
            isinstance(attempt, ModelAttemptRecord) for attempt in attempt_values
        ):
            raise TypeError("attempts must contain only ModelAttemptRecord values.")
        self.attempts = attempt_values
        super().__init__(run_id, reason)


# 给未来的实时消费者看当前正在发生什么
@dataclass(frozen=True)
class ModelAttemptNotice:
    notice_type: Literal[
        "attempt_started",
        "retry_scheduled",
        "model_fallback_started",
    ]
    request_id: str
    attempt_id: str
    attempt_number: int
    model_name: str
    mode: Literal["stream", "non_stream"]
    reason: str | None = None
    delay_seconds: float | None = None
    error_kind: ModelErrorKind | None = None  # 接保存已经判断好的错误类别
    previous_model_name: str | None = None  # 保存切换前真正失败的模型
    previous_mode: Literal["stream", "non_stream"] | None = None

    def __post_init__(self) -> None:
        if self.notice_type not in (
            "attempt_started",
            "retry_scheduled",
            "model_fallback_started",
        ):
            raise ValueError(
                "notice_type must be 'attempt_started', 'retry_scheduled', "
                "or 'model_fallback_started'."
            )
        for name in ("request_id", "attempt_id", "model_name"):
            _validate_non_empty_string(name, getattr(self, name))
        _validate_attempt_number(self.attempt_number)
        if self.mode not in ("stream", "non_stream"):
            raise ValueError("mode must be 'stream' or 'non_stream'.")
        if self.reason is not None:
            _validate_non_empty_string("reason", self.reason)
        if self.delay_seconds is not None:
            _validate_non_negative_number("delay_seconds", self.delay_seconds)
        if self.error_kind is not None and not isinstance(
            self.error_kind, ModelErrorKind
        ):
            raise TypeError("error_kind must be a ModelErrorKind or None.")
        if self.previous_model_name is not None:
            _validate_non_empty_string(
                "previous_model_name",
                self.previous_model_name,
            )
        if self.previous_mode is not None and self.previous_mode not in (
            "stream",
            "non_stream",
        ):
            raise ValueError(
                "previous_mode must be 'stream', 'non_stream', or None."
            )


# 模型最终成功时，调用方会同时得到标准 ModelResponse 和全部发送记录
@dataclass(frozen=True)
class ModelExecution:
    response: ModelResponse
    attempts: tuple[ModelAttemptRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.response, ModelResponse):
            raise TypeError("response must be a ModelResponse.")
        attempts = tuple(self.attempts)
        if not attempts:
            raise ValueError("attempts must not be empty.")
        if not all(isinstance(attempt, ModelAttemptRecord) for attempt in attempts):
            raise TypeError("attempts must contain only ModelAttemptRecord values.")
        if attempts[-1].outcome != "succeeded":
            raise ValueError("the final attempt must have succeeded.")
        object.__setattr__(self, "attempts", attempts)


class ModelExecutionError(ModelResponseError):
    def __init__(
        self,
        last_error: ModelResponseError,
        attempts: tuple[ModelAttemptRecord, ...],
    ) -> None:
        if not isinstance(last_error, ModelResponseError):
            raise TypeError("last_error must be a ModelResponseError.")
        attempt_values = tuple(attempts)
        if not attempt_values:
            raise ValueError("attempts must not be empty.")
        if not all(
            isinstance(attempt, ModelAttemptRecord) for attempt in attempt_values
        ):
            raise TypeError("attempts must contain only ModelAttemptRecord values.")
        if attempt_values[-1].outcome != "failed":
            raise ValueError("the final attempt must have failed.")

        self.last_error = last_error
        self.attempts = attempt_values
        super().__init__(
            str(last_error),
            kind=last_error.kind,
            status_code=last_error.status_code,
            error_code=last_error.error_code,
            retry_after=last_error.retry_after,
            request_id=last_error.request_id,
            attempt_id=last_error.attempt_id,
            original=last_error.original,
        )


# 保存本次请求真正使用的次数、退避、等待预算和总时限
@dataclass(frozen=True, init=False)
class RetryPolicy:
    max_attempts: int
    backoff_initial: float
    backoff_max: float
    jitter_ratio: float
    retry_budget: float
    total_timeout: float
    allow_non_stream_fallback: bool

    def __init__(self, settings: ModelSettings) -> None:
        if not isinstance(settings, ModelSettings):
            raise TypeError("settings must be a ModelSettings instance.")

        object.__setattr__(
            self,
            "max_attempts",
            settings.max_attempts
            if settings.max_attempts is not None
            else DEFAULT_MAX_ATTEMPTS,
        )
        for name, default in (
            ("backoff_initial", DEFAULT_BACKOFF_INITIAL),
            ("backoff_max", DEFAULT_BACKOFF_MAX),
            ("jitter_ratio", DEFAULT_JITTER_RATIO),
            ("retry_budget", DEFAULT_RETRY_BUDGET),
            ("total_timeout", DEFAULT_TOTAL_TIMEOUT),
        ):
            value = getattr(settings, name)
            object.__setattr__(
                self,
                name,
                float(value if value is not None else default),
            )
        object.__setattr__(
            self,
            "allow_non_stream_fallback",
            settings.allow_non_stream_fallback is True,
        )

    # 接收一次已经失败的模型请求、当前发送编号和此前累计等待秒数，输出 RetryDecision
    def decide(
        self,
        error: ModelResponseError,
        *,
        attempt_number: int,
        retry_delay_spent: float,
        model_fallback_allowed: bool = False,
    ) -> RetryDecision:
        if not isinstance(error, ModelResponseError):
            raise TypeError("error must be a ModelResponseError instance.")
        if type(attempt_number) is not int or attempt_number < 1:
            raise ValueError("attempt_number must be an integer starting at 1.")
        if (
            isinstance(retry_delay_spent, bool)
            or not isinstance(retry_delay_spent, (int, float))
            or not isfinite(retry_delay_spent)
            or retry_delay_spent < 0
        ):
            raise ValueError("retry_delay_spent must be a finite non-negative number.")
        if type(model_fallback_allowed) is not bool:
            raise TypeError("model_fallback_allowed must be a boolean.")

        if error.kind is ModelErrorKind.STREAM_UNAVAILABLE:
            if self.allow_non_stream_fallback:
                return RetryDecision(
                    RetryAction.NON_STREAM_FALLBACK,
                    "non_stream_fallback",
                )
            return RetryDecision(RetryAction.FAIL, "non_retryable_error")
        if error.kind not in _RETRYABLE_ERROR_KINDS:
            return RetryDecision(RetryAction.FAIL, "non_retryable_error")
        if attempt_number >= self.max_attempts:
            if (
                model_fallback_allowed
                and error.kind in (ModelErrorKind.RATE_LIMIT, ModelErrorKind.SERVER)
            ):
                return RetryDecision(RetryAction.MODEL_FALLBACK, "model_fallback")
            return RetryDecision(RetryAction.FAIL, "max_attempts_reached")
        if retry_delay_spent >= self.retry_budget:
            if (
                model_fallback_allowed
                and error.kind in (ModelErrorKind.RATE_LIMIT, ModelErrorKind.SERVER)
            ):
                return RetryDecision(RetryAction.MODEL_FALLBACK, "model_fallback")
            return RetryDecision(RetryAction.FAIL, "retry_budget_exhausted")
        return RetryDecision(RetryAction.RETRY, "retryable_error")


# 接收 原始 retry_after，输出从现在开始应等待的秒数
def parse_retry_after(
    value: float | str | None,
    *,
    now_timestamp: float,
) -> float | None:
    if (
        isinstance(now_timestamp, bool)
        or not isinstance(now_timestamp, (int, float))
        or not isfinite(now_timestamp)
    ):
        raise ValueError("now_timestamp must be a finite number.")

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if isfinite(value) and value >= 0 else None
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        return seconds if isfinite(seconds) and seconds >= 0 else None

    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return None
        return max(0.0, parsed.timestamp() - now_timestamp)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# 把错误、次数、已等待时间、已运行时间和随机函数组合起来，输出是否等待的decision 以及 等待秒数或停止原因
def compute_retry_delay(
    policy: RetryPolicy,
    error: ModelResponseError,
    *,
    attempt_number: int,
    retry_delay_spent: float,
    elapsed: float,
    now_timestamp: float,
    random_fn: Callable[[], float],
) -> RetryDelayDecision:
    if not isinstance(policy, RetryPolicy):
        raise TypeError("policy must be a RetryPolicy.")

    # 错误本身不可重试、次数耗尽或预算已经耗尽时，直接沿用原失败原因，不再计算无意义的 delay
    decision = policy.decide(
        error,
        attempt_number=attempt_number,
        retry_delay_spent=retry_delay_spent,
    )
    if decision.action is not RetryAction.RETRY:
        return RetryDelayDecision(None, decision.reason)

    # 只有策略允许重试时才检查运行耗时并解析服务端等待。合法 Retry-After 会直接成为候选 delay
    _validate_non_negative_number("elapsed", elapsed)
    delay = parse_retry_after(
        error.retry_after,
        now_timestamp=now_timestamp,
    )

    # 没有合法 Retry-After 时才计算指数退避
    if delay is None:
        if policy.backoff_initial == 0:
            base_delay = 0.0
        else:
            try:
                base_delay = min(
                    ldexp(policy.backoff_initial, attempt_number - 1),
                    policy.backoff_max,
                )
            except OverflowError:
                base_delay = policy.backoff_max

        delay = base_delay
        if policy.jitter_ratio > 0 and base_delay > 0:
            random_value = random_fn()
            if (
                isinstance(random_value, bool)
                or not isinstance(random_value, (int, float))
                or not isfinite(random_value)
                or not 0 <= random_value <= 1
            ):
                raise ValueError("random_fn must return a number between 0 and 1.")
            delay = min(
                base_delay
                * (
                    1
                    - policy.jitter_ratio
                    + 2 * policy.jitter_ratio * random_value
                ),
                policy.backoff_max,
            )

    # 候选等待会让累计等待超过预算时直接失败。刚好等于预算仍然允许，因为这次等待完成后还能发出下一次请求，但之后不能继续安排更多等待
    if retry_delay_spent + delay > policy.retry_budget:
        return RetryDelayDecision(None, "retry_budget_exceeded")
    if elapsed + delay >= policy.total_timeout:
        return RetryDelayDecision(None, "total_timeout_exceeded")
    return RetryDelayDecision(float(delay), "retry_scheduled")


# 把整次请求编号和发送序号组合成唯一 attempt id
def _attempt_id(request_id: str, attempt_number: int) -> str:
    _validate_non_empty_string("request_id", request_id)
    _validate_attempt_number(attempt_number)
    return f"{request_id}:attempt:{attempt_number}"


# 运行一个异步操作，同时检查现有取消信号和本次剩余总时限,在deadline前取消任务
async def _await_with_limits(
    awaitable: Awaitable[AwaitResult],
    *,
    cancellation_token: CancellationToken | None,
    timeout_seconds: float,
) -> AwaitResult:
    _validate_non_negative_number("timeout_seconds", timeout_seconds)
    operation = asyncio.ensure_future(awaitable)  # 启动异步操作  ensure_future() 把协程变成可观察、可取消的异步任务
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        while True:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            remaining = deadline - loop.time()  # 每轮先检查 token，再计算当前剩余时间
            if remaining <= 0:
                raise ModelResponseError(
                    "Model request exceeded total timeout.",
                    kind=ModelErrorKind.TRANSPORT,
                    error_code="total_timeout",
                )
            poll_seconds = (
                min(remaining, _CANCEL_POLL_SECONDS)
                if cancellation_token is not None
                else remaining
            )
            done, _ = await asyncio.wait(
                (operation,),
                timeout=poll_seconds,
            )
            if not done:
                continue
            if cancellation_token is not None:  # 作完成后再检查一次 token，避免取消和结果几乎同时到达时提交迟到结果
                cancellation_token.raise_if_cancelled()
            return operation.result()
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


# 在退避等待期间响应同一个取消信号和总时限
# 在 sleep 前后各检查一次取消，中间由 _await_with_limits() 持续观察。比如原本准备等待8秒，用户在第2秒取消，就不会继续睡满剩余6秒，更不会创建下一个 attempt
async def _sleep_with_cancel(
    delay_seconds: float,
    *,
    cancellation_token: CancellationToken | None,
    sleep: AsyncSleep,
    timeout_seconds: float,
) -> None:
    _validate_non_negative_number("delay_seconds", delay_seconds)
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    await _await_with_limits(
        sleep(delay_seconds),
        cancellation_token=cancellation_token,
        timeout_seconds=timeout_seconds,
    )
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


#  在 时间限制 以及 取消请求 下,读取一次模型发送产生的所有事件，把它们拼成一个完整 ModelResponse
async def _consume_stream_attempt(
    model: object,
    request: ModelRequest,
    *,
    max_buffer_chars: int,
    cancellation_token: CancellationToken | None,
    deadline: float,
    clock: Callable[[], float],
    attempt_started: float,
    on_event: Callable[[ModelEvent], None] | None,
) -> tuple[ModelResponse, float | None]:
    accumulator = ModelEventAccumulator(
        request.attempt_id,
        max_buffer_chars=max_buffer_chars,
    )
    received_event = False
    ttft_seconds: float | None = None
    try:
        async with aclosing(stream_model_response(model, request)) as events:  #aclosing() 保证无论正常结束、超时还是异常，都会关闭这个异步事件流
            while True:
                try:
                    event = await _await_with_limits(
                        anext(events),  # 从异步事件流读取下一条
                        cancellation_token=cancellation_token,
                        timeout_seconds=max(0.0, deadline - clock()),
                    )
                except StopAsyncIteration:
                    break
                received_event = True
                if ttft_seconds is None and isinstance(
                    event,
                    (TextDelta, ReasoningDelta, RefusalDelta, ToolCallStarted),
                ):
                    ttft_seconds = clock() - attempt_started
                accumulator.apply(event)
                if on_event is not None:
                    on_event(event)
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            return accumulator.finish(), ttft_seconds
    except ModelResponseError as exc:
        if received_event and exc.kind is ModelErrorKind.STREAM_UNAVAILABLE:
            exc.kind = ModelErrorKind.EARLY_CLOSE
        accumulator.abort()
        raise
    except BaseException:
        accumulator.abort()
        raise


# 把“开始发送”和“准备重试”等实时信息交给观察者，但观察者出错不能破坏模型请求
def _notify(
    on_notice: Callable[[ModelAttemptNotice], None] | None,
    notice: ModelAttemptNotice,
) -> None:
    if on_notice is None:
        return
    try:
        on_notice(notice)
    except Exception:
        pass


# 尝试发送模型请求并在retrypolicy允许的情况下进行等待并重新发送
async def run_model_request(
    model: object,
    request: ModelRequest,
    *,
    cancellation_token: CancellationToken | None = None,
    on_notice: Callable[[ModelAttemptNotice], None] | None = None,
    on_event: Callable[[ModelEvent], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: AsyncSleep = asyncio.sleep,
    random_fn: Callable[[], float] = random.random,
) -> ModelExecution:
    if not isinstance(request, ModelRequest):
        raise TypeError("request must be a ModelRequest.")
    if cancellation_token is not None and not isinstance(
        cancellation_token,
        CancellationToken,
    ):
        raise TypeError("cancellation_token must be a CancellationToken or None.")

    # 初始化本次执行状态

    policy = RetryPolicy(request.model_settings)  # 从请求设置生成 RetryPolicy
    max_buffer_chars = request.model_settings.max_stream_buffer_chars
    if max_buffer_chars is None:
        max_buffer_chars = getattr(model, "max_buffer_chars", None)
    if max_buffer_chars is None:
        max_buffer_chars = DEFAULT_MAX_STREAM_BUFFER_CHARS

    execution_started = clock()
    deadline = execution_started + policy.total_timeout
    retry_delay_spent = 0.0
    attempt_number = 1  # 从 attempt 1 开始，累计实际等待时间
    attempts: list[ModelAttemptRecord] = []
    fallback_model = request.model_settings.fallback_model
    current_model_name = request.model_name
    mode: Literal["stream", "non_stream"] = (
        "stream" if supports_streaming_model(model) else "non_stream"
    )
    using_non_stream_fallback = False
    using_model_fallback = False
    fallback_reason: str | None = None
    fallback_from_model: str | None = None
    fallback_from_mode: Literal["stream", "non_stream"] | None = None

    while True:  # 每轮复制基础请求，写入本轮模型名和 attempt id，再通知观察者
        if cancellation_token is not None:
            try:
                cancellation_token.raise_if_cancelled()
            except RunCancelledError as exc:
                raise ModelCancelledError(
                    exc.run_id,
                    exc.reason,
                    tuple(attempts),
                ) from exc


        if clock() >= deadline:
            timeout_error = ModelResponseError(
                "Model request exceeded total timeout.",
                kind=ModelErrorKind.TRANSPORT,
                error_code="total_timeout",
                request_id=request.request_id,
                attempt_id=attempts[-1].attempt_id if attempts else request.attempt_id,
            )
            if not attempts:
                raise timeout_error
            attempts[-1] = replace(
                attempts[-1],
                reason="total_timeout_exceeded",
            )
            raise ModelExecutionError(timeout_error, tuple(attempts))

        # 创建本轮请求：复制原请求，只修改本轮 model_name 和 attempt_id
        attempt_request = replace(
            request,
            model_name=current_model_name,
            attempt_id=_attempt_id(request.request_id, attempt_number),
        )

        if fallback_reason is not None:
            assert fallback_from_model is not None
            assert fallback_from_mode is not None
            _notify(
                on_notice,
                ModelAttemptNotice(
                    notice_type="model_fallback_started",
                    request_id=attempt_request.request_id,
                    attempt_id=attempt_request.attempt_id,
                    attempt_number=attempt_number,
                    model_name=attempt_request.model_name,
                    mode=mode,
                    reason=fallback_reason,
                    previous_model_name=fallback_from_model,
                    previous_mode=fallback_from_mode,
                ),
            )

        # 向外部观察者发送通知 本轮开始
        started_notice = ModelAttemptNotice(
            notice_type="attempt_started",
            request_id=attempt_request.request_id,
            attempt_id=attempt_request.attempt_id,
            attempt_number=attempt_number,
            model_name=attempt_request.model_name,
            mode=mode,
            reason=fallback_reason,
        )
        _notify(on_notice, started_notice)
        attempt_started = clock()
        ttft_seconds: float | None = None

        try:
            if using_non_stream_fallback:
                response = await _await_with_limits(
                    run_non_stream_model_response(model, attempt_request),
                    cancellation_token=cancellation_token,
                    timeout_seconds=max(0.0, deadline - clock()),
                )
            else:
                response, ttft_seconds = await _consume_stream_attempt(
                    model,
                    attempt_request,
                    max_buffer_chars=max_buffer_chars,
                    cancellation_token=cancellation_token,
                    deadline=deadline,
                    clock=clock,
                    attempt_started=attempt_started,
                    on_event=on_event,
                )
        except Exception as exc:
            if cancellation_token is not None and not isinstance(
                exc,
                RunCancelledError,
            ):
                try:
                    cancellation_token.raise_if_cancelled()
                except RunCancelledError as cancelled:
                    exc = cancelled
            if isinstance(exc, RunCancelledError):
                attempts.append(
                    ModelAttemptRecord(
                        request_id=attempt_request.request_id,
                        attempt_id=attempt_request.attempt_id,
                        attempt_number=attempt_number,
                        model_name=attempt_request.model_name,
                        mode=mode,
                        outcome="cancelled",
                        duration_seconds=clock() - attempt_started,
                        reason=fallback_reason or "cancelled",
                    )
                )
                raise ModelCancelledError(
                    exc.run_id,
                    exc.reason,
                    tuple(attempts),
                ) from exc

            # 将普通异常分类 并保存错误记录
            error = classify_model_error(exc)
            if error.request_id is None:
                error.request_id = attempt_request.request_id
            if error.attempt_id is None:
                error.attempt_id = attempt_request.attempt_id

            failed_record = ModelAttemptRecord(
                request_id=attempt_request.request_id,
                attempt_id=attempt_request.attempt_id,
                attempt_number=attempt_number,
                model_name=attempt_request.model_name,
                mode=mode,
                outcome="failed",
                duration_seconds=clock() - attempt_started,
                error_kind=error.kind,
                status_code=error.status_code,
            )
            attempts.append(failed_record)
            if error.error_code == "total_timeout":
                attempts[-1] = replace(
                    failed_record,
                    reason="total_timeout_exceeded",
                )
                raise ModelExecutionError(error, tuple(attempts)) from exc

            if using_non_stream_fallback:
                attempts[-1] = replace(
                    failed_record,
                    reason=fallback_reason,
                )
                raise ModelExecutionError(error, tuple(attempts)) from exc
            if using_model_fallback:
                attempts[-1] = replace(
                    failed_record,
                    reason=fallback_reason,
                )
                raise ModelExecutionError(error, tuple(attempts)) from exc

            retry_decision = policy.decide(
                error,
                attempt_number=attempt_number,
                retry_delay_spent=retry_delay_spent,
                model_fallback_allowed=(
                    fallback_model is not None
                    and fallback_model != request.model_name
                    and mode == "stream"
                    and clock() < deadline
                ),
            )

            # 把下一轮发送从主模型切换到备用模型，并留下“从哪个模型换到哪个模型”的通知
            if (
                retry_decision.action is RetryAction.NON_STREAM_FALLBACK
                and mode == "stream"
            ):
                attempts[-1] = replace(
                    failed_record,
                    reason=retry_decision.reason,
                )
                fallback_reason = retry_decision.reason
                fallback_from_model = attempt_request.model_name
                fallback_from_mode = mode
                attempt_number += 1
                mode = "non_stream"
                using_non_stream_fallback = True
                continue
            if retry_decision.action is RetryAction.NON_STREAM_FALLBACK:
                attempts[-1] = replace(
                    failed_record,
                    reason="non_retryable_error",
                )
                raise ModelExecutionError(error, tuple(attempts)) from exc
            if retry_decision.action is RetryAction.MODEL_FALLBACK:
                assert fallback_model is not None
                attempts[-1] = replace(
                    failed_record,
                    reason=retry_decision.reason,
                )
                fallback_reason = retry_decision.reason
                fallback_from_model = attempt_request.model_name
                fallback_from_mode = mode
                attempt_number += 1
                current_model_name = fallback_model
                using_model_fallback = True
                continue

            # 判断是否应该重试,得到等待决定
            delay_decision = compute_retry_delay(
                policy,
                error,
                attempt_number=attempt_number,
                retry_delay_spent=retry_delay_spent,
                elapsed=clock() - execution_started,
                now_timestamp=wall_clock(),
                random_fn=random_fn,
            )
            attempts[-1] = replace(
                failed_record,
                reason=delay_decision.reason,
            )
            if delay_decision.delay_seconds is None:
                raise ModelExecutionError(error, tuple(attempts)) from exc

            # 通知并等待下一次重试
            retry_notice = ModelAttemptNotice(
                notice_type="retry_scheduled",
                request_id=attempt_request.request_id,
                attempt_id=attempt_request.attempt_id,
                attempt_number=attempt_number,
                model_name=attempt_request.model_name,
                mode=mode,
                reason=delay_decision.reason,
                delay_seconds=delay_decision.delay_seconds,
                error_kind=error.kind,
            )
            _notify(on_notice, retry_notice)
            wait_started = clock()
            try:
                await _sleep_with_cancel(
                    delay_decision.delay_seconds,
                    cancellation_token=cancellation_token,
                    sleep=sleep,
                    timeout_seconds=max(0.0, deadline - clock()),
                )
            except RunCancelledError as exc:
                attempts[-1] = replace(
                    attempts[-1],
                    retry_delay_seconds=clock() - wait_started,
                )
                raise ModelCancelledError(
                    exc.run_id,
                    exc.reason,
                    tuple(attempts),
                ) from exc
            except ModelResponseError as timeout_error:
                if timeout_error.error_code != "total_timeout":
                    raise
                timeout_error.request_id = attempt_request.request_id
                timeout_error.attempt_id = attempt_request.attempt_id
                attempts[-1] = replace(
                    attempts[-1],
                    retry_delay_seconds=clock() - wait_started,
                    reason="total_timeout_exceeded",
                )
                raise ModelExecutionError(
                    timeout_error,
                    tuple(attempts),
                ) from timeout_error
            actual_delay = clock() - wait_started
            attempts[-1] = replace(
                attempts[-1],
                retry_delay_seconds=actual_delay,
            )
            retry_delay_spent += actual_delay
            attempt_number += 1
            continue

        attempts.append(
            ModelAttemptRecord(
                request_id=attempt_request.request_id,
                attempt_id=attempt_request.attempt_id,
                attempt_number=attempt_number,
                model_name=attempt_request.model_name,
                mode=mode,
                outcome="succeeded",
                duration_seconds=clock() - attempt_started,
                response_id=response.response_id,
                usage=response.usage,
                ttft_seconds=ttft_seconds if mode == "stream" else None,
                reason=fallback_reason,
            )
        )
        if cancellation_token is not None:
            try:
                cancellation_token.raise_if_cancelled()
            except RunCancelledError as exc:
                raise ModelCancelledError(
                    exc.run_id,
                    exc.reason,
                    tuple(attempts),
                ) from exc
        return ModelExecution(response=response, attempts=tuple(attempts))
