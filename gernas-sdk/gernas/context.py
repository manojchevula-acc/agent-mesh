"""Context propagation and request context (Blueprint §1 "standard trace/span context").

* :func:`request_context` — sets session / task / channel and the initiating
  principal for everything inside the block. The user id is tokenized with
  HMAC-SHA256 (``GERNAS_USER_TOKEN_KEY``); the raw id never leaves the process.
* :func:`inject_context` / :func:`extract_context` / :func:`attach_headers` —
  carry W3C ``traceparent`` + baggage across processes, queues and services. The
  baggage also carries the delegating run id and delegation path so a remote child
  run links to its parent (Blueprint §3 multi-agent "parent agent, delegation path").
* :func:`gernas_task` / :func:`wrap_in_context` — keep background tasks and threads
  in the caller's trace.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import hashlib
import hmac
import logging
from collections.abc import Callable, Coroutine, Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any, TypeVar

from opentelemetry import baggage, context, propagate
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.propagators.textmap import default_setter

from gernas.attributes import REGISTRY, Ctx

T = TypeVar("T")
_log = logging.getLogger("gernas")
_warned_no_key = False
# The span that received the request in this process (e.g. the ASGI SERVER span). It is
# usually the trace root, which Opik reads for the trace's thread, so request_context also
# stamps its session / task onto it even though that span started before they were known.
_entry_span: contextvars.ContextVar[Any] = contextvars.ContextVar("gernas_entry_span", default=None)


def set_entry_span(span: Any) -> contextvars.Token:
    """Mark *span* as the request's entry span (adapters; reset with :func:`reset_entry_span`)."""
    return _entry_span.set(span)


def reset_entry_span(token: contextvars.Token) -> None:
    _entry_span.reset(token)


def _stamp_request_spans(entries: dict[str, str]) -> None:
    from opentelemetry import trace

    from gernas.tracer import safe_set

    for span in {id(s): s for s in (trace.get_current_span(), _entry_span.get()) if s is not None}.values():
        if span.is_recording():
            for k, v in entries.items():
                safe_set(span, k, v)


def _warn_no_key() -> None:
    global _warned_no_key
    _warned_no_key = True
    _log.warning("gernas: GERNAS_USER_TOKEN_KEY is not set - user identity is not recorded")


def tokenize_user_id(user_id: str, key: str) -> str:
    """HMAC-SHA256 token of a user id (stable per key, not reversible without it)."""
    return hmac.new(key.encode("utf-8"), user_id.encode("utf-8"), hashlib.sha256).hexdigest()


@contextmanager
def request_context(
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    channel: str | None = None,
    user_id: str | None = None,
    principal_type: str | None = None,
    tenant: str | None = None,
    department: str | None = None,
) -> Iterator[None]:
    """Attach request-scoped context; every span inside (and every remote hop) carries it."""
    from gernas.tracer import get_config

    entries: dict[str, str] = {}
    if user_id:
        cfg = get_config()
        key = cfg.user_token_key if cfg is not None else ""
        if key:
            entries[Ctx.TOKENIZED_USER_ID] = tokenize_user_id(str(user_id), key)
        elif not _warned_no_key:
            _warn_no_key()
    allowed = REGISTRY[Ctx.PRINCIPAL_TYPE].enum
    if principal_type and allowed and principal_type not in allowed:
        _log.warning("gernas: principal_type %r is not one of %s - not recorded", principal_type, list(allowed))
        principal_type = None
    for k, v in (
        (Ctx.SESSION_ID, session_id),
        (Ctx.TASK_ID, task_id),
        (Ctx.CHANNEL, channel),
        (Ctx.PRINCIPAL_TYPE, principal_type),
        (Ctx.TENANT, tenant),
        (Ctx.DEPARTMENT, department),
    ):
        if v:
            entries[k] = str(v)
    _stamp_request_spans(entries)
    ctx = context.get_current()
    for k, v in entries.items():
        ctx = baggage.set_baggage(k, v, ctx)
    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)


