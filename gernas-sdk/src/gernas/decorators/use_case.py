"""@trace_use_case — the outermost span of a business request (Blueprint §3 Use Case
Layer: use-case ID, application, channel, business domain, user/session, request type,
business outcome).

``outcome_from(result)`` supplies the use-case specific ``business_outcome`` that the
Blueprint §5 task-success and cost-per-successful-task KPIs depend on — the SDK cannot
infer it. User and session come from :func:`gernas.request_context`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from gernas._metrics import record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, resolve, wrap
from gernas.run_state import error_category
from gernas.tracer import get_config, safe_set

F = TypeVar("F", bound=Callable[..., Any])

OUTCOMES = ("success", "partial", "failure", "abandoned")


class UseCaseSpec(Spec):
    op = SpanName.USE_CASE
    kind = "SERVER"

    def __init__(
        self,
        use_case_id: str | None,
        channel: Any,
        request_type: Any,
        outcome_from: Callable[[Any], str] | None,
        new_trace: bool,
    ) -> None:
        self.use_case_id, self.channel, self.request_type = use_case_id, channel, request_type
        self.outcome_from, self.new_trace = outcome_from, new_trace

    def start(self, span: Any, call: Call) -> None:
        val = lambda v: resolve(v, call) if callable(v) else v  # noqa: E731
        safe_set(span, Ctx.USE_CASE_ID, self.use_case_id)  # per-request override of GERNAS_USE_CASE_ID
        safe_set(span, Ctx.CHANNEL, val(self.channel))
        safe_set(span, Ctx.REQUEST_TYPE, val(self.request_type))

    def success(self, span: Any, call: Call, result: Any) -> None:
        if self.outcome_from is not None:
            outcome = resolve(self.outcome_from, call, result)
            if outcome in OUTCOMES:
                safe_set(span, Ctx.BUSINESS_OUTCOME, outcome)

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        cancelled = error_category(exc) == "cancelled"
        safe_set(span, Ctx.BUSINESS_OUTCOME, "abandoned" if cancelled else "failure")
        return not cancelled

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        attrs = getattr(span, "attributes", None) or {}
        cfg = get_config()
        status = "ok" if exc is None else ("cancelled" if error_category(exc) == "cancelled" else "error")
        record(
            Metric.USE_CASE_REQUESTS,
            {
                Ctx.USE_CASE_ID: self.use_case_id or (cfg.use_case_id if cfg else None),
                Ctx.CHANNEL: attrs.get(Ctx.CHANNEL),
                Ctx.BUSINESS_OUTCOME: attrs.get(Ctx.BUSINESS_OUTCOME),
                Ctx.EXECUTION_STATUS: status,
            },
        )


def trace_use_case(
    use_case_id: str | None = None,
    *,
    channel: str | Callable[..., str] | None = None,
    request_type: str | Callable[..., str] | None = None,
    outcome_from: Callable[[Any], str] | None = None,
    new_trace: bool = False,
) -> Callable[[F], F]:
    """Trace a business request as ``use_case.request`` (the root of the request trace).

    ``use_case_id`` overrides ``GERNAS_USE_CASE_ID`` for services hosting several use
    cases. ``new_trace=True`` always starts a fresh trace.
    """

    def decorator(fn: F) -> F:
        return wrap(fn, UseCaseSpec(use_case_id, channel, request_type, outcome_from, new_trace))

    return decorator
