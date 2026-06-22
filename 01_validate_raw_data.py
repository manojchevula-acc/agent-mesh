"""
01_validate_raw_data.py
-----------------------
Validates all raw CSV files in data/raw for:
  - Expected column presence
  - Missing values
  - Duplicate key records
  - Basic data type checks
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
RAW_DIR = os.path.join("data", "raw")

# Expected columns per file
EXPECTED_COLUMNS = {
    "customer_master.csv": [
        "customer_id", "customer_name", "customer_segment", "industry",
        "region", "preferred_currency", "risk_category", "internal_rating",
        "relationship_tenure_years", "relationship_status",
        "relationship_discount_pct", "annual_revenue_aed",
        "debt_to_equity_ratio", "credit_score", "existing_exposure_aed",
    ],
    "historical_deals.csv": [
        "deal_id", "deal_date", "customer_id", "product_id", "product_type",
        "currency", "tenor", "requested_amount", "funding_cost_pct",
        "standard_margin_pct", "risk_premium_pct", "relationship_discount_pct",
        "recommended_price_pct", "final_approved_price_pct",
        "expected_margin_pct", "deal_outcome", "sales_channel",
    ],
    "pricing_policy.csv": [
        "policy_id", "customer_segment", "product_type", "risk_category",
        "min_margin_pct", "risk_premium_pct", "max_relationship_discount_pct",
        "approval_required_if_discount_above_pct", "min_expected_margin_pct",
        "rwa_risk_weight_pct", "status",
    ],
    "product_master.csv": [
        "product_id", "product_name", "product_type", "pricing_method",
        "currency", "eligible_tenors", "standard_margin_pct", "max_margin_pct",
        "max_discount_allowed_pct", "min_ticket_size", "max_ticket_size",
        "eligible_segments",
    ],
    "treasury_rate_sheet.csv": [
        "rate_id", "currency", "benchmark_index", "tenor", "effective_date",
        "benchmark_rate_pct", "funding_cost_pct",
    ],
}

# Primary key columns per file (used for duplicate checks)
PRIMARY_KEYS = {
    "customer_master.csv":      ["customer_id"],
    "historical_deals.csv":     ["deal_id"],
    "pricing_policy.csv":       ["policy_id"],
    "product_master.csv":       ["product_id"],
    "treasury_rate_sheet.csv":  ["rate_id"],
}

# Numeric columns per file (sanity-checked for non-negative values)
NUMERIC_COLUMNS = {
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

# Date columns per file
DATE_COLUMNS = {
    "historical_deals.csv":    ["deal_date"],
    "treasury_rate_sheet.csv": ["effective_date"],
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def check_columns(df: pd.DataFrame, filename: str) -> bool:
    """Return True if all expected columns are present."""
    expected = set(EXPECTED_COLUMNS.get(filename, []))
    actual = set(df.columns)
    missing = expected - actual
    extra = actual - expected

    if missing:
        logger.error("[%s] Missing columns: %s", filename, sorted(missing))
    if extra:
        logger.warning("[%s] Extra columns (ignored): %s", filename, sorted(extra))
    if not missing:
        logger.info("[%s] Column check PASSED", filename)
        return True
    return False


def check_missing_values(df: pd.DataFrame, filename: str) -> bool:
    """Log missing-value counts and return True when no critical nulls exist."""
    null_counts = df.isnull().sum()
    null_counts = null_counts[null_counts > 0]

    if null_counts.empty:
        logger.info("[%s] Missing-value check PASSED (no nulls)", filename)
        return True

    logger.warning("[%s] Columns with missing values:\n%s", filename, null_counts.to_string())
    return False


def check_duplicates(df: pd.DataFrame, filename: str) -> bool:
    """Return True when no duplicate primary-key records are found."""
    pk_cols = PRIMARY_KEYS.get(filename)
    if not pk_cols:
        logger.info("[%s] No primary key configured — skipping duplicate check", filename)
        return True

    # Only check pk_cols that actually exist in df
    available_pk = [c for c in pk_cols if c in df.columns]
    if not available_pk:
        logger.warning("[%s] Primary key columns not found in data", filename)
        return True

    dup_count = df.duplicated(subset=available_pk).sum()
    if dup_count == 0:
        logger.info("[%s] Duplicate check PASSED (key: %s)", filename, available_pk)
        return True

    logger.error("[%s] Found %d duplicate rows on key %s", filename, dup_count, available_pk)
    return False


def check_numeric_types(df: pd.DataFrame, filename: str) -> bool:
    """Verify numeric columns can be cast to float; log any that cannot."""
    num_cols = NUMERIC_COLUMNS.get(filename, [])
    all_ok = True

    for col in num_cols:
        if col not in df.columns:
            continue
        try:
            pd.to_numeric(df[col], errors="raise")
        except (ValueError, TypeError):
            non_numeric = df[col][pd.to_numeric(df[col], errors="coerce").isna()].unique()
            logger.error(
                "[%s] Column '%s' has non-numeric values: %s",
                filename, col, non_numeric[:5],
            )
            all_ok = False

    if all_ok:
        logger.info("[%s] Numeric type check PASSED", filename)
    return all_ok


def check_date_columns(df: pd.DataFrame, filename: str) -> bool:
    """Verify date columns can be parsed; log any that cannot."""
    date_cols = DATE_COLUMNS.get(filename, [])
    all_ok = True

    for col in date_cols:
        if col not in df.columns:
            continue
        parsed = pd.to_datetime(df[col], errors="coerce")
        bad_count = parsed.isna().sum()
        if bad_count > 0:
            logger.error(
                "[%s] Column '%s' has %d unparseable date values",
                filename, col, bad_count,
            )
            all_ok = False

    if all_ok and date_cols:
        logger.info("[%s] Date type check PASSED", filename)
    return all_ok


# ---------------------------------------------------------------------------
# Per-file validation
# ---------------------------------------------------------------------------

def validate_file(filepath: str) -> bool:
    filename = os.path.basename(filepath)
    logger.info("=" * 60)
    logger.info("Validating: %s", filename)

    try:
        df = pd.read_csv(filepath)
        logger.info("[%s] Loaded — %d rows × %d columns", filename, *df.shape)
    except Exception as exc:
        logger.error("[%s] Failed to read file: %s", filename, exc)
        return False

    results = [
        check_columns(df, filename),
        check_missing_values(df, filename),
        check_duplicates(df, filename),
        check_numeric_types(df, filename),
        check_date_columns(df, filename),
    ]

    overall = all(results)
    status = "PASSED" if overall else "FAILED (see warnings/errors above)"
    logger.info("[%s] Overall validation: %s", filename, status)
    return overall


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting raw data validation")
    logger.info("Raw data directory: %s", os.path.abspath(RAW_DIR))

    if not os.path.isdir(RAW_DIR):
        logger.error("Raw data directory not found: %s", RAW_DIR)
        return

    csv_files = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".csv"))
    if not csv_files:
        logger.warning("No CSV files found in %s", RAW_DIR)
        return

    results = {}
    for filename in csv_files:
        filepath = os.path.join(RAW_DIR, filename)
        results[filename] = validate_file(filepath)

    logger.info("=" * 60)
    logger.info("Validation Summary")
    logger.info("=" * 60)
    for filename, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        logger.info("  %-40s %s", filename, status)

    total = len(results)
    passed = sum(results.values())
    logger.info("-" * 60)
    logger.info("Result: %d / %d files passed validation", passed, total)


if __name__ == "__main__":
    main()
