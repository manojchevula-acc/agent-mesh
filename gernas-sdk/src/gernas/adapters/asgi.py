"""Pure-ASGI server middleware: inbound context extraction + one SERVER span per request.

Works with Starlette, FastAPI and any ASGI app without depending on them, and —
unlike ``BaseHTTPMiddleware`` — keeps the span open until a streamed (SSE)
response has been fully sent::

    app.add_middleware(GernasASGIMiddleware)          # Starlette / FastAPI
    app = GernasASGIMiddleware(app, channel="a2a")     # any ASGI app
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas.attributes import Ctx, SpanName
from gernas.context import extract_context, reset_entry_span, set_entry_span
from gernas.decorators._core import record_exception, stamp_context, stamp_outcome
from gernas.tracer import get_tracer, safe_set

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


class GernasASGIMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        channel: str | None = None,
        exclude_paths: Iterable[str] = ("/health", "/healthz"),
        extract: bool = True,
    ) -> None:
        self.app = app
        self.channel = channel
        self.exclude = tuple(exclude_paths)
        self.extract = extract

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path", "").startswith(self.exclude):
            await self.app(scope, receive, send)
            return
        try:
            span, token = self._start(scope)
        except Exception:  # noqa: BLE001 - never break request handling
            await self.app(scope, receive, send)
            return
        entry_token = set_entry_span(span)
        status_holder: dict[str, int] = {}

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 0))
            await send(message)

        exc: BaseException | None = None
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as e:
            exc = e
            raise
        finally:
            self._finish(span, scope, status_holder.get("status"), exc)
            reset_entry_span(entry_token)
            otel_context.detach(token)

    def _start(self, scope: Scope) -> tuple[Any, Any]:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        parent = extract_context(headers) if self.extract else None
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        span = get_tracer().start_span(f"{method} {path}", context=parent, kind=SpanKind.SERVER)
        token = otel_context.attach(trace.set_span_in_context(span, parent or otel_context.get_current()))
        stamp_context(span, SpanName.HTTP)
        safe_set(span, Ctx.HTTP_METHOD, method)
        safe_set(span, Ctx.HTTP_ROUTE, path)
        safe_set(span, Ctx.CHANNEL, self.channel)
        return span, token

    @staticmethod
    def _finish(span: Any, scope: Scope, status: int | None, exc: BaseException | None) -> None:
        try:
            route = scope.get("route")
            template = getattr(route, "path", None) or getattr(route, "path_format", None)
            if template:
                safe_set(span, Ctx.HTTP_ROUTE, template)
                span.update_name(f"{scope.get('method', 'GET')} {template}")
            if status is not None:
                safe_set(span, Ctx.HTTP_STATUS_CODE, status)
            stamp_outcome(span, exc)
            if exc is not None:
                record_exception(span, exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            elif status is not None and status >= 500:
                safe_set(span, Ctx.EXECUTION_STATUS, "error")
                safe_set(span, Ctx.ERROR_CATEGORY, "internal")
                span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
        except Exception:  # noqa: BLE001
            pass
        finally:
            span.end()
