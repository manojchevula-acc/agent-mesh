"""Reviewer tool: batch-load the curated few-shot seed set into the approved examples.

Reads sql_agent/data/example_seed.yaml and, for each entry, re-validates the SQL through
the six-check validator AND runs it live against the governed DB (same guarantee as
scripts/promote_example.py) before storing it as an approved example. An example that
would not pass live is reported and skipped — never stored. Idempotent: an example whose
question already exists as approved has its SQL/validation left untouched, but its
``metadata`` (tables/columns/joins/intent/sql_pattern/... — see
scripts/generate_example_metadata.py) is refreshed from the seed file every run, so
re-running the metadata generator and then this script is enough to pick up a metadata
change WITHOUT re-executing already-approved SQL against the live DB.

Usage:
    python scripts/seed_examples.py [--by seed] [--file path/to/example_seed.yaml] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from sqlalchemy import insert, select, update

from sql_agent.db import db                      # runs the six-check validator on execute
from sql_agent.memory.db import examples, get_engine, init_tables

DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sql_agent" / "data" / "example_seed.yaml"


def _load_seed(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = data.get("examples", [])
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected a top-level 'examples:' list")
    return rows


def _existing_questions(conn) -> dict[str, int]:
    rows = conn.execute(select(examples.c.id, examples.c.question)
                         .where(examples.c.status == "approved"))
    return {question: id_ for id_, question in rows}


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
    if engine is not None:
        init_tables()  # ensure the examples.metadata column exists on a pre-existing DB

    existing: dict[str, int] = {}
    if engine is not None:
        with engine.connect() as conn:
            existing = _existing_questions(conn)

    loaded, skipped, refreshed, failed = 0, 0, 0, 0
    for entry in seed:
        question = (entry.get("question") or "").strip()
        sql = (entry.get("sql") or "").strip()
        metadata = entry.get("metadata")
        metadata_json = json.dumps(metadata) if metadata else None
        if not question or not sql:
            print(f"SKIP  (malformed entry, missing question/sql): {entry!r}")
            failed += 1
            continue

        if question in existing:
            # Already approved and live-validated — never re-execute the SQL, but DO
            # refresh metadata so scripts/generate_example_metadata.py changes apply
            # without re-running every example's SQL against the live DB again.
            if metadata_json and engine is not None and not args.dry_run:
                with engine.begin() as conn:
                    conn.execute(
                        update(examples).where(examples.c.id == existing[question])
                        .values(metadata=metadata_json)
                    )
                print(f"META  (refreshed metadata): {question}")
                refreshed += 1
            else:
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
                metadata=metadata_json,
                status="approved", approved_by=args.by,
            ))
        existing[question] = None  # already inserted this run; id unused after this point
        print(f"LOAD  {question}")
        loaded += 1

    print(f"\nDone. loaded={loaded} skipped={skipped} refreshed={refreshed} failed={failed} "
          f"(total={len(seed)}){' [dry-run]' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
