"""Gateway / Router agent node (the mesh front door).

A lightweight LLM classifier that maps a user request to exactly one domain:
finance, hr, or internal_job. It does NOT answer the request itself — the mesh
orchestrator uses its verdict to dispatch to the right specialist node over A2A.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent

GATEWAY_INSTRUCTIONS = """
You are the Router for an enterprise assistant mesh.
Classify the user's request into exactly ONE domain:

- finance       : budgets, financial reports/summaries, payments, payouts, spend.
- hr            : leave/PTO, benefits, HR policies, employment questions.
- internal_job  : internal job postings, internal mobility, open roles, careers.

Respond with ONLY the domain token on a single line: finance, hr, or internal_job.
If unclear, choose the closest match. Do not add any other text.
"""

VALID_DOMAINS = ("finance", "hr", "internal_job")


def get_gateway_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="GatewayAgent",
        instructions=GATEWAY_INSTRUCTIONS,
        log_path=log_path,
    )


def parse_domain(text: str) -> str:
    """Deterministically extracts a valid domain token from router output."""
    t = (text or "").strip().lower()
    # Direct token match first
    for d in VALID_DOMAINS:
        if d in t:
            return d
    # Keyword fallback
    if any(k in t for k in ("budget", "payment", "finance", "payout", "expense", "spend")):
        return "finance"
    if any(k in t for k in ("job", "role", "posting", "career", "mobility", "opening")):
        return "internal_job"
    return "hr"


agent = get_gateway_agent()
