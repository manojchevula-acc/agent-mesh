"""Section 4.2 — Product tools (product_master). Parameterised, one fixed shape each."""

from sql_agent.tools.decorator import tool

from sql_agent.db import db
from sql_agent.db.dialect import csv_membership_clause
from sql_agent.formatting import format_response


@tool
def get_product(product_id: str) -> dict:
    """Fetch ONE product's pricing attributes and ticket-size rules by ID."""
    sql = """
        SELECT product_id, product_name, product_type, pricing_method, currency,
               eligible_tenors, standard_margin_pct, max_margin_pct,
               max_discount_allowed_pct, min_ticket_size, max_ticket_size,
               eligible_segments
        FROM product_master WHERE product_id = :product_id
    """
    rows = db.execute(sql, {"product_id": product_id})
    return format_response(rows, tier="parameterised", tool="get_product")


@tool
def get_product_by_name(product_name: str) -> dict:
    """Fetch a product's pricing attributes by its product_name (e.g. "Term Loan",
    "Working Capital Loan"). Use when the question names a product by name rather
    than by PRODxxx id — resolve it here before any id-based or compute_* tool.
    Matches the exact name first, then a unique partial match."""
    cols = """product_id, product_name, product_type, pricing_method, currency,
              eligible_tenors, standard_margin_pct, max_margin_pct,
              max_discount_allowed_pct, min_ticket_size, max_ticket_size,
              eligible_segments"""
    rows = db.execute(
        f"SELECT {cols} FROM product_master WHERE product_name = :name",
        {"name": product_name},
    )
    if not rows.rows:
        # Fall back to a partial match so "Term Loan" still resolves if the caller
        # passed a near-miss; LIKE escaping keeps it injection-safe via the param.
        rows = db.execute(
            f"SELECT {cols} FROM product_master WHERE product_name LIKE :name",
            {"name": f"%{product_name}%"},
        )
    return format_response(rows, tier="parameterised", tool="get_product_by_name")


@tool
def get_products_for_segment(segment: str) -> list[dict]:
    """List every product eligible for ONE customer segment (Corporate or SME).
    Fixed single-filter shape — always exactly one condition."""
    sql = f"""
        SELECT product_id, product_name, product_type, standard_margin_pct,
               min_ticket_size, max_ticket_size
        FROM product_master
        WHERE {csv_membership_clause("eligible_segments", "segment")}
    """
    rows = db.execute(sql, {"segment": segment})
    return format_response(rows, tier="parameterised", tool="get_products_for_segment")
