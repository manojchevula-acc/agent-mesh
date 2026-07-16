"""Materialize gold_result IN PLACE in an eval dataset by running each gold_sql live.

The dataset (eval/datasets/gold_dynamic.yaml) is SELF-CONTAINED: every item carries the
question, the reference gold_sql, and the ACTUAL ROWS that SQL returns. This script owns
that last part. It executes every gold_sql through the SAME validated read path the agent
uses (sql_agent.db.Executor -> six-check validator -> live DB) and writes the rows back
into the YAML, so the dataset never drifts from the database it describes.

Re-run it whenever the DB snapshot changes. gold_result is machine-written on purpose —
hand-typing expected rows is how an eval ends up asserting a number the database never
produced, which then marks correct agent behaviour as a failure.

Editing is a ruamel round-trip, so the dataset's documentation comments survive. (PyYAML
would silently drop every comment in the file.)

Two integrity checks run alongside, because a wrong gold is worse than no gold:
  * EMPTY GOLD — a gold_sql returning zero rows cannot tell a correct agent from one that
    filtered everything away; any empty result the agent returns would "match".
  * TABLE/COLUMN DRIFT — gold_tables/gold_columns are parsed back out of gold_sql with
    sqlglot and compared. The comparison scripts grade the agent's table/column selection
    against these labels, so if they drift from the SQL they describe, the grading is
    against a fiction.

Run:
    .venv/Scripts/python.exe eval/materialize_gold.py                    # gold_dynamic
    .venv/Scripts/python.exe eval/materialize_gold.py --dataset gold_dynamic
    .venv/Scripts/python.exe eval/materialize_gold.py --check            # verify, write nothing
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sql_agent.db import db  # noqa: E402

from eval.sql_introspect import extract  # noqa: E402

HERE = Path(__file__).resolve().parent


def _yaml():
    """ruamel round-tripper, configured to keep the dataset readable.

    Imported here rather than at module scope so `--help` and the import of this module
    still work without the dependency, and so the failure names the exact fix.
    """
    try:
        from ruamel.yaml import YAML
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "ruamel.yaml is required to rewrite the dataset in place while preserving its "
            "comments.\n  Install with:  uv sync    (it is in the [dependency-groups] dev "
            "group)\n  or:            uv pip install 'ruamel.yaml>=0.18.0'"
        ) from exc
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096          # never re-wrap our SQL blocks or long row lines
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _clean(value):
    """Coerce a DB value into something YAML can round-trip as a plain scalar.

    MySQL hands back Decimal and date objects; dumped raw they become Python-specific
    YAML tags (!!python/object) that no other reader can load.
    """
    if isinstance(value, decimal.Decimal):
        f = float(value)
        return int(f) if f == int(f) else f
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _flow_row(row: dict):
    """One result row as a flow-style mapping, so a 17-row gold stays 17 lines."""
    from ruamel.yaml.comments import CommentedMap
    m = CommentedMap((k, _clean(v)) for k, v in row.items())
    m.fa.set_flow_style()
    return m


def _check_labels(item) -> list[str]:
    """Compare the item's declared gold_tables/gold_columns against its gold_sql."""
    sql = item.get("gold_sql")
    if not sql:
        return []
    tables, columns = extract(sql)
    problems = []
    declared_t = {str(t).lower() for t in item.get("gold_tables") or []}
    declared_c = {str(c).lower() for c in item.get("gold_columns") or []}
    if declared_t - tables:
        problems.append(f"gold_tables declares {sorted(declared_t - tables)} not in SQL")
    if tables - declared_t:
        problems.append(f"gold_tables missing {sorted(tables - declared_t)} used by SQL")
    if declared_c - columns:
        problems.append(f"gold_columns declares {sorted(declared_c - columns)} not in SQL")
    if columns - declared_c:
        problems.append(f"gold_columns missing {sorted(columns - declared_c)} used by SQL")
    return problems


def materialize(dataset: str, check_only: bool = False) -> int:
    path = HERE / "datasets" / f"{dataset}.yaml"
    yaml = _yaml()
    data = yaml.load(path)
    snapshot = (data.get("meta") or {}).get("db_snapshot", "unknown")

    ok = failed = empty = 0
    drift: list[str] = []

    for item in data["items"]:
        cid, sql = item["id"], item.get("gold_sql")
        for problem in _check_labels(item):
            drift.append(f"{cid}: {problem}")
        if not sql:
            print(f"SKIP {cid:5s} no gold_sql")
            continue
        try:
            rows = db.execute(sql).rows
        except Exception as exc:  # noqa: BLE001 — report and keep going
            print(f"FAIL {cid:5s} {type(exc).__name__}: {str(exc)[:110]}")
            failed += 1
            continue

        changed = ""
        if not check_only:
            from ruamel.yaml.comments import CommentedSeq
            new = CommentedSeq(_flow_row(r) for r in rows)
            if item.get("row_count") != len(rows):
                changed = f"  (was {item.get('row_count')} rows)"
            item["gold_result"] = new
            item["row_count"] = len(rows)

        flag = ""
        if not rows:
            empty += 1
            flag = ("   <== EMPTY GOLD: cannot distinguish a correct agent from one "
                    "that returns nothing")
        print(f"OK   {cid:5s} rows={len(rows)}{changed}{flag}")
        ok += 1

    if drift:
        print(f"\n!! {len(drift)} table/column label drift(s) — the selection grading uses "
              f"these, so they must match the SQL:")
        for d in drift:
            print(f"   {d}")

    if check_only:
        print(f"\n--check: {ok} executable, {failed} failed, {empty} empty, "
              f"{len(drift)} drifted. Nothing written.")
        return 1 if (failed or drift or empty) else 0

    yaml.dump(data, path)
    print(f"\nWrote {path}  ({ok} ok, {failed} failed, {empty} empty) "
          f"against snapshot '{snapshot}'")
    return 1 if (failed or drift) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Materialize gold_result in place for an eval dataset.")
    ap.add_argument("--dataset", default="gold_dynamic",
                    help="dataset stem under eval/datasets/ (default: gold_dynamic)")
    ap.add_argument("--check", action="store_true",
                    help="verify gold SQL + table/column labels; write nothing")
    args = ap.parse_args()
    raise SystemExit(materialize(args.dataset, check_only=args.check))
