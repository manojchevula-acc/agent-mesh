"""
mcp_server/customer_server.py
-------------------------------
FAB Customer Intelligence MCP Server  (port 9100)

Exposes customer-centric fab_semantic views as MCP tools.
Use this server for questions about WHO a customer is — their profile,
profitability, relationship history, deal track record, credit events,
cross-sell opportunities, and peer-group benchmarks.

Tools (9):
    customer_360            — full customer profile + deal KPIs
    profitability_summary   — profitability roll-up by product type
    margin_analysis         — deal-level margin decomposition
    rwa_impact              — risk-weighted asset impact per won deal
    win_loss_insights       — win rate, price gap, competitor pressure
    credit_rating_events    — credit rating migration events
    cross_sell_opportunity  — active cross-sell recommendations
    relationship_discount   — discount eligibility and approval threshold
    similar_customer_pricing — peer-group pricing for new customers

Authentication:
    Tokens are RS256 JWTs issued by the hub, audience = MCP_SERVER_ID.
    FastMCP JWTVerifier validates tokens; BearerClaimsMiddleware extracts
    claims into a ContextVar for per-tool RBAC via require_role().

Run as a network service (streamable HTTP):
    MCP_SERVER_ID=fab-customer-server MCP_TRANSPORT=http \\
        MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.customer_server
"""

import logging
import os
import pathlib

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
    _to_json,
    query_customer_360,
    query_profitability_summary,
    query_margin_analysis,
    query_rwa_impact,
    query_win_loss_insights,
    query_credit_rating_events,
    query_cross_sell_opportunity,
    query_relationship_discount,
    query_similar_customer_pricing,
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
    name="FAB Customer Intelligence MCP Server",
    instructions=(
        "This server answers questions about FAB customers — who they are, "
        "how profitable they are, their deal track record (win rate, margins, RWA), "
        "credit rating history, cross-sell opportunities, relationship discount "
        "eligibility, and peer-group pricing benchmarks. "
        "Pass an empty string to a filter to retrieve all records (max 15 rows)."
    ),
    auth=build_jwt_verifier(),
)


# ---------------------------------------------------------------------------
# Customer profile tools
# ---------------------------------------------------------------------------

@mcp.tool()
def customer_360(customer_id: str = "") -> str:
    """360 customer profile: master data + aggregated deal KPIs (total deals,
    win rate, deal volume, avg margin, avg approved price, last deal date).
    Pass customer_id (e.g. 'CUST001') or empty string for all customers."""
    require_role("admin", "agent")
    audit_log("customer_360", {"customer_id": customer_id})
    logger.info("[tool] customer_360 | customer_id=%r", customer_id)
    return _to_json(query_customer_360(customer_id))


@mcp.tool()
def profitability_summary(customer_id: str = "") -> str:
    """Profitability roll-up by product type: revenue, funding cost, operating
    cost, capital cost, net profit and profitability tier (High/Medium/Low).
    Filter by customer_id or pass empty string for all."""
    require_role("admin", "agent")
    audit_log("profitability_summary", {"customer_id": customer_id})
    logger.info("[tool] profitability_summary | customer_id=%r", customer_id)
    return _to_json(query_profitability_summary(customer_id))


@mcp.tool()
def margin_analysis(customer_id: str = "") -> str:
    """Deal-level margin decomposition: net margin, spread over benchmark,
    variance vs recommended price, and margin-below-minimum flag.
    Filter by customer_id or pass empty string for all deals."""
    require_role("admin", "agent")
    audit_log("margin_analysis", {"customer_id": customer_id})
    logger.info("[tool] margin_analysis | customer_id=%r", customer_id)
    return _to_json(query_margin_analysis(customer_id))


@mcp.tool()
def rwa_impact(customer_id: str = "") -> str:
    """Risk-Weighted Asset impact for won deals: exposure, risk weight pct,
    computed RWA, capital required and return on RWA.
    Filter by customer_id or pass empty string for all won deals."""
    require_role("admin", "agent")
    audit_log("rwa_impact", {"customer_id": customer_id})
    logger.info("[tool] rwa_impact | customer_id=%r", customer_id)
    return _to_json(query_rwa_impact(customer_id))


