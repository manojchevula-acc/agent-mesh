"""Agent node registry.

Maps each mesh node name to its builder and human-readable card metadata so the
generic A2A server (``a2a_server.py``) and the launcher can construct any node by
name without hardcoding per-agent wiring.

MCP-backed nodes (``MCP_BACKED_NODES``) receive a pre-connected ``mcp_tools``
list from the A2A server.  The list is obtained by the server via the connector
functions in ``src/integrations/mcp_clients.py`` and passed to the builder so
the session is kept alive for the node's lifetime.

Architecture (AgentMesh 15.0.6.2026 / LangGraph):
- PriceAssistAgent is the primary FAB banking orchestrator.
- DataAgent and RAGAgent are thin MCP clients consumed by PriceAssistAgent.
- ComplianceAgent is the semantic safety guardrail (layer 2).
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
    "compliance":   (get_compliance_agent,   "ComplianceAgent",   "Semantic safety guardrail."),
    "data_agent":   (get_data_agent,         "DataAgent",         "Structured data via DataLayer MCP."),
    "rag_agent":    (get_rag_agent,          "RAGAgent",          "Banking knowledge via RAG MCP."),
    "price_assist": (get_price_assist_agent, "PriceAssistAgent",  "Primary FAB banking orchestrator."),
}

# Nodes whose agent consumes an external service over MCP. The A2A server connects
# the MCP tools and passes them to the builder via ``mcp_tools=``.
MCP_BACKED_NODES = {"data_agent", "rag_agent"}

NODE_NAMES = list(AGENT_REGISTRY.keys())


def build_node(name: str, log_path: str = None, mcp_tools: list = None):
    """Builds the agent for a node name. Returns (agent, public_name, description).

    For MCP-backed nodes, pass a pre-connected ``mcp_tools`` list; if omitted
    the agent starts with no MCP tools (caller must connect before use).
    """
    if name not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent node '{name}'. Valid: {', '.join(NODE_NAMES)}")
    builder, public_name, description = AGENT_REGISTRY[name]
    if name in MCP_BACKED_NODES:
        agent = builder(log_path=log_path, mcp_tools=mcp_tools or [])
    else:
        agent = builder(log_path=log_path)
    return agent, public_name, description
