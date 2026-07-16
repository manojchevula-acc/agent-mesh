"""Standalone check: does the Pattern Retriever (sql_agent/memory/example_index.py)
surface LOGICALLY relevant few-shot examples for gold_dynamic questions — same query
pattern / same tables — rather than merely lexically similar ones?

No LLM calls and no live-agent invocation: only the retriever itself is exercised,
seeded from the approved examples table, so this is fast and free to re-run after every
tuning change (RRF weights, embedding prefix, corpus enrichment, ...).

There is no hand-labeled "the right example for gold question X" mapping (the two
datasets — gold_dynamic questions and the curated example seed set — were built
independently), so retrieval quality is judged with two PROXY signals per gold item:

  TABLE match   — a retrieved example's ``tags`` (table names) intersect gold_tables.
  PATTERN match — a retrieved example's MULTI-TAG SQL pattern set (see
                  sql_agent.memory.sql_pattern.classify_sql — aggregation, comparison,
                  ranking, trend, policy_violation, threshold, top_n/bottom_n, join,
                  window_function, cte, subquery, exists, case_when) OVERLAPS the gold
                  SQL's own pattern set (any shared tag counts, not exact equality —
                  this is what tells "compares two columns" apart from "aggregates one
                  column", the gap that let a lexically-similar-but-logically-different
                  example outrank the right one).

Neither proxy is ground truth by itself (two different tables can legitimately need the
same SQL shape; two examples on the same table can teach a different shape) — read the
--verbose per-question printout, not just the aggregate %, when judging whether a change
actually helped. tables_hint (the schema-retrieval intent signal the live agent pipeline
passes in) is NOT available here, since no agent turn runs — this measures the retriever
in isolation on the question text alone, not its full in-pipeline behaviour.

Prereqs: examples seeded (scripts/seed_examples.py). Defaults VECTOR_BACKEND to "memory"
regardless of .env — a fresh in-RAM index over ~50 examples builds in well under a
second, and this way the check never fights a running API server for the local-mode
QDRANT_PATH file lock (qdrant is exclusive-locked while the server holds it). Pass
--vector-backend qdrant to test the actual persisted index instead (server must be
stopped first in that case).

Every run writes a full record — every retrieved example per question, not just the
console verdict — to eval/results/example_retrieval.yaml (override with --out), so a
retrieval-tuning change can be diffed against a prior run without re-executing it.

Run:
    uv run python eval/check_example_retrieval.py                  # every gold_dynamic item
    uv run python eval/check_example_retrieval.py --id D03
    uv run python eval/check_example_retrieval.py --ids D01,D03,D07
    uv run python eval/check_example_retrieval.py --range D01-D11
    uv run python eval/check_example_retrieval.py --verbose         # also print every retrieved example
    uv run python eval/check_example_retrieval.py --k 5             # override examples_top_k
    uv run python eval/check_example_retrieval.py --out results/before.yaml
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import yaml  # noqa: E402

from sql_agent.config import settings  # noqa: E402
from sql_agent.memory.example_index import _example_tables, rank_examples  # noqa: E402
from sql_agent.memory.examples import all_approved_examples  # noqa: E402
from sql_agent.memory.sql_pattern import classify_sql, sql_pattern  # noqa: E402

OUT_DIR = HERE / "results"
OUT_PATH = OUT_DIR / "example_retrieval.yaml"


def _select(items: list[dict], args) -> list[dict]:
    if args.id:
        ids = {args.id}
    elif args.ids:
        ids = {t.strip() for t in args.ids.split(",") if t.strip()}
    elif args.range:
        if "-" not in args.range:
            raise SystemExit(f"--range expects START-END (e.g. D01-D11), got '{args.range}'")
        a, b = args.range.split("-", 1)
        order = [it["id"] for it in items]
        try:
            i, j = order.index(a.strip()), order.index(b.strip())
        except ValueError as exc:
            raise SystemExit(f"--range id not found in dataset: {exc}") from exc
        if i > j:
            i, j = j, i
        return items[i:j + 1]
    else:
        return items
    selected = [it for it in items if it["id"] in ids]
    missing = ids - {it["id"] for it in selected}
    if missing:
        raise SystemExit(f"id(s) not found in dataset: {sorted(missing)}")
    return selected


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check whether the Pattern Retriever surfaces logically relevant "
                    "few-shot examples for gold_dynamic questions.",
    )
    ap.add_argument("--dataset", default="gold_dynamic")
    sel = ap.add_argument_group("selection (default: every item)")
    sel.add_argument("--id", help="check ONE question by id (e.g. D03)")
    sel.add_argument("--ids", help="comma-separated ids, e.g. D01,D03,D07")
    sel.add_argument("--range", help="inclusive id range, e.g. D01-D11")
    ap.add_argument("--k", type=int, default=None,
                    help="override EXAMPLES_TOP_K for this run")
    ap.add_argument("--vector-backend", default="memory", choices=["memory", "faiss", "qdrant"],
                    help="default 'memory' (in-RAM, no file lock — see module docstring); "
                         "'qdrant' tests the actual persisted index (stop the API server first)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print every retrieved example (question/tables/pattern), "
                         "not just the per-item verdict")
    ap.add_argument("--out", default=None,
                    help=f"where to write the full YAML record (default {OUT_PATH})")
    args = ap.parse_args()
    settings.vector_backend = args.vector_backend
    out_path = Path(args.out) if args.out else OUT_PATH

    src = yaml.safe_load((HERE / "datasets" / f"{args.dataset}.yaml").read_text(encoding="utf-8"))
    items = _select(src["items"], args)

    corpus = all_approved_examples()
    if not corpus:
        raise SystemExit(
            "No approved examples found — run scripts/seed_examples.py first "
            "(and check EXAMPLES_ENABLED / AGENT_DB_DSN in .env)."
        )

    top_k = args.k or settings.examples_top_k
    print(f"Examples corpus : {len(corpus)} approved")
    print(f"Gold questions  : {len(items)} ({args.dataset})")
    print(f"top_k           : {top_k}")
    print(f"dense/bm25 wt   : {settings.examples_dense_weight}/{settings.examples_bm25_weight}")
    print(f"min_score gate  : {settings.examples_min_score}\n")

    def _patterns(sql: str | None) -> set[str]:
        shape = classify_sql(sql)
        return set(shape["patterns"]) if shape else set()

    table_hits = pattern_hits = either_hits = no_retrieval = 0
    records: list[dict] = []
    for it in items:
        question = it["question"]
        gold_tables = set(it.get("gold_tables") or [])
        gold_pattern = sql_pattern(it.get("gold_sql"))       # primary bucket, for display
        gold_patterns = _patterns(it.get("gold_sql"))        # full multi-tag set, for matching

        retrieved = rank_examples(question, corpus, tier="full_dynamic", k=args.k)

        if not retrieved:
            no_retrieval += 1
        table_hit = any(_example_tables(r) & gold_tables for r in retrieved)
        pattern_hit = bool(gold_patterns) and any(
            _patterns(r.get("validated_sql")) & gold_patterns for r in retrieved)
        table_hits += table_hit
        pattern_hits += pattern_hit
        either_hits += (table_hit or pattern_hit)

        verdict = "OK  " if (table_hit or pattern_hit) else "MISS"
        flags = f"table={'Y' if table_hit else 'n'} pattern={'Y' if pattern_hit else 'n'}"
        print(f"[{verdict}] {it['id']:5s} {flags}  gold_pattern={gold_pattern or '?':11s} "
              f"| {question[:70]}")

        retrieved_records = []
        for r in retrieved:
            r_tables = sorted(_example_tables(r))
            r_patterns = _patterns(r.get("validated_sql"))
            r_pattern = sql_pattern(r.get("validated_sql"))
            hit = bool((_example_tables(r) & gold_tables) or (r_patterns & gold_patterns))
            retrieved_records.append({"question": r["question"], "tables": r_tables,
                                      "pattern": r_pattern, "patterns": sorted(r_patterns),
                                      "hit": hit})
            if args.verbose:
                mark = "+" if hit else " "
                print(f"       {mark}  [{','.join(sorted(r_patterns)) or '?':30s}] "
                      f"{', '.join(r_tables) or '-':30s} {r['question'][:55]}")
        if args.verbose and not retrieved:
            print("         (no examples retrieved — confidence gate suppressed all, "
                  "or the corpus/index is empty)")

        records.append({
            "id": it["id"], "question": question,
            "gold_tables": sorted(gold_tables), "gold_pattern": gold_pattern,
            "table_hit": table_hit, "pattern_hit": pattern_hit,
            "either_hit": table_hit or pattern_hit,
            "retrieved": retrieved_records,
        })

    n = len(items)
    summary = {}
    if n:
        summary = {
            "table_match": {"hits": table_hits, "n": n, "pct": round(100 * table_hits / n, 1)},
            "pattern_match": {"hits": pattern_hits, "n": n, "pct": round(100 * pattern_hits / n, 1)},
            "either_match": {"hits": either_hits, "n": n, "pct": round(100 * either_hits / n, 1)},
            "no_retrieval": no_retrieval,
        }
        print(f"\n{'-' * 70}")
        print(f"table match   : {table_hits}/{n} ({100 * table_hits / n:.0f}%)")
        print(f"pattern match : {pattern_hits}/{n} ({100 * pattern_hits / n:.0f}%)")
        print(f"either match  : {either_hits}/{n} ({100 * either_hits / n:.0f}%)  <- headline")
        if no_retrieval:
            print(f"no examples   : {no_retrieval}/{n} question(s) got zero examples "
                  f"(confidence gate or empty corpus)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump({
        "dataset": args.dataset,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "corpus_size": len(corpus),
        "top_k": top_k,
        "examples_dense_weight": settings.examples_dense_weight,
        "examples_bm25_weight": settings.examples_bm25_weight,
        "examples_min_score": settings.examples_min_score,
        "vector_backend": args.vector_backend,
        "summary": summary,
        "items": records,
    }, sort_keys=False, allow_unicode=True, width=4096, default_flow_style=False),
        encoding="utf-8")
    print(f"\nWrote {out_path}  ({len(records)} question(s) recorded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
