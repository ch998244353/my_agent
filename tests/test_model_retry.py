from __future__ import annotations

import asyncio
import math
import unittest

from agents.contracts import ChatMessage, ModelRequest, ModelResponse
from agents.model_events import (
    ReasoningDelta,
    ReasoningDone,
    RefusalDelta,
    RefusalDone,
    ResponseCompleted,
    ResponseCreated,
    TextDelta,
    TextDone,
    ToolArgumentsDelta,
    ToolArgumentsDone,
    ToolCallStarted,
    UsageUpdated,
)
from agents.model_retry import (
    ModelAttemptNotice,
    ModelAttemptRecord,
    ModelCancelledError,
    ModelExecution,
    ModelExecutionError,
    RetryAction,
    RetryPolicy,
    compute_retry_delay,
    parse_retry_after,
    run_model_request,
)
from agents.model_settings import (
    DEFAULT_BACKOFF_INITIAL,
    DEFAULT_BACKOFF_MAX,
    DEFAULT_JITTER_RATIO,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BUDGET,
    DEFAULT_TOTAL_TIMEOUT,
    ModelSettings,
)
from agents.models import ModelErrorKind, ModelResponseError
from agents.events import RunCancelledError
from agents.run_control import CancellationToken


def _request(settings: ModelSettings | None = None) -> ModelRequest:
    return ModelRequest(
        model_name="model-a",
        messages=(ChatMessage(role="user", content="hello"),),
        tool_specs=(),
        model_settings=settings or ModelSettings(),
        request_id="request-1",
        attempt_id="base-attempt",
    )


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _ScriptedStreamModel:
    def __init__(
        self,
        *scripts: str | BaseException,
        max_buffer_chars: int = 100,
    ) -> None:
        self.scripts = list(scripts)
        self.max_buffer_chars = max_buffer_chars
        self.requests: list[ModelRequest] = []
        self.closed_attempt_ids: list[str] = []

    async def stream_response(self, request: ModelRequest):
        self.requests.append(request)
        script = self.scripts.pop(0)
        try:
            if isinstance(script, BaseException):
                raise script
            response_id = f"response-{len(self.requests)}"
            yield ResponseCreated(
                attempt_id=request.attempt_id,
                sequence=1,
                response_id=response_id,
            )
            if script:
                yield TextDelta(
                    attempt_id=request.attempt_id,
                    sequence=2,
                    output_index=0,
                    content_index=0,
                    delta=script,
                )
                yield TextDone(
                    attempt_id=request.attempt_id,
                    sequence=3,
                    output_index=0,
                    content_index=0,
                    text=script,
                )
            yield ResponseCompleted(
                attempt_id=request.attempt_id,
                sequence=4 if script else 2,
                response_id=response_id,
                output=[],
                request_id=request.request_id,
            )
        finally:
            self.closed_attempt_ids.append(request.attempt_id)


class _SyncModel:
    def get_response(
        self,
        messages,
        tool_specs,
        model_settings=None,
    ) -> ModelResponse:
        return ModelResponse(
            response_id="sync-response",
            output=[],
            output_text="sync",
            tool_calls=[],
        )


