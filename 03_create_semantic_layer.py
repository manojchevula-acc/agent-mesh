"""
03_create_semantic_layer.py
----------------------------
Reads curated CSVs and builds five business-ready semantic datasets:

  1. customer_360.csv              – Full customer profile + aggregated deal KPIs
  2. pricing_recommendation_view.csv – Deal pricing vs. policy benchmarks
  3. margin_analysis.csv           – Deal-level margin decomposition
  4. profitability_summary.csv     – Customer / product profitability roll-up
  5. rwa_impact_view.csv           – RWA-weighted exposure & capital cost view

All outputs are written to data/semantic/.
"""

import os
import logging
import pandas as pd

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CURATED_DIR  = os.path.join("data", "curated")
SEMANTIC_DIR = os.path.join("data", "semantic")


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_curated(filename: str) -> pd.DataFrame:
    """Load a curated CSV and return a DataFrame."""
    path = os.path.join(CURATED_DIR, filename)
    df = pd.read_csv(path, parse_dates=True, low_memory=False)
    logger.info("Loaded curated '%s' — %d rows × %d cols", filename, *df.shape)
    return df


def save_semantic(df: pd.DataFrame, filename: str) -> None:
    """Write a semantic DataFrame to data/semantic/."""
    path = os.path.join(SEMANTIC_DIR, filename)
    df.to_csv(path, index=False)
    logger.info("Saved semantic '%s' — %d rows × %d cols → %s", filename, *df.shape, path)


# ---------------------------------------------------------------------------
# 1. customer_360.csv
# ---------------------------------------------------------------------------

def build_customer_360(customers: pd.DataFrame, deals: pd.DataFrame) -> pd.DataFrame:
    """
    Combines customer master data with aggregated deal metrics to produce
    a single 360-degree view per customer.
    """
    logger.info("Building customer_360 ...")

    # Aggregate deal-level metrics per customer
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

    deal_agg["win_rate_pct"] = (
        deal_agg["won_deals"] / deal_agg["total_deals"] * 100
    ).round(2)

    # Merge with customer master
    c360 = customers.merge(deal_agg, on="customer_id", how="left")

    # Fill customers with no deals
    for col in ["total_deals", "won_deals", "lost_deals"]:
        c360[col] = c360[col].fillna(0).astype(int)
    for col in ["total_deal_volume_aed", "avg_deal_size_aed",
                "avg_expected_margin_pct", "avg_approved_price_pct",
                "avg_relationship_discount_pct", "win_rate_pct"]:
        c360[col] = c360[col].fillna(0.0)

    return c360


# ---------------------------------------------------------------------------
# 2. pricing_recommendation_view.csv
# ---------------------------------------------------------------------------

def build_pricing_recommendation_view(
    deals: pd.DataFrame,
    customers: pd.DataFrame,
    products: pd.DataFrame,
    policies: pd.DataFrame,
) -> pd.DataFrame:
    """
    Enriches each deal with product details and the applicable pricing policy,
    then flags whether the approved price complies with policy floors/ceilings.
    """
    logger.info("Building pricing_recommendation_view ...")

    # Bring in customer segment and risk_category
    cust_slim = customers[["customer_id", "customer_segment", "risk_category", "internal_rating"]]
    df = deals.merge(cust_slim, on="customer_id", how="left")

    # Bring in product details
    prod_slim = products[["product_id", "product_name", "pricing_method",
                           "max_discount_allowed_pct"]]
    df = df.merge(prod_slim, on="product_id", how="left")

    # Attach applicable policy (match on segment + product_type + risk_category)
    policy_slim = policies[
        policies["status"] == "Active"
    ][["customer_segment", "product_type", "risk_category",
       "min_margin_pct", "min_expected_margin_pct",
       "max_relationship_discount_pct", "rwa_risk_weight_pct"]]

    df = df.merge(
        policy_slim,
        on=["customer_segment", "product_type", "risk_category"],
        how="left",
        suffixes=("", "_policy"),
    )

    # Compliance flags
    df["price_below_policy_floor"] = (
        df["final_approved_price_pct"] < df["min_margin_pct"]
    ).fillna(False)

    df["margin_below_min"] = (
        df["expected_margin_pct"] < df["min_expected_margin_pct"]
    ).fillna(False)

    df["discount_exceeds_policy"] = (
        df["relationship_discount_pct"] > df["max_relationship_discount_pct"]
    ).fillna(False)

    df["policy_compliant"] = ~(
        df["price_below_policy_floor"]
        | df["margin_below_min"]
        | df["discount_exceeds_policy"]
    )

    return df


