"""Verify what Opik actually stored for one request: hierarchy, attributes, conformance.

    python scripts/opik_verify.py --task-id E2E000 [--timeout 90] [--json out.json]

Finds Opik spans whose ``gernas.task_id`` equals the request id, then loads every span
of those Opik traces and checks:

* one Opik trace per request (the OTel trace was not split across processes);
* every parent link resolves inside the trace (no orphans);
* the GERNAS conformance rules (required attributes, run context, duplicates,
  denylisted keys, enums) from :mod:`gernas.conformance`, applied to Opik's copy;
* ``llm.call`` spans are typed ``llm`` by Opik with model / provider (Collector mapping).

Opik stores OTel span attributes in the span ``input`` map and re-keys trace / span ids
as UUIDs, so hierarchy is checked on Opik's ids. Credentials: OPIK_API_KEY,
OPIK_WORKSPACE, OPIK_PROJECT_NAME (never printed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gernas.attributes import Ctx, SpanName  # noqa: E402
from gernas.conformance import NormalSpan, check  # noqa: E402

BASE = os.getenv("OPIK_URL_OVERRIDE", "https://www.comet.com/opik/api").rstrip("/") + "/v1/private"


def _get(path: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        headers={"authorization": os.environ["OPIK_API_KEY"], "Comet-Workspace": os.environ["OPIK_WORKSPACE"]},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https URL
        return json.loads(resp.read())


def _spans(query: str) -> list[dict]:
    out, page = [], 1
    while True:
        doc = _get(f"/spans?{query}&page={page}&size=100")
        out.extend(doc.get("content", []))
        if len(doc.get("content", [])) < 100:
            return out
        page += 1


def find_traces(project: str, task_id: str, timeout_s: float) -> set[str]:
    deadline = time.monotonic() + timeout_s
    q = f"project_name={urllib.parse.quote(project)}"
    while True:
        recent = _get(f"/spans?{q}&page=1&size=500").get("content", [])
        ids = {s["trace_id"] for s in recent if (s.get("input") or {}).get(Ctx.TASK_ID) == task_id}
        if ids or time.monotonic() > deadline:
            return ids
        time.sleep(5)


def to_normal(s: dict) -> NormalSpan:
    attrs = {k: v for k, v in (s.get("input") or {}).items() if isinstance(k, str)}
    status = "ERROR" if attrs.get(Ctx.EXECUTION_STATUS) == "error" else "OK"
    return NormalSpan(s["name"], s["trace_id"], s["id"], s.get("parent_span_id") or None, status, attrs, {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True, help="request id passed as gernas task_id")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--json")
    args = ap.parse_args()
    project = os.environ["OPIK_PROJECT_NAME"]
    trace_ids = find_traces(project, args.task_id, args.timeout)
    if not trace_ids:
        print(f"[FAIL] no Opik span with {Ctx.TASK_ID}={args.task_id} within {args.timeout:.0f}s")
        return 1
    spans = [s for t in sorted(trace_ids) for s in _spans(f"project_name={urllib.parse.quote(project)}&trace_id={t}")]
    by_id = {s["id"]: s for s in spans}
    orphans = [s["name"] for s in spans if s.get("parent_span_id") and s["parent_span_id"] not in by_id]
    norm = [to_normal(s) for s in spans]
    report = check(norm, require_gernas_only=True)
    llm = [s for s in spans if s["name"] == SpanName.LLM]
    llm_typed = all(s.get("type") == "llm" and s.get("model") for s in llm)
    foreign = [n.name for n in norm if Ctx.OPERATION not in n.attributes]
    ops = defaultdict(int)
    for n in norm:
        if n.operation:
            ops[n.operation] += 1

    checks = [
        ("request is one Opik trace", len(trace_ids) == 1, f"{len(trace_ids)} trace(s)"),
        ("every parent link resolves", not orphans, f"orphans: {orphans}" if orphans else ""),
        (
            "GERNAS conformance on Opik's copy",
            report.passed,
            "; ".join(f"[{v.rule}] {v.span}:{v.key}" for v in report.violations[:8]),
        ),
        ("llm.call typed 'llm' with model by Opik", llm_typed if llm else True, f"{len(llm)} llm span(s)"),
        (
            "only Gernas SDK spans (no competing instrumentation)",
            not foreign,
            f"foreign: {sorted(set(foreign))[:8]}" if foreign else "",
        ),
    ]
    children = defaultdict(list)
    for s in sorted(spans, key=lambda x: x.get("start_time", "")):
        children[s.get("parent_span_id")].append(s)
    lines: list[str] = []

    def walk(s: dict, depth: int) -> None:
        a = s.get("input") or {}
        keys = (Ctx.AGENT_ID, Ctx.STEP_ID, Ctx.TOOL_ID, Ctx.MODEL_ID, Ctx.EXECUTION_STATUS, Ctx.TERMINATION_REASON)
        extra = " ".join(f"{k.split('.', 1)[1]}={a[k]}" for k in keys if k in a)
        lines.append(f"{'  ' * depth}{s['name']} [{s.get('type')}] {extra}")
        for c in children[s["id"]]:
            walk(c, depth + 1)

    for root in children[None]:
        walk(root, 0)
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    print("operations: " + ", ".join(f"{k}={v}" for k, v in sorted(ops.items())))
    ok = all(c[1] for c in checks)
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "task_id": args.task_id,
                    "opik_trace_ids": sorted(trace_ids),
                    "spans": len(spans),
                    "operations": ops,
                    "tree": lines,
                    "checks": [{"check": n, "ok": o, "detail": d} for n, o, d in checks],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
