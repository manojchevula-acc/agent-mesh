"""Human-in-the-loop approval spans (WP1-CORE-02b steps 5-6, WP1-CORE-04 steps 1-2).

An approval can wait for hours. The span is started with ``tracer.start_span``
(never held open in a ``with`` block or attached to a context), the span object
is kept in a bounded correlation table keyed by ticket id, and nothing blocks.
:func:`complete_hitl` closes it with the outcome, waited time and approver
*role* (never identity). A timeout ends with a distinct ERROR status
``hitl.timed_out`` so it can be told apart from a rejection (status OK).
Human Intervention Rate = count(hitl.approval) / count(agent.run) from records alone.
"""
from __future__ import annotations

import functools
import inspect
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import get_field
from gernas.metrics.agentic import record
from gernas.run_state import RunState, current_run
from gernas.tracer import get_config, get_tracer, safe_set

F = TypeVar("F", bound=Callable[..., Any])

OUTCOMES = ("approved", "rejected", "changed", "timed_out")
_MAX_OPEN = 10_000


@dataclass
class _Open:
    span: Any
    started: float
    action: str
    timeout_s: float | None
    run: RunState | None


_open: dict[str, _Open] = {}
_lock = threading.Lock()


def start_hitl(ticket_id: str, action: str, *, timeout_s: float | None = None,
               approver_role: str | None = None, parent: Any = None) -> Any:
    """Open a long-lived ``hitl.approval`` span for *ticket_id* and return it.

    The span is a child of the current span (or *parent* context) but is not made
    current, so the caller's context is untouched while the approval is pending.
    """
    sweep_expired()
    try:
        ctx = parent if parent is not None else otel_context.get_current()
        span = get_tracer().start_span(SpanName.HITL, context=ctx, kind=SpanKind.INTERNAL)
        for key, value in get_config().envelope.items():
            safe_set(span, key, value)
        safe_set(span, Ctx.OPERATION, SpanName.HITL)
        safe_set(span, Ctx.HITL_TICKET_ID, ticket_id)
        safe_set(span, Ctx.HITL_ACTION, action)
        safe_set(span, Ctx.HITL_TIMEOUT_S, timeout_s)
        safe_set(span, Ctx.HITL_APPROVER_ROLE, approver_role)
        run = current_run()
        if run is not None:
            safe_set(span, Ctx.AGENT_RUN_ID, run.agent_run_id)
        with _lock:
            if len(_open) >= _MAX_OPEN:  # bound memory: force-close the oldest as timed out
                oldest = min(_open, key=lambda k: _open[k].started)
                _close(oldest, _open.pop(oldest), "timed_out", None)
            _open[ticket_id] = _Open(span, time.monotonic(), action, timeout_s, run)
        return span
    except Exception:  # noqa: BLE001
        return trace.INVALID_SPAN


def complete_hitl(ticket_id: str, outcome: str, *, approver_role: str | None = None) -> bool:
    """Close the approval span for *ticket_id*. Returns False if it is unknown."""
    if outcome not in OUTCOMES:
        raise ValueError(f"HITL outcome must be one of {OUTCOMES}")
    with _lock:
        entry = _open.pop(ticket_id, None)
    if entry is None:
        return False
    _close(ticket_id, entry, outcome, approver_role)
    return True


def _close(ticket_id: str, entry: _Open, outcome: str, approver_role: str | None) -> None:
    span = entry.span
    try:
        waited = round((time.monotonic() - entry.started) * 1000.0, 3)
        safe_set(span, Ctx.HITL_OUTCOME, outcome)
        safe_set(span, Ctx.HITL_WAITED_MS, waited)
        safe_set(span, Ctx.HITL_APPROVER_ROLE, approver_role)
        if outcome == "timed_out":
            span.set_status(Status(StatusCode.ERROR, "hitl.timed_out"))
        else:
            span.set_status(Status(StatusCode.OK))
        if outcome == "rejected" and entry.run is not None and entry.run.termination_override is None:
            entry.run.set_termination("human_rejected")
        record(Metric.HITL_OUTCOMES, {Ctx.HITL_ACTION: entry.action, Ctx.HITL_OUTCOME: outcome})
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            span.end()
        except Exception:  # noqa: BLE001
            pass


def sweep_expired(now: float | None = None) -> int:
    """Close pending approvals whose timeout has passed as ``timed_out``."""
    now = time.monotonic() if now is None else now
    with _lock:
        expired = [(k, e) for k, e in _open.items() if e.timeout_s is not None and now - e.started > e.timeout_s]
        for k, _ in expired:
            _open.pop(k, None)
    for k, e in expired:
        _close(k, e, "timed_out", None)
    return len(expired)


def pending_hitl() -> list[str]:
    with _lock:
        return list(_open)


def _ticket(result: Any, ticket_from: Callable[[Any], str] | None) -> str | None:
    if ticket_from is not None:
        try:
            return str(ticket_from(result))
        except Exception:  # noqa: BLE001
            return None
    if isinstance(result, str):
        return result
    value = get_field(result, "ticket_id", "approval_id", "request_id", "id")
    return None if value is None else str(value)


def trace_hitl(action: str | None = None, *, timeout_s: float | None = None,
               ticket_from: Callable[[Any], str] | None = None,
               outcome_from: Callable[[Any], str] | None = None) -> Callable[[F], F]:
    """Trace a human approval as ``hitl.approval``.

    Two modes:

    * **ticket mode** (default) — the decorated function *creates* an approval
      request and returns immediately; the span opens and stays pending until
      :func:`complete_hitl` is called with the returned ticket id;
    * **wait mode** — with ``outcome_from``, the decorated function waits for the
      decision itself and the span closes with ``outcome_from(result)``.
    """
    def decorator(fn: F) -> F:
        act = action or fn.__name__

        def _before(args: tuple, kwargs: dict) -> tuple[Any, str | None]:
            ctx = otel_context.get_current()
            if outcome_from is None:
                return ctx, None
            ticket = f"hitl_{uuid.uuid4().hex}"  # wait mode: the span covers the whole wait
            start_hitl(ticket, act, timeout_s=timeout_s, parent=ctx)
            return ctx, ticket

        def _after(result: Any, ctx: Any, pending: str | None) -> None:
            if pending is not None:
                with _lock:
                    entry = _open.get(pending)
                real = _ticket(result, ticket_from)
                if entry is not None and real:
                    safe_set(entry.span, Ctx.HITL_TICKET_ID, real)
                try:
                    outcome = outcome_from(result) if outcome_from else None
                except Exception:  # noqa: BLE001
                    outcome = None
                complete_hitl(pending, outcome if outcome in OUTCOMES else "changed")
                return
            ticket = _ticket(result, ticket_from)
            if ticket:
                start_hitl(ticket, act, timeout_s=timeout_s, parent=ctx)

        def _failed(exc: BaseException, pending: str | None) -> None:
            if pending is not None:
                timed_out = isinstance(exc, TimeoutError)
                with _lock:
                    entry = _open.pop(pending, None)
                if entry is not None:
                    _close(pending, entry, "timed_out" if timed_out else "rejected", None)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                ctx, pending = _before(args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    _failed(exc, pending)
                    raise
                _after(result, ctx, pending)
                return result
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx, pending = _before(args, kwargs)
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                _failed(exc, pending)
                raise
            _after(result, ctx, pending)
            return result
        return sync_wrapper  # type: ignore[return-value]
    return decorator
