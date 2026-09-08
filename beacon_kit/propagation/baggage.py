"""BaggageSpanProcessor — copies all W3C Baggage entries to span attributes automatically.

Mandatory tags propagated via Baggage (from docs):
  query.category, business_unit, environment, pod, component
"""
from __future__ import annotations

from typing import Any

from opentelemetry import baggage, context
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import inject, extract
from opentelemetry.sdk.trace import ReadableSpan, Span
from opentelemetry.sdk.trace import SpanProcessor


class BaggageSpanProcessor(SpanProcessor):
    """On span start, copies all Baggage key/value pairs into span attributes."""

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        ctx = parent_context if parent_context is not None else context.get_current()
        entries = baggage.get_all(ctx)
        for key, value in entries.items():
            if span.is_recording():
                span.set_attribute(key, value)

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def inject_baggage(carrier: dict, **entries: str) -> dict:
    """Set Baggage entries in the current context and inject W3C headers into *carrier*."""
    ctx = context.get_current()
    for key, value in entries.items():
        ctx = baggage.set_baggage(key, value, ctx)
    inject(carrier, context=ctx)
    return carrier


def set_baggage_on_current_context(**entries: str) -> object:
    """Attach Baggage entries to the active context so all child spans inherit them.

    Returns an attach token — the caller MUST call ``opentelemetry.context.detach(token)``
    when the scope ends (e.g. in a finally block) to avoid context leaks.

    Example::

        token = set_baggage_on_current_context(query_category="pricing", pod="pod1")
        try:
            ...
        finally:
            from opentelemetry import context as otel_context
            otel_context.detach(token)
    """
    ctx = context.get_current()
    for key, value in entries.items():
        ctx = baggage.set_baggage(key, value, ctx)
    return context.attach(ctx)


def extract_baggage(carrier: dict) -> dict[str, str]:
    """Extract Baggage entries from *carrier* and return them as a plain dict."""
    ctx = extract(carrier)
    return dict(baggage.get_all(ctx))
