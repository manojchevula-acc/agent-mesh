"""@trace_planning and planning_step — plans and their steps (WP1-CORE-02c step 5)."""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from opentelemetry import trace

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import Call, Spec, _begin, _end, as_list, get_field, resolve, wrap
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])


class PlanningSpec(Spec):
    op = SpanName.PLANNING

    def __init__(self, plan_id: str | Callable[..., str] | None, attempt_arg: str,
                 steps_from: Callable[[Any], Any] | None) -> None:
        self.plan_id, self.attempt_arg, self.steps_from = plan_id, attempt_arg, steps_from

    def start(self, span: Any, call: Call) -> None:
        plan_id = (resolve(self.plan_id, call) if callable(self.plan_id) else self.plan_id) \
            or call.arg("plan_id") or f"plan_{uuid.uuid4().hex[:16]}"
        call.state["plan_id"] = str(plan_id)
        attempt = call.arg(self.attempt_arg)
        safe_set(span, Ctx.PLAN_ID, str(plan_id))
        safe_set(span, Ctx.ATTEMPT_NUMBER, int(attempt) if isinstance(attempt, int) else 1)

    def success(self, span: Any, call: Call, result: Any) -> None:
        steps = resolve(self.steps_from, call, result) if self.steps_from else (
            get_field(result, "steps") if not isinstance(result, (list, tuple)) else result)
        items = as_list(steps) if steps is not None else None
        if items is not None:
            safe_set(span, Ctx.PLAN_STEP_COUNT, len(items))
        elif isinstance(steps, int):
            safe_set(span, Ctx.PLAN_STEP_COUNT, steps)
        found = get_field(result, "plan_id")
        if isinstance(found, str):
            safe_set(span, Ctx.PLAN_ID, found)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.PLANNING_LATENCY_MS, call.elapsed_ms())


def trace_planning(*, plan_id: str | Callable[..., str] | None = None, attempt_arg: str = "attempt",
                   steps_from: Callable[[Any], Any] | None = None) -> Callable[[F], F]:
    """Trace plan generation as ``agent.plan``; records the plan's step count."""
    def decorator(fn: F) -> F:
        return wrap(fn, PlanningSpec(plan_id, attempt_arg, steps_from))
    return decorator


class _StepSpec(Spec):
    op = SpanName.PLAN_STEP

    def __init__(self, plan_id: str, step_id: str, step_type: str, attempt: int) -> None:
        self.plan_id, self.step_id, self.step_type, self.attempt = plan_id, step_id, step_type, attempt

    def start(self, span: Any, call: Call) -> None:
        safe_set(span, Ctx.PLAN_ID, self.plan_id)
        safe_set(span, Ctx.STEP_ID, self.step_id)
        safe_set(span, Ctx.STEP_TYPE, self.step_type)
        safe_set(span, Ctx.ATTEMPT_NUMBER, self.attempt)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.DURATION_MS, call.elapsed_ms())


@contextmanager
def planning_step(plan_id: str, step_id: str, step_type: str = "step", attempt_number: int = 1) -> Iterator[Any]:
    """Context manager tracing one executed plan step as ``agent.plan.step``."""
    spec = _StepSpec(plan_id, step_id, step_type, attempt_number)
    call = Call(planning_step, (), {}, time.perf_counter())
    try:
        span, token = _begin(spec, call)
    except Exception:  # noqa: BLE001
        yield trace.get_current_span()
        return
    exc: BaseException | None = None
    try:
        yield span
    except BaseException as e:
        exc = e
        raise
    finally:
        _end(spec, span, call, exc, token)
