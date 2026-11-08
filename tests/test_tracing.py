from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    ModelErrorKind,
    ModelResponseError,
    ModelSettings,
    RunConfig,
)
from agents.contracts import ModelResponse  # noqa: E402
from agents.events import RunCancelledError  # noqa: E402
from agents.model_retry import ModelAttemptRecord  # noqa: E402
from agents.model_turn import _record_model_execution_span  # noqa: E402
from agents.runner import Runner  # noqa: E402
from agents.tracing import (  # noqa: E402
    BatchTraceProcessor,
    DebugTracingProcessor,
    ExportingTracingProcessor,
    agent_span,
    custom_span,
    function_span,
    generation_span,
    guardrail_span,
    handoff_span,
    InMemoryTracingProcessor,
    InMemoryTracingExporter,
    JSONLTracingExporter,
    model_span,
    NoOpSpan,
    NoOpTrace,
    SpanData,
    SpanError,
    SpanRecord,
    SynchronousMultiTracingProcessor,
    TraceRecord,
    TracingProcessor,
    set_trace_processors,
    gen_span_id,
    gen_trace_id,
    get_current_span,
    get_current_trace,
    span,
    task_span,
    tool_span,
    trace,
    turn_span,
)


class FinalModel:
    def get_response(self, messages, tool_specs):
        return ModelResponse(
            response_id="response-final",
            output=[],
            output_text="done",
            tool_calls=[],
        )


class TracingDataTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        set_trace_processors([])

    def test_generated_ids_use_trace_and_span_prefixes(self) -> None:
        self.assertTrue(gen_trace_id().startswith("trace_"))
        self.assertTrue(gen_span_id().startswith("span_"))

    def test_span_record_exports_structured_error(self) -> None:
        error: SpanError = {
            "message": "tool failed",
            "data": {"error_type": "ToolExecutionError"},
        }
        span = SpanRecord(
            span_id="span_error",
            trace_id="trace_error",
            parent_id=None,
            span_data=SpanData(span_type="function", name="search"),
        )

        span.set_error(error)
        exported = span.export()

        self.assertEqual(exported["error"], error)

    def test_trace_record_exports_only_trace_metadata(self) -> None:
        span = SpanRecord(
            span_id="span_child",
            trace_id="trace_parent",
            parent_id=None,
            span_data=SpanData(span_type="tool", name="search"),
        )
        trace = TraceRecord(
            trace_id="trace_parent",
            name="workflow",
            group_id="group_1",
            metadata={"lesson": 1},
            spans=[span],
        )

        exported = trace.export()

        self.assertEqual(exported["id"], "trace_parent")
        self.assertEqual(exported["workflow_name"], "workflow")
        self.assertEqual(exported["group_id"], "group_1")
        self.assertEqual(exported["metadata"], {"lesson": 1})
        self.assertNotIn("spans", exported)

    def test_trace_context_sets_and_resets_current_trace(self) -> None:
        self.assertIsNone(get_current_trace())

        with trace(
            "lesson workflow",
            trace_id="trace_lesson",
            group_id="course",
            metadata={"lesson": 2},
        ) as active_trace:
            self.assertIs(get_current_trace(), active_trace)
            self.assertEqual(active_trace.record.name, "lesson workflow")

        self.assertIsNone(get_current_trace())
        self.assertIsNotNone(active_trace.record.ended_at)

    def test_span_context_records_parent_child_relationship(self) -> None:
        with trace("workflow", trace_id="trace_parent") as active_trace:
            with span("agent", "Assistant") as parent_span:
                self.assertIs(get_current_span(), parent_span)

                with span("tool", "search") as child_span:
                    self.assertIs(get_current_span(), child_span)
                    self.assertEqual(
                        child_span.record.parent_id,
                        parent_span.record.span_id,
                    )

                self.assertIs(get_current_span(), parent_span)

            self.assertIsNone(get_current_span())

        self.assertEqual(len(active_trace.record.spans), 2)
        self.assertEqual(active_trace.record.spans[0].span_data.span_type, "agent")
        self.assertEqual(active_trace.record.spans[1].span_data.span_type, "tool")
        self.assertIsNotNone(active_trace.record.spans[0].ended_at)
        self.assertIsNotNone(active_trace.record.spans[1].ended_at)

    def test_debug_processor_receives_lifecycle_events(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])

        with trace("workflow", trace_id="trace_processor"):
            with span("agent", "Assistant"):
                pass

        events = processor.events()

        self.assertEqual(
            [event["event"] for event in events],
            ["trace_start", "span_start", "span_end", "trace_end"],
        )
        self.assertEqual(events[0]["item"]["id"], "trace_processor")
        self.assertIsNone(events[1]["item"]["ended_at"])
        self.assertIsNotNone(events[2]["item"]["ended_at"])
        self.assertIsNotNone(events[3]["item"]["ended_at"])

    def test_multi_processor_forwards_events_to_each_processor(self) -> None:
        first = DebugTracingProcessor()
        second = DebugTracingProcessor()
        multi_processor = SynchronousMultiTracingProcessor([first])
        multi_processor.add_tracing_processor(second)
        set_trace_processors([multi_processor])

        with trace("workflow", trace_id="trace_multi"):
            with span("tool", "search"):
                pass

        self.assertEqual(multi_processor.processors(), [first, second])
        self.assertEqual(
            [event["event"] for event in first.events()],
            ["trace_start", "span_start", "span_end", "trace_end"],
        )
        self.assertEqual(first.events(), second.events())

    def test_old_in_memory_processor_name_is_debug_processor_alias(self) -> None:
        self.assertIs(InMemoryTracingProcessor, DebugTracingProcessor)

    def test_exporting_processor_sends_finished_items_to_exporter(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = ExportingTracingProcessor(exporter)
        set_trace_processors([processor])

        with trace("workflow", trace_id="trace_export"):
            with span("tool", "search"):
                pass

        exported = exporter.items()

        self.assertEqual([item["object"] for item in exported], ["trace.span", "trace"])
        self.assertEqual(exported[0]["trace_id"], "trace_export")
        self.assertEqual(exported[0]["span_data"]["type"], "tool")
        self.assertEqual(exported[1]["id"], "trace_export")
        self.assertNotIn("spans", exported[1])

    def test_batch_processor_buffers_finished_items_until_flush(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])

        with trace("workflow", trace_id="trace_batch"):
            with span("tool", "search"):
                pass

        self.assertEqual(exporter.items(), [])

        processor.force_flush()
        exported = exporter.items()

        self.assertEqual([item["object"] for item in exported], ["trace.span", "trace"])
        self.assertEqual(processor.pending_count(), 0)

    def test_jsonl_exporter_writes_one_json_object_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "traces" / "items.jsonl"
            exporter = JSONLTracingExporter(export_path)
            processor = BatchTraceProcessor(exporter)
            set_trace_processors([processor])

            with trace("workflow", trace_id="trace_jsonl"):
                with span("tool", "search", data={"query": "ai agent"}):
                    pass

            processor.force_flush()
            lines = export_path.read_text(encoding="utf-8").splitlines()
            exported = [json.loads(line) for line in lines]

        self.assertEqual([item["object"] for item in exported], ["trace.span", "trace"])
        self.assertEqual(exported[0]["trace_id"], "trace_jsonl")
        self.assertEqual(exported[0]["span_data"]["query"], "ai agent")
        self.assertEqual(exported[0]["span_data"]["type"], "tool")
        self.assertNotIn("spans", exported[1])

    def test_specialized_span_helpers_export_expected_fields(self) -> None:
        with trace("workflow", trace_id="trace_helpers") as active_trace:
            with task_span("Plan the lesson"):
                with turn_span(1, "Planner"):
                    pass
            with generation_span(model="DirectModel", model_config={"temperature": 0}):
                pass
            with function_span("direct_tool", input={"q": "ai"}, output="ok"):
                pass
            with agent_span("Planner", task="Plan."):
                with model_span("FakeModel", agent="Planner", step_number=1, message_count=2, tool_count=3):
                    pass
                with tool_span("search", agent="Planner", step_number=2, call_id="call_1", arguments={"q": "ai"}):
                    pass
                with guardrail_span("safe_input", stage="input"):
                    pass
                with handoff_span("Planner", "Researcher", task="Find sources.", call_id="call_2"):
                    pass
                with custom_span("checkpoint", data={"phase": "done"}):
                    pass

        exported_spans = [item.export() for item in active_trace.record.spans]

        self.assertEqual(
            [item["span_data"]["type"] for item in exported_spans],
            [
                "custom", "custom", "generation", "function",
                "agent", "generation", "function", "guardrail", "handoff", "custom",
            ],
        )
        self.assertEqual(exported_spans[0]["span_data"]["name"], "task")
        self.assertEqual(exported_spans[0]["span_data"]["data"]["sdk_span_type"], "task")
        self.assertEqual(exported_spans[0]["span_data"]["data"]["name"], "Plan the lesson")
        self.assertEqual(exported_spans[1]["span_data"]["name"], "turn")
        self.assertEqual(exported_spans[1]["span_data"]["data"]["turn"], 1)
        self.assertEqual(exported_spans[1]["span_data"]["data"]["agent_name"], "Planner")
        self.assertEqual(exported_spans[2]["span_data"]["model"], "DirectModel")
        self.assertEqual(exported_spans[3]["span_data"]["input"], {"q": "ai"})
        self.assertEqual(exported_spans[4]["span_data"]["task"], "Plan.")
        self.assertEqual(exported_spans[5]["span_data"]["model"], "FakeModel")
        self.assertEqual(exported_spans[5]["span_data"]["model_config"]["message_count"], 2)
        self.assertEqual(exported_spans[6]["span_data"]["input"], {"q": "ai"})
        self.assertEqual(exported_spans[6]["span_data"]["arguments"], {"q": "ai"})
        self.assertEqual(exported_spans[7]["span_data"]["stage"], "input")
        self.assertEqual(exported_spans[8]["span_data"]["source_agent"], "Planner")
        self.assertEqual(exported_spans[8]["span_data"]["target_agent"], "Researcher")
        self.assertEqual(exported_spans[9]["span_data"]["phase"], "done")

    def test_model_execution_span_records_retry_and_model_fallback(self) -> None:
        attempts = (
            ModelAttemptRecord(
                request_id="request-1",
                attempt_id="request-1:attempt:1",
                attempt_number=1,
                model_name="primary",
                mode="stream",
                outcome="failed",
                duration_seconds=0.1,
                retry_delay_seconds=0.25,
                reason="retry_scheduled",
            ),
            ModelAttemptRecord(
                request_id="request-1",
                attempt_id="request-1:attempt:2",
                attempt_number=2,
                model_name="primary",
                mode="stream",
                outcome="failed",
                duration_seconds=0.2,
                reason="model_fallback",
            ),
            ModelAttemptRecord(
                request_id="request-1",
                attempt_id="request-1:attempt:3",
                attempt_number=3,
                model_name="backup",
                mode="stream",
                outcome="succeeded",
                duration_seconds=0.3,
                response_id="response-1",
                usage={"input_tokens": 3, "output_tokens": 2},
                ttft_seconds=0.05,
                reason="model_fallback",
            ),
        )
        response = ModelResponse(
            response_id="response-1",
            output=[],
            output_text="secret output",
            tool_calls=[],
            usage={"input_tokens": 3, "output_tokens": 2},
        )

        with trace("workflow"):
            with model_span(
                "primary",
                agent="Planner",
                step_number=1,
                message_count=1,
                tool_count=0,
            ) as active_span:
                _record_model_execution_span(
                    active_span,
                    attempts=attempts,
                    terminal_reason="completed",
                    response=response,
                )

        data = active_span.record.span_data.data
        self.assertEqual(data["request_id"], "request-1")
        self.assertEqual(data["final_attempt_id"], "request-1:attempt:3")
        self.assertEqual(data["attempt_count"], 3)
        self.assertEqual(data["retry_count"], 1)
        self.assertEqual(data["final_model"], "backup")
        self.assertEqual(data["final_mode"], "stream")
        self.assertEqual(data["response_id"], "response-1")
        self.assertEqual(data["usage"], {"input_tokens": 3, "output_tokens": 2})
        self.assertEqual(data["ttft_seconds"], 0.05)
        self.assertEqual(data["attempt_duration_seconds"], [0.1, 0.2, 0.3])
        self.assertEqual(data["retry_delay_seconds"], [0.25])
        self.assertFalse(data["non_stream_fallback_used"])
        self.assertTrue(data["model_fallback_used"])
        self.assertEqual(data["fallback_reason"], "model_fallback")
        self.assertEqual(data["fallback_attempt_id"], "request-1:attempt:3")
        self.assertEqual(data["fallback_from_model"], "primary")
        self.assertEqual(data["fallback_to_model"], "backup")
        self.assertEqual(data["fallback_from_mode"], "stream")
        self.assertEqual(data["fallback_to_mode"], "stream")
        self.assertEqual(data["terminal_reason"], "completed")
        self.assertNotIn("secret output", json.dumps(data))

    def test_model_execution_span_records_non_stream_fallback(self) -> None:
        attempts = (
            ModelAttemptRecord(
                request_id="request-2",
                attempt_id="request-2:attempt:1",
                attempt_number=1,
                model_name="primary",
                mode="stream",
                outcome="failed",
                duration_seconds=0.1,
                reason="non_stream_fallback",
            ),
            ModelAttemptRecord(
                request_id="request-2",
                attempt_id="request-2:attempt:2",
                attempt_number=2,
                model_name="primary",
                mode="non_stream",
                outcome="succeeded",
                duration_seconds=0.2,
                reason="non_stream_fallback",
            ),
        )

        with trace("workflow"):
            with model_span(
                "primary",
                agent="Planner",
                step_number=1,
                message_count=1,
                tool_count=0,
            ) as active_span:
                _record_model_execution_span(
                    active_span,
                    attempts=attempts,
                    terminal_reason="completed",
                )

        data = active_span.record.span_data.data
        self.assertEqual(data["final_mode"], "non_stream")
        self.assertTrue(data["non_stream_fallback_used"])
        self.assertFalse(data["model_fallback_used"])
        self.assertEqual(data["fallback_reason"], "non_stream_fallback")
        self.assertEqual(data["fallback_attempt_id"], "request-2:attempt:2")
        self.assertEqual(data["fallback_from_model"], "primary")
        self.assertEqual(data["fallback_to_model"], "primary")
        self.assertEqual(data["fallback_from_mode"], "stream")
        self.assertEqual(data["fallback_to_mode"], "non_stream")

    def test_model_execution_span_records_failed_fallback(self) -> None:
        attempts = (
            ModelAttemptRecord(
                request_id="request-failed",
                attempt_id="request-failed:attempt:1",
                attempt_number=1,
                model_name="primary",
                mode="stream",
                outcome="failed",
                duration_seconds=0.1,
                reason="model_fallback",
            ),
            ModelAttemptRecord(
                request_id="request-failed",
                attempt_id="request-failed:attempt:2",
                attempt_number=2,
                model_name="backup",
                mode="stream",
                outcome="failed",
                duration_seconds=0.2,
                reason="model_fallback",
            ),
        )

        with trace("workflow"):
            with model_span(
                "primary",
                agent="Planner",
                step_number=1,
                message_count=1,
                tool_count=0,
            ) as active_span:
                _record_model_execution_span(
                    active_span,
                    attempts=attempts,
                    terminal_reason="transport",
                )

        data = active_span.record.span_data.data
        self.assertTrue(data["model_fallback_used"])
        self.assertEqual(data["fallback_reason"], "model_fallback")
        self.assertEqual(
            data["fallback_attempt_id"],
            "request-failed:attempt:2",
        )
        self.assertEqual(data["fallback_from_model"], "primary")
        self.assertEqual(data["fallback_to_model"], "backup")
        self.assertEqual(data["fallback_from_mode"], "stream")
        self.assertEqual(data["fallback_to_mode"], "stream")

    def test_model_execution_span_records_failure_and_cancellation(self) -> None:
        failed_attempt = ModelAttemptRecord(
            request_id="request-3",
            attempt_id="request-3:attempt:1",
            attempt_number=1,
            model_name="primary",
            mode="stream",
            outcome="failed",
            duration_seconds=0.1,
        )

        with trace("workflow"):
            with model_span(
                "primary",
                agent="Planner",
                step_number=1,
                message_count=1,
                tool_count=0,
            ) as failed_span:
                _record_model_execution_span(
                    failed_span,
                    attempts=(failed_attempt,),
                    terminal_reason="unknown",
                )
            with model_span(
                "primary",
                agent="Planner",
                step_number=2,
                message_count=1,
                tool_count=0,
            ) as cancelled_span:
                _record_model_execution_span(
                    cancelled_span,
                    attempts=(),
                    terminal_reason="cancelled",
                )

        failed_data = failed_span.record.span_data.data
        self.assertEqual(failed_data["attempt_count"], 1)
        self.assertEqual(failed_data["terminal_reason"], "unknown")
        self.assertIsNone(failed_data["response_id"])
        self.assertIsNone(failed_data["usage"])
        for name in (
            "fallback_reason",
            "fallback_attempt_id",
            "fallback_from_model",
            "fallback_to_model",
            "fallback_from_mode",
            "fallback_to_mode",
        ):
            self.assertIsNone(failed_data[name])

        cancelled_data = cancelled_span.record.span_data.data
        self.assertEqual(cancelled_data["attempt_count"], 0)
        self.assertIsNone(cancelled_data["request_id"])
        self.assertEqual(cancelled_data["terminal_reason"], "cancelled")
        self.assertIsNone(cancelled_data["fallback_reason"])

    def test_span_without_active_trace_returns_noop_span(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])

        with span("tool", "search") as active_span:
            self.assertIsInstance(active_span, NoOpSpan)
            active_span.record.span_data.data["output"] = "ignored"

        self.assertIsNone(active_span.export())
        self.assertEqual(processor.events(), [])

    def test_disabled_trace_returns_noop_trace(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])

        with trace("disabled workflow", disabled=True) as active_trace:
            self.assertIsInstance(active_trace, NoOpTrace)
            with agent_span("Planner") as active_span:
                self.assertIsInstance(active_span, NoOpSpan)

        self.assertIsNone(active_trace.export())
        self.assertIsNone(active_span.export())
        self.assertEqual(processor.events(), [])

    def test_retry_run_aligns_result_snapshot_and_model_span(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])

        class RetryThenFinalModel:
            def __init__(self) -> None:
                self.calls = 0

            def get_response(self, messages, tool_specs):
                self.calls += 1
                if self.calls == 1:
                    raise ModelResponseError(
                        "network unavailable",
                        kind=ModelErrorKind.TRANSPORT,
                    )
                return ModelResponse(
                    response_id="response-final",
                    output=[],
                    output_text="done",
                    tool_calls=[],
                    usage={"input_tokens": 4, "output_tokens": 1},
                )

        model = RetryThenFinalModel()
        result = Runner.run_sync(
            Agent(
                memory=AgentMemory(),
                model=model,
                model_settings=ModelSettings(
                    max_attempts=2,
                    backoff_initial=0,
                    backoff_max=0,
                    jitter_ratio=0,
                    retry_budget=1,
                    total_timeout=10,
                ),
            ),
            "Retry once.",
        )
        processor.force_flush()
        span_data = next(
            item["span_data"]
            for item in exporter.items()
            if item["object"] == "trace.span"
            and item["span_data"]["type"] == "generation"
            and item["span_data"]["name"] == "RetryThenFinalModel"
        )
        snapshot = result.to_state()

        self.assertEqual(model.calls, 2)
        self.assertEqual(len(result.model_attempts), 2)
        self.assertEqual(len(snapshot["model_attempts"]), 2)
        self.assertEqual(span_data["attempt_count"], 2)
        self.assertEqual(span_data["retry_count"], 1)
        self.assertEqual(
            span_data["request_id"],
            result.model_attempts[-1].request_id,
        )
        self.assertEqual(
            span_data["final_attempt_id"],
            result.model_attempts[-1].attempt_id,
        )
        self.assertEqual(span_data["response_id"], "response-final")
        self.assertEqual(
            span_data["usage"],
            {"input_tokens": 4, "output_tokens": 1},
        )
        self.assertEqual(span_data["terminal_reason"], "completed")
        self.assertEqual(snapshot["last_model_terminal_reason"], "completed")

    def test_run_stream_records_transport_metrics_on_event_stream_span(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])
        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=FinalModel()),
            "Finish.",
            RunConfig(event_queue_size=1),
        )

        async def scenario() -> None:
            async for _event in stream:
                await asyncio.sleep(0.01)
            await stream._wait_result()

        asyncio.run(scenario())
        span_data = next(
            event["item"]["span_data"]
            for event in processor.events()
            if event["event"] == "span_end"
            and event["item"]["span_data"]["name"] == "event_stream"
        )

        self.assertGreater(span_data["max_publish_latency_seconds"], 0.005)
        self.assertEqual(span_data["max_queue_depth"], 1)
        self.assertNotIn("cancel_to_terminal_seconds", span_data)

    def test_cancelled_run_records_cancel_to_terminal_time(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])
        model_entered = Event()
        release_model = Event()

        class BlockingModel:
            def get_response(self, messages, tool_specs):
                model_entered.set()
                release_model.wait()
                return ModelResponse(
                    response_id="late-response",
                    output=[],
                    output_text="late",
                    tool_calls=[],
                )

        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=BlockingModel()),
            "Cancel.",
            RunConfig(cancellation_grace_period=0),
        )

        async def scenario() -> None:
            async def consume() -> None:
                _ = [event async for event in stream]
                with self.assertRaises(RunCancelledError):
                    await stream._wait_result()

            consumer = asyncio.create_task(consume())
            try:
                self.assertTrue(await asyncio.to_thread(model_entered.wait, 1))
                self.assertTrue(stream.controller.cancel("user"))
                await consumer
            finally:
                release_model.set()

        asyncio.run(scenario())
        span_data = next(
            event["item"]["span_data"]
            for event in processor.events()
            if event["event"] == "span_end"
            and event["item"]["span_data"]["name"] == "event_stream"
        )

        self.assertGreaterEqual(span_data["cancel_to_terminal_seconds"], 0)

    def test_disabled_stream_tracing_does_not_notify_processor(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])

        result = asyncio.run(
            Runner.run_async(
                Agent(memory=AgentMemory(), model=FinalModel()),
                "Finish.",
                RunConfig(tracing_disabled=True),
            )
        )

        self.assertEqual(result.final_answer, "done")
        self.assertEqual(processor.events(), [])

    def test_failing_processor_does_not_change_run_result(self) -> None:
        class FailingProcessor(TracingProcessor):
            def on_span_end(self, span) -> None:
                raise RuntimeError("observer failed")

        set_trace_processors([FailingProcessor()])

        result = asyncio.run(
            Runner.run_async(
                Agent(memory=AgentMemory(), model=FinalModel()),
                "Finish.",
            )
        )

        self.assertEqual(result.final_answer, "done")

    def test_one_hundred_streams_keep_metrics_isolated(self) -> None:
        processor = DebugTracingProcessor()
        set_trace_processors([processor])

        async def scenario() -> None:
            await asyncio.gather(
                *(
                    Runner.run_async(
                        Agent(memory=AgentMemory(), model=FinalModel()),
                        f"Finish {index}.",
                    )
                    for index in range(100)
                )
            )

        asyncio.run(scenario())
        stream_spans = [
            event["item"]["span_data"]
            for event in processor.events()
            if event["event"] == "span_end"
            and event["item"]["span_data"]["name"] == "event_stream"
        ]

        self.assertEqual(len(stream_spans), 100)
        self.assertTrue(
            all(
                0 <= span_data["max_queue_depth"] <= 64
                and span_data["max_publish_latency_seconds"] >= 0
                for span_data in stream_spans
            )
        )


if __name__ == "__main__":
    unittest.main()
