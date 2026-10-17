"""Transport adapters: ASGI server extraction, httpx injection, A2A interceptor, MCP server context."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas.tracer import get_tracer

from conftest import attrs

httpx = pytest.importorskip("httpx")

from gernas.adapters.asgi import GernasASGIMiddleware  # noqa: E402


async def _app(scope, receive, send):
    with get_tracer().start_as_current_span("handler"):
        status = 500 if scope["path"] == "/boom" else 200
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _call(app, path, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


async def test_asgi_continues_the_inbound_trace_and_baggage(spans):
    with get_tracer().start_as_current_span("remote-caller") as caller:
        with gernas.request_context(session_id="s-1"):
            headers = gernas.inject_context({})
    await _call(GernasASGIMiddleware(_app, channel="a2a"), "/agent", headers)
    server, handler = spans("GET /agent")[0], spans("handler")[0]
    assert server.context.trace_id == caller.get_span_context().trace_id
    assert handler.parent.span_id == server.context.span_id
    a = attrs(server)
    assert a[Ctx.HTTP_STATUS_CODE] == 200 and a[Ctx.HTTP_METHOD] == "GET" and a[Ctx.CHANNEL] == "a2a"
    assert a[Ctx.SESSION_ID] == "s-1" and a[Ctx.OPERATION] == SpanName.HTTP and a[Ctx.EXECUTION_STATUS] == "ok"


async def test_asgi_marks_5xx_and_skips_health(spans):
    await _call(GernasASGIMiddleware(_app), "/boom")
    await _call(GernasASGIMiddleware(_app), "/health")
    boom = spans("GET /boom")[0]
    assert boom.status.status_code == StatusCode.ERROR and attrs(boom)[Ctx.EXECUTION_STATUS] == "error"
    assert spans("GET /health") == []


async def test_asgi_uses_the_route_template(spans):
    starlette = pytest.importorskip("starlette")  # noqa: F841
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def item(request):
        return PlainTextResponse(request.path_params["item_id"])

    app = Starlette(routes=[Route("/items/{item_id}", item)], middleware=[Middleware(GernasASGIMiddleware)])
    assert (await _call(app, "/items/42")).text == "42"
    span = spans("GET /items/{item_id}")[0]
    assert attrs(span)[Ctx.HTTP_ROUTE] == "/items/{item_id}"


async def test_httpx_hooks_inject_traceparent():
    from gernas.adapters.httpx import async_event_hooks, sync_event_hooks

    seen: dict = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200)

    with get_tracer().start_as_current_span("client"):
        with httpx.Client(transport=httpx.MockTransport(handler), event_hooks=sync_event_hooks()) as c:
            c.get("http://peer/x")
        assert "traceparent" in seen
        seen.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks=async_event_hooks()) as c:
            await c.get("http://peer/y")
        assert "traceparent" in seen


async def test_a2a_interceptor_writes_trace_and_parent_run_headers():
    pytest.importorskip("a2a")
    from a2a.client.interceptors import BeforeArgs

    from gernas.adapters.a2a import GernasA2AInterceptor
    from gernas.run_state import current_run

    args = BeforeArgs(input=None, method="send_message", agent_card=None)

    @gernas.trace_agent("caller", agent_type="orchestrator")
    async def run():
        await GernasA2AInterceptor().before(args)
        return current_run().agent_run_id

    run_id = await run()
    params = dict(args.context.service_parameters)
    assert params["traceparent"].startswith("00-")
    assert f"{Ctx.PARENT_RUN_ID}={run_id}" in params["baggage"]


async def test_fastmcp_middleware_server_side_tool_span(spans):
    from gernas.adapters.mcp import GernasFastMCPMiddleware

    with get_tracer().start_as_current_span("agent-side") as caller:
        meta = gernas.inject_context({})
    message = SimpleNamespace(
        name="customer_360",
        arguments={"id": "C1"},
        meta=SimpleNamespace(model_dump=lambda **k: dict(meta, progressToken=None)),
    )

    async def call_next(ctx):
        return {"ok": True}

    mw = GernasFastMCPMiddleware("datalayer")
    assert await mw(SimpleNamespace(method="tools/call", message=message), call_next) == {"ok": True}
    assert await mw(SimpleNamespace(method="tools/list", message=None), call_next) == {"ok": True}
    span = spans(SpanName.TOOL)[0]
    a = attrs(span)
    assert span.context.trace_id == caller.get_span_context().trace_id and span.kind.name == "SERVER"
    assert a[Ctx.TOOL_ID] == "customer_360" and a[Ctx.TOOL_TYPE] == "mcp" and a[Ctx.DEPENDENCY] == "datalayer"
    assert a[Ctx.REQUEST_SIZE_BYTES] > 0 and "C1" not in str(a)


def test_mcp_meta_context_for_low_level_sdk(spans):
    from gernas.adapters.mcp import mcp_meta_context, meta_carrier

    with get_tracer().start_as_current_span("agent") as caller:
        meta = gernas.inject_context({})
    with mcp_meta_context(SimpleNamespace(request_context=SimpleNamespace(meta=meta))):
        get_tracer().start_span("server-work").end()
    with mcp_meta_context(None):
        pass
    assert spans("server-work")[0].context.trace_id == caller.get_span_context().trace_id
    assert meta_carrier(None) == {} and meta_carrier(42) == {}


async def test_fastmcp_middleware_falls_back_to_mcp_request_ctx(spans):
    pytest.importorskip("mcp")
    from mcp.server.lowlevel.server import request_ctx

    from gernas.adapters.mcp import GernasFastMCPMiddleware

    with get_tracer().start_as_current_span("agent-side") as caller:
        meta = gernas.inject_context({})
    token = request_ctx.set(SimpleNamespace(meta=SimpleNamespace(model_dump=lambda **k: meta)))
    try:
        context = SimpleNamespace(
            method="tools/call", message=SimpleNamespace(name="t", arguments={}, meta=None), fastmcp_context=None
        )

        async def call_next(ctx):
            return 1

        await GernasFastMCPMiddleware("datalayer")(context, call_next)
    finally:
        request_ctx.reset(token)
    assert spans(SpanName.TOOL)[0].context.trace_id == caller.get_span_context().trace_id
