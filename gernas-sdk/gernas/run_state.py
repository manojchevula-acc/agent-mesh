"""Per-run behavioural state: loops, runaway steps, retries, timeouts (WP1-CORE-03).

A :class:`RunState` is created by every ``@trace_agent`` invocation and bound to a
ContextVar for the life of the run — never shared, never global. Tool, MCP and
LLM decorators report into the active run so the agent span carries
``loop_detected``, ``max_step_violation``, ``retry_count`` and the final
``termination_reason`` without agent code doing anything.
"""
from __future__ import annotations

import contextvars
import threading
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any

from gernas.attributes import Ctx, Metric

TERMINATION_REASONS = ("completed", "max_steps", "timeout", "policy_block", "abandoned", "error", "human_rejected")

_current: contextvars.ContextVar["RunState | None"] = contextvars.ContextVar("gernas_run", default=None)


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


@dataclass
class RunState:
    agent_id: str
    agent_run_id: str = field(default_factory=new_run_id)
    parent_run_id: str | None = None
    span: Any = None
    loop_threshold: int = 3
    max_steps: int = 50
    depth: int = 0
    steps: int = 0
    tool_calls: Counter = field(default_factory=Counter)
    retry_count: int = 0
    timed_out: bool = False
    loop_detected: bool = False
    loop_repeat_count: int = 0
    max_step_violation: bool = False
    termination_override: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── signals reported by child decorators ─────────────────────────────────
    def record_step(self) -> None:
        with self._lock:
            self.steps += 1
            tripped = self.max_steps > 0 and self.steps > self.max_steps and not self.max_step_violation
            if tripped:
                self.max_step_violation = True
        if tripped:
            self._flag(Ctx.MAX_STEP_VIOLATION, True)
            _count(Metric.MAX_STEP_VIOLATIONS, {Ctx.AGENT_ID: self.agent_id})

    def on_tool_start(self, tool_id: str, args_hash: str) -> None:
        """Count (tool, args) pairs; flag a loop once the threshold is reached."""
        with self._lock:
            self.tool_calls[(tool_id, args_hash)] += 1
            count = self.tool_calls[(tool_id, args_hash)]
            newly = count >= self.loop_threshold > 0 and count > self.loop_repeat_count
            if newly:
                first = not self.loop_detected
                self.loop_detected = True
                self.loop_repeat_count = count
        self.record_step()
        if newly:
            self._flag(Ctx.LOOP_DETECTED, True)
            self._flag(Ctx.LOOP_REPEAT_COUNT, count)
            if first:
                _count(Metric.LOOP_DETECTIONS, {Ctx.AGENT_ID: self.agent_id})

    def increment_retry(self, n: int = 1) -> None:
        with self._lock:
            self.retry_count += n
        _count(Metric.RETRIES, {Ctx.AGENT_ID: self.agent_id}, n)

    def set_termination(self, reason: str) -> None:
        if reason not in TERMINATION_REASONS:
            raise ValueError(f"termination reason must be one of {TERMINATION_REASONS}")
        self.termination_override = reason

    # ── resolution at run end ────────────────────────────────────────────────
    def resolve_termination(self, exc: BaseException | None) -> str:
        if exc is not None:
            return classify_exception(exc)
        if self.termination_override:
            return self.termination_override
        if self.max_step_violation:
            return "max_steps"
        return "completed"

    def _flag(self, key: str, value: Any) -> None:
        if self.span is not None:
            from gernas.tracer import safe_set
            safe_set(self.span, key, value)


def classify_exception(exc: BaseException) -> str:
    import asyncio
    import concurrent.futures

    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "abandoned"
    name = type(exc).__name__
    if name in ("GuardrailBlockedError", "RBACDeniedError", "PolicyBlockedError"):
        return "policy_block"
    if name in ("HumanRejectedError",):
        return "human_rejected"
    return "error"


def _count(metric: str, attrs: dict, n: int = 1) -> None:
    try:
        from gernas.metrics.agentic import instruments
        instruments().counter(metric).add(n, attrs)
    except Exception:  # noqa: BLE001
        pass


# ── public helpers for agent code ────────────────────────────────────────────

def current_run() -> RunState | None:
    return _current.get()


def bind(run: RunState) -> contextvars.Token:
    return _current.set(run)


def unbind(token: contextvars.Token) -> None:
    try:
        _current.reset(token)
    except ValueError:  # reset from a different context (generator finalised elsewhere)
        _current.set(None)


def record_retry(n: int = 1) -> None:
    """Count a retry against the active run (no-op outside a run)."""
    run = _current.get()
    if run is not None:
        run.increment_retry(n)


def record_step() -> None:
    run = _current.get()
    if run is not None:
        run.record_step()


def set_termination_reason(reason: str) -> None:
    """Override the termination reason of the active run (e.g. ``policy_block``)."""
    run = _current.get()
    if run is not None:
        run.set_termination(reason)


# ── run registry for orphan detection (WP1-CTX-03 step 4) ─────────────────────

_known: "OrderedDict[str, int]" = OrderedDict()
_known_lock = threading.Lock()
_MAX_KNOWN = 10_000


def register_run(run_id: str, trace_id: int) -> None:
    with _known_lock:
        _known[run_id] = trace_id
        _known.move_to_end(run_id)
        while len(_known) > _MAX_KNOWN:
            _known.popitem(last=False)


def is_known_run(run_id: str, trace_id: int | None = None) -> bool:
    with _known_lock:
        known = _known.get(run_id)
    return known is not None and (trace_id is None or known == trace_id)
