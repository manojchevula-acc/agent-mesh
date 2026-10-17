"""Mesh observability package.

Public surface:
- ``gernas_telemetry`` — the single telemetry integration point (Gernas SDK).
- ``get_logger(category)`` — category loggers (mesh.agent, mesh.a2a, ...).
- ``baggage`` — application request context (set_request_baggage, get_request_id, …).
"""
from src.observability.logging_config import (
    CAT_A2A,
    CAT_AGENT,
    CAT_APPROVALS,
    CAT_MCP,
    CAT_STATE,
    CAT_SYSTEM,
    CAT_TOOLS,
    CAT_TRANSPORT,
    CAT_WORKFLOW,
    configure_logging,
    get_logger,
)
from src.observability import baggage

__all__ = [
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
    "CAT_STATE",
    "baggage",
]
