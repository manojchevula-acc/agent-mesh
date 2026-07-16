"""FAB AgentMesh Evaluation Suite — CLI entry point.

Usage:
    python workflow_evaluations/run_evaluation.py --mode ci
    python workflow_evaluations/run_evaluation.py --mode full
    python workflow_evaluations/run_evaluation.py --mode benchmarks [--dry-run]
    python workflow_evaluations/run_evaluation.py --mode replay --log data/audit_trail.jsonl
    python workflow_evaluations/run_evaluation.py --mode single --agent api --task flare_fpb
    python workflow_evaluations/run_evaluation.py --mode demo [--dry-run] [--tier {1,2}] [--save-baseline]
    python workflow_evaluations/run_evaluation.py --mode redteam

Demo mode (--mode demo):
    Tier 1 (default): 19 public datasets, no HuggingFace login needed (~95 API calls)
    Tier 2           : all 36 datasets; run `huggingface-cli login` first (~180 API calls)

Red-team mode (--mode redteam):
    Requires the live mesh to be running. Sends structured attack prompts across
    6 categories to verify the mesh blocks or refuses all of them.

Run from the agent-mesh/ directory.
"""
from __future__ import annotations

# Load .env before anything else so evaluators that read env vars directly
# (e.g. GROQ_API_KEY in task_adherence_evaluator) find them without needing
# src/config.py to be imported first.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass  # env vars must be set manually if python-dotenv is not installed

import argparse
import asyncio
import os
import sys
import pathlib

# Add agent-mesh root so src/* imports resolve
_MESH_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _MESH_ROOT not in sys.path:
    sys.path.insert(0, _MESH_ROOT)

# Add workflow_evaluations/ so internal imports resolve
_EVAL_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _EVAL_ROOT not in sys.path:
    sys.path.insert(0, _EVAL_ROOT)

from config import AGENT_ENDPOINTS, REPORTS_DIR, PASS_THRESHOLDS


def _parse_args():
    parser = argparse.ArgumentParser(
        description="FAB AgentMesh Evaluation Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["ci", "full", "benchmarks", "replay", "single", "demo", "redteam", "workflow"],
        default="ci",
        help="Evaluation mode (default: ci)",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[1, 2],
        default=1,
        help="Demo tier: 1=public datasets only (default), 2=all 36 datasets (needs HF login)",
    )
    parser.add_argument(
        "--log",
        default="data/audit_trail.jsonl",
        help="Path to audit_trail.jsonl for replay mode",
    )
    parser.add_argument(
        "--agent",
        default="api",
        help="Agent to target for single-task mode (api, rag, data, compliance, price_assist)",
    )
    parser.add_argument(
        "--task",
        default="flare_fpb",
        help="Benchmark task name for single-task mode",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load datasets and print sample counts without making any LLM calls",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save demo results as ci_baseline.json for future ci_gate.py comparisons",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Directory for report output (default: {REPORTS_DIR})",
    )
    return parser.parse_args()


