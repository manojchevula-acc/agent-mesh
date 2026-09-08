"""TelemetryMiddleware — Starlette BaseHTTPMiddleware that enriches the active span.

Must be added AFTER FastAPIInstrumentor has already extracted the traceparent header,
so that the span it enriches already has the parent context applied.
"""
from __future__ import annotations

import uuid
from typing import Callable

from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Enriches the active span created by FastAPIInstrumentor.

    Adds:
    - ``service.run_id``: from ``run_id_header`` request header, or auto-generated UUID
    - ``http.route``: URL path
    - ``x-run-id``: echoed back in the response for client correlation

    Falls back to ``X-Service-Run-ID`` when ``traceparent`` is stripped by a gateway.
    """

    def __init__(
        self,
        app: ASGIApp,
        run_id_header: str = "X-Run-ID",
        service_name: str = "",
        pod: str = "",
    ) -> None:
        super().__init__(app)
        self._run_id_header = run_id_header.lower()
        self._service_name = service_name
        self._pod = pod

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        run_id = (
            request.headers.get(self._run_id_header)
            or request.headers.get("x-service-run-id")
            or str(uuid.uuid4())
        )

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("service.run_id", run_id)
            span.set_attribute("http.route", request.url.path)
            if self._service_name:
                span.set_attribute("service.name", self._service_name)
            if self._pod:
                span.set_attribute("service.pod", self._pod)

        response = await call_next(request)
        response.headers["x-run-id"] = run_id
        return response
