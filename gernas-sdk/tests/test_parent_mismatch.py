"""WP1-CORE-05 step 4: wrong-parent detection — must run on every build.

If this fails, a decorator is leaking context and independent runs are being
stitched into one trace.
"""
from __future__ import annotations

import asyncio

import gernas
from gernas import SpanName


def test_two_independent_agent_runs_have_different_trace_ids(spans):
    @gernas.trace_agent("independent")
    def run():
        return 1

    run()
    run()
    first, second = spans(SpanName.AGENT)
    assert first.context.trace_id != second.context.trace_id
    assert first.parent is None and second.parent is None


async def test_concurrent_async_runs_do_not_share_a_parent(spans):
    @gernas.trace_agent("concurrent")
    async def run(i):
        await asyncio.sleep(0.001 * (3 - i))
        return i

    await asyncio.gather(*(run(i) for i in range(3)))
    agents = spans(SpanName.AGENT)
    assert len({s.context.trace_id for s in agents}) == 3


def test_generator_does_not_leak_context_after_close(spans):
    @gernas.trace_stream("m")
    def stream():
        yield 1
        yield 2

    gen = stream()
    next(gen)
    gen.close()

    @gernas.trace_agent("after")
    def run():
        return 1

    run()
    assert spans(SpanName.AGENT)[0].parent is None
