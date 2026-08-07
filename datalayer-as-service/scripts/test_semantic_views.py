"""
scripts/test_semantic_views.py
------------------------------
Validates the fab_semantic layer:

  1. Confirms .env is loaded and MySQL is reachable.
  2. Lists all views in fab_semantic.
  3. Prints the row count for every semantic view.
  4. Runs a sample SELECT for the key business views.

Run from the project root:
    python scripts/test_semantic_views.py
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from mcp_server.db import get_engine

SEPARATOR = "-" * 60

EXPECTED_VIEWS = [
    "segment_pricing_benchmark", "operations_cost_impact", "customer_360",
    "pricing_recommendation_view", "margin_analysis", "profitability_summary",
    "rwa_impact_view", "new_customer_pricing_view", "competitor_price_analysis",
    "pricing_trace_view", "relationship_discount_view", "win_loss_insights",
    "policy_exception_view",
]

SAMPLE_VIEWS = [
    "customer_360",
    "pricing_recommendation_view",
    "competitor_price_analysis",
    "pricing_trace_view",
    "policy_exception_view",
]


def print_section(title: str) -> None:
    print(f"\n{SEPARATOR}\n  {title}\n{SEPARATOR}")


def main() -> None:
    print("\n==============================================")
    print("  FAB Semantic Layer – View Validation Test")
    print("==============================================")

    load_dotenv()
    if not os.getenv("MYSQL_PASSWORD"):
        print("  ERROR: MYSQL_PASSWORD not set. Copy .env.example to .env.")
        sys.exit(1)

    try:
        engine = get_engine()

        print_section("Views present in fab_semantic")
        with engine.connect() as conn:
            df = pd.read_sql(text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS "
                "WHERE TABLE_SCHEMA = 'fab_semantic' ORDER BY TABLE_NAME"), conn)
        present = set(df["TABLE_NAME"])
        for v in EXPECTED_VIEWS:
            print(f"  {'EXISTS ' if v in present else 'MISSING'}  {v}")

        print_section("Row counts")
        with engine.connect() as conn:
            for v in EXPECTED_VIEWS:
                try:
                    n = conn.execute(text(f"SELECT COUNT(*) FROM fab_semantic.`{v}`")).scalar()
                    print(f"  {v:<32} {n} rows")
                except Exception as exc:
                    print(f"  {v:<32} ERROR: {exc}")

        for v in SAMPLE_VIEWS:
            print_section(f"Sample: SELECT * FROM {v} LIMIT 3")
            with engine.connect() as conn:
                sdf = pd.read_sql(text(f"SELECT * FROM fab_semantic.`{v}` LIMIT 3"), conn)
            records = sdf.astype(object).where(pd.notnull(sdf), None).to_dict(orient="records")
            print(json.dumps(records, indent=2, default=str))

    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        print("  Check your .env credentials and ensure MySQL is running.")
        sys.exit(1)

    print(f"\n{SEPARATOR}\n  Semantic view validation complete.\n{SEPARATOR}\n")


if __name__ == "__main__":
    main()
