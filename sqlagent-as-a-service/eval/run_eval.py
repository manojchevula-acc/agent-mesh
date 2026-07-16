"""Eval runner — score the WHOLE agent against a gold dataset.

Sends each dataset question (and every language/paraphrase variant) through the SAME
LangGraph ReAct agent the service runs (build_sql_agent_graph -> intent -> tool/tier
selection -> validator -> live DB -> answer), then SCORES the outcome (routing / SQL
execution / gold execution-match / table+column selection / refusal / leakage / answer)
— it does not assert pass/fail. Produces per-slice scores (by tier, domain, language) that
a binary test cannot give you.

This exercises the entire agent core in-process (the default, no server needed) — the
same path scripts/agent_scenario_tests.py uses.

The SQL the agent produced is RE-EXECUTED here, independently of the agent's own run, so
"did the SQL execute" is measured rather than inferred from the tool envelope — and so a
tool that reports success while returning nothing is still caught.

Prereqs: DB reachable (already used to materialize gold) + an LLM provider key in .env.
Gold answers come from eval/datasets/<name>.expected.json — run eval/materialize_gold.py
first if it is missing or the snapshot changed.

Run (selection is by dataset ID like D07, or by 1-based position — both accepted):
    .venv/Scripts/python.exe eval/run_eval.py --id 10                  # one example
    .venv/Scripts/python.exe eval/run_eval.py --id D07
    .venv/Scripts/python.exe eval/run_eval.py --ids 1,5,10,15
    .venv/Scripts/python.exe eval/run_eval.py --ids D01,D02,X01
    .venv/Scripts/python.exe eval/run_eval.py --range 1-20             # inclusive
    .venv/Scripts/python.exe eval/run_eval.py --range D01-D11
    .venv/Scripts/python.exe eval/run_eval.py --all --pause 6          # everything
    .venv/Scripts/python.exe eval/run_eval.py --smoke                  # a few per family
    .venv/Scripts/python.exe eval/run_eval.py --list                   # show ids+positions

Rate limits (Groq free tier): --pause N sleeps between examples, --retries N retries a
rate-limited example with exponential backoff, and --resume skips examples already present
in the previous run's EVAL_RESULTS.json so an interrupted run continues where it stopped
instead of re-burning tokens on completed work.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import yaml  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from sql_agent.agent.graph import build_sql_agent_graph  # noqa: E402
from sql_agent.db import db  # noqa: E402
from sql_agent.routing.tier_router import tier_of  # noqa: E402
from sql_agent.service.api import _parse_tool_content  # noqa: E402
from sql_agent.tools.registry import set_caller_scopes  # noqa: E402

from eval.scorers import (  # noqa: E402
    diagnose, score_answer, score_column_selection, score_gold_exec, score_leakage,
    score_refusal, score_routing, score_sql_exec, score_table_selection,
)
from eval.sql_introspect import extract  # noqa: E402

REPORT_PATH = HERE / "EVAL_RESULTS.md"
RESULTS_JSON = HERE / "EVAL_RESULTS.json"
SMOKE_IDS = ["P01", "V01", "S01", "D02", "D04", "D07", "X01", "X06"]


@dataclass
class AgentRun:
    tools_called: list[str] = field(default_factory=list)
    tiers_called: list[str] = field(default_factory=list)
    primary_data: list[dict] | None = None   # rows from the primary successful tool
    primary_sql: str = ""                     # SQL that ran (any tier)
    generated_sql: str = ""                   # full_dynamic generated SQL, if any
    answer: str = ""
    final_status: str = "no-tool"             # success | error | no-tool | rate-limited
    intent: dict = field(default_factory=dict)
    # --- filled in by _reexecute: the agent's SQL run again, independently ---
    agent_sql: str = ""                       # the SQL we re-execute and introspect
    agent_rows: list[dict] | None = None      # rows from OUR execution of agent_sql
    exec_error: str = ""                      # why agent_sql failed to execute, if it did
    result_rows: list[dict] | None = None     # rows compared against gold


def _governed_whitelist() -> set[str]:
    data = yaml.safe_load((HERE.parent / "sql_agent/semantic_layer/schema.yaml").read_text(
        encoding="utf-8"))
    return set(data.get("tables", {}).keys())


def _is_rate_limited(exc: Exception) -> bool:
    s = str(exc).lower()
    return (type(exc).__name__ == "RateLimitError"
            or "rate_limit" in s or "rate limit" in s or "429" in s
            or "tokens per day" in s)


def _extract(messages, intent: dict) -> AgentRun:
    run = AgentRun(intent=intent or {})
    for m in messages:
        if isinstance(m, AIMessage):
            for c in getattr(m, "tool_calls", None) or []:
                run.tools_called.append(c["name"])
    run.tiers_called = [tier_of(t) for t in run.tools_called]

    for m in reversed(messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            run.answer = m.content if isinstance(m.content, str) else str(m.content)
            break

    any_success = False
    for m in messages:
        if isinstance(m, ToolMessage):
            parsed = _parse_tool_content(m.content)
            if not isinstance(parsed, dict):
                continue
            if parsed.get("sql"):
                run.primary_sql = parsed["sql"]
            if parsed.get("query_tier") == "full_dynamic" and parsed.get("sql"):
                run.generated_sql = parsed["sql"]
            if parsed.get("status") == "success":
                any_success = True
                # the primary result = the successful tool's rows ("data" in the envelope)
                if parsed.get("data") is not None:
                    run.primary_data = parsed["data"]
    run.final_status = ("success" if any_success
                        else ("no-tool" if not run.tools_called else "error"))
    return run


def _reexecute(run: AgentRun) -> None:
    """Run the agent's own SQL against the DB ourselves, and pick the rows to grade.

    The agent already executed this SQL inside its tool call; doing it again here is
    deliberate. It gives an execution signal that does not depend on the tool envelope's
    self-reported status, and it is what makes `sql_exec` a real measurement. The envelope
    renders bound params inline (formatting/response_formatter._render_sql), so the SQL is
    re-executable as-is.

    Rows compared against gold are OUR execution's rows when we have them, falling back to
    the agent's own — a calculation tool answers from a formula and produces no SQL at all.
    """
    run.agent_sql = run.generated_sql or run.primary_sql
    if not run.agent_sql:
        run.result_rows = run.primary_data
        return
    try:
        run.agent_rows = db.execute(run.agent_sql).rows
        run.result_rows = run.agent_rows
    except Exception as exc:  # noqa: BLE001 — a rejected query is a scoreable outcome
        run.exec_error = f"{type(exc).__name__}: {exc}"
        run.result_rows = run.primary_data


def _expand(items: list[dict]):
    """One eval row per surface form; variants inherit the parent's gold + labels."""
    for it in items:
        yield {**it, "text": it["question"], "lang": "en", "variant_of": it["id"]}
        for v in it.get("variants", []):
            yield {**it, "text": v["text"], "lang": v.get("lang", "en"),
                   "id": f'{it["id"]}::{v.get("lang","en")}:{hash(v["text"]) & 0xfff:x}',
                   "variant_of": it["id"]}


