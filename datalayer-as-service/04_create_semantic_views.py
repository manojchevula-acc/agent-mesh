"""
04_create_semantic_views.py
----------------------------
Executes sql/03_create_semantic_views.sql against MySQL to create (or replace)
the enhanced business views in the fab_semantic schema.

Views created (13 business products + 0 helpers exposed):
  Core:
    customer_360, pricing_recommendation_view, margin_analysis,
    profitability_summary, rwa_impact_view
  Enhanced:
    new_customer_pricing_view, competitor_price_analysis, pricing_trace_view,
    segment_pricing_benchmark, operations_cost_impact,
    relationship_discount_view, win_loss_insights, policy_exception_view

After this script completes, the MCP server / AI agent connect to MySQL and
query ONLY the fab_semantic views (never raw or curated tables).

Run after 03_load_curated_to_mysql.py has loaded all tables.
"""

import os
import logging
from urllib.parse import quote_plus
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
SQL_FILE = os.path.join("sql", "03_create_semantic_views.sql")

EXPECTED_VIEWS = [
    # helper views (also queryable)
    "segment_pricing_benchmark",
    "operations_cost_impact",
    # core business views
    "customer_360",
    "pricing_recommendation_view",
    "margin_analysis",
    "profitability_summary",
    "rwa_impact_view",
    # enhanced business views
    "new_customer_pricing_view",
    "competitor_price_analysis",
    "pricing_trace_view",
    "relationship_discount_view",
    "win_loss_insights",
    "policy_exception_view",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_engine(host, port, user, password, database):
    url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"
        "?charset=utf8mb4"
    )
    return create_engine(url, echo=False, pool_pre_ping=True)


def execute_views_sql(engine, sql_path: str) -> list[str]:
    if not os.path.isfile(sql_path):
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    with open(sql_path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    statements = []
    for s in raw.split(";"):
        lines = [ln for ln in s.splitlines() if not ln.strip().startswith("--")]
        clean = "\n".join(lines).strip()
        if clean:
            statements.append(clean)

    created = []
    with engine.connect() as conn:
        for stmt in statements:
            upper = stmt.upper().lstrip()
            if upper.startswith("USE "):
                conn.execute(text(stmt))
                continue
            if "CREATE" in upper and "VIEW" in upper:
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
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception:
                    pass
    return created


def verify_views(engine) -> None:
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_SCHEMA = 'fab_semantic' ORDER BY TABLE_NAME"
        ))
        existing = {row[0] for row in result}

    logger.info("=" * 60)
    logger.info("View Verification")
    logger.info("=" * 60)
    all_ok = True
    for view in EXPECTED_VIEWS:
        ok = view in existing
        all_ok = all_ok and ok
        logger.info("  fab_semantic.%-35s %s", view, "EXISTS" if ok else "MISSING")

    logger.info("-" * 60)
    if all_ok:
        logger.info("All %d semantic views verified successfully.", len(EXPECTED_VIEWS))
    else:
        logger.warning("Some views are missing — check the error log above.")


def sample_views(engine) -> None:
    logger.info("=" * 60)
    logger.info("Row-count smoke test")
    logger.info("=" * 60)
    with engine.connect() as conn:
        for view in EXPECTED_VIEWS:
            try:
                row = conn.execute(text(f"SELECT COUNT(*) FROM fab_semantic.`{view}`")).fetchone()
                logger.info("  fab_semantic.%-35s %d rows", view, row[0])
            except Exception as exc:
                logger.error("  fab_semantic.%-35s ERROR: %s", view, exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_dotenv()
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = "fab_semantic"

    if not password or password == "<your_mysql_password>":
        logger.error("MYSQL_PASSWORD is not set. Update .env with your MySQL password.")
        return

    logger.info("Connecting to MySQL at %s:%d as '%s'", host, port, user)
    engine = build_engine(host, port, user, password, database)

    logger.info("Creating semantic views from: %s", SQL_FILE)
    logger.info("=" * 60)
    try:
        created = execute_views_sql(engine, SQL_FILE)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return

    logger.info("=" * 60)
    logger.info("%d view(s) processed", len(created))

    verify_views(engine)
    sample_views(engine)

    logger.info("=" * 60)
    logger.info("Semantic layer ready. MCP server can now query fab_semantic views only.")


if __name__ == "__main__":
    main()
