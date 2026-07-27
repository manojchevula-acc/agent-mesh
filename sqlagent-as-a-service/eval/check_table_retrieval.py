"""Standalone check: does the schema/table retriever (sql_agent/semantic_layer/selector.py)
surface the RIGHT tables for a gold_dynamic question — the tables the reference SQL
actually reads — rather than plausible-looking but wrong ones?

No LLM calls and no live-agent invocation: only ``select_tables`` / ``ranked_core`` are
exercised, driven by the gold question TEXT alone, so this is fast and free to re-run
after every retrieval-tuning change (RRF weights, embedding prefix/model, embedding_top_k,
table search_terms, ...). No database is needed either — retrieval reads schema.yaml and
the embedding index only.

GROUND TRUTH, NOT A PROXY ----------------------------------------------------
Unlike eval/check_example_retrieval.py (whose gold has no hand-labelled "right example"
mapping, so it falls back to table/pattern PROXY signals), every gold_dynamic item carries
``gold_tables`` — the exact tables its ``gold_sql`` reads, parsed by eval/sql_introspect.py
and verified against the SQL by materialize_gold.py --check so they cannot drift. That IS
the retrieval label. So table retrieval can be scored directly.

WHAT IS MEASURED (recall-first, because the selector is recall-oriented by design) -------
The selector returns CANDIDATES; the schema-link planner makes the precise cut. A gold
table MISSING from the candidates is a hard failure (generation is starved and can never
recover); extra candidates are only token cost. So the headline is RECALL, and three
distinct sets are scored per question to separate a genuine ranking win from a rescue:

  ranked core   (ranked_core)                     — the RRF top-K, retrieval quality ALONE.
  planner set   (select_tables apply_closure=F)   — core + BASE-table join closure; what
                                                     the schema-link planner chooses among.
  generator set (select_tables apply_closure=T)   — core + full join closure; what the
                                                     tier-3 generator ultimately SEES.

A gold table absent from the ranked core but present in the generator set was RESCUED BY
CLOSURE — retrieval itself missed it; the deterministic join net caught it. Tracking that
separately stops a closure rescue from hiding a ranking regression behind a green recall.

  full recall   — ALL of a question's gold_tables are present (the set the generator sees
                  is complete). This is the number that predicts whether generation CAN be
                  correct on tables alone.
  table recall  — fraction of gold tables present (partial credit, for triage).
  precision     — gold ∩ core / |core|: how much of the ranked slice is signal vs noise
                  (recall-first ≠ noise-free; a bloated candidate set costs tokens/accuracy).

tables_hint (the intent classifier's signal the live pipeline force-includes) is NOT
passed here: this measures the retriever on the QUESTION alone, its weakest, most honest
configuration. Pass --with-hint gold to additionally force-include each item's own
gold_tables as the hint — an upper bound showing what the closure/planner do once the
right seed is present (it makes recall trivially complete by construction, so read it as a
closure/planner diagnostic, not a retrieval score).

Every run writes a full record — the three sets and per-table verdicts for every question,
not just the console summary — to eval/results/table_retrieval/table_retrieval.yaml (override --out),
plus a readable per-question eval/results/table_retrieval/table_retrieval.md alongside it, so a
tuning change can be diffed against a prior run without re-executing it.

Run:
    uv run python eval/check_table_retrieval.py                    # every gold_dynamic item
    uv run python eval/check_table_retrieval.py --id D11
    uv run python eval/check_table_retrieval.py --ids D07,D11,D23
    uv run python eval/check_table_retrieval.py --range D01-D11
    uv run python eval/check_table_retrieval.py --verbose          # per-table hit/miss detail
    uv run python eval/check_table_retrieval.py --k 12             # override EMBEDDING_TOP_K
    uv run python eval/check_table_retrieval.py --with-hint gold   # closure/planner upper bound
    uv run python eval/check_table_retrieval.py --out results/before.yaml
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
from sql_agent.semantic_layer.loader import ALLOWED_TABLES, table_columns  # noqa: E402
from sql_agent.semantic_layer.selector import ranked_core, select_tables  # noqa: E402

OUT_DIR = HERE / "results" / "table_retrieval"
OUT_PATH = OUT_DIR / "table_retrieval.yaml"


def _select(items: list[dict], args) -> list[dict]:
    """Same id/ids/range selection contract as check_example_retrieval.py."""
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


def _rank_of(core: list[str], table: str) -> int | None:
    """1-based position of ``table`` in the ranked core, or None if absent."""
    return core.index(table) + 1 if table in core else None


def _render_markdown(doc: dict, summary: dict, records: list[dict]) -> str:
    """A human-readable per-question report — the readable sibling of the YAML record."""
    L = [f"# Table Retrieval — {doc['dataset']}", "",
         f"_Generated {doc['generated_at']} · schema tables {doc['schema_tables']} · "
         f"embedding_top_k {doc['embedding_top_k']} · rrf_k {doc['rrf_k']} · "
         f"backend {doc['vector_backend']} · tables_hint {doc['tables_hint']}_", ""]

    if summary:
        def _pct(d):
            return f"{d['hits']}/{d['n']} ({d['pct']}%)"
        frd = summary["full_recall_depth"]
        rk = summary.get("recall_at_k") or {}
        L += ["## Summary", "", "| Metric | Value |", "| --- | --- |",
              f"| core full recall | {_pct(summary['core_full_recall'])} |",
              f"| generator full recall (headline) | {_pct(summary['generator_full_recall'])} |",
              f"| column full recall (headline) | {_pct(summary['column_full_recall'])} "
              f"· mean coverage {summary['mean_column_coverage']} |",
              f"| table recall (core) | {_pct(summary['table_recall_core'])} |",
              f"| table recall (generator) | {_pct(summary['table_recall_generator'])} |",
              f"| questions rescued by closure | {summary['questions_rescued_by_closure']} |",
              f"| mean core size | {summary['mean_core_size']} |",
              f"| mean core precision | {summary['mean_core_precision']} |",
              f"| full-recall depth | mean {frd['mean']}, max {frd['max']} "
              f"(over {frd['answerable']}/{frd['n']}) |"]
        if rk:
            L.append("| recall@K (full) | "
                     + ", ".join(f"K={k}:{v}%" for k, v in rk.items()) + " |")
        L.append("")

    L += ["## Per-question", ""]
    for r in records:
        verdict = "MISS" if r["missing"] else ("RESQ" if r["rescued_by_closure"] else "OK")
        L += [f"### `{verdict}` {r['id']} — {r['question']}", "",
              f"- **gold tables:** {', '.join(r['gold_tables']) or '—'}",
              f"- **core full recall:** {'yes' if r['core_full_recall'] else 'no'} · "
              f"**generator full recall:** {'yes' if r['generator_full_recall'] else 'no'}"]
        if r["missing"]:
            L.append(f"- **missing (never seen):** {', '.join(r['missing'])}")
        if r["rescued_by_closure"]:
            L.append(f"- **rescued by closure:** {', '.join(r['rescued_by_closure'])}")
        if r["column_coverage"] is not None:
            line = (f"- **column coverage:** {r['column_coverage']} "
                    f"(full: {'yes' if r['column_full_recall'] else 'no'})")
            if r["missing_columns"]:
                line += f" · missing columns: {', '.join(r['missing_columns'])}"
            L.append(line)
        if r["full_recall_depth"] is not None:
            L.append(f"- **full-recall depth:** {r['full_recall_depth']}")
        if r["tables"]:
            L += ["", "| gold table | found via | rank in core |", "| --- | --- | --- |"]
            for t in r["tables"]:
                L.append(f"| {t['table']} | {t['found_via']} | "
                         f"{t['rank_in_core'] if t['rank_in_core'] else '—'} |")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check whether the schema/table retriever surfaces the gold tables "
                    "for gold_dynamic questions.",
    )
    ap.add_argument("--dataset", default="gold_dynamic")
    sel = ap.add_argument_group("selection (default: every item)")
    sel.add_argument("--id", help="check ONE question by id (e.g. D11)")
    sel.add_argument("--ids", help="comma-separated ids, e.g. D07,D11,D23")
    sel.add_argument("--range", help="inclusive id range, e.g. D01-D11")
    ap.add_argument("--k", type=int, default=None,
                    help="override EMBEDDING_TOP_K (candidate pool size) for this run")
    ap.add_argument("--with-hint", choices=["gold"], default=None,
                    help="force-include each item's gold_tables as tables_hint — a "
                         "closure/planner UPPER BOUND, not a retrieval score (see docstring)")
    ap.add_argument("--vector-backend", default="memory",
                    choices=["memory", "faiss", "qdrant"],
                    help="default 'memory' (in-RAM, no file lock); 'qdrant' tests the "
                         "actual persisted index (stop the API server first)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print per-table hit/miss (rank, which set caught it), "
                         "not just the per-item verdict")
    ap.add_argument("--out", default=None,
                    help=f"where to write the full YAML record (default {OUT_PATH})")
    args = ap.parse_args()

    settings.vector_backend = args.vector_backend
    # The selector no-ops to the FULL schema unless retrieval is on — which would score a
    # trivial 100% recall and measure nothing. Force it on for the duration of the check.
    settings.schema_retrieval_enabled = True
    out_path = Path(args.out) if args.out else OUT_PATH

    from sql_agent.semantic_layer.embeddings import get_backend
    if get_backend() is None:
        print("WARNING: EMBEDDING_BACKEND=none — dense retrieval is off; this scores the "
              "BM25-only path. Set EMBEDDING_BACKEND=local to test hybrid retrieval.\n")

    src = yaml.safe_load((HERE / "datasets" / f"{args.dataset}.yaml").read_text(encoding="utf-8"))
    items = _select(src["items"], args)

    top_k = args.k or settings.embedding_top_k
    print(f"Schema tables   : {len(ALLOWED_TABLES)} allowed")
    print(f"Gold questions  : {len(items)} ({args.dataset})")
    print(f"embedding_top_k : {top_k}")
    print(f"rrf_k           : {settings.rrf_k}")
    print(f"tables_hint     : {'gold_tables (upper bound)' if args.with_hint else 'none (question only)'}\n")

    core_full = gen_full = 0          # questions with ALL gold tables present in that set
    table_gold = table_core = table_gen = 0   # per-TABLE totals (partial-credit recall)
    rescued_questions = 0             # questions where closure caught a table the core missed
    core_sizes: list[int] = []
    precision_sum = 0.0
    records: list[dict] = []

    # ---- H1 (column-level context sufficiency) + M1 (rank-aware) accumulators ----------
    # table presence is necessary but not sufficient: generation still fails if a column
    # the gold SQL reads lives in no retrieved table. COLS maps every governed table to its
    # declared columns (the same authority the validator's column-binding check uses).
    COLS = table_columns()
    col_n = col_full = 0              # questions carrying gold_columns / with ALL of them available
    col_cov_sum = 0.0                 # Σ per-question column coverage (for the mean)
    depths: list[int] = []            # full-recall depth per answerable question (finite only)
    rank_data: list[tuple[set, list]] = []   # (gold_set, ranked core) kept for the Recall@K sweep

    for it in items:
        question = it["question"]
        gold = [t for t in (it.get("gold_tables") or [])]
        gold_set = set(gold)
        # Warn (once, in the record) if a gold table isn't even in the schema allow-list —
        # that's a dataset/schema drift bug, not a retrieval miss.
        unknown = sorted(gold_set - set(ALLOWED_TABLES))

        hint = list(gold) if args.with_hint == "gold" else None
        core = ranked_core(question, tables_hint=hint, top_k=args.k) or []
        planner_set = select_tables(question, tables_hint=hint, top_k=args.k,
                                    apply_closure=False)
        gen_set = select_tables(question, tables_hint=hint, top_k=args.k,
                                apply_closure=True)

        core_hit = gold_set <= set(core)
        gen_hit = gold_set <= gen_set
        rescued = sorted((gold_set & gen_set) - set(core))   # missed by ranking, saved by closure
        missing = sorted(gold_set - gen_set)                 # true misses: generator never sees them

        core_full += core_hit
        gen_full += gen_hit
        rescued_questions += bool(rescued)
        table_gold += len(gold_set)
        table_core += len(gold_set & set(core))
        table_gen += len(gold_set & gen_set)
        core_sizes.append(len(core))
        precision_sum += (len(gold_set & set(core)) / len(core)) if core else 0.0

        # ---- H1: does the generator's table set actually CONTAIN the columns gold reads? --
        # gold_columns is pre-parsed from gold_sql and verified by materialize_gold --check,
        # so it is a trustworthy label; fall back to skipping the item if absent.
        gold_cols = {c.lower() for c in (it.get("gold_columns") or [])}
        available_cols: set[str] = set()
        for t in gen_set:
            available_cols |= COLS.get(t, set())
        col_coverage = (len(gold_cols & available_cols) / len(gold_cols)) if gold_cols else None
        col_full_recall = (gold_cols <= available_cols) if gold_cols else None
        missing_cols = sorted(gold_cols - available_cols) if gold_cols else []
        if gold_cols:
            col_n += 1
            col_full += bool(col_full_recall)
            col_cov_sum += col_coverage

        # ---- M1: full-recall depth = shallowest K at which ALL gold tables are in the core.
        # For a set-consuming planner the meaningful rank signal is the DEEPEST gold table,
        # not the first hit — it is exactly the minimum embedding_top_k this question needs.
        gold_ranks = [_rank_of(core, t) for t in gold_set]
        full_recall_depth = max(gold_ranks) if gold_ranks and all(r is not None for r in gold_ranks) else None
        if full_recall_depth is not None:
            depths.append(full_recall_depth)
        rank_data.append((set(gold_set), list(core)))

        if missing:
            verdict = "MISS"
        elif rescued:
            verdict = "RESQ"   # complete, but only because closure rescued a ranking miss
        else:
            verdict = "OK  "
        print(f"[{verdict}] {it['id']:5s} core={'Y' if core_hit else 'n'} "
              f"gen={'Y' if gen_hit else 'n'}  "
              f"|core|={len(core):2d}  gold={','.join(gold) or '-'}"
              f"{'  MISSING=' + ','.join(missing) if missing else ''}"
              f"{'  rescued=' + ','.join(rescued) if rescued and not missing else ''}")

        table_records = []
        for t in gold:
            where = ("core" if t in core else
                     "closure" if t in gen_set else
                     "MISSING")
            table_records.append({"table": t, "in_core": t in core,
                                  "rank_in_core": _rank_of(core, t),
                                  "in_generator_set": t in gen_set, "found_via": where})
            if args.verbose:
                r = _rank_of(core, t)
                mark = "+" if t in core else ("~" if t in gen_set else " ")
                print(f"       {mark}  {t:34s} {where:8s}"
                      f"{'  rank ' + str(r) if r else ''}")
        if args.verbose and unknown:
            print(f"       !  not in schema allow-list: {', '.join(unknown)}")

        records.append({
            "id": it["id"], "question": question,
            "gold_tables": gold,
            "core_full_recall": core_hit, "generator_full_recall": gen_hit,
            "ranked_core": list(core), "planner_set": sorted(planner_set),
            "generator_set": sorted(gen_set),
            "rescued_by_closure": rescued, "missing": missing,
            "unknown_gold_tables": unknown,
            "gold_columns": sorted(gold_cols),
            "column_coverage": round(col_coverage, 3) if col_coverage is not None else None,
            "column_full_recall": col_full_recall,
            "missing_columns": missing_cols,
            "full_recall_depth": full_recall_depth,
            "tables": table_records,
        })

    n = len(items)
    summary = {}
    if n:
        # M1: full-recall@K sweep — fraction of questions whose gold tables are ALL inside
        # the ranked core's top-K, over a compact set of K's capped at the widest core seen.
        max_core = max((len(c) for _, c in rank_data), default=0)
        k_grid = sorted({k for k in (1, 3, 5, 8, 10, top_k) if 1 <= k <= max_core})
        recall_at_k = {
            k: round(100 * sum(1 for g, c in rank_data if g <= set(c[:k])) / n, 1)
            for k in k_grid
        }
        summary = {
            "core_full_recall": {"hits": core_full, "n": n, "pct": round(100 * core_full / n, 1)},
            "generator_full_recall": {"hits": gen_full, "n": n, "pct": round(100 * gen_full / n, 1)},
            # H1 (headline): can generation even succeed on COLUMNS, not just tables?
            "column_full_recall": {"hits": col_full, "n": col_n,
                                   "pct": round(100 * col_full / col_n, 1) if col_n else 0.0},
            "mean_column_coverage": round(col_cov_sum / col_n, 3) if col_n else 0.0,
            "table_recall_core": {"hits": table_core, "n": table_gold,
                                  "pct": round(100 * table_core / table_gold, 1) if table_gold else 0.0},
            "table_recall_generator": {"hits": table_gen, "n": table_gold,
                                       "pct": round(100 * table_gen / table_gold, 1) if table_gold else 0.0},
            "questions_rescued_by_closure": rescued_questions,
            "mean_core_size": round(sum(core_sizes) / n, 1),
            "mean_core_precision": round(precision_sum / n, 3),
            # M1 (diagnostic): how deep the ranking must go, and the Recall@K curve.
            "full_recall_depth": {"mean": round(sum(depths) / len(depths), 2) if depths else None,
                                  "answerable": len(depths), "n": n,
                                  "max": max(depths) if depths else None},
            "recall_at_k": recall_at_k,
        }
        print(f"\n{'-' * 70}")
        print(f"core full recall  : {core_full}/{n} ({100 * core_full / n:.0f}%)  "
              f"ranking alone surfaced every gold table")
        print(f"gen full recall   : {gen_full}/{n} ({100 * gen_full / n:.0f}%)  "
              f"<- headline (what the generator sees is complete)")
        if col_n:
            print(f"column full recall: {col_full}/{col_n} ({100 * col_full / col_n:.0f}%)  "
                  f"<- headline (gold columns all present; mean coverage {col_cov_sum / col_n:.2f})")
        print(f"table recall core : {table_core}/{table_gold} "
              f"({100 * table_core / table_gold:.0f}%)" if table_gold else "table recall core : n/a")
        print(f"rescued by closure: {rescued_questions}/{n} question(s) needed the join net "
              f"(ranking missed a gold table)")
        print(f"mean |core|       : {sum(core_sizes) / n:.1f} tables   "
              f"mean core precision: {precision_sum / n:.2f}")
        if depths:
            print(f"full-recall depth : mean {sum(depths) / len(depths):.1f}, max {max(depths)} "
                  f"(over {len(depths)}/{n} fully-recalled question(s))")
        if recall_at_k:
            print("recall@K (full)   : "
                  + "  ".join(f"K={k}:{pct:.0f}%" for k, pct in recall_at_k.items()))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "dataset": args.dataset,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema_tables": len(ALLOWED_TABLES),
        "embedding_top_k": top_k,
        "rrf_k": settings.rrf_k,
        "vector_backend": args.vector_backend,
        "tables_hint": "gold" if args.with_hint else "none",
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
