"""A2A hosting helpers.

Wraps a LangGraph CompiledGraph as an isolated HTTP server so the node can be
reached by other agents over the network.  Exposes two endpoints:

    GET  /health  — liveness check (status, uptime, model)
    POST /invoke  — accepts {"message": "..."} and returns {"text": "..."}

The ``TraceContextMiddleware`` continues the caller's distributed trace on every
inbound request so all spans inside the node become children of the caller's span.
"""
import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import time as _time

import uvicorn
from langchain_core.messages import HumanMessage
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.config import Config


class TraceContextMiddleware(BaseHTTPMiddleware):
    """Continues the caller's distributed trace on inbound requests.

    Extracts W3C ``traceparent`` / ``tracestate`` (injected by the client in
    ``src/a2a/clients.py``) from the request headers and attaches the resulting
    OpenTelemetry context for the duration of the request.  This makes every span
    the node emits a child of the orchestrator's span, yielding one coherent
    end-to-end distributed trace.

    Safe no-op when OpenTelemetry is not installed/configured.
    """

    async def dispatch(self, request, call_next):
        token = None
        try:
            from opentelemetry import context as otel_context, baggage, trace
            from opentelemetry.propagate import extract

            # Extract both W3C traceparent AND baggage from inbound headers.
            # The composite propagator (set by _ensure_composite_propagator) handles both.
            ctx = extract(dict(request.headers))
            token = otel_context.attach(ctx)

            # Enrich the active span with the caller's identity from propagated baggage
            # so remote spans are queryable by request_id / user / role in Grafana Tempo.
            try:
                span = trace.get_current_span()
                if span and span.is_recording():
                    for baggage_key, span_attr in (
                        ("fab.request_id", "fab.request_id"),
                        ("fab.user",       "fab.inbound.user"),
                        ("fab.role",       "fab.inbound.role"),
                        ("fab.session_id", "fab.inbound.session_id"),
                    ):
                        val = baggage.get_baggage(baggage_key, context=ctx)
                        if val:
                            span.set_attribute(span_attr, val)
            except Exception:
                pass
        except Exception:
            token = None
        try:
            return await call_next(request)
        finally:
            if token is not None:
                try:
                    from opentelemetry import context as otel_context
                    otel_context.detach(token)
                except Exception:
                    pass


def build_starlette_app(
    agent,
    card_name: str,
    card_description: str,
    port: int,
) -> Starlette:
    """Wraps a LangGraph CompiledGraph into a Starlette HTTP application.

    Installs ``TraceContextMiddleware`` so inbound calls continue the caller's
    distributed trace.  Adds a ``GET /health`` endpoint for liveness checks.
    """
    _start_time = _time.time()

    async def invoke(request: Request) -> JSONResponse:
        body = await request.json()
        message = body.get("message", "")
        result = await agent.ainvoke({"messages": [HumanMessage(content=message)]})
        text = result["messages"][-1].content
        return JSONResponse({"text": text})

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "status":         "ok",
            "node":           card_name,
            "uptime_seconds": round(_time.time() - _start_time, 1),
            "model":          getattr(Config, "GROQ_MODEL", "unknown"),
        })

    return Starlette(
        middleware=[Middleware(TraceContextMiddleware)],
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/invoke",  invoke,  methods=["POST"]),
        ],
    )


def serve(
    agent,
    card_name: str,
    card_description: str,
    port: int,
) -> None:
    """Blocks serving the agent as an HTTP server on the given port."""
    app = build_starlette_app(agent, card_name, card_description, port)
    uvicorn.run(app, host=Config.A2A_HOST, port=port, log_level="warning")
