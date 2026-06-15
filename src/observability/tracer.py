# -*- coding: utf-8 -*-
"""Observability - mesh-specific tracing using the Microsoft Agent Framework SDK.

The Agent Framework SDK's built-in observability (agent_framework.observability)
auto-instruments the following WITHOUT any code changes:
  - agent.run() calls         -> AgentTelemetryLayer (span + tokens + model)
  - @tool function executions -> get_function_span() (span per tool call)
  - LLM get_response() calls  -> ChatTelemetryLayer  (span per LLM call)
  - MCP tool calls            -> create_mcp_client_span()

To activate, call configure_otel_providers() at startup (run.py / a2a_server.py).
For App Insights: configure_azure_monitor(connection_string=...) from
  azure-monitor-opentelemetry (replaces configure_otel_providers).

This module handles ONLY mesh-specific events the SDK does NOT cover:
  SYSTEM_FLOW  - orchestrator pipeline steps (start, routing, complete, blocked)
  A2A_CALL     - outbound HTTP hops between agent nodes
  ACCESS_CTRL  - RBAC gate decisions
  COMPLIANCE   - compliance agent verdicts
  GUARDRAIL    - deterministic input/output filter results
  PAYMENT_GATE - human-in-the-loop approval gate

Each event is:
  1. Emitted as an OTel span via the SDK's get_tracer() so it appears in the
     distributed trace tree alongside the SDK's agent/tool spans.
  2. Written to data/trace_log.jsonl for local debugging.
  3. Routed through _send_to_app_insights() -- no-op until App Insights is wired.
"""
from __future__ import annotations

import json, os, sys, uuid, pathlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config


class EventType(str, Enum):
    SYSTEM_FLOW  = "SYSTEM_FLOW"
    A2A_CALL     = "A2A_CALL"
    ACCESS_CTRL  = "ACCESS_CTRL"
    COMPLIANCE   = "COMPLIANCE"
    GUARDRAIL    = "GUARDRAIL"
    PAYMENT_GATE = "PAYMENT_GATE"


