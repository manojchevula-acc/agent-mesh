"""Section 5 — Semi-dynamic tools. One flexible-filter search tool per table.

Each tool's allowed filters are exactly the filterable: true columns declared in the
semantic layer (Section 3). The clause-builder may NEVER emit a condition outside this
set — the shape of the WHERE clause varies, but only over an enumerated menu.
"""

from sql_agent.tools.decorator import tool

from sql_agent.db import db
from sql_agent.db.dialect import csv_membership_clause
from sql_agent.formatting import format_response
from sql_agent.semantic_layer.loader import canonicalize_enum


@tool
def find_customers(
    segment: str | None = None, risk_category: str | None = None,
    region: str | None = None, industry: str | None = None,
    relationship_status: str | None = None,
    min_revenue: float | None = None, max_dte: float | None = None,
) -> list[dict]:
    """Find customers matching ANY COMBINATION of the optional filters below.
    Use for portfolio shortlists, not for one specific named customer.
    segment: Corporate | SME
    risk_category: Low | Medium | High
    region: Dubai | Abu Dhabi | Sharjah | Ajman | Ras Al Khaimah | Fujairah
    industry: e.g. Trading, Manufacturing, Logistics, Retail, Healthcare
    relationship_status: New | Existing | Strategic
    min_revenue: minimum annual_revenue_aed
    max_dte: maximum debt_to_equity_ratio
    """
    clauses, params = ["1=1"], {}
    if segment:
        clauses.append("customer_segment = :segment")
        params["segment"] = canonicalize_enum("customer_master", "customer_segment", segment)
    if risk_category:
        clauses.append("risk_category = :risk_category")
        params["risk_category"] = canonicalize_enum("customer_master", "risk_category", risk_category)
    if region:
        clauses.append("region = :region")
        params["region"] = canonicalize_enum("customer_master", "region", region)
    if industry:
        clauses.append("industry = :industry")
        params["industry"] = canonicalize_enum("customer_master", "industry", industry)
    if relationship_status:
        clauses.append("relationship_status = :rel_status")
        params["rel_status"] = canonicalize_enum("customer_master", "relationship_status", relationship_status)
    if min_revenue is not None:
        clauses.append("annual_revenue_aed >= :min_revenue"); params["min_revenue"] = min_revenue
    if max_dte is not None:
        clauses.append("debt_to_equity_ratio <= :max_dte"); params["max_dte"] = max_dte

    sql = f"""SELECT customer_id, customer_name, customer_segment, risk_category,
                     region, annual_revenue_aed, debt_to_equity_ratio
              FROM customer_master WHERE {' AND '.join(clauses)} LIMIT 50"""
    rows = db.execute(sql, params)
    return format_response(rows, tier="semi_dynamic", tool="find_customers")


@tool
def find_products(
    product_type: str | None = None, segment: str | None = None,
    currency: str | None = None, pricing_method: str | None = None,
) -> list[dict]:
    """Find products matching ANY COMBINATION of the optional filters.
    product_type: Loan | Trade Finance | Treasury | Deposit
    segment: Corporate | SME (checked against eligible_segments)
    currency: AED | USD
    pricing_method: Fixed | Floating
    """
    clauses, params = ["1=1"], {}
    if product_type:
        clauses.append("product_type = :product_type")
        params["product_type"] = canonicalize_enum("product_master", "product_type", product_type)
    if segment:
        clauses.append(csv_membership_clause("eligible_segments", "segment"))
        params["segment"] = canonicalize_enum("customer_master", "customer_segment", segment)
    if currency:
        clauses.append("currency = :currency")
        params["currency"] = canonicalize_enum("product_master", "currency", currency)
    if pricing_method:
        clauses.append("pricing_method = :pricing_method")
        params["pricing_method"] = canonicalize_enum("product_master", "pricing_method", pricing_method)

    sql = f"""SELECT product_id, product_name, product_type, standard_margin_pct,
                     min_ticket_size, max_ticket_size
              FROM product_master WHERE {' AND '.join(clauses)} LIMIT 50"""
    rows = db.execute(sql, params)
    return format_response(rows, tier="semi_dynamic", tool="find_products")


@tool
def find_policies(
    segment: str | None = None, product_type: str | None = None,
    risk_category: str | None = None, status: str = "Active",
) -> list[dict]:
    """List pricing policy rows matching ANY COMBINATION of the optional
    filters. Defaults to Active policies only."""
    clauses, params = ["1=1"], {}
    if segment:
        clauses.append("customer_segment = :segment")
        params["segment"] = canonicalize_enum("pricing_policy", "customer_segment", segment)
    if product_type:
        clauses.append("product_type = :product_type")
        params["product_type"] = canonicalize_enum("pricing_policy", "product_type", product_type)
    if risk_category:
        clauses.append("risk_category = :risk_category")
        params["risk_category"] = canonicalize_enum("pricing_policy", "risk_category", risk_category)
    if status:
        clauses.append("status = :status")
        params["status"] = canonicalize_enum("pricing_policy", "status", status)

    sql = f"""SELECT policy_id, customer_segment, product_type, risk_category,
                     min_margin_pct, risk_premium_pct, rwa_risk_weight_pct
              FROM pricing_policy WHERE {' AND '.join(clauses)} LIMIT 50"""
    rows = db.execute(sql, params)
    return format_response(rows, tier="semi_dynamic", tool="find_policies")


@tool
def find_deals(
    customer_id: str | None = None, product_type: str | None = None,
    outcome: str | None = None, channel: str | None = None,
    currency: str | None = None, date_from: str | None = None,
    date_to: str | None = None, below_floor: bool = False,
) -> list[dict]:
    """Find historical deals matching ANY COMBINATION of filters.
    outcome: Won | Lost
    channel: Relationship Manager | API Quote | Branch | Digital Upload
    below_floor: true -> only deals where expected_margin_pct < 0
        (a useful shortcut filter, not a raw column)
    """
    clauses, params = ["1=1"], {}
    if customer_id:
        clauses.append("customer_id = :customer_id"); params["customer_id"] = customer_id
    if product_type:
        clauses.append("product_type = :product_type")
        params["product_type"] = canonicalize_enum("historical_deals", "product_type", product_type)
    if outcome:
        clauses.append("deal_outcome = :outcome")
        params["outcome"] = canonicalize_enum("historical_deals", "deal_outcome", outcome)
    if channel:
        clauses.append("sales_channel = :channel")
        params["channel"] = canonicalize_enum("historical_deals", "sales_channel", channel)
    if currency:
        clauses.append("currency = :currency")
        params["currency"] = canonicalize_enum("historical_deals", "currency", currency)
    if date_from:
        clauses.append("deal_date >= :date_from"); params["date_from"] = date_from
    if date_to:
        clauses.append("deal_date <= :date_to"); params["date_to"] = date_to
    if below_floor:
        clauses.append("expected_margin_pct < 0")

    sql = f"""SELECT deal_id, deal_date, customer_id, product_id, product_type,
                     requested_amount, recommended_price_pct,
                     final_approved_price_pct, expected_margin_pct,
                     deal_outcome, sales_channel
              FROM historical_deals WHERE {' AND '.join(clauses)} LIMIT 50"""
    rows = db.execute(sql, params)
    return format_response(rows, tier="semi_dynamic", tool="find_deals")
