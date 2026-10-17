"""Print one trace from the Collector's data-plane file as an indented tree.

    python scripts/inspect_trace.py <trace_id> [--file collector/out/dataplane-traces.json] [--json out.json]

Shows service, span name, status, duration and the key GERNAS attributes per span,
then a summary: services involved, span counts, ERROR spans, token totals and
estimated cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gernas.attributes import Ctx, SpanName  # noqa: E402

KEYS = (
    Ctx.AGENT_ID,
    Ctx.TERMINATION_REASON,
    Ctx.STEP_ID,
    Ctx.CHILD_AGENT_ID,
    Ctx.TOOL_ID,
    Ctx.TOOL_TYPE,
    Ctx.DEPENDENCY,
    Ctx.MODEL_ID,
    Ctx.TOKEN_INPUT,
    Ctx.TOKEN_OUTPUT,
    Ctx.ESTIMATED_COST,
    Ctx.POLICY_DECISION,
    Ctx.CONTENT_BLOCKED,
    Ctx.MEMORY_HIT,
    Ctx.RESULT_COUNT,
    Ctx.BUSINESS_OUTCOME,
    Ctx.HITL_OUTCOME,
    Ctx.HTTP_STATUS_CODE,
    Ctx.ERROR_CATEGORY,
    Ctx.STEP_COUNT,
    Ctx.RETRY_COUNT,
)


def _val(v: dict):
    return next(iter(v.values())) if v else None


def load(path: Path, trace_id: str) -> list[dict]:
    spans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for rs in json.loads(line).get("resourceSpans", []):
            svc = next(
                (_val(a["value"]) for a in rs["resource"].get("attributes", []) if a["key"] == "service.name"), "?"
            )
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    if sp["traceId"] != trace_id:
                        continue
                    spans.append(
                        {
                            "service": svc,
                            "name": sp["name"],
                            "id": sp["spanId"],
                            "parent": sp.get("parentSpanId") or None,
                            "status": {1: "OK", 2: "ERROR"}.get((sp.get("status") or {}).get("code"), "UNSET"),
                            "ms": (int(sp["endTimeUnixNano"]) - int(sp["startTimeUnixNano"])) / 1e6,
                            "start": int(sp["startTimeUnixNano"]),
                            "attributes": {a["key"]: _val(a["value"]) for a in sp.get("attributes", [])},
                            "events": [e.get("name") for e in sp.get("events", [])],
                        }
                    )
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
        extra = " ".join(f"{k.split('.', 1)[1] if k.startswith('gernas.') else k}={a[k]}" for k in KEYS if k in a)
        run = a.get(Ctx.AGENT_RUN_ID)
        run_txt = f" run={run[:12]}" if run else ""
        parent_run = a.get(Ctx.PARENT_RUN_ID)
        run_txt += f" parent_run={parent_run[:12]}" if parent_run and span["name"] == SpanName.AGENT else ""
        lines.append(
            f"{'  ' * depth}{span['name']} [{span['service']}] {span['status']} {span['ms']:.0f}ms{run_txt} {extra}"
            + (f" events={span['events']}" if span["events"] else "")
        )
        if depth < args.max_depth:
            for c in children[span["id"]]:
                walk(c, depth + 1)

    for r in roots:
        walk(r, 0)
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    llm = [s for s in spans if s["name"] == SpanName.LLM]
    count = lambda name: sum(1 for s in spans if s["name"] == name)  # noqa: E731
    summary = {
        "trace_id": args.trace_id,
        "spans": len(spans),
        "roots": [r["name"] for r in roots],
        "services": sorted({s["service"] for s in spans}),
        "error_spans": [s["name"] for s in spans if s["status"] == "ERROR"],
        "agent_runs": count(SpanName.AGENT),
        "steps": count(SpanName.STEP),
        "delegations": count(SpanName.DELEGATION),
        "llm_calls": len(llm),
        "tool_calls": count(SpanName.TOOL),
        "mcp_tool_calls": sum(1 for s in spans if s["attributes"].get(Ctx.TOOL_TYPE) == "mcp"),
        "token_input": sum(int(s["attributes"].get(Ctx.TOKEN_INPUT) or 0) for s in llm),
        "token_output": sum(int(s["attributes"].get(Ctx.TOKEN_OUTPUT) or 0) for s in llm),
        "estimated_cost": round(sum(float(s["attributes"].get(Ctx.ESTIMATED_COST) or 0) for s in spans), 6),
    }
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "tree": lines}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
