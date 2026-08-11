"""
Retail Banking Handoff POC — MAF DevUI
=======================================
Runs the entire handoff workflow in a single process so the framework's
in-memory OTel collector can capture the full span tree and display it in
the DevUI trace panel.

Entities registered:
  • retail_bank_handoff  — the full HandoffBuilder workflow
  • triage_agent         — chat with each agent individually
  • account_agent
  • card_agent
  • loan_agent
  • transfer_agent
  • fraud_agent

Run:
    cd research_pocs/retail_bank_handoff
    python devui_app.py

Then open http://127.0.0.1:8080 in your browser.

Note on HandoffBuilder + DevUI interactivity
---------------------------------------------
HandoffBuilder emits `request_info` events mid-run when an agent responds
without handing off (it needs the next user message).  If the DevUI does not
resume the workflow automatically on these events, the conversation will stall
after the first agent response.

If you observe stalling: open workflows/handoff_workflow.py and set
`use_autonomous_mode=True` in build_workflow() — this makes agents continue
without waiting for user input, enabling full end-to-end trace capture at the
cost of interactivity.  You can still test individual agents interactively by
selecting them directly in the DevUI sidebar.
"""

import os
import sys
import pathlib

# Suppress noisy warnings from the framework
os.environ.setdefault("PYTHONWARNINGS", "ignore")

# Env: load parent agent-mesh/.env as base, then local .env as override
_here = pathlib.Path(__file__).resolve().parent
_env_local = _here / ".env"
_env_parent = _here / ".." / ".." / "agent-mesh" / ".env"

from dotenv import load_dotenv

if _env_parent.exists():
    load_dotenv(str(_env_parent), override=False)
if _env_local.exists():
    load_dotenv(str(_env_local), override=True)

# Ensure local packages resolve correctly
sys.path.insert(0, str(_here))

from agent_framework.devui import serve
from agents.agent_factory import create_chat_client, create_agents
from workflows.handoff_workflow import build_workflow

HOST = "127.0.0.1"
PORT = 8080


def main() -> None:
    print("=" * 60)
    print("  Retail Banking Handoff POC — MAF DevUI")
    print("=" * 60)
    print(f"  LLM model : {os.environ.get('GROQ_MODEL', '(not set)')}")
    print(f"  Base URL  : {os.environ.get('LLM_BASE_URL', '(not set)')}")
    print("-" * 60)

    chat_client = create_chat_client()
    agents = create_agents(chat_client)
    triage, account, card, loan, transfer, fraud = agents

    # Standard mode (interactive handoff conversation).
    # If DevUI stalls mid-conversation, switch to use_autonomous=True below.
    workflow = build_workflow(*agents, use_checkpoints=False)

    entities = [workflow, triage, account, card, loan, transfer, fraud]

    print(f"  Entities  : retail_bank_handoff (workflow) + {len(agents)} agents")
    print(f"  URL       : http://{HOST}:{PORT}")
    print("=" * 60)
    print()
    print("  Agents registered:")
    for agent in agents:
        print(f"    • {agent.name}")
    print()
    print("  To test the full workflow:")
    print("    1. Select 'retail_bank_handoff' in the sidebar")
    print("    2. Type a banking query (e.g. 'block my card ending 4532')")
    print("    3. Watch the trace panel for agent handoffs and tool calls")
    print()
    print("  To test individual agents:")
    print("    Select any agent from the sidebar and chat directly")
    print()

    serve(
        entities=entities,
        host=HOST,
        port=PORT,
        auto_open=True,
        instrumentation_enabled=True,   # populates the OTel trace/span tree
        auth_enabled=False,             # no token needed for POC
        mode="developer",               # verbose errors
    )


if __name__ == "__main__":
    main()
