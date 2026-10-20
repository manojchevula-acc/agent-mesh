"""@trace_tool — tool, API and MCP invocations (Blueprint §2 tool context, §3 Skills/Tools:
invocation count, latency, success/failure, authorization decision, payload size, dependency).

MCP is a ``tool_type`` (Blueprint Fig. 1 "API & Tool Layer — APIs / MCP / Skills"), not a
separate schema. Payloads are never captured; only their sizes. An authorization
denial is recorded as ``authorization_decision=deny`` and keeps span status OK.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from gernas._metrics import record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, get_field, resolve, wrap
from gernas.run_state import current_run, error_category
from gernas.tracer import safe_set
from gernas.utils.hashing import payload_size

F = TypeVar("F", bound=Callable[..., Any])


class ToolSpec(Spec):
    op = SpanName.TOOL
    kind = "CLIENT"

    def __init__(
        self,
        tool_id: Any,
        tool_type: str,
        tool_version: str | None,
        api_operation: Any,
        dependency: str | None,
        authorization_decision: Any,
    ) -> None:
        self.tool_id, self.tool_type, self.tool_version = tool_id, tool_type, tool_version
        self.api_operation, self.dependency, self.authorization_decision = (
            api_operation,
            dependency,
            authorization_decision,
        )

    def start(self, span: Any, call: Call) -> None:
        tool_id = str(
            (resolve(self.tool_id, call) if callable(self.tool_id) else self.tool_id)
            or getattr(call.fn, "__name__", "tool")
        )
        call.state["tool_id"] = tool_id
        payload = call.payload()
        safe_set(span, Ctx.TOOL_ID, tool_id)
        safe_set(span, Ctx.TOOL_TYPE, self.tool_type)
        safe_set(span, Ctx.TOOL_VERSION, self.tool_version)
        safe_set(
            span,
            Ctx.API_OPERATION,
            resolve(self.api_operation, call) if callable(self.api_operation) else self.api_operation,
        )
        safe_set(span, Ctx.DEPENDENCY, self.dependency)
        safe_set(span, Ctx.REQUEST_SIZE_BYTES, payload_size(payload))
        safe_set(span, Ctx.AUTHORIZATION_DECISION, "not_evaluated")
        run = current_run()
        if run is not None:
            run.on_tool_call(tool_id, call.input_fingerprint())

    def success(self, span: Any, call: Call, result: Any) -> None:
        safe_set(span, Ctx.RESPONSE_SIZE_BYTES, payload_size(result))
        decision = (
            (
                resolve(self.authorization_decision, call, result)
                if callable(self.authorization_decision)
                else self.authorization_decision
            )
            or get_field(result, "authorization_decision")
            or "allow"
        )
        call.state["decision"] = str(decision)
        safe_set(span, Ctx.AUTHORIZATION_DECISION, str(decision))

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        if error_category(exc) == "auth":
            call.state["decision"] = "deny"
            safe_set(span, Ctx.AUTHORIZATION_DECISION, "deny")
            return False
        call.state["decision"] = "allow"
        safe_set(span, Ctx.AUTHORIZATION_DECISION, "allow")
        return super().failure(span, call, exc)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        dims = {Ctx.TOOL_ID: call.state.get("tool_id"), Ctx.TOOL_TYPE: self.tool_type}
        record(
            Metric.TOOL_CALLS,
            {
                **dims,
                Ctx.EXECUTION_STATUS: "error" if exc else "ok",
                Ctx.AUTHORIZATION_DECISION: call.state.get("decision", "not_evaluated"),
            },
            histogram=Metric.TOOL_DURATION,
            duration_ms=call.elapsed_ms(),
        )


def trace_tool(
    tool_id: str | Callable[..., str] | None = None,
    *,
    tool_type: str = "function",
    tool_version: str | None = None,
    api_operation: str | Callable[..., str] | None = None,
    dependency: str | None = None,
    authorization_decision: str | Callable[[Any], str] | None = None,
) -> Callable[[F], F]:
    """Trace a tool / API / MCP call as ``tool.call`` (``tool_id`` defaults to the function name).

    For MCP tools use ``tool_type="mcp", dependency="<mcp server>"``.
    """

    def decorator(fn: F) -> F:
        return wrap(fn, ToolSpec(tool_id, tool_type, tool_version, api_operation, dependency, authorization_decision))

    return decorator
