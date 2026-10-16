"""Print one trace from the Collector's data-plane file as an indented tree.

    python scripts/inspect_trace.py <trace_id> [--file collector/out/dataplane-traces.json] [--json out.json]

Shows service, span name, status, duration and the key GERNAS attributes per span,
then a summary: services involved, span count, orphaned parents, ERROR spans,
token totals and estimated cost.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("gernas.agent_id", "gernas.agent_run_id", "gernas.parent_run_id", "gernas.termination_reason",
        "gernas.child_agent_id", "gernas.mcp.server_id", "gernas.mcp.tool_name", "gernas.tool_id",
        "gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gernas.est_cost_aed",
        "gernas.policy_decision", "gernas.safety.content_blocked", "gernas.memory.hit", "gernas.retrieval.result_count",
        "gernas.business_outcome", "gernas.hitl.outcome", "http.response.status_code", "error.type",
        "gernas.prompt.template_id", "gernas.retry_count", "gernas.run.steps")


def _val(v: dict):
    return next(iter(v.values())) if v else None


def load(path: Path, trace_id: str) -> list[dict]:
    spans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for rs in json.loads(line).get("resourceSpans", []):
            svc = next((_val(a["value"]) for a in rs["resource"].get("attributes", []) if a["key"] == "service.name"), "?")
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    if sp["traceId"] != trace_id:
                        continue
                    attrs = {a["key"]: _val(a["value"]) for a in sp.get("attributes", [])}
                    spans.append({"service": svc, "scope": ss.get("scope", {}).get("name"), "name": sp["name"],
                                  "id": sp["spanId"], "parent": sp.get("parentSpanId") or None,
                                  "status": {1: "OK", 2: "ERROR"}.get((sp.get("status") or {}).get("code"), "UNSET"),
                                  "ms": (int(sp["endTimeUnixNano"]) - int(sp["startTimeUnixNano"])) / 1e6,
                                  "start": int(sp["startTimeUnixNano"]), "attributes": attrs,
                                  "events": [e.get("name") for e in sp.get("events", [])]})
    return spans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_id")
    ap.add_argument("--file", default=str(ROOT / "collector" / "out" / "dataplane-traces.json"))
    ap.add_argument("--json")
    ap.add_argument("--max-depth", type=int, default=30)
    args = ap.parse_args()
    spans = load(Path(args.file), args.trace_id)
    if not spans:
        print("trace not found (tail sampling may still be holding it)")
        return 1
    ids = {s["id"] for s in spans}
    children = defaultdict(list)
    roots = []
    for s in sorted(spans, key=lambda x: x["start"]):
        (children[s["parent"]] if s["parent"] in ids else roots).append(s)

    lines = []

    def walk(span: dict, depth: int) -> None:
        a = span["attributes"]
        extra = " ".join(f"{k.split('.', 1)[1] if k.startswith('gernas.') else k}={a[k]}" for k in KEYS
                         if k in a and k not in ("gernas.agent_run_id", "gernas.parent_run_id"))
        run = a.get("gernas.agent_run_id")
        run_txt = f" run={run[:12]}" if run else ""
        parent_run = a.get("gernas.parent_run_id")
        run_txt += f" parent_run={parent_run[:12]}" if parent_run and span["name"] == "agent.run" else ""
        lines.append(f"{'  ' * depth}{span['name']} [{span['service']}] {span['status']} {span['ms']:.0f}ms{run_txt} {extra}"
                     + (f" events={span['events']}" if span["events"] else ""))
        if depth < args.max_depth:
            for c in children[span["id"]]:
                walk(c, depth + 1)

    for r in roots:
        walk(r, 0)
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    services = sorted({s["service"] for s in spans})
    tokens_in = sum(int(s["attributes"].get("gen_ai.usage.input_tokens") or 0) for s in spans if s["name"] == "llm.call")
    tokens_out = sum(int(s["attributes"].get("gen_ai.usage.output_tokens") or 0) for s in spans if s["name"] == "llm.call")
    cost = sum(float(s["attributes"].get("gernas.est_cost_aed") or 0) for s in spans)
    summary = {"trace_id": args.trace_id, "spans": len(spans), "roots": [r["name"] for r in roots],
               "services": services, "error_spans": [s["name"] for s in spans if s["status"] == "ERROR"],
               "gernas_llm_calls": sum(1 for s in spans if s["name"] == "llm.call" and s["scope"] == "gernas"),
               "input_tokens": tokens_in, "output_tokens": tokens_out, "est_cost_aed": round(cost, 6),
               "agent_runs": sum(1 for s in spans if s["name"] == "agent.run"),
               "delegations": sum(1 for s in spans if s["name"] == "agent.delegate"),
               "mcp_calls": sum(1 for s in spans if s["name"] == "mcp.call")}
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "tree": lines}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
