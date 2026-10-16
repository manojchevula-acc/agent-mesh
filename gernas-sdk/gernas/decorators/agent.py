"""@trace_agent — one span per agent run (WP1-CORE-02a step 2, CORE-03, CTX-03/04).

Every invocation gets a fresh ``gernas.agent_run_id`` and a :class:`RunState`
bound for the life of the run. ``parent_run_id`` comes from the enclosing local
run, or from W3C baggage when the run was delegated across a process boundary.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, TypeVar

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from gernas.async_context import baggage_value
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, mark_status, resolve, wrap
from gernas.metrics.agentic import record
from gernas.run_state import RunState, bind, current_run, is_known_run, register_run, unbind
from gernas.tracer import get_config, safe_set
from gernas.utils.hashing import hash_inputs

F = TypeVar("F", bound=Callable[..., Any])
_log = logging.getLogger("gernas")

_ERRORS = ("error", "timeout")


class AgentSpec(Spec):
    op = SpanName.AGENT
    kind = "INTERNAL"

    def __init__(self, agent_id: str | Callable[..., str] | None, name: str | Callable[..., str] | None,
                 version: str | None,
                 framework: str, agent_type: str | None, attributes: Mapping[str, Any] | None,
                 parent_run_id: str | Callable[..., str] | None) -> None:
        self.agent_id = agent_id
        self.name = name
        self.version = version
        self.framework = framework
        self.agent_type = agent_type
        self.attributes = dict(attributes or {})
        self.parent_run_id = parent_run_id

    def _agent_id(self, call: Call) -> str:
        value = resolve(self.agent_id, call) if callable(self.agent_id) else self.agent_id
        return str(value or getattr(call.fn, "__name__", "agent"))

    def start(self, span: Any, call: Call) -> None:
        cfg = get_config()
        agent_id = self._agent_id(call)
        enclosing = current_run()
        explicit_parent = resolve(self.parent_run_id, call) if callable(self.parent_run_id) else self.parent_run_id
        parent_run_id, depth, orphan = None, 0, False
        span_ctx = span.get_span_context()
        if explicit_parent:
            parent_run_id = str(explicit_parent)
            orphan = not is_known_run(parent_run_id, span_ctx.trace_id) and not _has_remote_parent(span)
        elif enclosing is not None:
            parent_run_id, depth = enclosing.agent_run_id, enclosing.depth + 1
        else:
            remote = baggage_value(Ctx.PARENT_RUN_ID)
            if remote:
                parent_run_id, depth = remote, 1
                orphan = not _has_remote_parent(span)
        run = RunState(agent_id=agent_id, parent_run_id=parent_run_id, span=span,
                       loop_threshold=cfg.loop_threshold, max_steps=cfg.max_steps, depth=depth)
        call.state["run"] = run
        register_run(run.agent_run_id, span_ctx.trace_id)

        safe_set(span, Ctx.AGENT_ID, agent_id)
        name = resolve(self.name, call) if callable(self.name) else self.name
        safe_set(span, Ctx.AGENT_NAME, str(name or agent_id))
        safe_set(span, Ctx.AGENT_VERSION, self.version or cfg.agent_version)
        safe_set(span, Ctx.FRAMEWORK, self.framework)
        safe_set(span, Ctx.AGENT_TYPE, self.agent_type)
        safe_set(span, Ctx.AGENT_RUN_ID, run.agent_run_id)
        safe_set(span, Ctx.PARENT_RUN_ID, parent_run_id)
        safe_set(span, Ctx.TRACE_ID, format(span_ctx.trace_id, "032x"))
        safe_set(span, Ctx.PROVENANCE_ID, cfg.provenance_id or None)
        safe_set(span, Ctx.WORK_UNIT_ID, cfg.work_unit_id or None)
        safe_set(span, Ctx.COMMIT_SHA, cfg.commit_sha or None)
        safe_set(span, Ctx.INPUT_HASH, call.input_hash())
        if depth:
            safe_set(span, Ctx.DELEGATION_DEPTH, depth)
        for key, value in self.attributes.items():
            safe_set(span, key, value)
        if orphan:
            safe_set(span, Ctx.ORPHAN_DETECTED, True)
            span.add_event(Ctx.ORPHAN_DETECTED, {Ctx.PARENT_RUN_ID: parent_run_id or ""})
            record(Metric.ORPHANS, {Ctx.AGENT_ID: agent_id})
            _log.warning("gernas: orphan run %s (parent_run_id=%s has no parent trace context)",
                         run.agent_run_id, parent_run_id)

    def activate(self, span: Any, call: Call) -> Any:
        run = call.state.get("run")
        return bind(run) if run is not None else None

    def deactivate(self, call: Call, token: Any) -> None:
        if token is not None:
            unbind(token)

    def success(self, span: Any, call: Call, result: Any) -> None:
        safe_set(span, Ctx.OUTPUT_HASH, hash_inputs(result))

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        run: RunState | None = call.state.get("run")
        reason = run.resolve_termination(exc) if run else "error"
        return reason in _ERRORS

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        run: RunState | None = call.state.get("run")
        latency = call.elapsed_ms()
        reason = run.resolve_termination(exc) if run else ("error" if exc else "completed")
        safe_set(span, Ctx.TERMINATION_REASON, reason)
        safe_set(span, Ctx.DURATION_MS, latency)
        if run is not None:
            safe_set(span, Ctx.RUN_STEPS, run.steps)
            safe_set(span, Ctx.RETRY_COUNT, run.retry_count)
            if run.loop_detected:
                safe_set(span, Ctx.LOOP_DETECTED, True)
                safe_set(span, Ctx.LOOP_REPEAT_COUNT, run.loop_repeat_count)
            if run.max_step_violation:
                safe_set(span, Ctx.MAX_STEP_VIOLATION, True)
        if reason == "timeout":
            safe_set(span, Ctx.TIMED_OUT, True)
        if exc is None and reason == "max_steps":
            mark_status(span, StatusCode.ERROR, "max_steps")
        agent_id = run.agent_id if run else self._agent_id(call)
        record(Metric.AGENT_CALLS, {Ctx.AGENT_ID: agent_id, Ctx.TERMINATION_REASON: reason},
               histogram=Metric.AGENT_DURATION, latency_ms=latency)


def _has_remote_parent(span: Any) -> bool:
    parent = getattr(span, "parent", None)
    return bool(parent is not None and parent.is_valid)


def trace_agent(agent_id: str | Callable[..., str] | None = None, *, name: str | Callable[..., str] | None = None,
                version: str | None = None, framework: str = "custom", agent_type: str | None = None,
                attributes: Mapping[str, Any] | None = None,
                parent_run_id: str | Callable[..., str] | None = None) -> Callable[[F], F]:
    """Trace an agent entry point as ``agent.run``.

    ``agent_id`` may be a string or ``callable(*args, **kwargs) -> str`` (e.g. to
    read ``self.name``). ``version`` defaults to ``GERNAS_AGENT_VERSION`` /
    ``GERNAS_SERVICE_VERSION``.
    """
    def decorator(fn: F) -> F:
        return wrap(fn, AgentSpec(agent_id, name, version, framework, agent_type, attributes, parent_run_id))
    return decorator


def current_agent_span() -> Any:
    run = current_run()
    return run.span if run is not None else trace.get_current_span()
