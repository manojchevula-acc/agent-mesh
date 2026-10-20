"""Agent runtime KPIs (Blueprint §5): loops, max steps, retries, termination; streaming; HITL."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas.hitl import pending_hitl, sweep_expired

from conftest import attrs

# ── run state ────────────────────────────────────────────────────────────────


def test_loop_detection_on_repeated_identical_tool_calls(spans):
    @gernas.trace_tool("search")
    def search(q):
        return q

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        for _ in range(4):
            search("same")
        search("different")

    run()
    a = attrs(spans(SpanName.AGENT)[0])
    assert a[Ctx.LOOP_DETECTED] is True and a[Ctx.LOOP_REPEAT_COUNT] == 4 and a[Ctx.STEP_COUNT] == 5


def test_max_step_violation_terminates_with_error_status(spans):
    from gernas.tracer import get_config

    cfg = get_config()
    original, cfg.max_steps = cfg.max_steps, 2

    @gernas.trace_tool("t")
    def tool(i):
        return i

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        for i in range(3):
            tool(i)

    try:
        run()
    finally:
        cfg.max_steps = original
    span = spans(SpanName.AGENT)[0]
    a = attrs(span)
    assert a[Ctx.MAX_STEP_VIOLATION] is True and a[Ctx.TERMINATION_REASON] == "max_steps"
    assert span.status.status_code == StatusCode.ERROR


def test_retries_and_termination_override(spans):
    @gernas.trace_agent("a", agent_type="worker")
    def run():
        gernas.record_retry()
        gernas.record_retry(2)
        gernas.set_termination_reason("human_rejected")

    run()
    a = attrs(spans(SpanName.AGENT)[0])
    assert a[Ctx.RETRY_COUNT] == 3 and a[Ctx.TERMINATION_REASON] == "human_rejected"
    with pytest.raises(ValueError):

        @gernas.trace_agent("b", agent_type="worker")
        def bad():
            gernas.set_termination_reason("made_up")

        bad()


def test_helpers_are_noops_outside_a_run():
    gernas.record_retry()
    gernas.set_termination_reason("completed")


# ── streaming ────────────────────────────────────────────────────────────────


class Chunk:
    def __init__(self, usage=None, finish=None):
        self.usage = usage
        self.finish_reason = finish


def test_sync_stream_ttft_tokens_and_stop_reason(spans):
    @gernas.trace_llm("gpt-4o", provider="azure_openai")
    def stream():
        yield Chunk()
        yield Chunk()
        yield Chunk(usage={"prompt_tokens": 20, "completion_tokens": 8}, finish="stop")

    assert len(list(stream())) == 3
    a = attrs(spans(SpanName.LLM)[0])
    assert a[Ctx.TTFT_MS] >= 0 and a[Ctx.TOKEN_INPUT] == 20 and a[Ctx.TOKEN_OUTPUT] == 8
    assert a[Ctx.STOP_REASON] == "stop" and a[Ctx.TOKENS_PER_SECOND] > 0 and a[Ctx.EXECUTION_STATUS] == "ok"
    assert a[Ctx.ESTIMATED_COST] > 0


async def test_async_stream_inside_run_counts_one_step(spans):
    @gernas.trace_llm("m", provider="p")
    async def stream():
        for _ in range(3):
            await asyncio.sleep(0)
            yield Chunk()

    @gernas.trace_agent("a", agent_type="worker")
    async def run():
        return [c async for c in stream()]

    assert len(await run()) == 3
    llm, agent = spans(SpanName.LLM)[0], spans(SpanName.AGENT)[0]
    assert llm.parent.span_id == agent.context.span_id
    assert attrs(llm)[Ctx.AGENT_RUN_ID] == attrs(agent)[Ctx.AGENT_RUN_ID] and attrs(agent)[Ctx.STEP_COUNT] == 1


def test_abandoned_stream_is_cancelled_not_error(spans):
    @gernas.trace_llm("m", provider="p")
    def stream():
        for _ in range(10):
            yield Chunk()

    gen = stream()
    next(gen)
    gen.close()
    span = spans(SpanName.LLM)[0]
    assert attrs(span)[Ctx.EXECUTION_STATUS] == "cancelled" and span.status.status_code == StatusCode.UNSET


def test_function_returning_an_iterator(spans):
    @gernas.trace_llm("m", provider="p", stream=True)
    def start():
        return iter([Chunk(), Chunk(usage={"prompt_tokens": 1, "completion_tokens": 2})])

    assert len(list(start())) == 2
    assert attrs(spans(SpanName.LLM)[0])[Ctx.TOKEN_OUTPUT] == 2


def test_stream_error_is_recorded(spans):
    @gernas.trace_llm("m", provider="p")
    def stream():
        yield Chunk()
        raise ConnectionError("dropped")

    with pytest.raises(ConnectionError):
        list(stream())
    span = spans(SpanName.LLM)[0]
    assert span.status.status_code == StatusCode.ERROR and attrs(span)[Ctx.ERROR_CATEGORY] == "dependency"


# ── HITL ─────────────────────────────────────────────────────────────────────


def test_hitl_ticket_lifecycle_without_holding_context(spans):
    from opentelemetry import trace

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        before = trace.get_current_span()
        gernas.start_hitl("T-1", "approve_credit", approver_role="credit_officer")
        assert trace.get_current_span() is before
        assert "T-1" in pending_hitl()
        assert gernas.complete_hitl("T-1", "rejected")

    run()
    h, agent = spans(SpanName.HITL)[0], spans(SpanName.AGENT)[0]
    a = attrs(h)
    assert a[Ctx.HITL_TICKET_ID] == "T-1" and a[Ctx.HITL_ACTION] == "approve_credit"
    assert a[Ctx.HITL_OUTCOME] == "rejected" and a[Ctx.HITL_APPROVER_ROLE] == "credit_officer"
    assert a[Ctx.AGENT_RUN_ID] == attrs(agent)[Ctx.AGENT_RUN_ID] and h.parent.span_id == agent.context.span_id
    assert h.status.status_code == StatusCode.OK
    assert attrs(agent)[Ctx.TERMINATION_REASON] == "human_rejected"
    assert not gernas.complete_hitl("T-1", "approved")  # already closed
    with pytest.raises(ValueError):
        gernas.complete_hitl("T-x", "maybe")


def test_hitl_timeout_is_distinct_from_rejection(spans):
    gernas.start_hitl("T-2", "approve", timeout_s=0.0)
    import time

    assert sweep_expired(time.monotonic() + 1) == 1
    span = spans(SpanName.HITL)[0]
    a = attrs(span)
    assert a[Ctx.HITL_OUTCOME] == "timed_out" and a[Ctx.ERROR_CATEGORY] == "timeout"
    assert span.status.status_code == StatusCode.ERROR


async def test_trace_hitl_wait_mode(spans):
    @gernas.trace_hitl("approve_transfer", outcome_from=lambda r: "approved" if r else "rejected")
    async def wait_for_decision():
        await asyncio.sleep(0)
        return True

    assert await wait_for_decision() is True
    assert attrs(spans(SpanName.HITL)[0])[Ctx.HITL_OUTCOME] == "approved"


def test_trace_hitl_ticket_mode(spans):
    @gernas.trace_hitl("approve", ticket_from=lambda r: r["ticket"])
    def request():
        return {"ticket": "T-9"}

    request()
    assert "T-9" in pending_hitl()
    gernas.complete_hitl("T-9", "approved")
    assert attrs(spans(SpanName.HITL)[0])[Ctx.HITL_TICKET_ID] == "T-9"
