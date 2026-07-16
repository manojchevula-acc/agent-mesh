"""Step 3 of the eval — diff the agent's runs against the gold set and report.

Reads the two files the previous steps produced:
    eval/datasets/gold_dynamic.yaml   question + gold_sql + gold_result   (materialize_gold.py)
    eval/results/agent_runs.yaml      question + agent_sql + agent_result (run_agent.py)

and writes a tabular markdown analysis to eval/results/EVAL_REPORT.md.

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

Run:
    .venv/Scripts/python.exe eval/compare_llm.py                 # LLM + deterministic
    .venv/Scripts/python.exe eval/compare_llm.py --no-llm        # deterministic only, free
    .venv/Scripts/python.exe eval/compare_llm.py --ids D01,D02
    .venv/Scripts/python.exe eval/compare_llm.py --pause 3       # rate-limit friendly
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

from eval.sql_introspect import extract, overlap  # noqa: E402

GOLD_PATH = HERE / "datasets" / "gold_dynamic.yaml"
RUNS_PATH = HERE / "results" / "agent_runs.yaml"
REPORT_PATH = HERE / "results" / "EVAL_REPORT.md"
REPORT_JSON = HERE / "results" / "EVAL_REPORT.json"


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
        from sql_agent.llm import Step, get_llm
        prompt = _JUDGE_PROMPT.format(
            schema=_schema_for(item, run),
            question=item["question"],
            gold_sql=(item.get("gold_sql") or "").strip(),
            agent_sql=(run.get("agent_sql") or "(the agent produced no SQL)").strip(),
            gold_rows=json.dumps((item.get("gold_result") or [])[:cap], default=str),
            agent_rows=json.dumps((run.get("agent_result") or [])[:cap], default=str),
        )
        raw = get_llm(Step.JUDGE).invoke(prompt)
        text = raw.content if hasattr(raw, "content") else str(raw)
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
    ap.add_argument("--ids", help="only compare these ids, comma-separated")
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic comparison only — no tokens spent")
    ap.add_argument("--pause", type=float, default=0.0,
                    help="seconds between judge calls (rate limits)")
    args = ap.parse_args()

    gold_doc = yaml.safe_load(Path(args.gold).read_text(encoding="utf-8"))
    gold = {i["id"]: i for i in gold_doc["items"]}
    runs_path = Path(args.runs)
    if not runs_path.exists():
        raise SystemExit(f"No agent runs at {runs_path}. Run eval/run_agent.py first.")
    runs_doc = yaml.safe_load(runs_path.read_text(encoding="utf-8")) or {}
    runs = runs_doc.get("runs", [])
    # Coverage is measured against the WHOLE gold set, from the whole runs file — before
    # any --ids filter — because the question it answers ("how much of the benchmark has
    # actually been attempted?") is a property of the dataset, not of this invocation.
    covered = {r["gold_id"] for r in runs}
    unrun = [i for i in gold if i not in covered]
    if args.ids:
        want = {t.strip().lower() for t in args.ids.split(",")}
        runs = [r for r in runs if r["id"].lower() in want or r["gold_id"].lower() in want]
    if not runs:
        raise SystemExit("No agent runs matched the selection.")

    defaults = (gold_doc.get("meta") or {}).get("defaults") or {}
    tol = defaults.get("numeric_tolerance", 0.01)

    print(f"Comparing {len(runs)} agent run(s) against {len(gold)} gold item(s)"
          f"{' — deterministic only' if args.no_llm else ' — with LLM judge'}")
    if unrun:
        print(f"Coverage: {len(covered)}/{len(gold)} gold questions have a recorded run — "
              f"{len(unrun)} NOT run: {', '.join(unrun)}")

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
        verdict = ({"sql_verdict": "-", "sql_reason": "(--no-llm)",
                    "data_verdict": "-", "data_reason": ""}
                   if args.no_llm else judge(item, run))
        cmp["diagnosis"] = diagnose(run, cmp)
        passed = bool(cmp["data_match"])
        rows_out.append({"id": run["id"], "item": item, "run": run, "cmp": cmp,
                         "verdict": verdict, "passed": passed})
        flag = "  <== STALE" if run["id"] in stale else ""
        print(f"[{'PASS' if passed else 'FAIL'}] {run['id']:12s} "
              f"{cmp['diagnosis']:24s} sql={verdict['sql_verdict']:10s} "
              f"data={'MATCH' if cmp['data_match'] else 'MISMATCH'}{flag}")
        if args.pause and not args.no_llm:
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

    if not args.no_llm:
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

    L += ["\n## Per-question results\n",
          "| id | Question | Pass | Diagnosis | SQL verdict (LLM) | Data | "
          "Tables | Cols | Rows g/a |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows_out:
        it, run, c, v = r["item"], r["run"], r["cmp"], r["verdict"]
        gr = len(it.get("gold_result") or [])
        ar = "-" if run.get("agent_result") is None else len(run["agent_result"])
        L.append(
            f"| {r['id']} | {it['question'][:58]} | {'✅' if r['passed'] else '❌'} | "
            f"{c['diagnosis']} | {v['sql_verdict']} | "
            f"{'MATCH' if c['data_match'] else 'MISMATCH'} | "
            f"{c['table_recall']} | {c['column_recall']} | {gr}/{ar} |")

    L.append("\n## Per-question detail\n")
    for r in rows_out:
        it, run, c, v = r["item"], r["run"], r["cmp"], r["verdict"]
        L.append(f"### {r['id']} — {'✅ PASS' if r['passed'] else '❌ FAIL'} "
                 f"({c['diagnosis']})\n")
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
        if not args.no_llm:
            L.append(f"- **LLM SQL verdict:** `{v['sql_verdict']}` — {v['sql_reason']}")
            L.append(f"- **LLM data verdict:** `{v['data_verdict']}` — {v['data_reason']}")
        L.append(f"- **Agent answer:** {(run.get('agent_answer') or '-')[:400]}\n")

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
         "rows": [{"id": r["id"], "passed": r["passed"], **r["cmp"], **r["verdict"]}
                  for r in rows_out]},
        indent=2, default=str), encoding="utf-8")

    print(f"\nReport -> {REPORT_PATH}")
    print(f"JSON   -> {REPORT_JSON}")
    print(f"PASSED {passed}/{n}  ·  " + ", ".join(f"{k}={v}" for k, v in
                                                  sorted(modes.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