# --------------------------------------------------------------------------- #
# Selection — --id / --ids / --range / --all
# --------------------------------------------------------------------------- #
def _resolve_token(token: str, items: list[dict]) -> str:
    """One selection token -> one dataset id. Accepts a dataset id (D07, case-insensitive)
    or a 1-based position (10). Ids win over positions, so a dataset that ever uses bare
    numeric ids keeps working."""
    token = token.strip()
    for it in items:
        if it["id"].lower() == token.lower():
            return it["id"]
    if token.isdigit():
        pos = int(token)
        if not 1 <= pos <= len(items):
            raise SystemExit(f"--id/--ids: position {pos} out of range (1..{len(items)})")
        return items[pos - 1]["id"]
    raise SystemExit(f"--id/--ids: '{token}' is neither a known id nor a position. "
                     f"Use --list to see them.")


def _resolve_range(spec: str, items: list[dict]) -> list[str]:
    """'1-20' or 'D01-D11' -> every id in that inclusive window, in dataset order."""
    if "-" not in spec:
        raise SystemExit(f"--range expects START-END (e.g. 1-20 or D01-D11), got '{spec}'")
    start_tok, end_tok = spec.split("-", 1)
    start_id = _resolve_token(start_tok, items)
    end_id = _resolve_token(end_tok, items)
    order = [it["id"] for it in items]
    i, j = order.index(start_id), order.index(end_id)
    if i > j:
        i, j = j, i
    return order[i:j + 1]


def _select(items: list[dict], args) -> list[dict]:
    ids: list[str] | None = None
    if args.id:
        ids = [_resolve_token(args.id, items)]
    elif args.ids:
        ids = [_resolve_token(t, items) for t in args.ids.split(",") if t.strip()]
    elif args.only:                                    # back-compat with the old flag
        ids = [_resolve_token(t, items) for t in args.only.split(",") if t.strip()]
    elif args.range:
        ids = _resolve_range(args.range, items)
    elif args.smoke:
        ids = [i for i in SMOKE_IDS if any(it["id"] == i for it in items)]
    elif args.all or args.full:
        return items
    else:
        ids = [i for i in SMOKE_IDS if any(it["id"] == i for it in items)]  # default: smoke
    wanted = set(ids)
    return [it for it in items if it["id"] in wanted]


