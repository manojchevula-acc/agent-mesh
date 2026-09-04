"""Step 3 of the eval — diff the agent's runs against the gold set and report.

Reads the two files the previous steps produced:
    eval/datasets/gold_dynamic.yaml   question + gold_sql + gold_result   (materialize_gold.py)
    eval/results/agent_runs/agent_runs.yaml      question + agent_sql + agent_result (run_agent.py)

and writes a tabular markdown analysis to eval/results/llm_comparison/EVAL_REPORT.md.

TWO KINDS OF SIGNAL, DELIBERATELY SEPARATED
-------------------------------------------
DETERMINISTIC (free, exact, authoritative):
  data_match       — do the agent's rows equal gold's rows? Compared BY VALUE, ignoring
                     column names and column order, rounded to numeric_tolerance, and
                     order-insensitive unless the question is a ranking. This is the
                     ground truth for "did it get the right answer", and no LLM opinion
                     overrides it.
  sql_identical    — are the two queries the same modulo formatting/case? (sqlglot
                     normalisation, not string equality)
  table/column recall — did the agent read what gold read? (sqlglot)

LLM-JUDGED (costs tokens, explains rather than decides):
  sql_verdict      — IDENTICAL / EQUIVALENT / DIFFERENT, with a reason. This is the thing
                     no amount of string comparison can do: deciding that
                     `SUM(CASE WHEN x THEN 1 ELSE 0 END)/COUNT(*)` and
                     `AVG(x = 'Won')` express the same intent.
  data_reason      — a sentence explaining HOW the two result sets differ when they do.

Why not let the LLM decide data correctness: the rows are right there and comparing them
is exact and free — asking a model to eyeball 17 rows of decimals invites a wrong verdict
on a question we can simply answer. The LLM is used where judgement is actually required
(is this different SQL still correct?) and to explain, not to adjudicate the arithmetic.

The overall PASS/FAIL is therefore driven by the DETERMINISTIC data match. The LLM's SQL
verdict is what tells you WHY: a FAIL with sql_verdict=DIFFERENT is a logic error; a PASS
with sql_verdict=DIFFERENT is the agent finding a legitimate alternative route to the same
answer — which is exactly what a text-to-SQL eval must not punish.

DETERMINISTIC EVALUATOR (eval/deterministic/) — the LLM judge's transparent counterpart
---------------------------------------------------------------------------------------
Alongside the coarse deterministic signals above, every question is graded by the
deterministic evaluation layer: structural SQL precision/recall/F1 per schema element
(tables, columns, joins, filters, group-by, order-by, aggregations), result-set similarity
(row P/R/F1, cell accuracy, exact-match, Jaccard, fuzzy), and schema-aware id<->name
equivalence — composed into its OWN PASS/FAIL + confidence, with NO LLM involved. The
report then sets that verdict beside the LLM judge's data_verdict and quantifies their
agreement (Cohen's Kappa, stricter/leaner counts, the exact ids where they diverge) so the
strengths and blind spots of each evaluator are visible. See eval/deterministic/__init__.py.

THIS SCRIPT ALWAYS RUNS THE LLM JUDGE — that comparison is its entire purpose. For a
deterministic-only pass (no LLM, no tokens, its own report/output folder), use
eval/deterministic_eval.py instead; it shares the same eval/deterministic/ evaluator and
eval/deterministic/report.py rendering, so the "Deterministic evaluation" section here and
there are always computed identically.

ONE REPORT PER --runs FILE — evaluating a different recorded-runs file writes a
DIFFERENTLY-NAMED report (tag derived from the --runs filename, e.g. agent_runs_JOIN.yaml
-> EVAL_REPORT_JOIN.md) instead of overwriting the last one. Pass --tag to name it
explicitly. Re-running against the SAME --runs file still overwrites — but see --ids below,
which makes that overwrite a targeted UPDATE rather than a truncation.

--ids REFRESHES, IT DOES NOT FILTER THE REPORT DOWN — the report always covers every id in
--runs (so re-running on a subset never shrinks the file). What --ids controls is which ids
get a FRESH LLM judge call; every other id keeps its judge verdict from the previous
EVAL_REPORT.json for that same --runs/--tag (its deterministic metrics are still recomputed
fresh either way — they're free and exact, never cached). So after re-running one question
through eval/run_agent.py, `--ids <that id>` regrades just it — new SQL, new data, new judge
verdict — while every other row's judge verdict is carried over unchanged. Omit --ids to
judge everyone fresh, same as before.

Run:
    .venv/Scripts/python.exe eval/compare_llm.py                 # LLM + deterministic, everyone judged fresh
    .venv/Scripts/python.exe eval/compare_llm.py --ids D01,D02   # only D01/D02 rejudged; report still has everyone
    .venv/Scripts/python.exe eval/compare_llm.py --pause 3       # rate-limit friendly
    .venv/Scripts/python.exe eval/compare_llm.py --runs eval/results/agent_runs/agent_runs_JOIN.yaml
"""

from __future__ import annotations

import argparse
import decimal
import itertools
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import yaml  # noqa: E402

from eval.deterministic import agreement  # noqa: E402
from eval.deterministic import report as det_report  # noqa: E402
from eval.deterministic.evaluator import DeterministicEvaluator  # noqa: E402
from eval.sql_introspect import overlap  # noqa: E402

