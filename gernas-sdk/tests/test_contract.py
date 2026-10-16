"""WP1-CORE-07 step 2: span shape vs registry for every decorator (schema test, not value test)."""
from __future__ import annotations

import pytest

import gernas
from gernas import ENVELOPE, REQUIRED_ATTRIBUTES, SpanName
from gernas.conformance import NormalSpan, check, from_readable


def _exercise_all() -> None:
    """Invoke every decorator once on its happy path."""
    @gernas.trace_tool("t")
    def tool(x):
        return x

    @gernas.trace_mcp("srv")
    def mcp(name):
        return {}

    @gernas.trace_llm("gpt-4o", provider="azure_openai")
    def llm():
        return {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    @gernas.trace_stream("gpt-4o", provider="azure_openai")
    def stream():
        yield "a"

    @gernas.trace_rag()
    def rag(query):
        return [1]

    @gernas.trace_retrieval()
    def retrieval(query):
        return [1]

    @gernas.trace_embedding("e")
    def embed(texts):
        return None

    @gernas.trace_memory("read")
    def memory(k):
        return {"a": 1}

    @gernas.trace_planning()
    def plan(goal):
        return ["s1"]

    @gernas.trace_chain("c", mode="invoke")
    def chain(x):
        return x

    @gernas.trace_guardrail("g")
    def guard(x):
        return (False, "")

    @gernas.trace_content_safety("pii")
    def safety(x):
        return False

    @gernas.trace_delegation("child")
    def delegate(p):
        return p

    @gernas.trace_agent("agent")
    def agent():
        tool(1), mcp("n"), llm(), list(stream()), rag("q"), retrieval("q"), embed(["a"]), memory("k")
        plan("g"), chain(1), guard("x"), safety("x"), delegate("p")
        with gernas.planning_step("p", "s", "tool_call"):
            pass
        gernas.start_hitl("contract-ticket", "act")
        gernas.complete_hitl("contract-ticket", "approved")

    @gernas.trace_use_case()
    def use_case():
        agent()

    use_case()


def test_every_decorator_emits_its_required_attributes(spans):
    _exercise_all()
    report = check(from_readable(spans()))
    assert report.passed, [v.__dict__ for v in report.violations]
    expected_ops = set(REQUIRED_ATTRIBUTES) - {SpanName.HTTP, SpanName.DOCTOR}
    assert expected_ops <= set(report.operations), expected_ops - set(report.operations)


def test_all_spans_share_one_trace(spans):
    _exercise_all()
    assert len({s.context.trace_id for s in spans()}) == 1
    assert all(s.end_time is not None for s in spans())


@pytest.mark.parametrize("missing", ["gernas.agent_run_id", ENVELOPE[0]])
def test_contract_fails_when_a_required_attribute_is_removed(missing):
    attrs = {k: "x" for k in (*ENVELOPE, *REQUIRED_ATTRIBUTES[SpanName.AGENT])}
    attrs["gernas.operation"] = SpanName.AGENT
    attrs["gernas.termination_reason"] = "completed"
    attrs["gernas.environment"] = "dev"
    attrs.pop(missing)
    report = check([NormalSpan(SpanName.AGENT, "t", "s", None, "OK", attrs)])
    assert not report.passed
    assert {(v.rule, v.key) for v in report.violations} == {("required", missing)}


def test_conformance_rules_unregistered_restricted_enum_cardinality():
    base = {"gernas.operation": SpanName.GUARDRAIL, "gernas.policy_id": "p", "gernas.policy_decision": "maybe",
            "gernas.business_domain": "b", "gernas.use_case_id": "u", "gernas.application_id": "a",
            "gernas.environment": "dev", "gernas.made_up": 1, "gen_ai.prompt": "raw text"}
    spans = [NormalSpan("guardrail.check", "t", f"s{i}", None, "OK", {**base, "gernas.agent_id": f"agent-{i}"})
             for i in range(5)]
    rules = {v.rule for v in check(spans, max_bounded_values=3).violations}
    assert rules == {"unregistered", "restricted", "enum", "cardinality"}
