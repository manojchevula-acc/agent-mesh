-- =============================================================
-- 03_create_semantic_views.sql
-- Creates the enhanced business-ready views in fab_semantic.
-- All views SELECT only from fab_curated tables.
-- Run after 03_load_curated_to_mysql.py has loaded all tables.
--
-- Helper views (segment_pricing_benchmark, operations_cost_impact)
-- are created first because other views reference them.
-- =============================================================

USE fab_semantic;

-- =============================================================
-- HELPER 1. segment_pricing_benchmark
--   Aggregated segment pricing guideline per
--   segment x product_type x risk_category, exposed with
--   product_id / product_name for convenient lookups.
-- =============================================================
CREATE OR REPLACE VIEW segment_pricing_benchmark AS
SELECT
    b.customer_segment,
    b.product_type,
    pm.product_id,
    pm.product_name,
    b.risk_category,
    ROUND(b.base_margin_floor_pct, 4)                       AS base_margin_floor_pct,
    ROUND(b.target_margin_pct, 4)                           AS target_margin_pct,
    ROUND(b.seg_risk_premium_pct, 4)                        AS risk_premium_pct,
    ROUND(b.new_customer_buffer_pct, 4)                     AS new_customer_buffer_pct,
    ROUND(b.max_relationship_discount_pct, 4)               AS max_relationship_discount_pct,
    ROUND(b.min_profitability_margin_pct, 4)                AS min_profitability_margin_pct,
    ROUND(b.approval_required_if_discount_above_pct, 4)     AS approval_required_if_discount_above_pct,
    ROUND(b.pricing_cushion_for_rating_drop_pct, 4)         AS pricing_cushion_for_rating_drop_pct
FROM (
    SELECT
        customer_segment,
        product_type,
        risk_category,
        AVG(base_margin_floor_pct)                       AS base_margin_floor_pct,
        AVG(target_margin_pct)                           AS target_margin_pct,
        AVG(risk_premium_pct)                            AS seg_risk_premium_pct,
        AVG(new_customer_buffer_pct)                     AS new_customer_buffer_pct,
        AVG(max_relationship_discount_pct)               AS max_relationship_discount_pct,
        AVG(min_profitability_margin_pct)                AS min_profitability_margin_pct,
        AVG(approval_required_if_discount_above_pct)     AS approval_required_if_discount_above_pct,
        AVG(pricing_cushion_for_rating_drop_pct)         AS pricing_cushion_for_rating_drop_pct
    FROM fab_curated.customer_segment_pricing_rules
    WHERE rule_status = 'Active'
    GROUP BY customer_segment, product_type, risk_category
) b
LEFT JOIN fab_curated.product_master pm
       ON pm.product_type = b.product_type;


-- =============================================================
-- HELPER 2. operations_cost_impact
--   Operational cost margin per product x customer_segment.
-- =============================================================
CREATE OR REPLACE VIEW operations_cost_impact AS
SELECT
    product_id,
    MAX(product_name)                            AS product_name,
    MAX(product_type)                            AS product_type,
    customer_segment,
    ROUND(AVG(ops_cost_margin_pct), 4)           AS ops_cost_margin_pct,
    ROUND(AVG(annual_operating_cost_aed), 2)     AS avg_annual_operating_cost_aed,
    ROUND(AVG(onboarding_cost_aed), 2)           AS avg_onboarding_cost_aed,
    ROUND(AVG(monthly_servicing_cost_aed), 2)    AS avg_monthly_servicing_cost_aed,
    ROUND(AVG(exception_handling_cost_aed), 2)   AS avg_exception_handling_cost_aed
FROM fab_curated.operations_cost
GROUP BY product_id, customer_segment;


