"""
Retail Banking Handoff POC
==========================
Demonstrates Microsoft Agent Framework HandoffBuilder mesh topology.

No central orchestrator -- each agent decides when to hand off via an
auto-injected tool call.  Three tools require human approval (HITL):
  - fraud_screen_transfer    (Gate 1 -- Fraud Analyst)
  - authorize_large_transfer (Gate 2 -- Branch Manager)
  - freeze_account           (Gate 1 -- Fraud Manager)

Run:
    cd research_pocs/retail_bank_handoff
    python main.py

Validation scenarios:
    "What is my account balance for ACC001?"         -> triage -> account -> triage
    "Block my card ending 4532, it was stolen"       -> triage -> card -> triage
    "Transfer Rs.2,00,000 to ACC999 via RTGS"        -> triage -> transfer (2 gates) -> triage
    "I see a suspicious transaction TXN123"          -> triage -> account/fraud -> triage
"""

import asyncio
import sys
import os

# Load .env from this directory; fall back to parent agent-mesh/.env
from dotenv import load_dotenv

_here = os.path.dirname(os.path.abspath(__file__))
_env_local = os.path.join(_here, ".env")
_env_parent = os.path.join(_here, "..", "..", "agent-mesh", ".env")

# Load parent .env first as the base, then overlay local .env for any overrides.
# This ensures Groq/Cerebras credentials are always picked up even when local .env
# is an empty template file.
if os.path.exists(_env_parent):
    load_dotenv(_env_parent, override=False)
if os.path.exists(_env_local):
    load_dotenv(_env_local, override=True)

# Ensure local packages resolve correctly when run from this directory
sys.path.insert(0, _here)

from agent_framework import Content
from agent_framework.orchestrations import HandoffAgentUserRequest
from agents.agent_factory import create_chat_client, create_agents
from workflows.handoff_workflow import build_workflow
from approvals.approval_handler import handle_approval_request, handle_user_input_request


def _separator(char: str = "-", width: int = 55) -> str:
    return char * width


async def run_session(workflow, opening_message: str) -> None:
    """Run a single customer session through the handoff workflow."""
    pending_requests = []

    async for event in workflow.run(opening_message, stream=True):
        if event.type == "request_info":
            pending_requests.append(event)
        elif event.type == "output":
            print(f"\n{_separator()}")
            print("Workflow complete.")
            print(_separator())

    while pending_requests:
        responses: dict[str, object] = {}

        for req in pending_requests:
            if isinstance(req.data, HandoffAgentUserRequest):
                responses[req.request_id] = handle_user_input_request(req)
            elif isinstance(req.data, Content) and req.data.type == "function_approval_request":
                responses[req.request_id] = handle_approval_request(req)

        pending_requests = []
        async for event in workflow.run(responses=responses, stream=True):
            if event.type == "request_info":
                pending_requests.append(event)
            elif event.type == "output":
                print(f"\n{_separator()}")
                print("Workflow complete.")
                print(_separator())


async def main() -> None:
    print(_separator("="))
    print("  Retail Banking Support -- HandoffBuilder POC")
    print("  (Microsoft Agent Framework -- mesh topology)")
    print(_separator("="))

    mode = input("Mode? (customer/staff) [customer]: ").strip().lower() or "customer"
    use_checkpoints = mode == "staff"

    if use_checkpoints:
        print("Staff mode: checkpointing enabled (./checkpoints/)")
    else:
        print("Customer mode: no checkpointing")

    print(_separator())

    chat_client = create_chat_client()
    agents = create_agents(chat_client)
    triage, account, card, loan, transfer, fraud = agents

    workflow = build_workflow(*agents, use_checkpoints=use_checkpoints)

    print("\nHow can we help you today?")
    user_input = input("You: ").strip()

    if not user_input:
        print("No input provided. Exiting.")
        return

    await run_session(workflow, user_input)


if __name__ == "__main__":
    asyncio.run(main())