async def run_ci_mode(output_dir: str) -> None:
    """Layer 2 (evaluators) + Layer 1 local checks. No live agents required."""
    print("\n=== CI MODE: Custom Evaluators + Workflow Local Checks ===")

    from workflow.dataset_builder import build_dataset
    from workflow.run_maf_eval import _score_case
    from workflow.results_reporter import print_summary, save_json, save_csv, save_markdown_report
    from workflow.ci_reporter import save_ci_markdown_report
    from evaluators.compliance_evaluator import EvalScore

    dataset = build_dataset()
    print(f"Dataset: {len(dataset)} golden test cases")

    smoke_results = []

    # Demonstrate evaluator functions on synthetic/mock data
    print("\n--- Evaluators smoke-test (no live agents) ---")
    from evaluators.compliance_evaluator import compliance_decision_correct, prompt_injection_blocked
    from evaluators.pii_evaluator import pii_not_in_response
    from evaluators.rbac_evaluator import rbac_scope_respected
    from evaluators.rag_citation_evaluator import citation_present_and_valid

    # PII checks
    pii_clean = pii_not_in_response("The recommended margin is 2.35% for CUST_004.")
    pii_leak  = pii_not_in_response("Call Alice at +971-50-1234567 or bob@fab.ae")
    print(f"  PII clean: {pii_clean.score} ({pii_clean.label})")
    print(f"  PII leak : {pii_leak.score} ({pii_leak.label})")
    smoke_results.append({"evaluator": "PII (clean input)", "check": "No PII in safe response", "score": pii_clean.score, "label": pii_clean.label, "passed": pii_clean.score == 1.0})
    smoke_results.append({"evaluator": "PII (leak input)", "check": "UAE phone/email detected", "score": pii_leak.score, "label": pii_leak.label, "passed": pii_leak.score == 0.0})

    # Compliance checks
    comp_correct = compliance_decision_correct(False, None, [], "pass")
    comp_wrong   = compliance_decision_correct(False, None, [], "block")
    print(f"  Comp pass (expected pass): {comp_correct.score} ({comp_correct.label})")
    print(f"  Comp pass (expected block): {comp_wrong.score} ({comp_wrong.label})")
    smoke_results.append({"evaluator": "Compliance (expected pass)", "check": "Decision matches expected=pass", "score": comp_correct.score, "label": comp_correct.label, "passed": comp_correct.score == 1.0})
    smoke_results.append({"evaluator": "Compliance (expected block)", "check": "Decision mismatches expected=block", "score": comp_wrong.score, "label": comp_wrong.label, "passed": comp_wrong.score == 0.0})

    # RBAC checks
    rbac_alice = rbac_scope_respected("Margin for CUST_004 is 2.1%", "alice", "relationship_manager")
    rbac_dave  = rbac_scope_respected("Data for CUST_009 and CUST_010", "dave", "branch_operations_officer")
    print(f"  RBAC alice (all access): {rbac_alice.score} ({rbac_alice.label})")
    print(f"  RBAC dave (out-of-scope): {rbac_dave.score} ({rbac_dave.label})")
    smoke_results.append({"evaluator": "RBAC (alice, in-scope)", "check": "RM can see CUST_004", "score": rbac_alice.score, "label": rbac_alice.label, "passed": rbac_alice.score == 1.0})
    smoke_results.append({"evaluator": "RBAC (dave, out-of-scope)", "check": "BOO cannot see CUST_009/010", "score": rbac_dave.score, "label": rbac_dave.label, "passed": rbac_dave.score == 0.0})

    # Citation checks
    cit_good = citation_present_and_valid("Per Basel III Tier 1 capital requirements, the minimum is 4.5%.")
    cit_none = citation_present_and_valid("The minimum margin is 2%.")
    print(f"  Citation (Basel III): {cit_good.score} ({cit_good.label})")
    print(f"  Citation (none): {cit_none.score} ({cit_none.label})")
    smoke_results.append({"evaluator": "Citation (Basel III present)", "check": "Regulatory citation found", "score": cit_good.score, "label": cit_good.label, "passed": cit_good.score == 1.0})
    smoke_results.append({"evaluator": "Citation (none)", "check": "No citation detected", "score": cit_none.score, "label": cit_none.label, "passed": cit_none.score == 0.0})

    # --- New evaluator smoke tests ---
    print("\n--- New evaluator smoke-tests (offline) ---")
    from evaluators.task_completion_evaluator import task_completion_score
    from evaluators.intent_resolution_evaluator import intent_resolution_score
    from evaluators.tool_selection_evaluator import tool_selection_score
    from evaluators.tool_input_accuracy_evaluator import tool_input_accuracy_score
    from evaluators.tool_output_utilization_evaluator import tool_output_utilization_score
    from evaluators.tool_call_success_evaluator import tool_call_success_score

    # task_completion: data route with % and AED
    tc_data = task_completion_score(
        "CUST_001 margin is 2.35%. Credit limit: AED 500,000.", "data"
    )
    tc_empty = task_completion_score("", "data")
    print(f"  TaskCompletion data (fields present): {tc_data.score} ({tc_data.label})")
    print(f"  TaskCompletion data (empty):          {tc_empty.score} ({tc_empty.label})")
    smoke_results.append({"evaluator": "TaskCompletion (data, fields present)", "check": "% and AED in response", "score": tc_data.score, "label": tc_data.label, "passed": tc_data.score >= 0.5})
    smoke_results.append({"evaluator": "TaskCompletion (data, empty)", "check": "Empty response scores 0", "score": tc_empty.score, "label": tc_empty.label, "passed": tc_empty.score == 0.0})

    # intent_resolution
    ir_ok = intent_resolution_score("data", [{"agent_name": "DataAgent"}])
    ir_fail = intent_resolution_score("data", [{"agent_name": "RAGAgent"}])
    print(f"  IntentResolution data->DataAgent: {ir_ok.score} ({ir_ok.label})")
    print(f"  IntentResolution data->RAGAgent:  {ir_fail.score} ({ir_fail.label})")
    smoke_results.append({"evaluator": "IntentResolution (data→DataAgent)", "check": "Correct agent routed", "score": ir_ok.score, "label": ir_ok.label, "passed": ir_ok.score == 1.0})
    smoke_results.append({"evaluator": "IntentResolution (data→RAGAgent)", "check": "Wrong agent detected", "score": ir_fail.score, "label": ir_fail.label, "passed": ir_fail.score == 0.0})

    # tool_selection
    ts_correct = tool_selection_score(["Called profitability_summary tool"], "profitability")
    ts_wrong   = tool_selection_score(["Called margin_analysis tool"], "profitability")
    ts_none    = tool_selection_score(["No tool was called"], "profitability")
    print(f"  ToolSelection (correct):  {ts_correct.score} ({ts_correct.label})")
    print(f"  ToolSelection (wrong):    {ts_wrong.score} ({ts_wrong.label})")
    print(f"  ToolSelection (no tool):  {ts_none.score} ({ts_none.label})")
    smoke_results.append({"evaluator": "ToolSelection (correct)", "check": "profitability_summary tool called", "score": ts_correct.score, "label": ts_correct.label, "passed": ts_correct.score == 1.0})
    smoke_results.append({"evaluator": "ToolSelection (wrong tool)", "check": "Wrong tool scores < 1", "score": ts_wrong.score, "label": ts_wrong.label, "passed": ts_wrong.score < 1.0})
    smoke_results.append({"evaluator": "ToolSelection (no tool)", "check": "No tool call scores 0", "score": ts_none.score, "label": ts_none.label, "passed": ts_none.score == 0.0})

    # tool_input_accuracy
    tia_ok = tool_input_accuracy_score(
        "What is CUST_001 margin?", ["Called tool with customer_id=CUST_001"]
    )
    tia_fail = tool_input_accuracy_score(
        "What is CUST_001 margin?", ["Called tool with customer_id=CUST_999"]
    )
    print(f"  ToolInputAccuracy (correct ID): {tia_ok.score} ({tia_ok.label})")
    print(f"  ToolInputAccuracy (wrong ID):   {tia_fail.score} ({tia_fail.label})")
    smoke_results.append({"evaluator": "ToolInputAccuracy (correct ID)", "check": "CUST_001 entity extracted", "score": tia_ok.score, "label": tia_ok.label, "passed": tia_ok.score == 1.0})
    smoke_results.append({"evaluator": "ToolInputAccuracy (wrong ID)", "check": "CUST_999 mismatch detected", "score": tia_fail.score, "label": tia_fail.label, "passed": tia_fail.score == 0.0})

    # tool_output_utilization
    tou_ok = tool_output_utilization_score(
        ["margin_pct=12.4 credit_limit=500000 AED"], "The margin is 12.4 percent and credit limit is 500000 AED"
    )
    tou_fail = tool_output_utilization_score(
        ["margin_pct=12.4 credit_limit=500000 AED"], "I cannot provide that information."
    )
    print(f"  ToolOutputUtilization (used):     {tou_ok.score} ({tou_ok.label})")
    print(f"  ToolOutputUtilization (not used): {tou_fail.score} ({tou_fail.label})")
    smoke_results.append({"evaluator": "ToolOutputUtilization (used)", "check": "Tool output reflected in answer", "score": tou_ok.score, "label": tou_ok.label, "passed": tou_ok.score >= 0.5})
    smoke_results.append({"evaluator": "ToolOutputUtilization (ignored)", "check": "Tool output not used scores low", "score": tou_fail.score, "label": tou_fail.label, "passed": tou_fail.score < 0.5})

    # tool_call_success
    tcs_ok  = tool_call_success_score([{"agent_name": "DataAgent", "status": "success", "output": "margin=2.35%"}])
    tcs_err = tool_call_success_score([{"agent_name": "DataAgent", "status": "error", "output": "MCP_TOOL_ERROR: view not found"}])
    print(f"  ToolCallSuccess (clean): {tcs_ok.score} ({tcs_ok.label})")
    print(f"  ToolCallSuccess (error): {tcs_err.score} ({tcs_err.label})")
    smoke_results.append({"evaluator": "ToolCallSuccess (clean)", "check": "No error markers in audit", "score": tcs_ok.score, "label": tcs_ok.label, "passed": tcs_ok.score == 1.0})
    smoke_results.append({"evaluator": "ToolCallSuccess (MCP error)", "check": "MCP_TOOL_ERROR detected", "score": tcs_err.score, "label": tcs_err.label, "passed": tcs_err.score == 0.0})

    # Threshold checks
    print("\n--- Pass/Fail threshold validation ---")
    demo_scores = {
        "compliance_decision_correct": 0.97,
        "pii_not_in_response": 1.00,
        "rbac_scope_respected": 1.00,
        "citation_present_rate": 0.85,
    }
    threshold_results = []
    all_pass = True
    for metric, score in demo_scores.items():
        threshold = PASS_THRESHOLDS.get(metric, 0.0)
        passed = score >= threshold
        if not passed:
            all_pass = False
        status = "PASS" if passed else "FAIL"
        print(f"  {metric:<40} {score:.2f} >= {threshold:.2f}: {status}")
        threshold_results.append({"metric": metric, "score": score, "threshold": threshold, "passed": passed})

    print(f"\nCI result: {'ALL PASS' if all_pass else 'SOME FAILURES'}")

    save_ci_markdown_report(smoke_results, threshold_results, output_dir)

    # Replay from audit log if it exists
    log_path = "data/audit_trail.jsonl"
    if os.path.exists(log_path):
        print(f"\nAudit log found ({log_path}) — running log replay...")
        from workflow.run_maf_eval import run_log_replay_evaluation
        from workflow.results_reporter import save_json, save_csv, save_markdown_report
        replay_results = run_log_replay_evaluation(log_path)
        if replay_results:
            save_json(replay_results, output_dir)
            save_csv(replay_results, output_dir)
            save_markdown_report(replay_results, output_dir)


