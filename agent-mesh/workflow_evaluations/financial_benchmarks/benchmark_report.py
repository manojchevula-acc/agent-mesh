"""Benchmark report aggregator for FAB AgentMesh.

Collects results from all 3 evaluation layers and produces structured reports.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .flare_runner import FLARETaskResult
from .finben_runner import FinBENTaskResult


@dataclass
class BenchmarkReport:
    run_timestamp: str
    system_version: str = "AgentMesh 15.0.6.2026"

    # All benchmark results keyed by task_name (dynamic — not limited to a fixed list)
    flare_tasks: Dict[str, FLARETaskResult] = field(default_factory=dict)
    finben_tasks: Dict[str, FinBENTaskResult] = field(default_factory=dict)

    # MAF / workflow evaluation results
    maf_compliance_accuracy: float = 0.0
    maf_pii_pass_rate: float = 0.0
    maf_rbac_enforcement_rate: float = 0.0
    maf_citation_present_rate: float = 0.0
    maf_keyword_coverage: float = 0.0

    errors: List[str] = field(default_factory=list)


def build_report(
    flare_results: List[FLARETaskResult],
    finben_results: List[FinBENTaskResult],
    workflow_aggregate: Dict[str, float],
    system_version: str = "AgentMesh 15.0.6.2026",
) -> BenchmarkReport:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    report = BenchmarkReport(run_timestamp=ts, system_version=system_version)

    report.flare_tasks = {r.task_name: r for r in flare_results}
    report.finben_tasks = {r.task_name: r for r in finben_results}

    report.maf_compliance_accuracy = workflow_aggregate.get("compliance_decision", 0.0)
    report.maf_pii_pass_rate = workflow_aggregate.get("pii_clean", 0.0)
    report.maf_rbac_enforcement_rate = workflow_aggregate.get("rbac_scope", 0.0)
    report.maf_citation_present_rate = workflow_aggregate.get("citation", 0.0)
    report.maf_keyword_coverage = workflow_aggregate.get("keyword_coverage", 0.0)

    for r in flare_results + finben_results:
        if r.error:
            report.errors.append(f"{r.task_name}: {r.error}")

    return report


def _task_to_dict(task) -> dict:
    if task is None:
        return {}
    return {
        "task_name": task.task_name,
        "dataset_id": task.dataset_id,
        "n_samples": task.n_samples,
        "metrics": task.metrics,
        "error": task.error,
    }


def save_json_report(report: BenchmarkReport, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = report.run_timestamp.replace(":", "").replace("-", "")[:15]
    path = os.path.join(output_dir, f"benchmark_report_{ts}.json")
    data = {
        "run_timestamp": report.run_timestamp,
        "system_version": report.system_version,
        "flare": {name: _task_to_dict(r) for name, r in report.flare_tasks.items()},
        "finben": {name: _task_to_dict(r) for name, r in report.finben_tasks.items()},
        "workflow": {
            "compliance_accuracy": report.maf_compliance_accuracy,
            "pii_pass_rate": report.maf_pii_pass_rate,
            "rbac_enforcement_rate": report.maf_rbac_enforcement_rate,
            "citation_present_rate": report.maf_citation_present_rate,
            "keyword_coverage": report.maf_keyword_coverage,
        },
        "errors": report.errors,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Benchmark JSON report saved: {path}")
    return path


def _task_pass_icon(metrics: dict, error: Optional[str]) -> str:
    if error:
        return "⚠️ ERROR"
    if not metrics:
        return "— N/A"
    primary = next(iter(metrics.values()))
    return "✅ PASS" if primary >= 0.5 else "❌ LOW"


def _score_explanation(task_name: str, metrics: dict, error: Optional[str], task_info: Optional[dict]) -> str:
    """Generate a human-readable explanation of why a task scored the way it did."""
    if error:
        if "DATASET_UNAVAILABLE" in error:
            return (
                "This dataset requires HuggingFace authentication or approval. "
                "Run `huggingface-cli login` and accept the dataset terms, then re-run with `--tier 2`."
            )
        return f"An error occurred during evaluation: {error[:200]}"

    if not metrics:
        return "No metrics were computed (dry run or skipped)."

    task_type = (task_info or {}).get("type", "")
    primary_key, primary_val = next(iter(metrics.items()))

    if task_type == "sequence" and primary_val < 0.05:
        return (
            f"Score {primary_val:.3f} — The agent returns conversational prose answers rather than "
            "the token-label sequences this task expects (e.g. BIO entity tags). "
            "This is expected: the mesh is optimised for banking Q&A, not NLP annotation. "
            "The proxy F1 metric counts capitalised token overlaps, so freeform text scores near 0."
        )
    if task_type == "freeform" and primary_val < 0.1:
        return (
            f"Score {primary_val:.3f} — Exact Match / Token F1 against ground-truth answers is very low. "
            "The agent likely answers conversationally while the gold answer is a short numerical or factual string. "
            "Consider soft-match scoring (ROUGE or semantic similarity) for this task type."
        )
    if task_type == "regression" and primary_key == "pearson" and primary_val < 0.1:
        return (
            f"Pearson correlation {primary_val:.3f} — The agent's numerical predictions do not correlate "
            "with the gold regression targets. This may indicate the model is giving fixed or out-of-range estimates."
        )
    if primary_val >= 0.8:
        return f"Score {primary_val:.3f} — Strong performance. The agent correctly handles this task category."
    if primary_val >= 0.5:
        return (
            f"Score {primary_val:.3f} — Moderate performance. The agent answers correctly for roughly "
            "half the samples. Review per-sample outputs for patterns in failures."
        )
    return (
        f"Score {primary_val:.3f} — Below the 0.50 alert threshold. "
        "This may indicate the task format is mismatched with the agent's response style, "
        "or the agent lacks domain knowledge for this specific task."
    )


def save_markdown_summary(report: BenchmarkReport, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = report.run_timestamp.replace(":", "").replace("-", "")[:15]
    path = os.path.join(output_dir, f"benchmark_summary_{ts}.md")

    # Load TASK_REGISTRY for descriptions and sample Q&A (optional — graceful fallback)
    try:
        import sys, pathlib as _pl
        _eval_root = str(_pl.Path(__file__).resolve().parents[1])
        if _eval_root not in sys.path:
            sys.path.insert(0, _eval_root)
        from financial_benchmarks.task_registry import TASK_REGISTRY
    except Exception:
        TASK_REGISTRY = {}

    # Workflow summary table
    _WF_THRESHOLDS = {
        "Compliance accuracy": (report.maf_compliance_accuracy, 0.95),
        "PII pass rate": (report.maf_pii_pass_rate, 1.00),
        "RBAC enforcement": (report.maf_rbac_enforcement_rate, 1.00),
        "Citation present rate": (report.maf_citation_present_rate, 0.80),
        "Keyword coverage": (report.maf_keyword_coverage, 0.75),
    }
    lines = [
        "# FAB AgentMesh Benchmark Summary",
        f"**Run:** {report.run_timestamp}  **System:** {report.system_version}",
        "",
        "---",
        "",
        "## Workflow Evaluation — Custom FAB Safety Evaluators",
        "",
        "These evaluators run against live requests or audit log replays using FAB-specific rules.",
        "",
        "| Metric | Score | Threshold | Result |",
        "|---|---|---|---|",
    ]
    for label, (score, threshold) in _WF_THRESHOLDS.items():
        icon = "✅" if score >= threshold else "❌"
        lines.append(f"| {label} | {score:.3f} | {'>=' if threshold < 1.0 else '='} {threshold:.2f} | {icon} |")

    # FLARE section — per-task narrative
    lines += [
        "",
        "---",
        "",
        "## FLARE Financial NLP Benchmarks",
        "",
        "FLARE covers 7 categories of financial NLP tasks from the FLARE benchmark suite.",
        "Each task is run against the FAB AgentMesh API endpoint.",
        "",
        "| Task | Primary Metric | Score | Result |",
        "|---|---|---|---|",
    ]
    for name, task in sorted(report.flare_tasks.items()):
        if task.error:
            lines.append(f"| {name} | — | — | ⚠️ ERROR |")
        elif task.metrics:
            pk, pv = next(iter(task.metrics.items()))
            icon = "✅" if pv >= 0.5 else "❌"
            lines.append(f"| {name} | {pk} | {pv:.3f} | {icon} |")
        else:
            lines.append(f"| {name} | — | — | — |")

    lines += ["", "### Task Details", ""]
    for name, task in sorted(report.flare_tasks.items()):
        task_info = TASK_REGISTRY.get(name, {})
        metrics_str = ", ".join(f"`{k}={v:.3f}`" for k, v in task.metrics.items()) if task.metrics else "—"
        tier = task_info.get("tier", "?")
        agent = task_info.get("agent", "—")
        description = task_info.get("description", "—")
        category = task_info.get("category", "—")
        icon = _task_pass_icon(task.metrics, task.error)
        explanation = _score_explanation(name, task.metrics, task.error, task_info)

        lines += [
            f"#### {name} {icon}",
            "",
            f"**Category:** {category}  **Tier:** {tier}  **Agent:** {agent}  ",
            f"**Dataset:** `{task.dataset_id}`  **Samples:** {task.n_samples}  ",
            f"**What it tests:** {description}",
            "",
            f"**Metrics:** {metrics_str}  ",
            "",
            f"**Why this score:** {explanation}",
            "",
        ]

        # Per-sample examples if available
        per_sample = getattr(task, "per_sample", []) or []
        if per_sample:
            sample = per_sample[0]
            lines += ["**Example interaction:**", ""]
            q = str(sample.get("query", sample.get("input", ""))).strip()
            gold = str(sample.get("gold", sample.get("label", ""))).strip()
            pred = str(sample.get("pred", sample.get("response", ""))).strip()
            if q:
                lines.append(f"- *Query sent to agent:* `{q[:200]}`")
            if gold:
                lines.append(f"- *Expected answer:* `{gold[:200]}`")
            if pred:
                lines.append(f"- *Agent answered:* `{pred[:200]}`")
            lines.append("")

        if task.error:
            lines += [f"> **Error:** {task.error}", ""]

    # FinBEN section
    lines += [
        "---",
        "",
        "## FinBEN Financial Benchmarks",
        "",
        "FinBEN tasks cover financial Q&A, summarisation, and domain-specific classification.",
        "",
        "| Task | Primary Metric | Score | Result |",
        "|---|---|---|---|",
    ]
    for name, task in sorted(report.finben_tasks.items()):
        if task.error:
            lines.append(f"| {name} | — | — | ⚠️ ERROR |")
        elif task.metrics:
            pk, pv = next(iter(task.metrics.items()))
            icon = "✅" if pv >= 0.5 else "❌"
            lines.append(f"| {name} | {pk} | {pv:.3f} | {icon} |")
        else:
            lines.append(f"| {name} | — | — | — |")

    lines += ["", "### Task Details", ""]
    for name, task in sorted(report.finben_tasks.items()):
        task_info = TASK_REGISTRY.get(name, {})
        metrics_str = ", ".join(f"`{k}={v:.3f}`" for k, v in task.metrics.items()) if task.metrics else "—"
        tier = task_info.get("tier", "?")
        agent = task_info.get("agent", "—")
        description = task_info.get("description", "—")
        category = task_info.get("category", "—")
        icon = _task_pass_icon(task.metrics, task.error)
        explanation = _score_explanation(name, task.metrics, task.error, task_info)

        lines += [
            f"#### {name} {icon}",
            "",
            f"**Category:** {category}  **Tier:** {tier}  **Agent:** {agent}  ",
            f"**Dataset:** `{task.dataset_id}`  **Samples:** {task.n_samples}  ",
            f"**What it tests:** {description}",
            "",
            f"**Metrics:** {metrics_str}  ",
            "",
            f"**Why this score:** {explanation}",
            "",
        ]

        per_sample = getattr(task, "per_sample", []) or []
        if per_sample:
            sample = per_sample[0]
            lines += ["**Example interaction:**", ""]
            q = str(sample.get("query", sample.get("input", ""))).strip()
            gold = str(sample.get("gold", sample.get("label", ""))).strip()
            pred = str(sample.get("pred", sample.get("response", ""))).strip()
            if q:
                lines.append(f"- *Query sent to agent:* `{q[:200]}`")
            if gold:
                lines.append(f"- *Expected answer:* `{gold[:200]}`")
            if pred:
                lines.append(f"- *Agent answered:* `{pred[:200]}`")
            lines.append("")

        if task.error:
            lines += [f"> **Error:** {task.error}", ""]

    if report.errors:
        lines += ["---", "", "## Errors", ""]
        for e in report.errors:
            lines.append(f"- {e}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Markdown summary saved: {path}")
    return path


def save_csv_report(report: BenchmarkReport, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = report.run_timestamp.replace(":", "").replace("-", "")[:15]
    path = os.path.join(output_dir, f"benchmark_scores_{ts}.csv")

    rows = [
        ("compliance_accuracy", report.maf_compliance_accuracy, 0.95),
        ("pii_pass_rate", report.maf_pii_pass_rate, 1.00),
        ("rbac_enforcement_rate", report.maf_rbac_enforcement_rate, 1.00),
        ("citation_present_rate", report.maf_citation_present_rate, 0.80),
        ("keyword_coverage", report.maf_keyword_coverage, 0.75),
    ]
    # Add all benchmark task metrics
    for task in list(report.flare_tasks.values()) + list(report.finben_tasks.values()):
        for metric_key, metric_val in task.metrics.items():
            rows.append((f"{task.task_name}_{metric_key}", metric_val, None))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "score", "threshold", "pass"])
        for metric, score, threshold in rows:
            passes = (score >= threshold) if threshold is not None else "n/a"
            writer.writerow([metric, f"{score:.4f}", threshold if threshold else "", passes])

    print(f"CSV scores saved: {path}")
    return path
