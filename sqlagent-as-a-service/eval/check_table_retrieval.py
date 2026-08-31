"""Standalone check: does table retrieval surface the RIGHT tables for a gold_dynamic
question — the tables the reference SQL actually reads — rather than plausible-looking
but wrong ones?

TWO RETRIEVERS, ONE METHODOLOGY (--source) ------------------------------------------------
There are two independent table-retrieval paths in the live pipeline, and which one a
question actually goes through is decided by _plan_schema() in
sql_agent/routing/query_engine.py: ``set(kg.tables) if kg is not None and kg.tables else
select_tables(...)`` — i.e. the KG (sql_agent/kg/retrieval.py) is the PRIMARY path whenever
it is enabled and populated; the plain embedding/BM25 selector (selector.py) is only the
FALLBACK for when the KG is unavailable or empty.

  --source selector (default, unchanged)  — exercises ``select_tables`` / ``ranked_core``
                                             directly. This is what an agent WITHOUT the KG
                                             (KG_ENABLED=false, or the fallback path) sees.
  --source kg                             — exercises ``sql_agent.kg.retrieval.lookup``
                                             directly: the SAME candidate set a live KG-
                                             enabled dynamic query actually gets, including
                                             the S5 ranked-table signal, S6 one-hop base-
                                             table join closure (KG_JOIN_CLOSURE_ENABLED) and
                                             S7 join-path bridging, at the current
                                             KG_CANDIDATE_TOP_K cut.
  --source both                           — runs both over the same items, writes both
                                             reports, and prints a per-question delta so a
                                             regression/rescue on ONE retriever never hides
                                             behind an unrelated change on the other.

No LLM calls and no live-agent invocation either way — the KG source does exercise the KG
backend (Neo4j, or whichever KG_BACKEND is configured) since that's the retriever, but
nothing downstream of retrieval (schema-link planner, generation, validator) runs. Fast and
free to re-run after any retrieval-tuning change (RRF weights, embedding prefix/model,
embedding_top_k, KG_CANDIDATE_TOP_K, KG_JOIN_CLOSURE_ENABLED, KG term/scenario thresholds).

GROUND TRUTH, NOT A PROXY ----------------------------------------------------
Unlike eval/check_example_retrieval.py (whose gold has no hand-labelled "right example"
mapping, so it falls back to table/pattern PROXY signals), every gold_dynamic item carries
``gold_tables`` — the exact tables its ``gold_sql`` reads, parsed by eval/sql_introspect.py
and verified against the SQL by materialize_gold.py --check so they cannot drift. That IS
the retrieval label. So table retrieval can be scored directly.

WHAT IS MEASURED (recall-first, because both retrievers are recall-oriented by design) ----
The retriever returns CANDIDATES; the schema-link planner makes the precise cut. A gold
table MISSING from the candidates is a hard failure (generation is starved and can never
recover); extra candidates are only token cost. So the headline is RECALL, and — per
source — two sets are scored per question to separate a genuine ranking win from a rescue:

  selector source:
    ranked core   (ranked_core)                     — the RRF top-K, retrieval quality ALONE.
    generator set (select_tables apply_closure=T)   — core + full join closure; what the
                                                        tier-3 generator ultimately SEES.
  kg source:
    ranked core   — kg.tables MINUS any table whose ONLY attribution signal is
                    join_closure (SIGNAL_CLOSURE) — i.e. the fused S1-S5 signal set alone,
                    before S6/S7 graph expansion. Still score-ordered (kg.tables is sorted
                    by fused score), so the same rank-depth/recall@K diagnostics apply.
    generator set — kg.tables — the exact candidate set _plan_schema() hands the schema-
                    link planner in production.

A gold table absent from the ranked core but present in the generator set was RESCUED BY
CLOSURE — retrieval itself missed it; the deterministic join/graph net caught it. Tracking
that separately stops a closure rescue from hiding a ranking regression behind a green
recall (this is exactly the distinction that mattered when KG_JOIN_CLOSURE_ENABLED was
found off: KG "core" recall looked fine because the closure rescue was invisible).

  full recall   — ALL of a question's gold_tables are present (the set the generator sees
                  is complete). This is the number that predicts whether generation CAN be
                  correct on tables alone.
  table recall  — fraction of gold tables present (partial credit, for triage).
  precision     — gold ∩ core / |core|: how much of the ranked slice is signal vs noise
                  (recall-first ≠ noise-free; a bloated candidate set costs tokens/accuracy).

tables_hint (the intent classifier's signal the live pipeline force-includes) is NOT
passed here: this measures the retriever on the QUESTION alone, its weakest, most honest
configuration. Pass --with-hint gold to additionally force-include each item's own
gold_tables as the hint — an upper bound showing what closure/the planner do once the
right seed is present (it makes recall trivially complete by construction for the selector
source, and near-trivial for kg since tables_hint is additive there too — read it as a
closure/planner diagnostic, not a retrieval score).

Every run writes a full record — the sets and per-table verdicts for every question, not
just the console summary — to eval/results/table_retrieval/table_retrieval.yaml for the
selector source, table_retrieval_kg.yaml for the kg source (override with --out when
running a single source), plus a readable per-question .md alongside each, so a tuning
change can be diffed against a prior run without re-executing it.

Run:
    uv run python eval/check_table_retrieval.py                       # selector, every item (default, unchanged)
    uv run python eval/check_table_retrieval.py --source kg           # KG retrieval, every item
    uv run python eval/check_table_retrieval.py --source both --id D54
    uv run python eval/check_table_retrieval.py --source kg --ids D54,D60,D61,D65
    uv run python eval/check_table_retrieval.py --source both --verbose   # + per-table hit/miss + KG signals
    uv run python eval/check_table_retrieval.py --source kg --kg-k 6  # override KG_CANDIDATE_TOP_K for this run
    uv run python eval/check_table_retrieval.py --k 12                # override EMBEDDING_TOP_K (selector only)
    uv run python eval/check_table_retrieval.py --with-hint gold      # closure/planner upper bound
    uv run python eval/check_table_retrieval.py --out results/before.yaml   # single-source only
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
OUT_PATH_KG = OUT_DIR / "table_retrieval_kg.yaml"


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


# --------------------------------------------------------------------------- #
# Retriever adapters — each returns (ranked_core, generator_set, signals) for one
# question. ``signals`` is table -> [attribution signals] when the source can report it
# (kg), else None (selector) — purely a --verbose diagnostic, never scored.
# --------------------------------------------------------------------------- #
def _fetch_selector(question: str, hint: list[str] | None, k: int | None):
    core = ranked_core(question, tables_hint=hint, top_k=k) or []
    gen_set = select_tables(question, tables_hint=hint, top_k=k, apply_closure=True)
    return core, gen_set, None


def _fetch_kg(question: str, hint: list[str] | None):
    from sql_agent.kg.retrieval import lookup as kg_lookup

    result = kg_lookup(question, tables_hint=hint)
    gen_set = set(result.tables)
    # pre_closure_tables is the S1-S5 fused-and-cut set, frozen BEFORE S6 closure / S7
    # join-path bridging touch it — the true "core" snapshot. NOT reconstructed from
    # attribution: a table scored by a direct signal but ranked below the cut still gets
    # that signal recorded if closure later pulls it in, so "attribution has more than just
    # join_closure" is not a valid membership test (see KGLookup.pre_closure_tables).
    core = list(result.pre_closure_tables)
    return core, gen_set, result.attribution


SOURCE_LABEL = {"selector": "selector (select_tables/ranked_core)",
               "kg": "kg (sql_agent.kg.retrieval.lookup)"}

# Plain-language definitions for every summary metric, appended to every report so a
# reader doesn't need this script's docstring open to interpret the numbers. Kept as one
# shared block (not per-source) so selector and kg reports always explain identically.
METRIC_GLOSSARY_MD = [
    "## Metric glossary", "",
    "Two nested candidate sets are scored per question: the **core** (the retriever's raw "
    "ranked output — before any graph/closure help) and the **generator set** (core + "
    "join-closure/graph-edge expansion — what the SQL generator actually sees).", "",
    "| Metric | What it measures | How to read it |",
    "| --- | --- | --- |",
    "| **core full recall** | Binary per question: are ALL gold tables present in the "
    "core (ranking alone, before closure)? | The retriever's unaided quality. No partial "
    "credit — one missing table fails it. |",
    "| **generator full recall** (headline) | Binary per question: are ALL gold tables "
    "present in the generator set (what the SQL generator actually gets)? | **The number "
    "that matters most** — the real ceiling on whether generation CAN be correct. A miss "
    "here is a hard failure the generator can never recover from. |",
    "| **column full recall** (headline) / **mean column coverage** | Even when a gold "
    "table is present, does it actually carry the gold COLUMN? | A table can be present "
    "but useless if the specific column the SQL needs isn't in it. Coverage is the "
    "fractional version (partial credit); full recall is the all-or-nothing version. |",
    "| **table recall (core / generator)** | Partial-credit version of full recall, "
    "counted per TABLE across the whole run, not per question. | Useful for triage (a 3/4 "
    "miss is less broken than 0/4) but does not predict correctness the way full recall "
    "does — one missing table can still sink a question with high table recall. |",
    "| **questions rescued by closure** | A question whose core MISSED a gold table but "
    "whose generator set caught it anyway via join-closure/graph-edge expansion. | The "
    "\"how much are we relying on the safety net\" number. Lower is better only if the "
    "CORE full recall is correspondingly higher — it means the retriever's own ranking is "
    "doing more of the work unaided. |",
    "| **mean core size** | Average number of tables in the core set. | Fixed by "
    "config (EMBEDDING_TOP_K for selector, KG_CANDIDATE_TOP_K for kg) — this is really a "
    "restatement of that setting, not a retrieval-quality signal. |",
    "| **mean core precision** | Of the tables in the core, what fraction are actually "
    "gold tables (gold ∩ core / \\|core\\|), averaged per question. | Both retrievers "
    "deliberately over-fetch to protect recall (a missing table is a hard failure; an "
    "extra one only costs tokens), so LOW precision alongside HIGH recall is the intended "
    "trade-off, not a bug. Watch for precision dropping WITHOUT recall improving — that "
    "would mean the candidate set is bloating for no benefit. |",
    "| **full-recall depth** | For questions where the core eventually contains every "
    "gold table, how far down the ranked list you had to go (1-based rank of the deepest "
    "gold table). | Lower mean = a sharper ranker; a smaller EMBEDDING_TOP_K/"
    "KG_CANDIDATE_TOP_K could work without losing recall. The max shows the worst case. |",
    "| **recall@K** | Sweep: for several cutoffs K, what fraction of questions would "
    "have full recall if the ranked list were cut off at exactly K tables? | Shows how "
    "much recall you'd sacrifice by shrinking the candidate pool — use it to judge a "
    "token-budget cut BEFORE making it, rather than after. |",
    "",
    "**Reading order:** generator full recall is the real ceiling on correctness "
    "(everything downstream of retrieval needs this to be 100%); core full recall shows "
    "how much of that ceiling is earned by the retriever's own ranking versus rescued by "
    "the graph/closure net; everything else is diagnostic detail for WHY those two numbers "
    "look the way they do, not a target to directly optimize on its own.", "",
]


def _render_markdown(source: str, doc: dict, summary: dict, records: list[dict]) -> str:
    """A human-readable per-question report — the readable sibling of the YAML record."""
    L = [f"# Table Retrieval — {doc['dataset']} — {SOURCE_LABEL[source]}", "",
         f"_Generated {doc['generated_at']} · schema tables {doc['schema_tables']} · "
         + (f"embedding_top_k {doc['embedding_top_k']} · rrf_k {doc['rrf_k']} · "
            f"backend {doc['vector_backend']} · "
            if source == "selector" else
            f"kg_candidate_top_k {doc['kg_candidate_top_k']} · "
            f"kg_join_closure_enabled {doc['kg_join_closure_enabled']} · "
            f"kg_backend {doc['kg_backend']} · ")
         + f"tables_hint {doc['tables_hint']}_", ""]

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
        L += METRIC_GLOSSARY_MD

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
            has_signals = source == "kg"
            header = ("| gold table | found via | rank in core | signals |"
                      if has_signals else "| gold table | found via | rank in core |")
            sep = ("| --- | --- | --- | --- |" if has_signals else "| --- | --- | --- |")
            L += ["", header, sep]
            for t in r["tables"]:
                row = f"| {t['table']} | {t['found_via']} | {t['rank_in_core'] if t['rank_in_core'] else '—'} |"
                if has_signals:
                    row += f" {', '.join(t.get('signals') or []) or '—'} |"
                L.append(row)
        L.append("")
    return "\n".join(L)


def _run_source(source: str, items: list[dict], args, COLS: dict) -> tuple[dict, list[dict]]:
    """Score one retriever (selector or kg) over ``items`` — same methodology either way,
    varying only how (core, gen_set, signals) is fetched per question. Returns (summary,
    records) shaped identically for both sources so they can be diffed directly."""
    top_k = args.k if source == "selector" else settings.kg_candidate_top_k

    core_full = gen_full = 0
    table_gold = table_core = table_gen = 0
    rescued_questions = 0
    core_sizes: list[int] = []
    precision_sum = 0.0
    records: list[dict] = []

    col_n = col_full = 0
    col_cov_sum = 0.0
    depths: list[int] = []
    rank_data: list[tuple[set, list]] = []

    for it in items:
        question = it["question"]
        gold = [t for t in (it.get("gold_tables") or [])]
        gold_set = set(gold)
        unknown = sorted(gold_set - set(ALLOWED_TABLES))

        hint = list(gold) if args.with_hint == "gold" else None
        if source == "selector":
            core, gen_set, signals = _fetch_selector(question, hint, args.k)
        else:
            core, gen_set, signals = _fetch_kg(question, hint)

        core_hit = gold_set <= set(core)
        gen_hit = gold_set <= gen_set
        rescued = sorted((gold_set & gen_set) - set(core))
        missing = sorted(gold_set - gen_set)

        core_full += core_hit
        gen_full += gen_hit
        rescued_questions += bool(rescued)
        table_gold += len(gold_set)
        table_core += len(gold_set & set(core))
        table_gen += len(gold_set & gen_set)
        core_sizes.append(len(core))
        precision_sum += (len(gold_set & set(core)) / len(core)) if core else 0.0

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

        gold_ranks = [_rank_of(core, t) for t in gold_set]
        full_recall_depth = (max(gold_ranks) if gold_ranks and all(r is not None for r in gold_ranks)
                             else None)
        if full_recall_depth is not None:
            depths.append(full_recall_depth)
        rank_data.append((set(gold_set), list(core)))

        if missing:
            verdict = "MISS"
        elif rescued:
            verdict = "RESQ"
        else:
            verdict = "OK  "
        print(f"[{source[:3].upper():3s}][{verdict}] {it['id']:5s} core={'Y' if core_hit else 'n'} "
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
                                  "in_generator_set": t in gen_set, "found_via": where,
                                  "signals": (signals or {}).get(t)})
            if args.verbose:
                r = _rank_of(core, t)
                mark = "+" if t in core else ("~" if t in gen_set else " ")
                sig = f"  [{', '.join((signals or {}).get(t) or [])}]" if signals else ""
                print(f"       {mark}  {t:34s} {where:8s}"
                      f"{'  rank ' + str(r) if r else ''}{sig}")
        if args.verbose and unknown:
            print(f"       !  not in schema allow-list: {', '.join(unknown)}")

        records.append({
            "id": it["id"], "question": question,
            "gold_tables": gold,
            "core_full_recall": core_hit, "generator_full_recall": gen_hit,
            "ranked_core": list(core), "generator_set": sorted(gen_set),
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
        max_core = max((len(c) for _, c in rank_data), default=0)
        k_grid = sorted({k for k in (1, 3, 5, 8, 10, top_k or 0) if 1 <= k <= max_core})
        recall_at_k = {
            k: round(100 * sum(1 for g, c in rank_data if g <= set(c[:k])) / n, 1)
            for k in k_grid
        }
        summary = {
            "core_full_recall": {"hits": core_full, "n": n, "pct": round(100 * core_full / n, 1)},
            "generator_full_recall": {"hits": gen_full, "n": n, "pct": round(100 * gen_full / n, 1)},
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
            "full_recall_depth": {"mean": round(sum(depths) / len(depths), 2) if depths else None,
                                  "answerable": len(depths), "n": n,
                                  "max": max(depths) if depths else None},
            "recall_at_k": recall_at_k,
        }
        print(f"\n{'-' * 70}  [{SOURCE_LABEL[source]}]")
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
    return summary, records


def _print_delta(items: list[dict], sel_records: list[dict], kg_records: list[dict]) -> None:
    """Per-question disagreement between the two sources — the signal that matters most
    when tuning KG settings against the selector baseline: a rescue on one side must not be
    lost in an aggregate that only reports on the OTHER side improving too."""
    by_id_sel = {r["id"]: r for r in sel_records}
    by_id_kg = {r["id"]: r for r in kg_records}
    rows = []
    for it in items:
        s, k = by_id_sel.get(it["id"]), by_id_kg.get(it["id"])
        if not s or not k:
            continue
        if s["generator_full_recall"] != k["generator_full_recall"]:
            rows.append((it["id"], s["generator_full_recall"], k["generator_full_recall"]))
    if not rows:
        print("\nNo full-recall disagreement between selector and kg — both agree on every "
              "evaluated question.")
        return
    print(f"\n{'-' * 70}")
    print(f"selector vs kg — {len(rows)} question(s) disagree on generator full recall:")
    for qid, sel_ok, kg_ok in rows:
        tag = "kg RESCUES" if kg_ok and not sel_ok else "kg REGRESSES"
        print(f"  {qid:6s} selector={'OK' if sel_ok else 'MISS':4s}  "
              f"kg={'OK' if kg_ok else 'MISS':4s}   <-- {tag}")


def _render_comparison_markdown(dataset: str, items: list[dict], sel_doc: dict, kg_doc: dict,
                                sel_summary: dict, kg_summary: dict,
                                sel_records: list[dict], kg_records: list[dict]) -> str:
    """The side-by-side report --source both is FOR: every summary metric in one table,
    selector next to kg, plus the per-question delta — so a comparison never has to be
    reconstructed by hand from two separately-generated reports."""
    def _pct(d):
        return f"{d['hits']}/{d['n']} ({d['pct']}%)"

    sfrd, kfrd = sel_summary["full_recall_depth"], kg_summary["full_recall_depth"]
    L = [f"# Table Retrieval — {dataset} — selector vs kg", "",
         f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%S')} · {len(items)} question(s) · "
         f"selector: embedding_top_k {sel_doc['embedding_top_k']}, rrf_k {sel_doc['rrf_k']} · "
         f"kg: kg_candidate_top_k {kg_doc['kg_candidate_top_k']}, "
         f"kg_join_closure_enabled {kg_doc['kg_join_closure_enabled']}, "
         f"kg_backend {kg_doc['kg_backend']}_", "",
         "See `table_retrieval.md` (selector) and `table_retrieval_kg.md` (kg) for the "
         "full per-question detail behind each source; this file is the side-by-side "
         "summary and disagreement report.", "",
         "## Summary — selector vs kg", "",
         "| Metric | selector | kg |", "| --- | --- | --- |",
         f"| core full recall | {_pct(sel_summary['core_full_recall'])} | "
         f"{_pct(kg_summary['core_full_recall'])} |",
         f"| generator full recall (headline) | {_pct(sel_summary['generator_full_recall'])} | "
         f"{_pct(kg_summary['generator_full_recall'])} |",
         f"| column full recall (headline) | {_pct(sel_summary['column_full_recall'])} | "
         f"{_pct(kg_summary['column_full_recall'])} |",
         f"| mean column coverage | {sel_summary['mean_column_coverage']} | "
         f"{kg_summary['mean_column_coverage']} |",
         f"| table recall (core) | {_pct(sel_summary['table_recall_core'])} | "
         f"{_pct(kg_summary['table_recall_core'])} |",
         f"| table recall (generator) | {_pct(sel_summary['table_recall_generator'])} | "
         f"{_pct(kg_summary['table_recall_generator'])} |",
         f"| questions rescued by closure | {sel_summary['questions_rescued_by_closure']} | "
         f"{kg_summary['questions_rescued_by_closure']} |",
         f"| mean core size | {sel_summary['mean_core_size']} | {kg_summary['mean_core_size']} |",
         f"| mean core precision | {sel_summary['mean_core_precision']} | "
         f"{kg_summary['mean_core_precision']} |",
         f"| full-recall depth | mean {sfrd['mean']}, max {sfrd['max']} "
         f"(over {sfrd['answerable']}/{sfrd['n']}) | mean {kfrd['mean']}, max {kfrd['max']} "
         f"(over {kfrd['answerable']}/{kfrd['n']}) |"]
    sel_rk, kg_rk = sel_summary.get("recall_at_k") or {}, kg_summary.get("recall_at_k") or {}
    if sel_rk or kg_rk:
        L.append("| recall@K (full) | "
                 + ", ".join(f"K={k}:{v}%" for k, v in sel_rk.items()) + " | "
                 + ", ".join(f"K={k}:{v}%" for k, v in kg_rk.items()) + " |")
    L.append("")
    L += METRIC_GLOSSARY_MD

    by_id_sel = {r["id"]: r for r in sel_records}
    by_id_kg = {r["id"]: r for r in kg_records}
    delta_rows = []
    for it in items:
        s, k = by_id_sel.get(it["id"]), by_id_kg.get(it["id"])
        if not s or not k:
            continue
        if s["generator_full_recall"] != k["generator_full_recall"]:
            delta_rows.append((it["id"], it["question"], s["generator_full_recall"],
                               k["generator_full_recall"]))

    L += ["## Per-question disagreement (generator full recall)", ""]
    if not delta_rows:
        L += ["No disagreement — selector and kg agree on generator full recall for every "
             "evaluated question.", ""]
    else:
        L += ["| id | question | selector | kg | verdict |",
              "| --- | --- | --- | --- | --- |"]
        for qid, question, sel_ok, kg_ok in delta_rows:
            tag = "kg RESCUES" if kg_ok and not sel_ok else "kg REGRESSES"
            L.append(f"| {qid} | {question[:80]} | {'OK' if sel_ok else 'MISS'} | "
                     f"{'OK' if kg_ok else 'MISS'} | {tag} |")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check whether table retrieval surfaces the gold tables for "
                    "gold_dynamic questions — selector, kg, or both.",
    )
    ap.add_argument("--dataset", default="gold_dynamic")
    ap.add_argument("--source", choices=["selector", "kg", "both"], default="selector",
                    help="which retriever to score: the plain embedding/BM25 selector "
                         "(default, unchanged), the KG (sql_agent.kg.retrieval.lookup — "
                         "the actual production path when KG_ENABLED=true), or both")
    sel = ap.add_argument_group("selection (default: every item)")
    sel.add_argument("--id", help="check ONE question by id (e.g. D11)")
    sel.add_argument("--ids", help="comma-separated ids, e.g. D07,D11,D23")
    sel.add_argument("--range", help="inclusive id range, e.g. D01-D11")
    ap.add_argument("--k", type=int, default=None,
                    help="override EMBEDDING_TOP_K (selector candidate pool size) for this run")
    ap.add_argument("--kg-k", type=int, default=None,
                    help="override KG_CANDIDATE_TOP_K (kg pre-closure cut) for this run")
    ap.add_argument("--with-hint", choices=["gold"], default=None,
                    help="force-include each item's gold_tables as tables_hint — a "
                         "closure/planner UPPER BOUND, not a retrieval score (see docstring)")
    ap.add_argument("--vector-backend", default="memory",
                    choices=["memory", "faiss", "qdrant"],
                    help="default 'memory' (in-RAM, no file lock); 'qdrant' tests the "
                         "actual persisted index (stop the API server first) — selector only")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print per-table hit/miss (rank, which set caught it, and — for "
                         "--source kg — the KG attribution signals), not just the per-item verdict")
    ap.add_argument("--out", default=None,
                    help=f"where to write the full YAML record (default {OUT_PATH} for "
                         f"selector, {OUT_PATH_KG} for kg — not usable with --source both)")
    args = ap.parse_args()

    if args.out and args.source == "both":
        raise SystemExit("--out needs a single --source (selector or kg) — with 'both' "
                         "each source writes its own default path.")

    settings.vector_backend = args.vector_backend
    # The selector no-ops to the FULL schema unless retrieval is on — which would score a
    # trivial 100% recall and measure nothing. Force it on for the duration of the check.
    settings.schema_retrieval_enabled = True
    if args.kg_k is not None:
        settings.kg_candidate_top_k = args.kg_k

    sources = ["selector", "kg"] if args.source == "both" else [args.source]

    if "selector" in sources:
        from sql_agent.semantic_layer.embeddings import get_backend
        if get_backend() is None:
            print("WARNING: EMBEDDING_BACKEND=none — dense retrieval is off; this scores "
                  "the BM25-only path. Set EMBEDDING_BACKEND=local to test hybrid retrieval.\n")
    if "kg" in sources:
        if not settings.kg_enabled:
            print("WARNING: KG_ENABLED=false — the kg source will score an empty lookup "
                  "on every question (everything MISSING). Set KG_ENABLED=true in .env.\n")
        else:
            from sql_agent.kg.client import get_kg_client
            if get_kg_client() is None:
                print(f"WARNING: KG backend ({settings.kg_backend}) unreachable — the kg "
                      f"source will score an empty lookup on every question.\n")

    src = yaml.safe_load((HERE / "datasets" / f"{args.dataset}.yaml").read_text(encoding="utf-8"))
    items = _select(src["items"], args)

    print(f"Schema tables   : {len(ALLOWED_TABLES)} allowed")
    print(f"Gold questions  : {len(items)} ({args.dataset})")
    print(f"source(s)       : {', '.join(sources)}")
    if "selector" in sources:
        print(f"embedding_top_k : {args.k or settings.embedding_top_k}   rrf_k: {settings.rrf_k}")
    if "kg" in sources:
        print(f"kg_candidate_top_k: {settings.kg_candidate_top_k}   "
              f"kg_join_closure_enabled: {settings.kg_join_closure_enabled}   "
              f"kg_backend: {settings.kg_backend}")
    print(f"tables_hint     : {'gold_tables (upper bound)' if args.with_hint else 'none (question only)'}\n")

    COLS = table_columns()

    results: dict[str, tuple[dict, list[dict], dict]] = {}
    for source in sources:
        summary, records = _run_source(source, items, args, COLS)

        out_path = (Path(args.out) if args.out
                   else (OUT_PATH if source == "selector" else OUT_PATH_KG))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "dataset": args.dataset,
            "source": source,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "schema_tables": len(ALLOWED_TABLES),
            "tables_hint": "gold" if args.with_hint else "none",
            "summary": summary,
            "items": records,
        }
        if source == "selector":
            doc["embedding_top_k"] = args.k or settings.embedding_top_k
            doc["rrf_k"] = settings.rrf_k
            doc["vector_backend"] = args.vector_backend
        else:
            doc["kg_candidate_top_k"] = settings.kg_candidate_top_k
            doc["kg_join_closure_enabled"] = settings.kg_join_closure_enabled
            doc["kg_backend"] = settings.kg_backend
        out_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                                           width=4096, default_flow_style=False),
                            encoding="utf-8")
        md_path = out_path.with_suffix(".md")
        md_path.write_text(_render_markdown(source, doc, summary, records), encoding="utf-8")
        print(f"\nWrote {out_path}\n      {md_path}  ({len(records)} question(s) recorded)")
        results[source] = (summary, records, doc)

    if args.source == "both":
        sel_summary, sel_records, sel_doc = results["selector"]
        kg_summary, kg_records, kg_doc = results["kg"]
        _print_delta(items, sel_records, kg_records)
        cmp_path = OUT_DIR / "table_retrieval_comparison.md"
        cmp_path.write_text(
            _render_comparison_markdown(args.dataset, items, sel_doc, kg_doc,
                                        sel_summary, kg_summary, sel_records, kg_records),
            encoding="utf-8")
        print(f"\nWrote {cmp_path}  (selector vs kg side-by-side)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
