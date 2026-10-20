"""A2A client interceptor that propagates GERNAS trace context.

The A2A transport does not carry W3C trace context by itself. Passing
``GernasA2AInterceptor()`` as an A2A client interceptor (``A2AAgent(...,
auth_interceptor=GernasA2AInterceptor())`` in the Agent Framework) writes
``traceparent``/``tracestate`` and GERNAS baggage into the outgoing request
headers, so the remote node's spans join the caller's trace. The server side
extracts them with :class:`gernas.adapters.asgi.GernasASGIMiddleware`.
"""

from __future__ import annotations

from a2a.client.client import ClientCallContext
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor

from gernas.context import inject_context


class GernasA2AInterceptor(ClientCallInterceptor):
    """Inject W3C trace context + GERNAS baggage into every A2A call."""

    def __init__(self, inner: ClientCallInterceptor | None = None) -> None:
        self._inner = inner  # chain an existing (e.g. auth) interceptor

    async def before(self, args: BeforeArgs) -> None:
        if self._inner is not None:
            await self._inner.before(args)
        try:
            headers = inject_context({})
            if not headers:
                return
            if args.context is None:
                args.context = ClientCallContext()
            if args.context.service_parameters is None:
                args.context.service_parameters = {}
            args.context.service_parameters.update(headers)
        except Exception:  # noqa: BLE001 - propagation failure must not fail the call
            pass

    async def after(self, args: AfterArgs) -> None:
        if self._inner is not None:
            await self._inner.after(args)
