"""Eval runner — score the WHOLE agent against a gold dataset.

Sends each dataset question (and every language/paraphrase variant) through the SAME
LangGraph ReAct agent the service runs (build_sql_agent_graph -> intent -> tool/tier
selection -> validator -> live DB -> answer), then SCORES the outcome (routing / gold
execution-match / refusal / leakage) — it does not assert pass/fail. Produces per-slice
scores (by tier, domain, language) that a binary test cannot give you.

This exercises the entire agent core in-process (the default, no server needed) — the
same path scripts/agent_scenario_tests.py uses. Use `--transport http --url ...` to drive
the running FastAPI service instead (true end-to-end, incl. the /ask envelope + memory).

Prereqs: DB reachable (already used to materialize gold) + an LLM provider key in .env.
Gold answers come from eval/datasets/<name>.expected.json — run eval/materialize_gold.py
first if it is missing or the snapshot changed.

Run:
    .venv/Scripts/python.exe eval/run_eval.py --smoke              # a few cases per family
    .venv/Scripts/python.exe eval/run_eval.py --only D01,D02,X01
    .venv/Scripts/python.exe eval/run_eval.py --full --pause 6     # everything, rate-limit-safe
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
from sql_agent.routing.tier_router import tier_of  # noqa: E402
from sql_agent.service.api import _parse_tool_content  # noqa: E402
from sql_agent.tools.registry import set_caller_scopes  # noqa: E402

from eval.scorers import (  # noqa: E402
    score_gold_exec, score_leakage, score_refusal, score_routing,
)

REPORT_PATH = HERE / "EVAL_RESULTS.md"
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


def _expand(items: list[dict]):
    """One eval row per surface form; variants inherit the parent's gold + labels."""
    for it in items:
        yield {**it, "text": it["question"], "lang": "en", "variant_of": it["id"]}
        for v in it.get("variants", []):
            yield {**it, "text": v["text"], "lang": v.get("lang", "en"),
                   "id": f'{it["id"]}::{v.get("lang","en")}:{hash(v["text"]) & 0xfff:x}',
                   "variant_of": it["id"]}


def run(dataset: str, ids: list[str] | None, smoke: bool, pause: float):
    src = yaml.safe_load((HERE / "datasets" / f"{dataset}.yaml").read_text(encoding="utf-8"))
    expected = json.loads((HERE / "datasets" / f"{dataset}.expected.json").read_text(
        encoding="utf-8"))["expected"]
    whitelist = _governed_whitelist()

    items = src["items"]
    if ids:
        items = [it for it in items if it["id"] in ids]
    elif smoke:
        items = [it for it in items if it["id"] in SMOKE_IDS]

    graph = build_sql_agent_graph()
    rows_out = []
    for row in _expand(items):
        base_id = row["variant_of"]
        scopes = set(row.get("scopes") or [])
        set_caller_scopes(scopes)
        state = {
            "messages": [HumanMessage(content=row["text"])],
            "caller_agent": "eval_runner", "auth_scopes": scopes,
            "tool_call_count": 0, "dynamic_call_count": 0, "correlation_id": row["id"],
        }
        started = time.time()
        try:
            out = graph.invoke(state, config={"recursion_limit": 25})
            run_ = _extract(out.get("messages", []), out.get("intent", {}))
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limited(exc):
                print(f"[SKIP] {row['id']} — provider rate limit; stopping")
                break
            run_ = AgentRun(final_status="error", answer=f"{type(exc).__name__}: {exc}")
        latency = round((time.time() - started) * 1000)

        gold = (expected.get(base_id) or {}).get("gold_result")
        scores = [
            score_routing(row, run_),
            score_gold_exec(row, run_, gold),
            score_refusal(row, run_),
            score_leakage(row, run_, whitelist),
        ]
        primary = row.get("primary_metric", "routing")
        pscore = next((s for s in scores if s.name == primary), None)
        passed = bool(pscore and pscore.value == 1.0)
        rows_out.append({"row": row, "run": run_, "scores": scores,
                         "primary": primary, "passed": passed, "latency": latency})
        marks = " ".join(f"{s.name}={'✓' if s.value==1.0 else ('·' if s.value is None else '✗')}"
                         for s in scores)
        print(f"[{'PASS' if passed else 'FAIL'}] {row['id']:14s} {row['lang']:2s} "
              f"tools={run_.tools_called or '-'} | primary={primary} | {marks}")
        if pause:
            time.sleep(pause)
    return rows_out


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def report(rows_out, dataset: str):
    scored = [r for r in rows_out]
    passed = sum(1 for r in scored if r["passed"])
    lines = [f"# Eval Results — {dataset}\n",
             f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · "
             f"{len(scored)} rows · {passed}/{len(scored)} passed on primary metric._\n"]

    # per-scorer overall
    lines.append("## Scores by check\n")
    lines.append("| Check | Mean | Applicable |")
    lines.append("|---|---|---|")
    for name in ("routing", "gold_exec", "refusal", "leakage"):
        vals = [s.value for r in scored for s in r["scores"] if s.name == name and s.applicable]
        lines.append(f"| {name} | {_mean(vals) if vals else '—'} | {len(vals)} |")

    # primary-metric pass rate sliced
    def slice_by(key):
        buckets: dict = {}
        for r in scored:
            buckets.setdefault(r["row"].get(key, "?"), []).append(1.0 if r["passed"] else 0.0)
        return {k: _mean(v) for k, v in sorted(buckets.items())}

    for key, title in (("lang", "language"), ("expect_tier", "tier"), ("domain", "domain")):
        lines.append(f"\n## Primary-metric pass rate by {title}\n")
        lines.append("| " + title + " | pass rate | n |")
        lines.append("|---|---|---|")
        buckets: dict = {}
        for r in scored:
            buckets.setdefault(r["row"].get(key, "?"), []).append(r["passed"])
        for k, v in sorted(buckets.items()):
            lines.append(f"| {k} | {_mean([1.0 if x else 0.0 for x in v])} | {len(v)} |")

    # detail
    lines.append("\n## Per-row detail\n")
    lines.append("| id | lang | primary | pass | tools | scores |")
    lines.append("|---|---|---|---|---|---|")
    for r in scored:
        sc = " ".join(f"{s.name}:{'—' if s.value is None else s.value}" for s in r["scores"])
        lines.append(f"| {r['row']['id']} | {r['row']['lang']} | {r['primary']} | "
                     f"{'✅' if r['passed'] else '❌'} | {','.join(r['run'].tools_called) or '-'} | {sc} |")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT_PATH}")
    print(f"PASSED {passed}/{len(scored)} on primary metric")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Score the agent against a gold eval set.")
    ap.add_argument("--dataset", default="gold_v1")
    ap.add_argument("--only", help="comma-separated ids, e.g. D01,D02,X01")
    ap.add_argument("--smoke", action="store_true", help="a few cases per family")
    ap.add_argument("--full", action="store_true", help="all items")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between calls")
    args = ap.parse_args()
    ids = [x.strip() for x in args.only.split(",")] if args.only else None
    smoke = args.smoke or not (args.full or ids)
    print(f"Running eval '{args.dataset}' ({'only '+args.only if ids else ('smoke' if smoke else 'full')})...")
    report(run(args.dataset, ids, smoke, args.pause), args.dataset)
