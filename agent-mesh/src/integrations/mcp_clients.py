"""MCP client connectors for external services.

The Data Agent and RAG Agent are *thin* LangGraph ReAct agents: they hold no
domain logic.  Instead they consume the tool surface that each external service
exposes over MCP (Model Context Protocol) using streamable HTTP transport.
The services run independently on their own ports/processes:

    - DataLayer-as-a-Service: FastMCP server (SQL-view tools) on DATALAYER_MCP_URL.
    - RAG-as-a-Service:        MCP server (search_documents) on RAG_MCP_URL.

Each connector function opens a ``MultiServerMCPClient`` and returns
``(client, tools_list)``.  The A2A server keeps the client alive for the
node's lifetime; the caller must call ``client.__aexit__(None, None, None)``
on shutdown to cleanly close the session.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.config import Config


async def connect_datalayer_mcp() -> tuple:
    """Returns (client, tools_list). Caller must call client.__aexit__ on shutdown."""
    client = MultiServerMCPClient({
        "datalayer": {
            "url":       Config.DATALAYER_MCP_URL,
            "transport": "streamable_http",
        }
    })
    await client.__aenter__()
    return client, client.get_tools()


async def connect_rag_mcp() -> tuple:
    """Returns (client, tools_list). Caller must call client.__aexit__ on shutdown."""
    headers = {"X-API-Key": Config.RAG_API_KEY} if Config.RAG_API_KEY else {}
    spec: dict = {"url": Config.RAG_MCP_URL, "transport": "streamable_http"}
    if headers:
        spec["headers"] = headers
    client = MultiServerMCPClient({"rag": spec})
    await client.__aenter__()
    return client, client.get_tools()


# node name -> async connector function
MCP_CONNECTORS = {
    "data_agent": connect_datalayer_mcp,
    "rag_agent":  connect_rag_mcp,
}
