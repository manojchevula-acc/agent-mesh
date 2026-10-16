"""WP1-CORE-02b steps 3-4 and WP1-CORE-05 step 1: streaming span lifecycle."""
from __future__ import annotations

import asyncio
import gc

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName


@gernas.trace_stream("gpt-4o", provider="azure_openai")
def sync_stream(n, fail_at=None):
    for i in range(n):
        if i == fail_at:
            raise RuntimeError("upstream reset")
        yield f"tok{i}"


@gernas.trace_stream("gpt-4o", provider="azure_openai")
async def async_stream(n, fail_at=None):
    for i in range(n):
        if i == fail_at:
            raise RuntimeError("upstream reset")
        await asyncio.sleep(0)
        yield {"delta": f"tok{i}"}
    yield {"usage": {"prompt_tokens": 11, "completion_tokens": n}, "choices": [{"finish_reason": "stop"}]}


def test_complete_sync_stream_ends_ok_with_ttft_and_throughput(spans):
    assert list(sync_stream(5)) == [f"tok{i}" for i in range(5)]
    span = spans(SpanName.STREAM)[0]
    a = span.attributes
    assert span.status.status_code == StatusCode.OK
    assert a[Ctx.STREAM_OUTCOME] == "completed" and a[Ctx.STREAM_CHUNKS] == 5
    assert a[Ctx.TTFT_MS] >= 0 and a[Ctx.TOKENS_PER_SEC] > 0


async def test_complete_async_stream_reads_final_usage_chunk(spans):
    out = [c async for c in async_stream(3)]
    assert len(out) == 4
    a = spans(SpanName.STREAM)[0].attributes
    assert a[Ctx.STREAM_OUTCOME] == "completed"
    assert (a[Ctx.INPUT_TOKENS], a[Ctx.OUTPUT_TOKENS]) == (11, 3)
    assert tuple(a[Ctx.FINISH_REASON]) == ("stop",)
    assert a[Ctx.EST_COST_AED] > 0


def test_abandoned_stream_closes_as_cancelled(spans):
    gen = sync_stream(10)
    next(gen), next(gen)
    gen.close()  # client disconnect
    span = spans(SpanName.STREAM)[0]
    assert span.end_time is not None
    assert span.attributes[Ctx.STREAM_OUTCOME] == "cancelled" and span.attributes[Ctx.STREAM_CHUNKS] == 2
    assert span.status.status_code == StatusCode.UNSET  # not OK, not ERROR
    assert Ctx.TOKENS_PER_SEC not in span.attributes


async def test_abandoned_async_stream_closes_as_cancelled(spans):
    agen = async_stream(10)
    await agen.__anext__()
    await agen.aclose()
    span = spans(SpanName.STREAM)[0]
    assert span.attributes[Ctx.STREAM_OUTCOME] == "cancelled" and span.end_time is not None


def test_error_mid_stream_ends_error(spans):
    with pytest.raises(RuntimeError):
        list(sync_stream(5, fail_at=2))
    span = spans(SpanName.STREAM)[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes[Ctx.STREAM_OUTCOME] == "error" and span.attributes[Ctx.STREAM_CHUNKS] == 2


async def test_error_mid_async_stream_ends_error(spans):
    with pytest.raises(RuntimeError):
        _ = [c async for c in async_stream(5, fail_at=1)]
    assert spans(SpanName.STREAM)[0].status.status_code == StatusCode.ERROR


def test_ttft_absent_when_abandoned_before_first_chunk(spans):
    gen = sync_stream(3)
    next(gen)
    gen.close()
    gen2 = sync_stream(3)
    iter(gen2)
    gen2.close()  # never started: no span at all, nothing left open
    span = spans(SpanName.STREAM)[0]
    assert Ctx.TTFT_MS in span.attributes
    assert len(spans(SpanName.STREAM)) == 1


def test_garbage_collected_stream_still_closes(spans):
    def consume_one():
        gen = sync_stream(10)
        next(gen)

    consume_one()
    gc.collect()
    span = spans(SpanName.STREAM)[0]
    assert span.end_time is not None and span.attributes[Ctx.STREAM_OUTCOME] == "cancelled"


async def test_function_returning_an_async_iterator(spans):
    async def _gen():
        for i in range(3):
            yield i

    @gernas.trace_stream("m", provider="p")
    async def open_stream():
        return _gen()

    stream = await open_stream()
    assert [x async for x in stream] == [0, 1, 2]
    assert spans(SpanName.STREAM)[0].attributes[Ctx.STREAM_CHUNKS] == 3


def test_function_returning_a_non_stream_closes_span(spans):
    @gernas.trace_stream("m")
    def not_a_stream():
        return 42

    assert not_a_stream() == 42
    assert spans(SpanName.STREAM)[0].end_time is not None


def test_child_spans_inside_a_stream_nest_under_it(spans):
    @gernas.trace_tool("inner")
    def inner():
        return 1

    @gernas.trace_stream("m")
    def stream():
        yield inner()
        yield inner()

    list(stream())
    parent = spans(SpanName.STREAM)[0]
    children = spans("tool.inner")
    assert all(c.parent.span_id == parent.context.span_id for c in children)
