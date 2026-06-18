"""Generic A2A server entrypoint — hosts one mesh node on its own port.

Usage:
    python a2a_server.py --agent finance
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
from src.agents.node_registry import build_node, NODE_NAMES
from src.a2a.hosting import build_agent_card, serve


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

    # # Fail fast if the LLM backend is unavailable: without it every agent.run
    # # falls back to echoing the prompt, which is confusing to debug at runtime.
    ok, msg = Config.check_ollama()
    if not ok:
        import logging
        logging.getLogger("mesh.system").error("Node '%s' startup blocked: %s", args.agent, msg)
        print(f"[mesh] ERROR: {msg}")
        sys.exit(1)
    print(f"[mesh] {msg}")

    port = args.port or Config.AGENT_PORTS[args.agent]
    agent, public_name, description = build_node(args.agent)
    card = build_agent_card(public_name, description, port)

    print(f"[mesh] Starting '{args.agent}' ({public_name}) on http://{Config.A2A_HOST}:{port}/")
    serve(agent, card, port)


if __name__ == "__main__":
    main()
