"""Formats workflow evaluation results to console, JSON, CSV, and Markdown."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import List

from .run_maf_eval import CaseResult


def print_summary(results: List[CaseResult]) -> None:
    """Prints a per-case table and aggregate scores to stdout."""
    print("\n" + "=" * 90)
    print(f"{'ID':<18} {'User':<10} {'Route':<20} {'Blkd':<6} {'Comp':<6} {'PII':<6} {'RBAC':<6} {'Cit':<6} {'KW%':<6} {'ms'}")
    print("-" * 90)
    for r in results:
        s = r.scores
        print(
            f"{r.case_id:<18} {r.username:<10} {r.route_type:<20} "
            f"{'Y' if r.blocked else 'N':<6} "
            f"{s.get('compliance_decision', '-')!s:<6} "
            f"{s.get('pii_clean', '-')!s:<6} "
            f"{s.get('rbac_scope', '-')!s:<6} "
            f"{s.get('citation', '-')!s:<6} "
            f"{s.get('keyword_coverage', '-')!s:<6} "
            f"{r.latency_ms:.0f}"
        )
    print("=" * 90)

    all_metrics: set[str] = set()
    for r in results:
        all_metrics.update(r.scores.keys())

    print("\nAggregate scores:")
    for metric in sorted(all_metrics):
        vals = [r.scores[metric] for r in results if metric in r.scores]
        if vals:
            avg = sum(vals) / len(vals)
            print(f"  {metric:<35} avg={avg:.3f}  n={len(vals)}")


def save_json(results: List[CaseResult], output_dir: str) -> str:
    """Save enriched JSON report with full answers and per-evaluator narratives."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"evaluation_results_{ts}.json")

    serialized = []
    for r in results:
        overall_pass = all(d.get("passed", True) for d in r.eval_details) if r.eval_details else None
        block_reason = None
        if r.blocked:
            stage_label = {
                "guardrail": "Blocked by the deterministic input guardrail (injection / PII / destructive-intent pattern detected before any LLM call).",
                "rbac": "Blocked by RBAC — user does not have permission to access the requested resource.",
                "compliance": "Blocked by the ComplianceAgent semantic safety check.",
                "compliance_agent_error": "ComplianceAgent returned an error (check agent logs).",
                "unknown": "Request was blocked but the stage could not be determined from available records.",
                "eval_error": "An exception occurred during evaluation — the request may or may not have been blocked.",
            }
            block_reason = stage_label.get(r.block_stage or "unknown", f"Blocked at stage: {r.block_stage}")

        serialized.append({
            "case_id": r.case_id,
            "context": {
                "username": r.username,
                "role": r.role or "unknown",
                "route_type": r.route_type,
            },
            "task": {
                "query": r.query,
                "expected_outcome": (
                    "block — request should be stopped by a security guardrail"
                    if r.blocked
                    else "pass — request should be answered without blocking"
                ),
            },
            "outcome": {
                "blocked": r.blocked,
                "block_stage": r.block_stage,
                "block_reason": block_reason,
                "latency_ms": round(r.latency_ms),
                "agents_called": r.agents_called,
                "full_answer": r.answer,
                "overall_pass": overall_pass,
            },
            "evaluation": r.eval_details,
            "scores": r.scores,
            "error": r.error,
        })

    payload = {
        "report_type": "FAB AgentMesh Workflow Evaluation",
        "timestamp": ts,
        "total_cases": len(results),
        "overall_pass_rate": _pass_rate(results),
        "results": serialized,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"JSON report saved: {path}")
    return path