def _completed_ids() -> set[str]:
    if not RESULTS_JSON.exists():
        return set()
    try:
        prev = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt cache must not block a run
        return set()
    return {r["id"] for r in prev.get("rows", [])}


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def _invoke_with_retry(graph, state, row, retries: int, backoff: float) -> AgentRun:
    """Invoke the agent, retrying ONLY on provider rate limits.

    A rate limit is a property of the clock, not of the agent — retrying it measures the
    same thing again. Any other exception is a genuine outcome and is recorded as an error
    run rather than retried, so a real agent bug cannot be papered over by a retry.
    """
    for attempt in range(1, retries + 2):
        try:
            out = graph.invoke(state, config={"recursion_limit": 25})
            return _extract(out.get("messages", []), out.get("intent", {}))
        except Exception as exc:  # noqa: BLE001
            if not _is_rate_limited(exc):
                return AgentRun(final_status="error", answer=f"{type(exc).__name__}: {exc}")
            if attempt > retries:
                print(f"       rate-limited, retries exhausted ({retries})")
                return AgentRun(final_status="rate-limited",
                                answer=f"rate limited after {retries} retries: {exc}")
            wait = backoff * (2 ** (attempt - 1))
            print(f"       rate-limited, retry {attempt}/{retries} in {wait:.0f}s")
            time.sleep(wait)
    return AgentRun(final_status="rate-limited")


def run(dataset: str, items: list[dict], pause: float, retries: int, backoff: float,
        judge: bool, resume: bool):
    expected = json.loads((HERE / "datasets" / f"{dataset}.expected.json").read_text(
        encoding="utf-8"))["expected"]
    whitelist = _governed_whitelist()
    done = _completed_ids() if resume else set()

    graph = build_sql_agent_graph()
    rows_out = []
    for row in _expand(items):
        if row["id"] in done:
            print(f"[SKIP] {row['id']:14s} already in {RESULTS_JSON.name} (--resume)")
            continue
        base_id = row["variant_of"]
        scopes = set(row.get("scopes") or [])
        set_caller_scopes(scopes)
        state = {
            "messages": [HumanMessage(content=row["text"])],
            "caller_agent": "eval_runner", "auth_scopes": scopes,
            "tool_call_count": 0, "dynamic_call_count": 0, "correlation_id": row["id"],
        }
        started = time.time()
        run_ = _invoke_with_retry(graph, state, row, retries, backoff)
        _reexecute(run_)
        latency = round((time.time() - started) * 1000)

        gold = (expected.get(base_id) or {}).get("gold_result")
        scores = [
            score_routing(row, run_),
            score_sql_exec(row, run_),
            score_gold_exec(row, run_, gold),
            score_table_selection(row, run_),
            score_column_selection(row, run_),
            score_refusal(row, run_),
            score_leakage(row, run_, whitelist),
        ]
        if judge:
            scores.append(score_answer(row, run_, gold))
        primary = row.get("primary_metric", "routing")
        pscore = next((s for s in scores if s.name == primary), None)
        passed = bool(pscore and pscore.value == 1.0)
        why = diagnose(row, run_, scores, passed)
        rows_out.append({"row": row, "run": run_, "scores": scores, "gold": gold,
                         "primary": primary, "passed": passed, "latency": latency,
                         "diagnosis": why})
        marks = " ".join(f"{s.name}={'✓' if s.value==1.0 else ('·' if s.value is None else '✗')}"
                         for s in scores)
        print(f"[{'PASS' if passed else 'FAIL'}] {row['id']:14s} {row['lang']:2s} "
              f"{why:24s} | primary={primary} | {marks}")
        if pause:
            time.sleep(pause)
    return rows_out


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _fmt_rows(rows, limit: int = 6) -> str:
    """Render a result set for the report — capped, because a 50-row dump helps nobody."""
    if rows is None:
        return "_(none)_"
    if not rows:
        return "_(empty result set)_"
    shown = rows[:limit]
    body = "<br>".join(
        "`" + ", ".join(f"{k}={v}" for k, v in r.items())[:160] + "`" for r in shown)
    if len(rows) > limit:
        body += f"<br>_… {len(rows) - limit} more rows_"
    return body


