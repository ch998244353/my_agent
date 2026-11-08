from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    ChatMessage,
    ModelErrorKind,
    ModelProtocolError,
    ModelRequest,
    ModelResponse,
    ModelResponseError,
    ModelSettings,
    OpenAIResponsesModel,
    ResponseCompleted,
    ResponseCreated,
    ToolCall,
    ToolArgument,
    ToolSpec,
    UsageUpdated,
)
from agents.models import (  # noqa: E402
    _OpenAIEventMapper,
    _next_stream_item,
    stream_model_response,
)
from agents.model_events import ModelEventAccumulator  # noqa: E402


class FakeStream:
    def __init__(
        self,
        events: list[object],
        *,
        next_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._events = iter(events)
        self._next_error = next_error
        self._close_error = close_error
        self.next_thread_ids: list[int] = []
        self.close_thread_ids: list[int] = []
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        self.next_thread_ids.append(threading.get_ident())
        try:
            return next(self._events)
        except StopIteration:
            if self._next_error is not None:
                raise self._next_error
            raise

    def close(self) -> None:
        self.close_thread_ids.append(threading.get_ident())
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class FakeResponses:
    def __init__(
        self,
        stream: FakeStream,
        *,
        create_error: BaseException | None = None,
    ) -> None:
        self.stream = stream
        self.create_error = create_error
        self.requests: list[dict[str, object]] = []
        self.create_thread_ids: list[int] = []

    def create(self, **kwargs):
        self.create_thread_ids.append(threading.get_ident())
        self.requests.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        return self.stream


class FakeOpenAIClient:
    def __init__(
        self,
        stream: FakeStream,
        *,
        create_error: BaseException | None = None,
    ) -> None:
        self.responses = FakeResponses(stream, create_error=create_error)
        self.options: list[dict[str, object]] = []

    def with_options(self, **options):
        self.options.append(options)
        return self


class OpenAIStreamRequestTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_open_response_stream_reuses_request_builder_off_loop(self) -> None:
        event = {"type": "response.created", "sequence_number": 1}
        stream = FakeStream([event])
        client = FakeOpenAIClient(stream)
        model = OpenAIResponsesModel(
            model="unused-model",
            client=client,
            response_schema={"type": "object"},
        )
        request = ModelRequest(
            model_name="gpt-test",
            messages=(
                ChatMessage(role="system", content="Be concise."),
                ChatMessage(role="user", content="Check the stream."),
            ),
            tool_specs=(
                ToolSpec(
                    name="echo",
                    description="Echo text.",
                    arguments=[
                        ToolArgument(
                            name="text",
                            description="Text to echo.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="string",
                ),
            ),
            model_settings=ModelSettings(
                max_output_tokens=32,
                store=False,
                response_include=("reasoning.encrypted_content",),
                connect_timeout=3,
                read_timeout=7,
            ),
            request_id="request-1",
            attempt_id="attempt-1",
        )
        loop_thread_id = threading.get_ident()

        opened_stream = await model._open_response_stream(request)
        has_item, raw_event = await asyncio.to_thread(
            _next_stream_item,
            iter(opened_stream),
        )
        has_more, final_item = await asyncio.to_thread(
            _next_stream_item,
            iter(opened_stream),
        )

        self.assertIs(opened_stream, stream)
        self.assertEqual(len(client.options), 1)
        self.assertEqual(client.options[0]["max_retries"], 0)
        timeout = client.options[0]["timeout"]
        self.assertEqual(timeout.connect, 3)
        self.assertEqual(timeout.read, 7)
        self.assertEqual(timeout.write, 7)
        self.assertEqual(timeout.pool, 3)
        self.assertEqual(len(client.responses.requests), 1)
        self.assertNotEqual(client.responses.create_thread_ids, [loop_thread_id])
        self.assertTrue(all(value != loop_thread_id for value in stream.next_thread_ids))
        self.assertTrue(has_item)
        self.assertIs(raw_event, event)
        self.assertFalse(has_more)
        self.assertIsNone(final_item)

        sent = client.responses.requests[0]
        self.assertEqual(sent["model"], "gpt-test")
        self.assertEqual(sent["input"], [{"role": "user", "content": "Check the stream."}])
        self.assertEqual(sent["instructions"], "Be concise.")
        self.assertEqual(sent["max_output_tokens"], 32)
        self.assertFalse(sent["store"])
        self.assertEqual(sent["include"], ["reasoning.encrypted_content"])
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["tools"][0]["name"], "echo")
        self.assertEqual(model.last_input, sent["input"])
        self.assertEqual(model.last_tools, sent["tools"])
        self.assertEqual(
            model.last_request_summary,
            {
                "model": "gpt-test",
                "input_count": 1,
                "tool_count": 1,
                "has_schema": True,
                "uses_previous_response_id": False,
                "has_instructions": True,
                "store": False,
                "include_count": 1,
            },
        )

    async def test_non_stream_request_disables_sdk_retry_and_commits_on_success(
        self,
    ) -> None:
        response = SimpleNamespace(
            id="response-new",
            status="completed",
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "fallback"}],
                }
            ],
            usage={"total_tokens": 3},
            _request_id="sdk-request-1",
        )
        client = FakeOpenAIClient(response)
        model = OpenAIResponsesModel(
            model="unused-model",
            client=client,
            previous_response_id="response-old",
            pending_tool_outputs=[
                {
                    "type": "function_call_output",
                    "call_id": "call-old",
                    "output": "done",
                }
            ],
            last_response="old raw response",
            last_output_text="old text",
            last_ignored_provider_event_count=3,
        )
        request = ModelRequest(
            model_name="gpt-fallback",
            messages=(ChatMessage(role="user", content="Continue."),),
            tool_specs=(),
            model_settings=ModelSettings(
                connect_timeout=4,
                read_timeout=9,
            ),
            request_id="request-1",
            attempt_id="attempt-2",
        )

        result = await model.get_response_for_request(request)

        self.assertEqual(result.output_text, "fallback")
        self.assertIs(model.last_response, response)
        self.assertIsNone(model.previous_response_id)
        self.assertEqual(model.pending_tool_outputs, [])
        self.assertEqual(model.last_ignored_provider_event_count, 0)
        self.assertEqual(model.model, "unused-model")
        self.assertEqual(client.options[0]["max_retries"], 0)
        timeout = client.options[0]["timeout"]
        self.assertEqual(timeout.connect, 4)
        self.assertEqual(timeout.read, 9)
        sent = client.responses.requests[0]
        self.assertEqual(sent["model"], "gpt-fallback")
        self.assertIs(sent["stream"], False)
        self.assertNotIn("previous_response_id", sent)
        self.assertEqual(sent["input"], [{"role": "user", "content": "Continue."}])

    async def test_non_stream_request_failure_preserves_response_state(self) -> None:
        request_error = RuntimeError("request failed")
        request_error.status_code = 500
        client = FakeOpenAIClient(
            FakeStream([]),
            create_error=request_error,
        )
        pending = [
            {
                "type": "function_call_output",
                "call_id": "call-old",
                "output": "done",
            }
        ]
        model = OpenAIResponsesModel(
            client=client,
            previous_response_id="response-old",
            pending_tool_outputs=list(pending),
            last_response="old raw response",
            last_output_text="old text",
            last_ignored_provider_event_count=3,
        )
        request = ModelRequest(
            model_name="gpt-fallback",
            messages=(ChatMessage(role="user", content="Continue."),),
            tool_specs=(),
            model_settings=ModelSettings(),
            request_id="request-1",
            attempt_id="attempt-2",
        )

        with self.assertRaises(ModelResponseError) as caught:
            await model.get_response_for_request(request)

        self.assertEqual(caught.exception.kind, ModelErrorKind.SERVER)
        self.assertEqual(model.previous_response_id, "response-old")
        self.assertEqual(model.pending_tool_outputs, pending)
        self.assertEqual(model.last_response, "old raw response")
        self.assertEqual(model.last_output_text, "old text")
        self.assertEqual(model.last_ignored_provider_event_count, 0)

    async def test_next_stream_item_preserves_iterator_errors(self) -> None:
        class BrokenIterator:
            def __next__(self):
                raise ValueError("broken stream")

        with self.assertRaisesRegex(ValueError, "broken stream"):
            await asyncio.to_thread(_next_stream_item, BrokenIterator())


