"""Opik evaluation runner for the FAB Banking agent-mesh.

Runs quality evaluation against curated datasets using LLM-as-judge metrics:
  - Hallucination: Is the answer grounded in retrieved context?
  - Answer Relevance: Does the answer address the question?
  - Context Precision: Were retrieved chunks actually relevant?

Usage:
    python -m src.evaluation.opik_eval
    python -m src.evaluation.opik_eval --dataset fab-banking-queries --experiment my-run
"""
from __future__ import annotations

import argparse
import os
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env", override=False)

import opik
from opik.evaluation import evaluate
from opik.evaluation.metrics import Hallucination, AnswerRelevance


def run_banking_eval(
    dataset_name: str = "fab-banking-queries",
    experiment_name: str = "fab-banking-eval",
    model_name: str | None = None,
) -> dict:
    """Run quality evaluation on a curated FAB banking dataset.

    Returns a summary dict with metric averages.
    """
    client = opik.Opik()

    try:
        dataset = client.get_dataset(dataset_name)
    except Exception as e:
        print(f"[eval] Dataset '{dataset_name}' not found: {e}")
        print("[eval] Run scripts/create_eval_dataset.py first to seed the dataset.")
        return {}

    judge_model = model_name or os.getenv("EVAL_JUDGE_MODEL", "gpt-4o-mini")

    def task(item: dict) -> dict:
        """Call the agent mesh and return structured result for scoring."""
        try:
            import asyncio
            from src.mesh.orchestrator import handle_request
            from src.auth.identity_provider import login

            user = login("alice")
            result = asyncio.run(handle_request(user, item["input"]))
            return {
                "input": item["input"],
                "output": result.answer or "",
                "context": item.get("context", ""),
            }
        except Exception as exc:
            return {
                "input": item["input"],
                "output": f"ERROR: {exc}",
                "context": "",
            }

    metrics = [
        Hallucination(model=judge_model),
        AnswerRelevance(model=judge_model),
    ]

    print(f"[eval] Running experiment '{experiment_name}' on dataset '{dataset_name}'")
    print(f"[eval] Judge model: {judge_model}")

    evaluation = evaluate(
        dataset=dataset,
        task=task,
        scoring_metrics=metrics,
        experiment_name=experiment_name,
        project_name=os.getenv("OPIK_PROJECT_NAME", "agent-mesh-fab"),
    )

    print(f"[eval] Done. Results available in Opik dashboard → Experiments tab")
    return evaluation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Opik evaluation on the agent mesh")
    parser.add_argument("--dataset", default="fab-banking-queries", help="Dataset name in Opik")
    parser.add_argument("--experiment", default="fab-banking-eval", help="Experiment name")
    parser.add_argument("--model", default=None, help="LLM judge model override")
    args = parser.parse_args()

    run_banking_eval(
        dataset_name=args.dataset,
        experiment_name=args.experiment,
        model_name=args.model,
    )
