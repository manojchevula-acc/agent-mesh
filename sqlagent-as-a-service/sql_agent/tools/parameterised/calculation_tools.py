"""Section 4.6 — Calculation tools (parameterised).

These fetch their inputs via fixed queries (the same shapes the get_* tools use),
then call the pure formula functions in calculations/. Deterministic and cacheable.
The arithmetic is NEVER done by the LLM (Design Document §5 hard rule).
"""

from langchain_core.tools import tool

from sql_agent.calculations import pricing, risk, eligibility
from sql_agent.db import db
from sql_agent.formatting import format_calc, format_error
from sql_agent.semantic_layer.loader import canonicalize_enum


def _one(rows) -> dict | None:
    data = rows.rows if hasattr(rows, "rows") else rows
    return data[0] if data else None


def _fetch_customer(customer_id: str) -> dict | None:
    sql = """SELECT customer_segment, risk_category, relationship_discount_pct,
                    existing_exposure_aed
             FROM customer_master WHERE customer_id = :customer_id"""
    return _one(db.execute(sql, {"customer_id": customer_id}))


def _fetch_product(product_id: str) -> dict | None:
    sql = """SELECT product_type, currency, standard_margin_pct,
                    max_discount_allowed_pct, min_ticket_size, max_ticket_size
             FROM product_master WHERE product_id = :product_id"""
    return _one(db.execute(sql, {"product_id": product_id}))


def _fetch_funding(currency: str, tenor: str) -> dict | None:
    # tenor arrives from the LLM as user wording ("60-month"); snap it to "60M".
    tenor = canonicalize_enum("treasury_rate_sheet", "tenor", tenor)
    sql = """SELECT funding_cost_pct FROM treasury_rate_sheet
             WHERE currency = :currency AND tenor = :tenor
             ORDER BY effective_date DESC"""
    return _one(db.execute(sql, {"currency": currency, "tenor": tenor}))


def _fetch_policy(segment: str, product_type: str, risk_category: str) -> dict | None:
    sql = """SELECT min_margin_pct, risk_premium_pct,
                    approval_required_if_discount_above_pct, rwa_risk_weight_pct
             FROM pricing_policy
             WHERE customer_segment = :segment AND product_type = :product_type
               AND risk_category = :risk_category AND status = 'Active'"""
    return _one(db.execute(sql, {"segment": segment, "product_type": product_type,
                                 "risk_category": risk_category}))


@tool
def compute_recommended_price(customer_id: str, product_id: str, tenor: str) -> dict:
    """Compute the all-in recommended price for a customer+product+tenor using
    the canonical pricing formula. Deterministic; never estimated by the model."""
    cust = _fetch_customer(customer_id)
    prod = _fetch_product(product_id)
    if not cust or not prod:
        return format_error("ValidationError", "Customer or product not found", retryable=False)
    fund = _fetch_funding(prod["currency"], tenor)
    pol = _fetch_policy(cust["customer_segment"], prod["product_type"], cust["risk_category"])
    if not fund or not pol:
        return format_error("ValidationError", "Funding rate or pricing policy not found", retryable=False)

    inp = pricing.PriceInputs(
        funding_cost_pct=fund["funding_cost_pct"],
        standard_margin_pct=prod["standard_margin_pct"],
        risk_premium_pct=pol["risk_premium_pct"],
        relationship_discount_pct=cust["relationship_discount_pct"],
    )
    price = pricing.recommended_price(inp)
    # Derive the policy floor + compliance from the SAME policy row this tool already
    # resolved from the customer's ACTUAL segment/risk. Returning it here means callers
    # never have to make a separate get_pricing_policy call (where the model has been
    # observed to guess the wrong segment/risk and quote the wrong floor).
    floor = float(pol["min_margin_pct"])
    net_margin = pricing.net_interest_margin(price, fund["funding_cost_pct"])
    headroom = pricing.margin_headroom(net_margin, floor)
    below_floor = headroom < 0
    explain = {
        "formula": "recommended_price = funding_cost + standard_margin "
                   "+ risk_premium − relationship_discount",
        "expression": (
            f"{inp.funding_cost_pct} + {inp.standard_margin_pct} + {inp.risk_premium_pct} "
            f"− {inp.relationship_discount_pct} = {price}"
        ),
        "unit": "%",
        "terms": [
            {"label": "Funding cost", "value": inp.funding_cost_pct},
            {"label": "Standard margin", "value": inp.standard_margin_pct},
            {"label": "Risk premium", "value": inp.risk_premium_pct},
            {"label": "Relationship discount", "value": inp.relationship_discount_pct},
        ],
        "result": {"label": "Recommended price", "value": price},
        "policy_floor": {
            "policy_min_margin_pct": floor,
            "net_margin_over_funding_pct": net_margin,
            "margin_headroom_pct": headroom,
            "below_policy_floor": below_floor,
            "note": ("Net margin (price − funding) is below the policy minimum for this "
                     "customer's segment/risk." if below_floor else
                     "Net margin (price − funding) meets the policy minimum."),
        },
    }
    return format_calc({
        "recommended_price_pct": price,
        "policy_min_margin_pct": floor,
        "net_margin_over_funding_pct": net_margin,
        "margin_headroom_pct": headroom,
        "below_policy_floor": below_floor,
    }, tier="parameterised", tool="compute_recommended_price", explain=explain)


