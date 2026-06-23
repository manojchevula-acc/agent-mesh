"""
04_create_semantic_views.py
----------------------------
Executes sql/03_create_semantic_views.sql against MySQL to create (or replace)
the five business views in the fab_semantic schema.

Views created:
  fab_semantic.customer_360
  fab_semantic.pricing_recommendation_view
  fab_semantic.margin_analysis
  fab_semantic.profitability_summary
  fab_semantic.rwa_impact_view

After this script completes, MCP (or any BI tool) can connect to MySQL and
query only the fab_semantic views without touching raw or curated tables.

Run this after 03_load_curated_to_mysql.py has loaded all tables.
"""

import os
import logging
from urllib.parse import quote_plus
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
SQL_FILE = os.path.join("sql", "03_create_semantic_views.sql")

# View names expected to exist after the script runs
EXPECTED_VIEWS = [
    "customer_360",
    "pricing_recommendation_view",
    "margin_analysis",
    "profitability_summary",
    "rwa_impact_view",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_engine(host: str, port: int, user: str, password: str, database: str):
    url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"
        "?charset=utf8mb4"
    )
    return create_engine(url, echo=False, pool_pre_ping=True)


def execute_views_sql(engine, sql_path: str) -> list[str]:
    """
    Parse and execute each CREATE OR REPLACE VIEW statement from the SQL file.
    Returns a list of view names that were successfully created.
    """
    if not os.path.isfile(sql_path):
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    with open(sql_path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    # Split on semicolon; skip pure comment blocks and USE statements
    statements = []
    for s in raw.split(";"):
        stripped = s.strip()
        if not stripped:
            continue
        # Skip comment-only blocks
        lines = [ln for ln in stripped.splitlines() if not ln.strip().startswith("--")]
        clean = "\n".join(lines).strip()
        if clean:
            statements.append(clean)

    created = []
    with engine.connect() as conn:
        for stmt in statements:
            upper = stmt.upper().lstrip()
            if upper.startswith("USE "):
                # Execute USE as-is to keep context
                conn.execute(text(stmt))
                continue
            if "CREATE" in upper and "VIEW" in upper:
                # Extract view name for logging
                tokens = stmt.split()
                try:
                    view_name = tokens[tokens.index("VIEW") + 1].strip("`").split(".")[-1]
                except (ValueError, IndexError):
                    view_name = "unknown"

                try:
                    conn.execute(text(stmt))
                    conn.commit()
                    logger.info("  Created view: fab_semantic.%s", view_name)
                    created.append(view_name)
                except Exception as exc:
                    logger.error("  Failed to create view '%s': %s", view_name, exc)
            else:
                # Other statements (e.g. comments resolved to blank)
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception:
                    pass

    return created


def verify_views(engine) -> None:
    """Query INFORMATION_SCHEMA to confirm all views exist in fab_semantic."""
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT TABLE_NAME "
            "FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_SCHEMA = 'fab_semantic' "
            "ORDER BY TABLE_NAME"
        ))
        existing = {row[0] for row in result}

    logger.info("=" * 60)
    logger.info("View Verification")
    logger.info("=" * 60)
    all_ok = True
    for view in EXPECTED_VIEWS:
        status = "EXISTS" if view in existing else "MISSING"
        if view not in existing:
            all_ok = False
        logger.info("  fab_semantic.%-35s %s", view, status)

    if all_ok:
        logger.info("-" * 60)
        logger.info("All %d semantic views verified successfully.", len(EXPECTED_VIEWS))
    else:
        logger.warning("Some views are missing — check the error log above.")


def sample_views(engine) -> None:
    """Log row counts from each semantic view as a quick smoke-test."""
    logger.info("=" * 60)
    logger.info("Row-count smoke test")
    logger.info("=" * 60)
    with engine.connect() as conn:
        for view in EXPECTED_VIEWS:
            try:
                row = conn.execute(
                    text(f"SELECT COUNT(*) FROM fab_semantic.`{view}`")
                ).fetchone()
                logger.info("  fab_semantic.%-35s %d rows", view, row[0])
            except Exception as exc:
                logger.error("  fab_semantic.%-35s ERROR: %s", view, exc)


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
    # Connect to fab_semantic for view creation; views reference fab_curated tables
    database = "fab_semantic"

    if not password or password == "<your_password>":
        logger.error(
            "MYSQL_PASSWORD is not set. Update .env with your MySQL password."
        )
        return

    logger.info("Connecting to MySQL at %s:%d as '%s'", host, port, user)
    engine = build_engine(host, port, user, password, database)

    # Create / replace the semantic views
    logger.info("Creating semantic views from: %s", SQL_FILE)
    logger.info("=" * 60)
    try:
        created = execute_views_sql(engine, SQL_FILE)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return

    logger.info("=" * 60)
    logger.info("%d view(s) processed", len(created))

    # Verify and smoke-test
    verify_views(engine)
    sample_views(engine)

    logger.info("=" * 60)
    logger.info(
        "Semantic layer ready. MCP server can now connect to MySQL and "
        "query fab_semantic views only."
    )


if __name__ == "__main__":
    main()
