"""Step 2 of the eval — run the gold questions through the LIVE agent and record what it did.

This script SCORES NOTHING. It only produces the "what the agent actually did" half of the
comparison, so that judging (eval/compare_llm.py) is a separate, cheap, re-runnable step
that never needs to re-invoke the agent. That split matters on a rate-limited free tier:
one expensive pass over the agent, then as many judging passes as you like.

For each question it captures, into eval/results/agent_runs/agent_runs.yaml:
    question        — exactly what was asked (the gold item's question)
    agent_sql       — the SQL the agent generated
    agent_result    — the rows that SQL returns, re-executed HERE against the DB
    agent_answer    — the agent's final natural-language answer
    tools_called    — which tools it chose
    agent_tables    — tables/columns parsed out of agent_sql (sqlglot)
    agent_columns
    status          — success | no-sql | sql-error | no-tool | rate-limited | agent-error

The SQL is re-executed here rather than trusting the tool envelope's self-reported status,
so "the SQL runs" is measured, not claimed.

Questions go through the same path the service runs: build_sql_agent_graph -> intent ->
tool/tier selection -> validator -> live DB -> answer. The dataset is dynamic-tier, so the
dynamic_sql scope is granted; without it the agent would correctly refuse everything.

Run (selection accepts dataset ids like D07, or 1-based positions like 7):
    .venv/Scripts/python.exe eval/run_agent.py --id 10
    .venv/Scripts/python.exe eval/run_agent.py --id D07
    .venv/Scripts/python.exe eval/run_agent.py --ids 1,5,10,15
    .venv/Scripts/python.exe eval/run_agent.py --ids D01,D02,D11
    .venv/Scripts/python.exe eval/run_agent.py --range 1-20
    .venv/Scripts/python.exe eval/run_agent.py --range D01-D11
    .venv/Scripts/python.exe eval/run_agent.py --all --pause 6 --resume
    .venv/Scripts/python.exe eval/run_agent.py --list

Rate limits (Groq free tier): --pause sleeps between questions, --retries retries a
rate-limited question with exponential backoff, and --resume skips questions already in
the output file so an interrupted run continues instead of re-burning tokens.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import yaml  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from sql_agent.agent.graph import build_sql_agent_graph  # noqa: E402
from sql_agent.db import db  # noqa: E402
from sql_agent.service.api import _parse_tool_content  # noqa: E402
from sql_agent.tools.registry import set_caller_scopes  # noqa: E402

from eval.sql_introspect import extract  # noqa: E402

OUT_DIR = HERE / "results" / "agent_runs"
OUT_PATH = OUT_DIR / "agent_runs.yaml"


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
def _json_safe(obj):
    """DB values -> plain YAML scalars (Decimal/date would otherwise become !!python tags)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, decimal.Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def _is_rate_limited(exc: Exception) -> bool:
    s = str(exc).lower()
    return (type(exc).__name__ == "RateLimitError"
            or "rate_limit" in s or "rate limit" in s or "429" in s
            or "tokens per day" in s)


def _harvest(messages) -> dict:
    """Pull the SQL, the tool choices and the final answer out of the agent's messages."""
    tools_called, sql, answer = [], "", ""
    for m in messages:
        if isinstance(m, AIMessage):
            for c in getattr(m, "tool_calls", None) or []:
                tools_called.append(c["name"])
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            answer = m.content if isinstance(m.content, str) else str(m.content)
            break
    for m in messages:
        if isinstance(m, ToolMessage):
            parsed = _parse_tool_content(m.content)
            if isinstance(parsed, dict) and parsed.get("sql"):
                sql = parsed["sql"]          # last SQL wins — the one that answered
    return {"tools_called": tools_called, "agent_sql": sql, "agent_answer": answer}


