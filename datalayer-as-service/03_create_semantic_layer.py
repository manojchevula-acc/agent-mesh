"""
03_create_semantic_layer.py
----------------------------
Builds business-ready semantic CSV datasets in data/semantic from the curated
CSVs. Mirrors the MySQL views defined in sql/03_create_semantic_views.sql so the
file-based and database-based semantic layers stay consistent.

Outputs (created where the required curated sources are available):
  Core:
    customer_360.csv, pricing_recommendation_view.csv, margin_analysis.csv,
    profitability_summary.csv, rwa_impact_view.csv
  Enhanced:
    segment_pricing_benchmark.csv, operations_cost_impact.csv,
    new_customer_pricing_view.csv, competitor_price_analysis.csv,
    pricing_trace_view.csv, relationship_discount_view.csv,
    win_loss_insights.csv, policy_exception_view.csv

The script is robust: if an optional source CSV is missing it logs a warning and
skips (or degrades) the affected output instead of crashing.
"""

import os
import logging
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CURATED_DIR = os.path.join("data", "curated")
SEMANTIC_DIR = os.path.join("data", "semantic")


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_curated(name: str) -> pd.DataFrame | None:
    """Load a curated CSV by base name (without extension). Returns None if absent."""
    path = os.path.join(CURATED_DIR, f"{name}.csv")
    if not os.path.isfile(path):
        logger.warning("Curated source missing: %s.csv (dependent outputs will be skipped)", name)
        return None
    df = pd.read_csv(path, low_memory=False)
    logger.info("Loaded curated '%s' — %d rows x %d cols", name, *df.shape)
    return df


def save_semantic(df: pd.DataFrame, filename: str) -> None:
    path = os.path.join(SEMANTIC_DIR, filename)
    df = df.replace({np.nan: None})
    df.to_csv(path, index=False)
    logger.info("Saved semantic '%s' — %d rows x %d cols", filename, *df.shape)


def latest_rates(treasury: pd.DataFrame) -> pd.DataFrame:
    t = treasury.copy()
    t["effective_date"] = pd.to_datetime(t["effective_date"], errors="coerce")
    return (
        t.sort_values("effective_date", ascending=False)
        .drop_duplicates(subset=["currency", "tenor"])
        [["currency", "tenor", "benchmark_rate_pct", "funding_cost_pct"]]
        .rename(columns={
            "benchmark_rate_pct": "benchmark_rate_pct_treasury",
            "funding_cost_pct": "funding_cost_pct_treasury",
        })
    )


# ---------------------------------------------------------------------------
# Helper products
# ---------------------------------------------------------------------------

def build_segment_pricing_benchmark(seg_rules, products) -> pd.DataFrame:
    active = seg_rules[seg_rules.get("rule_status", "Active") == "Active"] if "rule_status" in seg_rules else seg_rules
    agg = (
        active.groupby(["customer_segment", "product_type", "risk_category"], dropna=False)
        .agg(
            base_margin_floor_pct=("base_margin_floor_pct", "mean"),
            target_margin_pct=("target_margin_pct", "mean"),
            risk_premium_pct=("risk_premium_pct", "mean"),
            new_customer_buffer_pct=("new_customer_buffer_pct", "mean"),
            max_relationship_discount_pct=("max_relationship_discount_pct", "mean"),
            min_profitability_margin_pct=("min_profitability_margin_pct", "mean"),
            approval_required_if_discount_above_pct=("approval_required_if_discount_above_pct", "mean"),
            pricing_cushion_for_rating_drop_pct=("pricing_cushion_for_rating_drop_pct", "mean"),
        )
        .reset_index()
    )
    if products is not None:
        prod = products[["product_id", "product_name", "product_type"]]
        agg = agg.merge(prod, on="product_type", how="left")
    return agg.round(4)


