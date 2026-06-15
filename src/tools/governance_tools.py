"""Governance tools shared by domain agents.

`consult_policy` lets a domain agent reach the Policy agent node over A2A during
its own reasoning (genuine agent-to-agent consultation initiated by the agent).
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import tool
from src.a2a.clients import ask_remote


@tool(description="Consult the corporate Policy agent for the rules that apply to a request.")
async def consult_policy(question: str) -> str:
    """Asks the Policy agent node (over A2A) which rules apply. Returns its guidance."""
    try:
        return await ask_remote("policy", f"Which corporate policy rules apply to: {question}")
    except Exception as e:  # policy node unreachable
        return f"POLICY_UNAVAILABLE: could not reach the Policy agent ({e})."


GOVERNANCE_TOOLS = [consult_policy]
