"""Materialize eval/datasets/gold_v1.expected.json by running each item's gold_sql live.

eval/run_eval.py (the gold_v1 track) reads its answer key from a SEPARATE file,
``<dataset>.expected.json``, shaped ``{"expected": {"<id>": {"gold_result": [...]}}}``
(see run_eval.py::run, which does ``expected.get(base_id).get("gold_result")``). That is a
DIFFERENT convention from gold_dynamic, whose gold_result lives inline in the YAML and is
written by materialize_gold.py — materialize_gold.py never produces a `.expected.json`
file for ANY dataset, gold_v1 included, so that prerequisite has never actually existed.
This script is the missing other half: it does for gold_v1 what materialize_gold.py does
for gold_dynamic, just written to the file shape run_eval.py actually reads.

Only items that declare a ``gold_sql`` get an entry (some gold_v1 items are routing/
refusal/leakage-only and have no reference SQL — score_gold_exec already treats a missing
entry as "not applicable" via ``expected.get(base_id) or {}``, so omitting them is correct,
not a gap).

Executes through the SAME validated read path the agent uses
(sql_agent.db.Executor -> six-check validator -> live DB), so the "gold" answer is never
hand-typed — the database is always the source of truth for what a query returns.

Run:
    .venv/Scripts/python.exe eval/materialize_gold_v1_expected.py                # write it
    .venv/Scripts/python.exe eval/materialize_gold_v1_expected.py --check        # verify only
    .venv/Scripts/python.exe eval/materialize_gold_v1_expected.py --dataset gold_v1
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from sql_agent.db import db  # noqa: E402

HERE = Path(__file__).resolve().parent


def _json_safe(value):
    """Coerce a DB value into something json.dump can write.

    Mirrors materialize_gold.py::_clean, but targets JSON's native types instead of a
    YAML-round-trippable scalar (no CommentedMap/flow-style concerns here).
    """
    if isinstance(value, decimal.Decimal):
        f = float(value)
        return int(f) if f == int(f) else f
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def materialize(dataset: str, check_only: bool = False) -> int:
    src = HERE / "datasets" / f"{dataset}.yaml"
    out = HERE / "datasets" / f"{dataset}.expected.json"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))

    expected: dict[str, dict] = {}
    ok = failed = empty = skipped = 0

    for item in data["items"]:
        cid, sql = item["id"], item.get("gold_sql")
        if not sql:
            print(f"SKIP {cid:5s} no gold_sql (routing/refusal/leakage-only item)")
            skipped += 1
            continue
        try:
            rows = db.execute(sql).rows
        except Exception as exc:  # noqa: BLE001 — report and keep going
            print(f"FAIL {cid:5s} {type(exc).__name__}: {str(exc)[:110]}")
            failed += 1
            continue

        expected[cid] = {"gold_result": [
            {k: _json_safe(v) for k, v in row.items()} for row in rows
        ]}
        flag = ""
        if not rows:
            empty += 1
            flag = ("   <== EMPTY GOLD: cannot distinguish a correct agent from one "
                    "that returns nothing")
        print(f"OK   {cid:5s} rows={len(rows)}{flag}")
        ok += 1

    if check_only:
        print(f"\n--check: {ok} executable, {failed} failed, {empty} empty, "
              f"{skipped} skipped (no gold_sql). Nothing written.")
        return 1 if failed else 0

    out.write_text(json.dumps({"expected": expected}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nWrote {out}  ({ok} ok, {failed} failed, {empty} empty, {skipped} skipped)")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Materialize the gold_v1 .expected.json answer key run_eval.py reads.")
    ap.add_argument("--dataset", default="gold_v1",
                    help="dataset stem under eval/datasets/ (default: gold_v1)")
    ap.add_argument("--check", action="store_true",
                    help="verify gold_sql executes; write nothing")
    args = ap.parse_args()
    raise SystemExit(materialize(args.dataset, check_only=args.check))