def build_operations_cost_impact(ops) -> pd.DataFrame:
    return (
        ops.groupby(["product_id", "customer_segment"], dropna=False)
        .agg(
            product_name=("product_name", "max"),
            product_type=("product_type", "max"),
            ops_cost_margin_pct=("ops_cost_margin_pct", "mean"),
            avg_annual_operating_cost_aed=("annual_operating_cost_aed", "mean"),
            avg_onboarding_cost_aed=("onboarding_cost_aed", "mean"),
            avg_monthly_servicing_cost_aed=("monthly_servicing_cost_aed", "mean"),
            avg_exception_handling_cost_aed=("exception_handling_cost_aed", "mean"),
        )
        .reset_index()
        .round(4)
    )


# ---------------------------------------------------------------------------
# Core products
# ---------------------------------------------------------------------------

def build_customer_360(customers, deals) -> pd.DataFrame:
    deal_agg = (
        deals.groupby("customer_id")
        .agg(
            total_deals=("deal_id", "count"),
            won_deals=("deal_outcome", lambda s: (s == "Won").sum()),
            lost_deals=("deal_outcome", lambda s: (s == "Lost").sum()),
            total_deal_volume_aed=("requested_amount", "sum"),
            avg_deal_size_aed=("requested_amount", "mean"),
            avg_expected_margin_pct=("expected_margin_pct", "mean"),
            avg_approved_price_pct=("final_approved_price_pct", "mean"),
            avg_relationship_discount_pct=("relationship_discount_pct", "mean"),
            last_deal_date=("deal_date", "max"),
        )
        .reset_index()
    )
    deal_agg["win_rate_pct"] = (deal_agg["won_deals"] / deal_agg["total_deals"] * 100).round(2)
    c360 = customers.merge(deal_agg, on="customer_id", how="left")
    for col in ["total_deals", "won_deals", "lost_deals"]:
        c360[col] = c360[col].fillna(0).astype(int)
    for col in ["total_deal_volume_aed", "avg_deal_size_aed", "avg_expected_margin_pct",
                "avg_approved_price_pct", "avg_relationship_discount_pct", "win_rate_pct"]:
        c360[col] = c360[col].fillna(0.0)
    return c360


def build_pricing_recommendation_view(deals, customers, products, policies, spb, ops, treasury) -> pd.DataFrame:
    df = deals.merge(
        customers[["customer_id", "customer_name", "customer_segment", "risk_category",
                   "internal_rating", "relationship_status"]],
        on="customer_id", how="left")
    df = df.merge(products[["product_id", "product_name", "pricing_method"]], on="product_id", how="left")

    pol = policies[policies["status"] == "Active"][[
        "customer_segment", "product_type", "risk_category", "min_margin_pct",
        "min_expected_margin_pct", "max_relationship_discount_pct", "rwa_risk_weight_pct"]]
    df = df.merge(pol, on=["customer_segment", "product_type", "risk_category"], how="left")

    if spb is not None:
        spb_slim = spb[["customer_segment", "product_id", "risk_category", "target_margin_pct"]].drop_duplicates(
            subset=["customer_segment", "product_id", "risk_category"])
        df = df.merge(spb_slim, on=["customer_segment", "product_id", "risk_category"], how="left")
    else:
        df["target_margin_pct"] = np.nan
    df["target_margin_pct"] = df["target_margin_pct"].fillna(df["standard_margin_pct"])

    if ops is not None:
        ops_slim = ops[["product_id", "customer_segment", "ops_cost_margin_pct"]]
        df = df.merge(ops_slim, on=["product_id", "customer_segment"], how="left")
    else:
        df["ops_cost_margin_pct"] = 0.0
    df["ops_cost_margin_pct"] = df["ops_cost_margin_pct"].fillna(0.0)

    lr = latest_rates(treasury)
    df = df.merge(lr[["currency", "tenor", "benchmark_rate_pct_treasury"]], on=["currency", "tenor"], how="left")

    df["system_recommended_price_pct"] = (
        df["funding_cost_pct"] + df["target_margin_pct"] + df["risk_premium_pct"]
        + df["ops_cost_margin_pct"] - df["relationship_discount_pct"]).round(4)
    df["approved_price_pct"] = df["final_approved_price_pct"]

    df["price_below_policy_floor"] = (df["final_approved_price_pct"] < df["min_margin_pct"]).fillna(False)
    df["margin_below_min"] = (df["expected_margin_pct"] < df["min_expected_margin_pct"]).fillna(False)
    df["discount_exceeds_policy"] = (df["relationship_discount_pct"] > df["max_relationship_discount_pct"]).fillna(False)
    df["policy_compliant"] = ~(df["price_below_policy_floor"] | df["margin_below_min"] | df["discount_exceeds_policy"])
    return df


