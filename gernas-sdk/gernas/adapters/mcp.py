"""MCP *server-side* adapters: continue the caller's trace inside MCP tool execution.

MCP clients that follow the OTel convention (e.g. Microsoft Agent Framework)
put ``traceparent`` / ``baggage`` into the request ``_meta``. These helpers read
it back so the server's spans join the agent's trace:

* :class:`GernasFastMCPMiddleware` — ``fastmcp`` (v2+) middleware: one server-side
  ``mcp.call`` span (kind SERVER) per tool call, parented to the caller;
* :func:`mcp_meta_context` — context manager for the low-level ``mcp`` SDK
  (``mcp.server.fastmcp``): ``with mcp_meta_context(ctx): ...``.

No MCP payloads are recorded — only fingerprints (principle 8).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from gernas.async_context import attach_headers
from gernas.decorators._core import Call, wrap
from gernas.decorators.tool import MCPSpec
from gernas.utils.hashing import hash_inputs


def meta_carrier(meta: Any) -> dict[str, str]:
    """String entries of an MCP ``_meta`` object/dict (where traceparent lives)."""
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        try:
            meta = meta.model_dump(by_alias=True, exclude_none=True)
        except Exception:  # noqa: BLE001
            meta = dict(getattr(meta, "__dict__", {}))
    if not isinstance(meta, dict):
        return {}
    return {str(k): v for k, v in meta.items() if isinstance(v, str)}


@contextmanager
def mcp_meta_context(ctx: Any) -> Iterator[None]:
    """Attach the trace context carried in a ``mcp.server.fastmcp.Context`` request."""
    meta = None
    try:
        meta = getattr(getattr(ctx, "request_context", None), "meta", None)
    except Exception:  # noqa: BLE001 - outside a request (direct call, tests)
        meta = None
    with attach_headers(meta_carrier(meta)):
        yield


class _ServerMCPSpec(MCPSpec):
    kind = "SERVER"

    def start(self, span: Any, call: Call) -> None:
        params = call.args[0].message
        call.state["input_hash"] = hash_inputs(getattr(params, "arguments", None) or {})
        super().start(span, call)


class GernasFastMCPMiddleware:
    """``fastmcp`` server middleware producing a server-side ``mcp.call`` span per tool call."""

    def __init__(self, server_id: str, *, transport: str | None = "streamable_http") -> None:
        def tool_name(context: Any, call_next: Any) -> str:
            return str(getattr(context.message, "name", "tool"))

        self._run = wrap(self._invoke, _ServerMCPSpec(server_id, "tool", tool_name, transport))

    @staticmethod
    async def _invoke(context: Any, call_next: Any) -> Any:
        return await call_next(context)

    async def __call__(self, context: Any, call_next: Any) -> Any:  # fastmcp Middleware protocol
        if getattr(context, "method", None) != "tools/call":
            return await call_next(context)
        with attach_headers(_request_meta(context)):
            return await self._run(context, call_next)


def _request_meta(context: Any) -> dict[str, str]:
    """``_meta`` of the current tool call: on the message, else on the MCP request context.

    fastmcp v3 strips ``_meta`` from the params it hands to middleware; it remains
    on the low-level MCP ``request_ctx`` (the same place fastmcp itself reads it).
    """
    carrier = meta_carrier(getattr(getattr(context, "message", None), "meta", None))
    if carrier:
        return carrier
    fctx = getattr(context, "fastmcp_context", None)
    try:
        carrier = meta_carrier(getattr(getattr(fctx, "request_context", None), "meta", None))
    except Exception:  # noqa: BLE001 - request_context raises outside a request
        carrier = {}
    if carrier:
        return carrier
    try:
        from mcp.server.lowlevel.server import request_ctx
        return meta_carrier(getattr(request_ctx.get(), "meta", None))
    except Exception:  # noqa: BLE001 - mcp not installed / outside a request
        return {}
