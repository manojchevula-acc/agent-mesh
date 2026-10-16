"""WP1-CORE-04: human approval spans and prompt provenance."""
from __future__ import annotations

import asyncio

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas.hitl import pending_hitl


@pytest.mark.parametrize("outcome", ["approved", "rejected", "changed", "timed_out"])
def test_each_outcome_is_recorded(spans, outcome):
    gernas.start_hitl(f"t-{outcome}", "submit_pricing_change", approver_role="credit_officer")
    assert spans(SpanName.HITL) == []  # still pending: nothing exported, nothing blocked
    assert gernas.complete_hitl(f"t-{outcome}", outcome, approver_role="credit_officer")
    span = spans(SpanName.HITL)[0]
    a = span.attributes
    assert a[Ctx.HITL_OUTCOME] == outcome and a[Ctx.HITL_ACTION] == "submit_pricing_change"
    assert a[Ctx.HITL_APPROVER_ROLE] == "credit_officer" and a[Ctx.HITL_WAITED_MS] >= 0
    assert a[Ctx.HITL_TICKET_ID] == f"t-{outcome}"


def test_timeout_is_distinguishable_from_rejection(spans):
    gernas.start_hitl("a", "x")
    gernas.start_hitl("b", "x")
    gernas.complete_hitl("a", "timed_out")
    gernas.complete_hitl("b", "rejected")
    timed_out, rejected = spans(SpanName.HITL)
    assert timed_out.status.status_code == StatusCode.ERROR and timed_out.status.description == "hitl.timed_out"
    assert rejected.status.status_code == StatusCode.OK


def test_pending_span_does_not_change_the_callers_context():
    with gernas.get_tracer().start_as_current_span("caller") as caller:
        gernas.start_hitl("ctx", "x")
        from opentelemetry import trace
        assert trace.get_current_span() is caller
    gernas.complete_hitl("ctx", "approved")


def test_unknown_ticket_and_invalid_outcome():
    assert gernas.complete_hitl("never-opened", "approved") is False
    with pytest.raises(ValueError):
        gernas.complete_hitl("x", "maybe")


def test_sweep_closes_expired_approvals_as_timed_out(spans):
    gernas.start_hitl("slow", "x", timeout_s=0.0)
    import time
    time.sleep(0.01)
    assert gernas.sweep_expired() >= 1
    assert "slow" not in pending_hitl()
    assert spans(SpanName.HITL)[0].attributes[Ctx.HITL_OUTCOME] == "timed_out"


def test_hitl_span_is_child_of_the_agent_and_rejection_ends_run_as_human_rejected(spans):
    @gernas.trace_agent("credit_flow")
    def run():
        gernas.start_hitl("t-run", "adjust_credit_limit")
        gernas.complete_hitl("t-run", "rejected")

    run()
    hitl = spans(SpanName.HITL)[0]
    agent = spans(SpanName.AGENT)[0]
    assert hitl.parent.span_id == agent.context.span_id
    assert hitl.attributes[Ctx.AGENT_RUN_ID] == agent.attributes[Ctx.AGENT_RUN_ID]
    assert agent.attributes[Ctx.TERMINATION_REASON] == "human_rejected"


def test_trace_hitl_ticket_mode(spans):
    @gernas.trace_hitl("submit_pricing_change")
    def create_request(change):
        return {"approval_id": "apr-7"}

    create_request({"bps": 10})
    assert "apr-7" in pending_hitl()
    gernas.complete_hitl("apr-7", "approved")
    assert spans(SpanName.HITL)[0].attributes[Ctx.HITL_TICKET_ID] == "apr-7"


async def test_trace_hitl_wait_mode_measures_the_wait(spans):
    @gernas.trace_hitl("approve_payment", outcome_from=lambda ok: "approved" if ok else "rejected")
    async def wait_for_decision(ticket):
        await asyncio.sleep(0.05)
        return True

    @gernas.trace_hitl("approve_payment", outcome_from=lambda ok: "approved")
    async def wait_too_long():
        raise TimeoutError

    assert await wait_for_decision("t") is True
    with pytest.raises(TimeoutError):
        await wait_too_long()
    ok, late = spans(SpanName.HITL)
    assert ok.attributes[Ctx.HITL_OUTCOME] == "approved" and ok.attributes[Ctx.HITL_WAITED_MS] >= 40
    assert late.attributes[Ctx.HITL_OUTCOME] == "timed_out"


def test_human_intervention_rate_is_computable_from_records(spans):
    @gernas.trace_agent("a")
    def run(needs_human):
        if needs_human:
            gernas.start_hitl(f"hir-{id(needs_human)}-{len(spans())}", "x")
            gernas.complete_hitl(f"hir-{id(needs_human)}-{len(spans())}", "approved")

    for flag in (True, False, False, False):
        run(flag)
    rate = len(spans(SpanName.HITL)) / len(spans(SpanName.AGENT))
    assert rate == 0.25


def test_record_prompt_same_template_different_text():
    span = gernas.get_tracer().start_span("p")
    first = gernas.record_prompt(span, "tpl", "1", "Hello")
    second = gernas.record_prompt(span, "tpl", "1", "Hello!")
    span.end()
    assert first != second and len(first) == 64
