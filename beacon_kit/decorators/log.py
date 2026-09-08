"""@log_calls decorator — structured OTEL-linked log records on function entry and exit."""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def log_calls(logger_name: str | None = None) -> Callable[[F], F]:
    """Emit structured log records on function entry and exit.

    Records flow to OTEL via LoggingHandler (attached by setup_telemetry) so every
    log line carries the active trace_id and span_id automatically.

    logger_name: override the logger name (defaults to fn.__module__).
    """
    def decorator(fn: F) -> F:
        _logger = logging.getLogger(logger_name or fn.__module__)

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _logger.info(
                    "Entering %s",
                    fn.__qualname__,
                    extra={"function.name": fn.__qualname__, "function.module": fn.__module__},
                )
                _start = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    _logger.info(
                        "Completed %s in %.1f ms",
                        fn.__qualname__,
                        (time.perf_counter() - _start) * 1000,
                        extra={
                            "function.name": fn.__qualname__,
                            "execution.duration_ms": round((time.perf_counter() - _start) * 1000, 2),
                            "execution.success": True,
                        },
                    )
                    return result
                except Exception as exc:
                    _logger.error(
                        "Error in %s: %s",
                        fn.__qualname__,
                        type(exc).__name__,
                        extra={
                            "function.name": fn.__qualname__,
                            "execution.success": False,
                            "error.type": type(exc).__name__,
                        },
                    )
                    raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                _logger.info(
                    "Entering %s",
                    fn.__qualname__,
                    extra={"function.name": fn.__qualname__, "function.module": fn.__module__},
                )
                _start = time.perf_counter()
                try:
                    result = fn(*args, **kwargs)
                    _logger.info(
                        "Completed %s in %.1f ms",
                        fn.__qualname__,
                        (time.perf_counter() - _start) * 1000,
                        extra={
                            "function.name": fn.__qualname__,
                            "execution.duration_ms": round((time.perf_counter() - _start) * 1000, 2),
                            "execution.success": True,
                        },
                    )
                    return result
                except Exception as exc:
                    _logger.error(
                        "Error in %s: %s",
                        fn.__qualname__,
                        type(exc).__name__,
                        extra={
                            "function.name": fn.__qualname__,
                            "execution.success": False,
                            "error.type": type(exc).__name__,
                        },
                    )
                    raise
            return sync_wrapper  # type: ignore[return-value]
    return decorator
