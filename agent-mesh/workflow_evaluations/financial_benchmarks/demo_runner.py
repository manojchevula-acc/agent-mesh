"""Demo runner — visually rich execution of all FinBEN/FLARE tasks for presentation.

Prints per-sample rows inline as each task completes, then a final ASCII summary
table and a markdown report.

Usage:
    asyncio.run(run_demo(endpoints, dry_run=False, max_tier=1))
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
from typing import Dict, List, Optional

from financial_benchmarks.task_registry import (
    TASK_REGISTRY,
    BenchmarkTaskResult,
    run_all_tasks,
)


# ---------------------------------------------------------------------------
# Endpoint pre-flight check
# ---------------------------------------------------------------------------

def _check_endpoint(api: str) -> bool:
    """Return True if the API endpoint responds within 3 seconds."""
    import httpx
    try:
        httpx.get(f"{api}/health", timeout=3.0)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Per-task verbose printer
# ---------------------------------------------------------------------------

def _print_task_header(task_name: str, idx: int, total: int) -> None:
    info = TASK_REGISTRY[task_name]
    tier_tag = f"Tier {info['tier']}"
    width = 72
    title = f"  [{idx}/{total}]  {task_name}  ({info['agent']})  {tier_tag}  "
    print("\n" + "=" * width)
    print(title[:width])
    print(f"  Dataset : {info['dataset_id']}")
    print(f"  Category: {info['category']}")
    print(f"  Desc    : {info['description'][:65]}")
    print("-" * width)


def _print_sample_row(idx: int, sample: dict, task_type: str) -> None:
    q = sample.get("query_preview", "")[:55]
    if task_type == "mc":
        gold = sample.get("gold", "")
        pred = sample.get("pred", "")
        tick = "[OK]" if sample.get("match") else "[X]"
        print(f"  #{idx:<2}  Q: {q!r:<58}  gold={gold:<12} pred={pred:<12} {tick}")
    elif task_type in ("freeform", "sequence"):
        gold = str(sample.get("gold", ""))[:25]
        pred = str(sample.get("pred", ""))[:25]
        score_key = "em" if "em" in sample else "f1"
        score = sample.get(score_key, 0.0)
        tick = "[OK]" if score >= 0.5 else "[~]"
        print(f"  #{idx:<2}  Q: {q!r:<58}  gold={gold:<27} {score_key}={score:.2f} {tick}")
    elif task_type == "summarize":
        r1 = sample.get("rouge1", 0.0)
        print(f"  #{idx:<2}  Q: {q!r:<58}  ROUGE-1={r1:.3f}")
    elif task_type == "regression":
        gold = sample.get("gold", 0.0)
        pred = sample.get("pred", 0.0)
        err  = sample.get("error", abs(pred - gold))
        print(f"  #{idx:<2}  Q: {q!r:<58}  gold={gold:+.3f}  pred={pred:+.3f}  |err|={err:.3f}")


def _print_task_footer(result: BenchmarkTaskResult, threshold: float = 0.0) -> None:
    if result.error:
        print(f"  >> ERROR:{result.error}")
        return
    if not result.metrics:
        print(f"  >> [DRY RUN - no scores]")
        return
    metrics_str = "  ".join(f"{k}={v:.3f}" for k, v in result.metrics.items())
    primary = next(iter(result.metrics.values()), 0.0)
    status = "PASS" if primary >= threshold else "FAIL"
    print(f"  >> {metrics_str}  [{status}]")


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

_DEMO_THRESHOLDS = {
    "mc":         ("f1_weighted", 0.50),
    "freeform":   ("token_f1",    0.30),
    "sequence":   ("f1_approx",   0.20),
    "summarize":  ("rouge1",      0.20),
    "regression": ("pearson",     0.10),
}

def _print_summary_table(results: List[BenchmarkTaskResult]) -> str:
    """Print and return a markdown summary table."""
    header = (
        f"{'Task':<22} {'Agent':<20} {'Category':<22} "
        f"{'Tier':<5} {'N':<4} {'Metric':<14} {'Score':<8} {'Pass'}"
    )
    sep = "-" * len(header)
    print("\n" + "=" * 72)
    print("  DEMO SUMMARY -- All Tasks")
    print("=" * 72)
    print(header)
    print(sep)

    md_rows = []
    all_pass = True
    for r in results:
        info = TASK_REGISTRY.get(r.task_name, {})
        metric_key, threshold = _DEMO_THRESHOLDS.get(r.task_type, ("score", 0.0))
        agent    = info.get("agent", "")
        category = info.get("category", "")
        tier     = info.get("tier", "")
        is_dry   = not r.metrics and not r.error
        if is_dry:
            score_str = "  --  "
            status    = "DRY RUN"
        else:
            score = r.metrics.get(metric_key, next(iter(r.metrics.values()), 0.0) if r.metrics else 0.0)
            passed = score >= threshold and not r.error
            if not passed:
                all_pass = False
            status    = "PASS" if passed else ("ERROR" if r.error else "FAIL")
            score_str = f"{score:<8.3f}"
        row = (
            f"{r.task_name:<22} {agent:<20} {category:<22} "
            f"{tier!s:<5} {r.n_samples:<4} {metric_key:<14} {score_str:<8} {status}"
        )
        print(row)
        md_rows.append(f"| {r.task_name} | {agent} | {category} | {tier} | {r.n_samples} | {metric_key}={score_str.strip()} | {status} |")

    print(sep)
    any_dry = any(not r.metrics and not r.error for r in results)
    if any_dry:
        print("  Overall: DRY RUN (scores not computed -- re-run without --dry-run for live scores)")
    else:
        print(f"  Overall: {'ALL PASS' if all_pass else 'SOME FAILURES'}")

    md = "\n".join([
        "## Demo Results",
        "",
        "| Task | Agent | Category | Tier | N | Score | Status |",
        "|---|---|---|---|---|---|---|",
    ] + md_rows)
    return md


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _save_demo_report(results: List[BenchmarkTaskResult], output_dir: str, md_table: str) -> str:
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(output_dir, exist_ok=True)

    # JSON
    json_path = os.path.join(output_dir, f"demo_report_{ts}.json")
    payload = []
    for r in results:
        info = TASK_REGISTRY.get(r.task_name, {})
        payload.append({
            "task_name":   r.task_name,
            "dataset_id":  r.dataset_id,
            "task_type":   r.task_type,
            "agent":       info.get("agent", ""),
            "category":    info.get("category", ""),
            "tier":        info.get("tier", ""),
            "n_samples":   r.n_samples,
            "metrics":     r.metrics,
            "error":       r.error,
            "per_sample":  r.per_sample,
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Markdown
    md_path = os.path.join(output_dir, f"demo_report_{ts}.md")
    intro = (
        f"# FAB AgentMesh — FinBEN/FLARE Demo Report\n\n"
        f"Generated: {ts}\n\n"
        f"Datasets: FinBEN (36 datasets, 24 tasks, 7 categories) + FLARE\n\n"
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(intro + md_table)

    print(f"\n  Report saved -> {md_path}")
    return md_path


# ---------------------------------------------------------------------------
# Main demo entry point
# ---------------------------------------------------------------------------

async def run_demo(
    endpoints:  Dict[str, str],
    dry_run:    bool = False,
    max_tier:   int  = 1,
    sample_sizes: Optional[Dict[str, int]] = None,
    output_dir: str  = "workflow_evaluations/reports",
) -> None:
    """Run all FinBEN/FLARE benchmark tasks with per-sample verbose output.

    Args:
        endpoints:    Agent endpoint map from config.AGENT_ENDPOINTS
        dry_run:      Print dataset info only, make zero API calls
        max_tier:     1 = public datasets only; 2 = all 36 (needs HF login)
        sample_sizes: Per-task sample count overrides (default: DEMO_SAMPLE_SIZES)
        output_dir:   Where to write demo_report_{ts}.json and .md
    """
    from workflow_evaluations.config import DEMO_SAMPLE_SIZES
    sizes = sample_sizes or DEMO_SAMPLE_SIZES

    # Map task agent names to config endpoint keys
    _AGENT_KEY = {
        "RAGAgent":         "rag",
        "ComplianceAgent":  "compliance",
        "DataAgent":        "data",
        "PriceAssistAgent": "price_assist",
    }
    fallback_api = endpoints.get("api", "http://localhost:8000")

    tasks_to_run = [
        (name, info)
        for name, info in TASK_REGISTRY.items()
        if info["tier"] <= max_tier
    ]
    total = len(tasks_to_run)
    tier_label = "Tier 1 (public)" if max_tier == 1 else "All tiers (public + gated)"

    # Representative endpoint for the header and pre-flight check (use rag as default)
    display_api = endpoints.get("rag", fallback_api)
    print(f"\n{'='*72}")
    print(f"  FAB AgentMesh -- FinBEN/FLARE Demo  ({tier_label})")
    print(f"  Tasks: {total}   Dry-run: {dry_run}")
    print(f"  Endpoints: compliance={endpoints.get('compliance','?')}  data={endpoints.get('data','?')}")
    print(f"             rag={endpoints.get('rag','?')}  price_assist={endpoints.get('price_assist','?')}")
    print(f"{'='*72}")

    if not dry_run:
        unreachable = [k for k in ("compliance", "data", "rag", "price_assist") if not _check_endpoint(endpoints.get(k, ""))]
        if unreachable:
            print(f"\n  [WARNING] Agents not reachable: {', '.join(unreachable)}")
            print(f"  Start the mesh first: python launch_mesh.py\n")

    results: List[BenchmarkTaskResult] = []
    from financial_benchmarks import task_registry as tr
    runner_map = {
        "mc":         tr.run_multiple_choice_task,
        "freeform":   tr.run_freeform_task,
        "sequence":   tr.run_sequence_task,
        "summarize":  tr.run_summarization_task,
        "regression": tr.run_regression_task,
    }
    for idx, (name, info) in enumerate(tasks_to_run, 1):
        n = sizes.get(name, 5)
        _print_task_header(name, idx, total)

        agent_key  = _AGENT_KEY.get(info["agent"], "api")
        task_api   = endpoints.get(agent_key, fallback_api)
        runner     = runner_map[info["type"]]
        result = await runner(name, task_api, n_samples=n, dry_run=dry_run)

        if not dry_run and result.per_sample:
            for i, sample in enumerate(result.per_sample, 1):
                _print_sample_row(i, sample, info["type"])

        metric_key, threshold = _DEMO_THRESHOLDS.get(info["type"], ("score", 0.0))
        _print_task_footer(result, threshold)
        results.append(result)

    md_table = _print_summary_table(results)
    if not dry_run:
        _save_demo_report(results, output_dir, md_table)
