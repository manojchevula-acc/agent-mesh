"""
mcp_server/pricing_server.py
------------------------------
FAB Pricing Engine MCP Server  (port 9200)

Exposes pricing-decision fab_semantic views as MCP tools.
Use this server for questions about HOW to price a deal — recommendations,
step-by-step price build, competitor comparison, segment benchmarks,
operations cost impact, and policy compliance.

Tools (9):
    pricing_recommendation   — recommended price per deal with compliance flags
    new_customer_pricing     — price for a new / prospect customer
    competitor_price_analysis — FAB offer vs competitor offer + suggested action
    pricing_trace            — step-by-step price build-up for a deal
    segment_pricing_benchmark — target margin / floor / discount limits by segment
    operations_cost_impact   — operational cost margin per product x segment
    policy_exception         — policy compliance per deal (margin, floor, discount)
    non_compliant_deals      — all deals that breach at least one policy rule
    compare_fab_vs_competitor — direct FAB vs competitor comparison with action

Authentication:
    Tokens are RS256 JWTs issued by the hub, audience = MCP_SERVER_ID.
    FastMCP JWTVerifier validates tokens; BearerClaimsMiddleware extracts
    claims into a ContextVar for per-tool RBAC via require_role().

Run as a network service (streamable HTTP):
    MCP_SERVER_ID=fab-pricing-server MCP_TRANSPORT=http \\
        MCP_HOST=127.0.0.1 MCP_PORT=9200 python -m mcp_server.pricing_server
"""

import json
import logging
import os
import pathlib
from typing import Any

# Load .env before importing auth — auth.py reads env vars at module level.
try:
    from dotenv import load_dotenv as _load_dotenv
    _here = pathlib.Path(__file__).resolve().parent
    _load_dotenv(_here.parent.parent / ".env")          # project root .env
    _load_dotenv(_here.parent / ".env", override=True)  # datalayer-as-service/.env (MySQL)
except ImportError:
    pass

import uvicorn
from fastmcp import FastMCP

from mcp_server.auth import build_jwt_verifier, claims_middleware, MCP_AUTH_ENABLED, require_role, audit_log

