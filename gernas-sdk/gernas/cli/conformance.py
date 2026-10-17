"""``gernas conformance`` — check exported spans against the GERNAS semantic conventions.

Exit code 0 = conformant, 1 = violations (machine-readable JSON report with
``--json``), 2 = usage/input error. Designed for the agent-team CI template::

    gernas conformance --file collector/out/dataplane-traces.json --service my-agent-service
"""

from __future__ import annotations

import argparse
import json
import sys

from gernas.conformance import check, load_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gernas conformance", description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, action="append", help="span file (repeatable)")
    ap.add_argument("--service", help="only check spans whose resource service.name matches")
    ap.add_argument("--gernas-only", action="store_true", help="skip spans not produced by Gernas decorators")
    ap.add_argument("--require-op", action="append", default=[], help="fail unless at least one span of this op exists")
    ap.add_argument("--max-bounded-values", type=int, default=500)
    ap.add_argument("--json", action="store_true", help="print the JSON report")
    args = ap.parse_args(argv)
    try:
        spans = [s for f in args.file for s in load_file(f)]
    except (OSError, ValueError) as exc:
        print(f"gernas conformance: cannot read input: {exc}", file=sys.stderr)
        return 2
    if args.service:
        spans = [s for s in spans if s.resource.get("service.name") == args.service]
    report = check(spans, max_bounded_values=args.max_bounded_values, require_gernas_only=args.gernas_only)
    missing_ops = [op for op in args.require_op if report.operations.get(op, 0) == 0]
    out = report.to_dict()
    out["missing_operations"] = missing_ops
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"spans checked: {report.spans_checked}  gernas spans: {report.gernas_spans}")
        print("operations: " + ", ".join(f"{k}={v}" for k, v in sorted(report.operations.items())))
        for v in report.violations[:50]:
            print(f"  [{v.rule}] {v.span}: {v.key} — {v.detail}")
        if len(report.violations) > 50:
            print(f"  ... {len(report.violations) - 50} more")
        for op in missing_ops:
            print(f"  [coverage] no span with operation {op}")
        print("RESULT: " + ("PASS" if report.passed and not missing_ops else "FAIL"))
    return 0 if report.passed and not missing_ops else 1
