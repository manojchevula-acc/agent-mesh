"""CI mode markdown report writer for FAB AgentMesh.

Produces reports/ci_report_{ts}.md from evaluator smoke-test results
and pass/fail threshold checks collected during --mode ci.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import List


def save_ci_markdown_report(
    smoke_results: List[dict],
    threshold_results: List[dict],
    output_dir: str,
) -> str:
    """Write a ci_report_{ts}.md and ci_report_{ts}.json to output_dir.

    Args:
        smoke_results: List of dicts with keys:
            evaluator (str), check (str), score (float), label (str), passed (bool)
        threshold_results: List of dicts with keys:
            metric (str), score (float), threshold (float), passed (bool)
        output_dir: Directory to write reports into.

    Returns:
        Path to the written .md file.
    """
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    total_checks = len(smoke_results) + len(threshold_results)
    failures = sum(1 for r in smoke_results if not r["passed"]) + sum(1 for r in threshold_results if not r["passed"])
    overall = "ALL PASS" if failures == 0 else f"{failures} FAILURE{'S' if failures != 1 else ''}"

    # ── JSON ──────────────────────────────────────────────────────────────────
    json_path = os.path.join(output_dir, f"ci_report_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": ts,
                "total_checks": total_checks,
                "failures": failures,
                "overall": overall,
                "smoke_results": smoke_results,
                "threshold_results": threshold_results,
            },
            f,
            indent=2,
        )

    # ── Markdown ──────────────────────────────────────────────────────────────
    lines: List[str] = [
        "# FAB AgentMesh — CI Evaluation Report",
        "",
        f"**Generated:** {ts}  ",
        f"**Total checks:** {total_checks}  **Failures:** {failures}  **Result:** {overall}",
        "",
        "---",
        "",
        "## Evaluator Smoke Tests",
        "",
        "Offline checks that exercise each evaluator function against synthetic inputs "
        "to verify the evaluation logic is working correctly (no live agents required).",
        "",
        "| Evaluator | Check | Score | Label | Result |",
        "|---|---|---|---|---|",
    ]
    for r in smoke_results:
        icon = "✅ PASS" if r["passed"] else "❌ FAIL"
        score_str = f"{r['score']:.2f}" if r["score"] is not None else "—"
        lines.append(
            f"| {r['evaluator']} | {r['check']} | {score_str} | {r['label']} | {icon} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Pass / Fail Threshold Validation",
        "",
        "Verifies that key metrics meet the minimum thresholds defined in `config.PASS_THRESHOLDS`.",
        "",
        "| Metric | Score | Threshold | Result |",
        "|---|---|---|---|",
    ]
    for r in threshold_results:
        icon = "✅ PASS" if r["passed"] else "❌ FAIL"
        lines.append(
            f"| {r['metric']} | {r['score']:.2f} | >= {r['threshold']:.2f} | {icon} |"
        )

    lines += [
        "",
        "---",
        "",
        f"## CI Gate Result",
        "",
        f"**{overall}**" + (
            ""
            if failures == 0
            else f" — {failures} check(s) did not meet their threshold."
        ),
        "",
    ]

    md_path = os.path.join(output_dir, f"ci_report_{ts}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"CI report saved: {md_path}")
    return md_path
