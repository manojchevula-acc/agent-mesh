"""
agent.py
--------
FAB Pricing Recommendation Data Assistant.

A LangChain + Groq agent that answers banking business questions by querying
the fab_semantic MySQL views through the existing mcp_server/tools.py functions.

Rather than spinning up an MCP stdio client, this agent wraps the same query
functions from mcp_server.tools as native LangChain tools — simpler and more
reliable for a local demo while still querying ONLY the semantic layer.

Usage:
    python agent.py            # interactive CLI chat
    (or import build_agent / run_agent from app.py)
"""

import os
import json
import logging

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage

# Reuse the existing semantic-layer query functions (fab_semantic views only)
from mcp_server.tools import (
    query_customer_360,
    query_pricing_recommendation,
    query_profitability_summary,
    query_margin_analysis,
    query_rwa_impact,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LangChain tools — thin wrappers around mcp_server.tools functions
# Each returns a JSON string so the LLM can read the structured records.
# ---------------------------------------------------------------------------

@tool
def customer_360(customer_id: str) -> str:
    """Get the 360-degree customer profile (master data + aggregated deal KPIs
    such as total deals, win rate, deal volume and average margin) for a given
    customer_id, e.g. 'CUST001'. Use this for general customer profile questions."""
    return json.dumps(query_customer_360(customer_id), default=str)


@tool
def pricing_recommendation(customer_id: str) -> str:
    """Get pricing recommendation details for a customer_id (e.g. 'CUST001').
    Returns each deal with recommended price, approved price, expected margin,
    policy benchmarks and compliance flags (price_below_policy_floor,
    margin_below_min, discount_exceeds_policy, policy_compliant)."""
    return json.dumps(query_pricing_recommendation(customer_id), default=str)


@tool
def margin_analysis(customer_id: str) -> str:
    """Get deal-level margin analysis for a customer_id (e.g. 'CUST001').
    Returns net margin decomposition, spread over the treasury benchmark, and
    variance versus the recommended price for each deal."""
    return json.dumps(query_margin_analysis(customer_id), default=str)


@tool
def profitability_summary(customer_id: str) -> str:
    """Get the profitability summary for a customer_id (e.g. 'CUST001').
    Aggregated by product type with total won deals, volume, average net margin,
    total expected margin (AED) and a profitability tier label."""
    return json.dumps(query_profitability_summary(customer_id), default=str)


@tool
def rwa_impact(customer_id: str) -> str:
    """Get RWA (Risk-Weighted Assets) impact analysis for a customer_id
    (e.g. 'CUST001'). Returns RWA-weighted exposure, capital required (Basel III
    8%), revenue, cost of funds, net revenue and return on RWA per won deal."""
    return json.dumps(query_rwa_impact(customer_id), default=str)


TOOLS = [
    customer_360,
    pricing_recommendation,
    margin_analysis,
    profitability_summary,
    rwa_impact,
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the FAB Pricing Recommendation Data Assistant.

Your job is to help relationship managers and pricing analysts understand
customer pricing, margins, profitability and capital (RWA) impact.

STRICT RULES:
- Use ONLY the data returned by the provided tools. These tools query the
  fab_semantic MySQL layer (semantic views only — never raw or curated tables).
- NEVER invent or assume customer information, numbers, or deals.
- Always call the appropriate tool(s) before answering any data question.
- If the user does not provide a customer_id (format like CUST001), politely
  ask them to provide one before calling any tool.
- If a tool returns a 'message' saying no records were found, clearly tell the
  user that no data is available for that customer_id.
- If a tool returns an 'error', tell the user there was a problem reading the
  data and suggest checking the database connection.

WHEN ANSWERING:
- Explain results in clear, business-friendly language (avoid raw JSON).
- Where relevant, explain: recommended price vs approved price, expected margin,
  policy compliance (and which policy rule was breached if non-compliant),
  profitability tier, and RWA / capital impact.
- Use concise tables or bullet points for multiple deals.
- Highlight risks such as negative margins, loss-making deals, or policy breaches.
"""


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

def build_agent(model: str = "qwen/qwen3.6-27b", temperature: float = 0):
    """
    Build and return a LangChain Groq agent wired with the semantic-layer tools.

    Raises:
        RuntimeError: if GROQ_API_KEY is not set.
    """
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your .env file before running."
        )

    # Imported lazily so a missing dependency surfaces a clear error
    from langchain_groq import ChatGroq
    from langchain.agents import create_agent

    llm = ChatGroq(model=model, temperature=temperature)
    agent = create_agent(model=llm, tools=TOOLS, system_prompt=SYSTEM_PROMPT)
    logger.info("Agent built with model=%s and %d tools", model, len(TOOLS))
    return agent


def run_agent(agent, user_input: str) -> str:
    """
    Invoke the agent synchronously with a single user message and return the
    assistant's final text answer.
    """
    result = agent.invoke({"messages": [HumanMessage(content=user_input)]})
    final_message = result["messages"][-1]
    return final_message.content


# ---------------------------------------------------------------------------
# CLI chat loop
# ---------------------------------------------------------------------------

def main():
    print("=" * 55)
    print("  FAB Pricing Recommendation Data Assistant (CLI)")
    print("=" * 55)

    try:
        agent = build_agent()
    except RuntimeError as exc:
        print(f"\nStartup error: {exc}")
        return

    print("\nChat started. Type 'exit' or 'quit' to stop.")
    print("Example: Show customer profile for CUST001")

    while True:
        user_input = input("\nYou: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        try:
            answer = run_agent(agent, user_input)
            print("\nBot:")
            print(answer)
        except Exception as exc:
            logger.error("Agent error: %s", exc)
            print(f"\nError: {exc}")


if __name__ == "__main__":
    main()