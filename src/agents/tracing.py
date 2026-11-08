from __future__ import annotations

import json
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4


# Generate IDs for traces and spans.
def gen_trace_id() -> str:
    return f"trace_{uuid4().hex}"

def gen_span_id() -> str:
    return f"span_{uuid4().hex}"


# Use UTC timestamps for exported tracing records.
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_current_trace: ContextVar[Any] = ContextVar(
    "agent_current_trace",
    default=None,
)
_current_span: ContextVar[Any] = ContextVar(
    "agent_current_span",
    default=None,
)


# Business data attached to a span.
class SpanError(TypedDict):
    message: str
    data: dict[str, Any] | None


@dataclass(frozen=True)
class SpanData:
    span_type: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)

    def export(self) -> dict[str, Any]:
        exported = {
            "type": self.span_type,
            "name": self.name,
        }
        exported.update(self.data)
        return exported


# Runtime record for a single span.
@dataclass
class SpanRecord:
    span_id: str
    trace_id: str
    parent_id: str | None
    span_data: SpanData
    started_at: str = field(default_factory=_utc_now)
    ended_at: str | None = None
    error: SpanError | None = None

    def finish(self) -> None:
        if self.ended_at is None:
            self.ended_at = _utc_now()

    def set_error(self, error: SpanError | Exception | str) -> None:
        if isinstance(error, dict):
            self.error = {
                "message": str(error["message"]),
                "data": error.get("data"),
            }
            return
        self.error = {"message": str(error), "data": None}

    def export(self) -> dict[str, Any]:
        exported = {
            "object": "trace.span",
            "id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "span_data": self.span_data.export(),
        }
        if self.error is not None:
            exported["error"] = self.error
        return exported