def save_csv(results: List[CaseResult], output_dir: str) -> str:
    """Save flat CSV with one row per case."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"evaluation_results_{ts}.csv")
    all_metrics = sorted({k for r in results for k in r.scores})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["case_id", "username", "role", "route_type", "blocked", "block_stage",
             "latency_ms", "query_preview", "answer_preview"]
            + all_metrics
        )
        for r in results:
            writer.writerow(
                [r.case_id, r.username, r.role, r.route_type,
                 r.blocked, r.block_stage or "", f"{r.latency_ms:.0f}",
                 r.query[:120], r.answer[:120]]
                + [r.scores.get(m, "") for m in all_metrics]
            )
    print(f"CSV report saved: {path}")
    return path


def save_markdown_report(results: List[CaseResult], output_dir: str) -> str:
    """Save a human-readable Markdown report anyone can understand end-to-end."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"evaluation_report_{ts}.md")

    pass_rate = _pass_rate(results)
    total = len(results)
    passed_count = sum(
        1 for r in results
        if all(d.get("passed", True) for d in r.eval_details)
    )

    lines: list[str] = [
        "# FAB AgentMesh — Workflow Evaluation Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Total cases evaluated:** {total}  ",
        f"**Overall pass rate:** {pass_rate:.1%} ({passed_count}/{total} cases fully passing)  ",
        "",
        "---",
        "",
        "## Summary Table",
        "",
        "| Case ID | User | Role | Route | Blocked | Overall | Latency |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in results:
        overall = "✅ PASS" if all(d.get("passed", True) for d in r.eval_details) else "❌ FAIL"
        if r.blocked and not r.eval_details:
            overall = "⚠️ BLOCKED"
        lines.append(
            f"| {r.case_id} | {r.username} | {r.role or '—'} | {r.route_type} "
            f"| {'YES' if r.blocked else 'no'} | {overall} | {r.latency_ms/1000:.1f}s |"
        )

    lines += ["", "---", "", "## Detailed Case Results", ""]

    for r in results:
        overall_pass = all(d.get("passed", True) for d in r.eval_details)
        status_icon = "✅ PASS" if (overall_pass and not r.error) else ("❌ FAIL" if not r.blocked else "⚠️ BLOCKED")

        lines += [
            f"### {r.case_id} — {status_icon}",
            "",
            f"**User:** {r.username}  ",
            f"**Role:** {r.role or 'unknown'}  ",
            f"**Task type:** {r.route_type}  ",
            f"**Latency:** {r.latency_ms/1000:.2f}s  ",
        ]
        if r.agents_called:
            lines.append(f"**Agents invoked:** {', '.join(r.agents_called)}  ")
        lines.append("")

        lines += ["#### Query", ""]
        lines.append(f"> {r.query}")
        lines.append("")

        if r.blocked:
            stage_label = {
                "guardrail": "Deterministic input guardrail (injection / PII / destructive-intent pattern)",
                "rbac": "RBAC — insufficient permissions for the requested resource",
                "compliance": "ComplianceAgent semantic safety check",
                "compliance_agent_error": "ComplianceAgent error (check agent logs)",
                "unknown": "Unknown stage (check audit_trail.jsonl for details)",
                "eval_error": "Evaluation exception",
            }
            stage_desc = stage_label.get(r.block_stage or "unknown", f"Stage: {r.block_stage}")
            lines += [
                "#### Outcome: Blocked",
                "",
                f"**Block stage:** {r.block_stage or 'unknown'}  ",
                f"**Reason:** {stage_desc}  ",
                "",
                "_No agent response was generated — the request was stopped before reaching PriceAssistAgent._",
                "",
            ]
        else:
            lines += ["#### Agent Response", ""]
            if r.answer:
                # Wrap long responses in a blockquote
                response_lines = r.answer.strip().split("\n")
                for line in response_lines[:40]:   # cap at 40 lines for readability
                    lines.append(f"> {line}")
                if len(response_lines) > 40:
                    lines.append(f"> _(response truncated at 40 lines — {len(response_lines)} total)_")
            else:
                lines.append("_[No answer returned]_")
            lines.append("")

        if r.eval_details:
            lines += ["#### Evaluation Details", ""]
            lines += [
                "| Evaluator | Score | Result |",
                "|---|---|---|",
            ]
            for d in r.eval_details:
                icon = "✅" if d.get("passed") else "❌"
                lines.append(f"| {d['evaluator']} | {d['score']:.2f} | {icon} {'PASS' if d.get('passed') else 'FAIL'} |")
            lines.append("")
            for d in r.eval_details:
                icon = "✅" if d.get("passed") else "❌"
                lines += [
                    f"**{icon} {d['evaluator']}** (score: {d['score']:.2f})",
                    "",
                    f"*What was checked:* {d['what_was_checked']}",
                    "",
                    f"*Finding:* {d['finding']}",
                    "",
                ]

        if r.error:
            lines += [f"> ⚠️ **Evaluation error:** {r.error}", ""]

        lines += ["---", ""]

    # Footer aggregates
    lines += ["## Aggregate Scores", ""]
    all_metrics: set[str] = set()
    for r in results:
        all_metrics.update(r.scores.keys())

    lines += ["| Metric | Average | Cases Scored |", "|---|---|---|"]
    for metric in sorted(all_metrics):
        vals = [r.scores[metric] for r in results if metric in r.scores]
        if vals:
            avg = sum(vals) / len(vals)
            lines.append(f"| {metric} | {avg:.3f} | {len(vals)}/{total} |")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Markdown report saved: {path}")
    return path


def _pass_rate(results: List[CaseResult]) -> float:
    if not results:
        return 0.0
    passed = sum(
        1 for r in results
        if all(d.get("passed", True) for d in r.eval_details)
    )
    return passed / len(results)
