"""
03_load_curated_to_mysql.py
----------------------------
Loads all curated CSV files from data/curated/ into MySQL schema fab_curated.

Steps performed:
  1. Read .env for MySQL credentials.
  2. Create schemas (fab_curated, fab_semantic) if they don't exist.
  3. Run sql/02_create_curated_tables.sql to create/verify tables.
  4. Load each curated CSV into its corresponding table using
     pandas.DataFrame.to_sql with if_exists='replace' strategy.

Run this after 02_create_curated_data.py has generated the curated CSVs.
"""

import os
import logging
from urllib.parse import quote_plus
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

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
SQL_DIR      = "sql"

# Map CSV filename → MySQL table name
CSV_TABLE_MAP = {
    "customer_master.csv":     "customer_master",
    "historical_deals.csv":    "historical_deals",
    "pricing_policy.csv":      "pricing_policy",
    "product_master.csv":      "product_master",
    "treasury_rate_sheet.csv": "treasury_rate_sheet",
}

# Date columns to parse per file
DATE_COLUMNS = {
    "historical_deals.csv":    ["deal_date"],
    "treasury_rate_sheet.csv": ["effective_date"],
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def build_engine(host: str, port: int, user: str, password: str, database: str):
    """Return a SQLAlchemy engine for the given MySQL database."""
    url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"
        "?charset=utf8mb4"
    )
    return create_engine(url, echo=False, pool_pre_ping=True)


def ensure_schemas(engine) -> None:
    """Create fab_curated and fab_semantic schemas if they don't exist."""
    sql_path = os.path.join(SQL_DIR, "01_create_schemas.sql")
    execute_sql_file(engine, sql_path, database=None)


def run_create_tables(engine) -> None:
    """Run the DDL script to create curated tables."""
    sql_path = os.path.join(SQL_DIR, "02_create_curated_tables.sql")
    execute_sql_file(engine, sql_path, database="fab_curated")


def execute_sql_file(engine, sql_path: str, database: str | None) -> None:
    """Execute a multi-statement SQL file, splitting on ';'."""
    if not os.path.isfile(sql_path):
        logger.warning("SQL file not found, skipping: %s", sql_path)
        return

    with open(sql_path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    # Strip comments and split into individual statements
    statements = [
        s.strip()
        for s in raw.split(";")
        if s.strip() and not s.strip().startswith("--")
    ]

    with engine.connect() as conn:
        if database:
            conn.execute(text(f"USE `{database}`"))
        for stmt in statements:
            if stmt:
                try:
                    conn.execute(text(stmt))
                except Exception as exc:
                    logger.warning("Statement skipped (%s): %.120s", type(exc).__name__, stmt)
        conn.commit()

    logger.info("Executed SQL file: %s", sql_path)


# ---------------------------------------------------------------------------
# Load helper
# ---------------------------------------------------------------------------

def load_csv_to_mysql(filename: str, engine) -> bool:
    """Load a single curated CSV into its MySQL table."""
    src = os.path.join(CURATED_DIR, filename)
    table = CSV_TABLE_MAP[filename]

    logger.info("=" * 60)
    logger.info("Loading: %s → fab_curated.%s", filename, table)

    try:
        date_cols = DATE_COLUMNS.get(filename, [])
        df = pd.read_csv(src, parse_dates=date_cols)
        logger.info("  Read %d rows × %d cols from CSV", *df.shape)
    except Exception as exc:
        logger.error("  Failed to read CSV: %s", exc)
        return False

    try:
        df.to_sql(
            name=table,
            con=engine,
            schema="fab_curated",
            if_exists="replace",   # Drops & recreates — safe for POC
            index=False,
            chunksize=500,
        )
        logger.info("  Loaded %d rows → fab_curated.%s", len(df), table)
    except Exception as exc:
        logger.error("  Failed to load into MySQL: %s", exc)
        return False

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load .env
    load_dotenv()
    host     = os.getenv("MYSQL_HOST", "localhost")
    port     = int(os.getenv("MYSQL_PORT", 3306))
    user     = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "fab_curated")

    if not password or password == "<your_password>":
        logger.error(
            "MYSQL_PASSWORD is not set. Update .env with your MySQL password."
        )
        return

    logger.info("Connecting to MySQL at %s:%d as '%s'", host, port, user)

    # Build engine pointed at the server (no specific DB yet)
    server_url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}"
        "?charset=utf8mb4"
    )
    server_engine = create_engine(server_url, echo=False, pool_pre_ping=True)

    # Step 1 – Ensure schemas exist
    logger.info("Step 1 – Ensuring schemas exist ...")
    ensure_schemas(server_engine)

    # Step 2 – Build engine on fab_curated and create tables
    engine = build_engine(host, port, user, password, database)
    logger.info("Step 2 – Creating/verifying curated tables ...")
    run_create_tables(engine)

    # Step 3 – Load curated CSVs
    logger.info("Step 3 – Loading curated CSVs into MySQL ...")
    if not os.path.isdir(CURATED_DIR):
        logger.error("Curated directory not found: %s", os.path.abspath(CURATED_DIR))
        return

    results = {}
    for filename in CSV_TABLE_MAP:
        results[filename] = load_csv_to_mysql(filename, engine)

    # Summary
    logger.info("=" * 60)
    logger.info("Load Summary")
    logger.info("=" * 60)
    for filename, ok in results.items():
        logger.info("  %-40s %s", filename, "OK" if ok else "FAILED")

    passed = sum(results.values())
    logger.info("-" * 60)
    logger.info("Result: %d / %d tables loaded successfully", passed, len(results))
    logger.info("Next step: run 04_create_semantic_views.py")


if __name__ == "__main__":
    main()
