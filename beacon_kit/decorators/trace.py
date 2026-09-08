"""@traced, @traced_node, @traced_pipeline decorators — wrap functions in OTEL spans."""
from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, TypeVar, overload

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

F = TypeVar("F", bound=Callable[..., Any])


def _make_traced_wrapper(fn: F, span_name: str, attributes: dict, kind: SpanKind) -> F:
    tracer = trace.get_tracer(__name__)

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(span_name, kind=kind) as span:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
        return async_wrapper  # type: ignore[return-value]
    else:
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(span_name, kind=kind) as span:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
        return sync_wrapper  # type: ignore[return-value]


@overload
def traced(fn: F) -> F: ...
@overload
def traced(
    *, name: str | None = None, attributes: dict | None = None, kind: SpanKind = SpanKind.INTERNAL
) -> Callable[[F], F]: ...


def traced(  # type: ignore[misc]
    fn: F | None = None,
    *,
    name: str | None = None,
    attributes: dict | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> F | Callable[[F], F]:
    """Wrap a function in a child OTEL span.

    Usage:
        @traced
        def my_func(): ...

        @traced(name="custom.span", attributes={"key": "value"})
        async def my_async_func(): ...
    """
    def decorator(f: F) -> F:
        span_name = name or f.__qualname__
        return _make_traced_wrapper(f, span_name, attributes or {}, kind)

    if fn is not None:
        return decorator(fn)
    return decorator


def traced_node(
    name: str,
    attributes: dict | None = None,
    state_attributes: list[str] | None = None,
) -> Callable[[F], F]:
    """Decorator for LangGraph node functions — INTERNAL span, node name as span name.

    state_attributes: keys to extract from the first positional arg when it is a dict
    (the LangGraph state). Each key is recorded as ``node.<key>`` on the span.
    """
    tracer = trace.get_tracer(__name__)

    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
                    for k, v in (attributes or {}).items():
                        span.set_attribute(k, v)
                    if state_attributes and args and isinstance(args[0], dict):
                        for key in state_attributes:
                            if key in args[0]:
                                span.set_attribute(f"node.{key}", str(args[0][key]))
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
                    for k, v in (attributes or {}).items():
                        span.set_attribute(k, v)
                    if state_attributes and args and isinstance(args[0], dict):
                        for key in state_attributes:
                            if key in args[0]:
                                span.set_attribute(f"node.{key}", str(args[0][key]))
                    try:
                        return fn(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        raise
            return sync_wrapper  # type: ignore[return-value]
    return decorator


def traced_pipeline(
    name: str,
    attribute_keys: list[str] | None = None,
) -> Callable[[F], F]:
    """Wrap a pipeline orchestrator in an INTERNAL root span.

    attribute_keys: named kwargs to capture as ``pipeline.<key>`` span attributes.
    Gives the trace a dedicated pipeline root span so all node spans nest under it,
    enabling per-pipeline latency queries from trace data alone.
    """
    tracer = trace.get_tracer(__name__)

    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
                    span.set_attribute("pipeline.name", name)
                    if attribute_keys:
                        for key in attribute_keys:
                            if key in kwargs:
                                span.set_attribute(f"pipeline.{key}", str(kwargs[key]))
                    try:
                        return await fn(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
                    span.set_attribute("pipeline.name", name)
                    if attribute_keys:
                        for key in attribute_keys:
                            if key in kwargs:
                                span.set_attribute(f"pipeline.{key}", str(kwargs[key]))
                    try:
                        return fn(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                        raise
            return sync_wrapper  # type: ignore[return-value]
    return decorator