-- =============================================================
-- 1. customer_360
--    Full customer profile enriched with aggregated deal KPIs.
-- =============================================================
CREATE OR REPLACE VIEW customer_360 AS
SELECT
    c.customer_id,
    c.customer_name,
    c.customer_segment,
    c.industry,
    c.region,
    c.preferred_currency,
    c.risk_category,
    c.internal_rating,
    c.relationship_tenure_years,
    c.relationship_status,
    c.relationship_discount_pct,
    c.annual_revenue_aed,
    c.debt_to_equity_ratio,
    c.credit_score,
    c.existing_exposure_aed,
    COALESCE(da.total_deals, 0)                                  AS total_deals,
    COALESCE(da.won_deals, 0)                                    AS won_deals,
    COALESCE(da.lost_deals, 0)                                   AS lost_deals,
    COALESCE(da.total_deal_volume_aed, 0)                        AS total_deal_volume_aed,
    COALESCE(da.avg_deal_size_aed, 0)                            AS avg_deal_size_aed,
    COALESCE(da.avg_expected_margin_pct, 0)                      AS avg_expected_margin_pct,
    COALESCE(da.avg_approved_price_pct, 0)                       AS avg_approved_price_pct,
    COALESCE(da.avg_relationship_discount_pct, 0)                AS avg_relationship_discount_pct,
    da.last_deal_date,
    COALESCE(ROUND(da.won_deals / NULLIF(da.total_deals, 0) * 100, 2), 0) AS win_rate_pct
FROM fab_curated.customer_master c
LEFT JOIN (
    SELECT
        customer_id,
        COUNT(*)                              AS total_deals,
        SUM(deal_outcome = 'Won')             AS won_deals,
        SUM(deal_outcome = 'Lost')            AS lost_deals,
        SUM(requested_amount)                 AS total_deal_volume_aed,
        AVG(requested_amount)                 AS avg_deal_size_aed,
        AVG(expected_margin_pct)              AS avg_expected_margin_pct,
        AVG(final_approved_price_pct)         AS avg_approved_price_pct,
        AVG(relationship_discount_pct)        AS avg_relationship_discount_pct,
        MAX(deal_date)                        AS last_deal_date
    FROM fab_curated.historical_deals
    GROUP BY customer_id
) da ON c.customer_id = da.customer_id;


-- =============================================================
-- 2. pricing_recommendation_view
--    Each deal enriched with product, treasury, policy, segment
--    benchmark and operational cost; recommended price rebuilt
--    from its pricing components plus compliance flags.
-- =============================================================
CREATE OR REPLACE VIEW pricing_recommendation_view AS
SELECT
    d.deal_id,
    d.deal_date,
    d.customer_id,
    c.customer_name,
    c.customer_segment,
    c.risk_category,
    c.internal_rating,
    c.relationship_status,
    d.product_id,
    pm.product_name,
    d.product_type,
    pm.pricing_method,
    d.currency,
    d.tenor,
    d.requested_amount,
    lr.benchmark_rate_pct_treasury,
    d.funding_cost_pct,
    COALESCE(spb.target_margin_pct, d.standard_margin_pct)        AS target_margin_pct,
    d.risk_premium_pct,
    COALESCE(ops.ops_cost_margin_pct, 0)                          AS ops_cost_margin_pct,
    d.relationship_discount_pct,
    ROUND(
        d.funding_cost_pct
        + COALESCE(spb.target_margin_pct, d.standard_margin_pct)
        + d.risk_premium_pct
        + COALESCE(ops.ops_cost_margin_pct, 0)
        - d.relationship_discount_pct, 4)                         AS system_recommended_price_pct,
    d.recommended_price_pct,
    d.final_approved_price_pct                                    AS approved_price_pct,
    d.expected_margin_pct,
    d.deal_outcome,
    d.sales_channel,
    pp.min_margin_pct                          AS policy_min_margin_pct,
    pp.min_expected_margin_pct                 AS policy_min_expected_margin_pct,
    pp.max_relationship_discount_pct           AS policy_max_discount_pct,
    pp.rwa_risk_weight_pct                     AS policy_rwa_risk_weight_pct,
    (d.final_approved_price_pct < pp.min_margin_pct)             AS price_below_policy_floor,
    (d.expected_margin_pct < pp.min_expected_margin_pct)        AS margin_below_min,
    (d.relationship_discount_pct > pp.max_relationship_discount_pct) AS discount_exceeds_policy,
    NOT (
        (d.final_approved_price_pct < pp.min_margin_pct)
        OR (d.expected_margin_pct < pp.min_expected_margin_pct)
        OR (d.relationship_discount_pct > pp.max_relationship_discount_pct)
    )                                                            AS policy_compliant
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_curated.product_master pm
       ON d.product_id = pm.product_id