def build_margin_analysis(deals, customers, policies, treasury) -> pd.DataFrame:
    df = deals.merge(
        customers[["customer_id", "customer_name", "customer_segment", "region",
                   "risk_category", "internal_rating"]],
        on="customer_id", how="left")
    pol = policies[policies["status"] == "Active"][[
        "customer_segment", "product_type", "risk_category", "min_expected_margin_pct"]]
    df = df.merge(pol, on=["customer_segment", "product_type", "risk_category"], how="left")
    lr = latest_rates(treasury)
    df = df.merge(lr[["currency", "tenor", "benchmark_rate_pct_treasury"]], on=["currency", "tenor"], how="left")

    df["net_margin_pct"] = (df["final_approved_price_pct"] - df["funding_cost_pct"]
                            - df["risk_premium_pct"] - df["relationship_discount_pct"]).round(4)
    df["spread_over_benchmark_pct"] = (df["final_approved_price_pct"] - df["benchmark_rate_pct_treasury"]).round(4)
    df["margin_vs_recommended_pct"] = (df["final_approved_price_pct"] - df["recommended_price_pct"]).round(4)
    df["margin_below_minimum"] = (df["expected_margin_pct"] < df["min_expected_margin_pct"]).fillna(False)
    return df


def build_profitability_summary(deals, customers, policies, ops) -> pd.DataFrame:
    won = deals[deals["deal_outcome"] == "Won"].merge(
        customers[["customer_id", "customer_segment", "region", "risk_category"]],
        on="customer_id", how="left")
    pol = policies[policies["status"] == "Active"][[
        "customer_segment", "product_type", "risk_category", "rwa_risk_weight_pct"]]
    won = won.merge(pol, on=["customer_segment", "product_type", "risk_category"], how="left")
    if ops is not None:
        won = won.merge(ops[["product_id", "customer_segment", "ops_cost_margin_pct"]],
                        on=["product_id", "customer_segment"], how="left")
    else:
        won["ops_cost_margin_pct"] = 0.0
    won["ops_cost_margin_pct"] = won["ops_cost_margin_pct"].fillna(0.0)
    won["rwa_risk_weight_pct"] = won["rwa_risk_weight_pct"].fillna(0.0)

    won["revenue_aed"] = won["requested_amount"] * won["final_approved_price_pct"] / 100
    won["funding_cost_aed"] = won["requested_amount"] * won["funding_cost_pct"] / 100
    won["operating_cost_aed"] = won["requested_amount"] * won["ops_cost_margin_pct"] / 100
    won["capital_cost_aed"] = won["requested_amount"] * won["rwa_risk_weight_pct"] / 100 * 0.08 * 0.10
    won["net_profit_row_aed"] = won["requested_amount"] * (
        won["final_approved_price_pct"] - won["funding_cost_pct"] - won["risk_premium_pct"]
        - won["relationship_discount_pct"] - won["ops_cost_margin_pct"]) / 100
    won["net_margin_row_pct"] = (won["final_approved_price_pct"] - won["funding_cost_pct"]
                                 - won["risk_premium_pct"] - won["relationship_discount_pct"])

    summary = (
        won.groupby(["customer_id", "customer_segment", "region", "risk_category", "product_type"], dropna=False)
        .agg(
            total_won_deals=("deal_id", "count"),
            total_volume_aed=("requested_amount", "sum"),
            revenue_aed=("revenue_aed", "sum"),
            funding_cost_aed=("funding_cost_aed", "sum"),
            operating_cost_aed=("operating_cost_aed", "sum"),
            capital_cost_aed=("capital_cost_aed", "sum"),
            net_profit_aed=("net_profit_row_aed", "sum"),
            avg_net_margin_pct=("net_margin_row_pct", "mean"),
        )
        .reset_index()
    )
    for c in ["revenue_aed", "funding_cost_aed", "operating_cost_aed", "capital_cost_aed", "net_profit_aed"]:
        summary[c] = summary[c].round(2)
    summary["avg_net_margin_pct"] = summary["avg_net_margin_pct"].round(4)
    summary["profitability_tier"] = pd.cut(
        summary["avg_net_margin_pct"], bins=[-9999, 0, 0.5, 1.0, 9999],
        labels=["Loss-Making", "Low", "Medium", "High"]).astype(str)
    return summary


