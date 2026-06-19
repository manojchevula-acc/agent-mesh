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
