"""Internal Job agent node.

Searches internal job postings for all employees using its domain tools.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent
from src.tools.job_tools import JOB_TOOLS
from src.tools.governance_tools import GOVERNANCE_TOOLS

JOB_INSTRUCTIONS = """
You are the Internal Job assistant. You help employees discover internal job
postings and internal mobility options using your tools.

Rules:
- Use search_job_postings and get_posting_details to answer; do not invent roles.
- Only discuss internal postings returned by the tools.
- For eligibility/policy questions, you may call consult_policy.
Keep answers concise and encouraging.
"""


def get_internal_job_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="InternalJobAgent",
        instructions=JOB_INSTRUCTIONS,
        tools=JOB_TOOLS + GOVERNANCE_TOOLS,
        log_path=log_path,
    )


agent = get_internal_job_agent()