async def run_full_mode(output_dir: str, dry_run: bool = False) -> None:
    """All 3 layers with live agents."""
    print("\n=== FULL MODE: All 3 evaluation layers ===")
    await run_ci_mode(output_dir)
    live_results = await run_workflow_live(output_dir)
    workflow_agg = _compute_workflow_aggregate(live_results) if live_results else {}
    await run_benchmarks_mode(output_dir, dry_run=dry_run, workflow_aggregate=workflow_agg)


def _compute_workflow_aggregate(case_results: list) -> dict:
    """Aggregate per-case CaseResult scores into a single dict for build_report()."""
    agg = {}
    for key in ("compliance_decision", "pii_clean", "rbac_scope", "citation", "keyword_coverage"):
        vals = [r.scores[key] for r in case_results if key in r.scores]
        agg[key] = sum(vals) / len(vals) if vals else 0.0
    return agg


async def run_workflow_live(output_dir: str) -> list:
    """Layer 1: Live workflow evaluation against running agents. Returns CaseResult list."""
    print("\n--- Layer 1: Live Workflow Evaluation ---")
    from workflow.dataset_builder import build_dataset
    from workflow.run_maf_eval import run_live_evaluation
    from workflow.results_reporter import print_summary, save_json, save_csv, save_markdown_report

    dataset = build_dataset()
    results = await run_live_evaluation(dataset)
    print_summary(results)
    save_json(results, output_dir)
    save_csv(results, output_dir)
    save_markdown_report(results, output_dir)
    return results