def ask_agent(graph, question: str, cid: str, scopes: set[str],
              retries: int, backoff: float) -> dict:
    """One question -> one record. Retries ONLY rate limits: those are a property of the
    clock, not the agent. A real agent error is an outcome worth recording, not retrying."""
    set_caller_scopes(scopes)
    state = {
        "messages": [HumanMessage(content=question)],
        "caller_agent": "eval_runner", "auth_scopes": scopes,
        "tool_call_count": 0, "dynamic_call_count": 0, "correlation_id": cid,
    }
    started = time.time()
    rec: dict = {"tools_called": [], "agent_sql": "", "agent_answer": ""}
    for attempt in range(1, retries + 2):
        try:
            out = graph.invoke(state, config={"recursion_limit": 25})
            rec = _harvest(out.get("messages", []))
            break
        except Exception as exc:  # noqa: BLE001
            if not _is_rate_limited(exc):
                rec = {"tools_called": [], "agent_sql": "", "agent_answer": "",
                       "status": "agent-error", "error": f"{type(exc).__name__}: {exc}"}
                break
            if attempt > retries:
                rec = {"tools_called": [], "agent_sql": "", "agent_answer": "",
                       "status": "rate-limited", "error": str(exc)[:300]}
                print(f"       rate-limited, retries exhausted ({retries})")
                break
            wait = backoff * (2 ** (attempt - 1))
            print(f"       rate-limited, retry {attempt}/{retries} in {wait:.0f}s")
            time.sleep(wait)

    rec["latency_ms"] = round((time.time() - started) * 1000)

    # Re-execute the agent's SQL ourselves — this is what makes "the SQL runs" a
    # measurement rather than a claim from the tool envelope.
    rec.setdefault("status", "")
    rec["agent_result"] = None
    rec["exec_error"] = ""
    if rec["agent_sql"]:
        tables, columns = extract(rec["agent_sql"])
        rec["agent_tables"] = sorted(tables)
        rec["agent_columns"] = sorted(columns)
        try:
            rec["agent_result"] = _json_safe(db.execute(rec["agent_sql"]).rows)
            rec["status"] = rec["status"] or "success"
        except Exception as exc:  # noqa: BLE001 — a rejected query is a real outcome
            rec["exec_error"] = f"{type(exc).__name__}: {exc}"
            rec["status"] = rec["status"] or "sql-error"
    else:
        rec["agent_tables"], rec["agent_columns"] = [], []
        if not rec["status"]:
            rec["status"] = "no-tool" if not rec["tools_called"] else "no-sql"
    rec["row_count"] = len(rec["agent_result"]) if rec["agent_result"] is not None else None
    return rec


# --------------------------------------------------------------------------- #
# Selection — --id / --ids / --range / --all
# --------------------------------------------------------------------------- #
def _resolve_token(token: str, items: list[dict]) -> str:
    """One token -> one dataset id. Accepts an id (D07, case-insensitive) or a 1-based
    position (7). Ids win over positions, so numeric ids would still work."""
    token = token.strip()
    for it in items:
        if str(it["id"]).lower() == token.lower():
            return it["id"]
    if token.isdigit():
        pos = int(token)
        if not 1 <= pos <= len(items):
            raise SystemExit(f"position {pos} out of range (1..{len(items)})")
        return items[pos - 1]["id"]
    raise SystemExit(f"'{token}' is neither a known id nor a position — try --list")


def _resolve_range(spec: str, items: list[dict]) -> list[str]:
    if "-" not in spec:
        raise SystemExit(f"--range expects START-END (e.g. 1-20 or D01-D11), got '{spec}'")
    a, b = spec.split("-", 1)
    order = [it["id"] for it in items]
    i, j = order.index(_resolve_token(a, items)), order.index(_resolve_token(b, items))
    if i > j:
        i, j = j, i
    return order[i:j + 1]


def _select(items: list[dict], args) -> list[dict]:
    if args.id:
        ids = [_resolve_token(args.id, items)]
    elif args.ids:
        ids = [_resolve_token(t, items) for t in args.ids.split(",") if t.strip()]
    elif args.range:
        ids = _resolve_range(args.range, items)
    elif args.all:
        return items
    else:
        raise SystemExit("Pick a selection: --id / --ids / --range / --all (see --list). "
                         "Nothing runs by default — every question costs API tokens.")
    wanted = set(ids)
    return [it for it in items if it["id"] in wanted]


