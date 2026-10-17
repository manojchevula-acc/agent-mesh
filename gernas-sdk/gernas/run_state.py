"""Per-run state behind the Blueprint §5 agentic KPIs.

One :class:`RunState` per agent run (bound to a ContextVar, never shared):
steps per run, tool calls, retries (retry rate), repeated identical tool calls
(agent loop rate), ``GERNAS_MAX_STEPS`` (maximum step violation) and the final
termination reason (task success / abandonment / timeout / policy block rates).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from gernas.attributes import Ctx, Metric

TERMINATION_REASONS = ("completed", "max_steps", "timeout", "policy_block", "abandoned", "error", "human_rejected")

_current: contextvars.ContextVar[RunState | None] = contextvars.ContextVar("gernas_run", default=None)


@dataclass
class RunState:
    agent_id: str
    agent_version: str = ""
    agent_run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex}")
    parent_run_id: str | None = None
    delegation_path: str = ""
    span: Any = None
    loop_threshold: int = 3
    max_steps: int = 50
    steps: int = 0
    retry_count: int = 0
    loop_detected: bool = False
    loop_repeat_count: int = 0
    max_step_violation: bool = False
    termination_override: str | None = None
    _calls: Counter = field(default_factory=Counter, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_step(self) -> None:
        with self._lock:
            self.steps += 1
            tripped = 0 < self.max_steps < self.steps and not self.max_step_violation
            if tripped:
                self.max_step_violation = True
        if tripped:
            self._flag(Ctx.MAX_STEP_VIOLATION, True)

    def on_tool_call(self, tool_id: str, input_fingerprint: str) -> None:
        """Count a tool call; flag a loop when the same tool + input repeats ``loop_threshold`` times."""
        with self._lock:
            self._calls[(tool_id, input_fingerprint)] += 1
            count = self._calls[(tool_id, input_fingerprint)]
            newly = count >= self.loop_threshold > 0 and count > self.loop_repeat_count
            first = newly and not self.loop_detected
            if newly:
                self.loop_detected, self.loop_repeat_count = True, count
        self.record_step()
        if newly:
            self._flag(Ctx.LOOP_DETECTED, True)
            self._flag(Ctx.LOOP_REPEAT_COUNT, count)
            if first:
                _count(Metric.AGENT_LOOPS, {Ctx.AGENT_ID: self.agent_id})

    def increment_retry(self, n: int = 1) -> None:
        with self._lock:
            self.retry_count += n
        _count(Metric.AGENT_RETRIES, {Ctx.AGENT_ID: self.agent_id}, n)

    def set_termination(self, reason: str) -> None:
        if reason not in TERMINATION_REASONS:
            raise ValueError(f"termination reason must be one of {TERMINATION_REASONS}")
        self.termination_override = reason

    def resolve_termination(self, exc: BaseException | None) -> str:
        if exc is not None:
            return termination_for(exc)
        if self.termination_override:
            return self.termination_override
        return "max_steps" if self.max_step_violation else "completed"

    def _flag(self, key: str, value: Any) -> None:
        if self.span is not None:
            from gernas.tracer import safe_set

            safe_set(self.span, key, value)


def error_category(exc: BaseException) -> str:
    """Blueprint §2 outcome ``error_category`` derived from an exception."""
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return "cancelled"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        return "timeout"
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429 or "RateLimit" in name or "Throttl" in name:
        return "rate_limit"
    if (
        isinstance(exc, PermissionError)
        or status in (401, 403)
        or any(s in name for s in ("Auth", "Denied", "Forbidden"))
    ):
        return "auth"
    if any(s in name for s in ("Blocked", "Policy", "Guardrail")):
        return "policy"
    if isinstance(exc, (ValueError, TypeError, KeyError)) or "Validation" in name:
        return "validation"
    if (
        isinstance(exc, (ConnectionError, OSError))
        or (isinstance(status, int) and status >= 500)
        or any(s in name for s in ("Connection", "Unavailable", "APIError", "NotFound", "Client"))
    ):
        return "dependency"
    return "internal"


def termination_for(exc: BaseException) -> str:
    category = error_category(exc)
    return {"cancelled": "abandoned", "timeout": "timeout", "policy": "policy_block"}.get(category, "error")


def _count(metric: str, attrs: dict, n: int = 1) -> None:
    from gernas._metrics import record

    record(metric, attrs, n=n)


def current_run() -> RunState | None:
    return _current.get()


def bind(run: RunState) -> contextvars.Token:
    return _current.set(run)


def unbind(token: contextvars.Token) -> None:
    try:
        _current.reset(token)
    except ValueError:  # token from another context (e.g. a generator finalised elsewhere)
        _current.set(None)


def record_retry(n: int = 1) -> None:
    """Count a retry against the active run (no-op outside a run)."""
    run = _current.get()
    if run is not None:
        run.increment_retry(n)


def set_termination_reason(reason: str) -> None:
    """Override the termination reason of the active run (e.g. ``policy_block``)."""
    run = _current.get()
    if run is not None:
        run.set_termination(reason)
