"""@track_latency and @count_calls — metric-emitting decorators with dimensional attributes."""
from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, TypeVar

from opentelemetry import metrics

F = TypeVar("F", bound=Callable[..., Any])


def _extract_attrs(attribute_keys: list[str] | None, args: tuple, kwargs: dict) -> dict:
    """Extract dimensional attributes from named kwargs and/or first positional arg if it is a dict."""
    if not attribute_keys:
        return {}
    out: dict = {}
    for key in attribute_keys:
        if key in kwargs:
            out[key] = str(kwargs[key])
        elif args and isinstance(args[0], dict) and key in args[0]:
            out[key] = str(args[0][key])
    return out


def track_latency(
    metric_name: str,
    unit: str = "ms",
    attribute_keys: list[str] | None = None,
) -> Callable[[F], F]:
    """Record a histogram of wall-clock duration in milliseconds.

    attribute_keys: named kwargs (or keys in state dict first arg) to attach as
    metric dimensions, enabling per-category latency slices in dashboards.
    """
    def decorator(fn: F) -> F:
        meter = metrics.get_meter(__name__)
        histogram = meter.create_histogram(
            metric_name, unit=unit, description=f"Latency of {fn.__qualname__}"
        )

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    attrs = _extract_attrs(attribute_keys, args, kwargs)
                    histogram.record(round((time.perf_counter() - start) * 1000, 2), attrs)
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    attrs = _extract_attrs(attribute_keys, args, kwargs)
                    histogram.record(round((time.perf_counter() - start) * 1000, 2), attrs)
            return sync_wrapper  # type: ignore[return-value]
    return decorator


def count_calls(
    metric_name: str,
    attributes: Any = None,
    attribute_keys: list[str] | None = None,
) -> Callable[[F], F]:
    """Increment a counter on every invocation.

    attributes: static dict or callable ``(args, kwargs, result) -> dict`` for dynamic values.
    attribute_keys: named kwargs (or state dict keys) to attach as extra dimensions.
    Both are merged; attribute_keys wins on collision.
    """
    def decorator(fn: F) -> F:
        meter = metrics.get_meter(__name__)
        counter = meter.create_counter(
            metric_name, description=f"Call count for {fn.__qualname__}"
        )

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _error = False
                result = None
                try:
                    result = await fn(*args, **kwargs)
                    return result
                except Exception:
                    _error = True
                    raise
                finally:
                    _static = attributes(args, kwargs, result) if callable(attributes) else (attributes or {})
                    _dynamic = _extract_attrs(attribute_keys, args, kwargs)
                    counter.add(1, {**_static, **_dynamic, "error": str(_error)})
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                _error = False
                result = None
                try:
                    result = fn(*args, **kwargs)
                    return result
                except Exception:
                    _error = True
                    raise
                finally:
                    _static = attributes(args, kwargs, result) if callable(attributes) else (attributes or {})
                    _dynamic = _extract_attrs(attribute_keys, args, kwargs)
                    counter.add(1, {**_static, **_dynamic, "error": str(_error)})
            return sync_wrapper  # type: ignore[return-value]
    return decorator