def build_rwa_impact_view(deals, customers, policies) -> pd.DataFrame:
    df = deals[deals["deal_outcome"] == "Won"].merge(
        customers[["customer_id", "customer_segment", "risk_category"]], on="customer_id", how="left")
    pol = policies[policies["status"] == "Active"][[
        "customer_segment", "product_type", "risk_category", "rwa_risk_weight_pct"]]
    df = df.merge(pol, on=["customer_segment", "product_type", "risk_category"], how="left")
    df["rwa_risk_weight_pct"] = df["rwa_risk_weight_pct"].fillna(0.0)

    df["exposure_aed"] = df["requested_amount"]
    df["risk_weight_pct"] = df["rwa_risk_weight_pct"]
    df["rwa_aed"] = (df["requested_amount"] * df["rwa_risk_weight_pct"] / 100).round(2)
    df["capital_required_aed"] = (df["rwa_aed"] * 0.08).round(2)
    df["revenue_aed"] = (df["requested_amount"] * df["final_approved_price_pct"] / 100).round(2)
    df["cost_of_funds_aed"] = (df["requested_amount"] * df["funding_cost_pct"] / 100).round(2)
    df["net_revenue_aed"] = (df["revenue_aed"] - df["cost_of_funds_aed"]).round(2)
    df["return_on_rwa_pct"] = (df["net_revenue_aed"] / df["rwa_aed"].replace(0, np.nan) * 100).round(4)
    df["is_high_rwa"] = df["rwa_risk_weight_pct"] >= 100
    return df[[
        "deal_id", "deal_date", "customer_id", "customer_segment", "product_type",
        "risk_category", "currency", "tenor", "exposure_aed", "risk_weight_pct", "rwa_aed",
        "capital_required_aed", "revenue_aed", "cost_of_funds_aed", "net_revenue_aed",
        "return_on_rwa_pct", "is_high_rwa", "final_approved_price_pct", "expected_margin_pct"]]


# ---------------------------------------------------------------------------
# Enhanced products
# ---------------------------------------------------------------------------

