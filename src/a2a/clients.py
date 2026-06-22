"""A2A client helpers — connect to remote agent nodes over the A2A protocol.

Cross-process distributed tracing
---------------------------------
Unlike MCP (where Agent Framework auto-injects trace context into
``params._meta``), the A2A transport does not propagate W3C trace context on its
own. We close that gap *without* replacing ``A2AAgent``'s HTTP client (passing a
bare ``httpx.AsyncClient`` bypasses the A2A SDK's client-factory setup and breaks
response parsing — the call ends up echoing the request). Instead,
``setup_observability`` enables OpenTelemetry httpx instrumentation, which
injects ``traceparent`` / ``tracestate`` onto every outbound httpx request,
including the one ``A2AAgent`` makes internally. The receiving node extracts and
attaches that context (see ``src/a2a/hosting.py``), so the remote agent's
``invoke_agent`` / ``chat`` / ``execute_tool`` spans continue the SAME
distributed trace as the caller.
"""
import sys
import time
import pathlib

import httpx

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework.a2a import A2AAgent

from src.config import Config
from src.observability import get_logger, CAT_A2A

_log = get_logger(CAT_A2A)


def get_remote_agent(name: str) -> A2AAgent:
    """Returns an A2A client bound to the named agent node's URL.

    Uses ``A2AAgent``'s own correctly-configured HTTP client. Trace context is
    propagated via OpenTelemetry httpx instrumentation (enabled at startup), so
    the remote node joins this caller's distributed trace.
    """
    return A2AAgent(
        name=name,
        url=Config.agent_url(name),
        supported_protocol_bindings=["JSONRPC"],
        timeout=httpx.Timeout(connect=10.0, read=Config.A2A_TIMEOUT, write=10.0, pool=5.0),
    )


async def ask_remote(
    name: str,
    prompt: str,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> str:
    """Sends a prompt to a remote agent node and returns its text response.

    A2A trace context is propagated via the client's httpx hook (see module
    docstring). The hop is also recorded to the centralized log, correlated with
    the active trace/span. The remote ``invoke_agent`` span (emitted by the SDK's
    AgentTelemetryLayer inside A2AAgent + the node server) provides the span tree.
    """
    t0 = time.perf_counter()
    error: str | None = None
    result = ""
    try:
        remote = get_remote_agent(name)
        res = await remote.run(prompt)
        result = getattr(res, "text", str(res))
        return result
    except Exception as e:
        error = str(e)
        _log.error("A2A call to node '%s' failed: %s", name, error,
                   extra={"node": name, "status": "ERROR"})
        raise
    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if error is None:
            _log.info("A2A call node=%s ok (%d ms, %d chars)",
                      name, duration_ms, len(result or ""),
                      extra={"node": name, "status": "SUCCESS"})
        try:
            from src.observability import record_a2a_call
            record_a2a_call(
                node=name,
                duration_ms=float(duration_ms),
                status="error" if error else "success",
            )
        except Exception:
            pass
        # Optional legacy JSONL sink (off by default; workflow/agent spans cover this).
        if Config.ENABLE_TRACE_JSONL:
            try:
                from src.observability import tracer
                tracer.trace_a2a_call(
                    node=name, prompt=prompt, response=result,
                    duration_ms=duration_ms,
                    status="ERROR" if error else "SUCCESS",
                    trace_id=trace_id, parent_span_id=parent_span_id, error=error,
                )
            except Exception:
                pass