LEFT JOIN fab_curated.pricing_policy pp
       ON  pp.customer_segment = c.customer_segment
       AND pp.product_type     = d.product_type
       AND pp.risk_category    = c.risk_category
       AND pp.status           = 'Active'
LEFT JOIN fab_semantic.segment_pricing_benchmark spb
       ON  spb.customer_segment = c.customer_segment
       AND spb.product_id       = d.product_id
       AND spb.risk_category    = c.risk_category
LEFT JOIN fab_semantic.operations_cost_impact ops
       ON  ops.product_id       = d.product_id
       AND ops.customer_segment = c.customer_segment
LEFT JOIN (
    SELECT r.currency, r.tenor,
           r.benchmark_rate_pct AS benchmark_rate_pct_treasury
    FROM fab_curated.treasury_rate_sheet r
    INNER JOIN (
        SELECT currency, tenor, MAX(effective_date) AS max_date
        FROM fab_curated.treasury_rate_sheet
        GROUP BY currency, tenor
    ) lt ON r.currency = lt.currency AND r.tenor = lt.tenor
        AND r.effective_date = lt.max_date
) lr ON d.currency = lr.currency AND d.tenor = lr.tenor;


-- =============================================================
-- 3. margin_analysis
-- =============================================================
CREATE OR REPLACE VIEW margin_analysis AS
SELECT
    d.deal_id,
    d.deal_date,
    d.customer_id,
    c.customer_name,
    c.customer_segment,
    c.region,
    c.risk_category,
    c.internal_rating,
    d.product_id,
    d.product_type,
    d.currency,
    d.tenor,
    d.requested_amount,
    d.funding_cost_pct,
    d.risk_premium_pct,
    d.relationship_discount_pct,
    d.recommended_price_pct,
    d.final_approved_price_pct,
    d.expected_margin_pct,
    d.deal_outcome,
    lr.benchmark_rate_pct_treasury,
    ROUND(d.final_approved_price_pct - d.funding_cost_pct
          - d.risk_premium_pct - d.relationship_discount_pct, 4) AS net_margin_pct,
    ROUND(d.final_approved_price_pct - lr.benchmark_rate_pct_treasury, 4) AS spread_over_benchmark_pct,
    ROUND(d.final_approved_price_pct - d.recommended_price_pct, 4)        AS margin_vs_recommended_pct,
    COALESCE(pp.min_expected_margin_pct, 0)                              AS min_expected_margin_pct,
    (d.expected_margin_pct < pp.min_expected_margin_pct)               AS margin_below_minimum
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_curated.pricing_policy pp
       ON  pp.customer_segment = c.customer_segment
       AND pp.product_type     = d.product_type
       AND pp.risk_category    = c.risk_category
       AND pp.status           = 'Active'
LEFT JOIN (
    SELECT r.currency, r.tenor,
           r.benchmark_rate_pct AS benchmark_rate_pct_treasury
    FROM fab_curated.treasury_rate_sheet r
    INNER JOIN (
        SELECT currency, tenor, MAX(effective_date) AS max_date
        FROM fab_curated.treasury_rate_sheet
        GROUP BY currency, tenor
    ) lt ON r.currency = lt.currency AND r.tenor = lt.tenor
        AND r.effective_date = lt.max_date
) lr ON d.currency = lr.currency AND d.tenor = lr.tenor;


-- =============================================================
-- 4. profitability_summary
--    Won-deal roll-up by customer x product_type with capital
--    cost and profitability tier.
-- =============================================================
CREATE OR REPLACE VIEW profitability_summary AS
SELECT
    d.customer_id,
    c.customer_segment,
    c.region,
    c.risk_category,
    d.product_type,
    COUNT(*)                                                      AS total_won_deals,
    SUM(d.requested_amount)                                       AS total_volume_aed,
    ROUND(SUM(d.requested_amount * d.final_approved_price_pct / 100), 2)  AS revenue_aed,
    ROUND(SUM(d.requested_amount * d.funding_cost_pct / 100), 2)         AS funding_cost_aed,
    ROUND(SUM(d.requested_amount * COALESCE(ops.ops_cost_margin_pct, 0) / 100), 2) AS operating_cost_aed,
    ROUND(SUM(d.requested_amount * pp.rwa_risk_weight_pct / 100 * 0.08 * 0.10), 2) AS capital_cost_aed,
    ROUND(SUM(
        d.requested_amount
        * (d.final_approved_price_pct - d.funding_cost_pct
           - d.risk_premium_pct - d.relationship_discount_pct
           - COALESCE(ops.ops_cost_margin_pct, 0)) / 100), 2)   AS net_profit_aed,
    ROUND(AVG(
        d.final_approved_price_pct - d.funding_cost_pct
        - d.risk_premium_pct - d.relationship_discount_pct), 4) AS avg_net_margin_pct,
    CASE
        WHEN AVG(d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct) < 0   THEN 'Loss-Making'
        WHEN AVG(d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct) < 0.5 THEN 'Low'
        WHEN AVG(d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct) < 1.0 THEN 'Medium'
        ELSE 'High'
    END                                                          AS profitability_tier
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_curated.pricing_policy pp
       ON  pp.customer_segment = c.customer_segment
       AND pp.product_type     = d.product_type
       AND pp.risk_category    = c.risk_category
       AND pp.status           = 'Active'
