"""Span lifecycle edge cases: awaitables returned by non-async callables, per-task context, exporter failure."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas import tracer as gernas_tracer

from conftest import attrs


def _duration_ms(span) -> float:
    return (span.end_time - span.start_time) / 1e6


async def test_async_callable_object_span_covers_the_await_and_records_the_error(spans):
    class Tool:
        async def __call__(self, x):
            await asyncio.sleep(0.03)
            raise ValueError("boom")

    tool = gernas.trace_tool("async_callable")(Tool())
    with pytest.raises(ValueError):
        await tool(1)
    span = spans(SpanName.TOOL)[0]
    assert _duration_ms(span) >= 25
    assert span.status.status_code == StatusCode.ERROR
    assert attrs(span)[Ctx.EXECUTION_STATUS] == "error"
    assert any(e.name == "exception" for e in span.events)


async def test_sync_function_returning_a_coroutine_keeps_the_span_open_and_parents_children(spans):
    @gernas.trace_llm("m", provider="p")
    async def llm():
        await asyncio.sleep(0.03)
        return None

    async def work(x):
        await llm()
        return x

    @gernas.trace_tool("returns_coro")
    def tool(x):
        return work(x)  # not awaited here: the work runs when the caller awaits

    assert await tool(7) == 7
    tool_span = spans(SpanName.TOOL)[0]
    llm_span = spans(SpanName.LLM)[0]
    assert _duration_ms(tool_span) >= 25
    assert llm_span.parent.span_id == tool_span.context.span_id
    assert tool_span.status.status_code == StatusCode.OK


async def test_sync_function_returning_a_task_is_not_rewrapped(spans):
    async def work():
        return 1

    @gernas.trace_tool("returns_task")
    def tool():
        return asyncio.ensure_future(work())

    task = tool()
    assert isinstance(task, asyncio.Future)  # business return type preserved
    assert await task == 1


async def test_request_context_baggage_is_isolated_per_concurrent_task(spans):
    @gernas.trace_agent("a", agent_type="worker")
    async def run(sid):
        with gernas.request_context(session_id=sid):
            await asyncio.sleep(0.01)
            return gernas.correlation()["session_id"]

    sids = [f"s{i}" for i in range(8)]
    assert await asyncio.gather(*(run(s) for s in sids)) == sids


def test_an_exporter_that_raises_never_reaches_the_caller_and_is_counted():
    class Broken:
        def export(self, batch):
            raise RuntimeError("backend down")

        def shutdown(self):
            raise RuntimeError("still down")

        def force_flush(self, timeout_millis=0):
            raise RuntimeError("still down")

    exporter = gernas_tracer.CountingSpanExporter(Broken())
    before = gernas_tracer.dropped_spans.value
    assert exporter.export([object(), object()]) is SpanExportResult.FAILURE
    assert gernas_tracer.dropped_spans.value == before + 2
    exporter.shutdown()
    assert exporter.force_flush() is False
