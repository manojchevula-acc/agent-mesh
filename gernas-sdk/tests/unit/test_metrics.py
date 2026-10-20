"""Metric emission: exact names, bounded dimensions only, one data point per call."""

from __future__ import annotations

import gernas
from gernas import Ctx, Metric
from gernas.attributes import REGISTRY


def _bounded(points):
    for dims, _ in points:
        for key in dims:
            assert REGISTRY[key].cardinality != "unbounded", key


def test_agent_tool_llm_metrics(metric_points):
    @gernas.trace_llm("gpt-4o", provider="azure_openai")
    def chat():
        return {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}

    @gernas.trace_tool("lookup")
    def lookup():
        return 1

    @gernas.trace_agent("metrics_agent", agent_type="worker")
    def run():
        chat()
        lookup()
        gernas.record_retry()

    run()
    m = metric_points()
    assert m[Metric.AGENT_RUNS] == [
        ({Ctx.AGENT_ID: "metrics_agent", Ctx.TERMINATION_REASON: "completed", Ctx.AGENT_VERSION: "9.9.9"}, 1)
    ]
    assert m[Metric.AGENT_RETRIES] == [({Ctx.AGENT_ID: "metrics_agent"}, 1)]
    assert m[Metric.TOOL_CALLS] == [
        (
            {
                Ctx.TOOL_ID: "lookup",
                Ctx.TOOL_TYPE: "function",
                Ctx.EXECUTION_STATUS: "ok",
                Ctx.AUTHORIZATION_DECISION: "allow",
            },
            1,
        )
    ]
    assert m[Metric.LLM_CALLS][0][0] == {
        Ctx.PROVIDER: "azure_openai",
        Ctx.MODEL_ID: "gpt-4o",
        Ctx.OPERATION: "llm.call",
        Ctx.EXECUTION_STATUS: "ok",
    }
    tokens = {d[Ctx.TOKEN_TYPE]: v for d, v in m[Metric.LLM_TOKENS]}
    assert tokens == {"input": 100, "output": 50}
    assert m[Metric.LLM_ESTIMATED_COST][0][1] > 0
    for name in (Metric.AGENT_RUN_DURATION, Metric.TOOL_DURATION, Metric.LLM_DURATION):
        assert m[name][0][1] == 1  # one histogram observation each
    for points in m.values():
        _bounded(points)


def test_policy_retrieval_delegation_hitl_metrics(metric_points):
    @gernas.trace_guardrail("pii")
    def guard():
        return ("deny", "pii")

    @gernas.trace_retrieval(index_id="kb")
    def search():
        return []

    @gernas.trace_delegation("child")
    def delegate():
        return 1

    @gernas.trace_agent("parent_m", agent_type="orchestrator")
    def run():
        guard()
        search()
        delegate()
        gernas.start_hitl("M-1", "approve")
        gernas.complete_hitl("M-1", "approved")

    run()
    m = metric_points()
    assert m[Metric.POLICY_DECISIONS] == [({Ctx.POLICY_ID: "pii", Ctx.POLICY_DECISION: "deny"}, 1)]
    assert m[Metric.RETRIEVAL_CALLS] == [({Ctx.INDEX_ID: "kb", Ctx.EXECUTION_STATUS: "ok", Ctx.EMPTY_RESULT: True}, 1)]
    assert m[Metric.AGENT_DELEGATIONS][0][0] == {
        Ctx.PARENT_AGENT_ID: "parent_m",
        Ctx.CHILD_AGENT_ID: "child",
        Ctx.EXECUTION_STATUS: "ok",
    }
    assert m[Metric.HITL_INTERVENTIONS] == [({Ctx.HITL_ACTION: "approve", Ctx.HITL_OUTCOME: "approved"}, 1)]
    for points in m.values():
        _bounded(points)


def test_loop_metric_counts_once_per_run(metric_points):
    @gernas.trace_tool("same")
    def tool():
        return 1

    @gernas.trace_agent("looper", agent_type="worker")
    def run():
        for _ in range(6):
            tool()

    run()
    assert metric_points()[Metric.AGENT_LOOPS] == [({Ctx.AGENT_ID: "looper"}, 1)]


def test_sdk_health_counters_are_registered(metric_points):
    from opentelemetry import trace

    gernas.annotate({"gen_ai.prompt": "x"})  # a redaction on the (non-recording) current span
    with trace.get_tracer("t").start_as_current_span("s"):
        gernas.annotate({"gen_ai.prompt": "x"})
    names = set(metric_points()) | set(_all_names())
    for name in (
        Metric.SDK_DROPPED_SPANS,
        Metric.SDK_DROPPED_LOGS,
        Metric.SDK_DROPPED_ATTRIBUTES,
        Metric.SDK_REDACTIONS,
    ):
        assert name in names


def _all_names():
    from conftest import METRICS

    data = METRICS.get_metrics_data()
    return {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
