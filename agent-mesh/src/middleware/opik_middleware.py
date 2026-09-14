"""Opik context middleware — replaces BeaconKit's TelemetryMiddleware.

Wraps each HTTP request in an Opik span so every trace has a top-level
http-request span with method, path, run_id, and status code attached.
"""
from __future__ import annotations

import opik
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class OpikContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        run_id = request.headers.get("X-Run-ID", "")
        session_id = request.headers.get("X-Session-ID", "")

        opik_tracer = opik.Opik()
        trace = opik_tracer.trace(
            name=f"{request.method} {request.url.path}",
            input={
                "method": request.method,
                "path": request.url.path,
                "run_id": run_id,
                "session_id": session_id,
            },
            tags=["api", "http"],
        )
        try:
            response = await call_next(request)
            trace.update(output={"status_code": response.status_code})
            return response
        except Exception as exc:
            trace.update(output={"error": str(exc)})
            raise
        finally:
            trace.end()
