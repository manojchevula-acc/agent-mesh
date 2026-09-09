"""beacon_kit — drop-in OpenTelemetry sidecar for agentic systems.

Three-layer observability kit:
  Layer 1 — Infrastructure: providers, exporters, config, middleware
  Layer 2 — Agentic Interactions: @traced_agent, @traced_llm_call, @traced_a2a_hop, @traced_mcp_tool
  Layer 3 — Compliance: @compliance_gate, @guardrail_check, @rbac_check

Typical minimal setup:

    from beacon_kit import setup_telemetry, TelemetryConfig, traced_agent, traced_tool
    config = TelemetryConfig(service_name="my-service", exporter_type="pretty")
    handle = setup_telemetry(config)

    @traced_agent("triage")
    def run_triage(query: str): ...
"""
# ── Layer 1: Core Infrastructure ────────────────────────────────────────────
from beacon_kit.core.config import TelemetryConfig
from beacon_kit.core.provider import TelemetryHandle, setup_telemetry, flush_telemetry
from beacon_kit.core.file_exporters import FileSpanExporter, FileMetricExporter, FileLogExporter
from beacon_kit.core.html_reporter import generate_html_report

# ── Layer 2: Agentic Interaction Decorators ──────────────────────────────────
from beacon_kit.decorators.trace import traced, traced_node, traced_pipeline
from beacon_kit.decorators.tool import traced_tool
from beacon_kit.decorators.agent import traced_agent, traced_llm_call, traced_a2a_hop, traced_mcp_tool
from beacon_kit.decorators.metrics import track_latency, count_calls
from beacon_kit.decorators.log import log_calls
from beacon_kit.metrics.agentic import AgenticMetrics

# ── Layer 3: Compliance Decorators ───────────────────────────────────────────
from beacon_kit.compliance.decorators import (
    compliance_gate,
    guardrail_check,
    rbac_check,
    GuardrailBlockedError,
    RBACDeniedError,
    set_default_compliance_config,
)
from beacon_kit.compliance.events import (
    ComplianceEvent,
    GuardrailEvent,
    RBACEvent,
    emit_compliance_event,
    emit_guardrail_event,
    emit_rbac_event,
)

# ── Propagation helpers ───────────────────────────────────────────────────────
from beacon_kit.propagation.context import (
    get_current_trace_id,
    get_current_span_id,
    serialize_context,
    restore_context,
)
from beacon_kit.propagation.baggage import inject_baggage, extract_baggage, set_baggage_on_current_context

# ── Logging ───────────────────────────────────────────────────────────────────
from beacon_kit.logconfig.request_logging import TraceContextFilter, setup_request_logging

# ── Utils ─────────────────────────────────────────────────────────────────────
from beacon_kit.utils.hashing import hash_inputs, summarize_output
from beacon_kit.utils.sanitizer import scrub_pii

__all__ = [
    # Layer 1 — Infrastructure
    "TelemetryConfig",
    "TelemetryHandle",
    "setup_telemetry",
    "flush_telemetry",
    "FileSpanExporter",
    "FileMetricExporter",
    "FileLogExporter",
    "generate_html_report",
    # Layer 2 — Agentic Interactions
    "traced",
    "traced_node",
    "traced_pipeline",
    "traced_tool",
    "traced_agent",
    "traced_llm_call",
    "traced_a2a_hop",
    "traced_mcp_tool",
    "track_latency",
    "count_calls",
    "log_calls",
    "AgenticMetrics",
    # Layer 3 — Compliance
    "compliance_gate",
    "guardrail_check",
    "rbac_check",
    "GuardrailBlockedError",
    "RBACDeniedError",
    "set_default_compliance_config",
    "ComplianceEvent",
    "GuardrailEvent",
    "RBACEvent",
    "emit_compliance_event",
    "emit_guardrail_event",
    "emit_rbac_event",
    # Propagation helpers
    "get_current_trace_id",
    "get_current_span_id",
    "serialize_context",
    "restore_context",
    "inject_baggage",
    "extract_baggage",
    "set_baggage_on_current_context",
    # Logging
    "TraceContextFilter",
    "setup_request_logging",
    # Utils
    "hash_inputs",
    "summarize_output",
    "scrub_pii",
]

__version__ = "0.2.0"
