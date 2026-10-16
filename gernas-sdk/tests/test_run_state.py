"""WP1-CORE-03: loop detection, runaway steps, retry counting, timeout differentiation."""
from __future__ import annotations

import asyncio

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName


@gernas.trace_tool("search")
def search(q):
    return [q]


@gernas.trace_tool("fetch")
def fetch(q):
    return q


def test_same_tool_same_args_three_times_flags_a_loop(spans):
    @gernas.trace_agent("looper")
    def run():
        for _ in range(3):
            search("same")

    run()
    a = spans(SpanName.AGENT)[0].attributes
    assert a[Ctx.LOOP_DETECTED] is True and a[Ctx.LOOP_REPEAT_COUNT] == 3


def test_different_args_do_not_flag_a_loop(spans):
    @gernas.trace_agent("explorer")
    def run():
        for q in ("a", "b", "c", "d"):
            search(q)

    run()
    assert Ctx.LOOP_DETECTED not in spans(SpanName.AGENT)[0].attributes


def test_runaway_agent_is_flagged_and_marked_failed(spans, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "max_steps", 4)

    @gernas.trace_agent("runaway")
    def run():
        for i in range(6):
            fetch(i)
        return "done"

    assert run() == "done"  # telemetry never stops the agent
    span = spans(SpanName.AGENT)[0]
    assert span.attributes[Ctx.MAX_STEP_VIOLATION] is True
    assert span.attributes[Ctx.TERMINATION_REASON] == "max_steps"
    assert span.attributes[Ctx.RUN_STEPS] == 6
    assert span.status.status_code == StatusCode.ERROR and span.status.description == "max_steps"


def test_retries_are_counted_across_the_whole_run(spans):
    @gernas.trace_tool("t1")
    def t1():
        gernas.record_retry()

    @gernas.trace_tool("t2")
    def t2():
        gernas.record_retry(2)

    @gernas.trace_tool("t3")
    def t3():
        gernas.record_retry(2)

    @gernas.trace_agent("retrier")
    def run():
        t1(), t2(), t3()

    run()
    assert spans(SpanName.AGENT)[0].attributes[Ctx.RETRY_COUNT] == 5


@pytest.mark.parametrize("exc,reason,is_error", [
    (asyncio.TimeoutError(), "timeout", True),
    (TimeoutError(), "timeout", True),
    (ValueError(), "error", True),
    (asyncio.CancelledError(), "abandoned", False),
])
async def test_termination_reason_per_exit(spans, exc, reason, is_error):
    @gernas.trace_agent("x")
    async def run():
        raise exc

    with pytest.raises(type(exc)):
        await run()
    span = spans(SpanName.AGENT)[0]
    assert span.attributes[Ctx.TERMINATION_REASON] == reason
    assert (span.status.status_code == StatusCode.ERROR) is is_error
    if reason == "timeout":
        assert span.attributes[Ctx.TIMED_OUT] is True


async def test_slow_but_completing_run_has_no_timeout_flag(spans):
    @gernas.trace_agent("slow")
    async def run():
        await asyncio.sleep(0.02)
        return 1

    await run()
    a = spans(SpanName.AGENT)[0].attributes
    assert a[Ctx.TERMINATION_REASON] == "completed" and Ctx.TIMED_OUT not in a


def test_explicit_termination_override(spans):
    @gernas.trace_agent("gate")
    def run():
        gernas.set_termination_reason("policy_block")
        return "blocked"

    run()
    assert spans(SpanName.AGENT)[0].attributes[Ctx.TERMINATION_REASON] == "policy_block"
    with pytest.raises(ValueError):
        gernas.current_run() or gernas.run_state.RunState("x").set_termination("bogus")


def test_run_state_is_per_invocation_not_global():
    seen = []

    @gernas.trace_agent("iso")
    def run():
        seen.append(gernas.current_run())

    run(), run()
    assert seen[0] is not seen[1]
    assert gernas.current_run() is None
    gernas.record_retry()  # no-op outside a run
    gernas.record_step()
