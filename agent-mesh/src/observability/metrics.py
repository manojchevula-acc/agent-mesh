"""Custom business metrics for the AgentMesh pipeline.

Lazy-initialized OTel instruments (counters + histograms) that are deferred
until after ``setup_observability()`` runs and the MeterProvider is wired.
Each ``record_*`` convenience function is crash-safe and respects
``Config.ENABLE_BUSINESS_METRICS``.

Meter name: ``agent_mesh``

Counters
--------
fab.guardrail.requests.total   — input guardrail evaluations
fab.rbac.requests.total        — RBAC role checks
fab.compliance.requests.total  — compliance A2A reviews
fab.mesh.requests.total        — end-to-end mesh requests
fab.domain.route.total         — routing decisions (Data / RAG / Hybrid)
fab.a2a.calls.total            — A2A hops (by target node)
fab.mcp.calls.total            — MCP tool invocations (by service + tool)

Histograms (unit: ms unless stated)
--------------------------------------
fab.guardrail.duration         — guardrail wall-clock time
fab.rbac.duration              — RBAC wall-clock time
fab.compliance.duration        — compliance wall-clock time
fab.domain.duration            — domain dispatch wall-clock time
fab.a2a.duration               — A2A hop wall-clock time
fab.mesh.request.duration      — full mesh request wall-clock time
fab.output.redaction.pii_hits  — PII tokens redacted per request (unit: {match})
"""
from __future__ import annotations

from opentelemetry import metrics as _otel_metrics

from src.config import Config

# ---------------------------------------------------------------------------
# Lazy instrument singletons — created once on first access
# ---------------------------------------------------------------------------

_meter = None

# Counters
_guardrail_counter = None
_rbac_counter = None
_compliance_counter = None
_mesh_request_counter = None
_domain_route_counter = None
_a2a_counter = None
_mcp_counter = None

# Histograms
_guardrail_duration = None
_rbac_duration = None
_compliance_duration = None
_domain_duration = None
_a2a_duration = None
_mesh_request_duration = None
_pii_hits = None


def _get_meter():
    global _meter
    if _meter is None:
        _meter = _otel_metrics.get_meter_provider().get_meter("agent_mesh", version="1.0.0")
    return _meter


# --- Counter accessors --------------------------------------------------------

def _guardrail_ctr():
    global _guardrail_counter
    if _guardrail_counter is None:
        _guardrail_counter = _get_meter().create_counter(
            "fab.guardrail.requests.total",
            unit="{request}",
            description="Total input guardrail evaluations",
        )
    return _guardrail_counter


def _rbac_ctr():
    global _rbac_counter
    if _rbac_counter is None:
        _rbac_counter = _get_meter().create_counter(
            "fab.rbac.requests.total",
            unit="{request}",
            description="Total RBAC role validation checks",
        )
    return _rbac_counter


def _compliance_ctr():
    global _compliance_counter
    if _compliance_counter is None:
        _compliance_counter = _get_meter().create_counter(
            "fab.compliance.requests.total",
            unit="{request}",
            description="Total compliance A2A reviews",
        )
    return _compliance_counter


def _mesh_request_ctr():
    global _mesh_request_counter
    if _mesh_request_counter is None:
        _mesh_request_counter = _get_meter().create_counter(
            "fab.mesh.requests.total",
            unit="{request}",
            description="Total end-to-end mesh requests",
        )
    return _mesh_request_counter


def _domain_route_ctr():
    global _domain_route_counter
    if _domain_route_counter is None:
        _domain_route_counter = _get_meter().create_counter(
            "fab.domain.route.total",
            unit="{request}",
            description="Routing decisions by route type",
        )
    return _domain_route_counter


def _a2a_ctr():
    global _a2a_counter
    if _a2a_counter is None:
        _a2a_counter = _get_meter().create_counter(
            "fab.a2a.calls.total",
            unit="{request}",
            description="Total A2A hops by target node",
        )
    return _a2a_counter


def _mcp_ctr():
    global _mcp_counter
    if _mcp_counter is None:
        _mcp_counter = _get_meter().create_counter(
            "fab.mcp.calls.total",
            unit="{request}",
            description="Total MCP tool invocations by service and tool name",
        )
    return _mcp_counter


# --- Histogram accessors ------------------------------------------------------

def _guardrail_hist():
    global _guardrail_duration
    if _guardrail_duration is None:
        _guardrail_duration = _get_meter().create_histogram(
            "fab.guardrail.duration",
            unit="ms",
            description="Input guardrail wall-clock time in milliseconds",
        )
    return _guardrail_duration


def _rbac_hist():
    global _rbac_duration
    if _rbac_duration is None:
        _rbac_duration = _get_meter().create_histogram(
            "fab.rbac.duration",
            unit="ms",
            description="RBAC validation wall-clock time in milliseconds",
        )
    return _rbac_duration


def _compliance_hist():
    global _compliance_duration
    if _compliance_duration is None:
        _compliance_duration = _get_meter().create_histogram(
            "fab.compliance.duration",
            unit="ms",
            description="Compliance A2A review wall-clock time in milliseconds",
        )
    return _compliance_duration


