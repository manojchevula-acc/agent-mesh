"""Generic A2A server entrypoint — hosts one mesh node on its own port.

Usage:
    python a2a_server.py --agent compliance
    python a2a_server.py --agent data_agent --port 8003

Each agent runs as an isolated HTTP server.  Other agents reach it via a plain
httpx POST to /invoke (see src/a2a/clients.py).
"""
import os
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import sys
import argparse
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.observability import setup_observability
from src.agents.node_registry import build_node, NODE_NAMES, MCP_BACKED_NODES
from src.a2a.hosting import serve, build_starlette_app
from src.integrations.mcp_clients import MCP_CONNECTORS


def _serve_mcp_node(name: str, port: int) -> None:
    """Serve an MCP-backed node, holding its MCP session open for the node's life.

    The agent's tools are auto-discovered from the external service's MCP server.
    The MultiServerMCPClient session is opened and kept alive while uvicorn serves,
    so every request the node handles can call the service's tools.

    Retries the MCP connection up to _MCP_RETRIES times with exponential backoff
    so a slow external service startup doesn't immediately crash the node.
    """
    import asyncio
    import time
    import uvicorn

    _MCP_RETRIES = 8
    _MCP_BACKOFF_BASE = 2.0   # seconds; doubles each startup attempt (2, 4, 8, …)
    _MCP_RECONNECT_DELAY = 5.0  # fixed wait after a mid-session MCP drop

    async def _run() -> None:
        """Startup retry loop + mid-session reconnect.

        Distinguishes two failure modes:
        - Startup failures (MCP not yet available): exponential backoff up to
          _MCP_RETRIES, then hard exit.
        - Mid-session drops (MCP restarted while A2A node was serving): fixed
          5-second reconnect loop, never exits.
        """
        ever_started = False
        startup_attempt = 0
        while True:
            startup_attempt += 1
            try:
                connector = MCP_CONNECTORS[name]
                mcp_client, mcp_tools = await connector()
                try:
                    agent, public_name, description = build_node(name, mcp_tools=mcp_tools)
                    app = build_starlette_app(agent, public_name, description, port)
                    print(f"[mesh] Starting '{name}' ({public_name}) on "
                          f"http://{Config.A2A_HOST}:{port}/  (MCP: connected)")
                    server = uvicorn.Server(
                        uvicorn.Config(app, host=Config.A2A_HOST, port=port, log_level="warning")
                    )
                    ever_started = True
                    startup_attempt = 0  # reset counter; future failures are reconnects
                    await server.serve()
                    return  # clean shutdown from SIGINT/SIGTERM — do not reconnect
                finally:
                    await mcp_client.__aexit__(None, None, None)
            except Exception as exc:
                if ever_started:
                    # Node was healthy before — MCP session dropped mid-flight.
                    print(f"[mesh] '{name}' MCP session dropped: {exc}. "
                          f"Reconnecting in {_MCP_RECONNECT_DELAY:.0f}s …")
                    await asyncio.sleep(_MCP_RECONNECT_DELAY)
                elif startup_attempt >= _MCP_RETRIES:
                    print(f"[mesh] '{name}' MCP connect failed after {_MCP_RETRIES} attempts. "
                          f"Is the external MCP service running? Last error: {exc}")
                    raise SystemExit(1) from exc
                else:
                    delay = _MCP_BACKOFF_BASE * (2 ** (startup_attempt - 1))
                    print(f"[mesh] '{name}' MCP connect failed (attempt {startup_attempt}/{_MCP_RETRIES}): "
                          f"{exc}. Retrying in {delay:.0f}s …")
                    await asyncio.sleep(delay)

    asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(description="Host a mesh agent node as an HTTP server.")
    parser.add_argument("--agent", required=True, choices=NODE_NAMES, help="Node name to host.")
    parser.add_argument("--port", type=int, default=None, help="Override the port (defaults to registry).")
    args = parser.parse_args()

    # Activate OpenTelemetry + centralised logging for THIS node process, with a
    # per-node service name so each node is a distinct service in the trace tree.
    setup_observability(service_name=f"agent_mesh_{args.agent}")

    Config.validate()

    # Fail fast if Groq is not configured.
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
    print(f"[mesh] Starting '{args.agent}' ({public_name}) on http://{Config.A2A_HOST}:{port}/")
    serve(agent, public_name, description, port)


if __name__ == "__main__":
    main()
