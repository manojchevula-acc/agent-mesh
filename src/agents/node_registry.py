"""Agent node registry.

Maps each mesh node name to its builder and human-readable card metadata so the
generic A2A server (``a2a_server.py``) and the launcher can construct any node by
name without hardcoding per-agent wiring.

Some nodes are *MCP-backed*: their agent consumes an external service over MCP
(see ``src/integrations/mcp_clients.py``). Their builders accept a ``mcp_tool``
keyword so the A2A server can pass a connected MCP session (kept alive for the
node's lifetime). ``MCP_BACKED_NODES`` lists them.

Architecture (AgentMesh 15.0.6.2026):
- PriceAssistAgent is the primary FAB banking orchestrator — all user requests are
  routed to it after guardrail/RBAC/compliance checks.
- DataAgent and RAGAgent are thin MCP clients consumed by PriceAssistAgent.
- ComplianceAgent is the semantic safety guardrail (layer 2).
- GatewayAgent and PolicyAgent have been removed; their responsibilities are now
  handled by PriceAssistAgent's internal intent classification.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.agents.compliance_agent import get_compliance_agent
from src.agents.data_agent import get_data_agent
from src.agents.rag_agent import get_rag_agent
from src.agents.price_assist_agent import get_price_assist_agent

# node name -> (builder, public name, description)
AGENT_REGISTRY = {
    "compliance": (get_compliance_agent, "ComplianceAgent", "Semantic safety guardrail (injection, leakage, harm)."),
    "data_agent": (get_data_agent, "DataAgent", "Customer/deal structured data via DataLayer-as-a-Service (MCP)."),
    "rag_agent": (get_rag_agent, "RAGAgent", "Banking knowledge retrieval via RAG-as-a-Service (MCP)."),
    "price_assist": (get_price_assist_agent, "PriceAssistAgent",
                     "Primary FAB banking assistant — intent classification, orchestration, delegation to Data & RAG."),
}

# Nodes whose agent consumes an external service over MCP. The A2A server connects
# the MCP tool and passes it to the builder via ``mcp_tool=``.
MCP_BACKED_NODES = {"data_agent", "rag_agent"}

NODE_NAMES = list(AGENT_REGISTRY.keys())


def build_node(name: str, log_path: str = None, mcp_tool=None):
    """Builds the agent for a node name. Returns (agent, public_name, description).

    For MCP-backed nodes, pass a connected ``mcp_tool``; if omitted the builder
    creates an unconnected tool (which the caller must connect before use).
    """
    if name not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent node '{name}'. Valid: {', '.join(NODE_NAMES)}")
    builder, public_name, description = AGENT_REGISTRY[name]
    if name in MCP_BACKED_NODES:
        agent = builder(log_path=log_path, mcp_tool=mcp_tool)
    else:
        agent = builder(log_path=log_path)
    return agent, public_name, description
