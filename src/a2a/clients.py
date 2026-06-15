"""A2A client helpers — connect to remote agent nodes over the A2A protocol."""
import sys
import time
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework.a2a import A2AAgent

from src.config import Config


def get_remote_agent(name: str) -> A2AAgent:
    """Returns an A2A client bound to the named agent node's URL."""
    return A2AAgent(
        name=name,
        url=Config.agent_url(name),
        supported_protocol_bindings=["JSONRPC"],
    )


async def ask_remote(
    name: str,
    prompt: str,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> str:
    """Sends a prompt to a remote agent node, returns its text response, and emits
    an A2A_CALL trace event so every inter-node hop is recorded."""
    from src.observability import tracer  # late import avoids circular dep at startup
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
        raise
    finally:
        tracer.trace_a2a_call(
            node=name, prompt=prompt, response=result,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            status="ERROR" if error else "SUCCESS",
            trace_id=trace_id, parent_span_id=parent_span_id, error=error,
        )
