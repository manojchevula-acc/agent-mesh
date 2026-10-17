"""@trace_delegation — agent-to-agent hand-offs (Blueprint §3 Multi-Agent Orchestration:
parent agent, child agent, delegation path, inter-agent calls, latency, failures, hand-offs).

The ``agent.delegate`` span records parent → child and the delegation path. Its
duration is the hand-off latency. The child run joins the same trace: in-process
through the OTel context, across processes through :func:`gernas.inject_context`
(injected automatically into a ``headers`` / ``carrier`` dict argument, and by the
A2A adapter), and links back with ``parent_run_id``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from gernas._metrics import record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.context import inject_context
from gernas.decorators._core import Call, Spec, resolve, wrap
from gernas.run_state import current_run
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])


class DelegationSpec(Spec):
    op = SpanName.DELEGATION
    kind = "CLIENT"

    def __init__(self, child_agent_id: Any, child_arg: str | None, headers_arg: str | None) -> None:
        self.child_agent_id, self.child_arg, self.headers_arg = child_agent_id, child_arg, headers_arg

    def start(self, span: Any, call: Call) -> None:
        if self.child_arg:
            child = call.arg(self.child_arg, position=0)
        else:
            child = resolve(self.child_agent_id, call) if callable(self.child_agent_id) else self.child_agent_id
        run = current_run()
        parent = run.agent_id if run is not None else None
        call.state["child"], call.state["parent"] = str(child or "unknown"), parent
        safe_set(span, Ctx.PARENT_AGENT_ID, parent)
        safe_set(span, Ctx.CHILD_AGENT_ID, call.state["child"])
        safe_set(
            span, Ctx.DELEGATION_PATH, f"{run.delegation_path}>{call.state['child']}" if run else call.state["child"]
        )
        if run is not None:
            run.record_step()
        for name in (self.headers_arg,) if self.headers_arg else ("headers", "carrier"):
            carrier = call.kwargs.get(name) if name in call.kwargs else call.arg(name)
            if isinstance(carrier, dict):
                inject_context(carrier)
                break

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        dims = {Ctx.PARENT_AGENT_ID: call.state.get("parent"), Ctx.CHILD_AGENT_ID: call.state.get("child")}
        record(
            Metric.AGENT_DELEGATIONS,
            {**dims, Ctx.EXECUTION_STATUS: "error" if exc else "ok"},
            histogram=Metric.AGENT_DELEGATION_DURATION,
            duration_ms=call.elapsed_ms(),
        )


def trace_delegation(
    child_agent_id: str | Callable[..., str] | None = None,
    *,
    child_arg: str | None = None,
    headers_arg: str | None = None,
) -> Callable[[F], F]:
    """Trace a hand-off from the current agent run to *child_agent_id* as ``agent.delegate``.

    Use ``child_arg="name"`` when the target agent is an argument of the call.
    """

    def decorator(fn: F) -> F:
        return wrap(fn, DelegationSpec(child_agent_id, child_arg, headers_arg))

    return decorator
