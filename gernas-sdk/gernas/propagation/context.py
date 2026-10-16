"""Helpers for reading, serialising, and restoring the active trace context.

Used to maintain trace continuity across async boundaries, checkpoints (HITL),
and Databricks job boundaries where the context must be reconstructed.
"""
from __future__ import annotations

from opentelemetry import trace, context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags


def get_current_trace_id() -> str:
    """Return the hex trace ID of the currently active span, or 32 zeros if none."""
    span = trace.get_current_span()
    tid = span.get_span_context().trace_id
    return format(tid, "032x") if tid else "0" * 32


def get_current_span_id() -> str:
    """Return the hex span ID of the currently active span, or 16 zeros if none."""
    span = trace.get_current_span()
    sid = span.get_span_context().span_id
    return format(sid, "016x") if sid else "0" * 16


def serialize_context() -> dict[str, str]:
    """Serialise the current traceparent for checkpoint storage (e.g., LangGraph HITL).

    The returned dict can be stored in a checkpoint and passed to restore_context()
    on the other side of an interrupt/resume boundary.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "trace_flags": str(int(ctx.trace_flags)),
    }


def restore_context(serialized: dict[str, str]) -> object:
    """Rebuild an OTEL context token from a serialised traceparent dict.

    Returns an object that can be used with context.attach() / context.detach()
    to make the restored context active for async job execution.
    """
    if not serialized.get("trace_id"):
        return context.get_current()

    span_ctx = SpanContext(
        trace_id=int(serialized["trace_id"], 16),
        span_id=int(serialized["span_id"], 16),
        is_remote=True,
        trace_flags=TraceFlags(int(serialized.get("trace_flags", "1"))),
    )
    restored_span = NonRecordingSpan(span_ctx)
    return trace.set_span_in_context(restored_span)
