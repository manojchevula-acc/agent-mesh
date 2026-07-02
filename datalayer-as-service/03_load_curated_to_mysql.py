"""
03_load_curated_to_mysql.py
----------------------------
Dynamically loads EVERY curated CSV in data/curated into MySQL schema
fab_curated. The table name is the CSV base filename (e.g. competitor_pricing.csv
-> fab_curated.competitor_pricing).

Steps:
  1. Read MySQL credentials from .env (password URL-encoded via quote_plus).
  2. Create schemas fab_curated and fab_semantic if they do not exist.
  3. (Best effort) run sql/02_create_curated_tables.sql for the core tables.
  4. Load each curated CSV with pandas.to_sql(if_exists='replace') — dynamic
     table creation, so newly added CSVs are picked up automatically.
  5. Validate the row count of every loaded table.

Run after 02_create_curated_data.py has generated the curated CSVs.
"""

import os
import re
import logging
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

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
CURATED_DIR = os.path.join("data", "curated")
SQL_DIR = "sql"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def build_engine(host, port, user, password, database=None):
    base = f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}"
    url = f"{base}/{database}?charset=utf8mb4" if database else f"{base}?charset=utf8mb4"
    return create_engine(url, echo=False, pool_pre_ping=True)


def execute_sql_file(engine, sql_path: str, database: str | None) -> None:
    if not os.path.isfile(sql_path):
        logger.warning("SQL file not found, skipping: %s", sql_path)
        return

    with open(sql_path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    statements = [s.strip() for s in raw.split(";") if s.strip() and not s.strip().startswith("--")]

    with engine.connect() as conn:
        if database:
            conn.execute(text(f"USE `{database}`"))
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as exc:
                logger.warning("Statement skipped (%s): %.100s", type(exc).__name__, stmt)
        conn.commit()
    logger.info("Executed SQL file: %s", sql_path)


def table_name_from_file(filename: str) -> str:
    base = os.path.splitext(filename)[0].lower()
    base = re.sub(r"[^\w]+", "_", base).strip("_")
    return base


# ---------------------------------------------------------------------------
# Load helper
# ---------------------------------------------------------------------------

def load_csv_to_mysql(filename: str, engine) -> tuple[bool, int]:
    src = os.path.join(CURATED_DIR, filename)
    table = table_name_from_file(filename)

    logger.info("=" * 60)
    logger.info("Loading: %s -> fab_curated.%s", filename, table)

    try:
        df = pd.read_csv(src)
    except Exception as exc:
        logger.error("  Failed to read CSV: %s", exc)
        return False, 0

    try:
        df.to_sql(
            name=table,
            con=engine,
            schema="fab_curated",
            if_exists="replace",   # dynamic create — safe for POC
            index=False,
            chunksize=500,
        )
    except Exception as exc:
        logger.error("  Failed to load into MySQL: %s", exc)
        return False, 0

    # Row-count validation
    try:
        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM fab_curated.`{table}`")).scalar()
    except Exception as exc:
        logger.error("  Row-count validation failed: %s", exc)
        return False, 0

    ok = count == len(df)
    logger.info("  CSV rows=%d | DB rows=%d | %s", len(df), count, "MATCH" if ok else "MISMATCH")
    return ok, count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_dotenv()
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")

    if not password or password == "<your_mysql_password>":
        logger.error("MYSQL_PASSWORD is not set. Update .env with your MySQL password.")
        return

    logger.info("Connecting to MySQL at %s:%d as '%s'", host, port, user)
    server_engine = build_engine(host, port, user, password)

    # Step 1 — schemas
    logger.info("Step 1 — Ensuring schemas exist ...")
    execute_sql_file(server_engine, os.path.join(SQL_DIR, "01_create_schemas.sql"), database=None)

    # Step 2 — core DDL (best effort; to_sql replace covers everything else)
    curated_engine = build_engine(host, port, user, password, "fab_curated")
    logger.info("Step 2 — Applying core curated DDL (best effort) ...")
    execute_sql_file(curated_engine, os.path.join(SQL_DIR, "02_create_curated_tables.sql"), database="fab_curated")

    # Step 3 — dynamic load of all curated CSVs
    logger.info("Step 3 — Loading all curated CSVs into fab_curated ...")
    if not os.path.isdir(CURATED_DIR):
        logger.error("Curated directory not found: %s", os.path.abspath(CURATED_DIR))
        return

    csv_files = sorted(f for f in os.listdir(CURATED_DIR) if f.lower().endswith(".csv"))
    if not csv_files:
        logger.warning("No curated CSVs found. Run 02_create_curated_data.py first.")
        return

    logger.info("Detected %d curated CSV(s)", len(csv_files))

    results = {}
    for filename in csv_files:
        ok, count = load_csv_to_mysql(filename, curated_engine)
        results[table_name_from_file(filename)] = (ok, count)

    logger.info("=" * 60)
    logger.info("Load Summary")
    logger.info("=" * 60)
    for table, (ok, count) in results.items():
        logger.info("  fab_curated.%-38s %-6s rows=%d", table, "OK" if ok else "FAILED", count)

    passed = sum(1 for ok, _ in results.values() if ok)
    logger.info("-" * 60)
    logger.info("Result: %d / %d tables loaded successfully", passed, len(results))
    logger.info("Next step: run 04_create_semantic_views.py")


if __name__ == "__main__":
    main()
