"""W3C baggage helpers.

The span processor that copies baggage onto spans lives in
:mod:`gernas.tracer` (``BaggageSpanProcessor``) and only copies allow-listed keys.
"""
from __future__ import annotations

from opentelemetry import baggage, context
from opentelemetry.propagate import extract, inject

from gernas.tracer import BaggageSpanProcessor  # re-export for compatibility

__all__ = ["BaggageSpanProcessor", "inject_baggage", "extract_baggage", "set_baggage_on_current_context"]


def inject_baggage(carrier: dict, **entries: str) -> dict:
    """Set baggage entries on the current context and inject W3C headers into *carrier*."""
    ctx = context.get_current()
    for key, value in entries.items():
        ctx = baggage.set_baggage(key, value, ctx)
    inject(carrier, context=ctx)
    return carrier


def set_baggage_on_current_context(**entries: str) -> object:
    """Attach baggage entries to the active context; returns the token to ``context.detach``."""
    ctx = context.get_current()
    for key, value in entries.items():
        ctx = baggage.set_baggage(key, value, ctx)
    return context.attach(ctx)


def extract_baggage(carrier: dict) -> dict[str, str]:
    """Extract baggage entries from *carrier* as a plain dict."""
    return dict(baggage.get_all(extract(carrier)))