@tool
def compute_margin_headroom(customer_id: str, product_id: str, tenor: str,
                            approved_price_pct: float) -> dict:
    """Compute net interest margin and headroom to the policy floor for an
    approved price. Negative headroom means the deal breaches policy."""
    cust = _fetch_customer(customer_id)
    prod = _fetch_product(product_id)
    if not cust or not prod:
        return format_error("ValidationError", "Customer or product not found", retryable=False)
    fund = _fetch_funding(prod["currency"], tenor)
    pol = _fetch_policy(cust["customer_segment"], prod["product_type"], cust["risk_category"])
    if not fund or not pol:
        return format_error("ValidationError", "Funding rate or pricing policy not found", retryable=False)

    nim = pricing.net_interest_margin(approved_price_pct, fund["funding_cost_pct"])
    headroom = pricing.margin_headroom(nim, pol["min_margin_pct"])
    explain = {
        "formula": "net_interest_margin = approved_price − funding_cost;  "
                   "margin_headroom = net_interest_margin − policy_min_margin",
        "expression": (
            f"NIM = {approved_price_pct} − {fund['funding_cost_pct']} = {nim};  "
            f"headroom = {nim} − {pol['min_margin_pct']} = {headroom}"
            + (" (below floor)" if headroom < 0 else "")
        ),
        "unit": "%",
        "terms": [
            {"label": "Approved price", "value": approved_price_pct},
            {"label": "Funding cost", "value": fund["funding_cost_pct"]},
            {"label": "Policy min margin", "value": pol["min_margin_pct"]},
        ],
        "result": {"label": "Margin headroom", "value": headroom},
    }
    return format_calc({
        "net_interest_margin_pct": nim,
        "margin_headroom_pct": headroom,
        "below_floor": headroom < 0,
    }, tier="parameterised", tool="compute_margin_headroom", explain=explain)


@tool
def compute_approval_required(customer_id: str, product_id: str,
                              applied_discount_pct: float) -> dict:
    """Compute the approval-required flag by comparing the applied relationship
    discount to the policy threshold for this customer+product."""
    cust = _fetch_customer(customer_id)
    prod = _fetch_product(product_id)
    if not cust or not prod:
        return format_error("ValidationError", "Customer or product not found", retryable=False)
    pol = _fetch_policy(cust["customer_segment"], prod["product_type"], cust["risk_category"])
    if not pol:
        return format_error("ValidationError", "Pricing policy not found", retryable=False)

    threshold = pol["approval_required_if_discount_above_pct"]
    required = pricing.approval_required(applied_discount_pct, threshold)
    explain = {
        "formula": "approval_required = applied_discount_pct > approval_threshold_pct",
        "expression": f"{applied_discount_pct} > {threshold} → {'Yes' if required else 'No'}",
        "unit": "%",
        "terms": [
            {"label": "Applied discount", "value": applied_discount_pct},
            {"label": "Approval threshold", "value": threshold},
        ],
        "result": {"label": "Approval required", "value": required},
    }
    return format_calc({
        "approval_required": required,
        "threshold_pct": threshold,
    }, tier="parameterised", tool="compute_approval_required", explain=explain)


@tool
def compute_rwa(customer_id: str, product_id: str, risk_category: str) -> dict:
    """Compute risk-weighted assets and an indicative capital charge from the
    customer's exposure and the policy risk weight."""
    cust = _fetch_customer(customer_id)
    prod = _fetch_product(product_id)
    if not cust or not prod:
        return format_error("ValidationError", "Customer or product not found", retryable=False)
    pol = _fetch_policy(cust["customer_segment"], prod["product_type"], risk_category)
    if not pol:
        return format_error("ValidationError", "Pricing policy not found", retryable=False)

    rwa_aed = risk.rwa(cust["existing_exposure_aed"], pol["rwa_risk_weight_pct"])
    cap = risk.capital_charge(rwa_aed)
    explain = {
        "formula": "rwa = exposure × (risk_weight% ÷ 100);  "
                   "capital_charge = rwa × capital_ratio (8%)",
        "expression": (
            f"RWA = {cust['existing_exposure_aed']} × {pol['rwa_risk_weight_pct']}% = {rwa_aed};  "
            f"capital charge = {rwa_aed} × 8% = {cap}"
        ),
        "unit": "AED",
        "terms": [
            {"label": "Exposure (AED)", "value": cust["existing_exposure_aed"]},
            {"label": "Risk weight %", "value": pol["rwa_risk_weight_pct"]},
            {"label": "Capital ratio %", "value": 8},
        ],
        "result": {"label": "RWA (AED)", "value": rwa_aed},
    }
    return format_calc({
        "rwa_aed": rwa_aed,
        "capital_charge": cap,
    }, tier="parameterised", tool="compute_rwa", explain=explain)


@tool
def compute_ticket_eligibility(product_id: str, requested_amount: float) -> dict:
    """Check whether a requested amount falls within the product's ticket-size band."""
    prod = _fetch_product(product_id)
    if not prod:
        return format_error("ValidationError", "Product not found", retryable=False)
    eligible = eligibility.ticket_eligible(
        requested_amount, prod["min_ticket_size"], prod["max_ticket_size"])
    explain = {
        "formula": "ticket_eligible = min_ticket_size ≤ requested_amount ≤ max_ticket_size",
        "expression": (
            f"{prod['min_ticket_size']} ≤ {requested_amount} ≤ {prod['max_ticket_size']} "
            f"→ {'eligible' if eligible else 'not eligible'}"
        ),
        "unit": "AED",
        "terms": [
            {"label": "Requested amount", "value": requested_amount},
            {"label": "Min ticket size", "value": prod["min_ticket_size"]},
            {"label": "Max ticket size", "value": prod["max_ticket_size"]},
        ],
        "result": {"label": "Eligible", "value": eligible},
    }
    return format_calc({
        "ticket_eligible": eligible,
        "min_ticket_size": prod["min_ticket_size"],
        "max_ticket_size": prod["max_ticket_size"],
    }, tier="parameterised", tool="compute_ticket_eligibility", explain=explain)
