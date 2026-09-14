"""Standalone compliance layer.

Provides chain-hashed, HMAC-signed audit trail, guardrail events, and RBAC
enforcement independent of any observability framework.
"""
from .chain import compute_chain_hash, sign_event, verify_chain
from .events import (
    ComplianceEvent,
    GuardrailEvent,
    RBACEvent,
    emit_compliance_event,
    emit_guardrail_event,
    emit_rbac_event,
)
from .decorators import (
    compliance_gate,
    guardrail_check,
    rbac_check,
    set_default_compliance_config,
    GuardrailBlockedError,
    RBACDeniedError,
)
from .hashing import hash_inputs, summarize_output
from .sanitizer import scrub_pii
from .config import ComplianceConfig

__all__ = [
    "compute_chain_hash", "sign_event", "verify_chain",
    "ComplianceEvent", "GuardrailEvent", "RBACEvent",
    "emit_compliance_event", "emit_guardrail_event", "emit_rbac_event",
    "compliance_gate", "guardrail_check", "rbac_check",
    "set_default_compliance_config",
    "GuardrailBlockedError", "RBACDeniedError",
    "hash_inputs", "summarize_output", "scrub_pii",
    "ComplianceConfig",
]
