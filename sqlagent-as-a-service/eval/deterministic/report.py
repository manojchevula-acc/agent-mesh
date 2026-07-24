"""Shared rendering for deterministic-evaluation reports.

Used by:
  eval/deterministic_eval.py   — standalone deterministic-only report (no LLM, no tokens,
                                  its own output folder)
  eval/compare_llm.py          — LLM + deterministic comparison report; imports the SAME
                                  headline/dashboard renderers so the deterministic section
                                  of both reports is computed and worded identically and can
                                  never quietly drift apart between the two scripts.

Nothing in this module touches an LLM, a provider key, or a token budget — every function
here is pure formatting over (gold item, agent run, DeterministicResult) triples the caller
already computed via eval.deterministic.evaluator.DeterministicEvaluator. This is the one
place that owns "what does a deterministic-evaluation report look like", so a future
reporting need (a third script, a test, a notebook) has one obvious module to import rather
than a choice between two scripts to copy from.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from eval.deterministic.evaluator import DeterministicEvaluator, METRIC_REGISTRY

# --------------------------------------------------------------------------- #
# Output naming — one report per --runs file, never a shared/overwritten one
# --------------------------------------------------------------------------- #
def derive_tag(runs_path) -> str:
    """A filename-safe suffix derived from the --runs file, so evaluating a DIFFERENT
    recorded-runs file writes a DIFFERENT report instead of silently overwriting the last
    one — the exact failure mode of a fixed `EVAL_REPORT.md` name.

    Matches the naming convention already in eval/results/ (agent_runs_JOIN.yaml,
    agent_runs_22JULY_MULTIJOIN.yaml, ...): the tag is whatever comes after the
    'agent_runs' prefix.
        agent_runs.yaml                 -> ''                   (the plain default name)
        agent_runs_JOIN.yaml            -> 'JOIN'
        agent_runs_22JULY_MULTIJOIN.yaml -> '22JULY_MULTIJOIN'
        some_other_name.yaml            -> 'some_other_name'    (whole stem, unrecognised)
    """
    stem = Path(runs_path).stem
    prefix = "agent_runs"
    if stem == prefix:
        return ""
    if stem.startswith(prefix + "_"):
        return stem[len(prefix) + 1:]
    return stem


def report_paths(out_dir: Path, base_name: str, tag: str) -> tuple[Path, Path]:
    """(markdown_path, json_path) for `base_name` in `out_dir`, suffixed with `tag` when
    one is given. Two runs with different tags never collide; the same runs file always
    re-evaluates to the same two paths (idempotent, diffable across re-runs)."""
    suffix = f"_{tag}" if tag else ""
    return (out_dir / f"{base_name}{suffix}.md", out_dir / f"{base_name}{suffix}.json")


# --------------------------------------------------------------------------- #
# Loading — gold set + recorded agent runs
# --------------------------------------------------------------------------- #
def load_gold(path) -> tuple[dict, dict]:
    """(full yaml doc, {id: item}) for the gold dataset at `path`."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return doc, {i["id"]: i for i in doc["items"]}


def load_runs(path) -> list[dict]:
    """The recorded agent runs at `path` (eval/run_agent.py's output). Raises with a clear
    next-step if the file is missing, rather than a bare FileNotFoundError."""
    runs_path = Path(path)
    if not runs_path.exists():
        raise SystemExit(f"No agent runs at {runs_path}. Run eval/run_agent.py first.")
    doc = yaml.safe_load(runs_path.read_text(encoding="utf-8")) or {}
    return doc.get("runs", [])


def select_and_cover(runs: list[dict], gold: dict, ids: str | None):
    """Apply an optional --ids filter; return (selected_runs, covered_ids, unrun_ids).

    Coverage is measured against the WHOLE runs file BEFORE the --ids filter, because "how
    much of the benchmark has actually been attempted" is a property of the dataset, not of
    this particular invocation's selection.
    """
    covered = {r["gold_id"] for r in runs}
    unrun = [i for i in gold if i not in covered]
    if ids:
        want = {t.strip().lower() for t in ids.split(",")}
        runs = [r for r in runs if r["id"].lower() in want or r["gold_id"].lower() in want]
    return runs, covered, unrun


