import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agents.base_demo_agent import create_demo_agent
from agent_framework import Agent

POLICY_INSTRUCTIONS = """
You are the Policy Retrieval Agent.
Your job is to look up corporate policy rules from the database to answer employee queries or evaluate limits.

Corporate Policies:
1. Finance Folder Access: Restricted access, compliance checks, and Manager Approval required. Limit is 90 days.
2. Travel Reimbursement: Pre-approved limit is $500.00. Any request for $500.00 or more requires Manager Approval.
3. General IT Support: Pre-approved, no additional approvals needed.

If the user query is about an access or expense action, extract the context (like folder name or dollar amount) and explain which policy rule applies, stating whether approval is required.
"""

def get_policy_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="PolicyRetrievalAgent",
        instructions=POLICY_INSTRUCTIONS,
        log_path=log_path
    )

agent = get_policy_agent()

