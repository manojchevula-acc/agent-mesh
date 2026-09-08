"""A2ATraceContextMiddleware — extract inbound W3C trace context on A2A server nodes.

Add this middleware to any Starlette/FastAPI server that receives A2A calls so that
all downstream spans become children of the originating caller's trace.
"""
from __future__ import annotations

from opentelemetry import propagate, trace, baggage, context as otel_context
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


_BAGGAGE_IDENTITY_KEYS = (
    "fab.request_id",
    "fab.user",
    "fab.role",
    "fab.session_id",
    # beacon_kit canonical keys
    "beacon.request_id",
    "beacon.user",
    "beacon.role",
    "beacon.session_id",
)


class A2ATraceContextMiddleware(BaseHTTPMiddleware):
    """Extract W3C traceparent + baggage from inbound A2A request headers.

    Attaches the extracted context so all spans created during the request
    (invoke_agent, tool calls, LLM calls) become children of the caller's trace.
    Also copies known baggage identity keys into the active span as attributes
    for Grafana / Tempo filtering.

    Usage:
        app = FastAPI()
        app.add_middleware(A2ATraceContextMiddleware)
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        carrier = dict(request.headers)
        ctx = propagate.extract(carrier)
        token = otel_context.attach(ctx)
        try:
            response = await call_next(request)
            self._enrich_active_span(request)
            return response
        finally:
            otel_context.detach(token)

    @staticmethod
    def _enrich_active_span(request: Request) -> None:
        span = trace.get_current_span()
        if not span.is_recording():
            return
        for key in _BAGGAGE_IDENTITY_KEYS:
            value = baggage.get_baggage(key)
            if value:
                span.set_attribute(key.replace(".", "_"), str(value))
        # Also record inbound remote IP and path for routing diagnostics
        client = request.client
        if client:
            span.set_attribute("a2a.inbound.client_host", client.host)
        span.set_attribute("a2a.inbound.path", request.url.path)
