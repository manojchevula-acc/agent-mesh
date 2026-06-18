"""Gateway / Router agent node (the mesh front door).

A lightweight LLM classifier that maps a user request to one or more domains:
finance, hr, or internal_job. For multi-domain queries it also decomposes the
original question into per-domain sub-questions so each specialist agent only
receives the portion of the query it should answer.
"""
import sys
import pathlib
from typing import Dict, List

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

For a SINGLE-DOMAIN query, return just the domain token on one line.
For a MULTI-DOMAIN query, return each domain on its own line as:
  domain: specific sub-question for that domain

Respond with ONLY domain tokens or "domain: sub-question" lines — nothing else.

Examples:
  "What is my leave balance?"
  -> hr

  "What is the engineering budget?"
  -> finance

  "Show me the leave policy and the engineering budget"
  -> hr: what is the leave policy
     finance: what is the engineering budget

  "Any open backend roles and what is my leave balance?"
  -> hr: what is my leave balance
     internal_job: what are the open backend roles

  "Tell me about leave policy, engineering budget and open backend roles"
  -> hr: what is the leave policy
     finance: what is the engineering budget
     internal_job: what are the open backend roles
"""

VALID_DOMAINS = ("finance", "hr", "internal_job")


def get_gateway_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="GatewayAgent",
        instructions=GATEWAY_INSTRUCTIONS,
        log_path=log_path,
    )


def parse_domain_queries(text: str, original_query: str) -> Dict[str, str]:
    """Parses gateway output into {domain: sub_query} mapping.

    Handles two formats:
      - "finance"                  (single token)   -> {"finance": original_query}
      - "finance: what is budget"  (domain: sub-q)  -> {"finance": "what is budget"}

    Always returns at least one entry.
    """
    result: Dict[str, str] = {}

    for line in (text or "").strip().splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if not lower:
            continue
        for d in VALID_DOMAINS:
            if lower.startswith(d + ":"):
                sub = stripped[len(d) + 1:].strip()
                result[d] = sub if sub else original_query
                break
            elif lower == d or lower.startswith(d + " "):
                result[d] = original_query
                break

    if result:
        return result

    # Keyword fallback
    tl = (text or "").lower()
    if any(k in tl for k in ("budget", "payment", "finance", "payout", "expense", "spend")):
        return {"finance": original_query}
    if any(k in tl for k in ("job", "role", "posting", "career", "mobility", "opening")):
        return {"internal_job": original_query}
    return {"hr": original_query}


def parse_domains(text: str) -> List[str]:
    """Extracts all valid domain tokens from gateway output. Always returns at least one."""
    return list(parse_domain_queries(text, "").keys())


def parse_domain(text: str) -> str:
    """Single-domain extract — kept for test-suite back-compat. Returns first domain."""
    return parse_domains(text)[0]


agent = get_gateway_agent()
