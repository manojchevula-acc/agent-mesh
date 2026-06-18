"""Finance agent node (leadership-only).

Answers finance questions and performs finance actions using its domain tools.
Sensitive actions (payments) use the framework's native approval gate. Access is
restricted to the leadership team — enforced deterministically by the mesh
orchestrator before the request ever reaches this node.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent
from src.tools.finance_tools import FINANCE_TOOLS
from src.tools.governance_tools import GOVERNANCE_TOOLS

FINANCE_INSTRUCTIONS = """
You are the Finance assistant for the leadership team.

Tool selection (pick exactly the right tool):
- A department's budget or spend  -> call get_budget_report with that department.
- Company-wide financials         -> call get_financial_summary.
- Only call issue_payment when the user EXPLICITLY asks to pay or send money.

Rules:
- Answer the question directly with concrete numbers returned by the tools.
- Do NOT ask follow-up questions when you already have the answer.
- Never invent figures.
- Only call consult_policy when the user asks about rules, limits, or approvals.
Keep answers concise and professional.
"""


def get_finance_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="FinanceAgent",
        instructions=FINANCE_INSTRUCTIONS,
        tools=FINANCE_TOOLS + GOVERNANCE_TOOLS,
        log_path=log_path,
    )


agent = get_finance_agent()