class OpenAIEventMapperTestCase(unittest.TestCase):
    def test_lifecycle_events_use_continuous_internal_sequences(self) -> None:
        mapper = _OpenAIEventMapper(
            attempt_id="attempt-1",
            request_summary={"model": "gpt-test"},
        )
        created = mapper.map(
            {
                "type": "response.created",
                "sequence_number": 1,
                "response": {"id": "response-1"},
            }
        )
        ignored_event = {
            "type": "response.in_progress",
            "sequence_number": 2,
            "provider_payload": "secret-provider-payload",
        }
        ignored = mapper.map(ignored_event)
        also_ignored = mapper.map(
            {
                "type": "response.queued",
                "sequence_number": 3,
            }
        )
        response = SimpleNamespace(
            id="response-1",
            output=({"type": "message"},),
            usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            _request_id="sdk-request-1",
        )
        completed = mapper.map(
            SimpleNamespace(
                type="response.completed",
                sequence_number=4,
                response=response,
            )
        )

        self.assertEqual(len(created), 1)
        self.assertIsInstance(created[0], ResponseCreated)
        self.assertEqual(created[0].sequence, 1)
        self.assertEqual(created[0].response_id, "response-1")
        self.assertEqual(ignored, ())
        self.assertEqual(also_ignored, ())
        self.assertEqual(mapper.ignored_event_count, 2)
        self.assertNotIn("secret-provider-payload", repr(vars(mapper)))
        self.assertEqual(len(completed), 2)
        self.assertIsInstance(completed[0], UsageUpdated)
        self.assertEqual(completed[0].sequence, 2)
        self.assertEqual(completed[0].usage["total_tokens"], 5)
        self.assertIsInstance(completed[1], ResponseCompleted)
        self.assertEqual(completed[1].sequence, 3)
        self.assertEqual(completed[1].output, [{"type": "message"}])
        self.assertIs(completed[1].raw, response)
        self.assertEqual(completed[1].request_summary, {"model": "gpt-test"})
        self.assertEqual(completed[1].request_id, "sdk-request-1")
        self.assertEqual(mapper.last_provider_sequence, 4)
        self.assertEqual(mapper.next_sequence, 4)

    def test_completed_without_usage_emits_only_terminal_event(self) -> None:
        mapper = _OpenAIEventMapper(attempt_id="attempt-1")
        mapper.map(
            {
                "type": "response.created",
                "sequence_number": 1,
                "response": {"id": "response-1"},
            }
        )

        events = mapper.map(
            {
                "type": "response.completed",
                "sequence_number": 2,
                "response": {"id": "response-1", "output": []},
            }
        )

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ResponseCompleted)
        self.assertEqual(events[0].sequence, 2)

    def test_invalid_event_fields_raise_protocol_error_for_the_attempt(self) -> None:
        invalid_events = (
            {"type": "response.created", "response": {"id": "response-1"}},
            {
                "type": "response.created",
                "sequence_number": True,
                "response": {"id": "response-1"},
            },
            {"sequence_number": 1, "response": {"id": "response-1"}},
            {
                "type": "response.created",
                "sequence_number": 1,
                "response": {},
            },
            {
                "type": "response.completed",
                "sequence_number": 1,
                "response": {"id": "response-1", "output": None},
            },
        )
        for raw_event in invalid_events:
            with self.subTest(raw_event=raw_event):
                with self.assertRaises(ModelProtocolError) as caught:
                    _OpenAIEventMapper(attempt_id="attempt-1").map(raw_event)
                self.assertEqual(caught.exception.attempt_id, "attempt-1")

        for invalid_sequence in (2, 1):
            with self.subTest(invalid_sequence=invalid_sequence):
                mapper = _OpenAIEventMapper(attempt_id="attempt-1")
                mapper.map(
                    {
                        "type": "response.created",
                        "sequence_number": 2,
                        "response": {"id": "response-1"},
                    }
                )
                with self.assertRaises(ModelProtocolError) as caught:
                    mapper.map(
                        {
                            "type": "response.in_progress",
                            "sequence_number": invalid_sequence,
                        }
                    )
                self.assertEqual(caught.exception.attempt_id, "attempt-1")

    def test_content_events_reassemble_text_reasoning_and_refusal(self) -> None:
        mapper = _OpenAIEventMapper(attempt_id="attempt-1")
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        raw_events = [
            {"type": "response.created", "response": {"id": "response-1"}},
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "content_index": 0,
                "delta": "后",
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "",
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "前",
            },
            {
                "type": "response.output_text.done",
                "output_index": 1,
                "content_index": 0,
                "text": "后",
            },
            {
                "type": "response.output_text.done",
                "output_index": 0,
                "content_index": 0,
                "text": "前",
            },
            {
                "type": "response.reasoning_text.delta",
                "output_index": 2,
                "content_index": 0,
                "delta": "思考",
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 2,
                "summary_index": 0,
                "delta": "摘要",
            },
            {
                "type": "response.reasoning_text.done",
                "output_index": 2,
                "content_index": 0,
                "text": "思考",
            },
            {
                "type": "response.reasoning_summary_text.done",
                "output_index": 2,
                "summary_index": 0,
                "text": "摘要",
            },
            {
                "type": "response.refusal.delta",
                "output_index": 3,
                "content_index": 0,
                "delta": "不能处理",
            },
            {
                "type": "response.refusal.done",
                "output_index": 3,
                "content_index": 0,
                "refusal": "不能处理",
            },
            {
                "type": "response.completed",
                "response": {"id": "response-1", "output": []},
            },
        ]

        for provider_sequence, raw_event in enumerate(raw_events, start=1):
            raw_event["sequence_number"] = provider_sequence
            for event in mapper.map(raw_event):
                accumulator.apply(event)

        self.assertEqual(accumulator.text, "前后")
        self.assertEqual(accumulator.reasoning, "思考摘要")
        self.assertEqual(accumulator.refusal, "不能处理")
        response = accumulator.finish()
        self.assertEqual(response.output_text, "前后")
        self.assertEqual(response.refusal, "不能处理")
        self.assertNotIn("思考", response.output_text)

    def test_done_text_must_match_accumulated_delta(self) -> None:
        mapper = _OpenAIEventMapper(attempt_id="attempt-1")
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        raw_events = (
            {
                "type": "response.created",
                "sequence_number": 1,
                "response": {"id": "response-1"},
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 2,
                "output_index": 0,
                "content_index": 0,
                "delta": "实际文字",
            },
        )
        for raw_event in raw_events:
            for event in mapper.map(raw_event):
                accumulator.apply(event)

        done_event = mapper.map(
            {
                "type": "response.output_text.done",
                "sequence_number": 3,
                "output_index": 0,
                "content_index": 0,
                "text": "不同文字",
            }
        )[0]
        with self.assertRaises(ModelProtocolError) as caught:
            accumulator.apply(done_event)
        self.assertEqual(caught.exception.attempt_id, "attempt-1")

    def test_invalid_content_fields_raise_protocol_error(self) -> None:
        invalid_events = (
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "content_index": 0,
                "delta": "text",
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "output_index": -1,
                "content_index": 0,
                "delta": "text",
            },
            {
                "type": "response.reasoning_text.delta",
                "sequence_number": 1,
                "output_index": 0,
                "content_index": None,
                "delta": "reasoning",
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "sequence_number": 1,
                "output_index": 0,
                "summary_index": -1,
                "delta": "summary",
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "output_index": 0,
                "content_index": 0,
                "delta": None,
            },
            {
                "type": "response.refusal.done",
                "sequence_number": 1,
                "output_index": 0,
                "content_index": 0,
                "text": "wrong field",
            },
        )
        for raw_event in invalid_events:
            with self.subTest(raw_event=raw_event):
                with self.assertRaises(ModelProtocolError) as caught:
                    _OpenAIEventMapper(attempt_id="attempt-1").map(raw_event)
                self.assertEqual(caught.exception.attempt_id, "attempt-1")

    def test_interleaved_function_calls_finish_in_output_order(self) -> None:
        mapper = _OpenAIEventMapper(attempt_id="attempt-1")
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=100)
        raw_events = [
            {"type": "response.created", "response": {"id": "response-1"}},
            SimpleNamespace(
                type="response.output_item.added",
                output_index=1,
                item=SimpleNamespace(
                    type="function_call",
                    id="item-b",
                    call_id="call-b",
                    name="beta",
                    arguments="",
                ),
            ),
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "id": "item-a",
                    "call_id": "call-a",
                    "name": "alpha",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item-b",
                "output_index": 1,
                "delta": '{"b":',
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item-a",
                "output_index": 0,
                "delta": '{"a":',
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item-b",
                "output_index": 1,
                "delta": "2}",
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item-a",
                "output_index": 0,
                "delta": "1}",
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "item-b",
                "output_index": 1,
                "name": "beta",
                "arguments": '{"b":2}',
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "item-a",
                "output_index": 0,
                "name": "alpha",
                "arguments": '{"a":1}',
            },
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": {
                    "type": "function_call",
                    "id": "item-b",
                    "call_id": "call-b",
                    "name": "beta",
                    "arguments": '{"b":2}',
                },
            },
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item=SimpleNamespace(
                    type="function_call",
                    id="item-a",
                    call_id="call-a",
                    name="alpha",
                    arguments='{"a":1}',
                ),
            ),
            {
                "type": "response.completed",
                "response": {"id": "response-1", "output": []},
            },
        ]

        for provider_sequence, raw_event in enumerate(raw_events, start=1):
            if isinstance(raw_event, dict):
                raw_event["sequence_number"] = provider_sequence
            else:
                raw_event.sequence_number = provider_sequence
            for event in mapper.map(raw_event):
                accumulator.apply(event)

        response = accumulator.finish()
        self.assertEqual(
            [tool_call.tool_name for tool_call in response.tool_calls],
            ["alpha", "beta"],
        )
        self.assertEqual(response.tool_calls[0].arguments, {"a": 1})
        self.assertEqual(response.tool_calls[0].call_id, "call-a")
        self.assertEqual(response.tool_calls[1].arguments, {"b": 2})
        self.assertEqual(response.tool_calls[1].call_id, "call-b")

    def test_function_call_identity_and_completion_conflicts_fail(self) -> None:
        def started_mapper() -> _OpenAIEventMapper:
            mapper = _OpenAIEventMapper(attempt_id="attempt-1")
            mapper.map(
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-1"},
                }
            )
            mapper.map(
                {
                    "type": "response.output_item.added",
                    "sequence_number": 2,
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "id": "item-1",
                        "call_id": "call-1",
                        "name": "echo",
                    },
                }
            )
            return mapper

        scenarios = []
        mapper = _OpenAIEventMapper(attempt_id="attempt-1")
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": 1,
                    "item_id": "missing",
                    "output_index": 0,
                    "delta": "{}",
                },
            )
        )
        mapper = started_mapper()
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": 3,
                    "item_id": "item-1",
                    "output_index": 1,
                    "delta": "{}",
                },
            )
        )
        mapper = started_mapper()
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": 3,
                    "item_id": "item-1",
                    "output_index": 0,
                    "name": "changed",
                    "arguments": "{}",
                },
            )
        )
        mapper = started_mapper()
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.output_item.done",
                    "sequence_number": 3,
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "id": "item-1",
                        "call_id": "call-1",
                        "name": "echo",
                        "arguments": "{}",
                    },
                },
            )
        )
        mapper = started_mapper()
        mapper.map(
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": 3,
                "item_id": "item-1",
                "output_index": 0,
                "name": "echo",
                "arguments": "{}",
            }
        )
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.completed",
                    "sequence_number": 4,
                    "response": {"id": "response-1", "output": []},
                },
            )
        )

        mapper = started_mapper()
        mapper.map(
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": 3,
                "item_id": "item-1",
                "output_index": 0,
                "name": "echo",
                "arguments": "{}",
            }
        )
        self.assertEqual(
            mapper.map(
                {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": 4,
                    "item_id": "item-1",
                    "output_index": 0,
                    "name": "echo",
                    "arguments": "{}",
                }
            ),
            (),
        )
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": 5,
                    "item_id": "item-1",
                    "output_index": 0,
                    "name": "echo",
                    "arguments": '{"changed":true}',
                },
            )
        )

        mapper = started_mapper()
        mapper.map(
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": 3,
                "item_id": "item-1",
                "output_index": 0,
                "name": "echo",
                "arguments": "{}",
            }
        )
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": 4,
                    "item_id": "item-1",
                    "output_index": 0,
                    "delta": "more",
                },
            )
        )

        mapper = started_mapper()
        mapper.map(
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": 3,
                "item_id": "item-1",
                "output_index": 0,
                "name": "echo",
                "arguments": "{}",
            }
        )
        scenarios.append(
            (
                mapper,
                {
                    "type": "response.output_item.done",
                    "sequence_number": 4,
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "id": "item-1",
                        "call_id": "changed-call",
                        "name": "echo",
                        "arguments": "{}",
                    },
                },
            )
        )

        for mapper, raw_event in scenarios:
            with self.subTest(raw_event=raw_event):
                with self.assertRaises(ModelProtocolError) as caught:
                    mapper.map(raw_event)
                self.assertEqual(caught.exception.attempt_id, "attempt-1")

    def test_duplicate_function_call_identity_fails(self) -> None:
        duplicate_items = (
            {
                "type": "function_call",
                "id": "item-1",
                "call_id": "call-2",
                "name": "second",
                "output_index": 1,
            },
            {
                "type": "function_call",
                "id": "item-2",
                "call_id": "call-1",
                "name": "second",
                "output_index": 1,
            },
            {
                "type": "function_call",
                "id": "item-2",
                "call_id": "call-2",
                "name": "second",
                "output_index": 0,
            },
        )
        for duplicate in duplicate_items:
            with self.subTest(duplicate=duplicate):
                mapper = _OpenAIEventMapper(attempt_id="attempt-1")
                mapper.map(
                    {
                        "type": "response.output_item.added",
                        "sequence_number": 1,
                        "output_index": 0,
                        "item": {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "first",
                        },
                    }
                )
                output_index = duplicate.pop("output_index")
                with self.assertRaises(ModelProtocolError):
                    mapper.map(
                        {
                            "type": "response.output_item.added",
                            "sequence_number": 2,
                            "output_index": output_index,
                            "item": duplicate,
                        }
                    )

    def test_invalid_function_arguments_fail_in_existing_accumulator(self) -> None:
        for arguments in ("{", "[]"):
            with self.subTest(arguments=arguments):
                mapper = _OpenAIEventMapper(attempt_id="attempt-1")
                accumulator = ModelEventAccumulator(
                    "attempt-1",
                    max_buffer_chars=100,
                )
                raw_events = (
                    {
                        "type": "response.created",
                        "sequence_number": 1,
                        "response": {"id": "response-1"},
                    },
                    {
                        "type": "response.output_item.added",
                        "sequence_number": 2,
                        "output_index": 0,
                        "item": {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "echo",
                        },
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "sequence_number": 3,
                        "item_id": "item-1",
                        "output_index": 0,
                        "delta": arguments,
                    },
                    {
                        "type": "response.function_call_arguments.done",
                        "sequence_number": 4,
                        "item_id": "item-1",
                        "output_index": 0,
                        "name": "echo",
                        "arguments": arguments,
                    },
                )
                for raw_event in raw_events[:-1]:
                    for event in mapper.map(raw_event):
                        accumulator.apply(event)
                done_event = mapper.map(raw_events[-1])[0]
                with self.assertRaises(ModelProtocolError):
                    accumulator.apply(done_event)