GOLD_PATH = HERE / "datasets" / "gold_dynamic.yaml"
RUNS_PATH = HERE / "results" / "agent_runs" / "agent_runs.yaml"
OUT_DIR = HERE / "results" / "llm_comparison"
REPORT_BASENAME = "EVAL_REPORT"


# --------------------------------------------------------------------------- #
# Deterministic comparison
# --------------------------------------------------------------------------- #
def _cell(v):
    """One value, coerced. MySQL hands back Decimals (sometimes as str) and 0/1 for
    booleans, so numbers become floats; anything else compares as text."""
    if isinstance(v, bool):
        return float(v)                       # gold 1 and agent True are the same answer
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _project(rows: list[dict], cols: list):
    """Rows reduced to value-tuples in a FIXED column order."""
    return [tuple(_cell(r.get(c)) for c in cols) for r in rows]


def _tolerance_for(gold_value: float, abs_floor: float) -> float:
    """How far may the agent's number sit from gold's and still be the same answer?

    Derived from the PRECISION GOLD REPORTED, because that is what the difference usually
    is. `ROUND(AVG(gap), 1)` publishes 16.2 for a true 16.25 — the agent's unrounded 16.25
    is not a different answer, it is the same one at full precision. Gold showing one
    decimal therefore admits +/-0.05; two decimals admits +/-0.005; an integer admits
    +/-0.5.

    A relative tolerance cannot express this: rounding error is a fixed absolute quantity
    (half of the last published digit), so a percentage band is simultaneously too tight
    for small values and too loose for large ones. A flat 0.2% passed 43.75-vs-43.8 while
    failing the identical 16.25-vs-16.2.

    `abs_floor` (the dataset's numeric_tolerance) is the minimum, so a gold quoted to many
    decimals never becomes stricter than the dataset intends.
    """
    try:
        exp = decimal.Decimal(str(gold_value)).as_tuple().exponent
    except (decimal.InvalidOperation, ValueError):
        return abs_floor
    dp = -exp if isinstance(exp, int) and exp < 0 else 0
    return max(abs_floor, 0.5 * (10.0 ** -dp))


def _cells_equal(g, a, abs_tol: float) -> bool:
    """Is the agent's value the same ANSWER as gold's?"""
    if isinstance(g, str) or isinstance(a, str):
        return str(g) == str(a)
    # 1e-9 absorbs binary-float noise at the tolerance boundary (16.25 vs 16.2 is exactly
    # 0.05 away, and must compare <= 0.05).
    return abs(g - a) <= _tolerance_for(g, abs_tol) + 1e-9


def _sort_key(row):
    """Stable ordering for order-insensitive comparison. Rounded so two rows that are
    equal within tolerance sort adjacently rather than being torn apart by float noise."""
    return tuple(f"{v:.3f}" if isinstance(v, float) else str(v) for v in row)


def _rows_equal(gold_rows, agent_rows, order_sensitive: bool, abs_tol: float) -> bool:
    if not order_sensitive:
        gold_rows = sorted(gold_rows, key=_sort_key)
        agent_rows = sorted(agent_rows, key=_sort_key)
    return all(
        len(g) == len(a) and all(_cells_equal(gv, av, abs_tol) for gv, av in zip(g, a))
        for g, a in zip(gold_rows, agent_rows)
    )


def result_match(gold, got, *, order_sensitive=False, numeric_tolerance=0.01) -> bool:
    """Do the agent's rows carry the same answer as gold's?

    Compared by VALUE, never by SQL text. Two properties matter and they pull against each
    other, so the alignment is done deliberately rather than by flattening everything:

    FAIR TO THE AGENT — a correct query must not fail on cosmetics:
      * different column ALIASES (gold `avg_expected_margin_pct`, agent `margin`)
      * EXTRA columns the question did not require (agent also returns `deal_count`)
      * different ROW ORDER, unless the question is a ranking (order_sensitive)
      * rounding to numeric_tolerance

    STRICT WHERE IT COUNTS — a wrong query must not pass:
      * values are matched through a column mapping that must hold across EVERY row, so a
        query that swaps `min_margin` and `max_margin` is caught rather than being
        flattened into the same unordered bag of numbers.

    Alignment is name-first: when the agent's column names cover gold's, that mapping is
    unambiguous and is the only one tried — so same-name-different-value is a real failure.
    Only when names differ (a rename) do we search column permutations for one that fits,
    which is what keeps aliasing free. A rename COMBINED with a value swap is inherently
    undecidable without semantics; the LLM verdict is the backstop there.
    """
    if gold is None or got is None:
        return False
    if len(gold) != len(got):
        return False
    if not gold:
        return True                            # both empty

    gcols = list(gold[0].keys())
    acols = list(got[0].keys())
    if len(acols) < len(gcols):
        return False                           # agent is missing columns gold needed
    gproj = _project(gold, gcols)

    by_name = {str(c).lower(): c for c in acols}
    if all(str(c).lower() in by_name for c in gcols):
        mapped = [by_name[str(c).lower()] for c in gcols]
        return _rows_equal(gproj, _project(got, mapped), order_sensitive, numeric_tolerance)

    # Renamed columns: find any consistent assignment of gold's columns onto the agent's.
    # Bounded so a wide result set cannot blow up the factorial search.
    if len(acols) > 8:
        return False
    for perm in itertools.permutations(acols, len(gcols)):
        if _rows_equal(gproj, _project(got, list(perm)), order_sensitive, numeric_tolerance):
            return True
    return False