def build_new_customer_pricing_view(prospects, products, spb, ops, treasury) -> pd.DataFrame:
    df = prospects.copy()
    df = df.merge(products[["product_id", "product_name", "standard_margin_pct"]].rename(
        columns={"product_id": "requested_product_id"}), on="requested_product_id", how="left")
    if spb is not None:
        spb_slim = spb[["customer_segment", "product_id", "risk_category", "target_margin_pct",
                        "risk_premium_pct", "new_customer_buffer_pct", "base_margin_floor_pct",
                        "min_profitability_margin_pct"]].drop_duplicates(
            subset=["customer_segment", "product_id", "risk_category"]).rename(
            columns={"product_id": "requested_product_id"})
        df = df.merge(spb_slim, on=["customer_segment", "requested_product_id", "risk_category"], how="left")
    for c in ["target_margin_pct", "risk_premium_pct", "new_customer_buffer_pct",
              "base_margin_floor_pct", "min_profitability_margin_pct"]:
        if c not in df:
            df[c] = np.nan
    df["target_margin_pct"] = df["target_margin_pct"].fillna(df["standard_margin_pct"])
    for c in ["risk_premium_pct", "new_customer_buffer_pct", "base_margin_floor_pct", "min_profitability_margin_pct"]:
        df[c] = df[c].fillna(0.0)

    if ops is not None:
        df = df.merge(ops[["product_id", "customer_segment", "ops_cost_margin_pct"]].rename(
            columns={"product_id": "requested_product_id"}),
            on=["requested_product_id", "customer_segment"], how="left")
    else:
        df["ops_cost_margin_pct"] = 0.0
    df["ops_cost_margin_pct"] = df["ops_cost_margin_pct"].fillna(0.0)

    lr = latest_rates(treasury)
    df = df.merge(lr.rename(columns={"currency": "preferred_currency", "tenor": "requested_tenor"}),
                  on=["preferred_currency", "requested_tenor"], how="left")
    df["funding_cost_pct_treasury"] = df["funding_cost_pct_treasury"].fillna(0.0)

    df["recommended_price_pct"] = (
        df["funding_cost_pct_treasury"] + df["target_margin_pct"] + df["risk_premium_pct"]
        + df["new_customer_buffer_pct"] + df["ops_cost_margin_pct"]).round(4)

    out = df.rename(columns={
        "requested_product_id": "product_id", "requested_product_type": "product_type",
        "preferred_currency": "currency", "requested_tenor": "tenor",
        "requested_amount_aed": "requested_amount",
        "funding_cost_pct_treasury": "funding_cost_pct"})
    keep = ["customer_id", "customer_name", "customer_segment", "industry", "region",
            "risk_category", "internal_rating", "relationship_status", "no_previous_relationship_flag",
            "product_id", "product_name", "product_type", "currency", "tenor", "requested_amount",
            "benchmark_rate_pct_treasury", "funding_cost_pct", "target_margin_pct", "risk_premium_pct",
            "new_customer_buffer_pct", "ops_cost_margin_pct", "recommended_price_pct",
            "base_margin_floor_pct", "min_profitability_margin_pct"]
    return out[[c for c in keep if c in out.columns]]


def build_competitor_price_analysis(memory) -> pd.DataFrame:
    m = memory.copy()
    m["fab_price_pct"] = m["initial_offered_price_pct"].round(4)
    m["competitor_price_pct"] = m["competitor_offer_rate_pct"].round(4)
    m["profitability_floor_pct"] = m["profitability_floor_price_pct"].round(4)
    m["max_reducible_pct"] = m["max_reducible_without_profitability_impact_pct"].round(4)
    m["competitor_gap_bps"] = ((m["fab_price_pct"] - m["competitor_price_pct"]) * 100).round(1)

    def action(row):
        if row["competitor_price_pct"] < row["profitability_floor_pct"]:
            return "REJECT"
        if row["competitor_gap_bps"] <= 20:
            return "MATCH"
        if row["competitor_gap_bps"] <= 60:
            return "COUNTER"
        return "ESCALATE"

    def reason(row):
        if row["competitor_price_pct"] < row["profitability_floor_pct"]:
            return "Competitor price is below FAB profitability floor - matching would be loss-making."
        if row["competitor_gap_bps"] <= 20:
            return "Gap is small and stays above floor - matching is safe."
        if row["competitor_gap_bps"] <= 60:
            return "Moderate gap - counter-offer between FAB price and competitor price."
        return "Large gap but above floor - escalate for pricing approval."

    m["suggested_action"] = m.apply(action, axis=1)
    m["action_reason"] = m.apply(reason, axis=1)
    m = m.rename(columns={"memory_interaction_id": "deal_id"})
    keep = ["deal_id", "conversation_id", "customer_id", "customer_name", "product_id",
            "product_name", "product_type", "competitor_name", "fab_price_pct",
            "competitor_price_pct", "profitability_floor_pct", "max_reducible_pct",
            "competitor_gap_bps", "suggested_action", "action_reason",
            "last_agent_recommendation", "rm_note"]
    return m[[c for c in keep if c in m.columns]]


