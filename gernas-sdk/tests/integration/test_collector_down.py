"""A dead Collector never surfaces in the agent; lost spans are counted (Blueprint §3 dropped spans).

Runs in a subprocess with the real OTLP gRPC exporter pointed at a closed port.
"""

from __future__ import annotations

import json

from conftest import IDENTITY, run_python

_SCRIPT = r"""
import asyncio, json, time
import gernas

handle = gernas.init_gernas()
errors = 0

from gernas import tracer

@gernas.trace_tool("t")
def tool(i):
    return i

@gernas.trace_tool("srv_tool", tool_type="mcp", dependency="srv")
async def mcp(name):
    return {}

@gernas.trace_llm("m", provider="p")
def stream():
    yield 1
    yield 2

@gernas.trace_agent("agent", agent_type="worker")
async def run(i):
    tool(i)
    await mcp("x")
    list(stream())
    gernas.start_hitl(f"t{i}", "a")
    gernas.complete_hitl(f"t{i}", "approved")

async def main():
    global errors
    for i in range(20):  # 20 runs x 5 spans = 100 span operations
        try:
            await run(i)
        except Exception:
            errors += 1

t0 = time.perf_counter()
asyncio.run(main())
agent_ms = (time.perf_counter() - t0) * 1000
handle.flush(3000)
handle.shutdown()
print(json.dumps({"errors": errors, "dropped_spans": tracer.dropped_spans.value, "agent_ms": agent_ms}))
"""


_LOGGING_RECONFIG_SCRIPT = r"""
import json, logging, logging.config, time
import gernas

handle = gernas.init_gernas()
logging.getLogger("gernas.app").info("export this record")
time.sleep(1.5)  # the batch log export is now in flight, retrying the dead Collector
t0 = time.perf_counter()
# What uvicorn.run() does at startup: dictConfig flushes every handler under the logging lock.
logging.config.dictConfig({"version": 1, "disable_existing_loggers": False})
print(json.dumps({"reconfig_s": time.perf_counter() - t0}))
"""


def test_collector_down_logging_reconfig_does_not_deadlock():
    result = run_python(
        _LOGGING_RECONFIG_SCRIPT,
        env={**IDENTITY, "GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:1", "GERNAS_OTLP_TIMEOUT_S": "10"},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["reconfig_s"] < 5


def test_collector_down_agent_sees_no_errors_and_drops_are_counted():
    result = run_python(
        _SCRIPT,
        env={**IDENTITY, "GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:1", "GERNAS_OTLP_TIMEOUT_S": "1"},
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["errors"] == 0
    assert out["dropped_spans"] > 0
    assert out["agent_ms"] < 5000  # export happens off the hot path