LEFT JOIN fab_semantic.operations_cost_impact ops
       ON  ops.product_id       = d.product_id
       AND ops.customer_segment = c.customer_segment
WHERE d.deal_outcome = 'Won'
GROUP BY d.customer_id, c.customer_segment, c.region, c.risk_category, d.product_type;


-- =============================================================
-- 5. rwa_impact_view
-- =============================================================
CREATE OR REPLACE VIEW rwa_impact_view AS
SELECT
    d.deal_id,
    d.deal_date,
    d.customer_id,
    c.customer_segment,
    d.product_type,
    c.risk_category,
    d.currency,
    d.tenor,
    d.requested_amount                                          AS exposure_aed,
    pp.rwa_risk_weight_pct                                      AS risk_weight_pct,
    ROUND(d.requested_amount * pp.rwa_risk_weight_pct / 100, 2) AS rwa_aed,
    ROUND(d.requested_amount * pp.rwa_risk_weight_pct / 100 * 0.08, 2) AS capital_required_aed,
    ROUND(d.requested_amount * d.final_approved_price_pct / 100, 2)    AS revenue_aed,
    ROUND(d.requested_amount * d.funding_cost_pct / 100, 2)     AS cost_of_funds_aed,
    ROUND(d.requested_amount * d.final_approved_price_pct / 100
          - d.requested_amount * d.funding_cost_pct / 100, 2)  AS net_revenue_aed,
    ROUND(
        (d.requested_amount * d.final_approved_price_pct / 100
         - d.requested_amount * d.funding_cost_pct / 100)
        / NULLIF(d.requested_amount * pp.rwa_risk_weight_pct / 100, 0) * 100, 4) AS return_on_rwa_pct,
    (pp.rwa_risk_weight_pct >= 100)                            AS is_high_rwa,
    d.final_approved_price_pct,
    d.expected_margin_pct
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_curated.pricing_policy pp
       ON  pp.customer_segment = c.customer_segment
       AND pp.product_type     = d.product_type
       AND pp.risk_category    = c.risk_category
       AND pp.status           = 'Active'
WHERE d.deal_outcome = 'Won';


-- =============================================================
-- 6. new_customer_pricing_view
--    Prices prospects with NO relationship history using segment
--    benchmark + treasury + product + operations cost.
-- =============================================================
CREATE OR REPLACE VIEW new_customer_pricing_view AS
SELECT
    p.customer_id,
    p.customer_name,
    p.customer_segment,
    p.industry,
    p.region,
    p.risk_category,
    p.internal_rating,
    p.relationship_status,
    p.no_previous_relationship_flag,
    p.requested_product_id                         AS product_id,
    pm.product_name,
    p.requested_product_type                       AS product_type,
    p.preferred_currency                           AS currency,
    p.requested_tenor                              AS tenor,
    p.requested_amount_aed                         AS requested_amount,
    lr.benchmark_rate_pct_treasury,
    lr.funding_cost_pct_treasury                   AS funding_cost_pct,
    COALESCE(spb.target_margin_pct, pm.standard_margin_pct)    AS target_margin_pct,
    COALESCE(spb.risk_premium_pct, 0)              AS risk_premium_pct,
    COALESCE(spb.new_customer_buffer_pct, 0)       AS new_customer_buffer_pct,
    COALESCE(ops.ops_cost_margin_pct, 0)           AS ops_cost_margin_pct,
    ROUND(
        COALESCE(lr.funding_cost_pct_treasury, 0)
        + COALESCE(spb.target_margin_pct, pm.standard_margin_pct)
        + COALESCE(spb.risk_premium_pct, 0)
        + COALESCE(spb.new_customer_buffer_pct, 0)
        + COALESCE(ops.ops_cost_margin_pct, 0), 4)  AS recommended_price_pct,
    COALESCE(spb.base_margin_floor_pct, 0)         AS policy_floor_margin_pct,
    COALESCE(spb.min_profitability_margin_pct, 0)  AS min_profitability_margin_pct
