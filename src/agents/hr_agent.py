"""HR agent node.

Answers HR self-service questions for all employees using its domain tools.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent
from src.tools.hr_tools import HR_TOOLS
from src.tools.governance_tools import GOVERNANCE_TOOLS

HR_INSTRUCTIONS = """
You are the HR assistant for employees.
You help with leave balances, benefits, and HR policies using your tools.

Rules:
- Use your HR tools to answer; do not invent figures or policies.
- Never reveal another employee's personal data.
- For policy/limit questions, you may call consult_policy to confirm corporate rules.
Keep answers concise, warm, and professional.
"""


def get_hr_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="HRAgent",
        instructions=HR_INSTRUCTIONS,
        tools=HR_TOOLS + GOVERNANCE_TOOLS,
        log_path=log_path,
    )


agent = get_hr_agent()