def evaluate_rows(gold: dict, runs: list[dict], det_ev: DeterministicEvaluator):
    """Score every (gold item, agent run) pair. Returns (rows_out, stale_ids).

    rows_out: one dict per matched run — {"id", "item", "run", "det"} — the common shape
    every renderer below expects. A caller with extra per-row data (compare_llm.py adds an
    LLM verdict) can freely add more keys; the renderers here only read these four.

    stale_ids: recorded runs whose question text no longer matches gold's CURRENT wording.
    If the gold question was edited since the run was recorded, grading that run against
    the new gold silently compares answers to two different questions — usually as a
    spurious failure — so these are called out rather than folded into the pass rate.
    Paraphrase/other-language variants are exempt: they deliberately differ from the
    parent's wording by design.
    """
    rows_out, stale = [], []
    for run in runs:
        item = gold.get(run["gold_id"])
        if not item:
            print(f"[warn] {run['id']}: no gold item '{run['gold_id']}' — skipped")
            continue
        if "::" not in run["id"] and run["question"].strip() != item["question"].strip():
            stale.append(run["id"])
        det = det_ev.evaluate(item, run)
        rows_out.append({"id": run["id"], "item": item, "run": run, "det": det})
    return rows_out, stale


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #
def mean(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def bar(x: float | None) -> str:
    """A 0-1 score as a tiny 10-cell bar, so a dashboard column is scannable at a glance."""
    if x is None:
        return "n/a"
    filled = int(round(x * 10))
    return "█" * filled + "░" * (10 - filled) + f" {x:.2f}"


def _evaluable(rows_out) -> list:
    """The DeterministicResults that actually had rows to grade — excludes rate-limited /
    errored / no-SQL runs, so a handful of non-answers cannot drag every metric mean toward
    zero and read as poor SQL quality."""
    return [r["det"] for r in rows_out if r["det"].evaluable]


def dm(rows_out, key: str) -> float | None:
    """Mean of one registered metric over evaluable rows (skipping rows it's N/A for)."""
    grade = _evaluable(rows_out)
    vals = [d.metrics.get(key) for d in grade if d.metrics.get(key) is not None]
    return mean(vals)


def _fmt_rows(rows, limit: int = 5) -> str:
    if rows is None:
        return "_(none)_"
    if not rows:
        return "_(empty)_"
    body = "<br>".join("`" + ", ".join(f"{k}={v}" for k, v in r.items())[:150] + "`"
                       for r in rows[:limit])
    if len(rows) > limit:
        body += f"<br>_… {len(rows) - limit} more_"
    return body


# --------------------------------------------------------------------------- #
# Console / markdown warnings (stale runs, partial coverage)
# --------------------------------------------------------------------------- #
def stale_console(stale: list[str]) -> list[str]:
    if not stale:
        return []
    return ([f"\n!! {len(stale)} run(s) answer an OUTDATED question — the gold wording "
             f"changed since they were recorded, so their verdicts are meaningless:"]
            + [f"   {sid}" for sid in stale]
            + [f"   Re-run:  .venv/Scripts/python.exe eval/run_agent.py "
               f"--ids {','.join(stale)}"])


def stale_md(stale: list[str]) -> list[str]:
    if not stale:
        return []
    return [f"> ⚠️ **{len(stale)} run(s) are STALE** — `{', '.join(stale)}` answer an "
            f"older wording of their question, so their verdicts below mean nothing. "
            f"Re-run: `eval/run_agent.py --ids {','.join(stale)}`\n"]


def coverage_md(covered: set, unrun: list, gold: dict, n: int) -> list[str]:
    if not unrun:
        return []
    missing_diff = sorted({str(gold[i].get("difficulty", "?")) for i in unrun})
    return [f"> ⚠️ **PARTIAL RUN — {len(covered)}/{len(gold)} gold questions "
            f"({round(100 * len(covered) / len(gold))}%) have been run.** The numbers "
            f"below are over the **{n} evaluated here**, NOT over the benchmark, and are "
            f"not comparable to a full run.\n>\n"
            f"> Not run: `{', '.join(unrun)}`\n>\n"
            f"> Untested difficulties: {', '.join('`' + d + '`' for d in missing_diff)}\n>\n"
            f"> Complete it with: `eval/run_agent.py --all --pause 6 --resume`\n"]


# --------------------------------------------------------------------------- #
# Markdown sections — the deterministic evaluation, in isolation
# --------------------------------------------------------------------------- #
def render_headline(rows_out) -> list[str]:
    """The verdict counts: STRICT (source of truth) and core-answer (lenient, additive —
    see eval/deterministic/evaluator.py's _m_core_answer for what it credits and why it
    never overrides the strict verdict)."""
    det_rows = [r["det"] for r in rows_out]
    grade = _evaluable(rows_out)
    no_answer = [d for d in det_rows if not d.evaluable]
    no_answer_ids = [r["id"] for r in rows_out if not r["det"].evaluable]
    det_passed = sum(1 for d in det_rows if d.passed)
    core_passed = sum(1 for d in det_rows if d.core_answer_match)
    n = len(det_rows)
    return ["\n## Deterministic evaluation\n",
        "_No LLM involved — reproducible metrics computed from the recorded agent "
        "results. Every score is 0–1, **higher = closer to gold**._\n",
        f"- **Verdict (strict):** {det_passed}/{n} PASS "
        f"(**{round(det_passed / n, 3) if n else '—'}**) — execution correctness "
        f"against gold's FULL column set. This is the source of truth.",
        f"- **Core-answer match (lenient):** {core_passed}/{n} "
        f"(**{round(core_passed / n, 3) if n else '—'}**) — additionally credits a "
        f"row where the agent returned FEWER columns than gold but every value it DID "
        f"return is correct (e.g. dropped a label/count column the question may not have "
        f"asked for). Does **not** change the strict verdict above — shown side by side "
        f"so you can judge whether the extra columns actually mattered for a given "
        f"question. See the `core_answer_match` column below for which rows this affects.",
        f"- **Graded on:** {len(grade)}/{n} questions that returned rows to compare"
        + (f"  ·  {len(no_answer)} produced NO result "
           f"(`{', '.join(no_answer_ids)}`) — excluded from the metric means below"
           if no_answer else "")]


def render_answer_correctness(rows_out) -> list[str]:
    """Section 1 — is the DATA right? These metrics decide the strict verdict."""
    grade = _evaluable(rows_out)
    L = ["\n### 1 · Answer correctness (the returned rows)\n",
         "_Is the DATA right? This is what a PASS is based on._\n",
         "| Metric | Score | What it means |", "|---|---|---|"]
    for key, meaning in (
        ("result_exact_match", "whole result set identical to gold (within tolerance)"),
        ("result_row_f1", "rows matched — F1 of row precision & recall"),
        ("cell_accuracy", "individual cells correct, across aligned rows"),
        ("result_jaccard", "row overlap (shared rows / all rows)"),
        ("fuzzy_similarity", "row similarity allowing typos & rounding"),
        ("semantic_equivalence",
         "same entities via id↔name resolution (e.g. PROD002 ≡ 'Term Loan')"),
        ("core_answer_match",
         "value-correct even if some gold columns were omitted (lenient lens)"),
    ):
        m = dm(rows_out, key)
        suffix = ""
        if key == "semantic_equivalence":
            applic = sum(1 for d in grade if d.metrics.get(key) is not None)
            suffix = f" _(on {applic} applicable)_"
        L.append(f"| {key.replace('_', ' ')} | {bar(m)}{suffix} | {meaning} |")
    return L


def render_query_construction(rows_out) -> list[str]:
    """Section 2 — was the SQL BUILT like gold? Diagnostic only — a legitimately different
    query SHOULD score below 1 here while still being correct; this never gates pass/fail."""
    grade = _evaluable(rows_out)
    L = ["\n### 2 · Query construction (how the SQL was built)\n",
         "_Diagnostic, **not** pass/fail: a correct query written a different way "
         "SHOULD score below 1 here. Low ‘exact match’ / ‘order by’ is "
         "normal and does not mean wrong — check answer correctness above for that._\n",
         "| SQL element | Precision | Recall | F1 |", "|---|---|---|---|"]
    for el, label in (("tables", "Tables"), ("columns", "Columns"), ("joins", "Joins"),
                      ("filters", "Filters (WHERE)"), ("group_by", "Group by"),
                      ("order_by", "Order by"), ("aggregations", "Aggregations")):
        p = mean([d.sql_elements[el]["precision"] for d in grade])
        rc = mean([d.sql_elements[el]["recall"] for d in grade])
        f = mean([d.sql_elements[el]["f1"] for d in grade])
        L.append(f"| {label} | {'—' if p is None else f'{p:.2f}'} | "
                 f"{'—' if rc is None else f'{rc:.2f}'} | "
                 f"{'—' if f is None else f'{f:.2f}'} |")
    L += ["\n| Overall | | | |", "|---|---|---|---|",
          f"| SQL structural similarity | | | **{dm(rows_out, 'sql_structural_similarity')}** |",
          f"| SQL exact match rate | | | {dm(rows_out, 'sql_exact_match')} "
          f"_(≈0 is normal — few agents reproduce gold verbatim)_ |"]
    return L


def render_per_question_table(rows_out) -> list[str]:
    """Deterministic-only per-question summary table (no LLM columns)."""
    L = ["\n## Per-question results\n",
         "| id | Question | Verdict | Core-answer | Conf | Diagnosis | Struct sim | "
         "Row F1 | Cell acc | Semantic | Rows g/a |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows_out:
        it, run, d = r["item"], r["run"], r["det"]
        gr = len(it.get("gold_result") or [])
        ar = "-" if run.get("agent_result") is None else len(run["agent_result"])
        rd = d.result_detail
        sem = "✅" if d.semantic_equivalent else "—"
        # Only mark this column when it DIVERGES from the strict verdict — nothing new to
        # say when they already agree.
        core = "—" if d.core_answer_match == d.passed else "✅ (subset correct)"
        L.append(
            f"| {r['id']} | {it['question'][:45]} | "
            f"{'✅ PASS' if d.passed else '❌ FAIL'} | {core} | {d.confidence} | "
            f"{d.diagnosis} | {d.structural_similarity} | {rd['row_f1']} | "
            f"{rd['cell_accuracy']} | {sem} | {gr}/{ar} |")
    return L


def render_per_question_detail(rows_out) -> list[str]:
    """Full per-question detail — SQL, results, every metric this evaluator produced. No
    LLM content; compare_llm.py appends its own LLM lines around a similarly-shaped block
    rather than sharing this one, since the LLM verdict is interleaved per-row, not a
    separate section."""
    L = ["\n## Per-question detail\n"]
    for r in rows_out:
        it, run, d = r["item"], r["run"], r["det"]
        L.append(f"### {r['id']} — {'✅ PASS' if d.passed else '❌ FAIL'} "
                 f"(confidence {d.confidence} · {d.diagnosis})\n")
        L.append(f"**Question:** {run['question']}\n")
        L.append(f"- Tools called: `{', '.join(run.get('tools_called') or []) or '-'}` · "
                 f"status `{run['status']}` · {run.get('latency_ms', '?')}ms")
        L.append(f"\n**Gold SQL**\n```sql\n{(it.get('gold_sql') or '-').strip()}\n```")
        L.append(f"**Agent SQL**\n```sql\n{(run.get('agent_sql') or '-').strip()}\n```")
        if run.get("exec_error"):
            L.append(f"> **SQL execution FAILED:** `{run['exec_error']}`")
        L.append(f"- **Gold tables:** `{sorted(it.get('gold_tables') or [])}` · "
                 f"**Agent tables:** `{run.get('agent_tables') or '-'}`")
        L.append(f"- **Gold columns:** `{sorted(it.get('gold_columns') or [])}`")
        L.append(f"- **Agent columns:** `{run.get('agent_columns') or '-'}`")
        L.append(f"\n| | Gold | Agent |\n|---|---|---|")
        L.append(f"| Result | {_fmt_rows(it.get('gold_result'))} | "
                 f"{_fmt_rows(run.get('agent_result'))} |")

        el, rd = d.sql_elements, d.result_detail
        L.append(f"\n**Deterministic evaluation — {'✅ PASS' if d.passed else '❌ FAIL'}** "
                 f"· confidence {d.confidence} · `{d.diagnosis}`"
                 + ("" if d.evaluable else " · _no result produced, not graded_"))

        L.append("\n_Answer correctness (returned rows):_\n")
        L.append("| exact match | row precision | row recall | row F1 | cell accuracy "
                 "| Jaccard | fuzzy | semantic equiv | core-answer |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        sem_cell = ("✅ yes" if d.semantic_equivalent
                    else ("—" if d.metrics.get("semantic_equivalence") is None else "✗ no"))
        L.append(f"| {rd['exact_match']} | {rd['row_precision']} | {rd['row_recall']} | "
                 f"{rd['row_f1']} | {rd['cell_accuracy']} | {rd['jaccard']} | "
                 f"{rd['fuzzy_similarity']} | {sem_cell} | "
                 f"{'✅' if d.core_answer_match else '✗'} |")

        L.append("\n_Query construction — precision / recall / F1 per SQL element "
                 f"(structural similarity **{d.structural_similarity}**, "
                 f"exact SQL `{d.exact_sql_match}`):_\n")
        L.append("| SQL element | precision | recall | F1 |")
        L.append("|---|---|---|---|")
        for elem, lbl in (("tables", "Tables"), ("columns", "Columns"), ("joins", "Joins"),
                          ("filters", "Filters (WHERE)"), ("group_by", "Group by"),
                          ("order_by", "Order by"), ("aggregations", "Aggregations")):
            e = el[elem]
            miss = f" · missing `{e['missing']}`" if e.get("missing") else ""
            extra = f" · extra `{e['extra']}`" if e.get("extra") else ""
            L.append(f"| {lbl} | {e['precision']} | {e['recall']} | {e['f1']}{miss}{extra} |")

        if d.semantic_equivalent or (d.semantic_reason
                                     and "no id/name" not in d.semantic_reason):
            L.append(f"\n- **Schema-aware equivalence:** "
                     f"{'✅ EQUIVALENT' if d.semantic_equivalent else 'ℹ️ note'} — "
                     f"{d.semantic_reason}")

        L.append(f"\n- **Agent answer:** {(run.get('agent_answer') or '-')[:400]}\n")
    return L


# --------------------------------------------------------------------------- #
# JSON sidecar
# --------------------------------------------------------------------------- #
def build_json_dashboard(rows_out) -> dict:
    """Every METRIC_REGISTRY entry, averaged over evaluable rows — the machine-readable
    twin of render_answer_correctness/render_query_construction, so a new metric added to
    the registry appears here automatically with no other change."""
    det_rows = [r["det"] for r in rows_out]
    grade = _evaluable(rows_out)
    no_answer_ids = [r["id"] for r in rows_out if not r["det"].evaluable]
    n = len(det_rows)
    det_passed = sum(1 for d in det_rows if d.passed)
    core_passed = sum(1 for d in det_rows if d.core_answer_match)
    dash = {key: dm(rows_out, key) for key in METRIC_REGISTRY}
    dash["deterministic_pass_rate"] = round(det_passed / n, 3) if n else None
    dash["core_answer_pass_rate"] = round(core_passed / n, 3) if n else None
    dash["graded_on"] = len(grade)
    dash["not_evaluable"] = len(no_answer_ids)
    dash["not_evaluable_ids"] = no_answer_ids
    return dash


def build_json_rows(rows_out) -> list[dict]:
    return [{
        "id": r["id"],
        "question": r["item"]["question"],
        "passed": r["det"].passed,
        "verdict": r["det"].verdict,
        "confidence": r["det"].confidence,
        "diagnosis": r["det"].diagnosis,
        "evaluable": r["det"].evaluable,
        "semantic_equivalent": r["det"].semantic_equivalent,
        "core_answer_match": r["det"].core_answer_match,
        "structural_similarity": r["det"].structural_similarity,
        "exact_sql_match": r["det"].exact_sql_match,
        "metrics": r["det"].metrics,
    } for r in rows_out]
