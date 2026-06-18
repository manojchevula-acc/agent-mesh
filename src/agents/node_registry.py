"""Agent node registry.

Maps each mesh node name to its builder and human-readable card metadata so the
generic A2A server (``a2a_server.py``) and the launcher can construct any node by
name without hardcoding per-agent wiring.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agents.gateway_agent import get_gateway_agent
from src.agents.finance_agent import get_finance_agent
from src.agents.hr_agent import get_hr_agent
from src.agents.internal_job_agent import get_internal_job_agent
from src.agents.policy_agent import get_policy_agent
from src.agents.compliance_agent import get_compliance_agent

# node name -> (builder, public name, description)
AGENT_REGISTRY = {
    "gateway": (get_gateway_agent, "GatewayAgent", "Routes requests to the correct specialist domain."),
    "finance": (get_finance_agent, "FinanceAgent", "Leadership-only finance assistant (budgets, payments)."),
    "hr": (get_hr_agent, "HRAgent", "HR self-service assistant (leave, benefits, policies)."),
    "internal_job": (get_internal_job_agent, "InternalJobAgent", "Internal job postings and mobility assistant."),
    "policy": (get_policy_agent, "PolicyAgent", "Resolves which corporate rules apply to a request."),
    "compliance": (get_compliance_agent, "ComplianceAgent", "Semantic safety guardrail (injection, leakage, harm)."),
}

NODE_NAMES = list(AGENT_REGISTRY.keys())


def build_node(name: str, log_path: str = None):
    """Builds the agent for a node name. Returns (agent, public_name, description)."""
    if name not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent node '{name}'. Valid: {', '.join(NODE_NAMES)}")
    builder, public_name, description = AGENT_REGISTRY[name]
    return builder(log_path=log_path), public_name, description
