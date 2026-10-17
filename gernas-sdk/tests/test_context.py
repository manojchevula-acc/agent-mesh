"""Context propagation (W3C traceparent + baggage), request identity, background tasks and threads."""

from __future__ import annotations

import asyncio
import threading

import gernas
from gernas import Ctx, SpanName
from gernas.context import tokenize_user_id

from conftest import attrs


def test_request_context_stamps_identity_and_tokenizes_the_user(spans):
    @gernas.trace_agent("a", agent_type="worker")
    def run():
        return 1

    with gernas.request_context(
        session_id="s-1",
        task_id="t-1",
        channel="web",
        user_id="alice",
        principal_type="human",
        tenant="fab",
        department="retail",
    ):
        run()
    a = attrs(spans(SpanName.AGENT)[0])
    assert a[Ctx.SESSION_ID] == "s-1" and a[Ctx.TASK_ID] == "t-1" and a[Ctx.CHANNEL] == "web"
    assert a[Ctx.PRINCIPAL_TYPE] == "human" and a[Ctx.TENANT] == "fab" and a[Ctx.DEPARTMENT] == "retail"
    assert a[Ctx.TOKENIZED_USER_ID] == tokenize_user_id("alice", "test-token-key")
    assert "alice" not in [str(v) for v in a.values()]
    assert Ctx.USER_ID not in a


def test_user_id_is_not_recorded_without_a_token_key(spans, caplog):
    from gernas.tracer import get_config

    cfg = get_config()
    original, cfg.user_token_key = cfg.user_token_key, ""

    @gernas.trace_tool("t")
    def tool():
        return 1

    try:
        with gernas.request_context(user_id="alice"):
            tool()
    finally:
        cfg.user_token_key = original
    a = attrs(spans(SpanName.TOOL)[0])
    assert Ctx.TOKENIZED_USER_ID not in a and "alice" not in [str(v) for v in a.values()]


def test_cross_process_hop_continues_the_trace_and_links_runs(spans):
    carrier: dict = {}

    @gernas.trace_delegation("remote_agent", headers_arg="headers")
    def call_remote(headers):
        return headers

    @gernas.trace_agent("caller", agent_type="orchestrator")
    def caller():
        with gernas.request_context(session_id="s-9"):
            call_remote(headers=carrier)

    caller()
    assert "traceparent" in carrier and "baggage" in carrier

    # the "remote" side: a fresh context rebuilt from headers only
    from opentelemetry import context as otel_context

    token = otel_context.attach(otel_context.Context())
    try:

        @gernas.trace_agent("remote_agent", agent_type="worker")
        def remote():
            return 1

        with gernas.attach_headers(carrier):
            remote()
    finally:
        otel_context.detach(token)
    caller_span = spans(SpanName.AGENT)[0]
    remote_span = spans(SpanName.AGENT)[1]
    delegate = spans(SpanName.DELEGATION)[0]
    assert remote_span.context.trace_id == caller_span.context.trace_id
    assert remote_span.parent.span_id == delegate.context.span_id
    r = attrs(remote_span)
    assert r[Ctx.PARENT_RUN_ID] == attrs(caller_span)[Ctx.AGENT_RUN_ID]
    assert r[Ctx.DELEGATION_PATH] == "caller>remote_agent" and r[Ctx.SESSION_ID] == "s-9"


def test_inject_and_extract_are_case_insensitive():
    with gernas.request_context(session_id="s"):
        headers = gernas.inject_context()
    upper = {k.upper(): v for k, v in headers.items()}
    from opentelemetry import baggage

    assert baggage.get_baggage(Ctx.SESSION_ID, gernas.extract_context(upper)) == "s"


async def test_gernas_task_keeps_the_trace_and_run(spans):
    @gernas.trace_memory("write")
    async def summarise(text):
        await asyncio.sleep(0)

    @gernas.trace_agent("a", agent_type="worker")
    async def run():
        return gernas.gernas_task(summarise("x"))

    task = await run()
    await task
    mem, agent = spans(SpanName.MEMORY)[0], spans(SpanName.AGENT)[0]
    assert mem.context.trace_id == agent.context.trace_id and mem.parent.span_id == agent.context.span_id
    assert attrs(mem)[Ctx.AGENT_RUN_ID] == attrs(agent)[Ctx.AGENT_RUN_ID]


def test_wrap_in_context_for_threads(spans):
    @gernas.trace_tool("worker_tool")
    def tool():
        return 1

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        t = threading.Thread(target=gernas.wrap_in_context(tool))
        t.start()
        t.join()

    run()
    tool_span, agent = spans(SpanName.TOOL)[0], spans(SpanName.AGENT)[0]
    assert tool_span.parent.span_id == agent.context.span_id
    assert attrs(tool_span)[Ctx.AGENT_RUN_ID] == attrs(agent)[Ctx.AGENT_RUN_ID]


def test_unknown_baggage_is_not_copied_to_spans(spans):
    from opentelemetry import baggage, context

    @gernas.trace_tool("t")
    def tool():
        return 1

    token = context.attach(baggage.set_baggage("customer_email", "jane@example.com"))
    try:
        tool()
    finally:
        context.detach(token)
    assert "customer_email" not in attrs(spans(SpanName.TOOL)[0])


def test_unregistered_principal_type_is_not_recorded(spans):
    @gernas.trace_tool("t")
    def tool():
        return 1

    with gernas.request_context(principal_type="employee"):
        tool()
    assert Ctx.PRINCIPAL_TYPE not in attrs(spans(SpanName.TOOL)[0])
