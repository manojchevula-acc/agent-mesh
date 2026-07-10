"""Section 4.6 — Semantic-view tools (fab_semantic.*). Parameterised, fixed shapes.

These tools query the business views the DATA PIPELINE service maintains in the
``fab_semantic`` schema. They are the SQL Agent's half of the cross-service contract:
the views' column shapes are declared in ``semantic_layer/schema.yaml`` and produced by
the pipeline's ``sql/03_create_semantic_views.sql``. The two must stay in sync.

Parity note: there is one tool here for every view the DATA PIPELINE service exposes as
a first-class query tool (its ``mcp_server/tools.py``). Deal-keyed tools (``get_deal_*``)
return ONE deal; the customer-keyed variants (``get_customer_*``) return every matching
row for a customer — mirroring the pipeline's customer-filtered tools. Views that the
pipeline filters on several optional dimensions use the ``_filtered_view`` helper below,
which drops blank filters so the tool can be called with only the fields the user gave.

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

import re

from langchain_core.tools import tool

from sql_agent.db import db
from sql_agent.formatting import format_response

_CUSTOMER_ID_RE = re.compile(r"^CUST\d+$", re.IGNORECASE)


def _resolve_customer_id(value: str | None) -> str | None:
    """Best-effort NAME -> id resolution for a customer_id argument.

    There is no get_customer_by_name tool bound to the agent (it was removed —
    the model kept fabricating ids with it); when asked about a customer by NAME
    ("Gulf Star Logistics") it has nothing else to call, so it passes the name
    straight through as customer_id and the query silently returns zero rows.
    Every customer-scoped tool below routes its customer_id argument through
    here first: an already-valid id (CUSTnnn) passes through with no extra
    query; anything else is looked up by name. Only substitutes on an
    UNAMBIGUOUS match (exactly one row) — zero or multiple matches return the
    original value unchanged, so the real query still runs and fails loudly
    (zero rows) rather than silently picking the wrong customer.
    """
    if not value or _CUSTOMER_ID_RE.match(value.strip()):
        return value
    name = value.strip()
    for sql in (
        "SELECT customer_id FROM customer_master WHERE customer_name = :name LIMIT 2",
        "SELECT customer_id FROM customer_master WHERE customer_name LIKE :name LIMIT 2",
    ):
        rows = db.execute(sql, {"name": name if "= :name" in sql else f"%{name}%"})
        matches = rows.rows if hasattr(rows, "rows") else rows
        if len(matches) == 1:
            return matches[0]["customer_id"]
        if len(matches) > 1:
            break  # ambiguous — stop here rather than trying the looser LIKE pass too
    return value


def _filtered_view(view: str, columns: str, filters: dict[str, str], tool: str,
                   *, extra_where: str | None = None, order_by: str | None = None) -> dict:
    """Run a parameterised SELECT against a ``fab_semantic`` view with optional
    equality filters.

    ``filters`` maps column_name -> value; blank/None values are dropped, so a tool
    with several optional filters can be called with only the ones the user supplied
    (an all-blank call returns the view capped at the executor row cap). Column names
    are hard-coded by the caller and never taken from user input; only the values are
    parameterised. ``extra_where`` is a caller-owned literal predicate (e.g. a fixed
    boolean flag); ``order_by`` makes multi-row results deterministic.
    """
    if filters.get("customer_id"):
        filters = {**filters, "customer_id": _resolve_customer_id(filters["customer_id"])}
    clauses: list[str] = []
    params: dict[str, str] = {}
    for col, val in filters.items():
        if val is not None and str(val).strip():
            clauses.append(f"{col} = :{col}")
            params[col] = str(val).strip()
    if extra_where:
        clauses.append(extra_where)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = f"ORDER BY {order_by}" if order_by else ""
    sql = f"SELECT {columns} FROM fab_semantic.{view} {where} {order}".strip()
    rows = db.execute(sql, params)
    return format_response(rows, tier="parameterised", tool=tool)


@tool
def get_customer_360(customer_id: str) -> dict:
    """Full customer profile plus aggregated deal KPIs (win rate, average margin,
    total volume, deal counts). Use when the question asks for a customer overview
    that includes historical deal performance. Not for explaining how a price or
    margin was calculated — for that use get_pricing_trace instead."""
    customer_id = _resolve_customer_id(customer_id)
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
    customer_id = _resolve_customer_id(customer_id)
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


# ---------------------------------------------------------------------------
# Customer-keyed variants of the deal-keyed views above. The pipeline exposes
# these three views filtered by customer_id (every deal for the customer); the
# deal-keyed tools above answer the single-deal question. Both are kept.
# ---------------------------------------------------------------------------

_PRICING_RECOMMENDATION_COLUMNS = """
    deal_id, deal_date, customer_id, customer_name, customer_segment,
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
"""

_MARGIN_ANALYSIS_COLUMNS = """
    deal_id, deal_date, customer_id, customer_name, customer_segment,
    region, risk_category, internal_rating, product_id, product_type,
    currency, tenor, requested_amount, funding_cost_pct, risk_premium_pct,
    relationship_discount_pct, recommended_price_pct,
    final_approved_price_pct, expected_margin_pct, deal_outcome,
    benchmark_rate_pct_treasury, net_margin_pct, spread_over_benchmark_pct,
    margin_vs_recommended_pct, min_expected_margin_pct, margin_below_minimum