def _load_prior() -> dict:
    if not OUT_PATH.exists():
        return {}
    try:
        doc = yaml.safe_load(OUT_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a corrupt file must not block a run
        return {}
    return {r["id"]: r for r in doc.get("runs", [])}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run gold questions through the live agent; record SQL + rows.",
        epilog="Selection accepts dataset ids (D07) or 1-based positions (7). See --list.")
    ap.add_argument("--dataset", default="gold_dynamic")
    sel = ap.add_argument_group("selection (rate-limit friendly)")
    sel.add_argument("--id", help="run ONE question, by id (D07) or position (10)")
    sel.add_argument("--ids", help="comma-separated: 1,5,10 or D01,D02,D11")
    sel.add_argument("--range", help="inclusive range: 1-20 or D01-D11")
    sel.add_argument("--all", action="store_true", help="run every question")
    sel.add_argument("--list", action="store_true", help="list ids with positions, then exit")
    rl = ap.add_argument_group("rate limits")
    rl.add_argument("--pause", type=float, default=0.0, help="seconds between questions")
    rl.add_argument("--retries", type=int, default=2,
                    help="retries per question on a provider rate limit (default 2)")
    rl.add_argument("--backoff", type=float, default=20.0,
                    help="initial retry backoff seconds, doubled per attempt (default 20)")
    rl.add_argument("--resume", action="store_true",
                    help=f"skip questions already in {OUT_PATH.name}")
    ap.add_argument("--variants", action="store_true",
                    help="also run each item's paraphrase/other-language variants")
    args = ap.parse_args()

    src = yaml.safe_load((HERE / "datasets" / f"{args.dataset}.yaml").read_text(
        encoding="utf-8"))
    items = src["items"]

    if args.list:
        print(f"{args.dataset}: {len(items)} questions\n")
        for n, it in enumerate(items, 1):
            print(f"  {n:3d}  {it['id']:5s}  {it.get('difficulty',''):22s} "
                  f"{it['question'][:70]}")
        return 0

    selected = _select(items, args)
    prior = _load_prior()
    scopes = set((src.get("meta") or {}).get("scopes_required") or ["dynamic_sql"])

    # Expand variants into their own questions, inheriting the parent's id-space.
    todo: list[tuple[str, str, dict]] = []
    for it in selected:
        todo.append((it["id"], it["question"], it))
        if args.variants:
            for v in it.get("variants") or []:
                todo.append((f'{it["id"]}::{v.get("lang","en")}', v["text"], it))

    print(f"Running {len(todo)} question(s) through the agent: "
          f"{', '.join(t[0] for t in todo)}")
    graph = build_sql_agent_graph()
    records = dict(prior)

    for cid, question, item in todo:
        if args.resume and cid in prior:
            print(f"[SKIP] {cid:12s} already recorded (--resume)")
            continue
        rec = ask_agent(graph, question, cid, scopes, args.retries, args.backoff)
        records[cid] = {
            "id": cid, "question": question,
            "gold_id": item["id"],
            **{k: rec[k] for k in ("status", "tools_called", "agent_sql", "agent_tables",
                                   "agent_columns", "row_count", "exec_error",
                                   "agent_answer", "latency_ms")},
            "agent_result": rec["agent_result"],
        }
        if rec.get("error"):
            records[cid]["error"] = rec["error"]
        n = rec["row_count"]
        print(f"[{rec['status']:12s}] {cid:12s} rows={n if n is not None else '-':>3} "
              f"tools={','.join(rec['tools_called']) or '-'}")
        if args.pause:
            time.sleep(args.pause)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ordered = [records[k] for k in sorted(records)]
    OUT_PATH.write_text(yaml.safe_dump(
        {"dataset": args.dataset,
         "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "runs": ordered},
        sort_keys=False, allow_unicode=True, width=4096, default_flow_style=False),
        encoding="utf-8")

    by_status: dict[str, int] = {}
    for r in ordered:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"\nWrote {OUT_PATH}  ({len(ordered)} run(s) recorded)")
    print("Status: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print(f"\nNext:  .venv/Scripts/python.exe eval/compare_llm.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
