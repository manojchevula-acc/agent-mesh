"""Reviewer tool: batch-load the curated few-shot seed set into the approved examples.

Reads sql_agent/data/example_seed.yaml and, for each entry, re-validates the SQL through
the six-check validator AND runs it live against the governed DB (same guarantee as
scripts/promote_example.py) before storing it as an approved example. An example that
would not pass live is reported and skipped — never stored. Idempotent: an example whose
question already exists as approved is left untouched.

Usage:
    python scripts/seed_examples.py [--by seed] [--file path/to/example_seed.yaml] [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from sqlalchemy import insert, select

from sql_agent.db import db                      # runs the six-check validator on execute
from sql_agent.memory.db import examples, get_engine

DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sql_agent" / "data" / "example_seed.yaml"


def _load_seed(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("examples", [])
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected a top-level 'examples:' list")
    return rows


def _existing_questions(conn) -> set[str]:
    rows = conn.execute(select(examples.c.question).where(examples.c.status == "approved"))
    return {r[0] for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_SEED), help="path to the seed YAML")
    ap.add_argument("--by", default="seed", help="approver name recorded on each example")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate every example but write nothing")
    args = ap.parse_args()

    seed = _load_seed(Path(args.file))
    if not seed:
        raise SystemExit("no examples found in seed file")

    engine = get_engine()
    if engine is None and not args.dry_run:
        raise SystemExit("AGENT_DB_DSN not set — no metadata DB to write to "
                         "(use --dry-run to validate only)")

    existing: set[str] = set()
    if engine is not None:
        with engine.connect() as conn:
            existing = _existing_questions(conn)

    loaded, skipped, failed = 0, 0, 0
    for entry in seed:
        question = (entry.get("question") or "").strip()
        sql = (entry.get("sql") or "").strip()
        if not question or not sql:
            print(f"SKIP  (malformed entry, missing question/sql): {entry!r}")
            failed += 1
            continue

        if question in existing:
            print(f"SKIP  (already approved): {question}")
            skipped += 1
            continue

        # Validate + execute so we only ever store known-good SQL.
        try:
            db.execute(sql)  # raises if it fails any of the six checks or the live run
        except Exception as exc:  # noqa: BLE001 — report the reason, keep going
            print(f"FAIL  {question}\n      -> {type(exc).__name__}: {exc}")
            failed += 1
            continue

        if args.dry_run:
            print(f"OK    (dry-run) {question}")
            loaded += 1
            continue

        with engine.begin() as conn:
            conn.execute(insert(examples).values(
                question=question, validated_sql=sql,
                tier=entry.get("tier", "full_dynamic"), tags=entry.get("tags", ""),
                status="approved", approved_by=args.by,
            ))
        existing.add(question)
        print(f"LOAD  {question}")
        loaded += 1

    print(f"\nDone. loaded={loaded} skipped={skipped} failed={failed} "
          f"(total={len(seed)}){' [dry-run]' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