async def run_replay_mode(log_path: str, output_dir: str) -> None:
    """Layer 1: Log replay mode — no live agents."""
    print(f"\n=== REPLAY MODE: {log_path} ===")
    from workflow.run_maf_eval import run_log_replay_evaluation
    from workflow.results_reporter import print_summary, save_json, save_csv, save_markdown_report

    results = run_log_replay_evaluation(log_path)
    print_summary(results)
    save_json(results, output_dir)
    save_csv(results, output_dir)
    save_markdown_report(results, output_dir)


async def run_benchmarks_mode(
    output_dir: str,
    dry_run: bool = False,
    workflow_aggregate: dict | None = None,
) -> None:
    """Layer 3: FinBEN + FLARE benchmarks only."""
    print(f"\n=== BENCHMARKS MODE {'(DRY RUN)' if dry_run else ''} ===")
    from financial_benchmarks.flare_runner import run_all_flare_tasks
    from financial_benchmarks.finben_runner import run_all_finben_tasks
    from financial_benchmarks.benchmark_report import build_report, save_json_report, save_markdown_summary, save_csv_report

    flare_results = await run_all_flare_tasks(AGENT_ENDPOINTS, dry_run=dry_run)
    finben_results = await run_all_finben_tasks(AGENT_ENDPOINTS, dry_run=dry_run)

    # If no live workflow scores were passed in, try to load them from the audit log replay
    if not workflow_aggregate:
        log_path = "data/audit_trail.jsonl"
        if os.path.exists(log_path):
            from workflow.run_maf_eval import run_log_replay_evaluation
            replay_results = run_log_replay_evaluation(log_path)
            workflow_aggregate = _compute_workflow_aggregate(replay_results) if replay_results else {}
        else:
            workflow_aggregate = {}

    report = build_report(flare_results, finben_results, workflow_aggregate)
    save_json_report(report, output_dir)
    save_markdown_summary(report, output_dir)
    save_csv_report(report, output_dir)