def build_pricing_trace_view(deals, customers, spb, ops) -> pd.DataFrame:
    df = deals.merge(customers[["customer_id", "customer_name", "customer_segment", "risk_category"]],
                     on="customer_id", how="left")
    if spb is not None:
        spb_slim = spb[["customer_segment", "product_id", "risk_category", "target_margin_pct"]].drop_duplicates(
            subset=["customer_segment", "product_id", "risk_category"])
        df = df.merge(spb_slim, on=["customer_segment", "product_id", "risk_category"], how="left")
    else:
        df["target_margin_pct"] = np.nan
    df["target_margin_pct"] = df["target_margin_pct"].fillna(df["standard_margin_pct"])
    if ops is not None:
        df = df.merge(ops[["product_id", "customer_segment", "ops_cost_margin_pct"]],
                      on=["product_id", "customer_segment"], how="left")
    else:
        df["ops_cost_margin_pct"] = 0.0
    df["ops_cost_margin_pct"] = df["ops_cost_margin_pct"].fillna(0.0)

    df["treasury_rate_component"] = df["funding_cost_pct"].round(4)
    df["target_margin_component"] = df["target_margin_pct"].round(4)
    df["risk_premium_component"] = df["risk_premium_pct"].round(4)
    df["operations_cost_component"] = df["ops_cost_margin_pct"].round(4)
    df["relationship_discount_component"] = df["relationship_discount_pct"].round(4)
    df["final_recommended_price"] = (
        df["treasury_rate_component"] + df["target_margin_component"] + df["risk_premium_component"]
        + df["operations_cost_component"] - df["relationship_discount_component"]).round(4)
    df["approved_price_pct"] = df["final_approved_price_pct"].round(4)
    df["explanation_text"] = df.apply(lambda r: (
        f"Treasury/funding {r['treasury_rate_component']:.2f}% + target margin "
        f"{r['target_margin_component']:.2f}% + risk premium {r['risk_premium_component']:.2f}% + "
        f"ops cost {r['operations_cost_component']:.2f}% - relationship discount "
        f"{r['relationship_discount_component']:.2f}% = recommended {r['final_recommended_price']:.2f}%"), axis=1)
    keep = ["deal_id", "customer_id", "customer_name", "customer_segment", "risk_category",
            "product_id", "product_type", "currency", "tenor", "treasury_rate_component",
            "target_margin_component", "risk_premium_component", "operations_cost_component",
            "relationship_discount_component", "final_recommended_price", "approved_price_pct",
            "explanation_text"]
    return df[[c for c in keep if c in df.columns]]


def build_relationship_discount_view(customers, policies) -> pd.DataFrame:
    pol = (policies[policies["status"] == "Active"]
           .groupby(["customer_segment", "risk_category"], dropna=False)
           .agg(max_relationship_discount_pct=("max_relationship_discount_pct", "max"),
                approval_required_if_discount_above_pct=("approval_required_if_discount_above_pct", "min"))
           .reset_index())
    df = customers.merge(pol, on=["customer_segment", "risk_category"], how="left")
    df["policy_max_relationship_discount_pct"] = df["max_relationship_discount_pct"]
    df["discount_eligible"] = df["relationship_discount_pct"] <= df["max_relationship_discount_pct"]
    df["approval_required"] = df["relationship_discount_pct"] > df["approval_required_if_discount_above_pct"]

    def reason(r):
        if pd.notna(r["max_relationship_discount_pct"]) and r["relationship_discount_pct"] > r["max_relationship_discount_pct"]:
            return "Discount exceeds policy cap - not eligible without exception."
        if pd.notna(r["approval_required_if_discount_above_pct"]) and r["relationship_discount_pct"] > r["approval_required_if_discount_above_pct"]:
            return "Discount within cap but above approval threshold - approval required."
        return "Discount within policy - auto-eligible."

    df["eligibility_reason"] = df.apply(reason, axis=1)
    keep = ["customer_id", "customer_name", "customer_segment", "risk_category", "relationship_status",
            "relationship_tenure_years", "relationship_discount_pct",
            "policy_max_relationship_discount_pct", "approval_required_if_discount_above_pct",
            "discount_eligible", "approval_required", "eligibility_reason"]
    return df[[c for c in keep if c in df.columns]]


