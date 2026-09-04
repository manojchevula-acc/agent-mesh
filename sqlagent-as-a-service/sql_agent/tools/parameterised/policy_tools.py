"""Section 4.4 — Pricing policy tools (pricing_policy).

The single most-called tool in the whole platform — every price, margin, and RWA
calculation depends on it.
"""

from sql_agent.tools.decorator import tool

from sql_agent.db import db
from sql_agent.formatting import format_response


@tool
def get_pricing_policy(segment: str, product_type: str, risk_category: str) -> dict:
    """Fetch the ACTIVE pricing policy row for one segment + product type +
    risk band. Always exactly three equality conditions on the composite key."""
    sql = """
        SELECT policy_id, min_margin_pct, risk_premium_pct,
               max_relationship_discount_pct,
               approval_required_if_discount_above_pct,
               min_expected_margin_pct, rwa_risk_weight_pct
        FROM pricing_policy
        WHERE customer_segment = :segment
          AND product_type = :product_type
          AND risk_category = :risk_category
          AND status = 'Active'
    """
    rows = db.execute(sql, {"segment": segment, "product_type": product_type,
                            "risk_category": risk_category})
    return format_response(rows, tier="parameterised", tool="get_pricing_policy")