# Runtime record for a full trace.
@dataclass
class TraceRecord:
    trace_id: str
    name: str
    group_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_utc_now)
    ended_at: str | None = None
    spans: list[SpanRecord] = field(default_factory=list)

    def add_span(self, span: SpanRecord) -> None:
        if span.trace_id != self.trace_id:
            raise ValueError("Span trace_id must match the trace.")
        self.spans.append(span)

    def finish(self) -> None:
        if self.ended_at is None:
            self.ended_at = _utc_now()

    def export(self) -> dict[str, Any]:
        return {
            "object": "trace",
            "id": self.trace_id,
            "workflow_name": self.name,
            "group_id": self.group_id,
            "metadata": dict(self.metadata),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


# Processor interface for trace/span lifecycle events.
class TracingProcessor:
    def on_trace_start(self, trace: Trace) -> None:
        pass
    def on_trace_end(self, trace: Trace) -> None:
        pass
    def on_span_start(self, span: Span) -> None:
        pass
    def on_span_end(self, span: Span) -> None:
        pass
    def force_flush(self) -> None:
        pass
    def shutdown(self) -> None:
        pass


# Exporter interface for completed trace/span snapshots.
class TracingExporter:
    def export(self, items: list[Trace | Span]) -> None:
        pass


# Store completed trace/span snapshots in memory.
class InMemoryTracingExporter(TracingExporter):
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def export(self, items: list[Trace | Span]) -> None:
        self._items.extend(deepcopy(item.export()) for item in items)

    def items(self) -> list[dict[str, Any]]:
        return deepcopy(self._items)

    def clear(self) -> None:
        self._items.clear()


class JSONLTracingExporter(TracingExporter):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def export(self, items: list[Trace | Span]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            for item in items:
                file.write(json.dumps(item.export(), ensure_ascii=False))
                file.write("\n")



# Export each trace/span when it ends.
class ExportingTracingProcessor(TracingProcessor):
    def __init__(self, exporter: TracingExporter) -> None:
        self.exporter = exporter

    def on_trace_start(self, trace: Trace) -> None:
        pass

    def on_trace_end(self, trace: Trace) -> None:
        self.exporter.export([trace])

    def on_span_start(self, span: Span) -> None:
        pass

    def on_span_end(self, span: Span) -> None:
        self.exporter.export([span])

    def force_flush(self) -> None:
        pass

    def shutdown(self) -> None:
        self.force_flush()


# Buffer completed items and export them on flush.
class BatchTraceProcessor(TracingProcessor):
    def __init__(self, exporter: TracingExporter, *, max_batch_size: int = 128) -> None:
        self.exporter = exporter
        self.max_batch_size = max_batch_size
        self._queue: list[Trace | Span] = []

    def _enqueue(self, item: Trace | Span) -> None:
        self._queue.append(item)
        if len(self._queue) >= self.max_batch_size:
            self.force_flush()

    def on_trace_start(self, trace: Trace) -> None:
        pass

    def on_trace_end(self, trace: Trace) -> None:
        self._enqueue(trace)

    def on_span_start(self, span: Span) -> None:
        pass

    def on_span_end(self, span: Span) -> None:
        self._enqueue(span)

    def force_flush(self) -> None:
        if not self._queue:
            return
        batch = self._queue
        self._queue = []
        self.exporter.export(batch)

    def shutdown(self) -> None:
        self.force_flush()

    def pending_count(self) -> int:
        return len(self._queue)


# Store lifecycle events in memory for debugging.
class DebugTracingProcessor(TracingProcessor):
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def _record(self, event: str, item: Trace | Span) -> None:
        self._events.append({"event": event, "item": deepcopy(item.export())})

    def on_trace_start(self, trace: Trace) -> None:
        self._record("trace_start", trace)

    def on_trace_end(self, trace: Trace) -> None:
        self._record("trace_end", trace)

    def on_span_start(self, span: Span) -> None:
        self._record("span_start", span)

    def on_span_end(self, span: Span) -> None:
        self._record("span_end", span)

    def events(self) -> list[dict[str, Any]]:
        return deepcopy(self._events)

    def clear(self) -> None:
        self._events.clear( )


InMemoryTracingProcessor = DebugTracingProcessor


class SynchronousMultiTracingProcessor(TracingProcessor):
    def __init__(self, processors: list[TracingProcessor] | None = None) -> None:
        self._processors: tuple[TracingProcessor, ...] = tuple(processors or [])

    def add_tracing_processor(self, processor: TracingProcessor) -> None:
        self._processors = (*self._processors, processor)

    def set_processors(self, processors: list[TracingProcessor]) -> None:
        self._processors = tuple(processors)

    def processors(self) -> list[TracingProcessor]:
        return list(self._processors)

    def _forward(self, method_name: str, item: Trace | Span | None = None) -> None:
        for processor in self._processors:
            try:
                method = getattr(processor, method_name)
                method(item) if item is not None else method()
            except Exception:
                continue

    def on_trace_start(self, trace: Trace) -> None:
        self._forward("on_trace_start", trace)

    def on_trace_end(self, trace: Trace) -> None:
        self._forward("on_trace_end", trace)

    def on_span_start(self, span: Span) -> None:
        self._forward("on_span_start", span)

    def on_span_end(self, span: Span) -> None:
        self._forward("on_span_end", span)

    def force_flush(self) -> None:
        self._forward("force_flush")

    def shutdown(self) -> None:
        self._forward("shutdown")

# Global processor dispatcher.
_multi_processor = SynchronousMultiTracingProcessor()


def add_trace_processor(processor: TracingProcessor) -> None:
    _multi_processor.add_tracing_processor(processor)


def set_trace_processors(processors: list[TracingProcessor]) -> None:
    _multi_processor.set_processors(processors)


def get_trace_processors() -> list[TracingProcessor]:
    return _multi_processor.processors()


# Dispatch trace/span lifecycle changes to processors.
def _notify_processors(method_name: str, item: Trace | Span) -> None:
    getattr(_multi_processor, method_name)(item)


class Trace:
    def __init__(self, name: str, *, trace_id: str | None = None,
                 group_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.record = TraceRecord(
            trace_id=trace_id or gen_trace_id(), name=name,
            group_id=group_id, metadata=dict(metadata or {}),
        )
        self._trace_token: Token[Any] | None = None
        self._span_token: Token[Any] | None = None
        self._started = False

    def start(self, *, mark_as_current: bool = True) -> None:
        if self._started:
            return
        self._started = True
        if mark_as_current:
            self._trace_token = _current_trace.set(self)
            self._span_token = _current_span.set(None)
        _notify_processors("on_trace_start", self)

    def finish(self, *, reset_current: bool = True) -> None:
        was_finished = self.record.ended_at is not None
        self.record.finish()
        if not was_finished:
            _notify_processors("on_trace_end", self)
        if not reset_current:
            return
        if self._span_token is not None: _current_span.reset(self._span_token)
        if self._trace_token is not None: _current_trace.reset(self._trace_token)
        self._span_token = None
        self._trace_token = None

    def add_span(self, span_record: SpanRecord) -> None:
        self.record.add_span(span_record)

    def export(self) -> dict[str, Any]:
        return self.record.export()

    def __enter__(self) -> Trace:
        self.start(mark_as_current=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.finish(reset_current=True)


class NoOpTrace:
    def __init__(self, name: str, *, set_current: bool = True) -> None:
        self.record = TraceRecord(trace_id="noop_trace", name=name)
        self._set_current = set_current
        self._trace_token: Token[Any] | None = None
        self._span_token: Token[Any] | None = None

    def start(self, *, mark_as_current: bool = True) -> None:
        if mark_as_current and self._set_current:
            self._trace_token = _current_trace.set(self)
            self._span_token = _current_span.set(None)

    def finish(self, *, reset_current: bool = True) -> None:
        if not reset_current:
            return
        if self._span_token is not None:
            _current_span.reset(self._span_token)
        if self._trace_token is not None:
            _current_trace.reset(self._trace_token)
        self._span_token = None
        self._trace_token = None

    def add_span(self, span_record: SpanRecord) -> None:
        return None

    def export(self) -> dict[str, Any] | None:
        return None

    def __enter__(self) -> NoOpTrace:
        self.start(mark_as_current=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.finish(reset_current=True)


class Span:
    def __init__(self, span_type: str, name: str, *, data: dict[str, Any] | None = None,
                 trace_record: Trace | NoOpTrace | None = None,
                 parent: Span | NoOpSpan | None = None,
                 span_id: str | None = None) -> None:
        resolved_trace = trace_record or get_current_trace()
        if resolved_trace is None:
            raise RuntimeError("Cannot create a span without an active trace.")
        resolved_parent = parent or get_current_span()
        self.trace = resolved_trace
        self.record = SpanRecord(
            span_id=span_id or gen_span_id(), trace_id=resolved_trace.record.trace_id,
            parent_id=resolved_parent.record.span_id if resolved_parent else None,
            span_data=SpanData(span_type=span_type, name=name, data=dict(data or {})),
        )
        self._span_token: Token[Any] | None = None

    def finish(self) -> None:
        self.record.finish()

    def set_error(self, error: SpanError | Exception | str) -> None:
        self.record.set_error(error)

    def export(self) -> dict[str, Any]:         return self.record.export()

    def __enter__(self) -> Span:
        self.trace.add_span(self.record)
        self._span_token = _current_span.set(self)
        _notify_processors("on_span_start", self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_val is not None and self.record.error is None:
            self.set_error(exc_val)
        self.finish()
        _notify_processors("on_span_end", self)
        if self._span_token is not None:
            _current_span.reset(self._span_token)
        self._span_token = None


class NoOpSpan:
    def __init__(self, span_type: str, name: str, *, data: dict[str, Any] | None = None) -> None:
        self.record = SpanRecord(
            span_id="noop_span",
            trace_id="noop_trace",
            parent_id=None,
            span_data=SpanData(span_type=span_type, name=name, data=dict(data or {})),
        )
        self._span_token: Token[Any] | None = None

    def finish(self) -> None:
        self.record.finish()

    def set_error(self, error: SpanError | Exception | str) -> None:
        self.record.set_error(error)

    def export(self) -> dict[str, Any] | None:
        return None

    def __enter__(self) -> NoOpSpan:
        self._span_token = _current_span.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_val is not None:
            self.set_error(exc_val)
        self.finish()
        if self._span_token is not None:
            _current_span.reset(self._span_token)
        self._span_token = None


def record_span_error(span_context: Span | NoOpSpan, error: Exception) -> None:
    data = span_context.record.span_data.data
    data["error_type"] = error.__class__.__name__
    error_data = {"error_type": error.__class__.__name__}
    if error.__cause__ is not None:
        data["error_cause_type"] = error.__cause__.__class__.__name__
        error_data["error_cause_type"] = error.__cause__.__class__.__name__
    span_context.set_error({"message": str(error), "data": error_data})


def trace(name: str, *, trace_id: str | None = None,
          group_id: str | None = None, metadata: dict[str, Any] | None = None,
          disabled: bool = False, only_if_missing: bool = False) -> Trace | NoOpTrace:
    if disabled:
        return NoOpTrace(name, set_current=True)
    if only_if_missing and get_current_trace() is not None:
        return NoOpTrace(name, set_current=False)
    return Trace(name, trace_id=trace_id, group_id=group_id, metadata=metadata)


def span(span_type: str, name: str, *, data: dict[str, Any] | None = None) -> Span | NoOpSpan:
    active_trace = get_current_trace()
    if active_trace is None or isinstance(active_trace, NoOpTrace):
        return NoOpSpan(span_type, name, data=data)
    return Span(span_type, name, data=data, trace_record=active_trace)


def agent_span(name: str, *, task: str | None = None) -> Span | NoOpSpan:
    data = {}
    if task is not None:
        data["task"] = task
    return span("agent", name, data=data)


def task_span(name: str) -> Span | NoOpSpan:
    return span(
        "custom",
        "task",
        data={"data": {"sdk_span_type": "task", "name": name}},
    )


def turn_span(turn: int, agent_name: str) -> Span | NoOpSpan:
    return span(
        "custom",
        "turn",
        data={
            "data": {
                "sdk_span_type": "turn",
                "turn": turn,
                "agent_name": agent_name,
            },
        },
    )


def generation_span(
    *,
    model: str | None = None,
    input: list[dict[str, Any]] | None = None,
    output: list[dict[str, Any]] | None = None,
    model_config: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Span | NoOpSpan:
    payload = {
        "input": input,
        "output": output,
        "model": model,
        "model_config": model_config,
        "usage": usage,
    }
    payload.update(data or {})
    return span("generation", model or "generation", data=payload)


def function_span(
    name: str,
    *,
    input: Any | None = None,
    output: Any | None = None,
    mcp_data: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Span | NoOpSpan:
    payload = {"input": input, "output": output, "mcp_data": mcp_data}
    payload.update(data or {})
    return span("function", name, data=payload)


def model_span(
    name: str,
    *,
    agent: str,
    step_number: int,
    message_count: int,
    tool_count: int,
    input: list[dict[str, Any]] | None = None,
) -> Span | NoOpSpan:
    model_config = {
        "agent": agent,
        "step_number": step_number,
        "message_count": message_count,
        "tool_count": tool_count,
    }
    return generation_span(model=name, input=input, model_config=model_config, data=model_config)


def tool_span(
    name: str,
    *,
    agent: str,
    step_number: int,
    call_id: str,
    arguments: dict[str, Any] | None,
) -> Span | NoOpSpan:
    metadata = {
        "agent": agent,
        "step_number": step_number,
        "call_id": call_id,
    }
    tool_input = dict(arguments) if arguments is not None else None
    if tool_input is not None:
        metadata["arguments"] = tool_input
    return function_span(name, input=tool_input, data=metadata)


def guardrail_span(name: str, *, stage: str) -> Span | NoOpSpan:
    return span("guardrail", name, data={"stage": stage})


def handoff_span(
    source_agent: str,
    target_agent: str,
    *,
    task: str,
    call_id: str,
) -> Span | NoOpSpan:
    return span(
        "handoff",
        f"{source_agent} -> {target_agent}",
        data={
            "source_agent": source_agent,
            "target_agent": target_agent,
            "task": task,
            "call_id": call_id,
        },
    )


def custom_span(name: str, *, data: dict[str, Any] | None = None) -> Span | NoOpSpan:
    return span("custom", name, data=data)


def get_current_trace() -> Trace | NoOpTrace | None:
    return _current_trace.get()


def get_current_span() -> Span | NoOpSpan | None:
    return _current_span.get()