def with_run_baggage(ctx: Context | None = None) -> Context:
    """*ctx* plus the active run as baggage (parent_run_id / parent_agent_id / delegation_path).

    Used by :class:`GernasBaggagePropagator`, so every injection — ``inject_context``,
    the A2A interceptor, httpx hooks, and libraries that call ``propagate.inject``
    themselves (e.g. Agent Framework's MCP client) — carries the calling run.
    """
    from gernas.run_state import current_run

    ctx = ctx if ctx is not None else context.get_current()
    run = current_run()
    if run is not None:
        ctx = baggage.set_baggage(Ctx.PARENT_RUN_ID, run.agent_run_id, ctx)
        ctx = baggage.set_baggage(Ctx.PARENT_AGENT_ID, run.agent_id, ctx)
        ctx = baggage.set_baggage(Ctx.DELEGATION_PATH, run.delegation_path, ctx)
    return ctx


class GernasBaggagePropagator(W3CBaggagePropagator):
    """W3C baggage propagator that also writes the active Gernas run (installed by ``init_gernas``)."""

    def inject(self, carrier: Any, context: Context | None = None, setter: Any = default_setter) -> None:
        try:
            context = with_run_baggage(context)
        except Exception:  # noqa: BLE001 - propagation failure must not break the hop
            pass
        super().inject(carrier, context=context, setter=setter)


def inject_context(
    headers: MutableMapping[str, str] | None = None, *, ctx: Context | None = None
) -> MutableMapping[str, str]:
    """Write W3C trace context + GERNAS baggage (incl. the calling run) into *headers*."""
    carrier: MutableMapping[str, str] = {} if headers is None else headers
    try:
        propagate.inject(carrier, context=with_run_baggage(ctx))
    except Exception:  # noqa: BLE001 - propagation failure must not break the hop
        pass
    return carrier


def extract_context(headers: MutableMapping[str, str] | dict | None) -> Context:
    """Rebuild an OTel Context from *headers* (case-insensitive)."""
    try:
        return propagate.extract({str(k).lower(): v for k, v in (headers or {}).items()})
    except Exception:  # noqa: BLE001
        return context.get_current()


@contextmanager
def attach_headers(headers: MutableMapping[str, str] | dict | None) -> Iterator[Context]:
    """Consumer side of a hop: ``with attach_headers(message.headers): ...``."""
    ctx = extract_context(headers)
    token = context.attach(ctx)
    try:
        yield ctx
    finally:
        context.detach(token)


def baggage_value(key: str, ctx: Context | None = None) -> str | None:
    try:
        value = baggage.get_baggage(key, ctx)
        return None if value is None else str(value)
    except Exception:  # noqa: BLE001
        return None


def correlation() -> dict[str, str | None]:
    """Current correlation identifiers for application logs and audit records.

    Read-only: ``trace_id`` / ``span_id`` of the active span (W3C hex), the active
    run's ``agent_id`` / ``agent_run_id``, and the propagated ``session_id`` /
    ``task_id``. Values are None when unavailable. Never raises.
    """
    out: dict[str, str | None] = dict.fromkeys(
        ("trace_id", "span_id", "agent_id", "agent_run_id", "session_id", "task_id")
    )
    try:
        from opentelemetry import trace

        from gernas.run_state import current_run

        sc = trace.get_current_span().get_span_context()
        if sc.is_valid:
            out["trace_id"], out["span_id"] = format(sc.trace_id, "032x"), format(sc.span_id, "016x")
        run = current_run()
        if run is not None:
            out["agent_id"], out["agent_run_id"] = run.agent_id, run.agent_run_id
        out["session_id"], out["task_id"] = baggage_value(Ctx.SESSION_ID), baggage_value(Ctx.TASK_ID)
    except Exception:  # noqa: BLE001
        pass
    return out


def gernas_task(coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
    """``asyncio.create_task`` that runs *coro* in the caller's context (same trace and run)."""
    return asyncio.get_running_loop().create_task(coro, name=name, context=contextvars.copy_context())


def wrap_in_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Bind *fn* to the current context so it can run on another thread."""
    ctx = contextvars.copy_context()

    @functools.wraps(fn)
    def bound(*args: Any, **kwargs: Any) -> T:
        return ctx.copy().run(fn, *args, **kwargs)

    return bound
