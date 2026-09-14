"""Compliance and guardrail event dataclasses and emitters.

Written synchronously within the business logic transaction so a backend
failure cannot silently drop compliance records.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .chain import compute_chain_hash, sign_event
from .hashing import hash_inputs

try:
    from opentelemetry import trace as _otel_trace

    def _get_trace_id() -> str:
        span = _otel_trace.get_current_span()
        tid = span.get_span_context().trace_id
        return format(tid, "032x") if tid else "0" * 32

    def _get_span_id() -> str:
        span = _otel_trace.get_current_span()
        sid = span.get_span_context().span_id
        return format(sid, "016x") if sid else "0" * 16

except ImportError:
    def _get_trace_id() -> str:
        return "0" * 32

    def _get_span_id() -> str:
        return "0" * 16


@dataclass
class ComplianceEvent:
    event_type: Literal[
        "pricing_query_started",
        "pricing_query_completed",
        "hitl_approval_requested",
        "hitl_approval_decision",
        "guardrail_triggered",
        "model_output_reviewed",
        "audit_trail_checkpoint",
        "compliance_violation_detected",
    ]
    run_id: str
    actor: str
    outcome: str
    payload_hash: str = ""
    chain_hash: str = ""
    signature: str = ""
    trace_id: str = field(default_factory=_get_trace_id)
    span_id: str = field(default_factory=_get_span_id)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardrailEvent:
    rule_id: str
    triggered: bool
    action: Literal["allow", "block", "warn", "escalate"]
    reason_hash: str
    run_id: str = ""
    trace_id: str = field(default_factory=_get_trace_id)
    span_id: str = field(default_factory=_get_span_id)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RBACEvent:
    resource: str
    required_role: str
    actual_role: str
    outcome: str  # "ALLOW" or "DENY"
    trace_id: str = field(default_factory=_get_trace_id)
    span_id: str = field(default_factory=_get_span_id)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


_chain_lock = threading.Lock()
_previous_hash: str = "0" * 64


def emit_compliance_event(event: ComplianceEvent, config: Any) -> ComplianceEvent:
    """Sign, chain-hash, and persist a compliance event."""
    global _previous_hash
    event_dict = asdict(event)
    signable = {k: v for k, v in event_dict.items() if k not in ("chain_hash", "signature")}
    with _chain_lock:
        event.chain_hash = compute_chain_hash(signable, _previous_hash)
        event.signature = sign_event(signable, config.compliance_hmac_key)
        _previous_hash = event.chain_hash
    _write_to_store(asdict(event), config.compliance_store)
    return event


def emit_guardrail_event(event: GuardrailEvent, config: Any) -> None:
    """Persist a guardrail event to the compliance store."""
    _write_to_store(asdict(event), config.compliance_store)


def emit_rbac_event(event: RBACEvent, config: Any) -> None:
    """Persist an RBAC access-control event to the compliance store."""
    _write_to_store(asdict(event), config.compliance_store)


def _write_to_store(record: dict, store_path: str) -> None:
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
