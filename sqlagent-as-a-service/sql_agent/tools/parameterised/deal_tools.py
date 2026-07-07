"""Section 4.5 — Historical deal tools (historical_deals). Parameterised."""

from langchain_core.tools import tool

from sql_agent.db import db
from sql_agent.formatting import format_response


@tool
def get_deal(deal_id: str) -> dict:
    """Fetch ONE historical deal record by its deal_id."""
    sql = "SELECT * FROM historical_deals WHERE deal_id = :deal_id"
    rows = db.execute(sql, {"deal_id": deal_id})
    return format_response(rows, tier="parameterised", tool="get_deal")


@tool
def get_deals_for_customer(customer_id: str) -> list[dict]:
    """List every historical deal for ONE customer. Fixed single-key shape —
    used for relationship history lookups (RM, Credit Officer)."""
    sql = """
        SELECT deal_id, deal_date, product_id, product_type, requested_amount,
               recommended_price_pct, final_approved_price_pct,
               expected_margin_pct, deal_outcome
        FROM historical_deals WHERE customer_id = :customer_id
        ORDER BY deal_date DESC
    """
    rows = db.execute(sql, {"customer_id": customer_id})
    return format_response(rows, tier="parameterised", tool="get_deals_for_customer")