FROM fab_curated.prospect_customer_profile p
LEFT JOIN fab_curated.product_master pm
       ON p.requested_product_id = pm.product_id
LEFT JOIN fab_semantic.segment_pricing_benchmark spb
       ON  spb.customer_segment = p.customer_segment
       AND spb.product_id       = p.requested_product_id
       AND spb.risk_category    = p.risk_category
LEFT JOIN fab_semantic.operations_cost_impact ops
       ON  ops.product_id       = p.requested_product_id
       AND ops.customer_segment = p.customer_segment
LEFT JOIN (
    SELECT r.currency, r.tenor,
           r.benchmark_rate_pct AS benchmark_rate_pct_treasury,
           r.funding_cost_pct   AS funding_cost_pct_treasury
    FROM fab_curated.treasury_rate_sheet r
    INNER JOIN (
        SELECT currency, tenor, MAX(effective_date) AS max_date
        FROM fab_curated.treasury_rate_sheet
        GROUP BY currency, tenor
    ) lt ON r.currency = lt.currency AND r.tenor = lt.tenor
        AND r.effective_date = lt.max_date
) lr ON p.preferred_currency = lr.currency AND p.requested_tenor = lr.tenor;


-- =============================================================
-- 7. competitor_price_analysis
--    FAB offer vs competitor offer (from negotiation memory)
--    with MATCH / COUNTER / ESCALATE / REJECT recommendation.
-- =============================================================
CREATE OR REPLACE VIEW competitor_price_analysis AS
SELECT
    m.memory_interaction_id                        AS deal_id,
    m.conversation_id,
    m.customer_id,
    m.customer_name,
    m.product_id,
    m.product_name,
    m.product_type,
    m.competitor_name,
    ROUND(m.initial_offered_price_pct, 4)          AS fab_price_pct,
    ROUND(m.competitor_offer_rate_pct, 4)          AS competitor_price_pct,
    ROUND(m.profitability_floor_price_pct, 4)      AS profitability_floor_pct,
    ROUND(m.max_reducible_without_profitability_impact_pct, 4) AS max_reducible_pct,
    ROUND((m.initial_offered_price_pct - m.competitor_offer_rate_pct) * 100, 1) AS competitor_gap_bps,
    CASE
        WHEN m.competitor_offer_rate_pct < m.profitability_floor_price_pct THEN 'REJECT'
        WHEN (m.initial_offered_price_pct - m.competitor_offer_rate_pct) * 100 <= 20 THEN 'MATCH'
        WHEN (m.initial_offered_price_pct - m.competitor_offer_rate_pct) * 100 <= 60 THEN 'COUNTER'
        ELSE 'ESCALATE'
    END                                            AS suggested_action,
    CASE
        WHEN m.competitor_offer_rate_pct < m.profitability_floor_price_pct
            THEN 'Competitor price is below FAB profitability floor - matching would be loss-making.'
        WHEN (m.initial_offered_price_pct - m.competitor_offer_rate_pct) * 100 <= 20
            THEN 'Gap is small and stays above floor - matching is safe.'
        WHEN (m.initial_offered_price_pct - m.competitor_offer_rate_pct) * 100 <= 60
            THEN 'Moderate gap - counter-offer between FAB price and competitor price.'
        ELSE 'Large gap but above floor - escalate for pricing approval.'
    END                                            AS action_reason,
    m.last_agent_recommendation,
    m.rm_note
FROM fab_curated.pricing_negotiation_memory m;


