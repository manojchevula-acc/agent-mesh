"""@trace_memory — agent memory reads and writes (WP1-CORE-02c step 4)."""
from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import Call, Spec, resolve, wrap
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])

_OPERATIONS = ("read", "write", "delete", "summarize")


def _size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def _count(value: Any) -> int | None:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return None


class MemorySpec(Spec):
    op = SpanName.MEMORY

    def __init__(self, operation: str, store: str | None, stale_from: Callable[[Any], bool] | None,
                 hit_from: Callable[[Any], bool] | None = None) -> None:
        if operation not in _OPERATIONS:
            raise ValueError(f"trace_memory operation must be one of {_OPERATIONS}")
        self.operation, self.store, self.stale_from, self.hit_from = operation, store, stale_from, hit_from

    def span_name(self, call: Call) -> str:
        return SpanName.MEMORY.format(operation=self.operation)

    def start(self, span: Any, call: Call) -> None:
        safe_set(span, Ctx.MEMORY_OPERATION, self.operation)
        safe_set(span, Ctx.MEMORY_STORE, self.store)
        if self.operation in ("write", "summarize"):
            payload = [a for a in call.args if not hasattr(a, "__dict__")] + list(call.kwargs.values())
            safe_set(span, Ctx.MEMORY_SIZE_BYTES, _size(payload))
            call.state["sized"] = True

    def success(self, span: Any, call: Call, result: Any) -> None:
        if self.operation == "read":
            if self.hit_from is not None:
                hit = resolve(self.hit_from, call, result)
            else:
                hit = result is not None and not (hasattr(result, "__len__") and len(result) == 0)
            safe_set(span, Ctx.MEMORY_HIT, hit)
            safe_set(span, Ctx.MEMORY_SIZE_BYTES, _size(result))
            safe_set(span, Ctx.MEMORY_ITEM_COUNT, _count(result))
            if self.stale_from is not None:
                stale = resolve(self.stale_from, call, result)
                if stale is not None:
                    safe_set(span, Ctx.MEMORY_STALE, bool(stale))
        elif not call.state.get("sized"):
            safe_set(span, Ctx.MEMORY_SIZE_BYTES, _size(result))

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        if exc is not None and self.operation == "read":
            safe_set(span, Ctx.MEMORY_SIZE_BYTES, 0)
        safe_set(span, Ctx.DURATION_MS, call.elapsed_ms())


def trace_memory(operation: str = "read", *, store: str | None = None,
                 stale_from: Callable[[Any], bool] | None = None,
                 hit_from: Callable[[Any], bool] | None = None) -> Callable[[F], F]:
    """Trace a memory operation as ``memory.{operation}``; reads record hit/miss.

    ``hit_from(result)`` overrides the default hit test (non-empty result).
    """
    MemorySpec(operation, store, stale_from, hit_from)  # validate eagerly

    def decorator(fn: F) -> F:
        return wrap(fn, MemorySpec(operation, store, stale_from, hit_from))
    return decorator