def report(rows_out, dataset: str, resume: bool):
    scored = list(rows_out)
    if not scored:
        print("\nNothing ran — every selected example was already complete (--resume) "
              "or the selection was empty.")
        return
    passed = sum(1 for r in scored if r["passed"])
    lines = [f"# Eval Results — {dataset}\n",
             f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · "
             f"{len(scored)} rows · {passed}/{len(scored)} passed on primary metric._\n"]

    # ---- summary metrics -------------------------------------------------
    lines.append("## Summary\n")
    lines.append("| Metric | Value | Applicable rows |")
    lines.append("|---|---|---|")
    lines.append(f"| Total examples evaluated | {len(scored)} | — |")
    for name, label in (
        ("sql_exec", "SQL execution success rate"),
        ("gold_exec", "Result-equivalence accuracy"),
        ("table_selection", "Table-selection accuracy"),
        ("column_selection", "Column-selection accuracy"),
        ("routing", "Tool-routing accuracy"),
        ("refusal", "Refusal accuracy"),
        ("leakage", "Governance (no ungoverned table)"),
        ("answer", "Final-answer accuracy"),
    ):
        vals = [s.value for r in scored for s in r["scores"]
                if s.name == name and s.applicable]
        mean = _mean(vals)
        lines.append(f"| {label} | {'—' if mean is None else mean} | {len(vals)} |")
    lines.append(f"| **Overall pass rate** | **{round(passed / len(scored), 3)}** "
                 f"| {len(scored)} |")

    # ---- failure-mode breakdown -----------------------------------------
    lines.append("\n## Outcome breakdown\n")
    lines.append("| Diagnosis | n |")
    lines.append("|---|---|")
    modes: dict[str, int] = {}
    for r in scored:
        modes[r["diagnosis"]] = modes.get(r["diagnosis"], 0) + 1
    for k, v in sorted(modes.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")

    # ---- primary-metric pass rate sliced --------------------------------
    for key, title in (("lang", "language"), ("expect_tier", "tier"), ("domain", "domain"),
                       ("difficulty", "difficulty")):
        lines.append(f"\n## Primary-metric pass rate by {title}\n")
        lines.append(f"| {title} | pass rate | n |")
        lines.append("|---|---|---|")
        buckets: dict = {}
        for r in scored:
            buckets.setdefault(r["row"].get(key, "?"), []).append(r["passed"])
        for k, v in sorted(buckets.items()):
            lines.append(f"| {k} | {_mean([1.0 if x else 0.0 for x in v])} | {len(v)} |")

    # ---- per-row detail table -------------------------------------------
    lines.append("\n## Per-row summary\n")
    lines.append("| id | lang | primary | pass | diagnosis | tools | scores |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in scored:
        sc = " ".join(f"{s.name}:{'—' if s.value is None else s.value}" for s in r["scores"])
        lines.append(f"| {r['row']['id']} | {r['row']['lang']} | {r['primary']} | "
                     f"{'✅' if r['passed'] else '❌'} | {r['diagnosis']} | "
                     f"{','.join(r['run'].tools_called) or '-'} | {sc} |")

    # ---- full per-example detail ----------------------------------------
    lines.append("\n## Per-example detail\n")
    for r in scored:
        row, run_ = r["row"], r["run"]
        gold_t = sorted(row.get("gold_tables") or [])
        gold_c = sorted(row.get("gold_columns") or [])
        agent_t, agent_c = (extract(run_.agent_sql) if run_.agent_sql else (set(), set()))
        lines.append(f"### {row['id']} — {'✅ PASS' if r['passed'] else '❌ FAIL'} "
                     f"({r['diagnosis']})\n")
        lines.append(f"- **Question** ({row['lang']}): {row['text']}")
        lines.append(f"- **Primary metric**: `{r['primary']}` · latency {r['latency']}ms")
        lines.append(f"- **Tools called**: `{', '.join(run_.tools_called) or '-'}`")
        lines.append(f"- **Gold SQL**:\n  ```sql\n  {(row.get('gold_sql') or '-').strip()}\n  ```")
        lines.append(f"- **Agent SQL**:\n  ```sql\n  {(run_.agent_sql or '-').strip()}\n  ```")
        lines.append(f"- **Gold tables**: `{gold_t or '-'}` · **Agent tables**: "
                     f"`{sorted(agent_t) or '-'}`")
        lines.append(f"- **Gold columns**: `{gold_c or '-'}`")
        lines.append(f"- **Agent columns**: `{sorted(agent_c) or '-'}`")
        lines.append(f"- **SQL execution**: "
                     f"{'FAILED — ' + run_.exec_error if run_.exec_error else ('ok' if run_.agent_sql else 'no SQL produced')}")
        lines.append(f"- **Gold result**:<br>{_fmt_rows(r['gold'])}")
        lines.append(f"- **Agent result**:<br>{_fmt_rows(run_.result_rows)}")
        lines.append(f"- **Agent answer**: {(run_.answer or '-')[:400]}")
        for s in r["scores"]:
            lines.append(f"  - `{s.name}` = {'—' if s.value is None else s.value} — {s.detail}")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    # ---- machine-readable sidecar (also powers --resume) -----------------
    prior = []
    if resume and RESULTS_JSON.exists():
        try:
            prior = json.loads(RESULTS_JSON.read_text(encoding="utf-8")).get("rows", [])
        except Exception:  # noqa: BLE001
            prior = []
    fresh_ids = {r["row"]["id"] for r in scored}
    payload_rows = [p for p in prior if p["id"] not in fresh_ids] + [
        {
            "id": r["row"]["id"], "question": r["row"]["text"], "lang": r["row"]["lang"],
            "primary_metric": r["primary"], "passed": r["passed"],
            "diagnosis": r["diagnosis"], "latency_ms": r["latency"],
            "gold_sql": r["row"].get("gold_sql"), "agent_sql": r["run"].agent_sql,
            "gold_tables": r["row"].get("gold_tables"),
            "agent_tables": sorted(extract(r["run"].agent_sql)[0]) if r["run"].agent_sql else [],
            "gold_columns": r["row"].get("gold_columns"),
            "agent_columns": sorted(extract(r["run"].agent_sql)[1]) if r["run"].agent_sql else [],
            "exec_error": r["run"].exec_error,
            "gold_result": r["gold"], "agent_result": r["run"].result_rows,
            "answer": r["run"].answer,
            "tools_called": r["run"].tools_called,
            "scores": {s.name: s.value for s in r["scores"]},
        }
        for r in scored
    ]
    RESULTS_JSON.write_text(json.dumps(
        {"dataset": dataset, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "rows": payload_rows}, indent=2, default=str), encoding="utf-8")

    print(f"\nReport -> {REPORT_PATH}")
    print(f"JSON   -> {RESULTS_JSON}")
    print(f"PASSED {passed}/{len(scored)} on primary metric")
    print("Outcomes: " + ", ".join(f"{k}={v}" for k, v in
                                   sorted(modes.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Score the agent against a gold eval set.",
        epilog="Selection accepts dataset ids (D07) or 1-based positions (10). "
               "See --list.")
    ap.add_argument("--dataset", default="gold_v1")
    sel = ap.add_argument_group("selection (rate-limit friendly)")
    sel.add_argument("--id", help="run ONE example, by id (D07) or position (10)")
    sel.add_argument("--ids", help="run several, comma-separated: 1,5,10 or D01,D02,X01")
    sel.add_argument("--range", help="run an inclusive range: 1-20 or D01-D11")
    sel.add_argument("--all", action="store_true", help="run every example")
    sel.add_argument("--smoke", action="store_true", help="a few cases per family (default)")
    sel.add_argument("--list", action="store_true", help="list ids with positions, then exit")
    sel.add_argument("--only", help=argparse.SUPPRESS)   # legacy alias for --ids
    sel.add_argument("--full", action="store_true", help=argparse.SUPPRESS)  # legacy --all
    rl = ap.add_argument_group("rate limits")
    rl.add_argument("--pause", type=float, default=0.0, help="seconds between examples")
    rl.add_argument("--retries", type=int, default=2,
                    help="retries per example on a provider rate limit (default 2)")
    rl.add_argument("--backoff", type=float, default=20.0,
                    help="initial retry backoff seconds, doubled per attempt (default 20)")
    rl.add_argument("--resume", action="store_true",
                    help="skip examples already in EVAL_RESULTS.json")
    ap.add_argument("--judge", action="store_true",
                    help="also LLM-grade the final natural-language answer (extra tokens)")
    args = ap.parse_args()

    src = yaml.safe_load((HERE / "datasets" / f"{args.dataset}.yaml").read_text(
        encoding="utf-8"))
    all_items = src["items"]

    if args.list:
        print(f"{args.dataset}: {len(all_items)} examples\n")
        for n, it in enumerate(all_items, 1):
            print(f"  {n:3d}  {it['id']:5s}  {it.get('primary_metric','?'):10s} "
                  f"{it['question'][:78]}")
        raise SystemExit(0)

    selected = _select(all_items, args)
    if not selected:
        raise SystemExit("Selection matched no examples. Use --list to see the ids.")
    print(f"Running eval '{args.dataset}' — {len(selected)} example(s): "
          f"{', '.join(it['id'] for it in selected)}")
    report(run(args.dataset, selected, args.pause, args.retries, args.backoff,
               args.judge, args.resume), args.dataset, args.resume)
