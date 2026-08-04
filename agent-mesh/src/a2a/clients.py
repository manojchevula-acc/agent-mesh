"""A2A client helpers — connect to remote agent nodes via plain HTTP POST.

Cross-process distributed tracing
---------------------------------
W3C traceparent / tracestate are injected into every outbound request via
``_otel_headers()``, which calls ``opentelemetry.propagate.inject``. The
receiving node extracts and attaches that context (see ``src/a2a/hosting.py``),
so the remote agent's spans continue the SAME distributed trace as the caller.
"""
import sys
import time
import pathlib

import httpx

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.observability import get_logger, CAT_A2A

_log = get_logger(CAT_A2A)


def _otel_headers() -> dict:
    """Injects W3C traceparent so downstream agents join the same trace."""
    try:
        from opentelemetry.propagate import inject
        headers: dict = {}
        inject(headers)
        return headers
    except Exception:
        return {}


async def ask_remote(
    name: str,
    prompt: str,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> str:
    """Sends a prompt to a remote agent node and returns its text response.

    POSTs ``{"message": prompt}`` to ``{agent_url}/invoke``.  Trace context is
    propagated via W3C traceparent injected into the request headers.  The hop
    is recorded to the centralised log, correlated with the active trace/span.
    """
    t0 = time.perf_counter()
    error: str | None = None
    result = ""
    url = f"{Config.agent_url(name)}invoke"
    try:
        async with httpx.AsyncClient(timeout=Config.A2A_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"message": prompt},
                headers=_otel_headers(),
            )
            resp.raise_for_status()
            result = resp.json().get("text", "")
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
