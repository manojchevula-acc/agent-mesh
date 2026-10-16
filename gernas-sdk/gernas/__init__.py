"""Gernas SDK — GERNAS Agent Telemetry SDK (``pip install gernas-telemetry``, ``import gernas``).

Evolved from Beacon Kit. Built only on OpenTelemetry SDK primitives; telemetry
flows SDK → OTel Collector → Opik / data plane. One startup call, decorators as
the only interface, PDPL redaction on every attribute, and telemetry that never
breaks the agent::

    import gernas

    gernas.init_gernas()                      # reads GERNAS_* environment variables

    @gernas.trace_agent("triage", framework="custom")
    async def run(query: str) -> str: ...
"""
from gernas._version import __version__
from gernas.async_context import (
    attach_headers,
    attached,
    extract_context,
    gernas_task,
    inject_context,
    run_in_context,
    to_thread,
    wrap_in_context,
)
from gernas.attributes import CANONICAL_CONTEXT, ENVELOPE, REQUIRED_ATTRIBUTES, Ctx, Metric, SpanName
from gernas.compliance.decorators import (
    GuardrailBlockedError,
    RBACDeniedError,
    compliance_gate,
    guardrail_check,
    rbac_check,
    set_default_compliance_config,
)
from gernas.compliance.events import (
    ComplianceEvent,
    GuardrailEvent,
    RBACEvent,
    emit_compliance_event,
    emit_guardrail_event,
    emit_rbac_event,
)
from gernas.config import GernasConfig
from gernas.core.audit_report import generate_audit_report
from gernas.core.file_exporters import FileLogExporter, FileMetricExporter, FileSpanExporter
from gernas.core.html_reporter import generate_html_report
from gernas.core.structured_logger import StructuredLogger, get_logger
from gernas.decorators import (
    count_calls,
    log_calls,
    planning_step,
    trace_agent,
    trace_chain,
    trace_content_safety,
    trace_delegation,
    trace_embedding,
    trace_guardrail,
    trace_hitl,
    trace_llm,
    trace_mcp,
    trace_memory,
    trace_planning,
    trace_rag,
    trace_retrieval,
    trace_stream,
    trace_tool,
    trace_use_case,
    traced,
    traced_a2a_hop,
    traced_agent,
    traced_llm_call,
    traced_mcp_tool,
    traced_node,
    traced_pipeline,
    traced_tool,
    track_latency,
)
from gernas.hitl import complete_hitl, pending_hitl, start_hitl, sweep_expired
from gernas.logconfig.request_logging import TraceContextFilter, setup_request_logging
from gernas.metrics.agentic import AgenticMetrics
from gernas.prompt_registry import prompt_sha256, record_prompt
from gernas.propagation.baggage import extract_baggage, inject_baggage, set_baggage_on_current_context
from gernas.propagation.context import get_current_span_id, get_current_trace_id, restore_context, serialize_context
from gernas.redaction import DROP, set_redaction_hook
from gernas.run_state import current_run, record_retry, record_step, set_termination_reason
from gernas.tracer import (
    GernasHandle,
    flush_gernas,
    get_config,
    get_dropped_attribute_count,
    get_dropped_span_count,
    get_handle,
    get_tracer,
    init_gernas,
    is_initialized,
    safe_set,
    safe_set_many,
    shutdown_gernas,
)
from gernas.utils.hashing import hash_inputs, summarize_output
from gernas.utils.sanitizer import scrub_pii

__all__ = [
    "__version__",
    # setup
    "init_gernas", "GernasConfig", "GernasHandle", "get_handle", "get_config", "get_tracer", "is_initialized",
    "flush_gernas", "shutdown_gernas", "get_dropped_span_count", "get_dropped_attribute_count",
    # attribute registry
    "Ctx", "SpanName", "Metric", "ENVELOPE", "CANONICAL_CONTEXT", "REQUIRED_ATTRIBUTES",
    # the sixteen decorators
    "trace_agent", "trace_llm", "trace_tool", "trace_mcp", "trace_stream", "trace_hitl", "trace_rag",
    "trace_retrieval", "trace_embedding", "trace_memory", "trace_planning", "trace_chain", "trace_use_case",
    "trace_delegation", "trace_guardrail", "trace_content_safety",
    # helpers
    "planning_step", "start_hitl", "complete_hitl", "pending_hitl", "sweep_expired", "record_prompt",
    "prompt_sha256", "current_run", "record_retry", "record_step", "set_termination_reason",
    "safe_set", "safe_set_many", "set_redaction_hook", "DROP",
    # propagation
    "inject_context", "extract_context", "attached", "attach_headers", "gernas_task", "run_in_context",
    "wrap_in_context", "to_thread", "inject_baggage", "extract_baggage", "set_baggage_on_current_context",
    "get_current_trace_id", "get_current_span_id", "serialize_context", "restore_context",
    # generic spans, metrics, logging
    "traced", "traced_node", "traced_pipeline", "track_latency", "count_calls", "log_calls", "AgenticMetrics",
    "TraceContextFilter", "setup_request_logging", "StructuredLogger", "get_logger",
    # compliance (Layer 3 audit chain)
    "compliance_gate", "guardrail_check", "rbac_check", "GuardrailBlockedError", "RBACDeniedError",
    "set_default_compliance_config", "ComplianceEvent", "GuardrailEvent", "RBACEvent",
    "emit_compliance_event", "emit_guardrail_event", "emit_rbac_event",
    # local sinks and reports
    "FileSpanExporter", "FileMetricExporter", "FileLogExporter", "generate_html_report", "generate_audit_report",
    # utils
    "hash_inputs", "summarize_output", "scrub_pii",
    # deprecated Beacon Kit aliases
    "traced_agent", "traced_llm_call", "traced_tool", "traced_mcp_tool", "traced_a2a_hop",
]
