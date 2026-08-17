"""MAF DevUI launcher for the Retail Banking Handoff POC.

Runs the 6-agent handoff mesh in a single process so the DevUI can capture
the full trace tree (spans, tool calls, handoffs) without needing Docker or
a network OTLP collector. The built-in in-memory span collector feeds the
DevUI trace panel directly.

Run
---
    python devui_app.py        ->  http://127.0.0.1:8080
    python ui_server.py        ->  http://localhost:8000  (API)
    npm run dev  (frontend/)   ->  http://localhost:5173  (Custom UI)
"""
import os

os.environ["OBS_PROFILE"] = "off"
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import sys
import pathlib

_here = pathlib.Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from dotenv import load_dotenv
load_dotenv(_here / ".env", override=True)

from agent_framework.devui import serve
from agents.agent_factory import create_agents
from workflows.handoff_workflow import build_workflow


def main() -> None:
    agents = create_agents()
    workflow = build_workflow(*agents)

    host      = os.environ.get("DEVUI_HOST", "127.0.0.1")
    port      = int(os.environ.get("DEVUI_PORT", "8080"))
    auto_open = os.environ.get("DEVUI_AUTO_OPEN", "true").lower() == "true"
    no_auth   = os.environ.get("DEVUI_NO_AUTH", "true").lower() == "true"

    print("=" * 60)
    print("  Retail Banking — MAF DevUI")
    print(f"  DevUI     : http://{host}:{port}")
    print(f"  Custom UI : http://localhost:5173  (if running)")
    print("=" * 60)

    serve(
        entities=[workflow, *agents],
        host=host,
        port=port,
        auto_open=auto_open,
        instrumentation_enabled=True,
        auth_enabled=not no_auth,
    )


if __name__ == "__main__":
    main()
