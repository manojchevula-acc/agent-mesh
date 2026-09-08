"""AgenticMetrics — lazy-initialized OTel instruments for Layer 2 (Agentic Interactions).

All instruments are created on first use so importing this module has zero cost
before setup_telemetry() is called.
"""
from __future__ import annotations

from opentelemetry import metrics


class AgenticMetrics:
    """Lazily-initialized OTel counters and histograms for agentic systems.

    Instantiate once after setup_telemetry() and call record_* methods.

    Example:
        m = AgenticMetrics()
        m.record_agent_call("success", "triage_agent")
    """

    def __init__(self, meter_name: str = "beacon_kit") -> None:
        self._meter_name = meter_name
        self._meter: metrics.Meter | None = None
        # counters
        self._agent_counter = None
        self._tool_counter = None
        self._llm_counter = None
        self._a2a_counter = None
        self._mcp_counter = None
        # histograms
        self._agent_duration = None
        self._llm_duration = None
        self._a2a_duration = None

    def _get_meter(self) -> metrics.Meter:
        if self._meter is None:
            self._meter = metrics.get_meter(self._meter_name)
        return self._meter

    def _agent_ctr(self):
        if self._agent_counter is None:
            self._agent_counter = self._get_meter().create_counter(
                "beacon.agent.calls.total", unit="{call}", description="Total agent invocations"
            )
        return self._agent_counter

    def _tool_ctr(self):
        if self._tool_counter is None:
            self._tool_counter = self._get_meter().create_counter(
                "beacon.tool.calls.total", unit="{call}", description="Total tool invocations"
            )
        return self._tool_counter

    def _llm_ctr(self):
        if self._llm_counter is None:
            self._llm_counter = self._get_meter().create_counter(
                "beacon.llm.calls.total", unit="{call}", description="Total LLM API calls"
            )
        return self._llm_counter

    def _a2a_ctr(self):
        if self._a2a_counter is None:
            self._a2a_counter = self._get_meter().create_counter(
                "beacon.a2a.calls.total", unit="{call}", description="Total A2A hop calls"
            )
        return self._a2a_counter

    def _mcp_ctr(self):
        if self._mcp_counter is None:
            self._mcp_counter = self._get_meter().create_counter(
                "beacon.mcp.calls.total", unit="{call}", description="Total MCP tool calls"
            )
        return self._mcp_counter

    def _agent_hist(self):
        if self._agent_duration is None:
            self._agent_duration = self._get_meter().create_histogram(
                "beacon.agent.duration", unit="ms", description="Agent invocation latency"
            )
        return self._agent_duration

    def _llm_hist(self):
        if self._llm_duration is None:
            self._llm_duration = self._get_meter().create_histogram(
                "beacon.llm.duration", unit="ms", description="LLM call latency"
            )
        return self._llm_duration

    def _a2a_hist(self):
        if self._a2a_duration is None:
            self._a2a_duration = self._get_meter().create_histogram(
                "beacon.a2a.duration", unit="ms", description="A2A hop latency"
            )
        return self._a2a_duration

    # ── Public record methods ────────────────────────────────────────────────

    def record_agent_call(self, result: str, agent_name: str, latency_ms: float = 0.0) -> None:
        try:
            attrs = {"result": result, "agent_name": agent_name}
            self._agent_ctr().add(1, attrs)
            if latency_ms > 0:
                self._agent_hist().record(latency_ms, attrs)
        except Exception:
            pass

    def record_tool_call(self, result: str, tool_name: str) -> None:
        try:
            self._tool_ctr().add(1, {"result": result, "tool_name": tool_name})
        except Exception:
            pass

    def record_llm_call(self, result: str, model: str, latency_ms: float = 0.0) -> None:
        try:
            attrs = {"result": result, "model": model}
            self._llm_ctr().add(1, attrs)
            if latency_ms > 0:
                self._llm_hist().record(latency_ms, attrs)
        except Exception:
            pass

    def record_a2a_call(self, result: str, target_node: str, latency_ms: float = 0.0) -> None:
        try:
            attrs = {"result": result, "target_node": target_node}
            self._a2a_ctr().add(1, attrs)
            if latency_ms > 0:
                self._a2a_hist().record(latency_ms, attrs)
        except Exception:
            pass

    def record_mcp_call(self, result: str, service: str, tool_name: str) -> None:
        try:
            self._mcp_ctr().add(1, {"result": result, "service": service, "tool_name": tool_name})
        except Exception:
            pass
