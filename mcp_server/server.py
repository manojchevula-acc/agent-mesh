"""
mcp_server/server.py
---------------------
FAB Pricing Recommendation MCP Server.

Exposes five tools that query the fab_semantic MySQL views:
  - customer_360
  - pricing_recommendation
  - profitability_summary
  - margin_analysis
  - rwa_impact

Run with:
    python -m mcp_server.server
"""

import logging
import json
from typing import Any

from fastmcp import FastMCP

from mcp_server.tools import (
    query_customer_360,
    query_pricing_recommendation,
    query_profitability_summary,
    query_margin_analysis,
    query_rwa_impact,
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
        "This server provides tools to query FAB's semantic banking views. "
        "All tools accept a customer_id and return relevant records from the "
        "fab_semantic MySQL schema. Pass an empty string to retrieve all records."
    ),
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _to_json(data: list[dict[str, Any]]) -> str:
    """Serialise results to a pretty-printed JSON string."""
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool 1 – customer_360
# ---------------------------------------------------------------------------
@mcp.tool()
def customer_360(customer_id: str) -> str:
    """
    Retrieve the 360-degree customer profile.

    Combines customer master data with aggregated deal KPIs including
    total deals, win rate, deal volume, and average margin.

    Args:
        customer_id: FAB customer identifier (e.g. CUST001).
                     Pass an empty string to retrieve all customers.

    Returns:
        JSON string with matching customer_360 records.
    """
    logger.info("[tool] customer_360 called | customer_id=%r", customer_id)
    results = query_customer_360(customer_id)
    return _to_json(results)


# ---------------------------------------------------------------------------
# Tool 2 – pricing_recommendation
# ---------------------------------------------------------------------------
@mcp.tool()
def pricing_recommendation(customer_id: str) -> str:
    """
    Retrieve pricing recommendation details for a customer.

    Shows each deal annotated with product information, the applicable
    pricing policy benchmarks, and three compliance flags:
      - price_below_policy_floor
      - margin_below_min
      - discount_exceeds_policy

    Args:
        customer_id: FAB customer identifier (e.g. CUST001).
                     Pass an empty string to retrieve all records.

    Returns:
        JSON string with matching pricing_recommendation_view records.
    """
    logger.info("[tool] pricing_recommendation called | customer_id=%r", customer_id)
    results = query_pricing_recommendation(customer_id)
    return _to_json(results)


# ---------------------------------------------------------------------------
# Tool 3 – profitability_summary
# ---------------------------------------------------------------------------
@mcp.tool()
def profitability_summary(customer_id: str) -> str:
    """
    Retrieve the profitability summary for a customer.

    Aggregated by product type showing total won deals, volume,
    average net margin, total expected margin (AED), and a
    profitability tier label (Loss-Making / Low / Medium / High).

    Args:
        customer_id: FAB customer identifier (e.g. CUST001).
                     Pass an empty string to retrieve all records.

    Returns:
        JSON string with matching profitability_summary records.
    """
    logger.info("[tool] profitability_summary called | customer_id=%r", customer_id)
    results = query_profitability_summary(customer_id)
    return _to_json(results)


# ---------------------------------------------------------------------------
# Tool 4 – margin_analysis
# ---------------------------------------------------------------------------
@mcp.tool()
def margin_analysis(customer_id: str) -> str:
    """
    Retrieve deal-level margin analysis for a customer.

    Includes net margin decomposition (approved price minus funding cost,
    risk premium, and relationship discount), spread over the treasury
    benchmark rate, and variance versus the system recommended price.

    Args:
        customer_id: FAB customer identifier (e.g. CUST001).
                     Pass an empty string to retrieve all records.

    Returns:
        JSON string with matching margin_analysis records.
    """
    logger.info("[tool] margin_analysis called | customer_id=%r", customer_id)
    results = query_margin_analysis(customer_id)
    return _to_json(results)


# ---------------------------------------------------------------------------
# Tool 5 – rwa_impact
# ---------------------------------------------------------------------------
@mcp.tool()
def rwa_impact(customer_id: str) -> str:
    """
    Retrieve RWA impact analysis for a customer's won deals.

    Shows RWA-weighted exposure, capital required (Basel III 8%),
    revenue, cost of funds, net revenue, and return on RWA per deal.

    Args:
        customer_id: FAB customer identifier (e.g. CUST001).
                     Pass an empty string to retrieve all records.

    Returns:
        JSON string with matching rwa_impact_view records.
    """
    logger.info("[tool] rwa_impact called | customer_id=%r", customer_id)
    results = query_rwa_impact(customer_id)
    return _to_json(results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting FAB Pricing Recommendation MCP Server ...")
    mcp.run()
