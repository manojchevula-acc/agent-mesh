"""WP1-CORE-05 steps 2-3 and WP1-CTX-03: tasks, queues, processes, orphans."""
from __future__ import annotations

import asyncio
import json
import queue
import threading

import gernas
from gernas import Ctx, SpanName

from conftest import run_python


async def test_gernas_task_keeps_the_trace(spans):
    tracer = gernas.get_tracer()

    async def background():
        with tracer.start_as_current_span("background"):
            await asyncio.sleep(0)

    with tracer.start_as_current_span("request") as req:
        task = gernas.gernas_task(background(), name="bg")
    await task
    assert spans("background")[0].context.trace_id == req.get_span_context().trace_id


async def test_to_thread_keeps_the_trace(spans):
    tracer = gernas.get_tracer()
    with tracer.start_as_current_span("owner") as owner:
        await gernas.to_thread(lambda: tracer.start_span("threaded").end())
    assert spans("threaded")[0].context.trace_id == owner.get_span_context().trace_id


def test_queue_hand_off_produces_one_connected_record(spans):
    q: queue.Queue = queue.Queue()

    @gernas.trace_agent("producer")
    def produce():
        q.put({"headers": gernas.inject_context({}, session_id="sess-q"), "body": "job"})

    @gernas.trace_agent("consumer")
    def consume(msg):
        return gernas.current_run()

    def worker():
        msg = q.get()
        with gernas.attach_headers(msg["headers"]):
            consume(msg)

    produce()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    by = {s.attributes[Ctx.AGENT_ID]: s for s in spans(SpanName.AGENT)}
    assert by["consumer"].context.trace_id == by["producer"].context.trace_id
    assert by["consumer"].attributes[Ctx.PARENT_RUN_ID] == by["producer"].attributes[Ctx.AGENT_RUN_ID]
    assert by["consumer"].attributes[Ctx.SESSION_ID] == "sess-q"  # session id survives the queue hop
    assert by["consumer"].attributes[Ctx.AGENT_RUN_ID] != by["producer"].attributes[Ctx.AGENT_RUN_ID]
    assert Ctx.ORPHAN_DETECTED not in by["consumer"].attributes


def test_cross_process_propagation(spans, tmp_path):
    @gernas.trace_agent("caller")
    def call():
        return gernas.inject_context({})

    headers = call()
    caller = spans(SpanName.AGENT)[0]
    out = tmp_path / "child.json"
    hdr = tmp_path / "headers.json"
    hdr.write_text(json.dumps(headers))
    code = f"""
import json, gernas
from gernas.config import GernasConfig
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
exp = InMemorySpanExporter()
gernas.init_gernas(GernasConfig(exporter="memory", service_name="child"), span_exporter=exp)

@gernas.trace_agent("remote_child")
def child():
    return 1

with gernas.attach_headers(json.load(open({str(hdr)!r}))):
    child()
s = exp.get_finished_spans()[0]
json.dump({{"trace_id": format(s.context.trace_id, "032x"), "parent_span": format(s.parent.span_id, "016x"),
           "parent_run_id": s.attributes.get("{Ctx.PARENT_RUN_ID}"),
           "orphan": s.attributes.get("{Ctx.ORPHAN_DETECTED}")}}, open({str(out)!r}, "w"))
"""
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    child = json.loads(out.read_text())
    assert child["trace_id"] == format(caller.context.trace_id, "032x")
    assert child["parent_span"] == format(caller.context.span_id, "016x")
    assert child["parent_run_id"] == caller.attributes[Ctx.AGENT_RUN_ID]
    assert child["orphan"] is None


def test_broken_chain_is_detected_as_orphan(spans):
    # baggage arrived (parent_run_id) but the traceparent was stripped in transit
    headers = {"baggage": f"{Ctx.PARENT_RUN_ID}=run_lost"}

    @gernas.trace_agent("orphaned")
    def child():
        return 1

    with gernas.attach_headers(headers):
        child()
    a = spans(SpanName.AGENT)[0].attributes
    assert a[Ctx.ORPHAN_DETECTED] is True and a[Ctx.PARENT_RUN_ID] == "run_lost"


def test_explicit_unknown_parent_is_an_orphan(spans):
    @gernas.trace_agent("x", parent_run_id="run_nobody")
    def child():
        return 1

    child()
    assert spans(SpanName.AGENT)[0].attributes[Ctx.ORPHAN_DETECTED] is True