class OpenAIStreamResponseTestCase(unittest.IsolatedAsyncioTestCase):
    def make_request(
        self,
        settings: ModelSettings | None = None,
    ) -> ModelRequest:
        return ModelRequest(
            model_name="gpt-test",
            messages=(ChatMessage(role="user", content="Stream this."),),
            tool_specs=(),
            model_settings=settings or ModelSettings(),
            request_id="request-1",
            attempt_id="attempt-1",
        )

    def make_model(self, stream: FakeStream, **changes) -> OpenAIResponsesModel:
        return OpenAIResponsesModel(
            model=changes.pop("model", "gpt-test"),
            client=FakeOpenAIClient(stream),
            previous_response_id="response-old",
            pending_tool_outputs=[
                {
                    "type": "function_call_output",
                    "call_id": "call-old",
                    "output": "done",
                }
            ],
            last_response="old raw response",
            last_output_text="old text",
            **changes,
        )

    async def test_complete_stream_commits_state_and_closes_off_loop(self) -> None:
        response = SimpleNamespace(
            id="response-new",
            output=[],
            usage={"total_tokens": 3},
            _request_id="sdk-request-1",
        )
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                {
                    "type": "response.in_progress",
                    "sequence_number": 2,
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 3,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "hello",
                },
                {
                    "type": "response.output_text.done",
                    "sequence_number": 4,
                    "output_index": 0,
                    "content_index": 0,
                    "text": "hello",
                },
                SimpleNamespace(
                    type="response.completed",
                    sequence_number=5,
                    response=response,
                ),
            ]
        )
        model = self.make_model(stream)
        loop_thread_id = threading.get_ident()

        events = [event async for event in model.stream_response(self.make_request())]

        self.assertEqual([event.sequence for event in events], [1, 2, 3, 4, 5])
        self.assertIs(model.last_response, response)
        self.assertEqual(model.last_output_text, "hello")
        self.assertEqual(model.previous_response_id, "response-new")
        self.assertEqual(model.pending_tool_outputs, [])
        self.assertEqual(model.last_ignored_provider_event_count, 1)
        self.assertTrue(stream.closed)
        self.assertTrue(all(value != loop_thread_id for value in stream.close_thread_ids))

    async def test_backup_stream_does_not_reuse_or_store_primary_response_id(
        self,
    ) -> None:
        response = SimpleNamespace(
            id="response-backup",
            output=[],
            usage=None,
        )
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-backup"},
                },
                SimpleNamespace(
                    type="response.completed",
                    sequence_number=2,
                    response=response,
                ),
            ]
        )
        model = self.make_model(stream, model="model-a")

        events = [event async for event in model.stream_response(self.make_request())]

        self.assertEqual(events[-1].response_id, "response-backup")
        self.assertEqual(model.model, "model-a")
        self.assertIsNone(model.previous_response_id)
        self.assertEqual(model.pending_tool_outputs, [])
        sent = model.client.responses.requests[0]
        self.assertNotIn("previous_response_id", sent)
        self.assertEqual(
            sent["input"],
            [{"role": "user", "content": "Stream this."}],
        )

    async def test_failed_stream_preserves_previous_state(self) -> None:
        failed_response = SimpleNamespace(
            error=SimpleNamespace(code="server_error", message="generation failed"),
            _request_id="sdk-request-1",
        )
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                {
                    "type": "response.in_progress",
                    "sequence_number": 2,
                },
                SimpleNamespace(
                    type="response.failed",
                    sequence_number=3,
                    response=failed_response,
                ),
            ]
        )
        model = self.make_model(stream)

        with self.assertRaisesRegex(ModelResponseError, "generation failed") as caught:
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertEqual(caught.exception.kind, ModelErrorKind.SERVER)
        self.assertEqual(caught.exception.request_id, "sdk-request-1")
        self.assertEqual(model.previous_response_id, "response-old")
        self.assertEqual(model.last_response, "old raw response")
        self.assertEqual(model.last_output_text, "old text")
        self.assertEqual(len(model.pending_tool_outputs), 1)
        self.assertEqual(model.last_ignored_provider_event_count, 1)
        self.assertTrue(stream.closed)

    async def test_error_before_created_is_stable_and_closes_stream(self) -> None:
        stream = FakeStream(
            [
                {
                    "type": "error",
                    "sequence_number": 1,
                    "code": "context_length_exceeded",
                    "message": "too much input",
                    "param": None,
                }
            ]
        )
        model = self.make_model(stream)

        with self.assertRaisesRegex(ModelResponseError, "too much input") as caught:
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertEqual(caught.exception.kind, ModelErrorKind.CONTEXT_WINDOW)
        self.assertEqual(caught.exception.attempt_id, "attempt-1")
        self.assertTrue(stream.closed)

    async def test_error_after_created_becomes_terminal_failure(self) -> None:
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                {
                    "type": "error",
                    "sequence_number": 2,
                    "code": "server_error",
                    "message": "stream failed",
                    "param": None,
                },
            ]
        )
        model = self.make_model(stream)

        with self.assertRaisesRegex(ModelResponseError, "stream failed") as caught:
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertEqual(caught.exception.kind, ModelErrorKind.SERVER)
        self.assertEqual(model.previous_response_id, "response-old")
        self.assertEqual(len(model.pending_tool_outputs), 1)
        self.assertTrue(stream.closed)

    async def test_incomplete_context_response_uses_context_error(self) -> None:
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                {
                    "type": "response.incomplete",
                    "sequence_number": 2,
                    "response": {
                        "incomplete_details": {
                            "reason": "context_length_exceeded"
                        },
                        "request_id": "sdk-request-1",
                    },
                },
            ]
        )
        model = self.make_model(stream)

        with self.assertRaises(ModelResponseError) as caught:
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertEqual(caught.exception.kind, ModelErrorKind.CONTEXT_WINDOW)
        self.assertEqual(model.previous_response_id, "response-old")
        self.assertEqual(len(model.pending_tool_outputs), 1)
        self.assertTrue(stream.closed)

    async def test_early_close_and_buffer_overflow_close_without_commit(self) -> None:
        scenarios = (
            (
                FakeStream(
                    [
                        {
                            "type": "response.created",
                            "sequence_number": 1,
                            "response": {"id": "response-new"},
                        }
                    ]
                ),
                100,
                ModelErrorKind.EARLY_CLOSE,
            ),
            (
                FakeStream(
                    [
                        {
                            "type": "response.created",
                            "sequence_number": 1,
                            "response": {"id": "response-new"},
                        },
                        {
                            "type": "response.output_text.delta",
                            "sequence_number": 2,
                            "output_index": 0,
                            "content_index": 0,
                            "delta": "too long",
                        },
                    ]
                ),
                3,
                ModelErrorKind.BUFFER_OVERFLOW,
            ),
        )
        for stream, max_buffer_chars, expected_kind in scenarios:
            with self.subTest(expected_kind=expected_kind):
                model = self.make_model(stream, max_buffer_chars=max_buffer_chars)
                with self.assertRaises(ModelProtocolError) as caught:
                    async for _ in model.stream_response(self.make_request()):
                        pass
                self.assertEqual(caught.exception.kind, expected_kind)
                if expected_kind is ModelErrorKind.BUFFER_OVERFLOW:
                    self.assertEqual(caught.exception.max_buffer_chars, 3)
                    self.assertEqual(caught.exception.attempted_buffer_chars, 8)
                self.assertEqual(model.previous_response_id, "response-old")
                self.assertEqual(model.last_response, "old raw response")
                self.assertEqual(model.last_output_text, "old text")
                self.assertEqual(len(model.pending_tool_outputs), 1)
                self.assertTrue(stream.closed)

    async def test_request_buffer_limit_overrides_model_default(self) -> None:
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 2,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "too long",
                },
            ]
        )
        model = self.make_model(stream, max_buffer_chars=100)
        request = self.make_request(
            ModelSettings(max_stream_buffer_chars=3)
        )

        with self.assertRaises(ModelProtocolError) as caught:
            async for _ in model.stream_response(request):
                pass

        self.assertEqual(caught.exception.kind, ModelErrorKind.BUFFER_OVERFLOW)
        self.assertTrue(stream.closed)

    async def test_main_stream_error_wins_over_close_error(self) -> None:
        read_error = RuntimeError("iterator broke")
        read_error.status_code = 404
        stream = FakeStream(
            [],
            next_error=read_error,
            close_error=RuntimeError("close broke"),
        )
        model = self.make_model(stream)

        with self.assertRaisesRegex(ModelResponseError, "iterator broke") as caught:
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertEqual(caught.exception.kind, ModelErrorKind.INVALID_REQUEST)
        self.assertIs(caught.exception.original, read_error)
        self.assertTrue(stream.closed)

    async def test_stream_open_404_is_classified_before_any_output(self) -> None:
        open_error = RuntimeError("stream endpoint unavailable")
        open_error.status_code = 404
        open_error.response = SimpleNamespace(
            headers={"x-request-id": "request-open-1"}
        )
        stream = FakeStream([])
        model = OpenAIResponsesModel(
            client=FakeOpenAIClient(stream, create_error=open_error),
            previous_response_id="response-old",
            last_response="old raw response",
        )

        with self.assertRaises(ModelResponseError) as caught:
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertEqual(
            caught.exception.kind,
            ModelErrorKind.STREAM_UNAVAILABLE,
        )
        self.assertEqual(caught.exception.request_id, "request-open-1")
        self.assertIs(caught.exception.original, open_error)
        self.assertEqual(model.previous_response_id, "response-old")
        self.assertEqual(model.last_response, "old raw response")
        self.assertFalse(stream.closed)

    async def test_consumer_aclose_closes_stream_without_commit(self) -> None:
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 2,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "partial",
                },
            ]
        )
        model = self.make_model(stream)
        events = model.stream_response(self.make_request())

        first = await anext(events)
        await events.aclose()

        self.assertIsInstance(first, ResponseCreated)
        self.assertTrue(stream.closed)
        self.assertEqual(model.previous_response_id, "response-old")
        self.assertEqual(len(model.pending_tool_outputs), 1)

    async def test_close_error_is_reported_after_completed_response(self) -> None:
        response = SimpleNamespace(id="response-new", output=[], usage=None)
        stream = FakeStream(
            [
                {
                    "type": "response.created",
                    "sequence_number": 1,
                    "response": {"id": "response-new"},
                },
                SimpleNamespace(
                    type="response.completed",
                    sequence_number=2,
                    response=response,
                ),
            ],
            close_error=RuntimeError("close broke"),
        )
        model = self.make_model(stream)

        with self.assertRaisesRegex(RuntimeError, "close broke"):
            async for _ in model.stream_response(self.make_request()):
                pass

        self.assertIs(model.last_response, response)
        self.assertEqual(model.previous_response_id, "response-new")
        self.assertEqual(model.pending_tool_outputs, [])
        self.assertTrue(stream.closed)

    def test_max_buffer_chars_must_be_a_positive_integer(self) -> None:
        for value in (True, 0, -1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    OpenAIResponsesModel(client=object(), max_buffer_chars=value)


class ModelStreamCompatibilityTestCase(unittest.IsolatedAsyncioTestCase):
    def make_request(self, settings: ModelSettings | None = None) -> ModelRequest:
        return ModelRequest(
            model_name="sync-test",
            messages=(ChatMessage(role="user", content="Use the model."),),
            tool_specs=(),
            model_settings=settings or ModelSettings(),
            request_id="request-compat",
            attempt_id="attempt-compat",
        )

    async def test_native_stream_has_priority(self) -> None:
        class CombinedModel:
            def __init__(self) -> None:
                self.native_calls = 0

            async def stream_response(self, request):
                self.native_calls += 1
                yield ResponseCreated(
                    attempt_id=request.attempt_id,
                    sequence=1,
                    response_id="native-response",
                )
                yield ResponseCompleted(
                    attempt_id=request.attempt_id,
                    sequence=2,
                    response_id="native-response",
                    output=[],
                )

            def get_response(self, messages, tool_specs):
                raise AssertionError("native stream must win")

            def decide(self, messages, tool_specs):
                raise AssertionError("native stream must win")

        model = CombinedModel()

        events = [
            event async for event in stream_model_response(model, self.make_request())
        ]

        self.assertEqual(model.native_calls, 1)
        self.assertEqual([event.sequence for event in events], [1, 2])

    async def test_sync_response_round_trips_through_standard_events(self) -> None:
        raw = object()
        settings = ModelSettings(temperature=0.25)
        original = ModelResponse(
            response_id="sync-response",
            output=[{"type": "message"}],
            output_text="complete text",
            tool_calls=[
                ToolCall("alpha", {"value": 1}, "call-a"),
                ToolCall("beta", {"value": "二"}, "call-b"),
            ],
            refusal="refusal text",
            raw=raw,
            usage={"total_tokens": 7},
            request_summary={"model": "sync-test"},
            request_id="sdk-request",
        )

        class SyncModel:
            def __init__(self) -> None:
                self.thread_id = None
                self.settings = None

            def get_response(
                self,
                messages,
                tool_specs,
                model_settings=None,
            ):
                self.thread_id = threading.get_ident()
                self.settings = model_settings
                return original

        model = SyncModel()
        request = self.make_request(settings)
        loop_thread_id = threading.get_ident()
        accumulator = ModelEventAccumulator(
            request.attempt_id,
            max_buffer_chars=1_000,
        )

        async for event in stream_model_response(model, request):
            accumulator.apply(event)
        rebuilt = accumulator.finish()

        self.assertNotEqual(model.thread_id, loop_thread_id)
        self.assertIs(model.settings, settings)
        self.assertEqual(rebuilt.response_id, original.response_id)
        self.assertEqual(rebuilt.output, original.output)
        self.assertEqual(rebuilt.output_text, original.output_text)
        self.assertEqual(rebuilt.tool_calls, original.tool_calls)
        self.assertEqual(rebuilt.refusal, original.refusal)
        self.assertIs(rebuilt.raw, raw)
        self.assertEqual(rebuilt.usage, original.usage)
        self.assertEqual(rebuilt.request_summary, original.request_summary)
        self.assertEqual(rebuilt.request_id, original.request_id)

    async def test_legacy_decide_returns_tool_or_empty_response(self) -> None:
        loop_thread_id = threading.get_ident()
        for tool_call in (ToolCall("echo", {"text": "hi"}, "call-1"), None):
            with self.subTest(tool_call=tool_call):
                class LegacyModel:
                    def __init__(self) -> None:
                        self.thread_id = None

                    def decide(self, messages, tool_specs):
                        self.thread_id = threading.get_ident()
                        return tool_call

                model = LegacyModel()
                request = self.make_request()
                accumulator = ModelEventAccumulator(
                    request.attempt_id,
                    max_buffer_chars=100,
                )
                async for event in stream_model_response(model, request):
                    accumulator.apply(event)
                rebuilt = accumulator.finish()

                self.assertNotEqual(model.thread_id, loop_thread_id)
                self.assertEqual(rebuilt.response_id, request.request_id)
                self.assertEqual(
                    rebuilt.tool_calls,
                    [] if tool_call is None else [tool_call],
                )

    async def test_sync_error_and_missing_capability_are_not_hidden(self) -> None:
        class BrokenModel:
            def get_response(self, messages, tool_specs):
                raise ValueError("sync failed")

        with self.assertRaisesRegex(ValueError, "sync failed"):
            async for _ in stream_model_response(BrokenModel(), self.make_request()):
                pass

        with self.assertRaisesRegex(TypeError, "get_response.*decide"):
            async for _ in stream_model_response(object(), self.make_request()):
                pass


if __name__ == "__main__":
    unittest.main()
