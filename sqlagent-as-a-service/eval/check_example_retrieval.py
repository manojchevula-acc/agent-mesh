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
console verdict — to eval/results/example_retrieval/example_retrieval.yaml (override with --out),
plus a readable per-question eval/results/example_retrieval/example_retrieval.md alongside it, so a
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

OUT_DIR = HERE / "results" / "example_retrieval"
OUT_PATH = OUT_DIR / "example_retrieval.yaml"


def _jaccard(a: set, b: set) -> float:
    """|A n B| / |A u B|; two empty sets score 0.0 here (absence of signal, not a match)."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _ndcg(grades: list[float]) -> float:
    """NDCG over a ranked list of graded relevances in [0,1], log2 positional discount.
    1.0 when the most-relevant examples are already first; 0.0 when there is no signal."""
    import math
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(grades))
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(sorted(grades, reverse=True)))
    return dcg / idcg if idcg else 0.0


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


def _render_markdown(doc: dict, summary: dict, records: list[dict]) -> str:
    """A human-readable per-question report — the readable sibling of the YAML record."""
    L = [f"# Few-shot Example Retrieval — {doc['dataset']}", "",
         f"_Generated {doc['generated_at']} · corpus {doc['corpus_size']} · top_k {doc['top_k']} · "
         f"dense/bm25 {doc['examples_dense_weight']}/{doc['examples_bm25_weight']} · "
         f"min_score {doc['examples_min_score']} · backend {doc['vector_backend']}_", ""]

    if summary:
        def _pct(d):
            return f"{d['hits']}/{d['n']} ({d['pct']}%)"
        L += ["## Summary", "", "| Metric | Value |", "| --- | --- |",
              f"| table match | {_pct(summary['table_match'])} |",
              f"| pattern match | {_pct(summary['pattern_match'])} |",
              f"| either match | {_pct(summary['either_match'])} |",
              f"| operator coverage (headline) | {summary['operator_coverage']} |",
              f"| strong hit (headline) | {_pct(summary['strong_hit'])} |",
              f"| pattern Jaccard | {summary['pattern_jaccard']} |",
              f"| table set coverage | {summary['table_set_coverage']} |",
              f"| precision@k | {summary['precision_at_k']} |",
              f"| MRR | {summary['mrr']} |",
              f"| NDCG@k | {summary['ndcg_at_k']} |",
              f"| no retrieval | {summary['no_retrieval']} |", ""]

    L += ["## Per-question", ""]
    for r in records:
        verdict = "OK" if r["either_hit"] else "MISS"
        oc = r["operator_coverage"]
        L += [f"### `{verdict}` {r['id']} — {r['question']}", "",
              f"- **gold pattern:** {r['gold_pattern'] or '—'} · "
              f"**gold tables:** {', '.join(r['gold_tables']) or '—'}",
              f"- **table hit:** {'Y' if r['table_hit'] else 'n'} · "
              f"**pattern hit:** {'Y' if r['pattern_hit'] else 'n'} · "
              f"**strong hit:** {'Y' if r['strong_hit'] else 'n'}",
              f"- **operator coverage:** {oc if oc is not None else '—'} · "
              f"**P@k:** {r['precision_at_k']} · **MRR:** {r['mrr']} · "
              f"**NDCG@k:** {r['ndcg_at_k']} · **pattern-Jaccard:** {r['pattern_jaccard']}"]
        if r["retrieved"]:
            L += ["", "| # | hit | patterns | tables | example question |",
                  "| --- | --- | --- | --- | --- |"]
            for i, e in enumerate(r["retrieved"], 1):
                q = (e["question"] or "").replace("|", "\\|")
                L.append(f"| {i} | {'✓' if e['hit'] else ''} | "
                         f"{', '.join(e['patterns']) or '—'} | "
                         f"{', '.join(e['tables']) or '—'} | {q} |")
        else:
            L += ["", "_(no examples retrieved — confidence gate suppressed all)_"]
        L.append("")
    return "\n".join(L)


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
    # H2/M2/M3 accumulators — set-level usefulness and rank-aware quality (proxy labels).
    strong_hits = 0
    op_cov_sum = tbl_cov_sum = 0.0
    op_cov_n = 0
    p_at_k_sum = mrr_sum = ndcg_sum = pat_jaccard_sum = 0.0
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
        rel_flags: list[bool] = []          # per-example proxy relevance (table OR pattern)
        grades: list[float] = []            # graded relevance in [0,1], for NDCG
        pat_jaccs: list[float] = []
        union_patterns: set[str] = set()
        union_tables: set[str] = set()
        strong_hit = False
        for r in retrieved:
            rt_set = _example_tables(r)
            rp_set = _patterns(r.get("validated_sql"))
            r_tables = sorted(rt_set)
            r_pattern = sql_pattern(r.get("validated_sql"))
            t_ok, p_ok = bool(rt_set & gold_tables), bool(rp_set & gold_patterns)
            hit = t_ok or p_ok
            rel_flags.append(hit)
            pj = _jaccard(rp_set, gold_patterns)
            pat_jaccs.append(pj)
            grades.append(0.5 * (_jaccard(rt_set, gold_tables) + pj))
            union_patterns |= rp_set
            union_tables |= rt_set
            strong_hit = strong_hit or (t_ok and p_ok)
            retrieved_records.append({"question": r["question"], "tables": r_tables,
                                      "pattern": r_pattern, "patterns": sorted(rp_set),
                                      "hit": hit})
            if args.verbose:
                mark = "+" if hit else " "
                print(f"       {mark}  [{','.join(sorted(rp_set)) or '?':30s}] "
                      f"{', '.join(r_tables) or '-':30s} {r['question'][:55]}")
        if args.verbose and not retrieved:
            print("         (no examples retrieved — confidence gate suppressed all, "
                  "or the corpus/index is empty)")

        # H2 (headline): does the retrieved SET collectively demonstrate EVERY SQL construct
        # the gold query uses? A per-example "looks similar" hit can still leave the prompt
        # missing a construct the answer needs; this measures the set, not each example.
        op_coverage = (len(gold_patterns & union_patterns) / len(gold_patterns)
                       if gold_patterns else None)
        tbl_coverage = (len(gold_tables & union_tables) / len(gold_tables)
                        if gold_tables else None)
        # M3: rank-aware quality over the proxy relevance labels (order the model sees).
        kk = len(retrieved)
        p_at_k = sum(rel_flags) / kk if kk else 0.0
        mrr = next((1 / (i + 1) for i, f in enumerate(rel_flags) if f), 0.0)
        ndcg = _ndcg(grades)
        pat_jaccard = max(pat_jaccs) if pat_jaccs else 0.0   # M2: best graded pattern overlap

        strong_hits += strong_hit
        p_at_k_sum += p_at_k
        mrr_sum += mrr
        ndcg_sum += ndcg
        pat_jaccard_sum += pat_jaccard
        if op_coverage is not None:
            op_cov_sum += op_coverage
            op_cov_n += 1
        if tbl_coverage is not None:
            tbl_cov_sum += tbl_coverage

        records.append({
            "id": it["id"], "question": question,
            "gold_tables": sorted(gold_tables), "gold_pattern": gold_pattern,
            "table_hit": table_hit, "pattern_hit": pattern_hit,
            "either_hit": table_hit or pattern_hit,
            "strong_hit": strong_hit,
            "operator_coverage": round(op_coverage, 3) if op_coverage is not None else None,
            "table_set_coverage": round(tbl_coverage, 3) if tbl_coverage is not None else None,
            "pattern_jaccard": round(pat_jaccard, 3),
            "precision_at_k": round(p_at_k, 3),
            "mrr": round(mrr, 3),
            "ndcg_at_k": round(ndcg, 3),
            "retrieved": retrieved_records,
        })

    n = len(items)
    summary = {}
    if n:
        summary = {
            "table_match": {"hits": table_hits, "n": n, "pct": round(100 * table_hits / n, 1)},
            "pattern_match": {"hits": pattern_hits, "n": n, "pct": round(100 * pattern_hits / n, 1)},
            "either_match": {"hits": either_hits, "n": n, "pct": round(100 * either_hits / n, 1)},
            # H2 (headline): fraction of the gold query's constructs the retrieved SET teaches.
            "operator_coverage": round(op_cov_sum / op_cov_n, 3) if op_cov_n else 0.0,
            # M2 (headline-secondary): >=1 example matching on BOTH tables AND pattern.
            "strong_hit": {"hits": strong_hits, "n": n, "pct": round(100 * strong_hits / n, 1)},
            # M2/M3 (diagnostic): graded overlap and rank-aware quality over proxy labels.
            "pattern_jaccard": round(pat_jaccard_sum / n, 3),
            "table_set_coverage": round(tbl_cov_sum / n, 3),
            "precision_at_k": round(p_at_k_sum / n, 3),
            "mrr": round(mrr_sum / n, 3),
            "ndcg_at_k": round(ndcg_sum / n, 3),
            "no_retrieval": no_retrieval,
        }
        print(f"\n{'-' * 70}")
        print(f"table match   : {table_hits}/{n} ({100 * table_hits / n:.0f}%)")
        print(f"pattern match : {pattern_hits}/{n} ({100 * pattern_hits / n:.0f}%)")
        print(f"either match  : {either_hits}/{n} ({100 * either_hits / n:.0f}%)  <- headline")
        if op_cov_n:
            print(f"operator cover: {100 * op_cov_sum / op_cov_n:.0f}%  <- headline "
                  f"(retrieved set teaches every gold construct)")
        print(f"strong hit    : {strong_hits}/{n} ({100 * strong_hits / n:.0f}%)  "
              f"(>=1 example matching table AND pattern)")
        print(f"P@k / MRR     : {p_at_k_sum / n:.2f} / {mrr_sum / n:.2f}   "
              f"NDCG@k {ndcg_sum / n:.2f}   pattern-Jaccard {pat_jaccard_sum / n:.2f}")
        if no_retrieval:
            print(f"no examples   : {no_retrieval}/{n} question(s) got zero examples "
                  f"(confidence gate or empty corpus)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
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
    }
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                                       width=4096, default_flow_style=False), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_render_markdown(doc, summary, records), encoding="utf-8")
    print(f"\nWrote {out_path}\n      {md_path}  ({len(records)} question(s) recorded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
