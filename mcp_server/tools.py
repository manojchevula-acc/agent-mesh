"""
mcp_server/tools.py
--------------------
Pure query functions for each semantic view.

Each function:
  - Accepts a customer_id string (or None / empty to return all rows).
  - Runs a parameterised SQL query against fab_semantic.
  - Returns a list of dicts (JSON-serialisable).
  - Returns a single-item list with a 'message' key when no data is found.
  - Raises no unhandled exceptions — errors are caught and returned as a
    list with an 'error' key so MCP tools always return something.
"""

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

from mcp_server.db import get_engine

logger = logging.getLogger(__name__)

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
            return [{"message": "No records found for the given customer_id."}]

        # Convert NaN / NaT to None so the result is JSON-safe.
        # astype(object) is required for pandas >= 3.0, where string columns use
        # the new StringDtype and a plain .where(..., None) leaves NaN in place.
        return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")

    except Exception as exc:
        logger.error("Query error: %s | SQL: %s | params: %s", exc, sql, params)
        return [{"error": str(exc)}]


def _build_params(customer_id: str | None) -> tuple[str, dict]:
    """Return (WHERE clause, params dict) based on whether customer_id is given."""
    if customer_id and customer_id.strip():
        return "WHERE customer_id = :customer_id", {"customer_id": customer_id.strip()}
    return "", {}


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def query_customer_360(customer_id: str) -> list[dict[str, Any]]:
    """
    Returns the customer 360 profile for the given customer_id.
    Includes customer master details plus aggregated deal KPIs.
    """
    where, params = _build_params(customer_id)
    sql = f"SELECT * FROM fab_semantic.customer_360 {where} LIMIT 100"
    logger.info("customer_360 query | customer_id=%r", customer_id)
    return _run_query(sql, params)


def query_pricing_recommendation(customer_id: str) -> list[dict[str, Any]]:
    """
    Returns pricing recommendation records for the given customer_id.
    Includes deal details, policy benchmarks, and compliance flags.
    """
    where, params = _build_params(customer_id)
    sql = f"SELECT * FROM fab_semantic.pricing_recommendation_view {where} LIMIT 100"
    logger.info("pricing_recommendation_view query | customer_id=%r", customer_id)
    return _run_query(sql, params)


def query_profitability_summary(customer_id: str) -> list[dict[str, Any]]:
    """
    Returns the profitability summary for the given customer_id.
    Aggregated by product type with tier classification.
    """
    where, params = _build_params(customer_id)
    sql = f"SELECT * FROM fab_semantic.profitability_summary {where} LIMIT 100"
    logger.info("profitability_summary query | customer_id=%r", customer_id)
    return _run_query(sql, params)


def query_margin_analysis(customer_id: str) -> list[dict[str, Any]]:
    """
    Returns deal-level margin analysis for the given customer_id.
    Includes net margin, spread over benchmark, and variance vs recommended price.
    """
    where, params = _build_params(customer_id)
    sql = f"SELECT * FROM fab_semantic.margin_analysis {where} LIMIT 100"
    logger.info("margin_analysis query | customer_id=%r", customer_id)
    return _run_query(sql, params)


def query_rwa_impact(customer_id: str) -> list[dict[str, Any]]:
    """
    Returns RWA impact records for the given customer_id.
    Includes RWA-weighted exposure, capital required, and return on RWA.
    """
    where, params = _build_params(customer_id)
    sql = f"SELECT * FROM fab_semantic.rwa_impact_view {where} LIMIT 100"
    logger.info("rwa_impact_view query | customer_id=%r", customer_id)
    return _run_query(sql, params)