def build_win_loss_insights(deals, customers, memory) -> pd.DataFrame:
    df = deals.merge(customers[["customer_id", "customer_name", "customer_segment"]],
                     on="customer_id", how="left")
    grp = (df.groupby(["customer_id", "customer_name", "customer_segment", "product_id", "product_type"], dropna=False)
           .agg(total_deals=("deal_id", "count"),
                won_deals=("deal_outcome", lambda s: (s == "Won").sum()),
                lost_deals=("deal_outcome", lambda s: (s == "Lost").sum()),
                avg_approved_price_pct=("final_approved_price_pct", "mean"),
                avg_recommended_price_pct=("recommended_price_pct", "mean"),
                avg_expected_margin_pct=("expected_margin_pct", "mean"))
           .reset_index())
    grp["win_rate_pct"] = (grp["won_deals"] / grp["total_deals"] * 100).round(2)
    grp["avg_price_gap_pct"] = (grp["avg_approved_price_pct"] - grp["avg_recommended_price_pct"]).round(4)
    for c in ["avg_approved_price_pct", "avg_recommended_price_pct", "avg_expected_margin_pct"]:
        grp[c] = grp[c].round(4)
    if memory is not None:
        comp = (memory.groupby(["customer_id", "product_id"], dropna=False)
                .agg(avg_competitor_offer_rate_pct=("competitor_offer_rate_pct", "mean")).reset_index())
        grp = grp.merge(comp, on=["customer_id", "product_id"], how="left")
        grp["avg_competitor_offer_rate_pct"] = grp["avg_competitor_offer_rate_pct"].round(4)
    else:
        grp["avg_competitor_offer_rate_pct"] = None
    return grp