-- =============================================================
-- 8. pricing_trace_view
--    Step-by-step decomposition of the recommended price per deal.
-- =============================================================
CREATE OR REPLACE VIEW pricing_trace_view AS
SELECT
    d.deal_id,
    d.customer_id,
    c.customer_name,
    c.customer_segment,
    c.risk_category,
    d.product_id,
    d.product_type,
    d.currency,
    d.tenor,
    ROUND(d.funding_cost_pct, 4)                                 AS treasury_rate_component,
    ROUND(COALESCE(spb.target_margin_pct, d.standard_margin_pct), 4) AS target_margin_component,
    ROUND(d.risk_premium_pct, 4)                                 AS risk_premium_component,
    ROUND(COALESCE(ops.ops_cost_margin_pct, 0), 4)               AS operations_cost_component,
    ROUND(d.relationship_discount_pct, 4)                        AS relationship_discount_component,
    ROUND(
        d.funding_cost_pct
        + COALESCE(spb.target_margin_pct, d.standard_margin_pct)
        + d.risk_premium_pct
        + COALESCE(ops.ops_cost_margin_pct, 0)
        - d.relationship_discount_pct, 4)                        AS final_recommended_price,
    ROUND(d.final_approved_price_pct, 4)                         AS approved_price_pct,
    CONCAT(
        'Treasury/funding ', ROUND(d.funding_cost_pct, 2),
        '% + target margin ', ROUND(COALESCE(spb.target_margin_pct, d.standard_margin_pct), 2),
        '% + risk premium ', ROUND(d.risk_premium_pct, 2),
        '% + ops cost ', ROUND(COALESCE(ops.ops_cost_margin_pct, 0), 2),
        '% - relationship discount ', ROUND(d.relationship_discount_pct, 2),
        '% = recommended ',
        ROUND(d.funding_cost_pct + COALESCE(spb.target_margin_pct, d.standard_margin_pct)
              + d.risk_premium_pct + COALESCE(ops.ops_cost_margin_pct, 0)
              - d.relationship_discount_pct, 2), '%'
    )                                                            AS explanation_text
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_semantic.segment_pricing_benchmark spb
       ON  spb.customer_segment = c.customer_segment
       AND spb.product_id       = d.product_id
       AND spb.risk_category    = c.risk_category
LEFT JOIN fab_semantic.operations_cost_impact ops
       ON  ops.product_id       = d.product_id
       AND ops.customer_segment = c.customer_segment;


-- =============================================================
-- 9. relationship_discount_view
--    Relationship discount eligibility & approval requirement.
-- =============================================================
CREATE OR REPLACE VIEW relationship_discount_view AS
SELECT
    c.customer_id,
    c.customer_name,
    c.customer_segment,
    c.risk_category,
    c.relationship_status,
    c.relationship_tenure_years,
    ROUND(c.relationship_discount_pct, 4)          AS relationship_discount_pct,
    ROUND(pol.max_relationship_discount_pct, 4)    AS policy_max_relationship_discount_pct,
    ROUND(pol.approval_required_if_discount_above_pct, 4) AS approval_required_if_discount_above_pct,
    (c.relationship_discount_pct <= pol.max_relationship_discount_pct) AS discount_eligible,
    (c.relationship_discount_pct > pol.approval_required_if_discount_above_pct) AS approval_required,
    CASE
        WHEN c.relationship_discount_pct > pol.max_relationship_discount_pct
            THEN 'Discount exceeds policy cap - not eligible without exception.'
        WHEN c.relationship_discount_pct > pol.approval_required_if_discount_above_pct
            THEN 'Discount within cap but above approval threshold - approval required.'
        ELSE 'Discount within policy - auto-eligible.'
    END                                            AS eligibility_reason
FROM fab_curated.customer_master c
LEFT JOIN (
    SELECT customer_segment, risk_category,
           MAX(max_relationship_discount_pct)              AS max_relationship_discount_pct,
           MIN(approval_required_if_discount_above_pct)    AS approval_required_if_discount_above_pct
    FROM fab_curated.pricing_policy
    WHERE status = 'Active'
    GROUP BY customer_segment, risk_category
) pol ON pol.customer_segment = c.customer_segment
     AND pol.risk_category    = c.risk_category;