from mcp_server.tools import (
    query_pricing_recommendation,
    query_new_customer_pricing,
    query_competitor_price_analysis,
    query_pricing_trace,
    query_segment_pricing_benchmark,
    query_operations_cost_impact,
    query_policy_exception,
    query_non_compliant_deals,
    query_compare_fab_vs_competitor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# FastMCP JWTVerifier validates RS256 tokens issued by the hub.
# audience = MCP_SERVER_ID ensures tokens cannot be reused across servers.
mcp = FastMCP(
    name="FAB Pricing Engine MCP Server",
    instructions=(
        "This server answers questions about how to price a deal at FAB — "
        "pricing recommendations, step-by-step price build (treasury + margin + "
        "risk premium + ops cost + discount), competitor comparison with a suggested "
        "action (MATCH/COUNTER/ESCALATE/REJECT), segment pricing benchmarks, "
        "operational cost impact, and policy compliance / exception detection. "
        "Pass an empty string to a filter to retrieve all records (max 15 rows)."
    ),
    auth=build_jwt_verifier(),
)


def _to_json(data: list[dict[str, Any]]) -> str:
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Pricing recommendation tools
# ---------------------------------------------------------------------------

@mcp.tool()
def pricing_recommendation(customer_id: str = "") -> str:
    """Pricing recommendation per deal: rebuilt system recommended price,
    approved price, expected margin, policy benchmarks and compliance flags
    (price_below_policy_floor, margin_below_min, discount_exceeds_policy).
    Filter by customer_id (e.g. 'CUST001') or pass empty string for all deals."""
    require_role("admin", "agent")
    audit_log("pricing_recommendation", {"customer_id": customer_id})
    logger.info("[tool] pricing_recommendation | customer_id=%r", customer_id)
    return _to_json(query_pricing_recommendation(customer_id))


@mcp.tool()
def new_customer_pricing(customer_id: str = "", segment: str = "",
                         product_id: str = "", risk_rating: str = "") -> str:
    """Recommended price for a NEW customer with no relationship history.
    Builds price from segment benchmark + treasury rate + ops cost + new-customer
    buffer. Filter by customer_id, segment (e.g. 'SME'), product_id or
    risk_rating (e.g. 'Low', 'Medium', 'High')."""
    require_role("admin", "agent")
    audit_log("new_customer_pricing", {"customer_id": customer_id, "segment": segment,
                                       "product_id": product_id, "risk_rating": risk_rating})
    logger.info("[tool] new_customer_pricing | %r %r %r %r",
                customer_id, segment, product_id, risk_rating)
    return _to_json(query_new_customer_pricing(customer_id, segment, product_id, risk_rating))


@mcp.tool()
def competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> str:
    """Compare FAB offer vs competitor offer: competitor_gap_bps,
    suggested action (MATCH / COUNTER / ESCALATE / REJECT) and reasoning.
    Filter by customer_id and/or deal_id or pass empty strings for all."""
    require_role("admin", "agent")
    audit_log("competitor_price_analysis", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] competitor_price_analysis | %r %r", customer_id, deal_id)
    return _to_json(query_competitor_price_analysis(customer_id, deal_id))


@mcp.tool()
def pricing_trace(customer_id: str = "", deal_id: str = "") -> str:
    """Step-by-step price build-up for a deal: treasury rate → target margin →
    risk premium → operations cost → relationship discount = final recommended
    price. Includes an explanation sentence for each component.
    Filter by customer_id and/or deal_id or pass empty strings for all."""
    require_role("admin", "agent")
    audit_log("pricing_trace", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] pricing_trace | %r %r", customer_id, deal_id)
    return _to_json(query_pricing_trace(customer_id, deal_id))


@mcp.tool()
def segment_pricing_benchmark(segment: str = "", product_id: str = "") -> str:
    """Segment-level pricing guideline: target margin pct, base margin floor,
    new-customer buffer, max relationship discount allowed and min profitability
    margin. Filter by segment (e.g. 'SME', 'Corporate') and/or product_id."""
    require_role("admin", "agent")
    audit_log("segment_pricing_benchmark", {"segment": segment, "product_id": product_id})
    logger.info("[tool] segment_pricing_benchmark | %r %r", segment, product_id)
    return _to_json(query_segment_pricing_benchmark(segment, product_id))


@mcp.tool()
def operations_cost_impact(product_id: str = "", customer_segment: str = "") -> str:
    """Operational cost impact on pricing: ops_cost_margin_pct and cost
    breakdown (onboarding, monthly servicing, exception handling) per
    product x customer segment combination."""
    require_role("admin", "agent")
    audit_log("operations_cost_impact", {"product_id": product_id, "customer_segment": customer_segment})
    logger.info("[tool] operations_cost_impact | %r %r", product_id, customer_segment)
    return _to_json(query_operations_cost_impact(product_id, customer_segment))


@mcp.tool()
def policy_exception(customer_id: str = "", deal_id: str = "") -> str:
    """Policy compliance per deal: lists exceptions with reasons —
    margin_below_min, price_below_floor, discount_exceeds_policy,
    competitor_match_requires_approval, high_rwa. Also shows whether the
    deal is overall policy_compliant. Filter by customer_id and/or deal_id."""
    require_role("admin")
    audit_log("policy_exception", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] policy_exception | %r %r", customer_id, deal_id)
    return _to_json(query_policy_exception(customer_id, deal_id))


@mcp.tool()
def non_compliant_deals(customer_id: str = "") -> str:
    """Only deals that breach at least one pricing policy rule
    (is_exception = true). Returns deal_id, customer_id, exception reasons
    and the policy fields that were violated.
    Filter by customer_id or pass empty string for all non-compliant deals."""
    require_role("admin")
    audit_log("non_compliant_deals", {"customer_id": customer_id})
    logger.info("[tool] non_compliant_deals | customer_id=%r", customer_id)
    return _to_json(query_non_compliant_deals(customer_id))


@mcp.tool()
def compare_fab_vs_competitor(customer_id: str = "", deal_id: str = "") -> str:
    """Direct comparison of FAB recommended/approved price vs competitor offer,
    with competitor_gap_bps and a suggested pricing action
    (MATCH / COUNTER / ESCALATE / REJECT).
    Filter by customer_id and/or deal_id or pass empty strings for all."""
    require_role("admin", "agent")
    audit_log("compare_fab_vs_competitor", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] compare_fab_vs_competitor | %r %r", customer_id, deal_id)
    return _to_json(query_compare_fab_vs_competitor(customer_id, deal_id))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        host      = os.getenv("MCP_HOST", "127.0.0.1")
        port      = int(os.getenv("MCP_PORT", "9200"))
        server_id = os.getenv("MCP_SERVER_ID", "fab-pricing-server")
        auth_msg  = f"JWTVerifier (aud={server_id})" if MCP_AUTH_ENABLED else "disabled"
        logger.info(
            "Starting FAB Pricing Engine MCP Server on %s:%s  auth=%s",
            host, port, auth_msg,
        )
        # claims_middleware() extracts JWT claims into ContextVar for require_role()
        app = mcp.http_app(middleware=claims_middleware())
        uvicorn.run(app, host=host, port=port, log_level="warning")
    else:
        logger.info("Starting FAB Pricing Engine MCP Server (stdio)")
        mcp.run()
