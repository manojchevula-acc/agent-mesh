"""Gateway / Router agent node (the mesh front door).

A lightweight LLM classifier that maps a user request to one or more domains:
finance, hr, or internal_job. It does NOT answer the request itself — the mesh
orchestrator uses its verdict to fan out to the right specialist nodes over A2A.
"""
import sys
import pathlib
from typing import List

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent

GATEWAY_INSTRUCTIONS = """
You are the Router for an enterprise assistant mesh.
Classify the user's request into ONE or MORE of these domains:

- finance       : budgets, financial reports/summaries, payments, payouts, spend.
- hr            : leave/PTO, benefits, HR policies, employment questions.
- internal_job  : internal job postings, internal mobility, open roles, careers.

If the request covers topics in MULTIPLE domains, list every relevant domain —
one token per line. If it covers only one domain, return just that one token.

Respond with ONLY domain tokens, nothing else.

Examples:
  "What is my leave balance?"                            -> hr
  "What is the engineering budget?"                      -> finance
  "Show me the leave policy and engineering budget"      -> hr
                                                            finance
  "Any open backend roles and what is my leave balance?" -> hr
                                                            internal_job
"""

VALID_DOMAINS = ("finance", "hr", "internal_job")


def get_gateway_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="GatewayAgent",
        instructions=GATEWAY_INSTRUCTIONS,
        log_path=log_path,
    )


def parse_domains(text: str) -> List[str]:
    """Extracts all valid domain tokens from gateway output. Always returns at least one."""
    t = (text or "").strip().lower()
    found = [d for d in VALID_DOMAINS if d in t]
    if found:
        return found
    # Keyword fallback — single domain
    if any(k in t for k in ("budget", "payment", "finance", "payout", "expense", "spend")):
        return ["finance"]
    if any(k in t for k in ("job", "role", "posting", "career", "mobility", "opening")):
        return ["internal_job"]
    return ["hr"]


def parse_domain(text: str) -> str:
    """Single-domain extract — kept for test-suite back-compat. Returns first domain."""
    return parse_domains(text)[0]


agent = get_gateway_agent()
