"""@trace_guardrail and @trace_content_safety (WP1-CORE-02d steps 5-6).

A deny/block is expected behaviour: the span status stays OK and the decision
is an attribute. A guardrail that raises ``GuardrailBlockedError`` (or any
``*Blocked*`` error) is recorded as ``deny`` without an ERROR status, and the
active run's ``termination_reason`` becomes ``policy_block``.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, get_field, resolve, wrap
from gernas.metrics.agentic import record
from gernas.run_state import current_run
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])

_DECISIONS = ("allow", "deny", "warn", "escalate")


def _is_block_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    return "Blocked" in name or "PolicyViolation" in name or name == "RBACDeniedError"


def infer_decision(result: Any) -> tuple[str, str | None]:
    """Return ``(decision, reason_category)`` from common guardrail return shapes.

    * ``("allow"|"deny"|"warn"|"escalate", category)`` tuples
    * ``(triggered: bool, reason)`` tuples (Beacon Kit / ``guardrail_check``)
    * ``bool`` — True means the guardrail *triggered* (deny)
    * dicts / objects with ``decision``, ``allowed``, ``blocked`` or ``triggered``
    """
    if isinstance(result, tuple) and result and isinstance(result[0], str) and result[0].lower() in _DECISIONS:
        return result[0].lower(), (result[1] if len(result) > 1 and isinstance(result[1], str) else None)
    if isinstance(result, tuple) and result and isinstance(result[0], bool):
        reason = result[1] if len(result) > 1 and isinstance(result[1], str) else None
        return ("deny" if result[0] else "allow"), reason
    if isinstance(result, bool):
        return ("deny" if result else "allow"), None
    decision = get_field(result, "decision", "policy_decision")
    category = get_field(result, "category", "risk_classification", "reason_code")
    if isinstance(decision, str) and decision.lower() in _DECISIONS:
        return decision.lower(), category
    allowed = get_field(result, "allowed", "passed", "is_safe")
    if isinstance(allowed, bool):
        return ("allow" if allowed else "deny"), category
    blocked = get_field(result, "blocked", "triggered", "is_blocked")
    if isinstance(blocked, bool):
        return ("deny" if blocked else "allow"), category
    return "allow", category


class GuardrailSpec(Spec):
    op = SpanName.GUARDRAIL

    def __init__(self, policy_id: str, risk_classification: str | Callable[[Any], str] | None,
                 decision_from: Callable[[Any], Any] | None, blocks_run: bool) -> None:
        self.policy_id, self.risk_classification = policy_id, risk_classification
        self.decision_from, self.blocks_run = decision_from, blocks_run

    def start(self, span: Any, call: Call) -> None:
        safe_set(span, Ctx.POLICY_ID, self.policy_id)
        if isinstance(self.risk_classification, str):
            safe_set(span, Ctx.RISK_CLASSIFICATION, self.risk_classification)

    def _decide(self, span: Any, call: Call, decision: str, category: Any) -> None:
        call.state["decision"] = decision
        safe_set(span, Ctx.POLICY_DECISION, decision)
        if callable(self.risk_classification):
            category = resolve(self.risk_classification, call, category) or category
        if isinstance(category, str) and category and not isinstance(self.risk_classification, str):
            safe_set(span, Ctx.RISK_CLASSIFICATION, category[:64])
        if decision == "deny" and self.blocks_run:
            run = current_run()
            if run is not None:
                run.set_termination("policy_block")

    def success(self, span: Any, call: Call, result: Any) -> None:
        if self.decision_from is not None:
            raw = resolve(self.decision_from, call, result)
            decision, category = (raw.lower(), None) if isinstance(raw, str) else infer_decision(raw)
        else:
            decision, category = infer_decision(result)
        self._decide(span, call, decision if decision in _DECISIONS else "allow", category)

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        if _is_block_error(exc):
            self._decide(span, call, "deny", getattr(exc, "rule_id", None))
            return False
        return super().failure(span, call, exc)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        if "decision" not in call.state:
            safe_set(span, Ctx.POLICY_DECISION, "allow" if exc is None else "escalate")
        record(Metric.GUARDRAIL_DECISIONS, {Ctx.POLICY_ID: self.policy_id,
                                            Ctx.POLICY_DECISION: call.state.get("decision", "allow")})


def trace_guardrail(policy_id: str, *, risk_classification: str | Callable[[Any], str] | None = None,
                    decision_from: Callable[[Any], Any] | None = None, blocks_run: bool = True) -> Callable[[F], F]:
    """Trace a policy / guardrail evaluation as ``guardrail.check``.

    The decision is inferred from the return value (see :func:`infer_decision`)
    or computed by ``decision_from(result)``.
    """
    def decorator(fn: F) -> F:
        return wrap(fn, GuardrailSpec(policy_id, risk_classification, decision_from, blocks_run))
    return decorator


class ContentSafetySpec(Spec):
    op = SpanName.CONTENT_SAFETY

    def __init__(self, category: str | None, policy: str | None, risk_level: str | None,
                 result_from: Callable[[Any], Any] | None) -> None:
        self.category, self.policy, self.risk_level, self.result_from = category, policy, risk_level, result_from

    def start(self, span: Any, call: Call) -> None:
        safe_set(span, Ctx.SAFETY_CATEGORY, self.category or "general")
        safe_set(span, Ctx.POLICY_TRIGGERED, None)

    def success(self, span: Any, call: Call, result: Any) -> None:
        raw = resolve(self.result_from, call, result) if self.result_from else result
        if isinstance(raw, tuple) and raw and isinstance(raw[0], bool):
            blocked = raw[0]
            category = raw[1] if len(raw) > 1 and isinstance(raw[1], str) else None
            level = raw[2] if len(raw) > 2 and isinstance(raw[2], str) else None
        elif isinstance(raw, bool):
            blocked, category, level = raw, None, None
        else:
            blocked = get_field(raw, "blocked", "content_blocked", "flagged", "redacted")
            category = get_field(raw, "category", "safety_category")
            level = get_field(raw, "risk_level", "severity")
            policy = get_field(raw, "policy", "policy_triggered")
            if isinstance(policy, str):
                safe_set(span, Ctx.POLICY_TRIGGERED, policy)
        blocked = bool(blocked)
        call.state["blocked"] = blocked
        safe_set(span, Ctx.CONTENT_BLOCKED, blocked)
        if isinstance(category, str) and category:
            safe_set(span, Ctx.SAFETY_CATEGORY, category)
        level = level or self.risk_level or ("high" if blocked else "safe")
        safe_set(span, Ctx.RISK_LEVEL, str(level))
        if blocked and self.policy:
            safe_set(span, Ctx.POLICY_TRIGGERED, self.policy)

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        if _is_block_error(exc):
            call.state["blocked"] = True
            safe_set(span, Ctx.CONTENT_BLOCKED, True)
            safe_set(span, Ctx.POLICY_TRIGGERED, self.policy)
            return False
        return super().failure(span, call, exc)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        if "blocked" not in call.state:
            safe_set(span, Ctx.CONTENT_BLOCKED, False)
        record(Metric.GUARDRAIL_DECISIONS, {Ctx.POLICY_ID: self.policy or self.category or "content_safety",
                                            Ctx.POLICY_DECISION: "deny" if call.state.get("blocked") else "allow"})


def trace_content_safety(category: str | None = None, *, policy: str | None = None, risk_level: str | None = None,
                         result_from: Callable[[Any], Any] | None = None) -> Callable[[F], F]:
    """Trace a content-safety check as ``content_safety.check``; blocks set ``content_blocked``."""
    def decorator(fn: F) -> F:
        return wrap(fn, ContentSafetySpec(category, policy, risk_level, result_from))
    return decorator
