"""Reviewer tool: promote a question -> validated SQL into the approved examples set.

The SQL is re-validated through the six checks before it can be approved, so a curated
example can never contain SQL that wouldn't pass live (architecture §4.4).

Usage:
    python scripts/promote_example.py --question "..." --sql "SELECT ..." --by alice
"""

import argparse

from sqlalchemy import insert

from sql_agent.db import db                      # runs the six-check validator on execute
from sql_agent.memory.db import examples, get_engine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", required=True)
    ap.add_argument("--sql", required=True)
    ap.add_argument("--tier", default="full_dynamic")
    ap.add_argument("--tags", default="")
    ap.add_argument("--by", required=True)
    args = ap.parse_args()

    # Validate (and execute) the SQL so we only ever store known-good examples.
    db.execute(args.sql)  # raises if it fails any of the six checks

    engine = get_engine()
    if engine is None:
        raise SystemExit("AGENT_DB_DSN not set — no metadata DB to write to")
    with engine.begin() as conn:
        conn.execute(insert(examples).values(
            question=args.question, validated_sql=args.sql, tier=args.tier,
            tags=args.tags, status="approved", approved_by=args.by,
        ))
    print("example approved")


if __name__ == "__main__":
    main()
