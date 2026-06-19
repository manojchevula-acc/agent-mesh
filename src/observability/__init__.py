"""Mesh observability package.

Public surface:
- ``setup_observability(service_name)`` — single startup activation point
  (framework-native OpenTelemetry + centralized logging).
- ``get_logger(category)`` — category loggers (mesh.agent, mesh.a2a, ...).
- ``tracer`` — legacy mesh event helpers (JSONL sink, off by default now that
  Agent Framework workflow/agent spans cover the same ground).
"""
from src.observability.logging_config import (
    CAT_A2A,
    CAT_AGENT,
    CAT_APPROVALS,
    CAT_MCP,
    CAT_SECURITY,
    CAT_SYSTEM,
    CAT_TOOLS,
    CAT_TRANSPORT,
    CAT_WORKFLOW,
    configure_logging,
    get_logger,
)
from src.observability.setup import setup_observability
from src.observability.metrics import (
    record_request,
    record_guardrail,
    record_access_denied,
    record_compliance,
    record_payment_gate,
    record_a2a_call,
)

__all__ = [
    "setup_observability",
    "configure_logging",
    "get_logger",
    "CAT_AGENT",
    "CAT_WORKFLOW",
    "CAT_TOOLS",
    "CAT_A2A",
    "CAT_MCP",
    "CAT_TRANSPORT",
    "CAT_APPROVALS",
    "CAT_SECURITY",
    "CAT_SYSTEM",
    "record_request",
    "record_guardrail",
    "record_access_denied",
    "record_compliance",
    "record_payment_gate",
    "record_a2a_call",
]