class Status(str, Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    BLOCK   = "BLOCK"
    SUCCESS = "SUCCESS"
    ERROR   = "ERROR"
    DENY    = "DENY"
    APPROVE = "APPROVE"


@dataclass
class TraceEvent:
    event_type: str
    name: str
    status: str
    timestamp: str            = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trace_id: str | None      = None
    span_id: str              = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_span_id: str | None = None
    duration_ms: int | None   = None
    attributes: dict           = field(default_factory=dict)
    error: str | None         = None


# -- Application Insights / OTel placeholder ----------------------------------

def _send_to_app_insights(event: dict) -> None:
    """
    ====================================================================
    APPLICATION INSIGHTS INTEGRATION - PLACEHOLDER (no-op for now)
    ====================================================================

    The Agent Framework SDK handles agent/tool/LLM spans automatically once
    you activate its built-in OpenTelemetry support at startup.

    OPTION A: Azure Monitor (recommended -- App Insights)
    -----------------------------------------------------
    pip install azure-monitor-opentelemetry

    In run.py and a2a_server.py (before any agents start), add:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(
            connection_string=os.environ["APPINSIGHTS_CONNECTION_STRING"]
        )
        # Optional: capture full message content in spans
        from agent_framework.observability import enable_sensitive_telemetry
        enable_sensitive_telemetry()

    OPTION B: Any OTLP backend (Jaeger, Grafana, etc.)
    ---------------------------------------------------
    Set environment variables, then call at startup:
        OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
        OTEL_SERVICE_NAME=agent_mesh

        from agent_framework.observability import configure_otel_providers
        configure_otel_providers()

    ENV VARS:
        APPINSIGHTS_CONNECTION_STRING = InstrumentationKey=<key>;IngestionEndpoint=...
        OTEL_EXPORTER_OTLP_ENDPOINT   = http://<backend>:4317
        OTEL_SERVICE_NAME             = agent_mesh
        ENABLE_CONSOLE_EXPORTERS      = true   (dev: print spans to stdout)
        ENABLE_SENSITIVE_DATA         = true   (dev: include message content)
    ====================================================================
    """
    pass  # no-op until App Insights / OTLP backend is configured


# -- OTel span helper using SDK's get_tracer() ---------------------------------

def _otel_span(name: str, attrs: dict, status: str, error: str | None = None) -> None:
    """Creates and immediately closes an OTel span via the SDK's tracer.
    The span is parented under the current OTel context, so it appears as a
    child of the enclosing agent/LLM span in the distributed trace tree.
    Safe no-op if OTel is not configured.
    """
    try:
        from agent_framework.observability import get_tracer
        from opentelemetry import trace
        tracer = get_tracer("agent_mesh")
        with tracer.start_as_current_span(f"mesh.{name}") as span:
            for k, v in attrs.items():
                safe_v = v if isinstance(v, (bool, int, float, str)) else str(v)
                span.set_attribute(str(k), safe_v)
            if error:
                span.set_attribute("error.message", error)
                span.set_status(trace.StatusCode.ERROR, error)
    except Exception:
        pass  # observability must NEVER crash the application


# -- Local JSONL storage -------------------------------------------------------

def _write_local(event: dict) -> None:
    """Append one JSON line to data/trace_log.jsonl. Never raises."""
    try:
        path = Config.TRACE_LOG_FILE
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def emit(event: TraceEvent) -> None:
    d = asdict(event)
    _write_local(d)
    _send_to_app_insights(d)
    _otel_span(event.name, {**event.attributes, "trace_id": event.trace_id or "",
                             "status": event.status, "duration_ms": event.duration_ms or 0},
               event.status, event.error)


# -- Typed emitters � mesh-specific layers only --------------------------------

def trace_guardrail(
    query: str, passed: bool, categories: list, duration_ms: int,
    trace_id: str | None = None, parent_span_id: str | None = None,
) -> None:
    emit(TraceEvent(
        event_type=EventType.GUARDRAIL, name="input_screen",
        status=Status.PASS if passed else Status.BLOCK,
        trace_id=trace_id, parent_span_id=parent_span_id, duration_ms=duration_ms,
        attributes={"query_length": len(query), "passed": passed,
                    "blocked_categories": categories},
    ))


def trace_a2a_call(
    node: str, prompt: str, response: str, duration_ms: int, status: str,
    trace_id: str | None = None, parent_span_id: str | None = None,
    error: str | None = None,
) -> None:
    emit(TraceEvent(
        event_type=EventType.A2A_CALL, name=f"a2a:{node}",
        status=status, trace_id=trace_id, parent_span_id=parent_span_id,
        duration_ms=duration_ms,
        attributes={"target_node": node, "prompt_length": len(prompt),
                    "response_length": len(response) if response else 0,
                    "response_preview": (response or "")[:120]},
        error=error,
    ))


def trace_access_control(
    domain: str, role: str, allowed: bool, reason: str,
    trace_id: str | None = None, parent_span_id: str | None = None,
) -> None:
    emit(TraceEvent(
        event_type=EventType.ACCESS_CTRL, name=f"access:{domain}",
        status=Status.PASS if allowed else Status.DENY,
        trace_id=trace_id, parent_span_id=parent_span_id,
        attributes={"domain": domain, "user_role": role,
                    "allowed": allowed, "reason": reason},
    ))


def trace_compliance(
    query: str, verdict: str, duration_ms: int,
    trace_id: str | None = None, parent_span_id: str | None = None,
) -> None:
    passed = "compliance_failed" not in verdict.lower()
    emit(TraceEvent(
        event_type=EventType.COMPLIANCE, name="compliance_check",
        status=Status.PASS if passed else Status.FAIL,
        trace_id=trace_id, parent_span_id=parent_span_id, duration_ms=duration_ms,
        attributes={"query_length": len(query), "passed": passed,
                    "verdict_preview": verdict[:120]},
    ))


def trace_payment_gate(
    approved: bool,
    trace_id: str | None = None, parent_span_id: str | None = None,
) -> None:
    emit(TraceEvent(
        event_type=EventType.PAYMENT_GATE, name="payment_approval_gate",
        status=Status.APPROVE if approved else Status.DENY,
        trace_id=trace_id, parent_span_id=parent_span_id,
        attributes={"approved": approved},
    ))


def trace_flow_step(
    step: str, status: str, duration_ms: int,
    attrs: dict | None = None,
    trace_id: str | None = None, parent_span_id: str | None = None,
    error: str | None = None,
) -> None:
    emit(TraceEvent(
        event_type=EventType.SYSTEM_FLOW, name=f"flow:{step}",
        status=status, trace_id=trace_id, parent_span_id=parent_span_id,
        duration_ms=duration_ms, attributes=attrs or {}, error=error,
    ))
