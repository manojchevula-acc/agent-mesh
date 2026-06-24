"""Generic A2A server entrypoint — hosts one mesh node on its own port.

Usage:
    python a2a_server.py --agent compliance
    python a2a_server.py --agent policy --port 8004

Each agent runs as an isolated A2A-protocol HTTP server. Other agents reach it
via an A2A client (see src/a2a/clients.py).
"""
import os
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import sys
import argparse
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import sys
import argparse
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.observability import setup_observability
from src.agents.node_registry import build_node, NODE_NAMES, MCP_BACKED_NODES
from src.a2a.hosting import build_agent_card, serve, build_starlette_app


def _serve_mcp_node(name: str, port: int) -> None:
    """Serve an MCP-backed node, holding its MCP session open for the node's life.

    The agent's tools are auto-discovered from the external service's MCP server.
    The streamable-HTTP session is opened with ``async with`` and kept alive while
    uvicorn serves, so every request the node handles can call the service's tools.

    Retries the MCP connection up to _MCP_RETRIES times with exponential backoff
    so a slow external service startup doesn't immediately crash the node.
    """
    import asyncio
    import time
    import uvicorn
    from src.integrations.mcp_clients import MCP_TOOL_FACTORIES

    _MCP_RETRIES = 8
    _MCP_BACKOFF_BASE = 2.0  # seconds; doubles each attempt (2, 4, 8, …)

    async def _run() -> None:
        last_exc: Exception | None = None
        for attempt in range(1, _MCP_RETRIES + 1):
            try:
                mcp_tool = MCP_TOOL_FACTORIES[name]()
                async with mcp_tool:  # connect + load tools; closes on shutdown
                    agent, public_name, description = build_node(name, mcp_tool=mcp_tool)
                    card = build_agent_card(public_name, description, port)
                    app = build_starlette_app(agent, card)
                    print(f"[mesh] Starting '{name}' ({public_name}) on "
                          f"http://{Config.A2A_HOST}:{port}/  (MCP: connected)")
                    server = uvicorn.Server(
                        uvicorn.Config(app, host=Config.A2A_HOST, port=port, log_level="warning")
                    )
                    await server.serve()
                    return  # clean shutdown
            except Exception as exc:
                last_exc = exc
                if attempt < _MCP_RETRIES:
                    delay = _MCP_BACKOFF_BASE * (2 ** (attempt - 1))
                    print(f"[mesh] '{name}' MCP connect failed (attempt {attempt}/{_MCP_RETRIES}): "
                          f"{exc}. Retrying in {delay:.0f}s …")
                    await asyncio.sleep(delay)
                else:
                    print(f"[mesh] '{name}' MCP connect failed after {_MCP_RETRIES} attempts. "
                          f"Is the external MCP service running? Last error: {exc}")
                    raise SystemExit(1) from last_exc

    asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(description="Host a mesh agent node as an A2A server.")
    parser.add_argument("--agent", required=True, choices=NODE_NAMES, help="Node name to host.")
    parser.add_argument("--port", type=int, default=None, help="Override the port (defaults to registry).")
    args = parser.parse_args()

    # Activate framework-native OpenTelemetry + centralized logging for THIS node
    # process, with a per-node service name so each node is a distinct service in
    # the distributed trace tree. Exporter wiring is driven by OBS_PROFILE.
    setup_observability(service_name=f"agent_mesh_{args.agent}")

    Config.validate()

    # Fail fast if Groq is not configured: without an API key every agent.run
    # will return a 401, which is confusing to debug at query time.
    ok, msg = Config.check_groq()
    if not ok:
        import logging
        logging.getLogger("mesh.system").error("Node '%s' startup blocked: %s", args.agent, msg)
        print(f"[mesh] ERROR: {msg}")
        sys.exit(1)
    print(f"[mesh] {msg}")

    port = args.port or Config.AGENT_PORTS[args.agent]

    # MCP-backed nodes need a live MCP session for their lifetime → async serve.
    if args.agent in MCP_BACKED_NODES:
        _serve_mcp_node(args.agent, port)
        return

    agent, public_name, description = build_node(args.agent)
    card = build_agent_card(public_name, description, port)

    print(f"[mesh] Starting '{args.agent}' ({public_name}) on http://{Config.A2A_HOST}:{port}/")
    serve(agent, card, port)


if __name__ == "__main__":
    main()
