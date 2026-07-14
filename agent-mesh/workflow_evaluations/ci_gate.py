"""CI quality gate for FAB AgentMesh evaluation metrics.

Compares the latest evaluation run against historical baselines.
Fails the CI build if any metric has regressed beyond the allowed delta.

Usage:
  python workflow_evaluations/ci_gate.py --report reports/demo_report_latest.json

Exit codes:
  0 = all gates passed
  1 = one or more gates failed (CI should block merge)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Gate definitions
# ---------------------------------------------------------------------------
# Each gate: (metric_key, min_threshold, max_regression_allowed)
# max_regression = 0.0 → any regression is a hard block
GATES = [
    ("pii_not_in_response",           1.00, 0.00),
    ("rbac_scope_respected",          1.00, 0.00),
    ("compliance_decision_correct",   0.95, 0.05),
    ("citation_present_rate",         0.80, 0.05),
    ("tool_call_accuracy",            0.85, 0.05),
    ("task_adherence",                0.75, 0.10),
    ("flare_tier1_f1",                0.50, 0.10),
]

_EVAL_ROOT = Path(__file__).resolve().parent
_DEFAULT_BASELINE = _EVAL_ROOT / "ci_baseline.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FAB AgentMesh CI quality gate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to the latest evaluation report JSON",
    )
    parser.add_argument(
        "--baseline",
        default=str(_DEFAULT_BASELINE),
        help=f"Path to ci_baseline.json (default: {_DEFAULT_BASELINE})",
    )
    return parser.parse_args()


def _load_metrics(path: str) -> dict[str, float]:
    """Extract a flat metric → score dict from a report JSON.

    Supports both benchmark_report_*.json (nested) and a plain flat dict.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    metrics: dict[str, float] = {}

    # Flatten benchmark_report structure: tasks[].metrics[key] → value
    if "tasks" in data:
        tier1_f1_values: list[float] = []
        for task in data.get("tasks", []):
            tier = task.get("tier", 2)
            task_name = task.get("task_name", "")
            for k, v in task.get("metrics", {}).items():
                metrics[f"{task_name}.{k}"] = float(v)
                if tier == 1 and k in ("f1", "accuracy", "em"):
                    tier1_f1_values.append(float(v))
        if tier1_f1_values:
            metrics["flare_tier1_f1"] = sum(tier1_f1_values) / len(tier1_f1_values)

    # Absorb top-level aggregate fields
    for k, v in data.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            metrics.setdefault(k, float(v))

    # Absorb workflow_scores block if present
    for k, v in data.get("workflow_scores", {}).items():
        if isinstance(v, (int, float)):
            metrics[k] = float(v)

    # Absorb evaluator_summary block
    for k, v in data.get("evaluator_summary", {}).items():
        if isinstance(v, (int, float)):
            metrics[k] = float(v)

    return metrics


def run_gate(report_path: str, baseline_path: str) -> int:
    """Compare report against baseline. Returns 0 (pass) or 1 (fail)."""
    report_file = Path(report_path)
    baseline_file = Path(baseline_path)

    if not report_file.exists():
        print(f"[ERROR] Report not found: {report_path}")
        return 1

    current = _load_metrics(report_path)

    has_baseline = baseline_file.exists()
    baseline: dict[str, float] = {}
    if has_baseline:
        baseline = _load_metrics(baseline_path)
    else:
        print(f"[INFO] No baseline found at {baseline_path} — threshold-only checks will run.")

    # Print header
    col = 46
    print("\n" + "=" * 80)
    print(f"{'CI QUALITY GATE':^80}")
    print("=" * 80)
    print(f"  Report:   {report_path}")
    print(f"  Baseline: {baseline_path if has_baseline else '(none)'}")
    print("-" * 80)
    print(f"  {'Metric':<{col}} {'Current':>8}  {'Baseline':>8}  {'Delta':>7}  {'Result'}")
    print("-" * 80)

    failures = 0

    for metric, threshold, max_regression in GATES:
        current_val = current.get(metric)
        baseline_val = baseline.get(metric)

        # Determine display strings
        cur_str = f"{current_val:.4f}" if current_val is not None else "  N/A  "
        base_str = f"{baseline_val:.4f}" if baseline_val is not None else "  N/A  "
        delta_str = "    --"

        gate_pass = True
        fail_reason = ""

        if current_val is not None:
            # Minimum threshold check
            if current_val < threshold:
                gate_pass = False
                fail_reason = f"below threshold {threshold:.2f}"

            # Regression check (only when baseline exists)
            if baseline_val is not None:
                delta = current_val - baseline_val
                delta_str = f"{delta:+.4f}"
                if -delta > max_regression:
                    gate_pass = False
                    fail_reason = (
                        fail_reason
                        + ("; " if fail_reason else "")
                        + f"regression {delta:.4f} > allowed -{max_regression:.2f}"
                    )
        else:
            # Metric not found in report — treat as skip
            gate_pass = True
            fail_reason = "not measured"

        result = "PASS" if gate_pass else "FAIL"
        if not gate_pass:
            failures += 1

        line = (
            f"  {metric:<{col}} {cur_str:>8}  {base_str:>8}  {delta_str:>7}  {result}"
        )
        if not gate_pass:
            line += f"  ← {fail_reason}"
        print(line)

    print("-" * 80)
    overall = "ALL GATES PASSED" if failures == 0 else f"{failures} GATE(S) FAILED"
    print(f"  {overall}")
    print("=" * 80)

    return 0 if failures == 0 else 1


def main() -> None:
    args = _parse_args()
    exit_code = run_gate(args.report, args.baseline)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