@mcp.tool()
def win_loss_insights(customer_id: str = "", product_id: str = "",
                      segment: str = "") -> str:
    """Win/loss insights: win rate, price gap vs recommended, competitor
    pressure. Filter by customer_id, product_id or segment (e.g. 'SME',
    'Corporate'). Pass empty strings to retrieve all."""
    require_role("admin", "agent")
    audit_log("win_loss_insights", {"customer_id": customer_id, "product_id": product_id,
                                    "segment": segment})
    logger.info("[tool] win_loss_insights | %r %r %r", customer_id, product_id, segment)
    return _to_json(query_win_loss_insights(customer_id, product_id, segment))


@mcp.tool()
def credit_rating_events(customer_id: str = "") -> str:
    """Credit rating migration events: old_rating → new_rating, direction,
    reason_code, recommended_pricing_action, additional_risk_premium_pct,
    floating_rate_required_flag, credit_review_required_flag.
    Filter by customer_id (e.g. 'CUST002') or pass empty string for all."""
    require_role("admin", "agent")
    audit_log("credit_rating_events", {"customer_id": customer_id})
    logger.info("[tool] credit_rating_events | customer_id=%r", customer_id)
    return _to_json(query_credit_rating_events(customer_id))


@mcp.tool()
def cross_sell_opportunity(customer_segment: str = "", industry: str = "") -> str:
    """Active cross-sell recommendations: trigger condition → recommended
    product, expected incremental revenue (AED), priority, and rationale.
    Filter by customer_segment (e.g. 'SME', 'Corporate') and/or industry
    (e.g. 'Healthcare', 'Real Estate')."""
    require_role("admin", "agent")
    audit_log("cross_sell_opportunity", {"customer_segment": customer_segment, "industry": industry})
    logger.info("[tool] cross_sell_opportunity | segment=%r industry=%r",
                customer_segment, industry)
    return _to_json(query_cross_sell_opportunity(customer_segment, industry))


@mcp.tool()
def relationship_discount(customer_id: str) -> str:
    """Relationship discount eligibility for a customer: current discount pct,
    maximum allowed discount pct, tenure years, and whether manager approval
    is required to grant a further discount."""
    require_role("admin", "agent")
    audit_log("relationship_discount", {"customer_id": customer_id})
    logger.info("[tool] relationship_discount | customer_id=%r", customer_id)
    return _to_json(query_relationship_discount(customer_id))


@mcp.tool()
def similar_customer_pricing(new_customer_id: str = "") -> str:
    """Peer-group pricing for a new or prospect customer: reference customer,
    similarity score, reference final price pct, adjustment for new customer,
    suggested price pct and adjustment rationale.
    Filter by new_customer_id (e.g. 'CUST021') or pass empty string for all."""
    require_role("admin", "agent")
    audit_log("similar_customer_pricing", {"new_customer_id": new_customer_id})
    logger.info("[tool] similar_customer_pricing | new_customer_id=%r", new_customer_id)
    return _to_json(query_similar_customer_pricing(new_customer_id))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        host      = os.getenv("MCP_HOST", "127.0.0.1")
        port      = int(os.getenv("MCP_PORT", "9100"))
        server_id = os.getenv("MCP_SERVER_ID", "fab-customer-server")
        auth_msg  = f"JWTVerifier (aud={server_id})" if MCP_AUTH_ENABLED else "disabled"
        logger.info(
            "Starting FAB Customer Intelligence MCP Server on %s:%s  auth=%s",
            host, port, auth_msg,
        )
        # claims_middleware() extracts JWT claims into ContextVar for require_role()
        app = mcp.http_app(middleware=claims_middleware())
        uvicorn.run(app, host=host, port=port, log_level="warning")
    else:
        logger.info("Starting FAB Customer Intelligence MCP Server (stdio)")
        mcp.run()
