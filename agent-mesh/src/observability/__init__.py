"""Mesh observability package.

Public surface:
- ``setup_observability(service_name)`` — single startup activation point
  (framework-native OpenTelemetry + centralized logging).
- ``get_logger(category)`` — category loggers (mesh.agent, mesh.a2a, ...).
- ``metrics`` — custom business metric helpers (record_guardrail, record_a2a_call, …).
- ``baggage`` — W3C baggage helpers (set_request_baggage, get_request_id, …).
- ``tracer`` — legacy mesh event helpers (JSONL sink, off by default now that
  Agent Framework workflow/agent spans cover the same ground).
"""
from src.observability.logging_config import (
    CAT_A2A,
    CAT_AGENT,
    CAT_APPROVALS,
    CAT_MCP,
    CAT_SYSTEM,
    CAT_TOOLS,
    CAT_TRANSPORT,
    CAT_WORKFLOW,
    configure_logging,
    get_logger,
)
from src.observability.setup import flush_observability, setup_observability
from src.observability import metrics, baggage

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
    "CAT_SYSTEM",
    "flush_observability",
    "metrics",
    "baggage",
]
