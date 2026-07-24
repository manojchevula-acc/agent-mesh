"""Deterministic-only text-to-SQL evaluation — no LLM, no tokens, fully reproducible.

Diffs the agent's recorded runs (eval/run_agent.py output) against gold using ONLY the
deterministic layer in eval/deterministic/ — structural SQL comparison (tables, columns,
joins, filters, group-by, order-by, aggregations), result-set similarity (row/cell P/R/F1,
exact-match, Jaccard, fuzzy), and schema-aware id<->name equivalence. Every score is exact
arithmetic: the same inputs always produce the same report, with no provider key, no
tokens, and no run-to-run variance.

WHY THIS IS ITS OWN SCRIPT, NOT A FLAG ON eval/compare_llm.py
---------------------------------------------------------------
eval/compare_llm.py's job is to compare the deterministic evaluator against an LLM judge
and report where they agree or diverge (Cohen's Kappa, stricter/leaner breakdown) — that
inherently needs both evaluators. A deterministic-only pass is a genuinely different,
LLM-free use case (CI-friendly, free, byte-for-byte reproducible) and deserves its own
entry point, its own output folder, and a name that doesn't imply an LLM is involved.

Both scripts share their deterministic rendering via eval/deterministic/report.py, so the
"## Deterministic evaluation" section here and inside compare_llm.py's report are always
computed identically and can't quietly drift apart.

Reads:
    eval/datasets/gold_dynamic.yaml   question + gold_sql + gold_result
    eval/results/agent_runs.yaml      question + agent_sql + agent_result

Writes (its OWN folder — never touches eval/results/EVAL_REPORT.*, which belongs to
eval/compare_llm.py's LLM-comparison report):
    eval/results/deterministic/DETERMINISTIC_EVAL_REPORT.md          (default --runs)
    eval/results/deterministic/DETERMINISTIC_EVAL_REPORT_<TAG>.md    (any other --runs)

ONE REPORT PER --runs FILE, NEVER A SHARED/OVERWRITTEN ONE
------------------------------------------------------------
The output name is derived from the --runs filename (see report.derive_tag): evaluating
agent_runs_JOIN.yaml and agent_runs_22JULY_MULTIJOIN.yaml in the same folder produces
DETERMINISTIC_EVAL_REPORT_JOIN.md and DETERMINISTIC_EVAL_REPORT_22JULY_MULTIJOIN.md side by
side — re-running against the SAME file still overwrites (idempotent), but two DIFFERENT
recorded runs never clobber each other. Pass --tag to name it explicitly instead.

Run:
    .venv/Scripts/python.exe eval/deterministic_eval.py
    .venv/Scripts/python.exe eval/deterministic_eval.py --ids D01,D02
    .venv/Scripts/python.exe eval/deterministic_eval.py --runs eval/results/agent_runs_22JULY_MULTIJOIN.yaml
    .venv/Scripts/python.exe eval/deterministic_eval.py --runs eval/results/agent_runs_JOIN.yaml --tag my-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from eval.deterministic import report as det_report  # noqa: E402
from eval.deterministic.evaluator import DeterministicEvaluator  # noqa: E402

GOLD_PATH = HERE / "datasets" / "gold_dynamic.yaml"
RUNS_PATH = HERE / "results" / "agent_runs.yaml"
OUT_DIR = HERE / "results" / "deterministic"
REPORT_BASENAME = "DETERMINISTIC_EVAL_REPORT"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic-only text-to-SQL evaluation — no LLM, no tokens, "
                    "fully reproducible.")
    ap.add_argument("--gold", default=str(GOLD_PATH))
    ap.add_argument("--runs", default=str(RUNS_PATH))
    ap.add_argument("--ids", help="only evaluate these ids, comma-separated")
    ap.add_argument("--tag", help="name the output report explicitly (default: derived "
                    "from the --runs filename, e.g. agent_runs_JOIN.yaml -> ..._JOIN.md)")
    args = ap.parse_args()

    tag = args.tag if args.tag is not None else det_report.derive_tag(args.runs)
    REPORT_PATH, REPORT_JSON = det_report.report_paths(OUT_DIR, REPORT_BASENAME, tag)

    gold_doc, gold = det_report.load_gold(args.gold)
    runs = det_report.load_runs(args.runs)
    runs, covered, unrun = det_report.select_and_cover(runs, gold, args.ids)
    if not runs:
        raise SystemExit("No agent runs matched the selection.")

    defaults = (gold_doc.get("meta") or {}).get("defaults") or {}
    tol = defaults.get("numeric_tolerance", 0.01)

    print(f"Deterministic evaluation — {len(runs)} agent run(s) against "
          f"{len(gold)} gold item(s). No LLM, no tokens.")
    if unrun:
        print(f"Coverage: {len(covered)}/{len(gold)} gold questions have a recorded run — "
              f"{len(unrun)} NOT run: {', '.join(unrun)}")

    # One evaluator for the whole run so the schema-aware matcher's lookup cache (and its
    # single DB touch per entity) is shared across every question.
    det_ev = DeterministicEvaluator(numeric_tolerance=tol)
    rows_out, stale = det_report.evaluate_rows(gold, runs, det_ev)

    for r in rows_out:
        d = r["det"]
        flag = "  <== STALE" if r["id"] in stale else ""
        print(f"[{'PASS' if d.passed else 'FAIL'}] {r['id']:14s} "
              f"{d.diagnosis:28s} conf={d.confidence:<5} "
              f"core={'Y' if d.core_answer_match else 'n'}{flag}")

    for line in det_report.stale_console(stale):
        print(line)

    n = len(rows_out)
    det_passed = sum(1 for r in rows_out if r["det"].passed)
    core_passed = sum(1 for r in rows_out if r["det"].core_answer_match)

    # ---------------- markdown report ----------------
    L = [f"# Deterministic Evaluation — {gold_doc['meta']['version']}\n",
         f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · {n} question(s) · "
         f"snapshot `{gold_doc['meta'].get('db_snapshot')}` · **NO LLM involved**_\n"]
    L += det_report.stale_md(stale)
    L += det_report.coverage_md(covered, unrun, gold, n)
    L += det_report.render_headline(rows_out)
    L += det_report.render_answer_correctness(rows_out)
    L += det_report.render_query_construction(rows_out)
    L += det_report.render_per_question_table(rows_out)
    L += det_report.render_per_question_detail(rows_out)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(L), encoding="utf-8")

    # ---------------- JSON sidecar ----------------
    REPORT_JSON.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dataset": gold_doc["meta"]["version"],
        "summary": {
            "gold_benchmark_size": len(gold),
            "questions_evaluated": n,
            "benchmark_coverage": round(len(covered) / len(gold), 3) if gold else None,
            "not_run": unrun, "stale": stale,
            "deterministic_pass": det_passed,
            "deterministic_pass_rate": round(det_passed / n, 3) if n else None,
            "core_answer_pass": core_passed,
            "core_answer_pass_rate": round(core_passed / n, 3) if n else None,
        },
        "dashboard": det_report.build_json_dashboard(rows_out),
        "rows": det_report.build_json_rows(rows_out),
    }, indent=2, default=str), encoding="utf-8")

    print(f"\nReport -> {REPORT_PATH}")
    print(f"JSON   -> {REPORT_JSON}")
    print(f"PASSED (strict) {det_passed}/{n}  ·  core-answer (lenient) {core_passed}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