def sql_identical(a: str, b: str) -> bool:
    """Same query modulo formatting/whitespace/case — via sqlglot normalisation, so
    `select X from t` and `SELECT x\n FROM t` count as identical, but a reordered GROUP BY
    does not (that is the LLM's job to call EQUIVALENT)."""
    if not a or not b:
        return False
    try:
        import sqlglot
        na = sqlglot.parse_one(a, dialect="mysql").sql(dialect="mysql", normalize=True)
        nb = sqlglot.parse_one(b, dialect="mysql").sql(dialect="mysql", normalize=True)
        return na.lower() == nb.lower()
    except Exception:  # noqa: BLE001
        return " ".join(a.lower().split()) == " ".join(b.lower().split())


# --------------------------------------------------------------------------- #
# LLM judge
# --------------------------------------------------------------------------- #
_JUDGE_PROMPT = """You are auditing a text-to-SQL agent. Compare its query against a \
verified reference query for the SAME question.

SCHEMA of the tables involved — read the [NOTE: ...] markers carefully, they define which
columns are pre-aggregated and must not be re-averaged:
{schema}

QUESTION:
{question}

REFERENCE (gold) SQL:
{gold_sql}

AGENT SQL:
{agent_sql}

REFERENCE (gold) ROWS — verified correct, from the live database:
{gold_rows}

AGENT ROWS — what the agent's SQL actually returned:
{agent_rows}

Judge two things independently.

1. sql_verdict — how do the QUERIES relate?
   "IDENTICAL"  : same query apart from formatting, aliasing, casing or column order.
   "EQUIVALENT" : written differently but computes the same thing for this question —
                  a different but equal filter, a different join order, an aggregate
                  expressed another way, or a DIFFERENT TABLE that carries the same
                  column at the same grain. Different SQL that is still correct is
                  EQUIVALENT, not DIFFERENT.
   "DIFFERENT"  : answers a different question — wrong grain, wrong filter, a missing
                  GROUP BY, or averaging a column the schema marks as pre-aggregated.

   IGNORE these entirely — they are never grounds for DIFFERENT:
     * a trailing LIMIT (the validator appends LIMIT 50 to every query automatically)
     * output column aliases, whitespace, casing, ROUND() to a different precision
   If the rows MATCH, the queries are almost certainly IDENTICAL or EQUIVALENT — say
   DIFFERENT only if you can name the specific clause that changes the meaning.
   Matching rows are EVIDENCE OF EQUIVALENCE, not a coincidence: do not explain a match
   away as luck. Two sources with the SAME grain exposing the SAME column give the same
   answer by construction — that is equivalence, not chance.

   Worked examples of the boundary:
     gold : SELECT AVG(expected_margin_pct) FROM fab_semantic.margin_analysis WHERE ...
     agent: SELECT AVG(expected_margin_pct) FROM fab_semantic.pricing_recommendation_view WHERE ...
     -> EQUIVALENT. The schema shows both are "one row per historical deal" and both carry
        expected_margin_pct. A different view at the same grain is a legitimate route.

     gold : SELECT SUM(won_deals)*100.0/SUM(total_deals) FROM fab_semantic.customer_360
     agent: SELECT AVG(win_rate_pct) FROM fab_semantic.customer_360
     -> DIFFERENT. win_rate_pct is marked pre-aggregated per customer, so AVG() of it is an
        unweighted average-of-averages, not the population rate.

2. data_verdict — do the ROW SETS agree?
   "MATCH"    : same values (ignore column-name and row-order differences unless the
                question asks for a ranking; allow small rounding differences).
   "MISMATCH" : the values genuinely differ.

Be concrete: name the clause, column or number responsible, and prefer the SEMANTIC cause
over a cosmetic one. Keep each reason under 20 words.

Reply with ONE line of JSON and nothing else:
{{"sql_verdict":"IDENTICAL|EQUIVALENT|DIFFERENT","sql_reason":"...",\
"data_verdict":"MATCH|MISMATCH","data_reason":"..."}}"""


def _schema_for(item: dict, run: dict) -> str:
    """Render the governed schema for just the tables the two queries touch.

    Reuses the agent's own renderer, so the judge sees the SAME column descriptions and
    [NOTE: pre-aggregated ...] markers the SQL generator was given. Without this the judge
    is comparing identifier strings with no idea what they mean — it cannot tell that
    margin_analysis and pricing_recommendation_view both carry expected_margin_pct at deal
    grain (equivalent), nor that AVG(win_rate_pct) is the classic average-of-averages bug.
    """
    try:
        from sql_agent.semantic_layer.renderer import render_schema_context
        tables = {str(t).lower() for t in (item.get("gold_tables") or [])}
        tables |= {str(t).lower() for t in (run.get("agent_tables") or [])}
        return render_schema_context(tables=sorted(tables)) if tables else "(unavailable)"
    except Exception:  # noqa: BLE001 — the judge is still useful without it
        return "(unavailable)"


