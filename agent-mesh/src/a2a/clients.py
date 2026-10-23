"""A2A client helpers — connect to remote agent nodes over the A2A protocol.

Cross-process distributed tracing
---------------------------------
The A2A transport does not propagate W3C trace context on its own, and passing a
bare ``httpx.AsyncClient`` to ``A2AAgent`` breaks the A2A SDK's client setup. The
Gernas SDK closes the gap with an A2A *client interceptor*
(``gernas.adapters.a2a.GernasA2AInterceptor``) that writes ``traceparent`` /
``tracestate`` and GERNAS baggage (``gernas.parent_run_id``, ``gernas.session_id``)
into every outgoing A2A request. The receiving node extracts them
(``GernasASGIMiddleware`` in ``src/a2a/hosting.py``), so the remote agent's spans
continue the SAME distributed trace and its ``agent.run`` links to the caller's run.

Every hop is an ``agent.delegate`` span (``@trace_delegation``) carrying the
parent/child agent ids and the inter-agent latency.
"""
import sys
import time
import pathlib

import httpx

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import gernas
from agent_framework.a2a import A2AAgent
from gernas.adapters.a2a import GernasA2AInterceptor

from src.config import Config
from src.utils.logging_config import get_logger, CAT_A2A

_log = get_logger(CAT_A2A)


def get_remote_agent(name: str) -> A2AAgent:
    """Returns an A2A client bound to the named agent node's URL.

    Uses ``A2AAgent``'s own correctly-configured HTTP client. Trace context is
    propagated by the Gernas A2A interceptor, so the remote node joins this
    caller's distributed trace.
    """
    return A2AAgent(
        name=name,
        url=Config.agent_url(name),
        supported_protocol_bindings=["JSONRPC"],
        timeout=httpx.Timeout(connect=10.0, read=Config.A2A_TIMEOUT, write=10.0, pool=5.0),
        auth_interceptor=GernasA2AInterceptor(),
    )


@gernas.trace_delegation(child_arg="name")
async def ask_remote(
    name: str,
    prompt: str,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> str:
    """Sends a prompt to a remote agent node and returns its text response.

    The hop is an ``agent.delegate`` span; A2A trace context is propagated by the
    Gernas A2A interceptor (see module docstring). The hop is also recorded to the
    centralized log, correlated with the active trace/span.
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
        _p = " ".join((prompt or "").split())[:200]
        rid = gernas.app_value("request_id") or "-"
        _log.error("A2A call to node '%s' failed: %s | sent: \"%s\"", name, error, _p,
                   extra={"node": name, "status": "ERROR", "request_id": rid})
        raise
    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if error is None:
            # Truncated previews so the log shows *what* was sent/received, not just sizes.
            _p = " ".join((prompt or "").split())[:200]
            _r = " ".join((result or "").split())[:200]
            rid = gernas.app_value("request_id") or "-"
            _log.info("A2A call node=%s ok (%d ms, %d chars) | sent: \"%s\" | recv: \"%s\"",
                      name, duration_ms, len(result or ""), _p, _r,
                      extra={"node": name, "status": "SUCCESS", "request_id": rid,
                             "duration_ms": duration_ms})
