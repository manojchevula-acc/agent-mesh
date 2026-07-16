"""Single-task markdown report writer for FAB AgentMesh.

Produces reports/single_report_{task}_{ts}.md when --mode single is used.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, Optional


def save_single_markdown_report(
    result: Any,
    agent: str,
    task_info: Dict[str, Any],
    output_dir: str,
) -> str:
    """Write a single_report_{task}_{ts}.md to output_dir.

    Args:
        result:    BenchmarkTaskResult (task_name, dataset_id, task_type,
                   n_samples, metrics, error, per_sample).
        agent:     Agent identifier string (e.g. "api", "rag").
        task_info: Entry from TASK_REGISTRY for this task.
        output_dir: Directory to write the report into.

    Returns:
        Path to the written .md file.
    """
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_task = result.task_name.replace("/", "_")

    # Resolve pass/fail per metric using PASS_THRESHOLDS from config
    try:
        import sys, pathlib as _pl
        _eval_root = str(_pl.Path(__file__).resolve().parents[1])
        if _eval_root not in sys.path:
            sys.path.insert(0, _eval_root)
        from config import PASS_THRESHOLDS
    except Exception:
        PASS_THRESHOLDS = {}

    from financial_benchmarks.benchmark_report import score_explanation, task_pass_icon

    # ── JSON sidecar ──────────────────────────────────────────────────────────
    json_path = os.path.join(output_dir, f"single_report_{safe_task}_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": ts,
                "agent": agent,
                "task_name": result.task_name,
                "dataset_id": result.dataset_id,
                "task_type": result.task_type,
                "n_samples": result.n_samples,
                "metrics": result.metrics,
                "error": result.error,
                "per_sample": (result.per_sample or [])[:5],
            },
            f,
            indent=2,
        )

    # ── Markdown ──────────────────────────────────────────────────────────────
    icon = task_pass_icon(result.metrics, result.error)
    explanation = score_explanation(result.task_name, result.metrics, result.error, task_info)

    category    = task_info.get("category", "—")
    tier        = task_info.get("tier", "—")
    description = task_info.get("description", "—")
    dataset_id  = result.dataset_id or task_info.get("dataset_id", "—")

    lines = [
        f"# FAB AgentMesh — Single Task Report",
        "",
        f"**Generated:** {ts}  ",
        f"**Agent:** `{agent}`  **Task:** `{result.task_name}`  **Result:** {icon}",
        "",
        "---",
        "",
        "## Task Overview",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Category | {category} |",
        f"| Tier | {tier} |",
        f"| Dataset | `{dataset_id}` |",
        f"| Samples evaluated | {result.n_samples} |",
        f"| Description | {description} |",
        "",
        "---",
        "",
        "## Metrics",
        "",
    ]

    if result.error:
        lines += [f"> **Error:** {result.error}", ""]
    elif result.metrics:
        lines += [
            "| Metric | Score | Threshold | Result |",
            "|---|---|---|---|",
        ]
        for k, v in result.metrics.items():
            threshold = PASS_THRESHOLDS.get(k)
            if threshold is not None:
                passed = v >= threshold
                t_str = f">= {threshold:.2f}"
                r_icon = "✅ PASS" if passed else "❌ FAIL"
            else:
                t_str = "—"
                r_icon = "—"
            lines.append(f"| {k} | {v:.4f} | {t_str} | {r_icon} |")
        lines.append("")
    else:
        lines += ["*No metrics computed (dry run).*", ""]

    lines += [
        "**Why this score:** " + explanation,
        "",
        "---",
        "",
        "## Sample Interactions",
        "",
    ]

    per_sample = (result.per_sample or [])[:5]
    if per_sample:
        lines += [
            "| # | Query (preview) | Gold | Predicted | Match |",
            "|---|---|---|---|---|",
        ]
        for i, s in enumerate(per_sample, 1):
            q    = str(s.get("query_preview", s.get("query", ""))).strip()[:80]
            gold = str(s.get("gold", s.get("label", ""))).strip()[:40]
            pred = str(s.get("pred", s.get("response", ""))).strip()[:40]
            match_val = s.get("match")
            if match_val is None:
                # fall back to numeric score check
                score_val = s.get("f1", s.get("em", s.get("rouge1", s.get("pearson"))))
                match_icon = "✅" if (score_val is not None and score_val >= 0.5) else "❌"
            else:
                match_icon = "✅" if match_val else "❌"
            lines.append(f"| {i} | {q} | {gold} | {pred} | {match_icon} |")
        lines.append("")
    else:
        lines += ["*No per-sample data available (dry run or not captured).*", ""]

    md_path = os.path.join(output_dir, f"single_report_{safe_task}_{ts}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Single-task report saved: {md_path}")
    return md_path
