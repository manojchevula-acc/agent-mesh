"""Compliance and guardrail event dataclasses and emitters.

Compliance events are NOT derived from OTEL spans — they are written synchronously
within the business logic transaction so a Collector failure cannot silently drop them.
Only trace_id and span_id from the active span context appear here.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from gernas.compliance.chain import compute_chain_hash, sign_event
from gernas.propagation.context import get_current_trace_id, get_current_span_id
from gernas.utils.hashing import hash_inputs

if TYPE_CHECKING:
    from gernas.config import GernasConfig


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
    payload_hash: str = ""       # SHA-256 of the business payload — never the payload itself
    chain_hash: str = ""
    signature: str = ""
    trace_id: str = field(default_factory=get_current_trace_id)
    span_id: str = field(default_factory=get_current_span_id)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardrailEvent:
    rule_id: str
    triggered: bool
    action: Literal["allow", "block", "warn", "escalate"]
    reason_hash: str           # SHA-256 of the guardrail reason — never the reason text itself
    run_id: str = ""
    trace_id: str = field(default_factory=get_current_trace_id)
    span_id: str = field(default_factory=get_current_span_id)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Thread-safe in-memory previous hash state for chain continuity
_chain_lock = threading.Lock()
_previous_hash: str = "0" * 64


def emit_compliance_event(event: ComplianceEvent, config: "GernasConfig") -> ComplianceEvent:
    """Sign, chain-hash, and persist a compliance event.

    In development: appends to a JSONL file at ``config.compliance_store``.
    In production: replace the file write with an Event Hub SDK call.
    """
    global _previous_hash

    event_dict = asdict(event)
    # Exclude chain fields from the hash computation
    signable = {k: v for k, v in event_dict.items() if k not in ("chain_hash", "signature")}

    with _chain_lock:
        event.chain_hash = compute_chain_hash(signable, _previous_hash)
        event.signature = sign_event(signable, config.compliance_hmac_key)
        _previous_hash = event.chain_hash

    _write_to_store(asdict(event), config.compliance_store)
    return event


def emit_guardrail_event(event: GuardrailEvent, config: "GernasConfig") -> None:
    """Persist a guardrail event to the compliance store (no chain hashing — high-volume)."""
    _write_to_store(asdict(event), config.compliance_store)


@dataclass
class RBACEvent:
    resource: str
    required_role: str
    actual_role: str
    outcome: str                 # "ALLOW" or "DENY"
    trace_id: str = field(default_factory=get_current_trace_id)
    span_id: str = field(default_factory=get_current_span_id)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def emit_rbac_event(event: RBACEvent, config: "GernasConfig") -> None:
    """Persist an RBAC access-control event to the compliance store."""
    _write_to_store(asdict(event), config.compliance_store)


def _write_to_store(record: dict, store_path: str) -> None:
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
