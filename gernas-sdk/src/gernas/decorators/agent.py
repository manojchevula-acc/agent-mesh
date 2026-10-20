"""@trace_agent — one ``agent.run`` span per agent run (Blueprint §2 agent + execution
context, §3 Agent Runtime, §5 agentic KPIs).

Every run gets a fresh ``agent_run_id`` and a :class:`RunState`. A nested or delegated
run records ``parent_run_id`` and extends ``delegation_path`` (from the enclosing local
run, or from baggage when the parent is in another process).

:func:`start_run` / :func:`end_run` expose the same lifecycle to framework adapters
(LangChain / LangGraph callbacks) so every framework produces the identical span.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas._metrics import observe, record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.context import baggage_value
from gernas.decorators._core import (
    Call,
    Spec,
    mark_status,
    record_exception,
    resolve,
    stamp_context,
    stamp_outcome,
    wrap,
)
from gernas.run_state import RunState, bind, current_run, unbind
from gernas.tracer import get_config, get_tracer, safe_set

F = TypeVar("F", bound=Callable[..., Any])
_ERROR_TERMINATIONS = ("error", "timeout")


def new_run(
    span: Any, agent_id: str, *, name: str | None, version: str | None, agent_type: str, framework: str | None
) -> RunState:
    """Create the RunState for *span* and stamp the agent / execution context on it."""
    cfg = get_config()
    enclosing = current_run()
    parent_run_id: str | None
    if enclosing is not None:
        parent_run_id, parent_path = enclosing.agent_run_id, enclosing.delegation_path
    else:
        parent_run_id, parent_path = baggage_value(Ctx.PARENT_RUN_ID), baggage_value(Ctx.DELEGATION_PATH) or ""
    version = version or (cfg.agent_version if cfg is not None else None)
    run = RunState(
        agent_id=agent_id,
        agent_version=version or "",
        parent_run_id=parent_run_id,
        delegation_path=f"{parent_path}>{agent_id}" if parent_path else agent_id,
        span=span,
        loop_threshold=cfg.loop_threshold if cfg is not None else 3,
        max_steps=cfg.max_steps if cfg is not None else 50,
    )
    safe_set(span, Ctx.AGENT_ID, agent_id)
    safe_set(span, Ctx.AGENT_NAME, name or agent_id)
    safe_set(span, Ctx.AGENT_VERSION, version)
    safe_set(span, Ctx.AGENT_TYPE, agent_type)
    safe_set(span, Ctx.FRAMEWORK, framework)
    safe_set(span, Ctx.AGENT_RUN_ID, run.agent_run_id)
    safe_set(span, Ctx.PARENT_RUN_ID, parent_run_id)
    safe_set(span, Ctx.DELEGATION_PATH, run.delegation_path)
    return run


def finish_run(span: Any, run: RunState, exc: BaseException | None, duration_ms: float) -> str:
    """Stamp the run outcome (termination reason, KPIs) and record agent metrics."""
    reason = run.resolve_termination(exc)
    safe_set(span, Ctx.TERMINATION_REASON, reason)
    safe_set(span, Ctx.STEP_COUNT, run.steps)
    safe_set(span, Ctx.RETRY_COUNT, run.retry_count)
    if run.loop_detected:
        safe_set(span, Ctx.LOOP_DETECTED, True)
        safe_set(span, Ctx.LOOP_REPEAT_COUNT, run.loop_repeat_count)
    if run.max_step_violation:
        safe_set(span, Ctx.MAX_STEP_VIOLATION, True)
    if exc is None and reason == "max_steps":
        mark_status(span, StatusCode.ERROR, "max_steps")
    dims = {Ctx.AGENT_ID: run.agent_id, Ctx.TERMINATION_REASON: reason}
    record(
        Metric.AGENT_RUNS,
        {**dims, Ctx.AGENT_VERSION: run.agent_version or None},
        histogram=Metric.AGENT_RUN_DURATION,
        duration_ms=duration_ms,
    )
    observe(Metric.AGENT_STEPS, run.steps, {Ctx.AGENT_ID: run.agent_id})
    return reason


@dataclass
class AgentRun:
    """A run opened outside a decorator (framework callbacks)."""

    span: Any
    run: RunState
    t0: float

    def end(self, exc: BaseException | None = None) -> None:
        import time

        try:
            reason = finish_run(self.span, self.run, exc, round((time.perf_counter() - self.t0) * 1000, 3))
            stamp_outcome(self.span, exc)
            if exc is not None and reason in _ERROR_TERMINATIONS:
                record_exception(self.span, exc)
                self.span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            elif exc is None and reason != "max_steps":
                self.span.set_status(Status(StatusCode.OK))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.span.end()


def start_run(
    agent_id: str,
    *,
    agent_type: str,
    name: str | None = None,
    version: str | None = None,
    framework: str | None = None,
    parent: Any = None,
) -> AgentRun:
    """Open an ``agent.run`` span + RunState without a decorator (caller must call ``.end()``)."""
    import time

    span = get_tracer().start_span(SpanName.AGENT, context=parent, kind=SpanKind.INTERNAL)
    stamp_context(span, SpanName.AGENT)
    return AgentRun(
        span,
        new_run(span, agent_id, name=name, version=version, agent_type=agent_type, framework=framework),
        time.perf_counter(),
    )


class AgentSpec(Spec):
    op = SpanName.AGENT

    def __init__(self, agent_id: Any, name: Any, version: str | None, agent_type: str, framework: str) -> None:
        self.agent_id, self.name, self.version = agent_id, name, version
        self.agent_type, self.framework = agent_type, framework

    def _value(self, value: Any, call: Call) -> Any:
        return resolve(value, call) if callable(value) else value

    def start(self, span: Any, call: Call) -> None:
        agent_id = str(self._value(self.agent_id, call) or getattr(call.fn, "__name__", "agent"))
        call.state["run"] = new_run(
            span,
            agent_id,
            name=self._value(self.name, call),
            version=self.version,
            agent_type=self.agent_type,
            framework=self.framework,
        )

    def activate(self, span: Any, call: Call) -> Any:
        run = call.state.get("run")
        return bind(run) if run is not None else None

    def deactivate(self, call: Call, token: Any) -> None:
        if token is not None:
            unbind(token)

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        run: RunState | None = call.state.get("run")
        return (run.resolve_termination(exc) if run else "error") in _ERROR_TERMINATIONS

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        run: RunState | None = call.state.get("run")
        if run is not None:
            finish_run(span, run, exc, call.elapsed_ms())


def trace_agent(
    agent_id: str | Callable[..., str] | None = None,
    *,
    agent_type: str,
    name: str | Callable[..., str] | None = None,
    version: str | None = None,
    framework: str = "custom",
) -> Callable[[F], F]:
    """Trace an agent run as ``agent.run``.

    ``agent_type`` is mandatory Blueprint §2 agent context (e.g. ``orchestrator``,
    ``worker``). ``agent_id`` / ``name`` may be callables of the call arguments.
    ``version`` defaults to ``GERNAS_AGENT_VERSION`` (or ``GERNAS_SERVICE_VERSION``).
    """

    def decorator(fn: F) -> F:
        return wrap(fn, AgentSpec(agent_id, name, version, agent_type, framework))

    return decorator