def judge(item: dict, run: dict, cap: int = 15) -> dict:
    """Ask the LLM to classify the SQL relationship and explain any data difference.

    Fails OPEN: any judging error yields verdict "?" rather than a failure, so a flaky
    judge never manufactures a result the agent did not cause. The deterministic data
    match is unaffected either way.
    """
    try:
        from sql_agent.llm import Step, complete
        prompt = _JUDGE_PROMPT.format(
            schema=_schema_for(item, run),
            question=item["question"],
            gold_sql=(item.get("gold_sql") or "").strip(),
            agent_sql=(run.get("agent_sql") or "(the agent produced no SQL)").strip(),
            gold_rows=json.dumps((item.get("gold_result") or [])[:cap], default=str),
            agent_rows=json.dumps((run.get("agent_result") or [])[:cap], default=str),
        )
        text = complete(Step.JUDGE, prompt)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"sql_verdict": "?", "sql_reason": "judge unparseable",
                    "data_verdict": "?", "data_reason": ""}
        d = json.loads(m.group(0))
        return {
            "sql_verdict": str(d.get("sql_verdict", "?")).upper(),
            "sql_reason": str(d.get("sql_reason", ""))[:160],
            "data_verdict": str(d.get("data_verdict", "?")).upper(),
            "data_reason": str(d.get("data_reason", ""))[:160],
        }
    except Exception as exc:  # noqa: BLE001 — never fail the report on the judge itself
        return {"sql_verdict": "?", "sql_reason": f"judge error: {type(exc).__name__}",
                "data_verdict": "?", "data_reason": ""}


# --------------------------------------------------------------------------- #
# Diagnosis
# --------------------------------------------------------------------------- #
def diagnose(run: dict, cmp: dict) -> str:
    """The single most informative outcome label.

    Ordered by what actually happened: first the failures that stop a query existing at
    all, then the DATA verdict, and only then the table choice. Table selection is
    deliberately NOT consulted before the data verdict — a query that reads a different
    table and still returns gold's rows is correct, not "wrong-tables". Two views can
    carry the same deal-grain column (expected_margin_pct lives on both margin_analysis
    and pricing_recommendation_view), so a different route to an identical answer is a
    legitimate solution and must not be labelled a failure. Table recall is still reported
    on its own; here it only refines an already-decided verdict.
    """
    if run["status"] == "rate-limited":
        return "rate-limited"
    if run["status"] == "agent-error":
        return "agent-error"
    if run["status"] == "no-tool":
        return "no-tool-called"
    if run["status"] == "no-sql":
        return "no-sql-produced"
    if run["status"] == "sql-error":
        return "sql-execution-failed"

    thin_tables = cmp["table_recall"] is not None and cmp["table_recall"] < 1.0
    if not cmp["data_match"]:
        # Wrong answer — say which of the two causes it was.
        return "wrong-tables" if thin_tables else "semantically-wrong-sql"
    # Right answer.
    if cmp["sql_identical"]:
        return "correct-identical-sql"
    return "correct-via-different-tables" if thin_tables else "correct-equivalent-sql"


def _llm_pass(verdict: dict):
    """The LLM judge's INDEPENDENT correctness verdict, as a tri-state.

    The judge reports data_verdict MATCH/MISMATCH; that — not the deterministic row check —
    is the LLM's own opinion of correctness, and comparing IT against the deterministic
    evaluator is the whole point of running both. Returns True/False, or None when the
    judge was unavailable/unparseable ("?") or disabled ("-"), so those rows are excluded
    from the agreement statistic rather than being silently scored as failures."""
    dv = str(verdict.get("data_verdict", "?")).upper()
    if dv == "MATCH":
        return True
    if dv == "MISMATCH":
        return False
    return None


