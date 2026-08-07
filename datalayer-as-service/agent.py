"""
agent.py
--------
FAB Pricing Recommendation Data Assistant.

A LangChain + Groq agent that answers banking pricing questions by querying the
fab_semantic MySQL views through the mcp_server/tools.py functions. The agent
wraps those functions as native LangChain tools (querying ONLY the semantic
layer — never raw or curated tables).

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

from mcp_server.tools import (
    query_customer_360,
    query_pricing_recommendation,
    query_profitability_summary,
    query_margin_analysis,
    query_rwa_impact,
    query_new_customer_pricing,
    query_competitor_price_analysis,
    query_pricing_trace,
    query_segment_pricing_benchmark,
    query_operations_cost_impact,
    query_relationship_discount,
    query_win_loss_insights,
    query_policy_exception,
    query_non_compliant_deals,
    query_compare_fab_vs_competitor,
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

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


# ---------------------------------------------------------------------------
# LangChain tools — thin wrappers around mcp_server.tools functions.
# ---------------------------------------------------------------------------

@tool
def customer_360(customer_id: str = "") -> str:
    """360 customer profile (master data + deal KPIs) for a customer_id e.g. 'CUST001'."""
    return json.dumps(query_customer_360(customer_id), default=str)


@tool
def pricing_recommendation(customer_id: str = "") -> str:
    """Pricing recommendation per deal for a customer_id: rebuilt recommended price,
    approved price, expected margin, policy benchmarks and compliance flags."""
    return json.dumps(query_pricing_recommendation(customer_id), default=str)


@tool
def margin_analysis(customer_id: str = "") -> str:
    """Deal-level margin analysis for a customer_id: net margin, spread over benchmark,
    variance vs recommended and margin-below-minimum flag."""
    return json.dumps(query_margin_analysis(customer_id), default=str)


@tool
def profitability_summary(customer_id: str = "") -> str:
    """Profitability summary for a customer_id: revenue, funding/operating/capital cost,
    net profit and profitability tier."""
    return json.dumps(query_profitability_summary(customer_id), default=str)


@tool
def rwa_impact(customer_id: str = "") -> str:
    """RWA impact for a customer_id's won deals: exposure, RWA, capital required and
    return on RWA."""
    return json.dumps(query_rwa_impact(customer_id), default=str)


@tool
def new_customer_pricing(customer_id: str = "", segment: str = "",
                         product_id: str = "", risk_rating: str = "") -> str:
    """Recommended price for a NEW customer with no relationship history. Filter by any
    of customer_id (e.g. 'CUST021'), segment (e.g. 'SME'), product_id (e.g. 'PROD001')
    or risk_rating (e.g. 'Medium')."""
    return json.dumps(query_new_customer_pricing(customer_id, segment, product_id, risk_rating), default=str)


@tool
def competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> str:
    """Compare FAB offer vs competitor offer; returns competitor_gap_bps and a
    MATCH / COUNTER / ESCALATE / REJECT suggested action with reasoning."""
    return json.dumps(query_competitor_price_analysis(customer_id, deal_id), default=str)


@tool
def pricing_trace(customer_id: str = "", deal_id: str = "") -> str:
    """Step-by-step price build-up (treasury, target margin, risk premium, ops cost,
    relationship discount, final recommended price) with an explanation sentence.
    Filter by customer_id and/or deal_id (e.g. 'DEAL040')."""
    return json.dumps(query_pricing_trace(customer_id, deal_id), default=str)


@tool
def segment_pricing_benchmark(segment: str = "", product_id: str = "") -> str:
    """Segment pricing guideline (target margin, base floor, new-customer buffer,
    max relationship discount, min profitability) by segment and/or product_id."""
    return json.dumps(query_segment_pricing_benchmark(segment, product_id), default=str)


@tool
def operations_cost_impact(product_id: str = "", customer_segment: str = "") -> str:
    """Operational cost impact on pricing (ops cost margin, cost breakdown) by
    product_id and/or customer_segment."""
    return json.dumps(query_operations_cost_impact(product_id, customer_segment), default=str)


@tool
def relationship_discount(customer_id: str) -> str:
    """Relationship discount eligibility and approval requirement for a customer_id."""
    return json.dumps(query_relationship_discount(customer_id), default=str)


@tool
def win_loss_insights(customer_id: str = "", product_id: str = "", segment: str = "") -> str:
    """Win/loss insights (win rate, price gap vs recommended, competitor pressure)
    filterable by customer_id, product_id or segment."""
    return json.dumps(query_win_loss_insights(customer_id, product_id, segment), default=str)


@tool
def policy_exception(customer_id: str = "", deal_id: str = "") -> str:
    """Policy exceptions per deal with reasons. Filter by customer_id and/or deal_id."""
    return json.dumps(query_policy_exception(customer_id, deal_id), default=str)


@tool
def non_compliant_deals(customer_id: str = "") -> str:
    """List only the deals that breach at least one policy rule (optionally by customer_id)."""
    return json.dumps(query_non_compliant_deals(customer_id), default=str)


@tool
def compare_fab_vs_competitor(customer_id: str = "", deal_id: str = "") -> str:
    """Direct FAB price vs competitor offer comparison with the suggested action."""
    return json.dumps(query_compare_fab_vs_competitor(customer_id, deal_id), default=str)


TOOLS = [
    customer_360,
    pricing_recommendation,
    margin_analysis,
    profitability_summary,
    rwa_impact,
    new_customer_pricing,
    competitor_price_analysis,
    pricing_trace,
    segment_pricing_benchmark,
    operations_cost_impact,
    relationship_discount,
    win_loss_insights,
    policy_exception,
    non_compliant_deals,
    compare_fab_vs_competitor,
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are FAB Pricing Recommendation Data Assistant. Use only \
semantic-layer tools. Do not invent data. For every pricing recommendation, \
explain the calculation, policy compliance, profitability impact, RWA impact and \
competitor comparison where available.

STRICT RULES:
- Use ONLY the data returned by the provided tools (they query the fab_semantic
  MySQL layer — semantic views only, never raw or curated tables).
- NEVER invent or assume customer information, numbers, deals or prices.
- Always call the appropriate tool(s) before answering any data question.
- If the user asks about a specific customer or deal but does NOT provide the
  customer_id (e.g. CUST001) or deal_id (e.g. DEAL040), politely ask for it
  before calling a tool. For "new customer" questions you may use segment,
  product_id and risk_rating with the new_customer_pricing tool.
- If a tool returns a 'message' saying no records were found, tell the user no
  data is available for that filter.
- If a tool returns an 'error', tell the user there was a problem reading the
  data and suggest checking the database connection.
- Do NOT expose SQL in the final answer unless the user explicitly asks for it.

TOOL SELECTION GUIDE:
- Interest rate / price for an existing customer  -> pricing_recommendation
- Interest rate for a NEW customer (no history)   -> new_customer_pricing
- Step-by-step price explanation                  -> pricing_trace
- Competitor is lower / match-reject-counter      -> competitor_price_analysis / compare_fab_vs_competitor
- Which deals are non-compliant and why           -> non_compliant_deals / policy_exception
- Competitor pricing pressure by product/segment  -> competitor_price_analysis / win_loss_insights
- Operations cost impact on pricing               -> operations_cost_impact
- Profitability and RWA impact                     -> profitability_summary + rwa_impact
- Relationship discount eligibility / approval    -> relationship_discount
- Win/loss insights                                -> win_loss_insights
- Segment benchmark                                -> segment_pricing_benchmark

WHEN ANSWERING, prefer this business-friendly structure:
- Recommendation
- Reasoning
- Pricing components (treasury, target margin, risk premium, ops cost, discount)
- Policy status (and which rule is breached, if any)
- Risk / RWA impact
- Competitor comparison (where available)
- Suggested next action
Use concise tables or bullet points and highlight negative margins or breaches.
"""


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

def build_agent(model: str = DEFAULT_MODEL, temperature: float = 0):
    """Build a LangChain Groq agent wired with the semantic-layer tools."""
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file before running.")

    from langchain_groq import ChatGroq
    from langchain.agents import create_agent

    llm = ChatGroq(model=model, temperature=temperature)
    agent = create_agent(model=llm, tools=TOOLS, system_prompt=SYSTEM_PROMPT)
    logger.info("Agent built with model=%s and %d tools", model, len(TOOLS))
    return agent


def run_agent(agent, user_input: str) -> str:
    """Invoke the agent with a single user message and return the final answer."""
    result = agent.invoke({"messages": [HumanMessage(content=user_input)]})
    return result["messages"][-1].content


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
    print("Example: What interest rate should I offer to customer CUST001?")

    while True:
        user_input = input("\nYou: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        try:
            print("\nBot:")
            print(run_agent(agent, user_input))
        except Exception as exc:
            logger.error("Agent error: %s", exc)
            print(f"\nError: {exc}")


if __name__ == "__main__":
    main()