# ---------------------------------------------------------------------------
# 3. margin_analysis.csv
# ---------------------------------------------------------------------------

def build_margin_analysis(
    deals: pd.DataFrame,
    customers: pd.DataFrame,
    treasury: pd.DataFrame,
) -> pd.DataFrame:
    """
    Deal-level margin decomposition:
      net_margin = approved_price - funding_cost - risk_premium - relationship_discount
    Also calculates spread vs. latest treasury benchmark.
    """
    logger.info("Building margin_analysis ...")

    cust_slim = customers[["customer_id", "customer_segment", "region",
                            "risk_category", "internal_rating"]]
    df = deals.merge(cust_slim, on="customer_id", how="left")

    # Latest benchmark rate per currency+tenor
    latest_rates = (
        treasury.sort_values("effective_date", ascending=False)
        .drop_duplicates(subset=["currency", "tenor"])
        [["currency", "tenor", "benchmark_rate_pct", "funding_cost_pct"]]
        .rename(columns={
            "benchmark_rate_pct": "benchmark_rate_pct_treasury",
            "funding_cost_pct":   "funding_cost_pct_treasury",
        })
    )
    df = df.merge(latest_rates, on=["currency", "tenor"], how="left")

    # Margin decomposition
    df["net_margin_pct"] = (
        df["final_approved_price_pct"]
        - df["funding_cost_pct"]
        - df["risk_premium_pct"]
        - df["relationship_discount_pct"]
    ).round(4)

    df["spread_over_benchmark_pct"] = (
        df["final_approved_price_pct"] - df["benchmark_rate_pct_treasury"]
    ).round(4)

    df["margin_vs_recommended_pct"] = (
        df["final_approved_price_pct"] - df["recommended_price_pct"]
    ).round(4)

    return df


# ---------------------------------------------------------------------------
# 4. profitability_summary.csv
# ---------------------------------------------------------------------------

def build_profitability_summary(margin_df: pd.DataFrame) -> pd.DataFrame:
    """
    Rolls up the margin analysis to customer × product_type level to provide
    a profitability summary.
    """
    logger.info("Building profitability_summary ...")

    won_deals = margin_df[margin_df["deal_outcome"] == "Won"].copy()

    summary = (
        won_deals.groupby(
            ["customer_id", "customer_segment", "region",
             "risk_category", "product_type"],
            dropna=False,
        )
        .agg(
            total_won_deals=("deal_id", "count"),
            total_volume_aed=("requested_amount", "sum"),
            avg_approved_price_pct=("final_approved_price_pct", "mean"),
            avg_funding_cost_pct=("funding_cost_pct", "mean"),
            avg_net_margin_pct=("net_margin_pct", "mean"),
            total_expected_margin_aed=(
                "requested_amount",
                lambda s: (
                    s * won_deals.loc[s.index, "net_margin_pct"] / 100
                ).sum()
            ),
        )
        .reset_index()
    )

    summary["avg_approved_price_pct"]  = summary["avg_approved_price_pct"].round(4)
    summary["avg_funding_cost_pct"]    = summary["avg_funding_cost_pct"].round(4)
    summary["avg_net_margin_pct"]      = summary["avg_net_margin_pct"].round(4)
    summary["total_expected_margin_aed"] = summary["total_expected_margin_aed"].round(2)

    summary["profitability_tier"] = pd.cut(
        summary["avg_net_margin_pct"],
        bins=[-999, 0, 0.5, 1.0, 999],
        labels=["Loss-Making", "Low", "Medium", "High"],
    ).astype(str)

    return summary


# ---------------------------------------------------------------------------
# 5. rwa_impact_view.csv
# ---------------------------------------------------------------------------

