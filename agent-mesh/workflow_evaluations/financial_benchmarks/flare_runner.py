"""FLARE financial benchmark runner for FAB AgentMesh.

Runs 4 FLARE tasks against live FAB agents to establish model capability baselines.
These tests use public HuggingFace financial datasets — they test the underlying LLM's
financial reasoning, not FAB-specific functionality. Scores will be lower than on
FAB-specific data (models are not fine-tuned on FAB domain).

Usage:
    asyncio.run(run_all_flare_tasks(endpoints=AGENT_ENDPOINTS, dry_run=True))
"""
from __future__ import annotations

import asyncio
import re
import sys
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class FLARETaskResult:
    task_name: str
    dataset_id: str
    n_samples: int
    metrics: Dict[str, float] = field(default_factory=dict)
    per_sample: List[dict] = field(default_factory=list)
    error: Optional[str] = None


def _check_hf_auth() -> bool:
    try:
        from huggingface_hub import whoami
        whoami()
        return True
    except Exception:
        return False


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _exact_match(pred: str, gold: str) -> float:
    return 1.0 if _normalise(pred) == _normalise(gold) else 0.0


def _f1_tokens(pred: str, gold: str) -> float:
    pred_tokens = set(_normalise(pred).split())
    gold_tokens = set(_normalise(gold).split())
    if not pred_tokens or not gold_tokens:
        return 0.0
    precision = len(pred_tokens & gold_tokens) / len(pred_tokens)
    recall = len(pred_tokens & gold_tokens) / len(gold_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


async def _call_agent(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    semaphore: asyncio.Semaphore,
    timeout: float = 60.0,
) -> str:
    """Calls the FAB API endpoint with a query, returns answer text."""
    async with semaphore:
        try:
            resp = await client.post(
                f"{endpoint}/api/query",
                json={"query": query, "username": "bob"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("response") or str(data)
        except Exception as exc:
            return f"ERROR: {exc}"


async def _check_agent_health(endpoint: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{endpoint}/health", timeout=5.0)
            return resp.status_code == 200
    except Exception:
        return False


async def run_flare_fpb(
    api_endpoint: str,
    n_samples: int = 200,
    dry_run: bool = False,
) -> FLARETaskResult:
    """Task 1: FPB sentiment analysis (TheFinAI/en-fpb).

    Maps to RAGAgent: classify financial statement sentiment.
    Metric: weighted F1 + accuracy.
    """
    task = FLARETaskResult(task_name="flare_fpb", dataset_id="TheFinAI/en-fpb", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] flare_fpb: would load {n_samples} samples from TheFinAI/en-fpb")
        return task
    try:
        from datasets import load_dataset
        from sklearn.metrics import f1_score, accuracy_score

        if not _check_hf_auth():
            raise RuntimeError("HuggingFace auth required. Run: huggingface-cli login")

        ds = load_dataset("TheFinAI/en-fpb", split="test").select(range(min(n_samples, 200)))
        label_map = {"positive": "positive", "negative": "negative", "neutral": "neutral"}

        semaphore = asyncio.Semaphore(5)
        preds, golds = [], []
        async with httpx.AsyncClient() as client:
            tasks_coros = []
            for item in ds:
                text = item.get("sentence", item.get("text", ""))
                prompt = f"Classify the sentiment of this financial statement as positive, negative, or neutral: {text}"
                tasks_coros.append(_call_agent(client, api_endpoint, prompt, semaphore))

            responses = await asyncio.gather(*tasks_coros)

        for item, response in zip(ds, responses):
            gold = label_map.get(str(item.get("label", "")).lower(), "neutral")
            pred_lower = response.lower()
            if "positive" in pred_lower:
                pred = "positive"
            elif "negative" in pred_lower:
                pred = "negative"
            else:
                pred = "neutral"
            preds.append(pred)
            golds.append(gold)
            task.per_sample.append({"gold": gold, "pred": pred, "match": gold == pred})

        task.metrics["accuracy"] = accuracy_score(golds, preds)
        task.metrics["f1_weighted"] = f1_score(golds, preds, average="weighted", zero_division=0)
        print(f"  flare_fpb: accuracy={task.metrics['accuracy']:.3f} f1={task.metrics['f1_weighted']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  flare_fpb ERROR: {exc}")
    return task


async def run_flare_finqa(
    api_endpoint: str,
    n_samples: int = 100,
    dry_run: bool = False,
) -> FLARETaskResult:
    """Task 2: FinQA numerical reasoning (TheFinAI/flare-finqa).

    Maps to PriceAssistAgent. Metric: Exact Match accuracy.
    Note: EM will be low — this tests model numerical reasoning capability baseline.
    """
    task = FLARETaskResult(task_name="flare_finqa", dataset_id="TheFinAI/flare-finqa", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] flare_finqa: would load {n_samples} samples from TheFinAI/flare-finqa")
        return task
    try:
        from datasets import load_dataset

        ds = load_dataset("TheFinAI/flare-finqa", split="test").select(range(min(n_samples, 100)))
        semaphore = asyncio.Semaphore(5)
        preds, golds = [], []
        async with httpx.AsyncClient() as client:
            coros = []
            for item in ds:
                question = item.get("question", "")
                context = item.get("context", "")[:500]
                prompt = f"Based on this financial context, answer the question.\nContext: {context}\nQuestion: {question}"
                coros.append(_call_agent(client, api_endpoint, prompt, semaphore))
            responses = await asyncio.gather(*coros)

        for item, response in zip(ds, responses):
            gold = str(item.get("answer", ""))
            em = _exact_match(response, gold)
            preds.append(response)
            golds.append(gold)
            task.per_sample.append({"gold": gold, "pred": response[:100], "em": em})

        em_scores = [s["em"] for s in task.per_sample]
        task.metrics["exact_match"] = sum(em_scores) / len(em_scores) if em_scores else 0.0
        f1_scores = [_f1_tokens(p, g) for p, g in zip(preds, golds)]
        task.metrics["token_f1"] = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
        print(f"  flare_finqa: EM={task.metrics['exact_match']:.3f} F1={task.metrics['token_f1']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  flare_finqa ERROR: {exc}")
    return task


async def run_flare_convfinqa(
    api_endpoint: str,
    n_samples: int = 50,
    dry_run: bool = False,
) -> FLARETaskResult:
    """Task 3: ConvFinQA multi-turn QA (ChanceFocus/flare-convfinqa).

    Tests multi-turn memory + RAGAgent integration.
    Metric: Exact Match accuracy per turn.
    """
    task = FLARETaskResult(task_name="flare_convfinqa", dataset_id="ChanceFocus/flare-convfinqa", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] flare_convfinqa: would load {n_samples} conversations from ChanceFocus/flare-convfinqa")
        return task
    try:
        from datasets import load_dataset

        ds = load_dataset("ChanceFocus/flare-convfinqa", split="test").select(range(min(n_samples, 50)))
        semaphore = asyncio.Semaphore(3)
        em_scores = []
        async with httpx.AsyncClient() as client:
            for item in ds:
                questions = item.get("questions", [])
                answers = item.get("answers", [])
                context = item.get("context", "")[:500]
                session_scores = []
                for q, gold in zip(questions, answers):
                    prompt = f"Financial context: {context}\nQuestion: {q}"
                    response = await _call_agent(client, api_endpoint, prompt, semaphore)
                    em = _exact_match(response, str(gold))
                    session_scores.append(em)
                    task.per_sample.append({"question": q[:80], "gold": str(gold), "em": em})
                if session_scores:
                    em_scores.extend(session_scores)

        task.metrics["exact_match_per_turn"] = sum(em_scores) / len(em_scores) if em_scores else 0.0
        print(f"  flare_convfinqa: EM/turn={task.metrics['exact_match_per_turn']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  flare_convfinqa ERROR: {exc}")
    return task


async def run_flare_bigdata22(
    api_endpoint: str,
    n_samples: int = 100,
    dry_run: bool = False,
) -> FLARETaskResult:
    """Task 4: Stock movement prediction (TheFinAI/flare-sm-bigdata).

    NOTE: Low relevance to FAB core use case — included for model capability baseline.
    Metric: Accuracy + MCC.
    """
    task = FLARETaskResult(task_name="flare_bigdata22", dataset_id="TheFinAI/flare-sm-bigdata", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] flare_bigdata22: would load {n_samples} samples from TheFinAI/flare-sm-bigdata")
        return task
    try:
        from datasets import load_dataset
        from sklearn.metrics import accuracy_score, matthews_corrcoef

        ds = load_dataset("TheFinAI/flare-sm-bigdata", split="test").select(range(min(n_samples, 100)))
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [
                _call_agent(client, api_endpoint,
                            f"Based on this financial news, will the stock price rise or fall? Answer with 'rise' or 'fall' only.\nNews: {item.get('text', item.get('headline', ''))}",
                            semaphore)
                for item in ds
            ]
            responses = await asyncio.gather(*coros)

        preds, golds = [], []
        for item, response in zip(ds, responses):
            gold_label = str(item.get("label", "")).lower()
            gold = 1 if "rise" in gold_label or gold_label == "1" else 0
            pred = 1 if "rise" in response.lower() else 0
            preds.append(pred)
            golds.append(gold)
            task.per_sample.append({"gold": gold_label, "pred": "rise" if pred == 1 else "fall"})

        task.metrics["accuracy"] = accuracy_score(golds, preds)
        task.metrics["mcc"] = float(matthews_corrcoef(golds, preds))
        print(f"  flare_bigdata22: accuracy={task.metrics['accuracy']:.3f} MCC={task.metrics['mcc']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  flare_bigdata22 ERROR: {exc}")
    return task


async def run_all_flare_tasks(
    endpoints: Dict[str, str],
    sample_sizes: Optional[Dict[str, int]] = None,
    dry_run: bool = False,
) -> List[FLARETaskResult]:
    """Runs all FLARE tasks via the unified task_registry (Tier 1 public datasets).

    Legacy per-function runners (run_flare_fpb, run_flare_finqa, etc.) are kept
    for backward compatibility with --mode single. New tasks run via task_registry.
    """
    from workflow_evaluations.config import BENCHMARK_SAMPLE_SIZES
    from financial_benchmarks.task_registry import TASK_REGISTRY, RUNNER_DISPATCH
    sizes = sample_sizes or BENCHMARK_SAMPLE_SIZES
    api_url = endpoints.get("api", "http://127.0.0.1:8000")

    flare_tasks = [
        name for name, info in TASK_REGISTRY.items()
        if name.startswith("flare_") and info["tier"] == 1
    ]

    print(f"\n--- FLARE Benchmarks ({len(flare_tasks)} tasks) ---")
    results = []
    for name in flare_tasks:
        info   = TASK_REGISTRY[name]
        n      = sizes.get(name, 100)
        runner = RUNNER_DISPATCH[info["type"]]
        api    = api_url
        r      = await runner(name, api, n_samples=n, dry_run=dry_run)
        if r.error:
            print(f"  {name}: ERROR — {r.error}")
        else:
            metrics_str = ", ".join(f"{k}={v:.3f}" for k, v in r.metrics.items()) or "no metrics"
            print(f"  {name}: {metrics_str}")
        # Wrap in FLARETaskResult for backward compat with benchmark_report.py
        compat = FLARETaskResult(
            task_name=r.task_name,
            dataset_id=r.dataset_id,
            n_samples=r.n_samples,
            metrics=r.metrics,
            per_sample=r.per_sample,
            error=r.error,
        )
        results.append(compat)
    return results
