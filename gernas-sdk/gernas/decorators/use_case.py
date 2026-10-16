"""@trace_use_case — the outermost span of a business request (WP1-CORE-02d step 2).

Binds use case, application, channel and business domain; agent spans nest
beneath it. ``outcome_from(result)`` supplies the use-case specific
``business_outcome`` (success / partial / failure / abandoned) that Task Success
Rate depends on — the platform cannot infer it.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import Call, Spec, resolve, wrap
from gernas.tracer import get_config, safe_set

F = TypeVar("F", bound=Callable[..., Any])

OUTCOMES = ("success", "partial", "failure", "abandoned")


class UseCaseSpec(Spec):
    op = SpanName.USE_CASE
    kind = "SERVER"

    def __init__(self, use_case_id: str | None, application_id: str | None, business_domain: str | None,
                 channel: str | Callable[..., str] | None, outcome_from: Callable[[Any], str] | None,
                 new_trace: bool) -> None:
        self.use_case_id, self.application_id, self.business_domain = use_case_id, application_id, business_domain
        self.channel, self.outcome_from, self.new_trace = channel, outcome_from, new_trace

    def start(self, span: Any, call: Call) -> None:
        cfg = get_config()
        safe_set(span, Ctx.USE_CASE_ID, self.use_case_id or cfg.use_case_id)
        safe_set(span, Ctx.APPLICATION_ID, self.application_id or cfg.application_id)
        safe_set(span, Ctx.BUSINESS_DOMAIN, self.business_domain or cfg.business_domain)
        channel = resolve(self.channel, call) if callable(self.channel) else self.channel
        safe_set(span, Ctx.CHANNEL, channel or call.arg("channel"))

    def success(self, span: Any, call: Call, result: Any) -> None:
        safe_set(span, Ctx.EXECUTION_STATUS, "ok")
        if self.outcome_from is not None:
            outcome = resolve(self.outcome_from, call, result)
            if outcome in OUTCOMES:
                safe_set(span, Ctx.BUSINESS_OUTCOME, outcome)

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        is_error = super().failure(span, call, exc)
        safe_set(span, Ctx.EXECUTION_STATUS, "error" if is_error else "ok")
        safe_set(span, Ctx.BUSINESS_OUTCOME, "failure" if is_error else "abandoned")
        return is_error


def trace_use_case(use_case_id: str | None = None, *, application_id: str | None = None,
                   business_domain: str | None = None, channel: str | Callable[..., str] | None = None,
                   outcome_from: Callable[[Any], str] | None = None, new_trace: bool = False) -> Callable[[F], F]:
    """Trace a business request as ``use_case.request``.

    Values default to ``GERNAS_USE_CASE_ID`` / ``GERNAS_APPLICATION_ID`` /
    ``GERNAS_BUSINESS_DOMAIN``. ``new_trace=True`` always starts a fresh trace.
    """
    def decorator(fn: F) -> F:
        return wrap(fn, UseCaseSpec(use_case_id, application_id, business_domain, channel, outcome_from, new_trace))
    return decorator
