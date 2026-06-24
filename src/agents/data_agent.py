"""Data Agent node.

A thin Microsoft Agent Framework agent that answers questions about FAB customer
and deal data. It holds NO business logic: all data access is delegated to the
DataLayer-as-a-Service over MCP (its 5 SQL-view tools are auto-discovered). The
LLM decides which tool(s) to call and synthesises the answer.

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
from src.integrations.mcp_clients import make_datalayer_mcp_tool

DATA_INSTRUCTIONS = """
You are the Data Agent for FAB's pricing and customer analytics.

You answer questions about customers, deals, pricing, margins, profitability, and
RWA/capital impact by calling the DataLayer tools. Available tools (each takes a
customer_id; pass an empty string to retrieve all records):
- customer_360            : 360° customer profile + aggregated deal KPIs.
- pricing_recommendation  : per-deal pricing vs policy benchmarks + compliance flags.
- profitability_summary   : profitability by product type + tier.
- margin_analysis         : per-deal margin decomposition vs treasury benchmark.
- rwa_impact              : RWA-weighted exposure + Basel III capital + return on RWA.

Rules:
- ALWAYS call the appropriate tool(s) before answering a data question. Never
  invent customer data, figures, or compliance verdicts.
- Extract the customer_id from the user's request (e.g. "CUST001"). If none is
  given and the question is customer-specific, ask for it.
- If a tool reports it is unavailable, say so plainly and do not fabricate data.
- Be concise; cite the tool/view your numbers came from.
"""


def get_data_agent(log_path: str = None, mcp_tool=None) -> Agent:
    """Builds the Data Agent.

    Args:
        log_path: optional audit log path.
        mcp_tool: a (connected) DataLayer MCP tool. When None, an unconnected tool
            is created — the caller is responsible for connecting it before use.
    """
    tool = mcp_tool or make_datalayer_mcp_tool()
    return create_demo_agent(
        name="DataAgent",
        instructions=DATA_INSTRUCTIONS,
        tools=[tool],
        log_path=log_path,
    )
