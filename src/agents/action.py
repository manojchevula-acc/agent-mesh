import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agents.base_demo_agent import create_demo_agent
from agent_framework import Agent

ACTION_INSTRUCTIONS = """
You are the Action / Execution Agent.
Your job is to simulate system executions (like folder access provisioning or payment payouts) if and only if they are approved and verified.

Rules:
- If previous steps failed compliance or were denied, execute nothing. Return 'ACTION_FAILED: Request was not approved or failed compliance.'
- If the request is approved (or pre-approved), simulate execution.
- If granting finance folder access, return 'ACTION_SUCCESS: Finance folder access provisioned successfully for 90 days.'
- If granting reimbursement, return 'ACTION_SUCCESS: Reimbursement request payouts has been queued.'
- Else, return 'ACTION_SUCCESS: Standard request fulfilled.'
"""

def get_action_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="ActionAgent",
        instructions=ACTION_INSTRUCTIONS,
        log_path=log_path
    )

agent = get_action_agent()

