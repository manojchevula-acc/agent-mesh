"""Materialize gold_result for an eval dataset by running each gold_sql live.

The dataset file (eval/datasets/gold_v1.yaml) is the human-authored SOURCE: it carries
the questions and the frozen gold_sql, but `gold_result: null` — because the actual rows
are SNAPSHOT-SPECIFIC data derived from the SQL. This script executes every gold_sql
through the SAME validated read path the agent uses (sql_agent.db.Executor -> six-check
validator -> live DB) and writes the results to a regenerable sidecar:

    eval/datasets/<name>.expected.json

Keeping the golds in a sidecar (a) leaves the commented source pristine, and (b) makes the
golds trivially regenerable when the DB snapshot changes — just re-run this script. The
eval runner loads both: the source for questions/labels, the sidecar for gold_result.

Run:
    .venv/Scripts/python.exe eval/materialize_gold.py                 # gold_v1
    .venv/Scripts/python.exe eval/materialize_gold.py --dataset gold_v1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from sql_agent.db import db  # noqa: E402

HERE = Path(__file__).resolve().parent


def materialize(dataset: str) -> int:
    src = HERE / "datasets" / f"{dataset}.yaml"
    out = HERE / "datasets" / f"{dataset}.expected.json"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    snapshot = (data.get("meta") or {}).get("db_snapshot", "unknown")

    expected: dict[str, dict] = {}
    ok = failed = skipped = 0
    for item in data["items"]:
        cid, sql = item["id"], item.get("gold_sql")
        if not sql:
            skipped += 1
            continue
        try:
            rows = db.execute(sql).rows
            expected[cid] = {"gold_result": rows, "row_count": len(rows)}
            print(f"OK   {cid:4s} rows={len(rows)}")
            ok += 1
        except Exception as exc:  # noqa: BLE001 — record the failure, keep going
            expected[cid] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"FAIL {cid:4s} {type(exc).__name__}: {str(exc)[:120]}")
            failed += 1

    payload = {
        "dataset": dataset,
        "db_snapshot": snapshot,
        "materialized_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "expected": expected,
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}  ({ok} ok, {failed} failed, {skipped} no-sql) "
          f"against snapshot '{snapshot}'")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Materialize gold_result for an eval dataset.")
    ap.add_argument("--dataset", default="gold_v1", help="dataset stem under eval/datasets/")
    raise SystemExit(materialize(ap.parse_args().dataset))
