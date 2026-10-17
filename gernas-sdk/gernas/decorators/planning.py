"""@trace_step and @trace_planning — the "Step" level of the Blueprint §10 drill-down
(Use Case → Agent → Agent Version → Run → Step → Span) and Blueprint §2 planning context.

``agent.step`` is the single step model for every framework: a MAF workflow
executor, a LangGraph node and a custom plan step all produce the same span.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import (
    Call,
    Spec,
    as_list,
    get_field,
    record_exception,
    resolve,
    stamp_context,
    stamp_outcome,
    wrap,
)
from gernas.run_state import current_run, error_category
from gernas.tracer import get_tracer, safe_set

F = TypeVar("F", bound=Callable[..., Any])


def _stamp_step(span: Any, step_id: str, step_type: str, attempt: int, plan_id: str | None, run: Any = None) -> None:
    safe_set(span, Ctx.STEP_ID, step_id)
    safe_set(span, Ctx.STEP_TYPE, step_type)
    safe_set(span, Ctx.ATTEMPT_NUMBER, attempt)
    safe_set(span, Ctx.PLAN_ID, plan_id)
    run = run if run is not None else current_run()
    if run is not None:
        run.record_step()


class StepSpec(Spec):
    op = SpanName.STEP

    def __init__(self, step_id: Any, step_type: str, attempt_number: Any, plan_id: Any) -> None:
        self.step_id, self.step_type, self.attempt_number, self.plan_id = step_id, step_type, attempt_number, plan_id

    def start(self, span: Any, call: Call) -> None:
        val = lambda v: resolve(v, call) if callable(v) else v  # noqa: E731
        attempt = val(self.attempt_number)
        _stamp_step(
            span,
            str(val(self.step_id) or getattr(call.fn, "__name__", "step")),
            self.step_type,
            attempt if isinstance(attempt, int) else 1,
            val(self.plan_id),
        )


def trace_step(
    step_id: str | Callable[..., str] | None = None,
    *,
    step_type: str,
    attempt_number: int | Callable[..., int] = 1,
    plan_id: str | Callable[..., str] | None = None,
) -> Callable[[F], F]:
    """Trace one executed step (workflow executor, graph node, plan step) as ``agent.step``."""

    def decorator(fn: F) -> F:
        return wrap(fn, StepSpec(step_id, step_type, attempt_number, plan_id))

    return decorator


@dataclass
class OpenStep:
    """A step opened by a framework callback (LangGraph node); caller must ``.end()``."""

    span: Any

    def end(self, exc: BaseException | None = None) -> None:
        try:
            stamp_outcome(self.span, exc)
            if exc is not None and error_category(exc) != "cancelled":
                record_exception(self.span, exc)
                self.span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            elif exc is None:
                self.span.set_status(Status(StatusCode.OK))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.span.end()


def open_step(
    step_id: str,
    step_type: str,
    *,
    parent: Any = None,
    attempt_number: int = 1,
    plan_id: str | None = None,
    run: Any = None,
) -> OpenStep:
    """Open an ``agent.step`` span without a decorator (framework callbacks); caller must ``.end()``."""
    span = get_tracer().start_span(SpanName.STEP, context=parent, kind=SpanKind.INTERNAL)
    stamp_context(span, SpanName.STEP, run)
    _stamp_step(span, step_id, step_type, attempt_number, plan_id, run)
    return OpenStep(span)


class PlanningSpec(Spec):
    op = SpanName.PLANNING

    def __init__(self, plan_id: Any, attempt_arg: str, steps_from: Callable[[Any], Any] | None) -> None:
        self.plan_id, self.attempt_arg, self.steps_from = plan_id, attempt_arg, steps_from

    def start(self, span: Any, call: Call) -> None:
        plan_id = (
            (resolve(self.plan_id, call) if callable(self.plan_id) else self.plan_id)
            or call.arg("plan_id")
            or f"plan_{uuid.uuid4().hex[:16]}"
        )
        attempt = call.arg(self.attempt_arg)
        safe_set(span, Ctx.PLAN_ID, str(plan_id))
        safe_set(span, Ctx.ATTEMPT_NUMBER, attempt if isinstance(attempt, int) else 1)

    def success(self, span: Any, call: Call, result: Any) -> None:
        steps = (
            resolve(self.steps_from, call, result)
            if self.steps_from
            else (result if isinstance(result, (list, tuple)) else get_field(result, "steps"))
        )
        items = as_list(steps) if steps is not None else None
        if items is not None:
            safe_set(span, Ctx.PLAN_STEP_COUNT, len(items))


def trace_planning(
    *,
    plan_id: str | Callable[..., str] | None = None,
    attempt_arg: str = "attempt",
    steps_from: Callable[[Any], Any] | None = None,
) -> Callable[[F], F]:
    """Trace plan generation as ``agent.plan`` (planning latency = span duration)."""

    def decorator(fn: F) -> F:
        return wrap(fn, PlanningSpec(plan_id, attempt_arg, steps_from))

    return decorator
