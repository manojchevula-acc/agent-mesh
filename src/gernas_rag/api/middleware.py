"""CORS, request ID, and structured logging middleware."""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..utils.logging import get_logger

logger = get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attaches a request id to every request and binds it to the log context."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER, str(uuid.uuid4()))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Logs the start and end of every request with latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        logger.info("Request started", method=request.method, path=request.url.path)
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "Request completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        return response
