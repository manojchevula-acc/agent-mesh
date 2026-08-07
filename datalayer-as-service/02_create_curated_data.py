"""
02_create_curated_data.py
--------------------------
Dynamically cleans EVERY CSV in data/raw and writes one curated CSV per raw
file into data/curated (same base filename).

Cleaning applied per file:
  - normalise column names to lower snake_case
  - trim whitespace on string values
  - drop fully-duplicate rows
  - parse inferred date columns (kept as ISO text in the curated CSV)
  - convert inferred numeric columns safely (guarded so 'tenor' stays text)
  - fill missing values: numeric -> median, string -> 'Unknown', date -> left null

Row counts before and after curation are logged. The script never breaks when a
new CSV is added later — it simply processes whatever it finds.
"""

import os
import re
import logging
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RAW_DIR = os.path.join("data", "raw")
CURATED_DIR = os.path.join("data", "curated")

NUMERIC_HINTS = (
    "amount", "rate", "margin", "cost", "revenue", "rwa", "capital",
    "score", "weight", "tenor", "price", "discount", "ratio", "aed",
    "pct", "fee", "premium", "buffer", "exposure", "cushion",
)
DATE_HINTS = ("date", "timestamp", "_from", "_to", "effective")
NUMERIC_EXCLUDE = ("tenor",)          # keep '1M','60M' as text
PARSE_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalise_columns(cols) -> list[str]:
    out = []
    for c in cols:
        c = str(c).strip().lower()
        c = re.sub(r"[^\w]+", "_", c)
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


# ---------------------------------------------------------------------------
# Per-file curation
# ---------------------------------------------------------------------------

def curate_file(filename: str) -> bool:
    src = os.path.join(RAW_DIR, filename)
    dst = os.path.join(CURATED_DIR, filename)

    logger.info("=" * 60)
    logger.info("Curating: %s", filename)

    try:
        df = pd.read_csv(src)
    except Exception as exc:
        logger.error("[%s] Failed to read: %s", filename, exc)
        return False

    rows_before = len(df)
    df.columns = normalise_columns(df.columns)

    numeric_cols = infer_numeric_columns(df)
    date_cols = infer_date_columns(df)
    string_cols = [c for c in df.columns if c not in numeric_cols and c not in date_cols]

    # Trim strings
    for c in string_cols:
        df[c] = df[c].astype(str).str.strip()
        df[c] = df[c].replace({"nan": None, "NaN": None, "None": None, "": None})

    # Drop fully-duplicate rows
    df = df.drop_duplicates(keep="first")

    # Parse dates (store as ISO date text; keep null when unparseable)
    for c in date_cols:
        parsed = pd.to_datetime(df[c], errors="coerce")
        # normalise timezone-aware values to naive to keep CSV/MySQL simple
        try:
            if getattr(parsed.dt, "tz", None) is not None:
                parsed = parsed.dt.tz_convert(None)
        except (TypeError, AttributeError):
            pass
        df[c] = parsed.dt.strftime("%Y-%m-%d")

    # Convert numeric columns safely
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Fill missing values
    for c in df.columns:
        if df[c].isnull().any():
            if c in numeric_cols and pd.api.types.is_numeric_dtype(df[c]):
                median_val = df[c].median()
                median_val = 0.0 if pd.isna(median_val) else median_val
                df[c] = df[c].fillna(median_val)
            elif c in date_cols:
                continue  # leave date nulls as-is
            else:
                df[c] = df[c].fillna("Unknown")

    try:
        df.to_csv(dst, index=False)
    except Exception as exc:
        logger.error("[%s] Failed to write curated file: %s", filename, exc)
        return False

    rows_after = len(df)
    logger.info("[%s] rows before=%d after=%d (dropped %d) -> %s",
                filename, rows_before, rows_after, rows_before - rows_after, dst)
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

    csv_files = sorted(f for f in os.listdir(RAW_DIR) if f.lower().endswith(".csv"))
    if not csv_files:
        logger.warning("No CSV files found in %s", RAW_DIR)
        return

    logger.info("Detected %d CSV file(s)", len(csv_files))

    results = {f: curate_file(f) for f in csv_files}

    logger.info("=" * 60)
    logger.info("Curation Summary")
    logger.info("=" * 60)
    for f, ok in results.items():
        logger.info("  %-42s %s", f, "OK" if ok else "FAILED")

    passed = sum(results.values())
    logger.info("-" * 60)
    logger.info("Result: %d / %d files curated successfully", passed, len(results))


if __name__ == "__main__":
    main()