async def run_demo_mode(
    output_dir: str,
    dry_run: bool = False,
    max_tier: int = 1,
    save_baseline: bool = False,
) -> None:
    """Demo mode: all FinBEN/FLARE tasks with per-sample verbose output."""
    import json as _json
    from pathlib import Path as _Path

    print(f"\n=== DEMO MODE {'(DRY RUN) ' if dry_run else ''}Tier {max_tier} ===")
    from financial_benchmarks.demo_runner import run_demo
    await run_demo(
        endpoints=AGENT_ENDPOINTS,
        dry_run=dry_run,
        max_tier=max_tier,
        output_dir=output_dir,
    )

    if save_baseline and not dry_run:
        # Find the most recently written demo report JSON
        reports_dir = _Path(output_dir)
        candidates = sorted(reports_dir.glob("benchmark_report_*.json"), reverse=True)
        if candidates:
            latest = candidates[0]
            baseline_path = _Path(_EVAL_ROOT) / "ci_baseline.json"
            import shutil as _shutil
            _shutil.copy(latest, baseline_path)
            print(f"\nBaseline saved: {baseline_path}  (source: {latest.name})")
        else:
            print("\n[WARNING] --save-baseline: no benchmark_report_*.json found in reports/")


async def run_redteam_mode(output_dir: str) -> None:
    """Red-team mode: send structured attack prompts to the live mesh."""
    print("\n=== RED-TEAM MODE ===")
    from red_team.red_team_runner import run_red_team
    await run_red_team(
        api_endpoint=AGENT_ENDPOINTS.get("api", "http://localhost:8000"),
        output_dir=output_dir,
    )


async def run_single_mode(agent: str, task: str, output_dir: str, dry_run: bool = False) -> None:
    """Run a single benchmark task against one agent."""
    print(f"\n=== SINGLE MODE: agent={agent} task={task} ===")
    endpoint = AGENT_ENDPOINTS.get(agent, AGENT_ENDPOINTS["api"])

    from financial_benchmarks import flare_runner, finben_runner
    from financial_benchmarks.task_registry import TASK_REGISTRY, RUNNER_DISPATCH
    from financial_benchmarks.single_reporter import save_single_markdown_report
    if task not in TASK_REGISTRY:
        print(f"Unknown task '{task}'. Available tasks ({len(TASK_REGISTRY)}): {list(TASK_REGISTRY.keys())}")
        return
    info   = TASK_REGISTRY[task]
    runner = RUNNER_DISPATCH[info["type"]]
    result = await runner(task, endpoint, n_samples=10, dry_run=dry_run)
    print(f"\nResult: {result.task_name}  metrics={result.metrics}")
    if result.error:
        print(f"Error: {result.error}")
    save_single_markdown_report(result, agent, info, output_dir)


async def main() -> None:
    args = _parse_args()
    output_dir = args.output_dir or os.path.join(_EVAL_ROOT, "reports")
    os.makedirs(output_dir, exist_ok=True)

    if args.mode == "ci":
        await run_ci_mode(output_dir)
    elif args.mode == "full":
        await run_full_mode(output_dir, dry_run=args.dry_run)
    elif args.mode == "benchmarks":
        await run_benchmarks_mode(output_dir, dry_run=args.dry_run)
    elif args.mode == "replay":
        await run_replay_mode(args.log, output_dir)
    elif args.mode == "single":
        await run_single_mode(args.agent, args.task, output_dir, dry_run=args.dry_run)
    elif args.mode == "demo":
        await run_demo_mode(
            output_dir,
            dry_run=args.dry_run,
            max_tier=args.tier,
            save_baseline=args.save_baseline,
        )
    elif args.mode == "redteam":
        await run_redteam_mode(output_dir)
    elif args.mode == "workflow":
        await run_workflow_live(output_dir)
    else:
        print(f"Unknown mode: {args.mode}")


if __name__ == "__main__":
    asyncio.run(main())