-- =============================================================
-- 10. win_loss_insights
--     Won/lost aggregation by customer x product with pricing
--     gap and competitor pressure.
-- =============================================================
CREATE OR REPLACE VIEW win_loss_insights AS
SELECT
    d.customer_id,
    c.customer_name,
    c.customer_segment,
    d.product_id,
    d.product_type,
    COUNT(*)                                                     AS total_deals,
    SUM(d.deal_outcome = 'Won')                                 AS won_deals,
    SUM(d.deal_outcome = 'Lost')                                AS lost_deals,
    ROUND(SUM(d.deal_outcome = 'Won') / COUNT(*) * 100, 2)      AS win_rate_pct,
    ROUND(AVG(d.final_approved_price_pct), 4)                   AS avg_approved_price_pct,
    ROUND(AVG(d.recommended_price_pct), 4)                      AS avg_recommended_price_pct,
    ROUND(AVG(d.final_approved_price_pct - d.recommended_price_pct), 4) AS avg_price_gap_pct,
    ROUND(AVG(d.expected_margin_pct), 4)                        AS avg_expected_margin_pct,
    ROUND(cp.avg_competitor_offer_rate_pct, 4)                 AS avg_competitor_offer_rate_pct
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN (
    SELECT customer_id, product_id,
           AVG(competitor_offer_rate_pct) AS avg_competitor_offer_rate_pct
    FROM fab_curated.pricing_negotiation_memory
    GROUP BY customer_id, product_id
) cp ON cp.customer_id = d.customer_id AND cp.product_id = d.product_id
GROUP BY d.customer_id, c.customer_name, c.customer_segment,
         d.product_id, d.product_type, cp.avg_competitor_offer_rate_pct;


-- =============================================================
-- 11. policy_exception_view
--     Explains which policy rules are breached per deal.
-- =============================================================
CREATE OR REPLACE VIEW policy_exception_view AS
SELECT
    d.deal_id,
    d.customer_id,
    c.customer_name,
    c.customer_segment,
    d.product_id,
    d.product_type,
    d.final_approved_price_pct                                 AS approved_price_pct,
    d.expected_margin_pct,
    d.relationship_discount_pct,
    pp.min_margin_pct,
    pp.min_expected_margin_pct,
    pp.max_relationship_discount_pct,
    pp.approval_required_if_discount_above_pct,
    pp.rwa_risk_weight_pct,
    (d.expected_margin_pct < pp.min_expected_margin_pct)                        AS margin_below_min,
    (d.relationship_discount_pct > pp.max_relationship_discount_pct)            AS discount_exceeds_policy,
    (d.final_approved_price_pct < pp.min_margin_pct)                            AS price_below_floor,
    (cp.competitor_rate IS NOT NULL AND cp.competitor_rate < d.final_approved_price_pct) AS competitor_match_requires_approval,
    (pp.rwa_risk_weight_pct >= 100)                                            AS high_rwa_requires_approval,
    (
        (d.expected_margin_pct < pp.min_expected_margin_pct)
        OR (d.relationship_discount_pct > pp.max_relationship_discount_pct)
        OR (d.final_approved_price_pct < pp.min_margin_pct)
        OR (pp.rwa_risk_weight_pct >= 100)
    )                                                                          AS is_exception,
    CONCAT_WS(' | ',
        CASE WHEN d.expected_margin_pct < pp.min_expected_margin_pct THEN 'margin_below_min' END,
        CASE WHEN d.relationship_discount_pct > pp.max_relationship_discount_pct THEN 'discount_exceeds_policy' END,
        CASE WHEN d.final_approved_price_pct < pp.min_margin_pct THEN 'price_below_floor' END,
        CASE WHEN cp.competitor_rate IS NOT NULL AND cp.competitor_rate < d.final_approved_price_pct THEN 'competitor_match_requires_approval' END,
        CASE WHEN pp.rwa_risk_weight_pct >= 100 THEN 'high_rwa_requires_approval' END
    )                                                                          AS exception_reason
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_curated.pricing_policy pp
       ON  pp.customer_segment = c.customer_segment
       AND pp.product_type     = d.product_type
       AND pp.risk_category    = c.risk_category
       AND pp.status           = 'Active'
LEFT JOIN (
    SELECT customer_id, product_id, MIN(competitor_offer_rate_pct) AS competitor_rate
    FROM fab_curated.pricing_negotiation_memory
    GROUP BY customer_id, product_id
) cp ON cp.customer_id = d.customer_id AND cp.product_id = d.product_id;
