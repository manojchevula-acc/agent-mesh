"""MCP client factories for external services.

The Data Agent and RAG Agent are *thin* Microsoft Agent Framework agents: they
hold no domain logic. Instead they consume the tool surface that each external
service already exposes over MCP (Model Context Protocol) using streamable HTTP.
The services run independently on their own ports/processes:

    - DataLayer-as-a-Service: FastMCP server (5 SQL-view tools) on DATALAYER_MCP_URL.
    - RAG-as-a-Service:        MCP server (search_documents) on RAG_MCP_URL, which
                               internally calls its own REST /api/v1/retrieve.

These factories return *unconnected* ``MCPStreamableHTTPTool`` instances. The A2A
server (``a2a_server.py``) opens the tool as an async context manager and keeps
the session alive for the node's lifetime, so every request the node serves can
call the service's tools. The framework auto-discovers each server's tools — we
do not hand-write per-tool wrappers, which keeps the agents genuinely thin.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import MCPStreamableHTTPTool

from src.config import Config


def make_datalayer_mcp_tool() -> MCPStreamableHTTPTool:
    """Builds an (unconnected) MCP client for the DataLayer FastMCP server."""
    return MCPStreamableHTTPTool(
        name="datalayer",
        url=Config.DATALAYER_MCP_URL,
        description=(
            "FAB semantic banking data: customer_360, pricing_recommendation, "
            "profitability_summary, margin_analysis, rwa_impact (by customer_id)."
        ),
        request_timeout=Config.MCP_REQUEST_TIMEOUT,
    )


def make_rag_mcp_tool() -> MCPStreamableHTTPTool:
    """Builds an (unconnected) MCP client for the RAG service's MCP server."""
    headers = {"X-API-Key": Config.RAG_API_KEY} if Config.RAG_API_KEY else None
    kwargs = dict(
        name="rag",
        url=Config.RAG_MCP_URL,
        description=(
            "Retrieval over FAB credit & regulatory policy documents "
            "(search_documents → grounded, cited passages/answers)."
        ),
        request_timeout=Config.MCP_REQUEST_TIMEOUT,
    )
    if headers:
        # Forward an API key to the RAG MCP server when one is configured.
        kwargs["header_provider"] = lambda _existing: headers
    return MCPStreamableHTTPTool(**kwargs)


# node name -> factory for the MCP tool that node's agent consumes.
MCP_TOOL_FACTORIES = {
    "data_agent": make_datalayer_mcp_tool,
    "rag_agent": make_rag_mcp_tool,
}