def build_policy_exception_view(deals, customers, policies, memory) -> pd.DataFrame:
    df = deals.merge(customers[["customer_id", "customer_name", "customer_segment", "risk_category"]],
                     on="customer_id", how="left")
    pol = policies[policies["status"] == "Active"][[
        "customer_segment", "product_type", "risk_category", "min_margin_pct",
        "min_expected_margin_pct", "max_relationship_discount_pct",
        "approval_required_if_discount_above_pct", "rwa_risk_weight_pct"]]
    df = df.merge(pol, on=["customer_segment", "product_type", "risk_category"], how="left")

    if memory is not None:
        comp = (memory.groupby(["customer_id", "product_id"], dropna=False)
                .agg(competitor_rate=("competitor_offer_rate_pct", "min")).reset_index())
        df = df.merge(comp, on=["customer_id", "product_id"], how="left")
    else:
        df["competitor_rate"] = np.nan

    df["approved_price_pct"] = df["final_approved_price_pct"]
    df["margin_below_min"] = (df["expected_margin_pct"] < df["min_expected_margin_pct"]).fillna(False)
    df["discount_exceeds_policy"] = (df["relationship_discount_pct"] > df["max_relationship_discount_pct"]).fillna(False)
    df["price_below_floor"] = (df["final_approved_price_pct"] < df["min_margin_pct"]).fillna(False)
    df["competitor_match_requires_approval"] = (
        df["competitor_rate"].notna() & (df["competitor_rate"] < df["final_approved_price_pct"]))
    df["high_rwa_requires_approval"] = (df["rwa_risk_weight_pct"] >= 100).fillna(False)
    df["is_exception"] = (df["margin_below_min"] | df["discount_exceeds_policy"] | df["price_below_floor"]
                          | df["high_rwa_requires_approval"])

    def reasons(r):
        out = []
        if r["margin_below_min"]:
            out.append("margin_below_min")
        if r["discount_exceeds_policy"]:
            out.append("discount_exceeds_policy")
        if r["price_below_floor"]:
            out.append("price_below_floor")
        if r["competitor_match_requires_approval"]:
            out.append("competitor_match_requires_approval")
        if r["high_rwa_requires_approval"]:
            out.append("high_rwa_requires_approval")
        return "; ".join(out)

    df["exception_reason"] = df.apply(reasons, axis=1)
    keep = ["deal_id", "customer_id", "customer_name", "customer_segment", "product_id", "product_type",
            "approved_price_pct", "expected_margin_pct", "relationship_discount_pct", "min_margin_pct",
            "min_expected_margin_pct", "max_relationship_discount_pct",
            "approval_required_if_discount_above_pct", "rwa_risk_weight_pct", "margin_below_min",
            "discount_exceeds_policy", "price_below_floor", "competitor_match_requires_approval",
            "high_rwa_requires_approval", "is_exception", "exception_reason"]
    return df[[c for c in keep if c in df.columns]]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting semantic layer creation")
    os.makedirs(SEMANTIC_DIR, exist_ok=True)

    # Load curated sources (None when absent)
    customers = load_curated("customer_master")
    deals = load_curated("historical_deals")
    policies = load_curated("pricing_policy")
    products = load_curated("product_master")
    treasury = load_curated("treasury_rate_sheet")
    seg_rules = load_curated("customer_segment_pricing_rules")
    ops_raw = load_curated("operations_cost")
    prospects = load_curated("prospect_customer_profile")
    memory = load_curated("pricing_negotiation_memory")

    if any(x is None for x in [customers, deals, policies, products, treasury]):
        logger.error("One or more CORE curated files are missing. Run 02_create_curated_data.py first.")
        return

    if "deal_date" in deals:
        deals["deal_date"] = pd.to_datetime(deals["deal_date"], errors="coerce")

    # Helper products
    spb = build_segment_pricing_benchmark(seg_rules, products) if seg_rules is not None else None
    ops = build_operations_cost_impact(ops_raw) if ops_raw is not None else None

    def safe(name, builder):
        logger.info("=" * 60)
        try:
            save_semantic(builder(), name)
        except Exception as exc:
            logger.error("Failed to build %s: %s", name, exc, exc_info=True)

    if spb is not None:
        safe("segment_pricing_benchmark.csv", lambda: spb)
    else:
        logger.warning("Skipping segment_pricing_benchmark (customer_segment_pricing_rules missing)")
    if ops is not None:
        safe("operations_cost_impact.csv", lambda: ops)
    else:
        logger.warning("Skipping operations_cost_impact (operations_cost missing)")

    safe("customer_360.csv", lambda: build_customer_360(customers, deals))
    safe("pricing_recommendation_view.csv",
         lambda: build_pricing_recommendation_view(deals, customers, products, policies, spb, ops, treasury))
    safe("margin_analysis.csv", lambda: build_margin_analysis(deals, customers, policies, treasury))
    safe("profitability_summary.csv", lambda: build_profitability_summary(deals, customers, policies, ops))
    safe("rwa_impact_view.csv", lambda: build_rwa_impact_view(deals, customers, policies))
    safe("pricing_trace_view.csv", lambda: build_pricing_trace_view(deals, customers, spb, ops))
    safe("relationship_discount_view.csv", lambda: build_relationship_discount_view(customers, policies))
    safe("win_loss_insights.csv", lambda: build_win_loss_insights(deals, customers, memory))
    safe("policy_exception_view.csv", lambda: build_policy_exception_view(deals, customers, policies, memory))

    if prospects is not None:
        safe("new_customer_pricing_view.csv",
             lambda: build_new_customer_pricing_view(prospects, products, spb, ops, treasury))
    else:
        logger.warning("Skipping new_customer_pricing_view (prospect_customer_profile missing)")

    if memory is not None:
        safe("competitor_price_analysis.csv", lambda: build_competitor_price_analysis(memory))
    else:
        logger.warning("Skipping competitor_price_analysis (pricing_negotiation_memory missing)")

    logger.info("=" * 60)
    logger.info("Semantic layer creation complete -> %s", os.path.abspath(SEMANTIC_DIR))


if __name__ == "__main__":
    main()
