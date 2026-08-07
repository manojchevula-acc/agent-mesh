"""
01_validate_raw_data.py
-----------------------
Dynamically validates EVERY CSV file found in data/raw.

For each file it checks:
  - empty file
  - inconsistent / duplicate column names
  - missing values (per column)
  - fully duplicate rows
  - duplicate primary keys (when an *_id column is detected)
  - date column validity (inferred from column name)
  - numeric column validity (inferred from column name)

The script is non-fatal by design: it logs clear WARNING / ERROR lines but only
reports a file as FAILED when it is empty or unreadable. A validation summary is
printed to the console and written to logs/validation_summary.txt.
"""

import os
import re
import logging
import pandas as pd

# ---------------------------------------------------------------------------
# Logging setup (console + logs/ file)
# ---------------------------------------------------------------------------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "validation_summary.txt"), mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_DIR = os.path.join("data", "raw")

# Substrings used to *infer* numeric columns from their name.
NUMERIC_HINTS = (
    "amount", "rate", "margin", "cost", "revenue", "rwa", "capital",
    "score", "weight", "tenor", "price", "discount", "ratio", "aed",
    "pct", "fee", "premium", "buffer", "exposure", "cushion",
)

# Substrings used to *infer* date columns from their name.
DATE_HINTS = ("date", "timestamp", "_from", "_to", "effective")

# Columns whose name matches a numeric hint but must stay textual.
NUMERIC_EXCLUDE = ("tenor",)  # e.g. '1M', '60M' are not numbers

# Threshold: a column is treated as numeric/date only if this fraction of
# non-null values parse successfully (protects mixed columns like 'tenor').
PARSE_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalise_columns(cols) -> list[str]:
    """Lower-case, trim and snake_case a list of column names."""
    out = []
    for c in cols:
        c = str(c).strip().lower()
        c = re.sub(r"[^\w]+", "_", c)   # non-word -> underscore
        c = re.sub(r"_+", "_", c).strip("_")
        out.append(c)
    return out


def infer_numeric_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if any(x in c for x in NUMERIC_EXCLUDE):
            continue
        if any(h in c for h in NUMERIC_HINTS):
            parsed = pd.to_numeric(df[c], errors="coerce")
            non_null = df[c].notna().sum()
            if non_null and parsed.notna().sum() / non_null >= PARSE_THRESHOLD:
                cols.append(c)
    return cols


def infer_date_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if any(h in c for h in DATE_HINTS):
            parsed = pd.to_datetime(df[c], errors="coerce")
            non_null = df[c].notna().sum()
            if non_null and parsed.notna().sum() / non_null >= PARSE_THRESHOLD:
                cols.append(c)
    return cols


def detect_primary_key(df: pd.DataFrame) -> str | None:
    """Best-effort primary-key detection: first *_id column that is unique."""
    id_cols = [c for c in df.columns if c.endswith("_id")]
    for c in id_cols:
        if df[c].is_unique:
            return c
    return id_cols[0] if id_cols else None


# ---------------------------------------------------------------------------
# Per-file validation
# ---------------------------------------------------------------------------

def validate_file(filepath: str) -> dict:
    filename = os.path.basename(filepath)
    logger.info("=" * 60)
    logger.info("Validating: %s", filename)

    summary = {"file": filename, "rows": 0, "cols": 0, "status": "PASSED", "warnings": 0}

    try:
        df = pd.read_csv(filepath)
    except Exception as exc:
        logger.error("[%s] Failed to read file: %s", filename, exc)
        summary["status"] = "FAILED"
        return summary

    if df.empty:
        logger.error("[%s] File is EMPTY (no data rows)", filename)
        summary["status"] = "FAILED"
        return summary

    # Normalise column names
    original_cols = list(df.columns)
    df.columns = normalise_columns(df.columns)
    if original_cols != list(df.columns):
        logger.info("[%s] Column names normalised to snake_case", filename)

    # Duplicate column names
    dup_cols = [c for c in set(df.columns) if list(df.columns).count(c) > 1]
    if dup_cols:
        logger.warning("[%s] Duplicate column names detected: %s", filename, dup_cols)
        summary["warnings"] += 1

    summary["rows"], summary["cols"] = df.shape
    logger.info("[%s] Loaded — %d rows x %d columns", filename, *df.shape)

    # Missing values
    nulls = df.isnull().sum()
    nulls = nulls[nulls > 0]
    if not nulls.empty:
        logger.warning("[%s] Missing values:\n%s", filename, nulls.to_string())
        summary["warnings"] += 1
    else:
        logger.info("[%s] No missing values", filename)

    # Fully duplicate rows
    dup_rows = df.duplicated().sum()
    if dup_rows:
        logger.warning("[%s] %d fully-duplicate row(s)", filename, dup_rows)
        summary["warnings"] += 1

    # Duplicate primary keys
    pk = detect_primary_key(df)
    if pk:
        dup_pk = df.duplicated(subset=[pk]).sum()
        if dup_pk:
            logger.warning("[%s] %d duplicate value(s) on inferred key '%s'", filename, dup_pk, pk)
            summary["warnings"] += 1
        else:
            logger.info("[%s] Primary key '%s' is unique", filename, pk)

    # Numeric columns
    for col in infer_numeric_columns(df):
        bad = pd.to_numeric(df[col], errors="coerce").isna() & df[col].notna()
        if bad.any():
            logger.warning("[%s] Numeric column '%s' has %d non-numeric value(s)", filename, col, int(bad.sum()))
            summary["warnings"] += 1

    # Date columns
    for col in infer_date_columns(df):
        bad = pd.to_datetime(df[col], errors="coerce").isna() & df[col].notna()
        if bad.any():
            logger.warning("[%s] Date column '%s' has %d unparseable value(s)", filename, col, int(bad.sum()))
            summary["warnings"] += 1

    status = "PASSED" if summary["warnings"] == 0 else "PASSED (with warnings)"
    summary["status"] = status
    logger.info("[%s] Validation: %s", filename, status)
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("Starting raw data validation")
    logger.info("Raw data directory: %s", os.path.abspath(RAW_DIR))

    if not os.path.isdir(RAW_DIR):
        logger.error("Raw data directory not found: %s", RAW_DIR)
        return

    csv_files = sorted(f for f in os.listdir(RAW_DIR) if f.lower().endswith(".csv"))
    if not csv_files:
        logger.warning("No CSV files found in %s", RAW_DIR)
        return

    logger.info("Detected %d CSV file(s): %s", len(csv_files), ", ".join(csv_files))

    results = [validate_file(os.path.join(RAW_DIR, f)) for f in csv_files]

    logger.info("=" * 60)
    logger.info("Validation Summary")
    logger.info("=" * 60)
    for r in results:
        logger.info("  %-42s %-24s rows=%-6d warnings=%d",
                    r["file"], r["status"], r["rows"], r["warnings"])

    failed = [r for r in results if r["status"] == "FAILED"]
    logger.info("-" * 60)
    logger.info("Result: %d file(s) validated, %d failed (empty/unreadable).",
                len(results), len(failed))
    logger.info("Full summary written to: %s",
                os.path.abspath(os.path.join(LOG_DIR, "validation_summary.txt")))


if __name__ == "__main__":
    main()
