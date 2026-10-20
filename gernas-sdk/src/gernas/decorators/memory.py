"""@trace_memory — agent memory reads and writes (Blueprint §3 Memory: read/write
operations, memory hit, stale memory, memory size)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import Call, Spec, resolve, wrap
from gernas.tracer import safe_set
from gernas.utils.hashing import payload_size

F = TypeVar("F", bound=Callable[..., Any])

_OPERATIONS = ("read", "write")


class MemorySpec(Spec):
    op = SpanName.MEMORY

    def __init__(
        self, operation: str, hit_from: Callable[[Any], bool] | None, stale_from: Callable[[Any], bool] | None
    ) -> None:
        if operation not in _OPERATIONS:
            raise ValueError(f"trace_memory operation must be one of {_OPERATIONS}")
        self.operation, self.hit_from, self.stale_from = operation, hit_from, stale_from

    def start(self, span: Any, call: Call) -> None:
        safe_set(span, Ctx.MEMORY_OPERATION, self.operation)
        if self.operation == "write":
            safe_set(span, Ctx.MEMORY_SIZE_BYTES, payload_size(call.payload()))

    def success(self, span: Any, call: Call, result: Any) -> None:
        if self.operation != "read":
            return
        if self.hit_from is not None:
            hit = resolve(self.hit_from, call, result)
        else:
            hit = result is not None and not (hasattr(result, "__len__") and len(result) == 0)
        safe_set(span, Ctx.MEMORY_HIT, bool(hit))
        safe_set(span, Ctx.MEMORY_SIZE_BYTES, payload_size(result))
        if self.stale_from is not None:
            stale = resolve(self.stale_from, call, result)
            if stale is not None:
                safe_set(span, Ctx.MEMORY_STALE, bool(stale))


def trace_memory(
    operation: str, *, hit_from: Callable[[Any], bool] | None = None, stale_from: Callable[[Any], bool] | None = None
) -> Callable[[F], F]:
    """Trace a memory ``read`` or ``write`` as ``memory.access``.

    ``hit_from(result)`` overrides the default hit test (non-empty result).
    """
    MemorySpec(operation, hit_from, stale_from)  # validate eagerly

    def decorator(fn: F) -> F:
        return wrap(fn, MemorySpec(operation, hit_from, stale_from))

    return decorator
