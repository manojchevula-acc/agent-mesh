"""Blueprint §4–§6 KPI instruments and the §6 Quality evaluation API."""

from __future__ import annotations

import pytest

import gernas
from gernas import Ctx, Metric, SpanName
from gernas.attributes import METRIC_UNITS

from conftest import attrs


class Chunk:
    def __init__(self, usage=None):
        self.usage = usage


def test_use_case_success_counter(metric_points):
    @gernas.trace_use_case(channel="web", outcome_from=lambda r: "success")
    def handle():
        return 1

    handle()
    assert metric_points()[Metric.USE_CASE_REQUESTS] == [
        (
            {
                Ctx.USE_CASE_ID: "UC-TEST",
                Ctx.CHANNEL: "web",
                Ctx.BUSINESS_OUTCOME: "success",
                Ctx.EXECUTION_STATUS: "ok",
            },
            1,
        )
    ]


def test_steps_per_run_and_planning_latency(metric_points):
    @gernas.trace_planning()
    def plan(goal):
        return ["a", "b"]

    @gernas.trace_tool("t")
    def tool():
        return 1

    @gernas.trace_agent("kpi_agent", agent_type="planner")
    def run():
        plan("g")
        tool()
        tool()

    run()
    m = metric_points()
    assert m[Metric.AGENT_STEPS] == [({Ctx.AGENT_ID: "kpi_agent"}, 1)]  # one histogram observation
    assert m[Metric.AGENT_PLAN_DURATION] == [({Ctx.AGENT_ID: "kpi_agent"}, 1)]


def test_streaming_ttft_and_tokens_per_second(metric_points):
    @gernas.trace_llm("gpt-4o", provider="azure_openai")
    def stream():
        yield Chunk()
        yield Chunk(usage={"prompt_tokens": 5, "completion_tokens": 9})

    list(stream())
    m = metric_points()
    dims = {Ctx.PROVIDER: "azure_openai", Ctx.MODEL_ID: "gpt-4o"}
    assert m[Metric.LLM_TTFT] == [(dims, 1)] and m[Metric.LLM_TOKENS_PER_SECOND] == [(dims, 1)]


def test_fallback_counter(metric_points):
    @gernas.trace_llm("m", provider="p")
    def chat():
        gernas.mark_fallback()
        return {}

    @gernas.trace_agent("fb_agent", agent_type="worker")
    def run():
        chat()

    run()
    assert metric_points()[Metric.FALLBACKS] == [({Ctx.AGENT_ID: "fb_agent", Ctx.OPERATION: SpanName.LLM}, 1)]


def test_histogram_units_come_from_the_registry():
    assert METRIC_UNITS[Metric.AGENT_STEPS] == "{step}" and METRIC_UNITS[Metric.LLM_TTFT] == "ms"
    assert METRIC_UNITS[Metric.EVALUATION_SCORE] == "1"


def test_record_evaluation_inside_a_run(spans, metric_points):
    @gernas.trace_agent("eval_agent", agent_type="worker")
    def run():
        gernas.record_evaluation("groundedness", 0.82, passed=True, evaluator="judge_v1")

    run()
    ev, agent = spans(SpanName.EVALUATION)[0], spans(SpanName.AGENT)[0]
    a = attrs(ev)
    assert ev.parent.span_id == agent.context.span_id
    assert a[Ctx.EVALUATION_METRIC] == "groundedness" and a[Ctx.EVALUATION_SCORE] == 0.82
    assert a[Ctx.EVALUATION_PASSED] is True and a[Ctx.EVALUATOR] == "judge_v1"
    assert a[Ctx.AGENT_RUN_ID] == attrs(agent)[Ctx.AGENT_RUN_ID]
    assert metric_points()[Metric.EVALUATION_SCORE] == [
        ({Ctx.EVALUATION_METRIC: "groundedness", Ctx.AGENT_ID: "eval_agent", Ctx.EVALUATION_PASSED: True}, 1)
    ]
    from gernas.conformance import check, from_readable

    assert check(from_readable(spans())).passed


def test_record_evaluation_offline_names_the_evaluated_run(spans):
    gernas.record_evaluation("refusal", 0.0, agent_id="faq_agent", agent_run_id="run_abc")
    a = attrs(spans(SpanName.EVALUATION)[0])
    assert a[Ctx.AGENT_ID] == "faq_agent" and a[Ctx.AGENT_RUN_ID] == "run_abc"


@pytest.mark.parametrize("metric,score", [("vibes", 0.5), ("relevance", 1.5), ("relevance", -0.1)])
def test_record_evaluation_rejects_bad_input(metric, score):
    with pytest.raises(ValueError):
        gernas.record_evaluation(metric, score)
