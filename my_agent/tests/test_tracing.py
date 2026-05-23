from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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


if __name__ == "__main__":
    unittest.main()
