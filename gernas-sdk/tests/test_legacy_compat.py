"""Beacon Kit compatibility: generic spans kept, deprecated aliases forward to spec decorators."""
from __future__ import annotations

import pytest

import gernas
from gernas import Ctx, SpanName


@pytest.mark.parametrize("factory,expected_span", [
    (lambda: gernas.traced_agent("legacy_agent"), SpanName.AGENT),
    (lambda: gernas.traced_llm_call("gpt-4o"), SpanName.LLM),
    (lambda: gernas.traced_tool("legacy_tool"), "tool.legacy_tool"),
    (lambda: gernas.traced_mcp_tool("datalayer", "customer_360"), SpanName.MCP),
    (lambda: gernas.traced_a2a_hop("data_agent"), SpanName.DELEGATION),
])
def test_deprecated_aliases_warn_and_emit_spec_spans(spans, factory, expected_span):
    with pytest.warns(DeprecationWarning):
        deco = factory()

    @deco
    def fn(*a, **k):
        return {"ok": True}

    fn()
    assert spans(expected_span)


def test_traced_a2a_hop_injects_into_carrier(spans):
    with pytest.warns(DeprecationWarning):
        @gernas.traced_a2a_hop("peer")
        def hop(carrier):
            return dict(carrier)

    sent = hop({})
    assert "traceparent" in sent


def test_generic_spans_carry_the_envelope(spans):
    @gernas.traced
    def bare():
        return 1

    @gernas.traced(name="custom.span", attributes={"k": "v"})
    async def named():
        return 2

    @gernas.traced_node("node.score", state_attributes=["stage"])
    def node(state):
        return state

    @gernas.traced_pipeline("pipeline.root", attribute_keys=["customer"])
    def pipeline(customer=None):
        return node({"stage": "rank"})

    import asyncio
    bare(), asyncio.run(named()), pipeline(customer="c1")
    assert spans("custom.span")[0].attributes["k"] == "v"
    assert spans("node.score")[0].attributes["node.stage"] == "rank"
    root = spans("pipeline.root")[0]
    assert root.attributes["pipeline.customer"] == "c1" and root.attributes[Ctx.USE_CASE_ID] == "UC-TEST"
    assert spans("node.score")[0].parent.span_id == root.context.span_id


def test_agentic_metrics_manual_api_never_raises():
    m = gernas.AgenticMetrics()
    m.record_agent_call("success", "a", latency_ms=3)
    m.record_tool_call("success", "t")
    m.record_llm_call("success", "m", latency_ms=1)
    m.record_a2a_call("error", "n", latency_ms=2)
    m.record_mcp_call("success", "s", "t")
