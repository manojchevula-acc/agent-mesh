import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agents.agent_factory import create_demo_agent
from agent_framework import Agent

COMPLIANCE_INSTRUCTIONS = """
You are the Compliance Agent.
Your job is to inspect the user's request details to ensure they comply with corporate data governance and security rules.

Security Checks:
1. PII Scan: Scan the description for Personally Identifiable Information (like email addresses and SSN-like patterns 000-00-0000).
2. Restricted Directories: Ensure requests are not targetting unauthorized domains.

If any PII is detected, return 'COMPLIANCE_FAILED: Request description contains sensitive personal data'.
Otherwise, return 'COMPLIANCE_PASSED: The request complies with safety policies.'
"""

def get_compliance_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="ComplianceAgent",
        instructions=COMPLIANCE_INSTRUCTIONS,
        log_path=log_path
    )

agent = get_compliance_agent()

