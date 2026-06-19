"""Application-level custom metrics for the agent mesh.

All instruments are created lazily from the global MeterProvider, which is
registered by ``setup_observability()`` before any request is processed.
Calling these helpers before setup (or with OTel disabled) produces no-ops —
the OTel API's NoOpMeter silently absorbs every call.

Design constraints
------------------
- Never raises: every public function is wrapped in ``try/except Exception: pass``.
- Never duplicates framework metrics: ``gen_ai.*`` and ``agent_framework.*`` are
  already emitted by the SDK; we only add mesh-specific application counters.
- Import-safe: instruments are created on first use, not at import time,
  so importing this module before ``setup_observability()`` is safe.

Prometheus metric names in Grafana Mimir (dots → underscores, meter-name prefix):
  agent_mesh_mesh_request_total
  agent_mesh_mesh_request_duration_ms_bucket  / _sum / _count
  agent_mesh_mesh_request_blocked_total
  agent_mesh_mesh_guardrail_block_total
  agent_mesh_mesh_access_denied_total
  agent_mesh_mesh_compliance_fail_total
  agent_mesh_mesh_payment_gate_total
  agent_mesh_mesh_a2a_duration_ms_bucket  / _sum / _count
  agent_mesh_mesh_a2a_error_total
  agent_mesh_mesh_router_domains_total
  agent_mesh_mesh_router_fallback_total
  agent_mesh_mesh_memory_messages_bucket / _sum / _count
  agent_mesh_mesh_memory_context_chars_bucket / _sum / _count
  agent_mesh_mesh_approval_wait_ms_bucket / _sum / _count
  agent_mesh_mesh_token_usage_total
  agent_mesh_mesh_cost_estimated_usd_bucket / _sum / _count
  agent_mesh_mesh_hallucination_suspected_total
  agent_mesh_mesh_tool_access_total
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Lazy instrument cache — populated on first record_*() call.
# ---------------------------------------------------------------------------

_meter = None
_instruments: dict = {}


def _get_meter():
    """Returns the global meter, acquiring it once after the MeterProvider is set."""
    global _meter
    if _meter is None:
        from opentelemetry import metrics as _metrics
        _meter = _metrics.get_meter("agent_mesh", version="1.0.0")
    return _meter


def _counter(name: str, unit: str, description: str):
    if name not in _instruments:
        _instruments[name] = _get_meter().create_counter(
            name=name, unit=unit, description=description
        )
    return _instruments[name]


def _histogram(name: str, unit: str, description: str):
    if name not in _instruments:
        _instruments[name] = _get_meter().create_histogram(
            name=name, unit=unit, description=description
        )
    return _instruments[name]


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------

def record_request(
    *,
    domain: str,
    role: str,
    status: str,
    duration_ms: float,
    block_stage: Optional[str] = None,
) -> None:
    """Records one completed mesh request: counter + latency histogram.

    Call from ``orchestrator.handle_request()`` in a ``finally`` block so it
    fires on both successful and blocked/error paths.

    Args:
        domain:      Primary domain resolved by the gateway (e.g. ``"hr"``).
                     Use ``"unknown"`` when routing did not complete.
        role:        User role (e.g. ``"employee"``, ``"leadership"``).
        status:      ``"success"``, ``"blocked"``, or ``"error"``.
        duration_ms: Wall-clock duration of ``handle_request()`` in milliseconds.
        block_stage: When ``status=="blocked"``, the stage that blocked the
                     request (e.g. ``"input_guardrail"``). ``None`` otherwise.
    """
    try:
        attrs = {"domain": domain, "role": role, "status": status}
        _counter(
            "mesh.request", "{request}",
            "Total mesh requests by domain, role and outcome",
        ).add(1, attrs)
        _histogram(
            "mesh.request.duration", "ms",
            "End-to-end mesh request duration in milliseconds",
        ).record(duration_ms, attrs)
        if status == "blocked" and block_stage:
            _counter(
                "mesh.request.blocked", "{request}",
                "Security-blocked mesh requests by stage and role",
            ).add(1, {"stage": block_stage, "role": role})
    except Exception:
        pass


def record_guardrail(
    *,
    categories: list[str],
    role: str,
) -> None:
    """Records an input guardrail block — one increment per violated category.

    Call from ``InputGuardrailExecutor`` when ``screen.allowed`` is ``False``.

    Args:
        categories: Violation categories from ``screen_input()``
                    (e.g. ``["prompt_injection", "pii"]``).
        role:       User role at the time of the block.
    """
    try:
        ctr = _counter(
            "mesh.guardrail.block", "{block}",
            "Input guardrail blocks by violation category and role",
        )
        for cat in categories:
            ctr.add(1, {"category": cat, "role": role})
    except Exception:
        pass


def record_access_denied(
    *,
    domain: str,
    role: str,
) -> None:
    """Records one RBAC denial for a (domain, role) pair.

    Call from ``AccessControlExecutor`` for each denied domain.

    Args:
        domain: The domain the user was denied access to.
        role:   The user's role that caused the denial.
    """
    try:
        _counter(
            "mesh.access_denied", "{denial}",
            "RBAC access denials by domain and role",
        ).add(1, {"domain": domain, "role": role})
    except Exception:
        pass


def record_compliance(
    *,
    domain: str,
) -> None:
    """Records one compliance gate failure.

    Call from ``ComplianceExecutor`` when ``"compliance_failed"`` appears in
    the verdict string.

    Args:
        domain: Primary domain of the request that failed compliance review.
    """
    try:
        _counter(
            "mesh.compliance.fail", "{failure}",
            "Compliance gate failures by domain",
        ).add(1, {"domain": domain or "unknown"})
    except Exception:
        pass


def record_payment_gate(
    *,
    approved: bool,
) -> None:
    """Records one payment gate decision (approved or denied).

    Call from ``PaymentApprovalExecutor`` when it makes a payment decision.

    Args:
        approved: ``True`` if the operator approved the outbound payment.
    """
    try:
        _counter(
            "mesh.payment_gate", "{decision}",
            "Payment gate decisions (approved vs denied)",
        ).add(1, {"outcome": "approved" if approved else "denied"})
    except Exception:
        pass


def record_a2a_call(
    *,
    node: str,
    duration_ms: float,
    status: str,
) -> None:
    """Records one A2A call: latency histogram + error counter.

    Call from ``ask_remote()``'s ``finally`` block where ``duration_ms`` is
    already computed.

    Args:
        node:        Target agent node name (e.g. ``"gateway"``, ``"compliance"``).
        duration_ms: Duration of the A2A call in milliseconds.
        status:      ``"success"`` or ``"error"``.
    """
    try:
        attrs = {"node": node, "status": status}
        _histogram(
            "mesh.a2a.duration", "ms",
            "A2A call duration in milliseconds by target node and status",
        ).record(duration_ms, attrs)
        if status == "error":
            _counter(
                "mesh.a2a.error", "{error}",
                "A2A transport errors by target node",
            ).add(1, {"node": node})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Decision Parameter metrics
# ---------------------------------------------------------------------------

def record_routing(
    *,
    domains_count: int,
    fallback_used: bool,
    role: str,
) -> None:
    """Records Gateway routing result — domain decomposition metrics.

    Covers the 'Planner Agent — reasoning logic, no. of plan steps' Decision Parameter.

    Args:
        domains_count: Number of domains resolved (1 = single-domain, 2+ = multi-domain).
        fallback_used: True when the keyword-based fallback resolved the domain (LLM gave no valid token).
        role:          User role at routing time.
    """
    try:
        _histogram(
            "mesh.router.domains", "{domain}",
            "Number of domains resolved per request by the Gateway router",
        ).record(domains_count, {"role": role})
        if fallback_used:
            _counter(
                "mesh.router.fallback", "{fallback}",
                "Requests where Gateway keyword fallback resolved the domain (LLM output was unparseable)",
            ).add(1, {"role": role})
    except Exception:
        pass


def record_memory_usage(
    *,
    messages_count: int,
    context_chars: int,
) -> None:
    """Records memory / context-window usage after each message append.

    Covers the 'Memory — context-window usage, memory recall accuracy' Decision Parameter.

    Args:
        messages_count: Total messages in the session history (proxy for context depth).
        context_chars:  Total characters across all session messages (proxy for token usage).
    """
    try:
        _histogram(
            "mesh.memory.messages", "{message}",
            "Session message count at each append (context depth proxy)",
        ).record(messages_count)
        _histogram(
            "mesh.memory.context_chars", "char",
            "Total session context characters at each append (token-count proxy)",
        ).record(context_chars)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Performance Parameter metrics
# ---------------------------------------------------------------------------

def record_approval_wait(
    *,
    wait_ms: float,
    approved: bool,
) -> None:
    """Records how long a human approver took to respond to a payment gate prompt.

    Covers the 'Human-in-Loop — approval wait time' Performance Parameter.

    Args:
        wait_ms:  Wall-clock duration from prompt display to decision in milliseconds.
        approved: Whether the payment was approved (used to slice wait time by outcome).
    """
    try:
        _histogram(
            "mesh.approval.wait_ms", "ms",
            "Time from payment approval prompt to human decision in milliseconds",
        ).record(wait_ms, {"outcome": "approved" if approved else "denied"})
    except Exception:
        pass


def record_token_usage(
    *,
    call_type: str,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    model: str,
) -> None:
    """Records estimated token consumption broken down by call type.

    Covers the 'FinOps — token usage (A2A & Tool2Tool), model used' Performance Parameter.
    Token counts are estimated as len(text) // 4 (standard ~4 chars per token heuristic)
    when the SDK does not expose raw counts directly.

    Args:
        call_type:               One of ``"a2a"``, ``"tool"``, ``"direct"`` — distinguishes
                                 A2A inter-agent calls from Tool2Tool (tool calls within agent).
        estimated_input_tokens:  Estimated prompt/input token count.
        estimated_output_tokens: Estimated completion/output token count.
        model:                   Model identifier (e.g. ``"llama3.2"``).
    """
    try:
        ctr = _counter(
            "mesh.token.usage", "{token}",
            "Estimated token consumption by call type, direction and model",
        )
        ctr.add(estimated_input_tokens,  {"call_type": call_type, "direction": "input",  "model": model})
        ctr.add(estimated_output_tokens, {"call_type": call_type, "direction": "output", "model": model})
    except Exception:
        pass


def record_cost(
    *,
    estimated_usd: float,
    domain: str,
    call_type: str,
) -> None:
    """Records estimated per-request serving cost in USD.

    Covers the 'Cost — serving endpoint & data-storage cost, cost per request' Performance Parameter.

    Args:
        estimated_usd: Computed from token counts × pricing table in ``Config``.
        domain:        Primary domain of the request.
        call_type:     ``"a2a"``, ``"tool"``, or ``"direct"``.
    """
    try:
        _histogram(
            "mesh.cost.estimated_usd", "usd",
            "Estimated per-request LLM serving cost in USD",
        ).record(estimated_usd, {"domain": domain or "unknown", "call_type": call_type})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Compliance / Safety metrics
# ---------------------------------------------------------------------------

def record_hallucination_suspected(
    *,
    domain: str,
    indicator: str,
) -> None:
    """Records when a response heuristic suspects hallucination in output.

    Covers the 'Safety — hallucination checks' Compliance Parameter.

    Args:
        domain:    Domain that produced the suspicious output.
        indicator: Short label for the matched heuristic (e.g. ``"speculation_phrase"``).
    """
    try:
        _counter(
            "mesh.hallucination.suspected", "{event}",
            "Responses flagged by heuristic hallucination detector by domain and indicator",
        ).add(1, {"domain": domain or "unknown", "indicator": indicator})
    except Exception:
        pass


def record_tool_access(
    *,
    tool_name: str,
    role: str,
    allowed: bool,
) -> None:
    """Records a tool-level authorization decision.

    Covers the 'Tools — tool access scope, authorization' Compliance Parameter.

    Args:
        tool_name: Name of the tool being invoked (e.g. ``"issue_payment"``).
        role:      User role requesting the tool.
        allowed:   Whether the tool call is authorized under policy.
    """
    try:
        _counter(
            "mesh.tool_access", "{access}",
            "Tool-level authorization decisions (allowed vs denied) by tool and role",
        ).add(1, {"tool": tool_name, "role": role, "outcome": "allowed" if allowed else "denied"})
    except Exception:
        pass
