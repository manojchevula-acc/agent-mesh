"""Section 4.1 — Customer tools (customer_master). Parameterised, one fixed shape each."""

from sql_agent.tools.decorator import tool

from sql_agent.db import db
from sql_agent.formatting import format_response


@tool
def get_customer(customer_id: str) -> dict:
    """Fetch the full profile of ONE customer by their customer_id.
    Use when the question names a specific customer by ID."""
    sql = """
        SELECT customer_id, customer_name, customer_segment, industry, region,
               preferred_currency, risk_category, internal_rating,
               relationship_tenure_years, relationship_status,
               annual_revenue_aed, debt_to_equity_ratio, credit_score,
               existing_exposure_aed
        FROM customer_master
        WHERE customer_id = :customer_id
    """
    rows = db.execute(sql, {"customer_id": customer_id})
    return format_response(rows, tier="parameterised", tool="get_customer")


@tool
def get_customer_by_name(customer_name: str) -> dict:
    """Fetch a customer's profile by name. Use when the question names a
    customer by company name rather than ID (the common RM phrasing)."""
    sql = """
        SELECT customer_id, customer_name, customer_segment, risk_category,
               credit_score, annual_revenue_aed
        FROM customer_master
        WHERE customer_name = :customer_name
    """
    rows = db.execute(sql, {"customer_name": customer_name})
    return format_response(rows, tier="parameterised", tool="get_customer_by_name")


@tool
def get_customer_exposure(customer_id: str) -> dict:
    """Fetch ONE customer's current exposure, revenue, and gearing — the inputs
    a Risk Manager needs for a single-name exposure check."""
    sql = """
        SELECT customer_id, customer_name, existing_exposure_aed,
               annual_revenue_aed, debt_to_equity_ratio, risk_category
        FROM customer_master WHERE customer_id = :customer_id
    """
    rows = db.execute(sql, {"customer_id": customer_id})
    return format_response(rows, tier="parameterised", tool="get_customer_exposure")
