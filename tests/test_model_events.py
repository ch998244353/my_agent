from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.model_events import (  # noqa: E402
    ModelEvent,
    ModelEventAccumulator,
    ModelEventDiagnostics,
    ReasoningDelta,
    ReasoningDone,
    RefusalDelta,
    RefusalDone,
    ResponseCompleted,
    ResponseCreated,
    ResponseFailed,
    ResponseIncomplete,
    TextDelta,
    TextDone,
    ToolArgumentsDelta,
    ToolArgumentsDone,
    ToolCallStarted,
    UsageUpdated,
)
from agents.contracts import ToolCall  # noqa: E402
from agents.models import (  # noqa: E402
    ModelErrorKind,
    ModelProtocolError,
    ModelResponseError,
)


class ModelEventTestCase(unittest.TestCase):
    def test_accumulator_accepts_one_created_event(self) -> None:
        accumulator = ModelEventAccumulator(
            "attempt-1",
            max_buffer_chars=100,
        )

        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1",
                sequence=1,
                response_id="response-1",
            )
        )

        self.assertTrue(accumulator.started)
        self.assertFalse(accumulator.finished)
        self.assertEqual(accumulator.response_id, "response-1")
        self.assertEqual(accumulator.expected_sequence, 2)

    def test_accumulator_rejects_wrong_event_without_advancing_sequence(self) -> None:
        accumulator = ModelEventAccumulator(
            "attempt-1",
            max_buffer_chars=100,
        )

        for event in (
            ModelEvent(attempt_id="attempt-2", sequence=1),
            ModelEvent(attempt_id="attempt-1", sequence=2),
            ModelEvent(attempt_id="attempt-1", sequence=1),
        ):
            with self.subTest(event=event):
                with self.assertRaises(ModelProtocolError) as raised:
                    accumulator.apply(event)
                self.assertEqual(raised.exception.kind, ModelErrorKind.PROTOCOL)
                self.assertEqual(raised.exception.attempt_id, "attempt-1")
                self.assertEqual(accumulator.expected_sequence, 1)

        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1",
                sequence=1,
                response_id="response-1",
            )
        )
        with self.assertRaises(ModelProtocolError):
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1",
                    sequence=2,
                    response_id="response-2",
                )
            )
        self.assertEqual(accumulator.expected_sequence, 2)

    def test_accumulator_abort_clears_state_and_closes_attempt(self) -> None:
        accumulator = ModelEventAccumulator(
            "attempt-1",
            max_buffer_chars=100,
        )
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1",
                sequence=1,
                response_id="response-1",
            )
        )

        accumulator.abort()

        self.assertFalse(accumulator.started)
        self.assertTrue(accumulator.finished)
        self.assertIsNone(accumulator.response_id)
        with self.assertRaises(ModelProtocolError):
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1",
                    sequence=2,
                    response_id="response-2",
                )
            )

    def test_accumulator_rejects_invalid_configuration(self) -> None:
        for attempt_id in ("", " ", None):
            with self.subTest(attempt_id=attempt_id):
                with self.assertRaises(ValueError):
                    ModelEventAccumulator(  # type: ignore[arg-type]
                        attempt_id,
                        max_buffer_chars=100,
                    )
        for max_buffer_chars in (True, 0, -1, 1.5):
            with self.subTest(max_buffer_chars=max_buffer_chars):
                with self.assertRaises(ValueError):
                    ModelEventAccumulator(  # type: ignore[arg-type]
                        "attempt-1",
                        max_buffer_chars=max_buffer_chars,
                    )

    def test_model_event_accepts_one_based_sequence_and_is_frozen(self) -> None:
        event = ModelEvent(attempt_id="attempt-1", sequence=1)

        self.assertEqual(event.attempt_id, "attempt-1")
        self.assertEqual(event.sequence, 1)
        with self.assertRaises(FrozenInstanceError):
            event.sequence = 2  # type: ignore[misc]

    def test_model_event_rejects_invalid_identity_and_sequence(self) -> None:
        for attempt_id in ("", " ", None):
            with self.subTest(attempt_id=attempt_id):
                with self.assertRaises(ValueError):
                    ModelEvent(attempt_id=attempt_id, sequence=1)  # type: ignore[arg-type]
        for sequence in (True, 0, -1, 1.5):
            with self.subTest(sequence=sequence):
                with self.assertRaises(ValueError):
                    ModelEvent(attempt_id="attempt-1", sequence=sequence)  # type: ignore[arg-type]

    def test_response_created_requires_response_id(self) -> None:
        event = ResponseCreated(
            attempt_id="attempt-1",
            sequence=1,
            response_id="response-1",
        )

        self.assertEqual(event.response_id, "response-1")
        for response_id in ("", " ", None):
            with self.subTest(response_id=response_id):
                with self.assertRaises(ValueError):
                    ResponseCreated(
                        attempt_id="attempt-1",
                        sequence=1,
                        response_id=response_id,  # type: ignore[arg-type]
                    )

    def test_content_events_validate_indexes_and_text_fields(self) -> None:
        event_types = (
            (TextDelta, "delta"),
            (TextDone, "text"),
            (ReasoningDelta, "delta"),
            (ReasoningDone, "text"),
            (RefusalDelta, "delta"),
            (RefusalDone, "text"),
        )
        for event_type, value_name in event_types:
            with self.subTest(event_type=event_type.__name__):
                event = event_type(
                    attempt_id="attempt-1",
                    sequence=1,
                    output_index=0,
                    content_index=0,
                    **{value_name: ""},
                )
                self.assertEqual(getattr(event, value_name), "")
                for field_name, invalid_value in (
                    ("output_index", True),
                    ("output_index", -1),
                    ("content_index", True),
                    ("content_index", -1),
                    (value_name, None),
                ):
                    values = {
                        "attempt_id": "attempt-1",
                        "sequence": 1,
                        "output_index": 0,
                        "content_index": 0,
                        value_name: "content",
                    }
                    values[field_name] = invalid_value
                    with self.assertRaises(ValueError):
                        event_type(**values)

    def test_accumulator_interleaves_and_sorts_content_blocks(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=20)
        events = (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=1,
                content_index=0,
                delta="B",
            ),
            ReasoningDelta(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                content_index=0,
                delta="think",
            ),
            RefusalDelta(
                attempt_id="attempt-1",
                sequence=4,
                output_index=0,
                content_index=0,
                delta="no",
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=5,
                output_index=0,
                content_index=1,
                delta="A",
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=6,
                output_index=0,
                content_index=1,
                delta="",
            ),
            TextDone(
                attempt_id="attempt-1",
                sequence=7,
                output_index=0,
                content_index=1,
                text="A",
            ),
            TextDone(
                attempt_id="attempt-1",
                sequence=8,
                output_index=1,
                content_index=0,
                text="B",
            ),
            ReasoningDone(
                attempt_id="attempt-1",
                sequence=9,
                output_index=0,
                content_index=0,
                text="think",
            ),
            RefusalDone(
                attempt_id="attempt-1",
                sequence=10,
                output_index=0,
                content_index=0,
                text="no",
            ),
        )

        for event in events:
            accumulator.apply(event)

        self.assertEqual(accumulator.text, "AB")
        self.assertEqual(accumulator.reasoning, "think")
        self.assertEqual(accumulator.refusal, "no")
        self.assertEqual(accumulator.buffered_chars, 9)
        self.assertEqual(accumulator.expected_sequence, 11)

    def test_done_mismatch_closes_attempt_and_clears_content(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=20)
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            )
        )
        accumulator.apply(
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                content_index=0,
                delta="hello",
            )
        )

        with self.assertRaises(ModelProtocolError) as raised:
            accumulator.apply(
                TextDone(
                    attempt_id="attempt-1",
                    sequence=3,
                    output_index=0,
                    content_index=0,
                    text="different",
                )
            )

        self.assertEqual(raised.exception.kind, ModelErrorKind.PROTOCOL)
        self.assertTrue(accumulator.finished)
        self.assertEqual(accumulator.text, "")
        self.assertEqual(accumulator.buffered_chars, 0)

    def test_delta_after_done_closes_attempt(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=20)
        for event in (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                content_index=0,
                delta="done",
            ),
            TextDone(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                content_index=0,
                text="done",
            ),
        ):
            accumulator.apply(event)

        with self.assertRaises(ModelProtocolError):
            accumulator.apply(
                TextDelta(
                    attempt_id="attempt-1",
                    sequence=4,
                    output_index=0,
                    content_index=0,
                    delta="late",
                )
            )
        self.assertTrue(accumulator.finished)
        self.assertEqual(accumulator.text, "")

    def test_shared_buffer_accepts_exact_limit_then_clears_on_overflow(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=5)
        for event in (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                content_index=0,
                delta="abc",
            ),
            ReasoningDelta(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                content_index=0,
                delta="de",
            ),
        ):
            accumulator.apply(event)
        self.assertEqual(accumulator.buffered_chars, 5)

        with self.assertRaises(ModelProtocolError) as raised:
            accumulator.apply(
                RefusalDelta(
                    attempt_id="attempt-1",
                    sequence=4,
                    output_index=0,
                    content_index=0,
                    delta="f",
                )
            )

        self.assertEqual(raised.exception.kind, ModelErrorKind.BUFFER_OVERFLOW)
        self.assertEqual(raised.exception.max_buffer_chars, 5)
        self.assertEqual(raised.exception.attempted_buffer_chars, 6)
        self.assertTrue(accumulator.finished)
        self.assertEqual(accumulator.text, "")
        self.assertEqual(accumulator.reasoning, "")
        self.assertEqual(accumulator.refusal, "")
        self.assertEqual(accumulator.buffered_chars, 0)
        self.assertEqual(accumulator.diagnostics.max_buffer_chars, 5)
        self.assertEqual(accumulator.diagnostics.attempted_buffer_chars, 6)

    def test_each_buffer_kind_reports_safe_overflow_diagnostics(self) -> None:
        scenarios = (
            (
                "text",
                (
                    TextDelta(
                        attempt_id="attempt-1",
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="ab",
                    ),
                    TextDelta(
                        attempt_id="attempt-1",
                        sequence=3,
                        output_index=0,
                        content_index=0,
                        delta="secret",
                    ),
                ),
            ),
            (
                "reasoning",
                (
                    ReasoningDelta(
                        attempt_id="attempt-1",
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="ab",
                    ),
                    ReasoningDelta(
                        attempt_id="attempt-1",
                        sequence=3,
                        output_index=0,
                        content_index=0,
                        delta="secret",
                    ),
                ),
            ),
            (
                "refusal",
                (
                    RefusalDelta(
                        attempt_id="attempt-1",
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="ab",
                    ),
                    RefusalDelta(
                        attempt_id="attempt-1",
                        sequence=3,
                        output_index=0,
                        content_index=0,
                        delta="secret",
                    ),
                ),
            ),
            (
                "tool arguments",
                (
                    ToolCallStarted(
                        attempt_id="attempt-1",
                        sequence=2,
                        output_index=0,
                        item_id="item-1",
                        call_id="call-1",
                        tool_name="weather",
                    ),
                    ToolArgumentsDelta(
                        attempt_id="attempt-1",
                        sequence=3,
                        output_index=0,
                        item_id="item-1",
                        delta="ab",
                    ),
                    ToolArgumentsDelta(
                        attempt_id="attempt-1",
                        sequence=4,
                        output_index=0,
                        item_id="item-1",
                        delta="secret",
                    ),
                ),
            ),
        )
        for label, events in scenarios:
            with self.subTest(label=label):
                accumulator = ModelEventAccumulator(
                    "attempt-1",
                    max_buffer_chars=4,
                )
                accumulator.apply(
                    ResponseCreated(
                        attempt_id="attempt-1",
                        sequence=1,
                        response_id="response-1",
                    )
                )
                for event in events[:-1]:
                    accumulator.apply(event)

                with self.assertRaises(ModelProtocolError) as raised:
                    accumulator.apply(events[-1])

                self.assertEqual(raised.exception.max_buffer_chars, 4)
                self.assertEqual(raised.exception.attempted_buffer_chars, 8)
                self.assertNotIn("secret", str(vars(raised.exception)))
                self.assertEqual(accumulator.diagnostics.max_buffer_chars, 4)
                self.assertEqual(
                    accumulator.diagnostics.attempted_buffer_chars,
                    8,
                )
                self.assertEqual(accumulator.buffered_chars, 0)
                self.assertEqual(accumulator.text, "")
                self.assertEqual(accumulator.reasoning, "")
                self.assertEqual(accumulator.refusal, "")
                self.assertEqual(accumulator.completed_tool_calls, ())

    def test_tool_events_validate_identity_position_and_arguments(self) -> None:
        valid_events = (
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=1,
                output_index=0,
                item_id="item-1",
                call_id="call-1",
                tool_name="weather",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=1,
                output_index=0,
                item_id="item-1",
                delta="",
            ),
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=1,
                output_index=0,
                item_id="item-1",
                arguments="{}",
            ),
        )
        self.assertEqual(len(valid_events), 3)

        for event_type, value_name in (
            (ToolArgumentsDelta, "delta"),
            (ToolArgumentsDone, "arguments"),
        ):
            for field_name, invalid_value in (
                ("output_index", True),
                ("output_index", -1),
                ("item_id", ""),
                (value_name, None),
            ):
                values = {
                    "attempt_id": "attempt-1",
                    "sequence": 1,
                    "output_index": 0,
                    "item_id": "item-1",
                    value_name: "{}",
                }
                values[field_name] = invalid_value
                with self.subTest(event_type=event_type, field_name=field_name):
                    with self.assertRaises(ValueError):
                        event_type(**values)

        for field_name in ("item_id", "call_id", "tool_name"):
            values = {
                "attempt_id": "attempt-1",
                "sequence": 1,
                "output_index": 0,
                "item_id": "item-1",
                "call_id": "call-1",
                "tool_name": "weather",
            }
            values[field_name] = " "
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    ToolCallStarted(**values)

    def test_complete_unicode_tool_arguments_create_one_tool_call(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        events = (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                item_id="item-1",
                call_id="call-1",
                tool_name="weather",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                item_id="item-1",
                delta='{"city":"',
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=4,
                output_index=0,
                item_id="item-1",
                delta='上海"}',
            ),
        )
        for event in events:
            accumulator.apply(event)
        self.assertEqual(accumulator.completed_tool_calls, ())

        arguments = '{"city":"上海"}'
        accumulator.apply(
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=5,
                output_index=0,
                item_id="item-1",
                arguments=arguments,
            )
        )

        self.assertEqual(
            accumulator.completed_tool_calls,
            (ToolCall("weather", {"city": "上海"}, "call-1"),),
        )
        self.assertEqual(accumulator.buffered_chars, len(arguments))

    def test_multiple_tools_are_sorted_and_same_done_is_idempotent(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        events = (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=2,
                output_index=1,
                item_id="item-b",
                call_id="call-b",
                tool_name="second",
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                item_id="item-a",
                call_id="call-a",
                tool_name="first",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=4,
                output_index=1,
                item_id="item-b",
                delta="{}",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=5,
                output_index=0,
                item_id="item-a",
                delta="{}",
            ),
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=6,
                output_index=1,
                item_id="item-b",
                arguments="{}",
            ),
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=7,
                output_index=0,
                item_id="item-a",
                arguments="{}",
            ),
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=8,
                output_index=0,
                item_id="item-a",
                arguments="{}",
            ),
        )
        for event in events:
            accumulator.apply(event)

        self.assertEqual(
            tuple(call.call_id for call in accumulator.completed_tool_calls),
            ("call-a", "call-b"),
        )

    def test_tool_start_rejects_duplicate_identity_and_position(self) -> None:
        for duplicate_field in ("item_id", "call_id", "output_index"):
            accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1", sequence=1, response_id="response-1"
                )
            )
            accumulator.apply(
                ToolCallStarted(
                    attempt_id="attempt-1",
                    sequence=2,
                    output_index=0,
                    item_id="item-1",
                    call_id="call-1",
                    tool_name="weather",
                )
            )
            values = {
                "output_index": 1,
                "item_id": "item-2",
                "call_id": "call-2",
            }
            values[duplicate_field] = {
                "item_id": "item-1",
                "call_id": "call-1",
                "output_index": 0,
            }[duplicate_field]

            with self.subTest(duplicate_field=duplicate_field):
                with self.assertRaises(ModelProtocolError):
                    accumulator.apply(
                        ToolCallStarted(
                            attempt_id="attempt-1",
                            sequence=3,
                            tool_name="other",
                            **values,
                        )
                    )
                self.assertTrue(accumulator.finished)
                self.assertEqual(accumulator.completed_tool_calls, ())

    def test_invalid_tool_arguments_never_commit(self) -> None:
        for arguments in ("{", "[]", "null", "1", "true"):
            accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
            for event in (
                ResponseCreated(
                    attempt_id="attempt-1", sequence=1, response_id="response-1"
                ),
                ToolCallStarted(
                    attempt_id="attempt-1",
                    sequence=2,
                    output_index=0,
                    item_id="item-1",
                    call_id="call-1",
                    tool_name="weather",
                ),
                ToolArgumentsDelta(
                    attempt_id="attempt-1",
                    sequence=3,
                    output_index=0,
                    item_id="item-1",
                    delta=arguments,
                ),
            ):
                accumulator.apply(event)

            with self.subTest(arguments=arguments):
                with self.assertRaises(ModelProtocolError):
                    accumulator.apply(
                        ToolArgumentsDone(
                            attempt_id="attempt-1",
                            sequence=4,
                            output_index=0,
                            item_id="item-1",
                            arguments=arguments,
                        )
                    )
                self.assertTrue(accumulator.finished)
                self.assertEqual(accumulator.completed_tool_calls, ())

    def test_tool_argument_protocol_conflicts_abort_attempt(self) -> None:
        start = ToolCallStarted(
            attempt_id="attempt-1",
            sequence=2,
            output_index=0,
            item_id="item-1",
            call_id="call-1",
            tool_name="weather",
        )
        delta = ToolArgumentsDelta(
            attempt_id="attempt-1",
            sequence=3,
            output_index=0,
            item_id="item-1",
            delta="{}",
        )
        done = ToolArgumentsDone(
            attempt_id="attempt-1",
            sequence=4,
            output_index=0,
            item_id="item-1",
            arguments="{}",
        )
        scenarios = (
            (
                (),
                ToolArgumentsDelta(
                    attempt_id="attempt-1",
                    sequence=2,
                    output_index=0,
                    item_id="missing",
                    delta="{}",
                ),
            ),
            (
                (start,),
                ToolArgumentsDelta(
                    attempt_id="attempt-1",
                    sequence=3,
                    output_index=1,
                    item_id="item-1",
                    delta="{}",
                ),
            ),
            (
                (start, delta),
                ToolArgumentsDone(
                    attempt_id="attempt-1",
                    sequence=4,
                    output_index=0,
                    item_id="item-1",
                    arguments='{"changed":true}',
                ),
            ),
            (
                (start, delta, done),
                ToolArgumentsDelta(
                    attempt_id="attempt-1",
                    sequence=5,
                    output_index=0,
                    item_id="item-1",
                    delta="late",
                ),
            ),
            (
                (start, delta, done),
                ToolArgumentsDone(
                    attempt_id="attempt-1",
                    sequence=5,
                    output_index=0,
                    item_id="item-1",
                    arguments='{"changed":true}',
                ),
            ),
        )

        for accepted_events, rejected_event in scenarios:
            accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1", sequence=1, response_id="response-1"
                )
            )
            for event in accepted_events:
                accumulator.apply(event)

            with self.subTest(rejected_event=rejected_event):
                with self.assertRaises(ModelProtocolError):
                    accumulator.apply(rejected_event)
                self.assertTrue(accumulator.finished)
                self.assertEqual(accumulator.completed_tool_calls, ())

    def test_tool_protocol_failure_clears_already_completed_calls(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        events = (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                item_id="item-1",
                call_id="call-1",
                tool_name="first",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                item_id="item-1",
                delta="{}",
            ),
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=4,
                output_index=0,
                item_id="item-1",
                arguments="{}",
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=5,
                output_index=1,
                item_id="item-2",
                call_id="call-2",
                tool_name="second",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=6,
                output_index=1,
                item_id="item-2",
                delta="{",
            ),
        )
        for event in events:
            accumulator.apply(event)
        self.assertEqual(len(accumulator.completed_tool_calls), 1)

        with self.assertRaises(ModelProtocolError):
            accumulator.apply(
                ToolArgumentsDone(
                    attempt_id="attempt-1",
                    sequence=7,
                    output_index=1,
                    item_id="item-2",
                    arguments="{",
                )
            )
        self.assertEqual(accumulator.completed_tool_calls, ())
        self.assertEqual(accumulator.buffered_chars, 0)

    def test_content_and_tool_arguments_share_buffer_limit(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=4)
        for event in (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                content_index=0,
                delta="ab",
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=3,
                output_index=1,
                item_id="item-1",
                call_id="call-1",
                tool_name="weather",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=4,
                output_index=1,
                item_id="item-1",
                delta="{}",
            ),
        ):
            accumulator.apply(event)
        self.assertEqual(accumulator.buffered_chars, 4)

        with self.assertRaises(ModelProtocolError) as raised:
            accumulator.apply(
                ToolArgumentsDelta(
                    attempt_id="attempt-1",
                    sequence=5,
                    output_index=1,
                    item_id="item-1",
                    delta="x",
                )
            )
        self.assertEqual(raised.exception.kind, ModelErrorKind.BUFFER_OVERFLOW)
        self.assertEqual(accumulator.text, "")
        self.assertEqual(accumulator.completed_tool_calls, ())

    def test_usage_and_terminal_events_validate_and_copy_snapshots(self) -> None:
        usage = {"input_tokens": 2}
        usage_event = UsageUpdated(
            attempt_id="attempt-1",
            sequence=1,
            usage=usage,
        )
        usage["output_tokens"] = 3
        self.assertEqual(usage_event.usage, {"input_tokens": 2})

        output = [{"type": "message"}]
        request_summary = {"model": "test"}
        completed = ResponseCompleted(
            attempt_id="attempt-1",
            sequence=1,
            response_id="response-1",
            output=output,
            request_summary=request_summary,
            request_id="request-1",
        )
        output.append({"type": "late"})
        request_summary["late"] = True
        self.assertEqual(completed.output, [{"type": "message"}])
        self.assertEqual(completed.request_summary, {"model": "test"})

        for factory in (
            lambda: UsageUpdated(
                attempt_id="attempt-1", sequence=1, usage=[]  # type: ignore[arg-type]
            ),
            lambda: ResponseCompleted(
                attempt_id="attempt-1",
                sequence=1,
                response_id="",
                output=[],
            ),
            lambda: ResponseFailed(
                attempt_id="attempt-1",
                sequence=1,
                kind="server",  # type: ignore[arg-type]
                message="failed",
            ),
            lambda: ResponseIncomplete(
                attempt_id="attempt-1",
                sequence=1,
                reason="",
                message="incomplete",
            ),
        ):
            with self.subTest(factory=factory):
                with self.assertRaises(ValueError):
                    factory()

    def test_finish_builds_existing_model_response_and_releases_state(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        raw = object()
        events = (
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            ),
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                content_index=0,
                delta="hello",
            ),
            TextDone(
                attempt_id="attempt-1",
                sequence=3,
                output_index=0,
                content_index=0,
                text="hello",
            ),
            ToolCallStarted(
                attempt_id="attempt-1",
                sequence=4,
                output_index=1,
                item_id="item-1",
                call_id="call-1",
                tool_name="weather",
            ),
            ToolArgumentsDelta(
                attempt_id="attempt-1",
                sequence=5,
                output_index=1,
                item_id="item-1",
                delta="{}",
            ),
            ToolArgumentsDone(
                attempt_id="attempt-1",
                sequence=6,
                output_index=1,
                item_id="item-1",
                arguments="{}",
            ),
            UsageUpdated(
                attempt_id="attempt-1",
                sequence=7,
                usage={"total_tokens": 4},
            ),
            UsageUpdated(
                attempt_id="attempt-1",
                sequence=8,
                usage={"total_tokens": 7},
            ),
            ResponseCompleted(
                attempt_id="attempt-1",
                sequence=9,
                response_id="response-1",
                output=[{"type": "message"}],
                raw=raw,
                request_summary={"model": "test"},
                request_id="request-1",
            ),
        )
        for event in events:
            accumulator.apply(event)

        response = accumulator.finish()

        self.assertEqual(response.response_id, "response-1")
        self.assertEqual(response.output, [{"type": "message"}])
        self.assertEqual(response.output_text, "hello")
        self.assertEqual(
            response.tool_calls,
            [ToolCall("weather", {}, "call-1")],
        )
        self.assertIsNone(response.refusal)
        self.assertIs(response.raw, raw)
        self.assertEqual(response.usage, {"total_tokens": 7})
        self.assertEqual(response.request_summary, {"model": "test"})
        self.assertEqual(response.request_id, "request-1")
        self.assertEqual(
            accumulator.diagnostics,
            ModelEventDiagnostics(
                usage_missing=False,
                peak_buffer_chars=7,
                max_buffer_chars=100,
            ),
        )
        self.assertTrue(accumulator.finished)
        self.assertFalse(accumulator.started)
        self.assertEqual(accumulator.buffered_chars, 0)
        self.assertEqual(accumulator.text, "")
        self.assertEqual(accumulator.completed_tool_calls, ())

        with self.assertRaises(ModelProtocolError):
            accumulator.finish()

    def test_finish_allows_missing_usage(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            )
        )
        accumulator.apply(
            ResponseCompleted(
                attempt_id="attempt-1",
                sequence=2,
                response_id="response-1",
                output=[],
            )
        )

        response = accumulator.finish()

        self.assertIsNone(response.output_text)
        self.assertIsNone(response.refusal)
        self.assertIsNone(response.usage)
        self.assertEqual(
            accumulator.diagnostics,
            ModelEventDiagnostics(
                usage_missing=True,
                peak_buffer_chars=0,
                max_buffer_chars=100,
            ),
        )

    def test_finish_rejects_early_close_and_releases_content(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            )
        )
        accumulator.apply(
            TextDelta(
                attempt_id="attempt-1",
                sequence=2,
                output_index=0,
                content_index=0,
                delta="partial",
            )
        )

        with self.assertRaises(ModelProtocolError) as raised:
            accumulator.finish()

        self.assertEqual(raised.exception.kind, ModelErrorKind.EARLY_CLOSE)
        self.assertEqual(accumulator.buffered_chars, 0)
        self.assertEqual(accumulator.text, "")
        self.assertEqual(accumulator.diagnostics.peak_buffer_chars, 7)

    def test_finish_rejects_pending_content_or_tool_arguments(self) -> None:
        for pending_kind in ("content", "tool"):
            accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1", sequence=1, response_id="response-1"
                )
            )
            if pending_kind == "content":
                accumulator.apply(
                    TextDelta(
                        attempt_id="attempt-1",
                        sequence=2,
                        output_index=0,
                        content_index=0,
                        delta="partial",
                    )
                )
            else:
                accumulator.apply(
                    ToolCallStarted(
                        attempt_id="attempt-1",
                        sequence=2,
                        output_index=0,
                        item_id="item-1",
                        call_id="call-1",
                        tool_name="weather",
                    )
                )
            accumulator.apply(
                ResponseCompleted(
                    attempt_id="attempt-1",
                    sequence=3,
                    response_id="response-1",
                    output=[],
                )
            )

            with self.subTest(pending_kind=pending_kind):
                with self.assertRaises(ModelProtocolError) as raised:
                    accumulator.finish()
                self.assertEqual(raised.exception.kind, ModelErrorKind.PROTOCOL)
                self.assertEqual(accumulator.buffered_chars, 0)
                self.assertEqual(accumulator.completed_tool_calls, ())

    def test_finish_rejects_mismatched_response_id(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            )
        )
        accumulator.apply(
            ResponseCompleted(
                attempt_id="attempt-1",
                sequence=2,
                response_id="response-2",
                output=[],
            )
        )

        with self.assertRaises(ModelProtocolError):
            accumulator.finish()
        self.assertIsNone(accumulator.response_id)

    def test_failed_terminal_preserves_error_facts_and_cleans_state(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            )
        )
        accumulator.apply(
            ResponseFailed(
                attempt_id="attempt-1",
                sequence=2,
                kind=ModelErrorKind.SERVER,
                message="provider failed",
                status_code=503,
                request_id="request-1",
            )
        )

        with self.assertRaises(ModelResponseError) as raised:
            accumulator.finish()

        self.assertEqual(raised.exception.kind, ModelErrorKind.SERVER)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.request_id, "request-1")
        self.assertEqual(raised.exception.attempt_id, "attempt-1")
        self.assertEqual(accumulator.buffered_chars, 0)

    def test_incomplete_terminal_only_maps_context_reasons_to_context_window(self) -> None:
        for reason, expected_kind in (
            ("context_window", ModelErrorKind.CONTEXT_WINDOW),
            ("max_output_tokens", ModelErrorKind.PROTOCOL),
        ):
            accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1", sequence=1, response_id="response-1"
                )
            )
            accumulator.apply(
                ResponseIncomplete(
                    attempt_id="attempt-1",
                    sequence=2,
                    reason=reason,
                    message="response incomplete",
                    request_id="request-1",
                )
            )

            with self.subTest(reason=reason):
                with self.assertRaises(ModelResponseError) as raised:
                    accumulator.finish()
                self.assertEqual(raised.exception.kind, expected_kind)
                self.assertIn(reason, str(raised.exception))
                self.assertEqual(raised.exception.request_id, "request-1")

    def test_event_after_terminal_is_rejected_and_cleans_attempt(self) -> None:
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        accumulator.apply(
            ResponseCreated(
                attempt_id="attempt-1", sequence=1, response_id="response-1"
            )
        )
        accumulator.apply(
            ResponseCompleted(
                attempt_id="attempt-1",
                sequence=2,
                response_id="response-1",
                output=[],
            )
        )

        with self.assertRaises(ModelProtocolError):
            accumulator.apply(
                UsageUpdated(
                    attempt_id="attempt-1",
                    sequence=3,
                    usage={"total_tokens": 1},
                )
            )

        self.assertIsNone(accumulator.response_id)
        self.assertEqual(accumulator.buffered_chars, 0)

    def test_repeated_or_conflicting_terminal_is_rejected(self) -> None:
        for second_terminal in (
            ResponseCompleted(
                attempt_id="attempt-1",
                sequence=3,
                response_id="response-1",
                output=[],
            ),
            ResponseFailed(
                attempt_id="attempt-1",
                sequence=3,
                kind=ModelErrorKind.SERVER,
                message="late failure",
            ),
        ):
            accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
            accumulator.apply(
                ResponseCreated(
                    attempt_id="attempt-1", sequence=1, response_id="response-1"
                )
            )
            accumulator.apply(
                ResponseCompleted(
                    attempt_id="attempt-1",
                    sequence=2,
                    response_id="response-1",
                    output=[],
                )
            )

            with self.subTest(second_terminal=second_terminal):
                with self.assertRaises(ModelProtocolError):
                    accumulator.apply(second_terminal)
                self.assertEqual(accumulator.buffered_chars, 0)


if __name__ == "__main__":
    unittest.main()