class _FallbackModel(_ScriptedStreamModel):
    def __init__(
        self,
        stream_error: BaseException,
        fallback_result: ModelResponse | BaseException,
    ) -> None:
        super().__init__(stream_error)
        self.fallback_result = fallback_result
        self.non_stream_requests: list[ModelRequest] = []

    async def get_response_for_request(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        self.non_stream_requests.append(request)
        if isinstance(self.fallback_result, BaseException):
            raise self.fallback_result
        return self.fallback_result


class RetryPolicyTests(unittest.TestCase):
    def test_uses_effective_defaults_without_changing_settings(self) -> None:
        settings = ModelSettings()

        policy = RetryPolicy(settings)

        self.assertEqual(policy.max_attempts, DEFAULT_MAX_ATTEMPTS)
        self.assertEqual(policy.backoff_initial, DEFAULT_BACKOFF_INITIAL)
        self.assertEqual(policy.backoff_max, DEFAULT_BACKOFF_MAX)
        self.assertEqual(policy.jitter_ratio, DEFAULT_JITTER_RATIO)
        self.assertEqual(policy.retry_budget, DEFAULT_RETRY_BUDGET)
        self.assertEqual(policy.total_timeout, DEFAULT_TOTAL_TIMEOUT)
        self.assertIsNone(settings.max_attempts)
        self.assertIsNone(settings.backoff_initial)
        self.assertIsNone(settings.backoff_max)
        self.assertIsNone(settings.jitter_ratio)
        self.assertIsNone(settings.retry_budget)
        self.assertIsNone(settings.total_timeout)

    def test_retries_only_retryable_error_kinds(self) -> None:
        policy = RetryPolicy(ModelSettings())
        retryable_kinds = (
            ModelErrorKind.TRANSPORT,
            ModelErrorKind.RATE_LIMIT,
            ModelErrorKind.SERVER,
            ModelErrorKind.EARLY_CLOSE,
        )

        for kind in retryable_kinds:
            with self.subTest(kind=kind):
                decision = policy.decide(
                    ModelResponseError("failed", kind=kind),
                    attempt_number=1,
                    retry_delay_spent=0,
                )
                self.assertEqual(decision.action, RetryAction.RETRY)
                self.assertEqual(decision.reason, "retryable_error")

    def test_non_retryable_reason_is_not_hidden_by_limits(self) -> None:
        policy = RetryPolicy(ModelSettings())
        non_retryable_kinds = (
            ModelErrorKind.AUTHENTICATION,
            ModelErrorKind.INVALID_REQUEST,
            ModelErrorKind.CONTEXT_WINDOW,
            ModelErrorKind.QUOTA,
            ModelErrorKind.STREAM_UNAVAILABLE,
            ModelErrorKind.PROTOCOL,
            ModelErrorKind.BUFFER_OVERFLOW,
            ModelErrorKind.CANCELLED,
            None,
        )

        for kind in non_retryable_kinds:
            with self.subTest(kind=kind):
                decision = policy.decide(
                    ModelResponseError("failed", kind=kind),
                    attempt_number=DEFAULT_MAX_ATTEMPTS,
                    retry_delay_spent=DEFAULT_RETRY_BUDGET,
                    model_fallback_allowed=True,
                )
                self.assertEqual(decision.action, RetryAction.FAIL)
                self.assertEqual(decision.reason, "non_retryable_error")

    def test_stream_unavailable_uses_explicit_non_stream_fallback(self) -> None:
        policy = RetryPolicy(
            ModelSettings(
                max_attempts=1,
                retry_budget=0,
                allow_non_stream_fallback=True,
            )
        )

        decision = policy.decide(
            ModelResponseError(
                "streaming is unavailable",
                kind=ModelErrorKind.STREAM_UNAVAILABLE,
            ),
            attempt_number=1,
            retry_delay_spent=0,
        )

        self.assertEqual(decision.action, RetryAction.NON_STREAM_FALLBACK)
        self.assertEqual(decision.reason, "non_stream_fallback")

    def test_stops_retryable_error_at_attempt_limit(self) -> None:
        policy = RetryPolicy(ModelSettings(max_attempts=2))

        decision = policy.decide(
            ModelResponseError("failed", kind=ModelErrorKind.SERVER),
            attempt_number=2,
            retry_delay_spent=0,
        )

        self.assertEqual(decision.action, RetryAction.FAIL)
        self.assertEqual(decision.reason, "max_attempts_reached")

    def test_stops_retryable_error_when_budget_is_exhausted(self) -> None:
        policy = RetryPolicy(ModelSettings(retry_budget=5))

        decision = policy.decide(
            ModelResponseError("failed", kind=ModelErrorKind.RATE_LIMIT),
            attempt_number=1,
            retry_delay_spent=5,
        )

        self.assertEqual(decision.action, RetryAction.FAIL)
        self.assertEqual(decision.reason, "retry_budget_exhausted")

    def test_uses_model_fallback_only_after_rate_or_server_limit(self) -> None:
        policy = RetryPolicy(ModelSettings(max_attempts=2, retry_budget=5))

        for kind in (ModelErrorKind.RATE_LIMIT, ModelErrorKind.SERVER):
            with self.subTest(kind=kind, limit="attempts"):
                decision = policy.decide(
                    ModelResponseError("failed", kind=kind),
                    attempt_number=2,
                    retry_delay_spent=0,
                    model_fallback_allowed=True,
                )
                self.assertEqual(decision.action, RetryAction.MODEL_FALLBACK)
                self.assertEqual(decision.reason, "model_fallback")

            with self.subTest(kind=kind, limit="budget"):
                decision = policy.decide(
                    ModelResponseError("failed", kind=kind),
                    attempt_number=1,
                    retry_delay_spent=5,
                    model_fallback_allowed=True,
                )
                self.assertEqual(decision.action, RetryAction.MODEL_FALLBACK)

            with self.subTest(kind=kind, limit="not_exhausted"):
                decision = policy.decide(
                    ModelResponseError("failed", kind=kind),
                    attempt_number=1,
                    retry_delay_spent=0,
                    model_fallback_allowed=True,
                )
                self.assertEqual(decision.action, RetryAction.RETRY)

        for kind in (ModelErrorKind.TRANSPORT, ModelErrorKind.EARLY_CLOSE):
            with self.subTest(kind=kind):
                decision = policy.decide(
                    ModelResponseError("failed", kind=kind),
                    attempt_number=2,
                    retry_delay_spent=5,
                    model_fallback_allowed=True,
                )
                self.assertEqual(decision.action, RetryAction.FAIL)

    def test_rejects_invalid_inputs(self) -> None:
        policy = RetryPolicy(ModelSettings())
        error = ModelResponseError("failed", kind=ModelErrorKind.TRANSPORT)

        with self.assertRaises(TypeError):
            RetryPolicy(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            policy.decide(
                RuntimeError("failed"),  # type: ignore[arg-type]
                attempt_number=1,
                retry_delay_spent=0,
            )

        for attempt_number in (0, True, 1.5):
            with self.subTest(attempt_number=attempt_number):
                with self.assertRaises(ValueError):
                    policy.decide(
                        error,
                        attempt_number=attempt_number,  # type: ignore[arg-type]
                        retry_delay_spent=0,
                    )

        for retry_delay_spent in (-1, True, math.inf, math.nan, "0"):
            with self.subTest(retry_delay_spent=retry_delay_spent):
                with self.assertRaises(ValueError):
                    policy.decide(
                        error,
                        attempt_number=1,
                        retry_delay_spent=retry_delay_spent,  # type: ignore[arg-type]
                    )

        with self.assertRaises(TypeError):
            policy.decide(
                error,
                attempt_number=1,
                retry_delay_spent=0,
                model_fallback_allowed=1,  # type: ignore[arg-type]
            )


class RetryDelayTests(unittest.TestCase):
    def test_parses_retry_after_numbers_and_http_dates(self) -> None:
        now_timestamp = 1_445_412_470
        cases = (
            (None, None),
            (3, 3.0),
            ("2.5", 2.5),
            ("Wed, 21 Oct 2015 07:28:00 GMT", 10.0),
            ("Wed, 21 Oct 2015 07:27:00 GMT", 0.0),
            ("not-a-delay", None),
            (-1, None),
            ("inf", None),
            (True, None),
        )

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    parse_retry_after(
                        value,  # type: ignore[arg-type]
                        now_timestamp=now_timestamp,
                    ),
                    expected,
                )

        for now_timestamp in (True, math.inf, math.nan, "0"):
            with self.subTest(now_timestamp=now_timestamp):
                with self.assertRaises(ValueError):
                    parse_retry_after(
                        "1",
                        now_timestamp=now_timestamp,  # type: ignore[arg-type]
                    )

    def test_computes_capped_exponential_backoff(self) -> None:
        policy = RetryPolicy(
            ModelSettings(
                max_attempts=1_000_001,
                backoff_initial=1,
                backoff_max=4,
                jitter_ratio=0,
                retry_budget=30,
                total_timeout=40,
            )
        )
        error = ModelResponseError("failed", kind=ModelErrorKind.SERVER)

        for attempt_number, expected in ((1, 1), (2, 2), (3, 4), (4, 4)):
            with self.subTest(attempt_number=attempt_number):
                decision = compute_retry_delay(
                    policy,
                    error,
                    attempt_number=attempt_number,
                    retry_delay_spent=0,
                    elapsed=0,
                    now_timestamp=0,
                    random_fn=lambda: 0.5,
                )
                self.assertEqual(decision.delay_seconds, expected)
                self.assertEqual(decision.reason, "retry_scheduled")

        overflow_safe = compute_retry_delay(
            policy,
            error,
            attempt_number=1_000_000,
            retry_delay_spent=0,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 0.5,
        )
        self.assertEqual(overflow_safe.delay_seconds, 4)

    def test_applies_symmetric_jitter_then_caps(self) -> None:
        policy = RetryPolicy(
            ModelSettings(
                max_attempts=4,
                backoff_initial=10,
                backoff_max=20,
                jitter_ratio=0.2,
                retry_budget=50,
                total_timeout=100,
            )
        )
        error = ModelResponseError("failed", kind=ModelErrorKind.TRANSPORT)

        low = compute_retry_delay(
            policy,
            error,
            attempt_number=1,
            retry_delay_spent=0,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 0,
        )
        high = compute_retry_delay(
            policy,
            error,
            attempt_number=1,
            retry_delay_spent=0,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 1,
        )
        capped = compute_retry_delay(
            policy,
            error,
            attempt_number=2,
            retry_delay_spent=0,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 1,
        )

        self.assertEqual(low.delay_seconds, 8)
        self.assertAlmostEqual(high.delay_seconds or 0, 12)
        self.assertEqual(capped.delay_seconds, 20)

    def test_retry_after_bypasses_backoff_cap_and_jitter(self) -> None:
        policy = RetryPolicy(
            ModelSettings(
                max_attempts=3,
                backoff_initial=1,
                backoff_max=2,
                jitter_ratio=1,
                retry_budget=20,
                total_timeout=30,
            )
        )
        error = ModelResponseError(
            "failed",
            kind=ModelErrorKind.RATE_LIMIT,
            retry_after="7",
        )

        def unexpected_random() -> float:
            raise AssertionError("random_fn must not be called for Retry-After")

        decision = compute_retry_delay(
            policy,
            error,
            attempt_number=1,
            retry_delay_spent=0,
            elapsed=0,
            now_timestamp=0,
            random_fn=unexpected_random,
        )

        self.assertEqual(decision.delay_seconds, 7)
        self.assertEqual(decision.reason, "retry_scheduled")

    def test_rejects_delays_that_cross_retry_or_total_budget(self) -> None:
        policy = RetryPolicy(
            ModelSettings(
                max_attempts=3,
                backoff_initial=2,
                backoff_max=2,
                jitter_ratio=0,
                retry_budget=5,
                total_timeout=10,
            )
        )
        error = ModelResponseError("failed", kind=ModelErrorKind.SERVER)

        exactly_budgeted = compute_retry_delay(
            policy,
            error,
            attempt_number=1,
            retry_delay_spent=3,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 0.5,
        )
        retry_budget_exceeded = compute_retry_delay(
            policy,
            error,
            attempt_number=1,
            retry_delay_spent=3.1,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 0.5,
        )
        total_timeout_exceeded = compute_retry_delay(
            policy,
            error,
            attempt_number=1,
            retry_delay_spent=0,
            elapsed=8,
            now_timestamp=0,
            random_fn=lambda: 0.5,
        )

        self.assertEqual(exactly_budgeted.delay_seconds, 2)
        self.assertEqual(retry_budget_exceeded.delay_seconds, None)
        self.assertEqual(retry_budget_exceeded.reason, "retry_budget_exceeded")
        self.assertEqual(total_timeout_exceeded.delay_seconds, None)
        self.assertEqual(total_timeout_exceeded.reason, "total_timeout_exceeded")

    def test_preserves_policy_failure_and_rejects_invalid_random_values(self) -> None:
        exhausted_policy = RetryPolicy(ModelSettings(max_attempts=1))
        error = ModelResponseError("failed", kind=ModelErrorKind.SERVER)

        exhausted = compute_retry_delay(
            exhausted_policy,
            error,
            attempt_number=1,
            retry_delay_spent=0,
            elapsed=0,
            now_timestamp=0,
            random_fn=lambda: 0.5,
        )
        self.assertEqual(exhausted.delay_seconds, None)
        self.assertEqual(exhausted.reason, "max_attempts_reached")

        jitter_policy = RetryPolicy(
            ModelSettings(
                backoff_initial=1,
                backoff_max=2,
                jitter_ratio=0.5,
            )
        )
        for random_value in (-0.1, 1.1, math.inf, math.nan, True, "0"):
            with self.subTest(random_value=random_value):
                with self.assertRaises(ValueError):
                    compute_retry_delay(
                        jitter_policy,
                        error,
                        attempt_number=1,
                        retry_delay_spent=0,
                        elapsed=0,
                        now_timestamp=0,
                        random_fn=lambda value=random_value: value,
                    )


class ModelExecutionContractTests(unittest.TestCase):
    @staticmethod
    def _record(outcome: str) -> ModelAttemptRecord:
        return ModelAttemptRecord(
            request_id="req_1",
            attempt_id=f"attempt_{outcome}",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
            outcome=outcome,  # type: ignore[arg-type]
            duration_seconds=0.25,
            error_kind=(
                ModelErrorKind.SERVER if outcome == "failed" else None
            ),
            status_code=503 if outcome == "failed" else None,
            retry_delay_seconds=1 if outcome == "failed" else None,
            reason="server_error" if outcome == "failed" else None,
        )

    def test_validates_attempt_records_and_notices(self) -> None:
        record = self._record("failed")
        notice = ModelAttemptNotice(
            notice_type="retry_scheduled",
            request_id=record.request_id,
            attempt_id=record.attempt_id,
            attempt_number=record.attempt_number,
            model_name=record.model_name,
            mode=record.mode,
            reason="retry_scheduled",
            delay_seconds=1,
            error_kind=ModelErrorKind.SERVER,
        )

        self.assertEqual(record.outcome, "failed")
        self.assertEqual(notice.delay_seconds, 1)
        self.assertIs(notice.error_kind, ModelErrorKind.SERVER)
        self.assertIsNone(notice.previous_model_name)

        invalid_records = (
            {"request_id": ""},
            {"attempt_number": 0},
            {"mode": "batch"},
            {"outcome": "unknown"},
            {"duration_seconds": math.inf},
            {"status_code": 600},
            {"retry_delay_seconds": -1},
            {"reason": ""},
        )
        base_record = {
            "request_id": "req_1",
            "attempt_id": "attempt_1",
            "attempt_number": 1,
            "model_name": "model-a",
            "mode": "stream",
            "outcome": "failed",
            "duration_seconds": 0.25,
        }
        for changes in invalid_records:
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    ModelAttemptRecord(  # type: ignore[arg-type]
                        **(base_record | changes)
                    )

        with self.assertRaises(ValueError):
            ModelAttemptNotice(
                notice_type="unknown",  # type: ignore[arg-type]
                request_id="req_1",
                attempt_id="attempt_1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
            )
        with self.assertRaises(TypeError):
            ModelAttemptNotice(
                notice_type="retry_scheduled",
                request_id="req_1",
                attempt_id="attempt_1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                error_kind="transport",  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            ModelAttemptNotice(
                notice_type="model_fallback_started",
                request_id="req_1",
                attempt_id="attempt_2",
                attempt_number=2,
                model_name="model-b",
                mode="stream",
                previous_model_name=" ",
            )
        with self.assertRaises(ValueError):
            ModelAttemptNotice(
                notice_type="model_fallback_started",
                request_id="req_1",
                attempt_id="attempt_2",
                attempt_number=2,
                model_name="model-b",
                mode="stream",
                previous_model_name="model-a",
                previous_mode="batch",  # type: ignore[arg-type]
            )

    def test_model_execution_requires_a_successful_final_attempt(self) -> None:
        succeeded = self._record("succeeded")
        response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text="done",
            tool_calls=[],
        )

        execution = ModelExecution(
            response=response,
            attempts=[succeeded],  # type: ignore[arg-type]
        )

        self.assertEqual(execution.response, response)
        self.assertEqual(execution.attempts, (succeeded,))
        with self.assertRaises(ValueError):
            ModelExecution(response=response, attempts=())
        with self.assertRaises(ValueError):
            ModelExecution(response=response, attempts=(self._record("failed"),))

    def test_success_metadata_is_validated_and_usage_is_copied(self) -> None:
        usage = {"input_tokens": 3}
        record = ModelAttemptRecord(
            request_id="req_1",
            attempt_id="attempt_1",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
            outcome="succeeded",
            duration_seconds=0.5,
            response_id="resp_1",
            usage=usage,
            ttft_seconds=0.25,
        )

        usage["input_tokens"] = 99

        self.assertEqual(record.response_id, "resp_1")
        self.assertEqual(record.usage, {"input_tokens": 3})
        self.assertEqual(record.ttft_seconds, 0.25)
        for changes in (
            {"response_id": ""},
            {"usage": []},
            {"ttft_seconds": -1},
            {"ttft_seconds": math.inf},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    ModelAttemptRecord(  # type: ignore[arg-type]
                        request_id="req_1",
                        attempt_id="attempt_1",
                        attempt_number=1,
                        model_name="model-a",
                        mode="stream",
                        outcome="succeeded",
                        duration_seconds=0.5,
                        **changes,
                    )

    def test_model_execution_error_preserves_last_error_and_failed_attempts(
        self,
    ) -> None:
        original = RuntimeError("provider failed")
        last_error = ModelResponseError(
            "model failed",
            kind=ModelErrorKind.SERVER,
            status_code=503,
            error_code="server_error",
            retry_after="2",
            request_id="req_1",
            attempt_id="attempt_failed",
            original=original,
        )
        failed = self._record("failed")

        execution_error = ModelExecutionError(
            last_error,
            [failed],  # type: ignore[arg-type]
        )

        self.assertIs(execution_error.last_error, last_error)
        self.assertEqual(execution_error.attempts, (failed,))
        self.assertEqual(execution_error.kind, last_error.kind)
        self.assertEqual(execution_error.status_code, last_error.status_code)
        self.assertEqual(execution_error.error_code, last_error.error_code)
        self.assertEqual(execution_error.retry_after, last_error.retry_after)
        self.assertEqual(execution_error.request_id, last_error.request_id)
        self.assertEqual(execution_error.attempt_id, last_error.attempt_id)
        self.assertIs(execution_error.original, original)

        with self.assertRaises(ValueError):
            ModelExecutionError(last_error, ())
        with self.assertRaises(ValueError):
            ModelExecutionError(last_error, (self._record("succeeded"),))


class ModelRequestExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ttft_uses_the_first_meaningful_stream_event(self) -> None:
        class TimedEventModel:
            def __init__(self, event_kind: str, clock: _FakeClock) -> None:
                self.event_kind = event_kind
                self.clock = clock

            async def stream_response(self, request: ModelRequest):
                yield ResponseCreated(
                    attempt_id=request.attempt_id,
                    sequence=1,
                    response_id="response-1",
                )
                self.clock.value = 0.25
                if self.event_kind == "text":
                    yield TextDelta(
                        attempt_id=request.attempt_id,
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="done",
                    )
                    yield TextDone(
                        attempt_id=request.attempt_id,
                        sequence=3,
                        output_index=0,
                        content_index=0,
                        text="done",
                    )
                elif self.event_kind == "reasoning":
                    yield ReasoningDelta(
                        attempt_id=request.attempt_id,
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="think",
                    )
                    yield ReasoningDone(
                        attempt_id=request.attempt_id,
                        sequence=3,
                        output_index=0,
                        content_index=0,
                        text="think",
                    )
                elif self.event_kind == "refusal":
                    yield RefusalDelta(
                        attempt_id=request.attempt_id,
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="no",
                    )
                    yield RefusalDone(
                        attempt_id=request.attempt_id,
                        sequence=3,
                        output_index=0,
                        content_index=0,
                        text="no",
                    )
                else:
                    yield ToolCallStarted(
                        attempt_id=request.attempt_id,
                        sequence=2,
                        output_index=0,
                        item_id="item-1",
                        call_id="call-1",
                        tool_name="echo_text",
                    )
                    yield ToolArgumentsDelta(
                        attempt_id=request.attempt_id,
                        sequence=3,
                        output_index=0,
                        item_id="item-1",
                        delta='{"text":"done"}',
                    )
                    yield ToolArgumentsDone(
                        attempt_id=request.attempt_id,
                        sequence=4,
                        output_index=0,
                        item_id="item-1",
                        arguments='{"text":"done"}',
                    )
                sequence = 5 if self.event_kind == "tool" else 4
                yield UsageUpdated(
                    attempt_id=request.attempt_id,
                    sequence=sequence,
                    usage={"total_tokens": 7},
                )
                yield ResponseCompleted(
                    attempt_id=request.attempt_id,
                    sequence=sequence + 1,
                    response_id="response-1",
                    output=[],
                    request_id=request.request_id,
                )

        for event_kind in ("text", "reasoning", "refusal", "tool"):
            with self.subTest(event_kind=event_kind):
                clock = _FakeClock()
                execution = await run_model_request(
                    TimedEventModel(event_kind, clock),
                    _request(),
                    clock=clock,
                )
                record = execution.attempts[0]

                self.assertEqual(record.ttft_seconds, 0.25)
                self.assertEqual(record.response_id, "response-1")
                self.assertEqual(record.usage, {"total_tokens": 7})

    async def test_created_and_completed_only_has_no_ttft(self) -> None:
        execution = await run_model_request(_ScriptedStreamModel(""), _request())

        self.assertIsNone(execution.attempts[0].ttft_seconds)

    async def test_first_native_attempt_succeeds_without_changing_base_request(
        self,
    ) -> None:
        model = _ScriptedStreamModel("done")
        request = _request()
        notices: list[ModelAttemptNotice] = []

        execution = await run_model_request(
            model,
            request,
            on_notice=notices.append,
        )

        self.assertEqual(execution.response.output_text, "done")
        self.assertEqual(len(execution.attempts), 1)
        self.assertEqual(execution.attempts[0].outcome, "succeeded")
        self.assertEqual(execution.attempts[0].mode, "stream")
        self.assertEqual(execution.attempts[0].response_id, "response-1")
        self.assertEqual(
            execution.attempts[0].attempt_id,
            "request-1:attempt:1",
        )
        self.assertEqual(request.attempt_id, "base-attempt")
        self.assertEqual(model.closed_attempt_ids, ["request-1:attempt:1"])
        self.assertEqual(
            [notice.notice_type for notice in notices],
            ["attempt_started"],
        )

    async def test_retry_waits_once_then_returns_second_attempt(self) -> None:
        model = _ScriptedStreamModel(TimeoutError("network timeout"), "done")
        clock = _FakeClock()
        notices: list[ModelAttemptNotice] = []
        request = _request(
            ModelSettings(
                max_attempts=2,
                backoff_initial=1,
                backoff_max=1,
                jitter_ratio=0,
                retry_budget=2,
                total_timeout=10,
            )
        )

        execution = await run_model_request(
            model,
            request,
            on_notice=notices.append,
            clock=clock,
            wall_clock=lambda: 0,
            sleep=clock.sleep,
            random_fn=lambda: 0.5,
        )

        self.assertEqual(clock.sleeps, [1])
        self.assertEqual(
            [record.outcome for record in execution.attempts],
            ["failed", "succeeded"],
        )
        self.assertEqual(execution.attempts[0].error_kind, ModelErrorKind.TRANSPORT)
        self.assertEqual(execution.attempts[0].retry_delay_seconds, 1)
        self.assertEqual(execution.attempts[0].reason, "retry_scheduled")
        self.assertIsNone(execution.attempts[0].response_id)
        self.assertIsNone(execution.attempts[0].usage)
        self.assertIsNone(execution.attempts[0].ttft_seconds)
        self.assertEqual(execution.attempts[1].response_id, "response-2")
        self.assertEqual(
            [record.attempt_id for record in execution.attempts],
            ["request-1:attempt:1", "request-1:attempt:2"],
        )
        self.assertEqual(
            [notice.notice_type for notice in notices],
            ["attempt_started", "retry_scheduled", "attempt_started"],
        )
        self.assertIs(notices[1].error_kind, ModelErrorKind.TRANSPORT)

    async def test_non_retryable_error_keeps_identity_and_notice_errors_are_ignored(
        self,
    ) -> None:
        provider_error = ModelResponseError(
            "bad key",
            kind=ModelErrorKind.AUTHENTICATION,
            status_code=401,
        )
        model = _ScriptedStreamModel(provider_error)

        def broken_notice_sink(notice: ModelAttemptNotice) -> None:
            raise RuntimeError("observer failed")

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(
                model,
                _request(),
                on_notice=broken_notice_sink,
            )

        error = caught.exception
        self.assertIs(error.last_error, provider_error)
        self.assertEqual(error.request_id, "request-1")
        self.assertEqual(error.attempt_id, "request-1:attempt:1")
        self.assertEqual(error.attempts[0].status_code, 401)
        self.assertEqual(error.attempts[0].reason, "non_retryable_error")

    async def test_retry_limits_stop_before_another_attempt(self) -> None:
        cases = (
            (
                ModelSettings(
                    max_attempts=2,
                    backoff_initial=1,
                    backoff_max=1,
                    jitter_ratio=0,
                    retry_budget=3,
                    total_timeout=10,
                ),
                (TimeoutError("one"), TimeoutError("two")),
                "max_attempts_reached",
                [1],
                2,
            ),
            (
                ModelSettings(
                    max_attempts=3,
                    backoff_initial=2,
                    backoff_max=2,
                    jitter_ratio=0,
                    retry_budget=1,
                    total_timeout=10,
                ),
                (TimeoutError("one"),),
                "retry_budget_exceeded",
                [],
                1,
            ),
            (
                ModelSettings(
                    max_attempts=3,
                    backoff_initial=2,
                    backoff_max=2,
                    jitter_ratio=0,
                    retry_budget=2,
                    total_timeout=2,
                ),
                (TimeoutError("one"),),
                "total_timeout_exceeded",
                [],
                1,
            ),
        )

        for settings, scripts, reason, sleeps, request_count in cases:
            with self.subTest(reason=reason):
                model = _ScriptedStreamModel(*scripts)
                clock = _FakeClock()
                with self.assertRaises(ModelExecutionError) as caught:
                    await run_model_request(
                        model,
                        _request(settings),
                        clock=clock,
                        wall_clock=lambda: 0,
                        sleep=clock.sleep,
                        random_fn=lambda: 0.5,
                    )

                self.assertEqual(caught.exception.attempts[-1].reason, reason)
                self.assertEqual(clock.sleeps, sleeps)
                self.assertEqual(len(model.requests), request_count)

    async def test_rate_limit_exhaustion_uses_one_backup_model_attempt(self) -> None:
        model = _ScriptedStreamModel(
            ModelResponseError(
                "rate limited",
                kind=ModelErrorKind.RATE_LIMIT,
                status_code=429,
            ),
            "backup answer",
        )
        notices: list[ModelAttemptNotice] = []

        execution = await run_model_request(
            model,
            _request(
                ModelSettings(
                    max_attempts=1,
                    fallback_model="model-b",
                )
            ),
            on_notice=notices.append,
        )

        self.assertEqual(execution.response.output_text, "backup answer")
        self.assertEqual(
            [record.model_name for record in execution.attempts],
            ["model-a", "model-b"],
        )
        self.assertEqual(
            [record.request_id for record in execution.attempts],
            ["request-1", "request-1"],
        )
        self.assertEqual(
            [record.attempt_id for record in execution.attempts],
            ["request-1:attempt:1", "request-1:attempt:2"],
        )
        self.assertEqual(
            [record.reason for record in execution.attempts],
            ["model_fallback", "model_fallback"],
        )
        self.assertEqual(
            [notice.notice_type for notice in notices],
            [
                "attempt_started",
                "model_fallback_started",
                "attempt_started",
            ],
        )
        self.assertEqual(notices[1].reason, "model_fallback")
        self.assertEqual(notices[1].previous_model_name, "model-a")
        self.assertEqual(notices[1].previous_mode, "stream")
        self.assertEqual(notices[1].mode, "stream")
        self.assertEqual(notices[1].attempt_id, execution.attempts[1].attempt_id)
        self.assertEqual(
            [request.model_name for request in model.requests],
            ["model-a", "model-b"],
        )

    async def test_backup_model_failure_ends_without_retrying(self) -> None:
        model = _ScriptedStreamModel(
            ModelResponseError("server failed", kind=ModelErrorKind.SERVER),
            TimeoutError("backup timed out"),
            "must not run",
        )
        clock = _FakeClock()

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(
                model,
                _request(
                    ModelSettings(
                        max_attempts=5,
                        retry_budget=0,
                        fallback_model="model-b",
                    )
                ),
                clock=clock,
                sleep=clock.sleep,
            )

        self.assertEqual(len(model.requests), 2)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(caught.exception.last_error.kind, ModelErrorKind.TRANSPORT)
        self.assertEqual(
            [record.model_name for record in caught.exception.attempts],
            ["model-a", "model-b"],
        )
        self.assertEqual(
            caught.exception.attempts[-1].reason,
            "model_fallback",
        )

    async def test_same_model_name_does_not_start_model_fallback(self) -> None:
        model = _ScriptedStreamModel(
            ModelResponseError("server failed", kind=ModelErrorKind.SERVER),
        )

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(
                model,
                _request(
                    ModelSettings(
                        max_attempts=1,
                        fallback_model="model-a",
                    )
                ),
            )

        self.assertEqual(len(model.requests), 1)
        self.assertEqual(
            caught.exception.attempts[-1].reason,
            "max_attempts_reached",
        )

    async def test_request_buffer_limit_wins_and_closes_native_stream(self) -> None:
        model = _ScriptedStreamModel("too long", max_buffer_chars=100)
        request = _request(
            ModelSettings(
                max_stream_buffer_chars=3,
            )
        )

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(model, request)

        self.assertEqual(
            caught.exception.last_error.kind,
            ModelErrorKind.BUFFER_OVERFLOW,
        )
        self.assertEqual(model.closed_attempt_ids, ["request-1:attempt:1"])

    async def test_sync_model_uses_same_events_with_non_stream_mode(self) -> None:
        execution = await run_model_request(_SyncModel(), _request())

        self.assertEqual(execution.response.output_text, "sync")
        self.assertEqual(execution.attempts[0].mode, "non_stream")
        self.assertIsNone(execution.attempts[0].ttft_seconds)

    async def test_stream_unavailable_falls_back_once_without_retry_wait(self) -> None:
        stream_error = ModelResponseError(
            "streaming is unavailable",
            kind=ModelErrorKind.STREAM_UNAVAILABLE,
        )
        model = _FallbackModel(
            stream_error,
            ModelResponse(
                response_id="fallback-response",
                output=[],
                output_text="fallback",
                tool_calls=[],
            ),
        )
        notices: list[ModelAttemptNotice] = []
        clock = _FakeClock()

        execution = await run_model_request(
            model,
            _request(ModelSettings(allow_non_stream_fallback=True)),
            on_notice=notices.append,
            clock=clock,
            sleep=clock.sleep,
        )

        self.assertEqual(execution.response.output_text, "fallback")
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(
            [record.mode for record in execution.attempts],
            ["stream", "non_stream"],
        )
        self.assertEqual(
            [record.reason for record in execution.attempts],
            ["non_stream_fallback", "non_stream_fallback"],
        )
        self.assertEqual(
            [record.attempt_id for record in execution.attempts],
            ["request-1:attempt:1", "request-1:attempt:2"],
        )
        self.assertEqual(len(model.non_stream_requests), 1)
        self.assertEqual(model.non_stream_requests[0].model_name, "model-a")
        self.assertEqual(
            [notice.notice_type for notice in notices],
            [
                "attempt_started",
                "model_fallback_started",
                "attempt_started",
            ],
        )
        self.assertEqual(notices[1].reason, "non_stream_fallback")
        self.assertEqual(notices[1].previous_model_name, "model-a")
        self.assertEqual(notices[1].model_name, "model-a")
        self.assertEqual(notices[1].previous_mode, "stream")
        self.assertEqual(notices[1].mode, "non_stream")
        self.assertEqual(notices[1].attempt_id, execution.attempts[1].attempt_id)

    async def test_non_stream_fallback_is_disabled_by_default(self) -> None:
        model = _FallbackModel(
            ModelResponseError(
                "streaming is unavailable",
                kind=ModelErrorKind.STREAM_UNAVAILABLE,
            ),
            ModelResponse(
                response_id="unused",
                output=[],
                output_text="unused",
                tool_calls=[],
            ),
        )

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(model, _request())

        self.assertEqual(caught.exception.attempts[-1].reason, "non_retryable_error")
        self.assertEqual(model.non_stream_requests, [])

    async def test_non_stream_fallback_failure_ends_without_retrying(self) -> None:
        model = _FallbackModel(
            ModelResponseError(
                "streaming is unavailable",
                kind=ModelErrorKind.STREAM_UNAVAILABLE,
            ),
            TimeoutError("fallback timed out"),
        )
        clock = _FakeClock()

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(
                model,
                _request(
                    ModelSettings(
                        max_attempts=5,
                        allow_non_stream_fallback=True,
                        fallback_model="model-b",
                    )
                ),
                clock=clock,
                sleep=clock.sleep,
            )

        self.assertEqual(
            [record.mode for record in caught.exception.attempts],
            ["stream", "non_stream"],
        )
        self.assertEqual(
            caught.exception.attempts[-1].reason,
            "non_stream_fallback",
        )
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(model.non_stream_requests), 1)
        self.assertEqual(clock.sleeps, [])

    async def test_stream_error_after_first_event_does_not_fallback(self) -> None:
        fallback_requests: list[ModelRequest] = []

        class PartialStreamModel:
            async def stream_response(self, request: ModelRequest):
                yield ResponseCreated(
                    attempt_id=request.attempt_id,
                    sequence=1,
                    response_id="partial-response",
                )
                raise ModelResponseError(
                    "stream stopped",
                    kind=ModelErrorKind.STREAM_UNAVAILABLE,
                )

            async def get_response_for_request(
                self,
                request: ModelRequest,
            ) -> ModelResponse:
                fallback_requests.append(request)
                raise AssertionError("partial streams must not fall back")

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(
                PartialStreamModel(),
                _request(
                    ModelSettings(
                        max_attempts=1,
                        allow_non_stream_fallback=True,
                    )
                ),
            )

        self.assertEqual(caught.exception.last_error.kind, ModelErrorKind.EARLY_CLOSE)
        self.assertEqual(fallback_requests, [])

    async def test_total_deadline_can_stop_before_non_stream_fallback(self) -> None:
        clock = _FakeClock()
        fallback_requests: list[ModelRequest] = []

        class DeadlineModel:
            async def stream_response(self, request: ModelRequest):
                clock.value = 1
                if False:
                    yield
                raise ModelResponseError(
                    "streaming is unavailable",
                    kind=ModelErrorKind.STREAM_UNAVAILABLE,
                )

            async def get_response_for_request(
                self,
                request: ModelRequest,
            ) -> ModelResponse:
                fallback_requests.append(request)
                raise AssertionError("expired requests must not fall back")

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(
                DeadlineModel(),
                _request(
                    ModelSettings(
                        total_timeout=1,
                        retry_budget=0,
                        allow_non_stream_fallback=True,
                    )
                ),
                clock=clock,
            )

        self.assertEqual(
            caught.exception.attempts[-1].reason,
            "total_timeout_exceeded",
        )
        self.assertEqual(fallback_requests, [])

    async def test_cancellation_can_stop_before_non_stream_fallback(self) -> None:
        token = CancellationToken("run-1")
        fallback_requests: list[ModelRequest] = []

        class CancellingModel:
            async def stream_response(self, request: ModelRequest):
                token.cancel("user stopped")
                if False:
                    yield
                raise ModelResponseError(
                    "streaming is unavailable",
                    kind=ModelErrorKind.STREAM_UNAVAILABLE,
                )

            async def get_response_for_request(
                self,
                request: ModelRequest,
            ) -> ModelResponse:
                fallback_requests.append(request)
                raise AssertionError("cancelled requests must not fall back")

        with self.assertRaises(ModelCancelledError) as caught:
            await run_model_request(
                CancellingModel(),
                _request(ModelSettings(allow_non_stream_fallback=True)),
                cancellation_token=token,
            )

        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertEqual(caught.exception.attempts[0].outcome, "cancelled")
        self.assertEqual(fallback_requests, [])

    async def test_external_task_cancellation_is_not_classified_as_failure(
        self,
    ) -> None:
        started = asyncio.Event()
        closed = False

        class BlockingModel:
            async def stream_response(self, request: ModelRequest):
                nonlocal closed
                try:
                    yield ResponseCreated(
                        attempt_id=request.attempt_id,
                        sequence=1,
                        response_id="response-blocked",
                    )
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed = True

        task = asyncio.create_task(run_model_request(BlockingModel(), _request()))
        await started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed)

    async def test_cancelled_token_stops_before_first_attempt(self) -> None:
        model = _ScriptedStreamModel("unused")
        token = CancellationToken("run-1")
        token.cancel("user stopped")

        with self.assertRaises(ModelCancelledError) as caught:
            await run_model_request(
                model,
                _request(),
                cancellation_token=token,
            )

        self.assertEqual(caught.exception.run_id, "run-1")
        self.assertEqual(caught.exception.reason, "user stopped")
        self.assertEqual(caught.exception.attempts, ())
        self.assertIsInstance(caught.exception, RunCancelledError)
        self.assertEqual(model.requests, [])

    async def test_token_cancellation_closes_blocked_stream(self) -> None:
        blocked = asyncio.Event()
        closed = False

        class BlockingModel:
            async def stream_response(self, request: ModelRequest):
                nonlocal closed
                try:
                    yield ResponseCreated(
                        attempt_id=request.attempt_id,
                        sequence=1,
                        response_id="response-blocked",
                    )
                    blocked.set()
                    await asyncio.Event().wait()
                finally:
                    closed = True

        token = CancellationToken("run-2")
        task = asyncio.create_task(
            run_model_request(
                BlockingModel(),
                _request(),
                cancellation_token=token,
            )
        )
        await blocked.wait()
        token.cancel("cancel blocked stream")

        with self.assertRaises(ModelCancelledError) as caught:
            await asyncio.wait_for(task, timeout=1)
        self.assertEqual(caught.exception.run_id, "run-2")
        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertEqual(caught.exception.attempts[0].outcome, "cancelled")
        self.assertIsNone(caught.exception.attempts[0].response_id)
        self.assertTrue(closed)

    async def test_token_cancellation_interrupts_backoff(self) -> None:
        model = _ScriptedStreamModel(TimeoutError("network"), "unused")
        sleep_started = asyncio.Event()
        token = CancellationToken("run-3")

        async def blocking_sleep(seconds: float) -> None:
            sleep_started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_model_request(
                model,
                _request(
                    ModelSettings(
                        max_attempts=2,
                        backoff_initial=1,
                        backoff_max=1,
                        jitter_ratio=0,
                        retry_budget=2,
                        total_timeout=10,
                    )
                ),
                cancellation_token=token,
                sleep=blocking_sleep,
            )
        )
        await sleep_started.wait()
        token.cancel("cancel backoff")

        with self.assertRaises(ModelCancelledError) as caught:
            await asyncio.wait_for(task, timeout=1)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertEqual(caught.exception.attempts[0].outcome, "failed")
        self.assertIsNotNone(caught.exception.attempts[0].retry_delay_seconds)
        self.assertLess(caught.exception.attempts[0].retry_delay_seconds, 1)

    async def test_total_timeout_closes_attempt_without_retry(self) -> None:
        started = asyncio.Event()
        closed = False

        class BlockingModel:
            async def stream_response(self, request: ModelRequest):
                nonlocal closed
                try:
                    started.set()
                    await asyncio.Event().wait()
                    yield
                finally:
                    closed = True

        model = BlockingModel()
        request = _request(
            ModelSettings(
                total_timeout=0.02,
                retry_budget=0,
            )
        )

        with self.assertRaises(ModelExecutionError) as caught:
            await run_model_request(model, request)

        self.assertTrue(started.is_set())
        self.assertTrue(closed)
        self.assertEqual(caught.exception.last_error.error_code, "total_timeout")
        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertEqual(
            caught.exception.attempts[0].reason,
            "total_timeout_exceeded",
        )


if __name__ == "__main__":
    unittest.main()
