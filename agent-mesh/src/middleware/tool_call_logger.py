"""Tool-call logging middleware (ground truth for REAL MCP invocations).

Unlike ``AuditMiddleware`` (which runs once per *agent* invocation), this is a
``FunctionMiddleware`` that fires once per *actual tool call* the framework's
function-invocation loop makes. It is the authoritative record of how many times
a tool (e.g. ``search_documents``) was genuinely executed for a single request —
independent of how many ``<llm_reasoning>`` blocks the model wrote in its text.

For each real call it logs:
  - the function name and arguments,
  - a per-request 1-based sequence number (call_index),
  - whether the call is a DUPLICATE of an earlier call in the same request
    (identical function + arguments), which is the concrete signal that the
    model re-issued a redundant retrieval.

This is what answers "did a second real MCP call actually happen, and why".
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Tuple

from agent_framework import FunctionMiddleware, FunctionInvocationContext

from src.observability import get_logger, CAT_MCP

_log = get_logger(CAT_MCP)


def _request_id() -> str:
    try:
        from opentelemetry import baggage as _baggage
        return (_baggage.get_baggage("fab.request_id") or "-")
    except Exception:
        return "-"


def _args_signature(arguments: Any) -> str:
    """Stable string signature of the tool arguments for duplicate detection."""
    try:
        if hasattr(arguments, "model_dump"):
            arguments = arguments.model_dump()
        return json.dumps(arguments, sort_keys=True, default=str)
    except Exception:
        return str(arguments)


class ToolCallLogMiddleware(FunctionMiddleware):
    """Logs every REAL tool invocation with sequence + duplicate detection.

    State is kept per request_id so concurrent requests do not interfere. Each
    entry is a list of (function_name, args_signature) seen so far for that
    request; the list length gives the call_index and lets us flag duplicates.
    """

    def __init__(self, agent_name: str = "unknown") -> None:
        self._agent_name = agent_name
        self._seen: Dict[str, List[Tuple[str, str]]] = {}

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        fn_name = getattr(context.function, "name", "unknown_function")
        sig = _args_signature(context.arguments)
        rid = _request_id()

        history = self._seen.setdefault(rid, [])
        call_index = len(history) + 1
        prior_identical = [
            i + 1 for i, (n, s) in enumerate(history) if n == fn_name and s == sig
        ]
        is_duplicate = bool(prior_identical)
        history.append((fn_name, sig))

        # Ground-truth log line: this fires ONLY on a real tool execution.
        _log.info(
            "REAL_TOOL_CALL agent=%s tool=%s call_index=%d duplicate=%s%s req=%s args=%s",
            self._agent_name,
            fn_name,
            call_index,
            is_duplicate,
            (f" (identical to call #{prior_identical[0]})" if is_duplicate else ""),
            rid,
            sig[:300],
        )

        try:
            await call_next()
        finally:
            # Bound memory: forget a request's history once it grows large or
            # when many requests have accumulated (best-effort, no strict TTL).
            if len(self._seen) > 512:
                self._seen.clear()
