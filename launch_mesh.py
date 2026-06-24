"""Launch the agent mesh — one isolated process (and port) per node.

Spawns the four mesh nodes as separate A2A servers, then waits. Ctrl+C tears
them all down. Start the external MCP services first:
  - DataLayer-as-a-Service (port 9100)
  - RAG-as-a-Service MCP server (port 9000)

Node start order (AgentMesh 15.0.6.2026):
  compliance   -> semantic safety guardrail
  data_agent   -> structured data via DataLayer MCP
  rag_agent    -> banking knowledge via RAG MCP
  price_assist -> primary FAB banking orchestrator (started last)

Usage: python launch_mesh.py
"""
import os
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import sys
import time
import signal
import subprocess
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.agents.node_registry import NODE_NAMES

# Start specialist agents first, then the primary orchestrator last.
START_ORDER = ["compliance", "data_agent", "rag_agent", "price_assist"]


def main():
    server = str(pathlib.Path(__file__).resolve().parent / "a2a_server.py")
    procs = []
    print("=" * 70)
    print("  LAUNCHING AGENT MESH (Microsoft Agent Framework + A2A)")
    print("=" * 70)
    for name in START_ORDER:
        port = Config.AGENT_PORTS[name]
        p = subprocess.Popen([sys.executable, server, "--agent", name, "--port", str(port)])
        procs.append((name, p))
        print(f"  -> {name:<13} pid={p.pid:<6} http://{Config.A2A_HOST}:{port}/")
        time.sleep(1.0)

    print("-" * 70)
    print("  Mesh is starting. Give it ~10s to warm up, then run:  python run.py")
    print("  Press Ctrl+C to stop the whole mesh.")
    print("=" * 70)

    def _shutdown(*_):
        print("\n[mesh] Shutting down all nodes...")
        for name, p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    reported = set()
    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None and name not in reported:
                    reported.add(name)
                    print(f"[mesh] WARNING: node '{name}' exited (code {p.returncode}).")
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
