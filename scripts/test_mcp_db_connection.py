"""
scripts/test_mcp_db_connection.py
-----------------------------------
Quick smoke-test for the MCP server database connection.

Tests performed:
  1. Connect to MySQL fab_semantic using credentials from .env.
  2. List all views available in fab_semantic.
  3. Run SELECT * FROM customer_360 LIMIT 5 and print results.

Run from the project root:
    python scripts/test_mcp_db_connection.py
"""

import sys
import os
import json

# Allow imports from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from sqlalchemy import text
from mcp_server.db import get_engine

SEPARATOR = "-" * 60


def print_section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def test_connection() -> None:
    print_section("Step 1 – Connect to MySQL fab_semantic")
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT VERSION(), DATABASE()")).fetchone()
    print(f"  MySQL version : {row[0]}")
    print(f"  Connected DB  : {row[1]}")
    print("  Status        : OK")
    return engine


def test_list_views(engine) -> None:
    print_section("Step 2 – Available views in fab_semantic")
    sql = text(
        "SELECT TABLE_NAME "
        "FROM INFORMATION_SCHEMA.VIEWS "
        "WHERE TABLE_SCHEMA = 'fab_semantic' "
        "ORDER BY TABLE_NAME"
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn)

    if df.empty:
        print("  No views found in fab_semantic.")
        return

    for _, row in df.iterrows():
        print(f"  - {row['TABLE_NAME']}")


def test_sample_query(engine) -> None:
    print_section("Step 3 – SELECT * FROM customer_360 LIMIT 5")
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT * FROM fab_semantic.customer_360 LIMIT 5"),
            conn,
        )

    if df.empty:
        print("  No rows returned.")
        return

    print(f"  Rows returned : {len(df)}")
    print(f"  Columns       : {list(df.columns)}\n")

    # Pretty-print as JSON (astype(object) keeps NaN->None working on pandas 3.0)
    records = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    print(json.dumps(records, indent=2, default=str))


def main() -> None:
    print("\n========================================")
    print("  FAB MCP Server – DB Connection Test")
    print("========================================")

    try:
        engine = test_connection()
        test_list_views(engine)
        test_sample_query(engine)
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        print("  Check your .env credentials and ensure MySQL is running.")
        sys.exit(1)

    print(f"\n{SEPARATOR}")
    print("  All tests passed. MCP server is ready to run.")
    print(f"{SEPARATOR}\n")


if __name__ == "__main__":
    main()