"""

_RWA_IMPACT_COLUMNS = """
    deal_id, deal_date, customer_id, customer_segment, product_type,
    risk_category, currency, tenor, exposure_aed, risk_weight_pct,
    rwa_aed, capital_required_aed, revenue_aed, cost_of_funds_aed,
    net_revenue_aed, return_on_rwa_pct, is_high_rwa,
    final_approved_price_pct, expected_margin_pct
"""


@tool
def get_customer_pricing_recommendations(customer_id: str) -> dict:
    """Pricing and policy-compliance rows for EVERY historical deal of ONE customer:
    each deal's rebuilt recommended price, approved price, expected margin, the policy
    benchmarks, and the compliance flags. Use when asked about a customer's pricing
    across their deals; for a single deal use get_deal_pricing_compliance."""
    return _filtered_view(
        "pricing_recommendation_view", _PRICING_RECOMMENDATION_COLUMNS,
        {"customer_id": customer_id}, "get_customer_pricing_recommendations",
        order_by="deal_date DESC")


@tool
def get_customer_margin_analysis(customer_id: str) -> dict:
    """Margin decomposition for EVERY historical deal of ONE customer: net margin,
    spread over the treasury benchmark, variance versus the recommended price, and the
    margin-below-minimum flag per deal. Use for a customer-wide margin picture; for a
    single deal use get_deal_margin_analysis."""
    return _filtered_view(
        "margin_analysis", _MARGIN_ANALYSIS_COLUMNS,
        {"customer_id": customer_id}, "get_customer_margin_analysis",
        order_by="deal_date DESC")


@tool
def get_customer_rwa_impact(customer_id: str) -> dict:
    """Basel III RWA and capital metrics for EVERY won deal of ONE customer: exposure,
    RWA, capital required, revenue, net revenue, and return on RWA per deal. Use for a
    customer-wide capital picture; for a single deal use get_deal_rwa_impact."""
    return _filtered_view(
        "rwa_impact_view", _RWA_IMPACT_COLUMNS,
        {"customer_id": customer_id}, "get_customer_rwa_impact",
        order_by="deal_date DESC")


# ---------------------------------------------------------------------------
# Remaining fab_semantic views the pipeline exposes as query tools.
# ---------------------------------------------------------------------------

@tool
def get_new_customer_pricing(customer_id: str = "", customer_segment: str = "",
                             product_id: str = "", risk_category: str = "") -> dict:
    """Recommended price for a PROSPECT with no relationship history. Filter by any
    combination of customer_id, customer_segment (Corporate/SME), product_id, or
    risk_category (Low/Medium/High). Use for "what rate for a new customer" questions —
    for an existing customer use get_customer_pricing_recommendations instead."""
    columns = """
        customer_id, customer_name, customer_segment, industry, region,
        risk_category, internal_rating, relationship_status,
        no_previous_relationship_flag, product_id, product_name, product_type,
        currency, tenor, requested_amount, benchmark_rate_pct_treasury,
        funding_cost_pct, target_margin_pct, risk_premium_pct,
        new_customer_buffer_pct, ops_cost_margin_pct, recommended_price_pct,
        policy_floor_margin_pct, min_profitability_margin_pct
    """
    return _filtered_view(
        "new_customer_pricing_view", columns,
        {"customer_id": customer_id, "customer_segment": customer_segment,
         "product_id": product_id, "risk_category": risk_category},
        "get_new_customer_pricing", order_by="customer_id")


@tool
def get_competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> dict:
    """FAB versus competitor comparison per negotiation: FAB price, competitor price,
    the gap in basis points, the profitability floor, and a MATCH / COUNTER / ESCALATE /
    REJECT suggested action with its reason. Filter by customer_id and/or deal_id. This
    is also the tool for a direct "FAB vs competitor" comparison."""
    columns = """
        deal_id, conversation_id, customer_id, customer_name, product_id,
        product_name, product_type, competitor_name, fab_price_pct,
        competitor_price_pct, profitability_floor_pct, max_reducible_pct,
        competitor_gap_bps, suggested_action, action_reason,
        last_agent_recommendation, rm_note
    """
    return _filtered_view(
        "competitor_price_analysis", columns,
        {"customer_id": customer_id, "deal_id": deal_id},
        "get_competitor_price_analysis", order_by="deal_id")


@tool
def get_pricing_trace(customer_id: str = "", deal_id: str = "") -> dict:
    """Step-by-step recommended-price build-up for a deal: the treasury, target-margin,
    risk-premium, operations-cost, and relationship-discount components that sum to the
    recommended price, plus a human-readable explanation sentence. Filter by customer_id
    and/or deal_id — deal_id is OPTIONAL: if you were not given one, OMIT it rather than
    guessing a value; customer_id alone returns every deal for that customer. Use when
    asked to EXPLAIN how a price was constructed."""
    columns = """
        deal_id, customer_id, customer_name, customer_segment, risk_category,
        product_id, product_type, currency, tenor, treasury_rate_component,
        target_margin_component, risk_premium_component, operations_cost_component,
        relationship_discount_component, final_recommended_price,
        approved_price_pct, explanation_text
    """
    return _filtered_view(
        "pricing_trace_view", columns,
        {"customer_id": customer_id, "deal_id": deal_id},
        "get_pricing_trace", order_by="deal_id")


@tool
def get_segment_pricing_benchmark(customer_segment: str = "", product_id: str = "") -> dict:
    """Segment pricing guideline: base margin floor, target margin, risk premium,
    new-customer buffer, max relationship discount, minimum profitability, and the
    discount-approval threshold, per segment x product. Filter by customer_segment
    (Corporate/SME) and/or product_id."""
    columns = """
        customer_segment, product_type, product_id, product_name, risk_category,
        base_margin_floor_pct, target_margin_pct, risk_premium_pct,
        new_customer_buffer_pct, max_relationship_discount_pct,
        min_profitability_margin_pct, approval_required_if_discount_above_pct,
        pricing_cushion_for_rating_drop_pct
    """
    return _filtered_view(
        "segment_pricing_benchmark", columns,
        {"customer_segment": customer_segment, "product_id": product_id},
        "get_segment_pricing_benchmark", order_by="product_id")


@tool
def get_operations_cost_impact(product_id: str = "", customer_segment: str = "") -> dict:
    """Operational cost impact on pricing: the ops cost margin plus the annual operating,
    onboarding, monthly servicing, and exception-handling cost averages, per product x
    customer segment. Filter by product_id and/or customer_segment (Corporate/SME)."""
    columns = """
        product_id, product_name, product_type, customer_segment,
        ops_cost_margin_pct, avg_annual_operating_cost_aed,
        avg_onboarding_cost_aed, avg_monthly_servicing_cost_aed,
        avg_exception_handling_cost_aed
    """
    return _filtered_view(
        "operations_cost_impact", columns,
        {"product_id": product_id, "customer_segment": customer_segment},
        "get_operations_cost_impact", order_by="product_id")


@tool
def get_relationship_discount(customer_id: str) -> dict:
    """Relationship-discount eligibility for ONE customer: the negotiated discount, the
    policy cap, the approval threshold, and the discount_eligible / approval_required
    verdicts with an explanation. Use when asked whether a customer's discount is allowed
    or needs approval."""
    columns = """
        customer_id, customer_name, customer_segment, risk_category,
        relationship_status, relationship_tenure_years, relationship_discount_pct,
        policy_max_relationship_discount_pct,
        approval_required_if_discount_above_pct, discount_eligible,
        approval_required, eligibility_reason
    """
    return _filtered_view(
        "relationship_discount_view", columns,
        {"customer_id": customer_id}, "get_relationship_discount")


@tool
def get_win_loss_insights(customer_id: str = "", product_id: str = "",
                          customer_segment: str = "") -> dict:
    """Won/lost aggregation per customer x product: total/won/lost counts, win rate,
    average approved and recommended prices, the average price gap, expected margin, and
    the average competitor offer rate. Filter by customer_id, product_id, and/or
    customer_segment (Corporate/SME)."""
    columns = """
        customer_id, customer_name, customer_segment, product_id, product_type,
        total_deals, won_deals, lost_deals, win_rate_pct, avg_approved_price_pct,
        avg_recommended_price_pct, avg_price_gap_pct, avg_expected_margin_pct,
        avg_competitor_offer_rate_pct
    """
    return _filtered_view(
        "win_loss_insights", columns,
        {"customer_id": customer_id, "product_id": product_id,
         "customer_segment": customer_segment},
        "get_win_loss_insights", order_by="customer_id")


@tool
def get_policy_exceptions(customer_id: str = "", deal_id: str = "") -> dict:
    """Per-deal policy-breach detail: which rules a deal broke (margin below minimum,
    discount over cap, price below floor, competitor-match / high-RWA approval), the
    is_exception flag, and a pipe-delimited reason string. Filter by customer_id and/or
    deal_id. Use to see WHY deals are non-compliant; for only the breaching deals use
    get_non_compliant_deals."""
    columns = """
        deal_id, customer_id, customer_name, customer_segment, product_id,
        product_type, approved_price_pct, expected_margin_pct,
        relationship_discount_pct, min_margin_pct, min_expected_margin_pct,
        max_relationship_discount_pct, approval_required_if_discount_above_pct,
        rwa_risk_weight_pct, margin_below_min, discount_exceeds_policy,
        price_below_floor, competitor_match_requires_approval,
        high_rwa_requires_approval, is_exception, exception_reason
    """
    return _filtered_view(
        "policy_exception_view", columns,
        {"customer_id": customer_id, "deal_id": deal_id},
        "get_policy_exceptions", order_by="deal_id")


@tool
def get_non_compliant_deals(customer_id: str = "") -> dict:
    """Only the deals that breach at least one policy rule (is_exception = true), with
    the same breach detail as get_policy_exceptions. Optionally filter by customer_id.
    Use for "which deals are non-compliant" questions."""
    columns = """
        deal_id, customer_id, customer_name, customer_segment, product_id,
        product_type, approved_price_pct, expected_margin_pct,
        relationship_discount_pct, min_margin_pct, min_expected_margin_pct,
        max_relationship_discount_pct, approval_required_if_discount_above_pct,
        rwa_risk_weight_pct, margin_below_min, discount_exceeds_policy,
        price_below_floor, competitor_match_requires_approval,
        high_rwa_requires_approval, is_exception, exception_reason
    """
    return _filtered_view(
        "policy_exception_view", columns,
        {"customer_id": customer_id}, "get_non_compliant_deals",
        extra_where="is_exception = TRUE", order_by="deal_id")


@tool
def get_cross_sell_opportunity(customer_segment: str = "", industry: str = "") -> dict:
    """Active cross-sell product recommendations by customer segment and industry: the
    product the customer already holds, the recommended next product, the expected
    incremental revenue, a priority label, and the rationale. Filter by customer_segment
    (Corporate/SME) and/or industry."""
    columns = """
        cross_sell_rule_id, customer_segment, industry, trigger_condition,
        primary_product_id, primary_product_name, recommended_product_id,
        recommended_product_name, expected_incremental_revenue_aed,
        cross_sell_priority, rationale, rule_status
    """
    return _filtered_view(
        "cross_sell_opportunity", columns,
        {"customer_segment": customer_segment, "industry": industry},
        "get_cross_sell_opportunity",
        extra_where="rule_status = 'Active'", order_by="cross_sell_rule_id")


@tool
def get_credit_rating_events(customer_id: str = "") -> dict:
    """Credit-rating migration events: the old/new internal rating and risk category,
    the change direction and reason, and the recommended pricing action (additional risk
    premium, floating-rate-required, credit-review-required flags). Optionally filter by
    customer_id."""
    columns = """
        rating_event_id, customer_id, customer_name, event_date,
        old_internal_rating, new_internal_rating, old_risk_category,
        new_risk_category, rating_change_direction, reason_code,
        recommended_pricing_action, additional_risk_premium_pct,
        floating_rate_required_flag, credit_review_required_flag
    """
    return _filtered_view(
        "credit_rating_events", columns,
        {"customer_id": customer_id}, "get_credit_rating_events",
        order_by="event_date DESC")


@tool
def get_similar_customer_pricing(new_customer_id: str = "") -> dict:
    """Reference-similarity pricing for a new/prospect customer: the most similar
    existing customer(s), the similarity score, the reference deal's final price, the
    adjustment applied for the new customer, and the resulting suggested price with its
    rationale. Optionally filter by new_customer_id."""
    columns = """
        similarity_mapping_id, new_customer_id, new_customer_name,
        reference_customer_id, reference_customer_name, customer_segment,
        requested_product_id, requested_product_name, similarity_score,
        reference_final_price_pct, adjustment_for_new_customer_pct,
        suggested_price_pct, suggested_price_adjustment_rationale
    """
    return _filtered_view(
        "similar_customer_pricing", columns,
        {"new_customer_id": new_customer_id}, "get_similar_customer_pricing",
        order_by="similarity_score DESC")
