"""
02_create_curated_data.py
--------------------------
Reads raw CSVs, applies cleaning rules, and writes curated CSVs to data/curated.

Cleaning steps per file:
  - Cast columns to correct data types
  - Drop duplicate primary-key rows (keep first)
  - Fill or flag missing values
  - Normalise string columns (strip whitespace, title-case where appropriate)
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
RAW_DIR     = os.path.join("data", "raw")
CURATED_DIR = os.path.join("data", "curated")

PRIMARY_KEYS = {
    "customer_master.csv":      ["customer_id"],
    "historical_deals.csv":     ["deal_id"],
    "pricing_policy.csv":       ["policy_id"],
    "product_master.csv":       ["product_id"],
    "treasury_rate_sheet.csv":  ["rate_id"],
}

# Columns to cast to float
FLOAT_COLUMNS = {
    "customer_master.csv": [
        "relationship_tenure_years", "relationship_discount_pct",
        "annual_revenue_aed", "debt_to_equity_ratio", "credit_score",
        "existing_exposure_aed",
    ],
    "historical_deals.csv": [
        "requested_amount", "funding_cost_pct", "standard_margin_pct",
        "risk_premium_pct", "relationship_discount_pct",
        "recommended_price_pct", "final_approved_price_pct",
        "expected_margin_pct",
    ],
    "pricing_policy.csv": [
        "min_margin_pct", "risk_premium_pct", "max_relationship_discount_pct",
        "approval_required_if_discount_above_pct", "min_expected_margin_pct",
        "rwa_risk_weight_pct",
    ],
    "product_master.csv": [
        "standard_margin_pct", "max_margin_pct", "max_discount_allowed_pct",
        "min_ticket_size", "max_ticket_size",
    ],
    "treasury_rate_sheet.csv": ["benchmark_rate_pct", "funding_cost_pct"],
}

# Columns to cast to datetime
DATE_COLUMNS = {
    "historical_deals.csv":    ["deal_date"],
    "treasury_rate_sheet.csv": ["effective_date"],
}

# String columns to strip / normalise
STRING_COLUMNS = {
    "customer_master.csv": [
        "customer_name", "customer_segment", "industry", "region",
        "preferred_currency", "risk_category", "internal_rating",
        "relationship_status",
    ],
    "historical_deals.csv": [
        "product_type", "currency", "tenor", "deal_outcome", "sales_channel",
    ],
    "pricing_policy.csv":  ["customer_segment", "product_type", "risk_category", "status"],
    "product_master.csv":  [
        "product_name", "product_type", "pricing_method", "currency",
        "eligible_segments",
    ],
    "treasury_rate_sheet.csv": ["currency", "benchmark_index", "tenor"],
}


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def drop_duplicates(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    pk_cols = [c for c in PRIMARY_KEYS.get(filename, []) if c in df.columns]
    if not pk_cols:
        return df
    before = len(df)
    df = df.drop_duplicates(subset=pk_cols, keep="first")
    dropped = before - len(df)
    if dropped:
        logger.warning("[%s] Dropped %d duplicate rows on key %s", filename, dropped, pk_cols)
    else:
        logger.info("[%s] No duplicates found", filename)
    return df


def cast_floats(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    for col in FLOAT_COLUMNS.get(filename, []):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    logger.info("[%s] Numeric columns cast to float", filename)
    return df


def cast_dates(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    for col in DATE_COLUMNS.get(filename, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
            bad = df[col].isna().sum()
            if bad:
                logger.warning("[%s] Column '%s': %d values could not be parsed as dates", filename, col, bad)
    if DATE_COLUMNS.get(filename):
        logger.info("[%s] Date columns parsed", filename)
    return df


def normalise_strings(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    for col in STRING_COLUMNS.get(filename, []):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    logger.info("[%s] String columns normalised", filename)
    return df


def fill_missing(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    """
    Strategy:
      - Numeric NaNs → fill with column median (safe for POC).
      - String NaNs  → fill with 'Unknown'.
    Logs counts before filling.
    """
    null_counts = df.isnull().sum()
    null_counts = null_counts[null_counts > 0]

    if null_counts.empty:
        logger.info("[%s] No missing values to fill", filename)
        return df

    logger.info("[%s] Filling missing values:\n%s", filename, null_counts.to_string())

    for col in df.columns:
        if df[col].isnull().any():
            if pd.api.types.is_numeric_dtype(df[col]):
                median_val = df[col].median()
                df[col] = df[col].fillna(median_val)
                logger.info("[%s] '%s' → filled %d NaN(s) with median %.4f", filename, col, null_counts.get(col, 0), median_val)
            else:
                df[col] = df[col].fillna("Unknown")
                logger.info("[%s] '%s' → filled %d NaN(s) with 'Unknown'", filename, col, null_counts.get(col, 0))
    return df


# ---------------------------------------------------------------------------
# Per-file curate
# ---------------------------------------------------------------------------

def curate_file(filename: str) -> bool:
    src = os.path.join(RAW_DIR, filename)
    dst = os.path.join(CURATED_DIR, filename)

    logger.info("=" * 60)
    logger.info("Curating: %s", filename)

    try:
        df = pd.read_csv(src)
        logger.info("[%s] Loaded — %d rows × %d columns", filename, *df.shape)
    except Exception as exc:
        logger.error("[%s] Failed to read: %s", filename, exc)
        return False

    df = drop_duplicates(df, filename)
    df = cast_floats(df, filename)
    df = cast_dates(df, filename)
    df = normalise_strings(df, filename)
    df = fill_missing(df, filename)

    try:
        df.to_csv(dst, index=False)
        logger.info("[%s] Curated file written → %s (%d rows)", filename, dst, len(df))
    except Exception as exc:
        logger.error("[%s] Failed to write curated file: %s", filename, exc)
        return False

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting curated data creation")

    if not os.path.isdir(RAW_DIR):
        logger.error("Raw data directory not found: %s", os.path.abspath(RAW_DIR))
        return

    os.makedirs(CURATED_DIR, exist_ok=True)
    logger.info("Curated output directory: %s", os.path.abspath(CURATED_DIR))

    csv_files = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".csv"))
    if not csv_files:
        logger.warning("No CSV files found in %s", RAW_DIR)
        return

    results = {}
    for filename in csv_files:
        results[filename] = curate_file(filename)

    logger.info("=" * 60)
    logger.info("Curation Summary")
    logger.info("=" * 60)
    for filename, ok in results.items():
        logger.info("  %-40s %s", filename, "OK" if ok else "FAILED")

    passed = sum(results.values())
    logger.info("-" * 60)
    logger.info("Result: %d / %d files curated successfully", passed, len(results))


if __name__ == "__main__":
    main()
