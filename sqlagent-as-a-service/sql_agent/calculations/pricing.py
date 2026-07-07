"""Deterministic pricing formulas. No SQL, no LLM. Inputs are plain floats —
callers (tools/) are responsible for fetching them via the parameterised tools.
Technical Spec §7.1.
"""

from dataclasses import dataclass


@dataclass
class PriceInputs:
    funding_cost_pct: float
    standard_margin_pct: float
    risk_premium_pct: float
    relationship_discount_pct: float


def recommended_price(inp: PriceInputs) -> float:
    """recommended_price_pct = funding + standard_margin + risk_premium - discount

    Verified against DEAL001: 6.00 + 2.10 + 0.45 - 0.22 = 8.33
    """
    return round(
        inp.funding_cost_pct + inp.standard_margin_pct
        + inp.risk_premium_pct - inp.relationship_discount_pct, 2)


def net_interest_margin(approved_price_pct: float, funding_cost_pct: float) -> float:
    """net_interest_margin_pct = approved_price - funding_cost

    approved_price arrives as a float (tool arg) while funding_cost is a DB Decimal;
    coerce both so we never mix float with Decimal (which raises TypeError)."""
    return round(float(approved_price_pct) - float(funding_cost_pct), 2)


def margin_headroom(net_margin_pct: float, min_margin_pct: float) -> float:
    """margin_headroom_pct = net_margin - policy floor.  < 0 => BELOW FLOOR."""
    return round(float(net_margin_pct) - float(min_margin_pct), 2)


def approval_required(relationship_discount_pct: float,
                      approval_threshold_pct: float) -> bool:
    """True when the applied discount exceeds the policy's approval threshold."""
    return relationship_discount_pct > approval_threshold_pct


def discount_utilisation(relationship_discount_pct: float,
                         max_discount_allowed_pct: float) -> float:
    """> 1.0 means the discount exceeds the product's allowed cap (invalid)."""
    if max_discount_allowed_pct == 0:
        raise ValueError("max_discount_allowed_pct must be non-zero")
    return round(relationship_discount_pct / max_discount_allowed_pct, 3)
