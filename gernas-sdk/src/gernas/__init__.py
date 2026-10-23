"""Gernas SDK — GERNAS Agent Telemetry SDK (``pip install gernas-telemetry``, ``import gernas``).

Implements the GERNAS Enterprise Telemetry & Observability Blueprint on OpenTelemetry:
the canonical telemetry context (Blueprint §2) on every signal, export to the GERNAS
OTel Collector (§1), and the agent / model / tool / knowledge / safety telemetry of
§3, §5 and §6 — the same model for Microsoft Agent Framework, LangChain, LangGraph and
custom Python agents::

    import gernas

    gernas.init_gernas()                                   # GERNAS_* environment variables

    @gernas.trace_agent("pricing_assistant", agent_type="orchestrator")
    async def run(question: str) -> str: ...
"""

from collections.abc import Mapping
from typing import Any

from gernas._version import __version__
from gernas.attributes import Ctx, Metric, SpanName
from gernas.config import GernasConfig, GernasConfigError
from gernas.context import (
    app_context,
    app_value,
    attach_headers,
    correlation,
    extract_context,
    gernas_task,
    inject_context,
    request_context,
    wrap_in_context,
)
from gernas.decorators import (
    mark_fallback,
    trace_agent,
    trace_content_safety,
    trace_delegation,
    trace_document,
    trace_embedding,
    trace_guardrail,
    trace_llm,
    trace_memory,
    trace_planning,
    trace_retrieval,
    trace_step,
    trace_tool,
    trace_use_case,
)
from gernas.evaluation import record_evaluation
from gernas.hitl import complete_hitl, start_hitl, trace_hitl
from gernas.logs import CorrelationLogFilter
from gernas.redaction import DROP, set_redaction_hook
from gernas.run_state import record_retry, set_termination_reason
from gernas.tracer import GernasHandle, init_gernas


def annotate(attributes: Mapping[str, Any]) -> None:
    """Set registry attributes (``Ctx.*`` keys) on the current span, e.g. ``Ctx.COMPUTE_UNITS``.

    Values pass through the same redaction as SDK-written attributes; never raises.
    """
    from opentelemetry import trace

    from gernas.tracer import safe_set_many

    safe_set_many(trace.get_current_span(), attributes)


__all__ = [
    "__version__",
    # setup
    "init_gernas",
    "GernasConfig",
    "GernasConfigError",
    "GernasHandle",
    # semantic conventions
    "Ctx",
    "SpanName",
    "Metric",
    # decorators
    "trace_use_case",
    "trace_agent",
    "trace_step",
    "trace_planning",
    "trace_delegation",
    "trace_llm",
    "trace_embedding",
    "trace_tool",
    "trace_retrieval",
    "trace_document",
    "trace_memory",
    "trace_guardrail",
    "trace_content_safety",
    "trace_hitl",
    # run / call helpers
    "start_hitl",
    "complete_hitl",
    "record_retry",
    "set_termination_reason",
    "mark_fallback",
    "record_evaluation",
    "annotate",
    # context propagation
    "request_context",
    "correlation",
    "inject_context",
    "extract_context",
    "attach_headers",
    "gernas_task",
    "wrap_in_context",
    "app_context",
    "app_value",
    # logging
    "CorrelationLogFilter",
    # privacy
    "set_redaction_hook",
    "DROP",
]
