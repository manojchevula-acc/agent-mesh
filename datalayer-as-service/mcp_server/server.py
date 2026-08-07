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
  Analytical (new):
    cross_sell_opportunity, credit_rating_events, similar_customer_pricing

Run (stdio — default, for local/Claude Desktop clients):
    python -m mcp_server.server

Run as a network service (streamable HTTP):
    MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.server
"""

import logging
import json
import os
import pathlib
from typing import Any

import httpx

# Load .env files BEFORE importing auth — auth.py reads MCP_API_KEY and
# MCP_JWT_SECRET at module level, so they must be in os.environ first.
try:
    from dotenv import load_dotenv as _load_dotenv
    _here = pathlib.Path(__file__).resolve().parent
    _load_dotenv(_here.parent.parent / ".env")          # project root .env (MCP auth keys)
    _load_dotenv(_here.parent / ".env", override=True)  # datalayer-as-service/.env (MySQL creds)
except ImportError:
    pass

from fastmcp import FastMCP
from mcp_server.auth import require_role, audit_log

try:
    from mcp_server.tool_registry import get_tool_credentials, seed_dev_credentials as _seed_dev_credentials
    _TOOL_REGISTRY_AVAILABLE = True
except ImportError:
    get_tool_credentials = None  # type: ignore[assignment]
    _seed_dev_credentials = None
    _TOOL_REGISTRY_AVAILABLE = False

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
    query_cross_sell_opportunity,
    query_credit_rating_events,
    query_similar_customer_pricing,
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
# Auto-seed tool credentials on first startup
# ---------------------------------------------------------------------------
if _TOOL_REGISTRY_AVAILABLE and _seed_dev_credentials is not None:
    _db_path = pathlib.Path(__file__).resolve().parent.parent / "tool_credentials.db"
    _auto_seed = os.getenv("AUTO_SEED_CREDENTIALS", "true").lower() not in ("false", "0", "no")
    if _auto_seed and not _db_path.exists():
        try:
            _seed_dev_credentials()
            logger.info("tool_registry: seeded dev credentials in %s", _db_path)
        except Exception as _e:
            logger.warning("tool_registry: auto-seed failed: %s", _e)

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
    require_role("admin", "agent")
    audit_log("customer_360", {"customer_id": customer_id})
    logger.info("[tool] customer_360 | customer_id=%r", customer_id)
    return _to_json(query_customer_360(customer_id))


@mcp.tool()
def pricing_recommendation(customer_id: str = "") -> str:
    """Pricing recommendation per deal: rebuilt system recommended price, approved
    price, expected margin, policy benchmarks and compliance flags."""
    require_role("admin", "agent")
    audit_log("pricing_recommendation", {"customer_id": customer_id})
    logger.info("[tool] pricing_recommendation | customer_id=%r", customer_id)
    return _to_json(query_pricing_recommendation(customer_id))


@mcp.tool()
def profitability_summary(customer_id: str = "") -> str:
    """Profitability roll-up by product type: revenue, funding/operating/capital
    cost, net profit and profitability tier."""
    require_role("admin", "agent")
    audit_log("profitability_summary", {"customer_id": customer_id})
    logger.info("[tool] profitability_summary | customer_id=%r", customer_id)
    return _to_json(query_profitability_summary(customer_id))


@mcp.tool()
def margin_analysis(customer_id: str = "") -> str:
    """Deal-level margin decomposition: net margin, spread over benchmark, variance
    vs recommended, and margin-below-minimum flag."""
    require_role("admin", "agent")
    audit_log("margin_analysis", {"customer_id": customer_id})
    logger.info("[tool] margin_analysis | customer_id=%r", customer_id)
    return _to_json(query_margin_analysis(customer_id))


@mcp.tool()
def rwa_impact(customer_id: str = "") -> str:
    """RWA impact for won deals: exposure, risk weight, RWA, capital required and
    return on RWA."""
    require_role("admin", "agent")
    audit_log("rwa_impact", {"customer_id": customer_id})
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
    require_role("admin", "agent")
    audit_log("new_customer_pricing", {"customer_id": customer_id, "segment": segment,
                                       "product_id": product_id, "risk_rating": risk_rating})
    logger.info("[tool] new_customer_pricing | %r %r %r %r", customer_id, segment, product_id, risk_rating)
    return _to_json(query_new_customer_pricing(customer_id, segment, product_id, risk_rating))


@mcp.tool()
def competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> str:
    """Compare FAB offer vs competitor offer and return competitor_gap_bps plus a
    MATCH / COUNTER / ESCALATE / REJECT suggested action with reasoning."""
    require_role("admin", "agent")
    audit_log("competitor_price_analysis", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] competitor_price_analysis | %r %r", customer_id, deal_id)
    return _to_json(query_competitor_price_analysis(customer_id, deal_id))


@mcp.tool()
def pricing_trace(customer_id: str = "", deal_id: str = "") -> str:
    """Step-by-step price build-up: treasury, target margin, risk premium, ops cost,
    relationship discount, final recommended price and an explanation sentence."""
    require_role("admin", "agent")
    audit_log("pricing_trace", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] pricing_trace | %r %r", customer_id, deal_id)
    return _to_json(query_pricing_trace(customer_id, deal_id))


@mcp.tool()
def segment_pricing_benchmark(segment: str = "", product_id: str = "") -> str:
    """Segment pricing guideline: target margin, base floor, new-customer buffer,
    max relationship discount and min profitability margin."""
    require_role("admin", "agent")
    audit_log("segment_pricing_benchmark", {"segment": segment, "product_id": product_id})
    logger.info("[tool] segment_pricing_benchmark | %r %r", segment, product_id)
    return _to_json(query_segment_pricing_benchmark(segment, product_id))


@mcp.tool()
def operations_cost_impact(product_id: str = "", customer_segment: str = "") -> str:
    """Operational cost impact on pricing: ops cost margin and cost breakdown per
    product x customer segment."""
    require_role("admin", "agent")
    audit_log("operations_cost_impact", {"product_id": product_id, "customer_segment": customer_segment})
    logger.info("[tool] operations_cost_impact | %r %r", product_id, customer_segment)
    return _to_json(query_operations_cost_impact(product_id, customer_segment))


@mcp.tool()
def relationship_discount(customer_id: str) -> str:
    """Relationship discount eligibility and whether approval is required for a
    customer."""
    require_role("admin", "agent")
    audit_log("relationship_discount", {"customer_id": customer_id})
    logger.info("[tool] relationship_discount | %r", customer_id)
    return _to_json(query_relationship_discount(customer_id))


@mcp.tool()
def win_loss_insights(customer_id: str = "", product_id: str = "", segment: str = "") -> str:
    """Win/loss insights: win rate, price gap vs recommended and competitor
    pressure, filterable by customer, product or segment."""
    require_role("admin", "agent")
    audit_log("win_loss_insights", {"customer_id": customer_id, "product_id": product_id,
                                    "segment": segment})
    logger.info("[tool] win_loss_insights | %r %r %r", customer_id, product_id, segment)
    return _to_json(query_win_loss_insights(customer_id, product_id, segment))


@mcp.tool()
def policy_exception(customer_id: str = "", deal_id: str = "") -> str:
    """Policy exception approvals: deals requiring senior approval. Admin role required."""
    require_role("admin")
    audit_log("policy_exception", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] policy_exception | %r %r", customer_id, deal_id)
    return _to_json(query_policy_exception(customer_id, deal_id))


@mcp.tool()
def non_compliant_deals(customer_id: str = "") -> str:
    """Non-compliant deals that breach at least one policy rule. Admin role required."""
    require_role("admin")
    audit_log("non_compliant_deals", {"customer_id": customer_id})
    logger.info("[tool] non_compliant_deals | %r", customer_id)
    return _to_json(query_non_compliant_deals(customer_id))


@mcp.tool()
def compare_fab_vs_competitor(customer_id: str = "", deal_id: str = "") -> str:
    """Direct FAB recommended/approved price vs competitor offer comparison with the
    suggested pricing action."""
    require_role("admin", "agent")
    audit_log("compare_fab_vs_competitor", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] compare_fab_vs_competitor | %r %r", customer_id, deal_id)
    return _to_json(query_compare_fab_vs_competitor(customer_id, deal_id))


@mcp.tool()
def cross_sell_opportunity(customer_segment: str = "", industry: str = "") -> str:
    """Active cross-sell product recommendations: customer_segment + industry + trigger_condition
    → recommended_product_name, expected_incremental_revenue_aed, cross_sell_priority, rationale.
    Filter by customer_segment (e.g. 'SME', 'Corporate') and/or industry (e.g. 'Healthcare')."""
    require_role("admin", "agent")
    audit_log("cross_sell_opportunity", {"customer_segment": customer_segment, "industry": industry})
    logger.info("[tool] cross_sell_opportunity | segment=%r industry=%r", customer_segment, industry)
    return _to_json(query_cross_sell_opportunity(customer_segment, industry))


@mcp.tool()
def credit_rating_events(customer_id: str = "") -> str:
    """Credit rating migration events per customer: old/new rating, direction, reason,
    recommended pricing action, risk premium, review flags. Agent or admin role required."""
    require_role("admin", "agent")
    audit_log("credit_rating_events", {"customer_id": customer_id})
    logger.info("[tool] credit_rating_events | customer_id=%r", customer_id)
    return _to_json(query_credit_rating_events(customer_id))


@mcp.tool()
def similar_customer_pricing(new_customer_id: str = "") -> str:
    """Reference similarity scores and suggested pricing for new/prospect customers:
    reference_customer_name, similarity_score, reference_final_price_pct,
    adjustment_for_new_customer_pct, suggested_price_pct, adjustment_rationale.
    Filter by new_customer_id (e.g. 'CUST021'). Pass '' for all mappings."""
    require_role("admin", "agent")
    audit_log("similar_customer_pricing", {"new_customer_id": new_customer_id})
    logger.info("[tool] similar_customer_pricing | new_customer_id=%r", new_customer_id)
    return _to_json(query_similar_customer_pricing(new_customer_id))


# ---------------------------------------------------------------------------
# External tools — independent per-service authentication
# Each tool fetches its own credentials from tool_registry.py (SQLite).
# The agent JWT (MCP_API_KEY) is NEVER forwarded to these external services.
# ---------------------------------------------------------------------------

@mcp.tool()
def credit_bureau_check(customer_id: str, loan_amount: float = 0.0,
                        product_type: str = "") -> str:
    """External credit bureau check for a customer.

    Calls an independent external credit bureau service with its own Bearer JWT
    (stored in tool_registry.py under tool_name='credit_bureau_check').
    Returns: credit_score, risk_band, default_probability_pct, recommendation.
    Note: CUST999 and WATCH001/WATCH002 are flagged in the mock watchlist.
    """
    require_role("admin", "agent")
    audit_log("credit_bureau_check",
              {"customer_id": customer_id, "product_type": product_type},
              service="credit-bureau-external")
    logger.info("[tool] credit_bureau_check | customer_id=%r", customer_id)
    if not _TOOL_REGISTRY_AVAILABLE:
        return "Tool registry unavailable: install sqlite3 / tool_registry.py dependencies."
    try:
        creds = get_tool_credentials("credit_bureau_check")
    except (KeyError, RuntimeError) as e:
        return f"Tool registry error: {e}"
    try:
        resp = httpx.post(
            creds.service_url,
            json={"customer_id": customer_id, "loan_amount": loan_amount,
                  "product_type": product_type},
            headers=creds.auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return (
                f"External service auth failed (401) — credential may be wrong or expired.\n"
                f"Rotate: python datalayer-as-service/mcp_server/tool_registry.py "
                f"--rotate credit_bureau_check <new_token>"
            )
        return f"Credit bureau error {e.response.status_code}: {e.response.text}"
    except httpx.ConnectError:
        return (
            "Credit bureau service unreachable. Start it with:\n"
            "  python datalayer-as-service/mcp_server/external_service.py"
        )


@mcp.tool()
def fx_rate_lookup(currency_pair: str) -> str:
    """Look up FX spot rate for a currency pair via external FX provider.

    Calls an independent FX rate service using an X-API-Key header
    (stored in tool_registry.py under tool_name='fx_rate_lookup').
    Uses a DIFFERENT auth pattern (custom header) from the credit bureau (Bearer JWT).
    Supported pairs: USDAED, EURAED, GBPAED, USDINR, USDEUR, EURUSD,
                     USDGBP, USDCNY, USDSGD, USDCHF, AEDUSF, AEDINR.
    """
    require_role("admin", "agent")
    audit_log("fx_rate_lookup", {"currency_pair": currency_pair}, service="fx-rate-external")
    logger.info("[tool] fx_rate_lookup | pair=%r", currency_pair)
    if not _TOOL_REGISTRY_AVAILABLE:
        return "Tool registry unavailable: install sqlite3 / tool_registry.py dependencies."
    try:
        creds = get_tool_credentials("fx_rate_lookup")
    except (KeyError, RuntimeError) as e:
        return f"Tool registry error: {e}"
    try:
        resp = httpx.get(
            f"{creds.service_url}/{currency_pair}",
            headers=creds.auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return (
                f"FX service auth failed (401) — X-API-Key may be wrong or expired.\n"
                f"Rotate: python datalayer-as-service/mcp_server/tool_registry.py "
                f"--rotate fx_rate_lookup <new_key>"
            )
        return f"FX rate error {e.response.status_code}: {e.response.text}"
    except httpx.ConnectError:
        return (
            "FX rate service unreachable. Start it with:\n"
            "  python datalayer-as-service/mcp_server/external_service.py"
        )


@mcp.tool()
def sanctions_screen(customer_id: str, customer_name: str = "",
                     country: str = "") -> str:
    """Compliance sanctions / AML screening for a customer. Admin role required.

    Calls an independent sanctions screening service with its own Bearer JWT
    (stored in tool_registry.py under tool_name='sanctions_screen').
    Uses a different token from credit bureau — per-service credential isolation.
    Returns: sanctions_hit, risk_level (CRITICAL/CLEAR), action_required.
    Note: CUST999, WATCH001, WATCH002 are on the mock sanctions watchlist.
    """
    require_role("admin")
    audit_log("sanctions_screen",
              {"customer_id": customer_id, "country": country},
              service="sanctions-external")
    logger.info("[tool] sanctions_screen | customer_id=%r", customer_id)
    if not _TOOL_REGISTRY_AVAILABLE:
        return "Tool registry unavailable: install sqlite3 / tool_registry.py dependencies."
    try:
        creds = get_tool_credentials("sanctions_screen")
    except (KeyError, RuntimeError) as e:
        return f"Tool registry error: {e}"
    try:
        resp = httpx.post(
            creds.service_url,
            json={"customer_id": customer_id, "customer_name": customer_name,
                  "country": country},
            headers=creds.auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return (
                f"Sanctions service auth failed (401) — token may be wrong or expired.\n"
                f"Rotate: python datalayer-as-service/mcp_server/tool_registry.py "
                f"--rotate sanctions_screen <new_token>"
            )
        return f"Sanctions service error {e.response.status_code}: {e.response.text}"
    except httpx.ConnectError:
        return (
            "Sanctions service unreachable. Start it with:\n"
            "  python datalayer-as-service/mcp_server/external_service.py"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        import uvicorn
        from mcp_server.auth import BearerAuthMiddleware, MCP_AUTH_ENABLED, _MCP_DEV_MODE_ACTIVE
        host = os.getenv("MCP_HOST", "127.0.0.1")
        port = int(os.getenv("MCP_PORT", "9100"))
        auth_info = "open-dev" if _MCP_DEV_MODE_ACTIVE else ("enabled" if MCP_AUTH_ENABLED else "disabled")
        logger.info(
            "Starting FAB Pricing MCP Server (streamable HTTP) on %s:%s | auth=%s",
            host, port, auth_info,
        )
        _app = mcp.http_app()
        if MCP_AUTH_ENABLED:
            _app.add_middleware(BearerAuthMiddleware)
        uvicorn.run(_app, host=host, port=port, log_level="warning")
    else:
        logger.info("Starting FAB Pricing Recommendation MCP Server (stdio) ...")
        mcp.run()
