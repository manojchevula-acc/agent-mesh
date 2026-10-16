"""@trace_delegation — agent-to-agent hand-offs (WP1-CORE-02d steps 3-4).

The parent span records who delegated to whom and the round-trip latency. The
child joins the same trace: in-process through the OTel context, across a
process boundary through headers produced by :func:`gernas.inject_context`
(``traceparent`` + baggage ``gernas.parent_run_id``) — the decorator injects
them into a ``headers``/``carrier`` dict argument automatically, and the A2A
adapter does it for A2A clients. The child's ``@trace_agent`` sets
``parent_run_id`` and generates its own ``agent_run_id``.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from opentelemetry import baggage, context as otel_context

from gernas.async_context import inject_context
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, resolve, wrap
from gernas.metrics.agentic import record
from gernas.run_state import current_run
from gernas.tracer import get_config, safe_set

F = TypeVar("F", bound=Callable[..., Any])


class DelegationSpec(Spec):
    op = SpanName.DELEGATION
    kind = "CLIENT"

    def __init__(self, child_agent_id: str | Callable[..., str] | None, child_arg: str | None,
                 parent_agent_id: str | None, protocol: str, headers_arg: str | None) -> None:
        self.child_agent_id, self.child_arg = child_agent_id, child_arg
        self.parent_agent_id, self.protocol, self.headers_arg = parent_agent_id, protocol, headers_arg

    def _child(self, call: Call) -> str:
        if self.child_arg:
            value = call.arg(self.child_arg, position=0)
        elif callable(self.child_agent_id):
            value = resolve(self.child_agent_id, call)
        else:
            value = self.child_agent_id
        return str(value or "unknown")

    def start(self, span: Any, call: Call) -> None:
        run = current_run()
        child = self._child(call)
        call.state["child"] = child
        safe_set(span, Ctx.PARENT_AGENT_ID, self.parent_agent_id or (run.agent_id if run else get_config().service_name))
        safe_set(span, Ctx.CHILD_AGENT_ID, child)
        safe_set(span, Ctx.DELEGATION_PROTOCOL, self.protocol)
        safe_set(span, Ctx.DELEGATION_DEPTH, (run.depth + 1) if run else 1)
        if run is not None:
            safe_set(span, Ctx.AGENT_RUN_ID, run.agent_run_id)
            run.record_step()
        carrier = None
        for name in ((self.headers_arg,) if self.headers_arg else ("headers", "carrier")):
            candidate = call.kwargs.get(name) if name in call.kwargs else call.arg(name)
            if isinstance(candidate, dict):
                carrier = candidate
                break
        if carrier is not None:
            inject_context(carrier)

    def activate(self, span: Any, call: Call) -> Any:
        """Expose the delegating run id as baggage for transports that inject it."""
        run = current_run()
        if run is None:
            return None
        return otel_context.attach(baggage.set_baggage(Ctx.PARENT_RUN_ID, run.agent_run_id))

    def deactivate(self, call: Call, token: Any) -> None:
        if token is not None:
            otel_context.detach(token)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        latency = call.elapsed_ms()
        safe_set(span, Ctx.DELEGATION_LATENCY_MS, latency)
        record(Metric.A2A_CALLS, {Ctx.CHILD_AGENT_ID: call.state.get("child", "unknown"),
                                  Ctx.DELEGATION_PROTOCOL: self.protocol,
                                  "result": "error" if exc else "success"},
               histogram=Metric.A2A_DURATION, latency_ms=latency)


def trace_delegation(child_agent_id: str | Callable[..., str] | None = None, *, child_arg: str | None = None,
                     parent_agent_id: str | None = None, protocol: str = "a2a",
                     headers_arg: str | None = None) -> Callable[[F], F]:
    """Trace a delegation from the current agent to *child_agent_id* as ``agent.delegate``.

    Use ``child_arg="name"`` when the target agent is an argument of the call.
    """
    def decorator(fn: F) -> F:
        return wrap(fn, DelegationSpec(child_agent_id, child_arg, parent_agent_id, protocol, headers_arg))
    return decorator
