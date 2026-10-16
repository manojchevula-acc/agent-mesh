"""@trace_chain — LangChain / runnable chains (WP1-CORE-02d step 1).

``mode="construct"`` (default) wraps a function that *builds* a chain: the
returned runnable is bound to :class:`~gernas.adapters.langchain.GernasLangChainCallback`
so every invocation emits ``chain.run`` with nested ``llm.call`` / ``tool.*`` spans.
``mode="invoke"`` wraps a function that *runs* a chain in a ``chain.run`` span.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, TypeVar

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import Call, Spec, wrap
from gernas.tracer import safe_set
from gernas.utils.hashing import hash_inputs

F = TypeVar("F", bound=Callable[..., Any])


class ChainSpec(Spec):
    op = SpanName.CHAIN

    def __init__(self, name: str | None) -> None:
        self.name = name

    def start(self, span: Any, call: Call) -> None:
        safe_set(span, Ctx.CHAIN_NAME, self.name or getattr(call.fn, "__name__", "chain"))
        safe_set(span, Ctx.INPUT_HASH, call.input_hash())

    def success(self, span: Any, call: Call, result: Any) -> None:
        safe_set(span, Ctx.OUTPUT_HASH, hash_inputs(result))

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.DURATION_MS, call.elapsed_ms())


def instrument_runnable(runnable: Any, provider: str | None = None) -> Any:
    """Return *runnable* bound to a GERNAS callback handler (no-op if unsupported)."""
    if not hasattr(runnable, "with_config"):
        return runnable
    try:
        from gernas.adapters.langchain import GernasLangChainCallback
        return runnable.with_config(callbacks=[GernasLangChainCallback(provider=provider)])
    except Exception:  # noqa: BLE001 - missing extra or incompatible runnable: run untraced
        return runnable


def trace_chain(name: str | None = None, *, mode: str = "construct", provider: str | None = None) -> Callable[[F], F]:
    if mode not in ("construct", "invoke"):
        raise ValueError("trace_chain mode must be 'construct' or 'invoke'")

    def decorator(fn: F) -> F:
        if mode == "invoke":
            return wrap(fn, ChainSpec(name))
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return instrument_runnable(await fn(*args, **kwargs), provider)
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return instrument_runnable(fn(*args, **kwargs), provider)
        return sync_wrapper  # type: ignore[return-value]
    return decorator
