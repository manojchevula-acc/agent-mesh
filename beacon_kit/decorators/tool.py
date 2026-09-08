"""@traced_tool decorator — wraps tool functions with input hashing and output summarisation.

Raw inputs/outputs are never stored in spans (privacy-by-design).
Only SHA-256 hashes of inputs and structural summaries of outputs appear in telemetry.
"""
from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, TypeVar

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from beacon_kit.utils.hashing import hash_inputs, summarize_output
from beacon_kit.utils.sanitizer import scrub_pii

F = TypeVar("F", bound=Callable[..., Any])


def traced_tool(name: str) -> Callable[[F], F]:
    """Wrap a tool function in a CLIENT span.

    Records:
    - ``tool.input_hash``: SHA-256 of inputs (not the inputs themselves)
    - ``tool.output.*``: structural summary (field count, size, error presence)
    - ``tool.latency_ms``: wall-clock duration
    """
    def decorator(fn: F) -> F:
        tracer = trace.get_tracer(__name__)
        span_name = f"tool.{name}"

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("tool.name", name)
                    span.set_attribute("tool.input_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    try:
                        result = await fn(*args, **kwargs)
                        summary = summarize_output(result)
                        for k, v in summary.items():
                            if v is not None:
                                span.set_attribute(f"tool.output.{k}", str(v))
                        span.set_attribute("tool.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed_msg = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed_msg))
                        span.set_status(Status(StatusCode.ERROR, scrubbed_msg))
                        span.set_attribute("tool.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("tool.name", name)
                    span.set_attribute("tool.input_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    try:
                        result = fn(*args, **kwargs)
                        summary = summarize_output(result)
                        for k, v in summary.items():
                            if v is not None:
                                span.set_attribute(f"tool.output.{k}", str(v))
                        span.set_attribute("tool.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed_msg = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed_msg))
                        span.set_status(Status(StatusCode.ERROR, scrubbed_msg))
                        span.set_attribute("tool.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return sync_wrapper  # type: ignore[return-value]

    return decorator
