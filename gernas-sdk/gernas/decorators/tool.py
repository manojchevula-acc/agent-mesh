"""@trace_tool and @trace_mcp — tool and MCP invocations (WP1-CORE-02b steps 1-2).

Inputs and outputs are recorded as SHA-256 fingerprints only; MCP payloads are
never captured. An authorization denial is recorded as
``gernas.tool.auth_decision=deny`` with status OK — it is policy working, not a
failure. Each call reports into the active run for loop detection.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, get_field, resolve, wrap
from gernas.metrics.agentic import record
from gernas.run_state import current_run
from gernas.tracer import safe_set
from gernas.utils.hashing import hash_inputs

F = TypeVar("F", bound=Callable[..., Any])

_AUTH_ERRORS = ("PermissionError", "RBACDeniedError", "AuthorizationError", "Forbidden", "AccessDenied")


def _is_auth_denial(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return any(name in type(exc).__name__ for name in _AUTH_ERRORS)


class ToolSpec(Spec):
    op = SpanName.TOOL
    kind = "INTERNAL"

    def __init__(self, tool_id: str | Callable[..., str] | None, tool_type: str, tool_version: str | None,
                 auth_decision: str | Callable[[Any], str] | None) -> None:
        self.tool_id, self.tool_type, self.tool_version, self.auth_decision = tool_id, tool_type, tool_version, auth_decision

    def _tool_id(self, call: Call) -> str:
        value = resolve(self.tool_id, call) if callable(self.tool_id) else self.tool_id
        return str(value or getattr(call.fn, "__name__", "tool"))

    def span_name(self, call: Call) -> str:
        call.state["tool_id"] = self._tool_id(call)
        return SpanName.TOOL.format(tool_id=call.state["tool_id"])

    def start(self, span: Any, call: Call) -> None:
        tool_id = call.state.get("tool_id") or self._tool_id(call)
        digest = call.input_hash()
        safe_set(span, Ctx.TOOL_ID, tool_id)
        safe_set(span, Ctx.GEN_AI_TOOL_NAME, tool_id)
        safe_set(span, Ctx.TOOL_TYPE, self.tool_type)
        safe_set(span, Ctx.TOOL_VERSION, self.tool_version)
        safe_set(span, Ctx.INPUT_HASH, digest)
        safe_set(span, Ctx.AUTH_DECISION, "not_evaluated")
        run = current_run()
        if run is not None:
            run.on_tool_start(tool_id, digest)

    def success(self, span: Any, call: Call, result: Any) -> None:
        safe_set(span, Ctx.OUTPUT_HASH, hash_inputs(result))
        decision = (resolve(self.auth_decision, call, result) if callable(self.auth_decision)
                    else self.auth_decision) or get_field(result, "auth_decision") or "allow"
        call.state["result"] = "success" if decision != "deny" else "denied"
        safe_set(span, Ctx.AUTH_DECISION, str(decision))

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        if _is_auth_denial(exc):
            safe_set(span, Ctx.AUTH_DECISION, "deny")
            call.state["result"] = "denied"
            return False
        safe_set(span, Ctx.AUTH_DECISION, "allow")
        safe_set(span, Ctx.TOOL_FAILURE_REASON, type(exc).__name__)
        call.state["result"] = "error"
        return super().failure(span, call, exc)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.DURATION_MS, call.elapsed_ms())
        record(Metric.TOOL_CALLS, {Ctx.TOOL_ID: call.state.get("tool_id", "tool"),
                                   "result": call.state.get("result", "success")})


def trace_tool(tool_id: str | Callable[..., str] | None = None, *, tool_type: str = "function",
               tool_version: str | None = None,
               auth_decision: str | Callable[[Any], str] | None = None) -> Callable[[F], F]:
    """Trace a tool call as ``tool.{tool_id}`` (defaults to the function name)."""
    def decorator(fn: F) -> F:
        return wrap(fn, ToolSpec(tool_id, tool_type, tool_version, auth_decision))
    return decorator


class MCPSpec(Spec):
    op = SpanName.MCP
    kind = "CLIENT"

    def __init__(self, server_id: str, operation: str, tool_name: str | Callable[..., str] | None,
                 transport: str | None) -> None:
        if operation not in ("tool", "resource", "prompt"):
            raise ValueError("trace_mcp operation must be 'tool', 'resource' or 'prompt'")
        self.server_id, self.operation, self.tool_name, self.transport = server_id, operation, tool_name, transport

    def start(self, span: Any, call: Call) -> None:
        name = (resolve(self.tool_name, call) if callable(self.tool_name) else self.tool_name) \
            or call.arg("tool_name") or call.arg("name") or call.arg("uri") or getattr(call.fn, "__name__", "mcp")
        call.state["tool_name"] = str(name)
        digest = call.input_hash()
        safe_set(span, Ctx.MCP_SERVER_ID, self.server_id)
        safe_set(span, Ctx.MCP_OPERATION, self.operation)
        safe_set(span, Ctx.MCP_TOOL_NAME, str(name))
        safe_set(span, Ctx.GEN_AI_TOOL_NAME, str(name))
        safe_set(span, Ctx.MCP_TRANSPORT, self.transport)
        safe_set(span, Ctx.TOOL_TYPE, "mcp")
        safe_set(span, Ctx.INPUT_HASH, digest)
        run = current_run()
        if run is not None:
            run.on_tool_start(f"mcp:{self.server_id}:{name}", digest)

    def success(self, span: Any, call: Call, result: Any) -> None:
        safe_set(span, Ctx.OUTPUT_HASH, hash_inputs(result))
        if get_field(result, "isError", "is_error") is True:
            call.state["result"] = "error"

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.DURATION_MS, call.elapsed_ms())
        result = "error" if exc is not None else call.state.get("result", "success")
        record(Metric.MCP_CALLS, {Ctx.MCP_SERVER_ID: self.server_id, Ctx.MCP_TOOL_NAME: call.state.get("tool_name", ""),
                                  Ctx.MCP_OPERATION: self.operation, "result": result})


def trace_mcp(server_id: str, *, operation: str = "tool", tool_name: str | Callable[..., str] | None = None,
              transport: str | None = None) -> Callable[[F], F]:
    """Trace an MCP tool / resource / prompt call as ``mcp.call``.

    The transport is an attribute, never part of the span name (cardinality).
    ``tool_name`` defaults to a ``tool_name``/``name``/``uri`` argument, else the
    function name.
    """
    MCPSpec(server_id, operation, tool_name, transport)  # validate eagerly

    def decorator(fn: F) -> F:
        return wrap(fn, MCPSpec(server_id, operation, tool_name, transport))
    return decorator
