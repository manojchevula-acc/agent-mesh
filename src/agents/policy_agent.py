"""Policy agent node.

Shared advisory agent: resolves which corporate rules apply to a request. It
loads the rule base from ``data/policies.json`` so the knowledge base is the
single source of truth (no longer hardcoded in the prompt).
"""
import sys
import json
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent
from src.config import Config


def _load_policy_text() -> str:
    try:
        with open(Config.POLICIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return "No policy data available."

    lines = []
    for p in data.get("domain_policies", []):
        lines.append(f"[{p.get('domain', '').upper()}] {p.get('name', '')}: {p.get('guidelines', '')}")
    for p in data.get("policies", []):
        lines.append(f"{p.get('name', '')}: {p.get('guidelines', '')}")
    return "\n".join(lines) if lines else "No policy data available."


def _instructions() -> str:
    return (
        "You are the Policy agent. Resolve which corporate rules apply to a request "
        "and state clearly whether an action is permitted, restricted, or requires approval.\n\n"
        "Corporate policy knowledge base:\n"
        f"{_load_policy_text()}\n\n"
        "Answer concisely. Cite the relevant policy name. If a request is about a "
        "restricted resource or an outbound payment, say it requires approval."
    )


def get_policy_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="PolicyAgent",
        instructions=_instructions(),
        log_path=log_path,
    )


agent = get_policy_agent()
