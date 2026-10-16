"""Framework adapters: ASGI server middleware, httpx hooks, A2A interceptor, MAF middlewares."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas.adapters.asgi import GernasASGIMiddleware


# ── ASGI ─────────────────────────────────────────────────────────────────────

async def _app(scope, receive, send):
    tracer = gernas.get_tracer()
    with tracer.start_as_current_span("handler"):
        status = 500 if scope["path"] == "/boom" else 200
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})


async def _call(app, path, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


async def test_asgi_middleware_continues_inbound_trace(spans):
    with gernas.get_tracer().start_as_current_span("remote-caller") as caller:
        headers = gernas.inject_context({}, session_id="s-1")
    resp = await _call(GernasASGIMiddleware(_app, channel="a2a"), "/agent", {**headers, "x-run-id": "run-77"})
    assert resp.headers["x-run-id"] == "run-77"
    server = spans("GET /agent")[0]
    handler = spans("handler")[0]
    assert server.context.trace_id == caller.get_span_context().trace_id
    assert handler.parent.span_id == server.context.span_id
    a = server.attributes
    assert a[Ctx.HTTP_STATUS_CODE] == 200 and a[Ctx.HTTP_METHOD] == "GET" and a[Ctx.CHANNEL] == "a2a"
    assert a[Ctx.REQUEST_ID] == "run-77" and a[Ctx.SESSION_ID] == "s-1"


async def test_asgi_middleware_marks_5xx_and_skips_health(spans):
    await _call(GernasASGIMiddleware(_app), "/boom")
    await _call(GernasASGIMiddleware(_app), "/health")
    assert spans("GET /boom")[0].status.status_code == StatusCode.ERROR
    assert spans("GET /health") == []


async def test_asgi_middleware_with_starlette_route_template(spans):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def item(request):
        return PlainTextResponse(request.path_params["item_id"])

    app = Starlette(routes=[Route("/items/{item_id}", item)], middleware=[Middleware(GernasASGIMiddleware)])
    resp = await _call(app, "/items/42")
    assert resp.text == "42"
    span = spans(SpanName.HTTP.format(method="GET", route="/items/{item_id}"))[0]
    assert span.attributes[Ctx.HTTP_ROUTE] == "/items/{item_id}"


# ── httpx hooks ──────────────────────────────────────────────────────────────

async def test_httpx_hooks_inject_traceparent():
    from gernas.adapters.httpx import async_event_hooks, sync_event_hooks
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200)

    with gernas.get_tracer().start_as_current_span("client"):
        with httpx.Client(transport=httpx.MockTransport(handler), event_hooks=sync_event_hooks()) as c:
            c.get("http://peer/x")
        assert "traceparent" in seen
        seen.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks=async_event_hooks()) as c:
            await c.get("http://peer/y")
        assert "traceparent" in seen


# ── A2A interceptor ──────────────────────────────────────────────────────────

async def test_a2a_interceptor_writes_trace_headers():
    from a2a.client.interceptors import BeforeArgs
    from gernas.adapters.a2a import GernasA2AInterceptor

    args = BeforeArgs(input=None, method="send_message", agent_card=None)

    @gernas.trace_agent("caller")
    async def run():
        await GernasA2AInterceptor().before(args)
        return gernas.current_run().agent_run_id

    run_id = await run()
    params = dict(args.context.service_parameters)
    assert params["traceparent"].startswith("00-")
    assert f"{Ctx.PARENT_RUN_ID}={run_id}" in params["baggage"]


# ── Microsoft Agent Framework middlewares ────────────────────────────────────

async def test_maf_middlewares_emit_agent_llm_tool_and_mcp_spans(spans):
    from agent_framework import AgentContext, ChatContext, FunctionInvocationContext
    from gernas.adapters.agent_framework import (
        GernasAgentMiddleware, GernasChatMiddleware, GernasFunctionMiddleware, gernas_middleware,
    )

    assert len(gernas_middleware("x")) == 3
    agent_ctx = AgentContext(agent=SimpleNamespace(name="data_agent"), messages=[])
    chat_ctx = ChatContext(client=SimpleNamespace(model_id="qwen/qwen3.6-27b"), messages=[], options={})
    fn = SimpleNamespace(name="customer_360")

    async def chat_next():
        chat_ctx.result = SimpleNamespace(usage_details={"input_token_count": 12, "output_token_count": 4},
                                          finish_reason="stop", model="qwen/qwen3.6-27b")

    async def tool_next():
        pass

    async def agent_next():
        await GernasChatMiddleware(provider="groq").process(chat_ctx, chat_next)
        for mcp_server in ("datalayer", None):
            fctx = FunctionInvocationContext(function=fn, arguments={"customer_id": "C1"})
            await GernasFunctionMiddleware(mcp_server).process(fctx, tool_next)
        agent_ctx.result = SimpleNamespace(text="answer")

    await GernasAgentMiddleware().process(agent_ctx, agent_next)
    agent = spans(SpanName.AGENT)[0]
    llm = spans(SpanName.LLM)[0]
    mcp = spans(SpanName.MCP)[0]
    tool = spans("tool.customer_360")[0]
    assert agent.attributes[Ctx.AGENT_ID] == "data_agent" and agent.attributes[Ctx.FRAMEWORK] == "agent_framework"
    assert agent.attributes[Ctx.RUN_STEPS] == 3
    assert llm.attributes[Ctx.MODEL_ID] == "qwen/qwen3.6-27b" and llm.attributes[Ctx.PROVIDER] == "groq"
    assert (llm.attributes[Ctx.INPUT_TOKENS], llm.attributes[Ctx.OUTPUT_TOKENS]) == (12, 4)
    assert mcp.attributes[Ctx.MCP_SERVER_ID] == "datalayer" and mcp.attributes[Ctx.MCP_TOOL_NAME] == "customer_360"
    assert mcp.attributes[Ctx.INPUT_HASH] == tool.attributes[Ctx.INPUT_HASH]  # hash of the arguments, not the context
    for child in (llm, mcp, tool):
        assert child.parent.span_id == agent.context.span_id


async def test_maf_agent_middleware_records_failures(spans):
    from agent_framework import AgentContext
    from gernas.adapters.agent_framework import GernasAgentMiddleware

    async def boom():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await GernasAgentMiddleware("rag_agent").process(AgentContext(agent=SimpleNamespace(name="x"), messages=[]), boom)
    span = spans(SpanName.AGENT)[0]
    assert span.attributes[Ctx.TERMINATION_REASON] == "timeout" and span.status.status_code == StatusCode.ERROR


# ── MCP server side ──────────────────────────────────────────────────────────

async def test_fastmcp_middleware_continues_the_callers_trace(spans):
    from gernas.adapters.mcp import GernasFastMCPMiddleware

    with gernas.get_tracer().start_as_current_span("agent-side") as caller:
        meta = gernas.inject_context({})
    message = SimpleNamespace(name="customer_360", arguments={"id": "C1"},
                              meta=SimpleNamespace(model_dump=lambda **k: dict(meta, progressToken=None)))
    context = SimpleNamespace(method="tools/call", message=message)

    async def call_next(ctx):
        return {"ok": True}

    mw = GernasFastMCPMiddleware("datalayer")
    assert await mw(context, call_next) == {"ok": True}
    assert await mw(SimpleNamespace(method="tools/list", message=None), call_next) == {"ok": True}
    span = spans(SpanName.MCP)[0]
    assert span.context.trace_id == caller.get_span_context().trace_id
    assert span.kind.name == "SERVER" and span.attributes[Ctx.MCP_TOOL_NAME] == "customer_360"


def test_mcp_meta_context_for_low_level_sdk(spans):
    from gernas.adapters.mcp import meta_carrier, mcp_meta_context

    with gernas.get_tracer().start_as_current_span("agent") as caller:
        meta = gernas.inject_context({})
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta=meta))
    with mcp_meta_context(ctx):
        gernas.get_tracer().start_span("server-work").end()
    with mcp_meta_context(None):
        pass
    assert spans("server-work")[0].context.trace_id == caller.get_span_context().trace_id
    assert meta_carrier(None) == {} and meta_carrier(42) == {}


async def test_fastmcp_middleware_falls_back_to_mcp_request_ctx(spans):
    from mcp.server.lowlevel.server import request_ctx
    from gernas.adapters.mcp import GernasFastMCPMiddleware

    with gernas.get_tracer().start_as_current_span("agent-side") as caller:
        meta = gernas.inject_context({})
    token = request_ctx.set(SimpleNamespace(meta=SimpleNamespace(model_dump=lambda **k: meta)))
    try:
        context = SimpleNamespace(method="tools/call", message=SimpleNamespace(name="t", arguments={}, meta=None),
                                  fastmcp_context=None)

        async def call_next(ctx):
            return 1

        await GernasFastMCPMiddleware("datalayer")(context, call_next)
    finally:
        request_ctx.reset(token)
    assert spans(SpanName.MCP)[0].context.trace_id == caller.get_span_context().trace_id
