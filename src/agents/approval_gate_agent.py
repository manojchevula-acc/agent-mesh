import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agents.agent_factory import create_demo_agent
from agent_framework import Agent

APPROVAL_INSTRUCTIONS = """
You are the Approval Gate Agent.
Your job is to manage manager authorization for sensitive actions.

Rules:
- High-risk actions (restricted folder access, travel reimbursements >= $500) require explicit manager sign-off.
- General inquiries or low-risk expenses are pre-approved and do not need manager sign-off.
- If approval is requested, trigger approval evaluation. Return 'APPROVED' or 'DENIED' based on the evaluation outcome.
"""

def get_approval_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="ApprovalGateAgent",
        instructions=APPROVAL_INSTRUCTIONS,
        log_path=log_path
    )

agent = get_approval_agent()

