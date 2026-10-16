"""Context propagation across async tasks, threads, queues and processes.

The three places standard instrumentation silently loses track (WP1-CORE-05):

* ``asyncio.create_task`` — use :func:`gernas_task` to run the coroutine inside
  the caller's OTel context;
* threads / executors — :func:`run_in_context` / :func:`wrap_in_context`;
* queues and remote hops — :func:`inject_context` into message headers on the
  producer, :func:`extract_context` + :func:`attach` on the consumer.

Headers carry W3C ``traceparent``/``tracestate`` plus W3C baggage with the GERNAS
identifiers ``gernas.session_id`` and ``gernas.parent_run_id`` (the producing
run's ``agent_run_id``), so a child run can link to its parent (WP1-CTX-03).
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Coroutine, Iterator, MutableMapping, TypeVar

from opentelemetry import baggage, context, propagate
from opentelemetry.context import Context

from gernas.attributes import Ctx

T = TypeVar("T")


def _current_run_id() -> str | None:
    from gernas.run_state import current_run
    run = current_run()
    return run.agent_run_id if run is not None else None


def inject_context(headers: MutableMapping[str, str] | None = None, *,
                   session_id: str | None = None, ctx: Context | None = None) -> MutableMapping[str, str]:
    """Write W3C trace context + GERNAS baggage into *headers* (created if None)."""
    carrier: MutableMapping[str, str] = {} if headers is None else headers
    try:
        ctx = ctx if ctx is not None else context.get_current()
        run_id = _current_run_id()
        if run_id:
            ctx = baggage.set_baggage(Ctx.PARENT_RUN_ID, run_id, ctx)
        if session_id:
            ctx = baggage.set_baggage(Ctx.SESSION_ID, session_id, ctx)
        propagate.inject(carrier, context=ctx)
    except Exception:  # noqa: BLE001 - propagation failure must not break the hop
        pass
    return carrier


def extract_context(headers: MutableMapping[str, str] | dict | None) -> Context:
    """Return an OTel Context rebuilt from *headers* (empty context if none)."""
    try:
        normalised = {str(k).lower(): v for k, v in (headers or {}).items()}
        return propagate.extract(normalised)
    except Exception:  # noqa: BLE001
        return context.get_current()


@contextmanager
def attached(ctx: Context) -> Iterator[None]:
    """Make *ctx* current for the block (consumer side of a queue hop)."""
    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)


@contextmanager
def attach_headers(headers: MutableMapping[str, str] | dict | None) -> Iterator[Context]:
    """Extract and attach in one step: ``with attach_headers(msg.headers): ...``."""
    ctx = extract_context(headers)
    token = context.attach(ctx)
    try:
        yield ctx
    finally:
        context.detach(token)


def gernas_task(coro: Coroutine[Any, Any, T], *, name: str | None = None) -> "asyncio.Task[T]":
    """``asyncio.create_task`` that pins the caller's OTel context into the task.

    ``create_task`` copies contextvars at creation time; this makes the copy
    explicit and also survives libraries that schedule tasks from a different
    context (e.g. callbacks), so background work joins the caller's trace.
    """
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return loop.create_task(coro, name=name, context=ctx)


def wrap_in_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Bind *fn* to the current OTel context so it can run on another thread."""
    otel_ctx = context.get_current()

    @functools.wraps(fn)
    def bound(*args: Any, **kwargs: Any) -> T:
        token = context.attach(otel_ctx)
        try:
            return fn(*args, **kwargs)
        finally:
            context.detach(token)

    return bound


def run_in_context(fn: Callable[..., T], *args: Any, **kwargs: Any) -> Callable[[], T]:
    """Return a zero-arg callable for ``Thread(target=...)``/executors."""
    bound = wrap_in_context(fn)
    return lambda: bound(*args, **kwargs)


async def to_thread(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``asyncio.to_thread`` that keeps the trace connected."""
    return await asyncio.to_thread(wrap_in_context(fn), *args, **kwargs)


def baggage_value(key: str, ctx: Context | None = None) -> str | None:
    try:
        value = baggage.get_baggage(key, ctx)
        return str(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


def set_baggage(**entries: str) -> object:
    """Attach baggage entries (keys given as Ctx values via ``**{Ctx.X: v}``).

    Returns the attach token; call :func:`opentelemetry.context.detach` with it.
    """
    ctx = context.get_current()
    for key, value in entries.items():
        if value is not None:
            ctx = baggage.set_baggage(key, str(value), ctx)
    return context.attach(ctx)


AwaitableT = Awaitable[T]
