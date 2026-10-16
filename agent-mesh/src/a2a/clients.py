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

from agent_framework.a2a import A2AAgent

from src.config import Config
from src.observability import get_logger, CAT_A2A

_log = get_logger(CAT_A2A)

from src.observability import gernas_telemetry as _gt


def get_remote_agent(name: str) -> A2AAgent:
    """Returns an A2A client bound to the named agent node's URL.

    Uses ``A2AAgent``'s own correctly-configured HTTP client. Trace context is
    propagated by the Gernas A2A interceptor, so the remote node joins this
    caller's distributed trace.
    """
    kwargs = {}
    interceptor = _gt.a2a_interceptor()
    if interceptor is not None:
        kwargs["auth_interceptor"] = interceptor
    return A2AAgent(
        name=name,
        url=Config.agent_url(name),
        supported_protocol_bindings=["JSONRPC"],
        timeout=httpx.Timeout(connect=10.0, read=Config.A2A_TIMEOUT, write=10.0, pool=5.0),
        **kwargs,
    )


@_gt.trace_delegation(child_arg="name", protocol="a2a")
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
        try:
            from opentelemetry import baggage as _otel_baggage
            rid = _otel_baggage.get_baggage("fab.request_id") or "-"
        except Exception:
            rid = "-"
        _log.error("A2A call to node '%s' failed: %s | sent: \"%s\"", name, error, _p,
                   extra={"node": name, "status": "ERROR", "request_id": rid})
        raise
    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if error is None:
            # Truncated previews so the log shows *what* was sent/received, not just sizes.
            _p = " ".join((prompt or "").split())[:200]
            _r = " ".join((result or "").split())[:200]
            try:
                from opentelemetry import baggage as _otel_baggage
                rid = _otel_baggage.get_baggage("fab.request_id") or "-"
            except Exception:
                rid = "-"
            _log.info("A2A call node=%s ok (%d ms, %d chars) | sent: \"%s\" | recv: \"%s\"",
                      name, duration_ms, len(result or ""), _p, _r,
                      extra={"node": name, "status": "SUCCESS", "request_id": rid,
                             "duration_ms": duration_ms})
        # Business metric — A2A hop latency and outcome by target node.
        try:
            from src.observability.metrics import record_a2a_call
            record_a2a_call(
                target_node=name,
                result="ERROR" if error else "SUCCESS",
                duration_ms=float(duration_ms),
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
