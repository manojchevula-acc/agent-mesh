"""Live check of the MCP leg: MAF MCP client → DataLayer MCP server, traced by the Gernas SDK only.

    python scripts/mcp_probe.py MCPPROBE18A [--url http://127.0.0.1:9100/mcp]
    python scripts/opik_verify.py --task-id MCPPROBE18A

Expected in Opik for the task id: ``use_case.request → agent.run → tool.call`` (CLIENT,
``tool_type=mcp``) ``→ tool.call`` (SERVER, ``GernasFastMCPMiddleware`` in the DataLayer
process) — one trace, with the probe's run context on the server span.

Needs: the Collector, the DataLayer MCP server started with the same ``GERNAS_*``
variables, ``GERNAS_*`` in this process, and ``agent-framework`` installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import gernas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id", help="request id recorded as gernas.task_id")
    ap.add_argument("--url", default=os.getenv("DATALAYER_MCP_URL", "http://127.0.0.1:9100/mcp"))
    args = ap.parse_args()

    from agent_framework import MCPStreamableHTTPTool

    from gernas.adapters.agent_framework import disable_native_telemetry

    handle = gernas.init_gernas(service_name="gernas_mcp_probe")
    disable_native_telemetry()

    @gernas.trace_tool(lambda fn, arguments: fn.name, tool_type="mcp", dependency="datalayer")
    async def call_mcp(fn, arguments):
        return await fn.invoke(arguments=arguments)

    @gernas.trace_agent("mcp_probe_agent", agent_type="worker")
    async def run():
        async with MCPStreamableHTTPTool(name="datalayer", url=args.url) as mcp:
            functions = {f.name: f for f in mcp.functions}
            name = next(n for n in functions if "customer" in n.lower())
            result = await call_mcp(functions[name], {})
            return name, len(str(result))

    @gernas.trace_use_case(channel="cli", request_type="mcp_probe")
    async def probe():
        with gernas.request_context(session_id=f"sess-{args.task_id}", task_id=args.task_id, principal_type="service"):
            return await run()

    tool, chars = asyncio.run(probe())
    handle.shutdown()
    print(json.dumps({"task_id": args.task_id, "tool": tool, "result_chars": chars}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
