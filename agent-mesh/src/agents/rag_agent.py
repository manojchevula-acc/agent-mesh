"""RAG Agent node.

A thin Microsoft Agent Framework agent that answers policy/document questions by
retrieving grounded, cited context from RAG-as-a-Service over MCP. It holds NO
retrieval logic: embeddings, hybrid search, reranking, freshness and answer
generation all live in the RAG service. The agent's ``search_documents`` tool is
auto-discovered from the service's MCP server.

The MCP tool is connected (and kept alive) by the A2A server; for in-process use
(e.g. DevUI) an unconnected tool is created and must be connected by the caller.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Agent
from src.agents.agent_factory import create_demo_agent
from src.config import Config
from src.integrations.mcp_clients import make_rag_mcp_tool

RAG_INSTRUCTIONS = """
You are the RAG Agent for FAB's credit and regulatory policy knowledge base.

You answer questions by retrieving grounded context from policy documents via the
``search_documents`` tool (args: query, top_k, generate_answer). Use it for any
question about pricing floors/ceilings, fees, regulatory rules, credit policy, or
product guidelines.

Rules:
- ALWAYS call search_documents before answering. Never invent figures or rules.
- Set generate_answer=true when the user wants a direct, cited answer; otherwise
  summarise the retrieved passages yourself.
- Always cite the source document and clause/section for each fact.
- If a passage is flagged stale (⚠), warn the user it may be outdated.
- If retrieval is unavailable, say so plainly and do not fabricate an answer.
"""


def get_rag_agent(log_path: str = None, mcp_tool=None) -> Agent:
    """Builds the RAG Agent.

    Args:
        log_path: optional audit log path.
        mcp_tool: a (connected) RAG MCP tool. When None, an unconnected tool is
            created — the caller is responsible for connecting it before use.
    """
    tool = mcp_tool or make_rag_mcp_tool()
    return create_demo_agent(
        name="RAGAgent",
        instructions=RAG_INSTRUCTIONS,
        tools=[tool],
        log_path=log_path,
        model=Config.RAG_AGENT_MODEL,
        api_key=Config.RAG_AGENT_API_KEY,
    )
