from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timedelta, timezone
from typing import ClassVar
import json
import unittest

from agents.events import (
    ApprovalRequiredEvent,
    ApprovalResolvedEvent,
    EVENT_SCHEMA_VERSION,
    ModelAttemptStartedEvent,
    ModelCompletedEvent,
    ModelFallbackEvent,
    ModelRetryScheduledEvent,
    ModelStartedEvent,
    RunAlreadyFinishedError,
    RunCancelledError,
    RunCancelledEvent,
    RunCompletedEvent,
    RunErrorEvent,
    RunEvent,
    RunEventReducer,
    RunFailedEvent,
    RunStartedEvent,
    RunStoppedEvent,
    SteerAcceptedEvent,
    SteerAppliedEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolRequestedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    event_to_dict,
    is_terminal_event,
    utc_now,
)


@dataclass(frozen=True, kw_only=True)
class ExampleEvent(RunEvent):
    EVENT_TYPE: ClassVar[str] = "example"

    detail: str


class RunEventTests(unittest.TestCase):
    def valid_event(self, **overrides: object) -> ExampleEvent:
        values = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
            "turn_id": "turn-1",
            "step_number": 0,
            "detail": "started",
        }
        values.update(overrides)
        return ExampleEvent(**values)

    def test_event_exposes_validated_immutable_metadata(self) -> None:
        event = self.valid_event()

        self.assertEqual(event.event_type, "example")
        self.assertEqual(event.schema_version, EVENT_SCHEMA_VERSION)
        with self.assertRaises(FrozenInstanceError):
            event.sequence = 2

    def test_invalid_metadata_is_rejected(self) -> None:
        invalid_values = (
            ("run_id", ""),
            ("run_id", 1),
            ("sequence", 0),
            ("sequence", True),
            ("timestamp", datetime(2026, 7, 11)),
            ("timestamp", datetime(2026, 7, 11, tzinfo=timezone(timedelta(hours=8)))),
            ("turn_id", " "),
            ("turn_id", 1),
            ("step_number", -1),
            ("step_number", True),
            ("schema_version", True),
            ("schema_version", EVENT_SCHEMA_VERSION + 1),
        )

        for field_name, value in invalid_values:
            with self.subTest(field_name=field_name, value=value):
                with self.assertRaises(ValueError):
                    self.valid_event(**{field_name: value})

    def test_runtime_errors_keep_run_context(self) -> None:
        finished = RunAlreadyFinishedError("run-1", 7)
        cancelled = RunCancelledError("run-2", "user")

        self.assertEqual((finished.run_id, finished.terminal_sequence), ("run-1", 7))
        self.assertIn("sequence 7", str(finished))
        self.assertEqual((cancelled.run_id, cancelled.reason), ("run-2", "user"))
        self.assertIn("user", str(cancelled))

    def test_run_and_turn_events_expose_their_lifecycle_payload(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }

        started = RunStartedEvent(**metadata, agent_name="Assistant")
        turn_started = TurnStartedEvent(**metadata, turn_id="turn-1", turn_number=1)
        turn_completed = TurnCompletedEvent(
            **metadata,
            turn_id="turn-1",
            turn_number=1,
        )

        self.assertEqual(started.event_type, "run_started")
        self.assertEqual(turn_started.turn_number, 1)
        self.assertEqual(turn_completed.event_type, "turn_completed")
        self.assertFalse(is_terminal_event(started))
        self.assertFalse(is_terminal_event(turn_completed))

    def test_all_run_endings_are_terminal(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 2,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }
        terminal_events = (
            RunCompletedEvent(**metadata, reached_final_answer=True),
            RunStoppedEvent(
                **metadata,
                reason="tool_approval_required",
                resumable=True,
            ),
            RunCancelledEvent(**metadata, reason="user"),
            RunFailedEvent(**metadata, error_type="ModelError", message="failed"),
        )

        self.assertTrue(all(is_terminal_event(event) for event in terminal_events))
        self.assertTrue(terminal_events[1].resumable)

    def test_lifecycle_payload_validation_rejects_invalid_values(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }
        invalid_events = (
            lambda: RunStartedEvent(**metadata, agent_name=" "),
            lambda: TurnStartedEvent(**metadata, turn_number=0),
            lambda: TurnCompletedEvent(**metadata, turn_number=True),
            lambda: RunCompletedEvent(**metadata, reached_final_answer=1),
            lambda: RunStoppedEvent(**metadata, reason="", resumable=True),
            lambda: RunStoppedEvent(**metadata, reason="limit", resumable=1),
            lambda: RunCancelledEvent(**metadata, reason=" "),
            lambda: RunFailedEvent(**metadata, error_type="", message="failed"),
            lambda: RunFailedEvent(**metadata, error_type="ModelError", message=""),
        )

        for make_event in invalid_events:
            with self.subTest(make_event=make_event):
                with self.assertRaises(ValueError):
                    make_event()

    def test_process_events_expose_small_typed_payloads(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 3,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
            "turn_id": "turn-1",
            "step_number": 2,
        }
        events = (
            ModelStartedEvent(**metadata, model_name="gpt-example"),
            ModelCompletedEvent(
                **metadata,
                response_id="response-1",
                tool_call_count=1,
            ),
            ToolRequestedEvent(**metadata, tool_name="search", call_id="call-1"),
            ToolStartedEvent(**metadata, tool_name="search", call_id="call-1"),
            ToolCompletedEvent(**metadata, tool_name="search", call_id="call-1"),
            ToolFailedEvent(
                **metadata,
                tool_name="search",
                call_id="call-1",
                error_type="TimeoutError",
                message="timed out",
            ),
            ApprovalRequiredEvent(
                **metadata,
                tool_name="shell",
                call_id="call-2",
                reason="writes files",
            ),
            ApprovalResolvedEvent(
                **metadata,
                tool_name="shell",
                call_id="call-2",
                decision="approved",
            ),
            SteerAcceptedEvent(**metadata, steer_id="steer-1"),
            SteerAppliedEvent(**metadata, steer_id="steer-1"),
            RunErrorEvent(
                **metadata,
                error_type="RecoverableError",
                message="retrying",
            ),
        )

        self.assertEqual(
            [event.event_type for event in events],
            [
                "model_started",
                "model_completed",
                "tool_requested",
                "tool_started",
                "tool_completed",
                "tool_failed",
                "approval_required",
                "approval_resolved",
                "steer_accepted",
                "steer_applied",
                "run_error",
            ],
        )
        self.assertTrue(all(not is_terminal_event(event) for event in events))

    def test_model_event_payload_validation_rejects_invalid_values(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }
        invalid_events = (
            lambda: ModelStartedEvent(**metadata, model_name=" "),
            lambda: ModelCompletedEvent(
                **metadata,
                response_id="",
                tool_call_count=0,
            ),
            lambda: ModelCompletedEvent(
                **metadata,
                response_id=None,
                tool_call_count=-1,
            ),
            lambda: ModelCompletedEvent(
                **metadata,
                response_id=None,
                tool_call_count=True,
            ),
        )

        for make_event in invalid_events:
            with self.subTest(make_event=make_event):
                with self.assertRaises(ValueError):
                    make_event()

    def test_model_attempt_events_expose_small_validated_payloads(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }
        attempt = ModelAttemptStartedEvent(
            **metadata,
            request_id="request-1",
            attempt_id="request-1:attempt:1",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
        )
        retry = ModelRetryScheduledEvent(
            **metadata,
            request_id="request-1",
            attempt_id="request-1:attempt:1",
            attempt_number=1,
            model_name="model-a",
            mode="stream",
            error_kind="transport",
            delay_seconds=1,
            reason="retry_scheduled",
        )
        fallback = ModelFallbackEvent(
            **metadata,
            request_id="request-1",
            attempt_id="request-1:attempt:2",
            attempt_number=2,
            from_model="model-a",
            to_model="model-b",
            reason="model_fallback",
            from_mode="stream",
            to_mode="stream",
        )

        self.assertEqual(
            [event.event_type for event in (attempt, retry, fallback)],
            [
                "model_attempt_started",
                "model_retry_scheduled",
                "model_fallback",
            ],
        )
        self.assertTrue(
            all(not is_terminal_event(event) for event in (attempt, retry, fallback))
        )
        self.assertIsNone(event_to_dict(attempt)["reason"])
        self.assertEqual(event_to_dict(retry)["delay_seconds"], 1)
        self.assertEqual(event_to_dict(fallback)["from_model"], "model-a")
        self.assertEqual(event_to_dict(fallback)["from_mode"], "stream")
        compatible_fallback = ModelFallbackEvent(
            **metadata,
            request_id="request-1",
            attempt_id="request-1:attempt:2",
            attempt_number=2,
            from_model="model-a",
            to_model="model-b",
            reason="model_fallback",
        )
        self.assertIsNone(compatible_fallback.from_mode)
        self.assertIsNone(compatible_fallback.to_mode)

    def test_model_attempt_event_validation_rejects_invalid_values(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }
        invalid_events = (
            lambda: ModelAttemptStartedEvent(
                **metadata,
                request_id="",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
            ),
            lambda: ModelAttemptStartedEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=True,
                model_name="model-a",
                mode="stream",
            ),
            lambda: ModelAttemptStartedEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="batch",
            ),
            lambda: ModelAttemptStartedEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                reason=" ",
            ),
            lambda: ModelRetryScheduledEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                error_kind="",
                delay_seconds=1,
                reason="retry_scheduled",
            ),
            lambda: ModelRetryScheduledEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                error_kind="transport",
                delay_seconds=True,
                reason="retry_scheduled",
            ),
            lambda: ModelRetryScheduledEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                error_kind="transport",
                delay_seconds=-1,
                reason="retry_scheduled",
            ),
            lambda: ModelRetryScheduledEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-1",
                attempt_number=1,
                model_name="model-a",
                mode="stream",
                error_kind="transport",
                delay_seconds=float("nan"),
                reason="retry_scheduled",
            ),
            lambda: ModelFallbackEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-2",
                attempt_number=2,
                from_model=" ",
                to_model="model-b",
                reason="model-a->model-b",
            ),
            lambda: ModelFallbackEvent(
                **metadata,
                request_id="request-1",
                attempt_id="attempt-2",
                attempt_number=2,
                from_model="model-a",
                to_model="model-b",
                reason="model_fallback",
                from_mode="batch",
                to_mode="stream",
            ),
        )

        for make_event in invalid_events:
            with self.subTest(make_event=make_event):
                with self.assertRaises(ValueError):
                    make_event()

    def test_tool_approval_steer_and_error_validation_reject_invalid_values(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, tzinfo=timezone.utc),
        }
        invalid_events = (
            lambda: ToolRequestedEvent(**metadata, tool_name="", call_id="call-1"),
            lambda: ToolStartedEvent(**metadata, tool_name="search", call_id=" "),
            lambda: ToolCompletedEvent(**metadata, tool_name=1, call_id="call-1"),
            lambda: ToolFailedEvent(
                **metadata,
                tool_name="search",
                call_id="call-1",
                error_type="",
                message="failed",
            ),
            lambda: ToolFailedEvent(
                **metadata,
                tool_name="search",
                call_id="call-1",
                error_type="ToolError",
                message="",
            ),
            lambda: ApprovalRequiredEvent(
                **metadata,
                tool_name="shell",
                call_id="call-2",
                reason=" ",
            ),
            lambda: ApprovalResolvedEvent(
                **metadata,
                tool_name="shell",
                call_id="call-2",
                decision="pending",
            ),
            lambda: SteerAcceptedEvent(**metadata, steer_id=""),
            lambda: SteerAppliedEvent(**metadata, steer_id=1),
            lambda: RunErrorEvent(
                **metadata,
                error_type="",
                message="retrying",
            ),
            lambda: RunErrorEvent(
                **metadata,
                error_type="RecoverableError",
                message=" ",
            ),
        )

        for make_event in invalid_events:
            with self.subTest(make_event=make_event):
                with self.assertRaises(ValueError):
                    make_event()

    def test_every_concrete_event_serializes_to_json(self) -> None:
        metadata = {
            "run_id": "run-1",
            "sequence": 1,
            "timestamp": datetime(2026, 7, 11, 8, 30, tzinfo=timezone.utc),
            "turn_id": "turn-1",
            "step_number": 2,
        }
        events = (
            RunStartedEvent(**metadata, agent_name="Assistant"),
            TurnStartedEvent(**metadata, turn_number=1),
            TurnCompletedEvent(**metadata, turn_number=1),
            RunCompletedEvent(**metadata, reached_final_answer=True),
            RunStoppedEvent(**metadata, reason="limit", resumable=False),
            RunCancelledEvent(**metadata, reason="user"),
            RunFailedEvent(
                **metadata,
                error_type="ModelError",
                message="failed",
            ),
            ModelStartedEvent(**metadata, model_name="gpt-example"),
            ModelAttemptStartedEvent(
                **metadata,
                request_id="request-1",
                attempt_id="request-1:attempt:1",
                attempt_number=1,
                model_name="gpt-example",
                mode="stream",
            ),
            ModelRetryScheduledEvent(
                **metadata,
                request_id="request-1",
                attempt_id="request-1:attempt:1",
                attempt_number=1,
                model_name="gpt-example",
                mode="stream",
                error_kind="transport",
                delay_seconds=1,
                reason="retry_scheduled",
            ),
            ModelFallbackEvent(
                **metadata,
                request_id="request-1",
                attempt_id="request-1:attempt:2",
                attempt_number=2,
                from_model="gpt-example",
                to_model="gpt-backup",
                reason="model_fallback",
                from_mode="stream",
                to_mode="stream",
            ),
            ModelCompletedEvent(
                **metadata,
                response_id=None,
                tool_call_count=1,
            ),
            ToolRequestedEvent(**metadata, tool_name="search", call_id="call-1"),
            ToolStartedEvent(**metadata, tool_name="search", call_id="call-1"),
            ToolCompletedEvent(**metadata, tool_name="search", call_id="call-1"),
            ToolFailedEvent(
                **metadata,
                tool_name="search",
                call_id="call-1",
                error_type="TimeoutError",
                message="timed out",
            ),
            ApprovalRequiredEvent(
                **metadata,
                tool_name="shell",
                call_id="call-2",
                reason=None,
            ),
            ApprovalResolvedEvent(
                **metadata,
                tool_name="shell",
                call_id="call-2",
                decision="rejected",
            ),
            SteerAcceptedEvent(**metadata, steer_id="steer-1"),
            SteerAppliedEvent(**metadata, steer_id="steer-1"),
            RunErrorEvent(
                **metadata,
                error_type="RecoverableError",
                message="retrying",
            ),
        )

        for event in events:
            with self.subTest(event_type=event.event_type):
                payload = event_to_dict(event)
                self.assertEqual(payload["event_type"], event.event_type)
                self.assertEqual(payload["timestamp"], "2026-07-11T08:30:00Z")
                self.assertEqual(payload["schema_version"], EVENT_SCHEMA_VERSION)
                self.assertEqual(json.loads(json.dumps(payload)), payload)

        self.assertIsNone(event_to_dict(events[11])["response_id"])
        self.assertIsNone(event_to_dict(events[16])["reason"])
        with self.assertRaises(TypeError):
            event_to_dict("not an event")

    def test_reducer_assigns_contiguous_sequence_and_injected_clock(self) -> None:
        timestamp = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        reducer = RunEventReducer("run-1", clock=lambda: timestamp)

        started = reducer.emit(RunStartedEvent, agent_name="Assistant")
        turn = reducer.emit(
            TurnStartedEvent,
            turn_id="turn-1",
            turn_number=1,
        )

        self.assertEqual((started.sequence, turn.sequence), (1, 2))
        self.assertEqual((started.timestamp, turn.timestamp), (timestamp, timestamp))
        self.assertEqual(reducer.next_sequence, 3)
        self.assertFalse(reducer.finished)
        self.assertIsNone(reducer.terminal_event)

    def test_reducer_failed_emit_does_not_consume_sequence(self) -> None:
        timestamp = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        reducer = RunEventReducer("run-1", clock=lambda: timestamp)

        with self.assertRaises(TypeError):
            reducer.emit("not an event")
        with self.assertRaises(ValueError):
            reducer.emit(TurnStartedEvent, turn_number=0)

        event = reducer.emit(TurnStartedEvent, turn_number=1)
        self.assertEqual(event.sequence, 1)
        self.assertEqual(reducer.next_sequence, 2)

    def test_reducer_closes_permanently_after_first_terminal(self) -> None:
        timestamp = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        reducer = RunEventReducer("run-1", clock=lambda: timestamp)
        terminal = reducer.emit(
            RunStoppedEvent,
            reason="tool_approval_required",
            resumable=True,
        )

        self.assertTrue(reducer.finished)
        self.assertIs(reducer.terminal_event, terminal)
        self.assertEqual(reducer.next_sequence, 2)
        with self.assertRaises(RunAlreadyFinishedError) as raised:
            reducer.emit(TurnStartedEvent, turn_number=1)
        self.assertEqual(raised.exception.terminal_sequence, 1)
        self.assertIs(reducer.terminal_event, terminal)
        self.assertEqual(reducer.next_sequence, 2)

    def test_reducers_do_not_share_sequence_or_terminal_state(self) -> None:
        timestamp = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        first = RunEventReducer("run-1", clock=lambda: timestamp)
        second = RunEventReducer("run-2", clock=lambda: timestamp)

        first.emit(RunStartedEvent, agent_name="First")
        first.emit(RunCompletedEvent, reached_final_answer=True)
        second_event = second.emit(RunStartedEvent, agent_name="Second")

        self.assertTrue(first.finished)
        self.assertFalse(second.finished)
        self.assertEqual(second_event.sequence, 1)
        self.assertEqual(second.next_sequence, 2)

    def test_reducer_validates_constructor_and_default_clock(self) -> None:
        with self.assertRaises(ValueError):
            RunEventReducer(" ")
        with self.assertRaises(TypeError):
            RunEventReducer("run-1", clock=None)

        self.assertEqual(utc_now().utcoffset(), timedelta(0))


if __name__ == "__main__":
    unittest.main()
