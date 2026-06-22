-- =============================================================
-- 03_create_semantic_views.sql
-- Creates five business-ready views in fab_semantic schema.
-- All views SELECT only from fab_curated tables.
-- Run after 03_load_curated_to_mysql.py has loaded all tables.
-- =============================================================

USE fab_semantic;

-- -------------------------------------------------------------
-- 1. customer_360
--    Full customer profile enriched with aggregated deal KPIs.
-- -------------------------------------------------------------
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
    -- Deal aggregates
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


-- -------------------------------------------------------------
-- 2. pricing_recommendation_view
--    Each deal annotated with product info, applicable policy,
--    and three compliance flags.
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW pricing_recommendation_view AS
SELECT
    d.deal_id,
    d.deal_date,
    d.customer_id,
    c.customer_name,
    c.customer_segment,
    c.risk_category,
    c.internal_rating,
    d.product_id,
    pm.product_name,
    d.product_type,
    pm.pricing_method,
    pm.max_discount_allowed_pct,
    d.currency,
    d.tenor,
    d.requested_amount,
    d.funding_cost_pct,
    d.standard_margin_pct,
    d.risk_premium_pct,
    d.relationship_discount_pct,
    d.recommended_price_pct,
    d.final_approved_price_pct,
    d.expected_margin_pct,
    d.deal_outcome,
    d.sales_channel,
    -- Policy benchmarks
    pp.min_margin_pct                           AS policy_min_margin_pct,
    pp.min_expected_margin_pct                  AS policy_min_expected_margin_pct,
    pp.max_relationship_discount_pct            AS policy_max_discount_pct,
    pp.rwa_risk_weight_pct                      AS policy_rwa_risk_weight_pct,
    -- Compliance flags
    (d.final_approved_price_pct < pp.min_margin_pct)          AS price_below_policy_floor,
    (d.expected_margin_pct < pp.min_expected_margin_pct)      AS margin_below_min,
    (d.relationship_discount_pct > pp.max_relationship_discount_pct) AS discount_exceeds_policy,
    NOT (
        (d.final_approved_price_pct < pp.min_margin_pct)
        OR (d.expected_margin_pct < pp.min_expected_margin_pct)
        OR (d.relationship_discount_pct > pp.max_relationship_discount_pct)
    )                                                          AS policy_compliant
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN fab_curated.product_master pm
       ON d.product_id = pm.product_id
LEFT JOIN fab_curated.pricing_policy pp
       ON  pp.customer_segment = c.customer_segment
       AND pp.product_type     = d.product_type
       AND pp.risk_category    = c.risk_category
       AND pp.status           = 'Active';


-- -------------------------------------------------------------
-- 3. margin_analysis
--    Deal-level margin decomposition with treasury benchmark
--    comparison.
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW margin_analysis AS
WITH latest_rates AS (
    -- Pick the most recent rate for each currency + tenor pair
    SELECT
        r.currency,
        r.tenor,
        r.benchmark_rate_pct  AS benchmark_rate_pct_treasury,
        r.funding_cost_pct    AS funding_cost_pct_treasury
    FROM fab_curated.treasury_rate_sheet r
    INNER JOIN (
        SELECT currency, tenor, MAX(effective_date) AS max_date
        FROM fab_curated.treasury_rate_sheet
        GROUP BY currency, tenor
    ) latest
      ON r.currency       = latest.currency
     AND r.tenor          = latest.tenor
     AND r.effective_date = latest.max_date
)
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
    lr.funding_cost_pct_treasury,
    -- Margin decomposition
    ROUND(
        d.final_approved_price_pct
        - d.funding_cost_pct
        - d.risk_premium_pct
        - d.relationship_discount_pct,
        4
    )                                                     AS net_margin_pct,
    ROUND(d.final_approved_price_pct - lr.benchmark_rate_pct_treasury, 4)
                                                          AS spread_over_benchmark_pct,
    ROUND(d.final_approved_price_pct - d.recommended_price_pct, 4)
                                                          AS margin_vs_recommended_pct
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
LEFT JOIN latest_rates lr
       ON d.currency = lr.currency
      AND d.tenor    = lr.tenor;


-- -------------------------------------------------------------
-- 4. profitability_summary
--    Won-deals roll-up by customer x product_type with
--    profitability tier.
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW profitability_summary AS
SELECT
    d.customer_id,
    c.customer_segment,
    c.region,
    c.risk_category,
    d.product_type,
    COUNT(*)                                                      AS total_won_deals,
    SUM(d.requested_amount)                                       AS total_volume_aed,
    ROUND(AVG(d.final_approved_price_pct), 4)                    AS avg_approved_price_pct,
    ROUND(AVG(d.funding_cost_pct), 4)                            AS avg_funding_cost_pct,
    -- Net margin = approved price - funding cost - risk premium - discount
    ROUND(AVG(
        d.final_approved_price_pct
        - d.funding_cost_pct
        - d.risk_premium_pct
        - d.relationship_discount_pct
    ), 4)                                                         AS avg_net_margin_pct,
    ROUND(SUM(
        d.requested_amount
        * (d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct)
        / 100
    ), 2)                                                         AS total_expected_margin_aed,
    -- Profitability tier
    CASE
        WHEN AVG(d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct) < 0    THEN 'Loss-Making'
        WHEN AVG(d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct) < 0.5  THEN 'Low'
        WHEN AVG(d.final_approved_price_pct - d.funding_cost_pct - d.risk_premium_pct - d.relationship_discount_pct) < 1.0  THEN 'Medium'
        ELSE 'High'
    END                                                           AS profitability_tier
FROM fab_curated.historical_deals d
LEFT JOIN fab_curated.customer_master c
       ON d.customer_id = c.customer_id
WHERE d.deal_outcome = 'Won'
GROUP BY
    d.customer_id,
    c.customer_segment,
    c.region,
    c.risk_category,
    d.product_type;


-- -------------------------------------------------------------
-- 5. rwa_impact_view
--    RWA-weighted exposure and capital cost for each won deal.
--    Assumes 8% Basel III minimum capital ratio.
-- -------------------------------------------------------------
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
    d.requested_amount,
    pp.rwa_risk_weight_pct,
    -- RWA calculations
    ROUND(d.requested_amount * pp.rwa_risk_weight_pct / 100, 2)  AS rwa_aed,
    ROUND(d.requested_amount * pp.rwa_risk_weight_pct / 100 * 0.08, 2)
                                                                  AS capital_required_aed,
    -- Revenue & cost
    ROUND(d.requested_amount * d.final_approved_price_pct / 100, 2) AS revenue_aed,
    ROUND(d.requested_amount * d.funding_cost_pct / 100, 2)      AS cost_of_funds_aed,
    ROUND(
        d.requested_amount * d.final_approved_price_pct / 100
        - d.requested_amount * d.funding_cost_pct / 100,
        2
    )                                                             AS net_revenue_aed,
    -- Return on RWA
    ROUND(
        (d.requested_amount * d.final_approved_price_pct / 100
         - d.requested_amount * d.funding_cost_pct / 100)
        / NULLIF(d.requested_amount * pp.rwa_risk_weight_pct / 100, 0)
        * 100,
        4
    )                                                             AS return_on_rwa_pct,
    (pp.rwa_risk_weight_pct >= 100)                              AS is_high_rwa,
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
