"""
mcp_server/tools.py
--------------------
Pure query functions for the fab_semantic views.

Design rules (all tools follow these):
  - Query ONLY fab_semantic views (never lower-layer source tables).
  - Use parameterised SQL (no string interpolation of user values).
  - Return a list of JSON-serialisable dicts.
  - When no rows match, return a single-item list with a 'message' key.
  - An empty filter returns capped data (max 100 rows).
  - All errors are caught and returned as a list with an 'error' key so an MCP
    tool always returns something.
"""

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

from mcp_server.db import get_engine

logger = logging.getLogger(__name__)

MAX_ROWS = 100


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_query(sql: str, params: dict) -> list[dict[str, Any]]:
    """Execute *sql* with *params* and return rows as a list of dicts."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)
        if df.empty:
            return [{"message": "No records found for the given filter(s)."}]
        # NaN / NaT -> None so the result is JSON-safe.
        return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    except Exception as exc:
        logger.error("Query error: %s | SQL: %s | params: %s", exc, sql, params)
        return [{"error": str(exc)}]


def _query_view(view: str, filters: dict[str, str | None], extra_where: str | None = None) -> list[dict[str, Any]]:
    """
    Query a fab_semantic view with optional equality filters.

    *filters* maps column_name -> value. Empty / None values are ignored.
    Column names are trusted (hard-coded by callers); values are parameterised.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for col, val in filters.items():
        if val is not None and str(val).strip():
            clauses.append(f"{col} = :{col}")
            params[col] = str(val).strip()
    if extra_where:
        clauses.append(extra_where)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM fab_semantic.{view} {where} LIMIT {MAX_ROWS}"
    logger.info("query %s | filters=%s", view, {k: v for k, v in params.items()})
    return _run_query(sql, params)


# ---------------------------------------------------------------------------
# Existing tools
# ---------------------------------------------------------------------------

def query_customer_360(customer_id: str = "") -> list[dict[str, Any]]:
    """360 customer profile + aggregated deal KPIs."""
    return _query_view("customer_360", {"customer_id": customer_id})


def query_pricing_recommendation(customer_id: str = "") -> list[dict[str, Any]]:
    """Deal pricing with rebuilt recommended price, policy benchmarks and compliance flags."""
    return _query_view("pricing_recommendation_view", {"customer_id": customer_id})


def query_profitability_summary(customer_id: str = "") -> list[dict[str, Any]]:
    """Profitability roll-up (revenue, funding/operating/capital cost, net profit, tier)."""
    return _query_view("profitability_summary", {"customer_id": customer_id})


def query_margin_analysis(customer_id: str = "") -> list[dict[str, Any]]:
    """Deal-level margin decomposition, spread over benchmark and margin-below-min flag."""
    return _query_view("margin_analysis", {"customer_id": customer_id})


def query_rwa_impact(customer_id: str = "") -> list[dict[str, Any]]:
    """RWA-weighted exposure, capital required and return on RWA per won deal."""
    return _query_view("rwa_impact_view", {"customer_id": customer_id})


# ---------------------------------------------------------------------------
# New tools
# ---------------------------------------------------------------------------

def query_new_customer_pricing(customer_id: str = "", segment: str = "",
                               product_id: str = "", risk_rating: str = "") -> list[dict[str, Any]]:
    """Recommended price for prospects with no relationship history."""
    return _query_view("new_customer_pricing_view", {
        "customer_id": customer_id,
        "customer_segment": segment,
        "product_id": product_id,
        "risk_category": risk_rating,
    })


def query_competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> list[dict[str, Any]]:
    """FAB vs competitor comparison with MATCH / COUNTER / ESCALATE / REJECT action."""
    return _query_view("competitor_price_analysis", {
        "customer_id": customer_id,
        "deal_id": deal_id,
    })


def query_pricing_trace(customer_id: str = "", deal_id: str = "") -> list[dict[str, Any]]:
    """Step-by-step recommended-price decomposition with explanation text."""
    return _query_view("pricing_trace_view", {
        "customer_id": customer_id,
        "deal_id": deal_id,
    })


def query_segment_pricing_benchmark(segment: str = "", product_id: str = "") -> list[dict[str, Any]]:
    """Segment pricing guideline (target margin, floor, buffers, discount caps)."""
    return _query_view("segment_pricing_benchmark", {
        "customer_segment": segment,
        "product_id": product_id,
    })


def query_operations_cost_impact(product_id: str = "", customer_segment: str = "") -> list[dict[str, Any]]:
    """Operational cost margin per product x customer segment."""
    return _query_view("operations_cost_impact", {
        "product_id": product_id,
        "customer_segment": customer_segment,
    })


def query_relationship_discount(customer_id: str = "") -> list[dict[str, Any]]:
    """Relationship discount eligibility and approval requirement."""
    return _query_view("relationship_discount_view", {"customer_id": customer_id})


def query_win_loss_insights(customer_id: str = "", product_id: str = "",
                            segment: str = "") -> list[dict[str, Any]]:
    """Won/lost aggregation with pricing gap and competitor pressure."""
    return _query_view("win_loss_insights", {
        "customer_id": customer_id,
        "product_id": product_id,
        "customer_segment": segment,
    })


def query_policy_exception(customer_id: str = "", deal_id: str = "") -> list[dict[str, Any]]:
    """Per-deal policy breaches with exception reasons."""
    return _query_view("policy_exception_view", {
        "customer_id": customer_id,
        "deal_id": deal_id,
    })


def query_non_compliant_deals(customer_id: str = "") -> list[dict[str, Any]]:
    """Only the deals that breach at least one policy rule."""
    return _query_view("policy_exception_view", {"customer_id": customer_id},
                       extra_where="is_exception = 1")


def query_compare_fab_vs_competitor(customer_id: str = "", deal_id: str = "") -> list[dict[str, Any]]:
    """Alias over competitor_price_analysis for direct FAB vs competitor comparison."""
    return query_competitor_price_analysis(customer_id=customer_id, deal_id=deal_id)
