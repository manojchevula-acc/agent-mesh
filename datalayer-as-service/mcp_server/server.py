"""
mcp_server/server.py
---------------------
FAB Pricing Recommendation MCP Server.

Exposes the fab_semantic MySQL views as MCP tools. All tools query ONLY the
fab_semantic schema (never raw or curated tables).

Tools:
  Core:
    customer_360, pricing_recommendation, profitability_summary,
    margin_analysis, rwa_impact
  Enhanced:
    new_customer_pricing, competitor_price_analysis, pricing_trace,
    segment_pricing_benchmark, operations_cost_impact, relationship_discount,
    win_loss_insights, policy_exception, non_compliant_deals,
    compare_fab_vs_competitor

Run (stdio — default, for local/Claude Desktop clients):
    python -m mcp_server.server

Run as a network service (streamable HTTP):
    MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.server
"""

import logging
import json
import os
from typing import Any

from fastmcp import FastMCP

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
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="FAB Pricing Recommendation MCP Server",
    instructions=(
        "This server provides tools to query FAB's semantic banking views "
        "(fab_semantic). Tools cover customer 360, pricing recommendation, "
        "pricing trace, competitor comparison, margin, profitability, RWA, "
        "segment benchmarks, operations cost, relationship discount, win/loss "
        "and policy exceptions. Pass an empty string to a filter to retrieve "
        "capped data (max 100 rows)."
    ),
)


def _to_json(data: list[dict[str, Any]]) -> str:
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Core tools
# ---------------------------------------------------------------------------
@mcp.tool()
def customer_360(customer_id: str = "") -> str:
    """360 customer profile: master data + aggregated deal KPIs (deals, win rate,
    volume, avg margin). Empty customer_id returns all customers (max 100)."""
    logger.info("[tool] customer_360 | customer_id=%r", customer_id)
    return _to_json(query_customer_360(customer_id))


@mcp.tool()
def pricing_recommendation(customer_id: str = "") -> str:
    """Pricing recommendation per deal: rebuilt system recommended price, approved
    price, expected margin, policy benchmarks and compliance flags."""
    logger.info("[tool] pricing_recommendation | customer_id=%r", customer_id)
    return _to_json(query_pricing_recommendation(customer_id))


@mcp.tool()
def profitability_summary(customer_id: str = "") -> str:
    """Profitability roll-up by product type: revenue, funding/operating/capital
    cost, net profit and profitability tier."""
    logger.info("[tool] profitability_summary | customer_id=%r", customer_id)
    return _to_json(query_profitability_summary(customer_id))


@mcp.tool()
def margin_analysis(customer_id: str = "") -> str:
    """Deal-level margin decomposition: net margin, spread over benchmark, variance
    vs recommended, and margin-below-minimum flag."""
    logger.info("[tool] margin_analysis | customer_id=%r", customer_id)
    return _to_json(query_margin_analysis(customer_id))


@mcp.tool()
def rwa_impact(customer_id: str = "") -> str:
    """RWA impact for won deals: exposure, risk weight, RWA, capital required and
    return on RWA."""
    logger.info("[tool] rwa_impact | customer_id=%r", customer_id)
    return _to_json(query_rwa_impact(customer_id))


# ---------------------------------------------------------------------------
# Enhanced tools
# ---------------------------------------------------------------------------
@mcp.tool()
def new_customer_pricing(customer_id: str = "", segment: str = "",
                         product_id: str = "", risk_rating: str = "") -> str:
    """Recommended price for a NEW customer with no relationship history, based on
    segment benchmark, product, risk rating, treasury rate and operations cost.
    Filter by any of customer_id, segment, product_id or risk_rating."""
    logger.info("[tool] new_customer_pricing | %r %r %r %r", customer_id, segment, product_id, risk_rating)
    return _to_json(query_new_customer_pricing(customer_id, segment, product_id, risk_rating))


@mcp.tool()
def competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> str:
    """Compare FAB offer vs competitor offer and return competitor_gap_bps plus a
    MATCH / COUNTER / ESCALATE / REJECT suggested action with reasoning."""
    logger.info("[tool] competitor_price_analysis | %r %r", customer_id, deal_id)
    return _to_json(query_competitor_price_analysis(customer_id, deal_id))


@mcp.tool()
def pricing_trace(customer_id: str = "", deal_id: str = "") -> str:
    """Step-by-step price build-up: treasury, target margin, risk premium, ops cost,
    relationship discount, final recommended price and an explanation sentence."""
    logger.info("[tool] pricing_trace | %r %r", customer_id, deal_id)
    return _to_json(query_pricing_trace(customer_id, deal_id))


@mcp.tool()
def segment_pricing_benchmark(segment: str = "", product_id: str = "") -> str:
    """Segment pricing guideline: target margin, base floor, new-customer buffer,
    max relationship discount and min profitability margin."""
    logger.info("[tool] segment_pricing_benchmark | %r %r", segment, product_id)
    return _to_json(query_segment_pricing_benchmark(segment, product_id))


@mcp.tool()
def operations_cost_impact(product_id: str = "", customer_segment: str = "") -> str:
    """Operational cost impact on pricing: ops cost margin and cost breakdown per
    product x customer segment."""
    logger.info("[tool] operations_cost_impact | %r %r", product_id, customer_segment)
    return _to_json(query_operations_cost_impact(product_id, customer_segment))


@mcp.tool()
def relationship_discount(customer_id: str) -> str:
    """Relationship discount eligibility and whether approval is required for a
    customer."""
    logger.info("[tool] relationship_discount | %r", customer_id)
    return _to_json(query_relationship_discount(customer_id))


@mcp.tool()
def win_loss_insights(customer_id: str = "", product_id: str = "", segment: str = "") -> str:
    """Win/loss insights: win rate, price gap vs recommended and competitor
    pressure, filterable by customer, product or segment."""
    logger.info("[tool] win_loss_insights | %r %r %r", customer_id, product_id, segment)
    return _to_json(query_win_loss_insights(customer_id, product_id, segment))


@mcp.tool()
def policy_exception(customer_id: str = "", deal_id: str = "") -> str:
    """Policy exceptions per deal with reasons (margin_below_min, price_below_floor,
    discount_exceeds_policy, competitor_match_requires_approval, high_rwa...)."""
    logger.info("[tool] policy_exception | %r %r", customer_id, deal_id)
    return _to_json(query_policy_exception(customer_id, deal_id))


@mcp.tool()
def non_compliant_deals(customer_id: str = "") -> str:
    """Only deals that breach at least one policy rule (is_exception = true)."""
    logger.info("[tool] non_compliant_deals | %r", customer_id)
    return _to_json(query_non_compliant_deals(customer_id))


@mcp.tool()
def compare_fab_vs_competitor(customer_id: str = "", deal_id: str = "") -> str:
    """Direct FAB recommended/approved price vs competitor offer comparison with the
    suggested pricing action."""
    logger.info("[tool] compare_fab_vs_competitor | %r %r", customer_id, deal_id)
    return _to_json(query_compare_fab_vs_competitor(customer_id, deal_id))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        host = os.getenv("MCP_HOST", "127.0.0.1")
        port = int(os.getenv("MCP_PORT", "9100"))
        logger.info("Starting FAB Pricing MCP Server (streamable HTTP) on %s:%s ...", host, port)
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        logger.info("Starting FAB Pricing Recommendation MCP Server (stdio) ...")
        mcp.run()