def build_rwa_impact_view(
    deals: pd.DataFrame,
    customers: pd.DataFrame,
    policies: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calculates RWA-weighted exposure and implied capital cost for each won deal.
    Assumes 8 % minimum capital ratio (Basel III simplified).
    """
    logger.info("Building rwa_impact_view ...")

    CAPITAL_RATIO = 0.08  # 8 % Tier-1 capital requirement

    cust_slim = customers[["customer_id", "customer_segment", "risk_category"]]
    df = deals[deals["deal_outcome"] == "Won"].copy()
    df = df.merge(cust_slim, on="customer_id", how="left")

    policy_slim = (
        policies[policies["status"] == "Active"]
        [["customer_segment", "product_type", "risk_category", "rwa_risk_weight_pct"]]
    )
    df = df.merge(
        policy_slim,
        on=["customer_segment", "product_type", "risk_category"],
        how="left",
    )

    # RWA calculations
    df["rwa_weight_decimal"]  = df["rwa_risk_weight_pct"] / 100
    df["rwa_aed"]             = (df["requested_amount"] * df["rwa_weight_decimal"]).round(2)
    df["capital_required_aed"] = (df["rwa_aed"] * CAPITAL_RATIO).round(2)

    # Return on RWA (simple proxy)
    df["revenue_aed"]        = (df["requested_amount"] * df["final_approved_price_pct"] / 100).round(2)
    df["cost_of_funds_aed"]  = (df["requested_amount"] * df["funding_cost_pct"] / 100).round(2)
    df["net_revenue_aed"]    = (df["revenue_aed"] - df["cost_of_funds_aed"]).round(2)

    df["return_on_rwa_pct"] = (
        df["net_revenue_aed"] / df["rwa_aed"].replace(0, float("nan")) * 100
    ).round(4)

    df["rwa_risk_weight_pct"] = df["rwa_risk_weight_pct"].fillna(0)
    df["is_high_rwa"] = df["rwa_risk_weight_pct"] >= 100

    return df[[
        "deal_id", "deal_date", "customer_id", "customer_segment",
        "product_type", "risk_category", "currency", "tenor",
        "requested_amount", "rwa_risk_weight_pct", "rwa_aed",
        "capital_required_aed", "revenue_aed", "cost_of_funds_aed",
        "net_revenue_aed", "return_on_rwa_pct", "is_high_rwa",
        "final_approved_price_pct", "expected_margin_pct",
    ]]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting semantic layer creation")
    os.makedirs(SEMANTIC_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Load curated data
    # ------------------------------------------------------------------
    try:
        customers = load_curated("customer_master.csv")
        deals     = load_curated("historical_deals.csv")
        policies  = load_curated("pricing_policy.csv")
        products  = load_curated("product_master.csv")
        treasury  = load_curated("treasury_rate_sheet.csv")
    except FileNotFoundError as exc:
        logger.error(
            "Curated file missing: %s\n"
            "Run 02_create_curated_data.py first.",
            exc,
        )
        return

    # Ensure date column is datetime
    if "deal_date" in deals.columns:
        deals["deal_date"] = pd.to_datetime(deals["deal_date"], errors="coerce")
    if "effective_date" in treasury.columns:
        treasury["effective_date"] = pd.to_datetime(treasury["effective_date"], errors="coerce")

    # ------------------------------------------------------------------
    # Build and save each semantic view
    # ------------------------------------------------------------------
    outputs = {
        "customer_360.csv": lambda: build_customer_360(customers, deals),
        "pricing_recommendation_view.csv": lambda: build_pricing_recommendation_view(
            deals, customers, products, policies
        ),
        "margin_analysis.csv": lambda: build_margin_analysis(deals, customers, treasury),
    }

    built = {}
    for filename, builder in outputs.items():
        logger.info("=" * 60)
        try:
            df = builder()
            save_semantic(df, filename)
            built[filename] = df
        except Exception as exc:
            logger.error("Failed to build %s: %s", filename, exc, exc_info=True)

    # profitability_summary depends on margin_analysis
    logger.info("=" * 60)
    try:
        if "margin_analysis.csv" in built:
            prof = build_profitability_summary(built["margin_analysis.csv"])
            save_semantic(prof, "profitability_summary.csv")
        else:
            logger.warning("Skipping profitability_summary — margin_analysis not available")
    except Exception as exc:
        logger.error("Failed to build profitability_summary.csv: %s", exc, exc_info=True)

    # rwa_impact_view
    logger.info("=" * 60)
    try:
        rwa = build_rwa_impact_view(deals, customers, policies)
        save_semantic(rwa, "rwa_impact_view.csv")
    except Exception as exc:
        logger.error("Failed to build rwa_impact_view.csv: %s", exc, exc_info=True)

    logger.info("=" * 60)
    logger.info("Semantic layer creation complete. Files written to: %s",
                os.path.abspath(SEMANTIC_DIR))


if __name__ == "__main__":
    main()