def _fmt_rows(rows, limit=5) -> str:
    if rows is None:
        return "_(none)_"
    if not rows:
        return "_(empty)_"
    body = "<br>".join("`" + ", ".join(f"{k}={v}" for k, v in r.items())[:150] + "`"
                       for r in rows[:limit])
    if len(rows) > limit:
        body += f"<br>_… {len(rows)-limit} more_"
    return body


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diff agent runs against the gold set; write a markdown analysis.")
    ap.add_argument("--gold", default=str(GOLD_PATH))
    ap.add_argument("--runs", default=str(RUNS_PATH))
    ap.add_argument("--ids", help="only re-judge these ids, comma-separated — the report "
                    "still covers every id in --runs; unlisted ids keep their cached judge "
                    "verdict from the previous report")
    ap.add_argument("--pause", type=float, default=0.0,
                    help="seconds between judge calls (rate limits)")
    ap.add_argument("--tag", help="name the output report explicitly (default: derived "
                    "from the --runs filename, e.g. agent_runs_JOIN.yaml -> ..._JOIN.md)")
    args = ap.parse_args()

    tag = args.tag if args.tag is not None else det_report.derive_tag(args.runs)
    REPORT_PATH, REPORT_JSON = det_report.report_paths(OUT_DIR, REPORT_BASENAME, tag)

    gold_doc = yaml.safe_load(Path(args.gold).read_text(encoding="utf-8"))
    gold = {i["id"]: i for i in gold_doc["items"]}
    runs_path = Path(args.runs)
    if not runs_path.exists():
        raise SystemExit(f"No agent runs at {runs_path}. Run eval/run_agent.py first.")
    runs_doc = yaml.safe_load(runs_path.read_text(encoding="utf-8")) or {}
    runs = runs_doc.get("runs", [])
    if not runs:
        raise SystemExit(f"No runs recorded in {runs_path}. Run eval/run_agent.py first.")
    # Coverage is measured against the WHOLE gold set, and the report below covers every run
    # in --runs — --ids does NOT filter this list, it only selects who gets rejudged (see
    # module docstring), so this is a property of the dataset, not of this invocation.
    covered = {r["gold_id"] for r in runs}
    unrun = [i for i in gold if i not in covered]

    # --ids selects who gets a FRESH judge call, not who's in the report — the report always
    # covers every run in --runs (see module docstring). None => judge everyone (unchanged
    # default behaviour); an explicit set that matches nothing is worth a warning since it
    # usually means a typo, but is not fatal — everyone just keeps their cached verdict.
    judge_ids: set[str] | None = None
    if args.ids:
        judge_ids = {t.strip().lower() for t in args.ids.split(",")}
        matched = {r["id"].lower() for r in runs} | {r["gold_id"].lower() for r in runs}
        unmatched = judge_ids - matched
        if unmatched:
            print(f"[warn] --ids not found among recorded runs: {', '.join(sorted(unmatched))}")

    # Cached judge verdicts from the PREVIOUS report for this same --runs/--tag, keyed by id.
    # Only the judge (sql_verdict/sql_reason/data_verdict/data_reason) is cached — everything
    # else is recomputed fresh below regardless, since it's free and exact.
    verdict_cache: dict[str, dict] = {}
    if REPORT_JSON.exists():
        try:
            prior = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
            for row in prior.get("rows", []):
                verdict_cache[row["id"]] = {
                    "sql_verdict": row.get("sql_verdict", "?"),
                    "sql_reason": row.get("sql_reason", ""),
                    "data_verdict": row.get("data_verdict", "?"),
                    "data_reason": row.get("data_reason", ""),
                }
        except Exception as exc:  # noqa: BLE001 — a corrupt/missing prior report just means no cache
            print(f"[warn] could not read prior report for judge cache | {exc}")

    defaults = (gold_doc.get("meta") or {}).get("defaults") or {}
    tol = defaults.get("numeric_tolerance", 0.01)

    if judge_ids is None:
        print(f"Comparing {len(runs)} agent run(s) against {len(gold)} gold item(s) "
              f"— with LLM judge")
    else:
        print(f"Comparing {len(runs)} agent run(s) against {len(gold)} gold item(s) — "
              f"re-judging {', '.join(sorted(args.ids.split(',')))}, "
              f"{len(runs) - len(judge_ids)} other row(s) keep their cached verdict")
    if unrun:
        print(f"Coverage: {len(covered)}/{len(gold)} gold questions have a recorded run — "
              f"{len(unrun)} NOT run: {', '.join(unrun)}")

    # One evaluator for the whole run so the schema-aware matcher's lookup cache (and its
    # single DB touch per entity) is shared across every question.
    det_ev = DeterministicEvaluator(numeric_tolerance=tol)

    rows_out = []
    stale: list[str] = []
    for run in runs:
        item = gold.get(run["gold_id"])
        if not item:
            print(f"[warn] {run['id']}: no gold item '{run['gold_id']}' — skipped")
            continue
        # The recorded run answers the question as it was WORDED AT THE TIME. If the gold
        # question has since been edited, grading that run against the new gold compares
        # answers to two different questions — silently, and usually as a spurious failure.
        # Variants deliberately differ from the parent's wording, so only exact items are
        # checked.
        if "::" not in run["id"] and run["question"].strip() != item["question"].strip():
            stale.append(run["id"])
        order_sensitive = bool(item.get("order_sensitive", False))
        gold_rows = item.get("gold_result")
        agent_rows = run.get("agent_result")

        g_tables = {str(t).lower() for t in item.get("gold_tables") or []}
        g_cols = {str(c).lower() for c in item.get("gold_columns") or []}
        a_tables = {str(t).lower() for t in run.get("agent_tables") or []}
        a_cols = {str(c).lower() for c in run.get("agent_columns") or []}
        t_ov = overlap(g_tables, a_tables)
        c_ov = overlap(g_cols, a_cols)

        cmp = {
            "data_match": result_match(gold_rows, agent_rows,
                                       order_sensitive=order_sensitive,
                                       numeric_tolerance=tol),
            "sql_identical": sql_identical(item.get("gold_sql", ""),
                                           run.get("agent_sql", "")),
            "table_recall": t_ov["recall"], "tables_missing": t_ov["missing"],
            "tables_extra": t_ov["extra"],
            "column_recall": c_ov["recall"], "columns_missing": c_ov["missing"],
        }
        needs_judge = (judge_ids is None
                      or run["id"].lower() in judge_ids
                      or run["gold_id"].lower() in judge_ids)
        if needs_judge:
            verdict = judge(item, run)
        else:
            verdict = verdict_cache.get(run["id"]) or {
                "sql_verdict": "?", "sql_reason": "not re-judged this run (no prior cache)",
                "data_verdict": "?", "data_reason": ""}
        cmp["diagnosis"] = diagnose(run, cmp)
        passed = bool(cmp["data_match"])
        # Deterministic evaluator — the LLM judge's transparent counterpart. Runs the full
        # metric suite (structural SQL P/R/F1, result-set similarity, schema-aware id<->name
        # equivalence) and renders its OWN PASS/FAIL + confidence, to be compared with the
        # LLM's data_verdict below. Always recomputed fresh — it's free and exact, so a
        # row's --ids status never lets its deterministic grade go stale.
        det = det_ev.evaluate(item, run)
        llm_pass = _llm_pass(verdict)
        agree = (llm_pass is None) or (llm_pass == det.passed)
        rows_out.append({"id": run["id"], "item": item, "run": run, "cmp": cmp,
                         "verdict": verdict, "passed": passed, "det": det,
                         "llm_pass": llm_pass, "det_pass": det.passed, "agree": agree})
        flag = "  <== STALE" if run["id"] in stale else ""
        disagree = "" if agree else "  <== LLM/DET DISAGREE"
        cached = "  (cached judge)" if not needs_judge else ""
        print(f"[{'PASS' if passed else 'FAIL'}] {run['id']:12s} "
              f"{cmp['diagnosis']:24s} sql={verdict['sql_verdict']:10s} "
              f"data={'MATCH' if cmp['data_match'] else 'MISMATCH'} "
              f"det={det.verdict}({det.confidence}){flag}{disagree}{cached}")
        if needs_judge and args.pause:
            time.sleep(args.pause)

    if stale:
        print(f"\n!! {len(stale)} run(s) answer an OUTDATED question — the gold wording "
              f"changed since they were recorded, so their verdicts are meaningless:")
        for sid in stale:
            print(f"   {sid}")
        print(f"   Re-run:  .venv/Scripts/python.exe eval/run_agent.py "
              f"--ids {','.join(stale)}")

    # ---------------- report ----------------
    n = len(rows_out)
    passed = sum(1 for r in rows_out if r["passed"])
    execd = sum(1 for r in rows_out if r["run"]["status"] == "success")
    t_vals = [r["cmp"]["table_recall"] for r in rows_out
              if r["cmp"]["table_recall"] is not None]
    c_vals = [r["cmp"]["column_recall"] for r in rows_out
              if r["cmp"]["column_recall"] is not None]

    def mean(v):
        return round(sum(v) / len(v), 3) if v else None

    L = [f"# Agent vs Gold — {gold_doc['meta']['version']}\n",
         f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · {n} question(s) · "
         f"snapshot `{gold_doc['meta'].get('db_snapshot')}`_\n",
         "Pass/fail is decided by the **deterministic row comparison** (exact, "
         "value-based, order- and column-name-insensitive). The **LLM verdict** explains "
         "*why* — in particular whether differently-written SQL is still correct.\n"]
    if stale:
        L += [f"> ⚠️ **{len(stale)} run(s) are STALE** — `{', '.join(stale)}` answer an "
              f"older wording of their question, so their verdicts below mean nothing. "
              f"Re-run: `eval/run_agent.py --ids {','.join(stale)}`\n"]
    if unrun:
        # Without this, a pass rate over a favourable subset reads exactly like a pass rate
        # over the benchmark. The untested difficulties are named because WHICH questions
        # are missing decides whether the number is representative — a run that skipped
        # every join and subquery is not 73% correct at text-to-SQL.
        missing_diff = sorted({str(gold[i].get("difficulty", "?")) for i in unrun})
        L += [f"> ⚠️ **PARTIAL RUN — {len(covered)}/{len(gold)} gold questions "
              f"({round(100*len(covered)/len(gold))}%) have been run.** The pass rate below "
              f"is over the **{n} evaluated here**, NOT over the benchmark, and is not "
              f"comparable to a full run.\n>\n"
              f"> Not run: `{', '.join(unrun)}`\n>\n"
              f"> Untested difficulties: {', '.join('`'+d+'`' for d in missing_diff)}\n>\n"
              f"> Complete it with: `eval/run_agent.py --all --pause 6 --resume`\n"]
    L += ["## Summary\n",
         "| Metric | Value |", "|---|---|",
         f"| Gold benchmark size | {len(gold)} |",
         f"| Questions evaluated here | {n} |",
         f"| Benchmark coverage | {len(covered)}/{len(gold)} "
         f"({round(100*len(covered)/len(gold))}%) |",
         f"| SQL execution success rate | {round(execd/n, 3) if n else '—'} ({execd}/{n}) |",
         f"| Result-equivalence accuracy (**pass rate**) | **{round(passed/n, 3) if n else '—'}** ({passed}/{n}) |",
         f"| Table-selection accuracy (recall) | {mean(t_vals) if t_vals else '—'} |",
         f"| Column-selection accuracy (recall) | {mean(c_vals) if c_vals else '—'} |"]

    modes: dict[str, int] = {}
    for r in rows_out:
        modes[r["cmp"]["diagnosis"]] = modes.get(r["cmp"]["diagnosis"], 0) + 1
    L += ["\n## Outcome breakdown\n", "| Diagnosis | n |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(modes.items(), key=lambda kv: -kv[1])]

    sv: dict[str, int] = {}
    for r in rows_out:
        k = r["verdict"]["sql_verdict"]
        sv[k] = sv.get(k, 0) + 1
    L += ["\n## LLM query-equivalence verdicts\n", "| Verdict | n | Meaning |",
          "|---|---|---|"]
    meaning = {
        "IDENTICAL": "same query bar formatting",
        "EQUIVALENT": "written differently, computes the same thing",
        "DIFFERENT": "answers a different question",
        "?": "judge unavailable / unparseable",
    }
    L += [f"| {k} | {v} | {meaning.get(k,'')} |"
          for k, v in sorted(sv.items(), key=lambda kv: -kv[1])]

    # ---------------- deterministic evaluation (shared with eval/deterministic_eval.py) --
    # Rendered via eval/deterministic/report.py so this section is computed and worded
    # IDENTICALLY to the standalone deterministic-only report — the two can never drift.
    det_rows = [r["det"] for r in rows_out]
    det_passed = sum(1 for d in det_rows if d.passed)
    core_passed = sum(1 for d in det_rows if d.core_answer_match)
    nden = len(det_rows)
    L += det_report.render_headline(rows_out)
    L += det_report.render_answer_correctness(rows_out)
    L += det_report.render_query_construction(rows_out)

    # ---------------- LLM vs deterministic agreement ----------------
    pairs = [{"id": r["id"], "llm_pass": r["llm_pass"], "det_pass": r["det_pass"]}
             for r in rows_out]
    agr = agreement.compute_agreement(pairs)
    L += ["\n## LLM vs deterministic agreement\n",
          "_How often the LLM judge and the deterministic evaluator reach the SAME "
          "PASS/FAIL, corrected for chance. Kappa near 1 = concordant; near 0 = no better "
          "than chance. Rows where the judge gave no verdict are excluded._\n",
          "| | Value |", "|---|---|",
          f"| Comparable questions (both rendered a verdict) | {agr.n} |",
          f"| Both PASS | {agr.both_pass} |",
          f"| Both FAIL | {agr.both_fail} |",
          f"| LLM PASS, deterministic FAIL | {agr.llm_only_pass} |",
          f"| Deterministic PASS, LLM FAIL | {agr.det_only_pass} |",
          f"| Raw agreement | {round(agr.raw_agreement, 3)} |",
          f"| **Cohen's Kappa** | **{'—' if agr.cohen_kappa is None else round(agr.cohen_kappa, 3)}** "
          f"({agr.kappa_interpretation}) |",
          f"| LLM pass rate | {round(agr.llm_pass_rate, 3)} |",
          f"| Deterministic pass rate | {round(agr.deterministic_pass_rate, 3)} |",
          f"| Stricter evaluator | {agr.stricter} |"]
    if agr.disagreement_ids.get("llm_pass_deterministic_fail"):
        ids = ", ".join(f"`{i}`" for i in agr.disagreement_ids["llm_pass_deterministic_fail"])
        L += [f"\n**LLM lenient / deterministic strict** — {ids}. Usually a semantic "
              f"equivalence the metrics cannot yet see (a candidate rule for "
              f"`eval/deterministic/schema_semantic.py`), or the judge overlooking a real "
              f"difference. Inspect each below."]
    if agr.disagreement_ids.get("deterministic_pass_llm_fail"):
        ids = ", ".join(f"`{i}`" for i in agr.disagreement_ids["deterministic_pass_llm_fail"])
        L += [f"\n**Deterministic lenient / LLM strict** — {ids}. Usually the metrics "
              f"proving equivalence (identical rows, or id<->name resolution) while the "
              f"judge was misled by cosmetic SQL differences."]

    L += ["\n## Per-question results\n",
          "| id | Question | Pass | Diagnosis | SQL verdict (LLM) | Data | Det | Conf | "
          "Agree | Tables | Cols | Rows g/a |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows_out:
        it, run, c, v, d = r["item"], r["run"], r["cmp"], r["verdict"], r["det"]
        gr = len(it.get("gold_result") or [])
        ar = "-" if run.get("agent_result") is None else len(run["agent_result"])
        agree_mark = "=" if r["agree"] else "≠"
        L.append(
            f"| {r['id']} | {it['question'][:52]} | {'✅' if r['passed'] else '❌'} | "
            f"{c['diagnosis']} | {v['sql_verdict']} | "
            f"{'MATCH' if c['data_match'] else 'MISMATCH'} | "
            f"{'✅' if d.passed else '❌'} | {d.confidence} | {agree_mark} | "
            f"{c['table_recall']} | {c['column_recall']} | {gr}/{ar} |")

    L.append("\n## Per-question detail\n")
    for r in rows_out:
        it, run, c, v, d = r["item"], r["run"], r["cmp"], r["verdict"], r["det"]
        # Header carries the DETERMINISTIC verdict — the corrected pass/fail (it credits the
        # schema-aware id<->name equivalence the raw row match cannot see).
        L.append(f"### {r['id']} — Deterministic: {'✅ PASS' if d.passed else '❌ FAIL'} "
                 f"(confidence {d.confidence} · {d.diagnosis})\n")
        L.append(f"**Question:** {run['question']}\n")
        L.append(f"- Tools called: `{', '.join(run.get('tools_called') or []) or '-'}` · "
                 f"status `{run['status']}` · {run.get('latency_ms','?')}ms")
        L.append(f"\n**Gold SQL**\n```sql\n{(it.get('gold_sql') or '-').strip()}\n```")
        L.append(f"**Agent SQL**\n```sql\n{(run.get('agent_sql') or '-').strip()}\n```")
        if run.get("exec_error"):
            L.append(f"> **SQL execution FAILED:** `{run['exec_error']}`")
        L.append(f"- **Gold tables:** `{sorted(it.get('gold_tables') or [])}` · "
                 f"**Agent tables:** `{run.get('agent_tables') or '-'}`"
                 + (f" · **missing** `{c['tables_missing']}`" if c["tables_missing"] else "")
                 + (f" · extra `{c['tables_extra']}`" if c["tables_extra"] else ""))
        L.append(f"- **Gold columns:** `{sorted(it.get('gold_columns') or [])}`")
        L.append(f"- **Agent columns:** `{run.get('agent_columns') or '-'}`"
                 + (f" · **missing** `{c['columns_missing']}`" if c["columns_missing"] else ""))
        L.append(f"\n| | Gold | Agent |\n|---|---|---|")
        L.append(f"| Result | {_fmt_rows(it.get('gold_result'))} | "
                 f"{_fmt_rows(run.get('agent_result'))} |")
        L.append(f"\n- **Data comparison (deterministic):** "
                 f"{'MATCH' if c['data_match'] else 'MISMATCH'}"
                 + (" — order-sensitive (ranking)" if it.get("order_sensitive") else ""))
        L.append(f"- **LLM SQL verdict:** `{v['sql_verdict']}` — {v['sql_reason']}")
        L.append(f"- **LLM data verdict:** `{v['data_verdict']}` — {v['data_reason']}")

        # ---- per-question deterministic METRICS (explicit, this question only) --------
        el, rd = d.sql_elements, d.result_detail
        L.append(f"\n**Deterministic evaluation — {'✅ PASS' if d.passed else '❌ FAIL'}** "
                 f"· confidence {d.confidence} · `{d.diagnosis}`"
                 + ("" if d.evaluable else " · _no result produced, not graded_"))
        if r["llm_pass"] is not None:
            L.append(f"- LLM verdict: **{'PASS' if r['llm_pass'] else 'FAIL'}** — "
                     f"{'✅ agrees' if r['agree'] else '⚠️ DISAGREES'} with deterministic")

        # Answer correctness — the metrics that decide pass/fail for THIS question.
        L.append("\n_Answer correctness (returned rows):_\n")
        L.append("| exact match | row precision | row recall | row F1 | cell accuracy "
                 "| Jaccard | fuzzy | semantic equiv |")
        L.append("|---|---|---|---|---|---|---|---|")
        sem_cell = ("✅ yes" if d.semantic_equivalent
                    else ("—" if d.metrics.get("semantic_equivalence") is None else "✗ no"))
        L.append(f"| {rd['exact_match']} | {rd['row_precision']} | {rd['row_recall']} | "
                 f"{rd['row_f1']} | {rd['cell_accuracy']} | {rd['jaccard']} | "
                 f"{rd['fuzzy_similarity']} | {sem_cell} |")

        # SQL construction — precision / recall / F1 for every schema element.
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

    # Same builder eval/deterministic_eval.py uses, so the machine-readable sidecar of both
    # reports is computed identically and can never drift apart.
    det_dashboard = det_report.build_json_dashboard(rows_out)
    no_answer = [d for d in det_rows if not d.evaluable]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(L), encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(
        {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "summary": {"gold_benchmark_size": len(gold),
                     "questions_evaluated": n, "passed": passed,
                     "pass_rate": round(passed / n, 3) if n else None,
                     "pass_rate_is_over": "questions_evaluated, NOT the full benchmark",
                     "benchmark_coverage": round(len(covered) / len(gold), 3),
                     "not_run": unrun, "stale": stale,
                     "sql_exec_rate": round(execd / n, 3) if n else None,
                     "table_recall": mean(t_vals), "column_recall": mean(c_vals),
                     "outcomes": modes},
         "deterministic_dashboard": det_dashboard,
         "llm_vs_deterministic_agreement": agr.as_dict(),
         "rows": [{"id": r["id"], "passed": r["passed"],
                   "llm_pass": r["llm_pass"], "deterministic_pass": r["det_pass"],
                   "agree": r["agree"],
                   "deterministic_verdict": r["det"].verdict,
                   "deterministic_confidence": r["det"].confidence,
                   "deterministic_diagnosis": r["det"].diagnosis,
                   "deterministic_evaluable": r["det"].evaluable,
                   "semantic_equivalent": r["det"].semantic_equivalent,
                   "core_answer_match": r["det"].core_answer_match,
                   "deterministic_metrics": r["det"].metrics,
                   **r["cmp"], **r["verdict"]}
                  for r in rows_out]},
        indent=2, default=str), encoding="utf-8")

    print(f"\nReport -> {REPORT_PATH}")
    print(f"JSON   -> {REPORT_JSON}")
    print(f"PASSED {passed}/{n}  ·  " + ", ".join(f"{k}={v}" for k, v in
                                                  sorted(modes.items(), key=lambda kv: -kv[1])))
    print(f"Deterministic (strict): {det_passed}/{nden} PASS  ·  "
          f"core-answer (lenient, ignores missing/extra columns): {core_passed}/{nden}"
          + (f"  ·  {len(no_answer)} produced no result" if no_answer else ""))
    print(f"LLM vs deterministic: agree {agr.both_pass + agr.both_fail}/{agr.n}, "
          f"kappa {'—' if agr.cohen_kappa is None else round(agr.cohen_kappa, 3)} "
          f"({agr.kappa_interpretation})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
