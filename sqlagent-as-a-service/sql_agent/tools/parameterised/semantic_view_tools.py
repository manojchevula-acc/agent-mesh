"""Section 4.6 — Semantic-view tools (fab_semantic.*). Parameterised, one fixed shape each.

These five tools query the business views the DATA PIPELINE service maintains in the
``fab_semantic`` schema. They are the SQL Agent's half of the cross-service contract:
the views' column shapes are declared in ``semantic_layer/schema.yaml`` and produced by
the pipeline's ``sql/03_create_semantic_views.sql``. The two must stay in sync.

Why fully-qualified ``fab_semantic.<view>`` in the SQL:
  The read-only connection's default schema is ``fab_curated`` (where the base tables
  live), so the views in ``fab_semantic`` must be addressed with their schema prefix.
  The validator's table whitelist matches on the BARE table name (sqlglot ``Table.name``
  drops the schema), so the schema-qualified reference still validates against the
  unqualified ``customer_360`` / ``margin_analysis`` / ... keys declared in schema.yaml.

Why explicit column lists instead of ``SELECT *``:
  The contract is "the agent returns only the columns it has declared; a new pipeline
  column is ignored until schema.yaml is updated" (TWO_SERVICE_DESIGN.md, loose-coupling
  table). Explicit projections enforce that and keep the response shape stable, exactly
  as every other parameterised tool in the catalogue does.
"""

from langchain_core.tools import tool

from sql_agent.db import db
from sql_agent.formatting import format_response


@tool
def get_customer_360(customer_id: str) -> dict:
    """Full customer profile plus aggregated deal KPIs (win rate, average margin,
    total volume, deal counts). Use when the question asks for a customer overview
    that includes historical deal performance — not just the raw profile fields
    (for those alone, use get_customer)."""
    sql = """
        SELECT customer_id, customer_name, customer_segment, industry, region,
               preferred_currency, risk_category, internal_rating,
               relationship_tenure_years, relationship_status,
               relationship_discount_pct, annual_revenue_aed, debt_to_equity_ratio,
               credit_score, existing_exposure_aed,
               total_deals, won_deals, lost_deals, total_deal_volume_aed,
               avg_deal_size_aed, avg_expected_margin_pct, avg_approved_price_pct,
               avg_relationship_discount_pct, last_deal_date, win_rate_pct
        FROM fab_semantic.customer_360
        WHERE customer_id = :customer_id
    """
    rows = db.execute(sql, {"customer_id": customer_id})
    return format_response(rows, tier="parameterised", tool="get_customer_360")


@tool
def get_deal_pricing_compliance(deal_id: str) -> dict:
    """Policy compliance for one historical deal: the deal's price/margin/discount
    against the applicable policy benchmarks, plus the flags price_below_policy_floor,
    margin_below_min, discount_exceeds_policy, and policy_compliant. Use when asked
    whether a specific past deal broke any pricing rule."""
    sql = """
        SELECT deal_id, deal_date, customer_id, customer_name, customer_segment,
               risk_category, internal_rating, relationship_status, product_id,
               product_name, product_type, pricing_method, currency, tenor,
               requested_amount, benchmark_rate_pct_treasury, funding_cost_pct,
               target_margin_pct, risk_premium_pct, ops_cost_margin_pct,
               relationship_discount_pct, system_recommended_price_pct,
               recommended_price_pct, approved_price_pct, expected_margin_pct,
               deal_outcome, sales_channel, policy_min_margin_pct,
               policy_min_expected_margin_pct, policy_max_discount_pct,
               policy_rwa_risk_weight_pct, price_below_policy_floor, margin_below_min,
               discount_exceeds_policy, policy_compliant
        FROM fab_semantic.pricing_recommendation_view
        WHERE deal_id = :deal_id
    """
    rows = db.execute(sql, {"deal_id": deal_id})
    return format_response(rows, tier="parameterised", tool="get_deal_pricing_compliance")


@tool
def get_deal_margin_analysis(deal_id: str) -> dict:
    """Margin decomposition for one historical deal: final price versus funding cost,
    risk premium, and relationship discount, the resulting net margin, and the spread
    over the treasury benchmark and versus the recommended price. Use when asked to
    explain where the margin on a specific deal came from."""
    sql = """
        SELECT deal_id, deal_date, customer_id, customer_name, customer_segment,
               region, risk_category, internal_rating, product_id, product_type,
               currency, tenor, requested_amount, funding_cost_pct, risk_premium_pct,
               relationship_discount_pct, recommended_price_pct,
               final_approved_price_pct, expected_margin_pct, deal_outcome,
               benchmark_rate_pct_treasury, net_margin_pct, spread_over_benchmark_pct,
               margin_vs_recommended_pct, min_expected_margin_pct, margin_below_minimum
        FROM fab_semantic.margin_analysis
        WHERE deal_id = :deal_id
    """
    rows = db.execute(sql, {"deal_id": deal_id})
    return format_response(rows, tier="parameterised", tool="get_deal_margin_analysis")


@tool
def get_customer_profitability(customer_id: str) -> dict:
    """Won deals grouped by product type for one customer: deal count, total volume,
    average approved price, average funding cost, average net margin, total expected
    margin, and a profitability tier per product type. Use when asked how profitable
    a customer is across their product mix."""
    sql = """
        SELECT customer_id, customer_segment, region, risk_category, product_type,
               total_won_deals, total_volume_aed, revenue_aed, funding_cost_aed,
               operating_cost_aed, capital_cost_aed, net_profit_aed, avg_net_margin_pct,
               profitability_tier
        FROM fab_semantic.profitability_summary
        WHERE customer_id = :customer_id
    """
    rows = db.execute(sql, {"customer_id": customer_id})
    return format_response(rows, tier="parameterised", tool="get_customer_profitability")


@tool
def get_deal_rwa_impact(deal_id: str) -> dict:
    """Basel III capital metrics for one booked (won) historical deal: RWA amount,
    8 percent capital charge, revenue, cost of funds, net revenue, and return on RWA.
    Use when asked about capital consumption on an EXISTING deal. For a prospective
    new deal use compute_rwa instead."""
    sql = """
        SELECT deal_id, deal_date, customer_id, customer_segment, product_type,
               risk_category, currency, tenor, exposure_aed, risk_weight_pct,
               rwa_aed, capital_required_aed, revenue_aed, cost_of_funds_aed,
               net_revenue_aed, return_on_rwa_pct, is_high_rwa,
               final_approved_price_pct, expected_margin_pct
        FROM fab_semantic.rwa_impact_view
        WHERE deal_id = :deal_id
    """
    rows = db.execute(sql, {"deal_id": deal_id})
    return format_response(rows, tier="parameterised", tool="get_deal_rwa_impact")
