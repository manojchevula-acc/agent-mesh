"""Metric instruments for agentic systems (names come from ``gernas.Metric``).

Decorators record these automatically; :class:`AgenticMetrics` remains for
manual recording. Instruments are created lazily on the global meter proxy, so
importing this module before ``init_gernas()`` costs nothing and still works
afterwards. Dimensions are bounded-cardinality registry fields only.
"""
from __future__ import annotations

import threading
from typing import Any

from opentelemetry import metrics

from gernas._version import __version__
from gernas.attributes import Ctx, Metric

_UNITS = {
    Metric.AGENT_CALLS: "{call}", Metric.LLM_CALLS: "{call}", Metric.TOOL_CALLS: "{call}",
    Metric.MCP_CALLS: "{call}", Metric.A2A_CALLS: "{call}", Metric.RETRIEVAL_CALLS: "{call}",
    Metric.LLM_TOKENS: "{token}", Metric.LLM_COST: "AED", Metric.GUARDRAIL_DECISIONS: "{decision}",
    Metric.HITL_OUTCOMES: "{approval}", Metric.LOOP_DETECTIONS: "{run}", Metric.MAX_STEP_VIOLATIONS: "{run}",
    Metric.RETRIES: "{retry}", Metric.ORPHANS: "{run}",
}


class _Instruments:
    def __init__(self, meter_name: str = "gernas") -> None:
        self._meter_name = meter_name
        self._lock = threading.Lock()
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._gauges: dict[str, Any] = {}

    def _meter(self) -> metrics.Meter:
        return metrics.get_meter(self._meter_name, __version__)

    def counter(self, name: str):
        inst = self._counters.get(name)
        if inst is None:
            with self._lock:
                inst = self._counters.get(name)
                if inst is None:
                    inst = self._meter().create_counter(name, unit=_UNITS.get(name, "1"))
                    self._counters[name] = inst
        return inst

    def histogram(self, name: str):
        inst = self._histograms.get(name)
        if inst is None:
            with self._lock:
                inst = self._histograms.get(name)
                if inst is None:
                    inst = self._meter().create_histogram(name, unit="ms")
                    self._histograms[name] = inst
        return inst

    def gauge(self, name: str):
        inst = self._gauges.get(name)
        if inst is None:
            with self._lock:
                inst = self._gauges.get(name)
                if inst is None:
                    inst = self._meter().create_gauge(name, unit="1")
                    self._gauges[name] = inst
        return inst


_instruments = _Instruments()


def instruments() -> _Instruments:
    return _instruments


def record(counter: str, attrs: dict, *, histogram: str | None = None, latency_ms: float | None = None,
           n: float = 1) -> None:
    """Best-effort metric write used by the decorators; never raises."""
    try:
        _instruments.counter(counter).add(n, attrs)
        if histogram and latency_ms is not None:
            _instruments.histogram(histogram).record(latency_ms, attrs)
    except Exception:  # noqa: BLE001
        pass


class AgenticMetrics:
    """Manual recording API (kept from Beacon Kit, renamed to ``gernas.*`` metrics).

    Example::

        m = AgenticMetrics()
        m.record_agent_call("success", "triage_agent", latency_ms=120)
    """

    def __init__(self, meter_name: str = "gernas") -> None:
        self._inst = _instruments if meter_name == "gernas" else _Instruments(meter_name)

    def _rec(self, counter: str, attrs: dict, histogram: str | None = None, latency_ms: float = 0.0) -> None:
        try:
            self._inst.counter(counter).add(1, attrs)
            if histogram and latency_ms > 0:
                self._inst.histogram(histogram).record(latency_ms, attrs)
        except Exception:  # noqa: BLE001
            pass

    def record_agent_call(self, result: str, agent_name: str, latency_ms: float = 0.0) -> None:
        self._rec(Metric.AGENT_CALLS, {"result": result, Ctx.AGENT_ID: agent_name}, Metric.AGENT_DURATION, latency_ms)

    def record_tool_call(self, result: str, tool_name: str) -> None:
        self._rec(Metric.TOOL_CALLS, {"result": result, Ctx.TOOL_ID: tool_name})

    def record_llm_call(self, result: str, model: str, latency_ms: float = 0.0) -> None:
        self._rec(Metric.LLM_CALLS, {"result": result, Ctx.MODEL_ID: model}, Metric.LLM_DURATION, latency_ms)

    def record_a2a_call(self, result: str, target_node: str, latency_ms: float = 0.0) -> None:
        self._rec(Metric.A2A_CALLS, {"result": result, Ctx.CHILD_AGENT_ID: target_node}, Metric.A2A_DURATION, latency_ms)

    def record_mcp_call(self, result: str, service: str, tool_name: str) -> None:
        self._rec(Metric.MCP_CALLS, {"result": result, Ctx.MCP_SERVER_ID: service, Ctx.MCP_TOOL_NAME: tool_name})