def _domain_hist():
    global _domain_duration
    if _domain_duration is None:
        _domain_duration = _get_meter().create_histogram(
            "fab.domain.duration",
            unit="ms",
            description="Domain dispatch (price_assist A2A) wall-clock time in milliseconds",
        )
    return _domain_duration


def _a2a_hist():
    global _a2a_duration
    if _a2a_duration is None:
        _a2a_duration = _get_meter().create_histogram(
            "fab.a2a.duration",
            unit="ms",
            description="A2A hop wall-clock time in milliseconds",
        )
    return _a2a_duration


def _mesh_request_hist():
    global _mesh_request_duration
    if _mesh_request_duration is None:
        _mesh_request_duration = _get_meter().create_histogram(
            "fab.mesh.request.duration",
            unit="ms",
            description="Full mesh request wall-clock time in milliseconds",
        )
    return _mesh_request_duration


def _pii_hits_hist():
    global _pii_hits
    if _pii_hits is None:
        _pii_hits = _get_meter().create_histogram(
            "fab.output.redaction.pii_hits",
            unit="{match}",
            description="PII tokens redacted per request by output redaction",
        )
    return _pii_hits


# ---------------------------------------------------------------------------
# Public convenience record_* functions — all crash-safe
# ---------------------------------------------------------------------------

def record_guardrail(result: str, category: str, duration_ms: float) -> None:
    """Record one guardrail evaluation.

    Args:
        result:      ``"PASS"`` or ``"BLOCK"``
        category:    violation category or ``"none"``
        duration_ms: wall-clock time in milliseconds
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        attrs = {"result": result, "category": category, "stage": "input_guardrail"}
        _guardrail_ctr().add(1, attrs)
        _guardrail_hist().record(duration_ms, {"result": result})
    except Exception:
        pass


def record_rbac(result: str, role: str, duration_ms: float) -> None:
    """Record one RBAC check.

    Args:
        result:      ``"PASS"`` or ``"BLOCK"``
        role:        the role value being checked
        duration_ms: wall-clock time in milliseconds
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        attrs = {"result": result, "role": role}
        _rbac_ctr().add(1, attrs)
        _rbac_hist().record(duration_ms, {"result": result})
    except Exception:
        pass


def record_compliance(result: str, role: str, duration_ms: float) -> None:
    """Record one compliance review.

    Args:
        result:      ``"PASSED"``, ``"FAILED"``, or ``"BYPASSED"``
        role:        the role value of the requesting user
        duration_ms: wall-clock time in milliseconds
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        attrs = {"result": result, "role": role}
        _compliance_ctr().add(1, attrs)
        _compliance_hist().record(duration_ms, {"result": result})
    except Exception:
        pass


def record_mesh_request(result: str, block_stage: str, duration_ms: float) -> None:
    """Record one end-to-end mesh request.

    Args:
        result:      ``"SUCCESS"``, ``"BLOCKED"``, or ``"ERROR"``
        block_stage: stage name that blocked, or ``"none"``
        duration_ms: wall-clock time in milliseconds
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        attrs = {"result": result, "block_stage": block_stage}
        _mesh_request_ctr().add(1, attrs)
        _mesh_request_hist().record(duration_ms, attrs)
    except Exception:
        pass


def record_domain_route(route: str, duration_ms: float) -> None:
    """Record one routing decision.

    Args:
        route:       ``"Data Layer Service"``, ``"RAG Service"``, or
                     ``"Data Layer + RAG (Hybrid)"``
        duration_ms: domain dispatch wall-clock time in milliseconds
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        _domain_route_ctr().add(1, {"route": route})
        _domain_hist().record(duration_ms, {"route": route})
    except Exception:
        pass


def record_a2a_call(target_node: str, result: str, duration_ms: float) -> None:
    """Record one A2A hop.

    Args:
        target_node: name of the remote agent node
        result:      ``"SUCCESS"`` or ``"ERROR"``
        duration_ms: wall-clock time in milliseconds
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        attrs = {"target_node": target_node, "result": result}
        _a2a_ctr().add(1, attrs)
        _a2a_hist().record(duration_ms, {"target_node": target_node})
    except Exception:
        pass


def record_mcp_call(service: str, tool_name: str, result: str) -> None:
    """Record one MCP tool invocation.

    Args:
        service:   ``"datalayer"`` or ``"rag"``
        tool_name: name of the MCP tool
        result:    ``"SUCCESS"`` or ``"ERROR"``
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        _mcp_ctr().add(1, {"service": service, "tool_name": tool_name, "result": result})
    except Exception:
        pass


def record_pii_hits(count: int) -> None:
    """Record the number of PII tokens redacted in one output redaction pass.

    Args:
        count: number of ``[REDACTED_*]`` tokens found/replaced
    """
    if not Config.ENABLE_BUSINESS_METRICS:
        return
    try:
        _pii_hits_hist().record(float(count))
    except Exception:
        pass
