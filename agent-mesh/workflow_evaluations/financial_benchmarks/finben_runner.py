"""FinBEN financial benchmark runner for FAB AgentMesh.

Runs 5 FinBEN task categories against live FAB agents to establish model
NLP capability baselines on standard financial language tasks.

These tests use public HuggingFace datasets — they test the underlying LLM's
financial NLP performance, not FAB-specific functionality.
"""
from __future__ import annotations

import asyncio
import re
import sys
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class FinBENTaskResult:
    task_name: str
    dataset_id: str
    n_samples: int
    metrics: Dict[str, float] = field(default_factory=dict)
    per_sample: List[dict] = field(default_factory=list)
    error: Optional[str] = None


def compute_rouge_scores(predictions: List[str], references: List[str]) -> Dict[str, float]:
    """ROUGE-1, ROUGE-2, ROUGE-L using rouge-score library."""
    try:
        from rouge_score import rouge_scorer
        scorer = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
        r1_scores, r2_scores, rL_scores = [], [], []
        for pred, ref in zip(predictions, references):
            scores = scorer.score(ref, pred)
            r1_scores.append(scores["rouge1"].fmeasure)
            r2_scores.append(scores["rouge2"].fmeasure)
            rL_scores.append(scores["rougeL"].fmeasure)
        return {
            "rouge1": sum(r1_scores) / len(r1_scores),
            "rouge2": sum(r2_scores) / len(r2_scores),
            "rougeL": sum(rL_scores) / len(rL_scores),
        }
    except ImportError:
        return {"rouge1": 0.0, "rouge2": 0.0, "rougeL": 0.0,
                "error": "rouge-score not installed; run: pip install rouge-score"}


def compute_f1_score(predictions: List[str], references: List[str], average: str = "weighted") -> float:
    """Weighted F1 using sklearn."""
    from sklearn.metrics import f1_score
    return float(f1_score(references, predictions, average=average, zero_division=0))


def compute_exact_match(predictions: List[str], references: List[str]) -> float:
    """Exact match accuracy — normalise whitespace and case before comparing."""
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", str(t).strip().lower())
    matches = sum(norm(p) == norm(r) for p, r in zip(predictions, references))
    return matches / len(predictions) if predictions else 0.0


def compute_mcc(predictions: List[str], references: List[str]) -> float:
    """Matthews Correlation Coefficient using sklearn — for binary tasks."""
    from sklearn.metrics import matthews_corrcoef
    try:
        return float(matthews_corrcoef(references, predictions))
    except Exception:
        return 0.0


async def _call_agent(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    semaphore: asyncio.Semaphore,
    timeout: float = 60.0,
) -> str:
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


async def run_finben_ner(
    api_endpoint: str,
    n_samples: int = 150,
    dry_run: bool = False,
) -> FinBENTaskResult:
    """Category 1: Named Entity Recognition (TheFinAI/flare-ner).

    Metric: token-level F1. Maps to RAGAgent document analysis.
    """
    task = FinBENTaskResult(task_name="finben_ner", dataset_id="TheFinAI/flare-ner", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] finben_ner: would load {n_samples} samples from TheFinAI/flare-ner")
        return task
    try:
        from datasets import load_dataset
        ds = load_dataset("TheFinAI/flare-ner", split="test").select(range(min(n_samples, 150)))
        semaphore = asyncio.Semaphore(5)
        preds, golds = [], []
        async with httpx.AsyncClient() as client:
            coros = [
                _call_agent(client, api_endpoint,
                            f"Extract all named entities (people, companies, locations) from this financial text: {item.get('tokens', item.get('text', ''))[:300]}",
                            semaphore)
                for item in ds
            ]
            responses = await asyncio.gather(*coros)

        for item, response in zip(ds, responses):
            gold_entities = " ".join(item.get("ner_tags", [])) if isinstance(item.get("ner_tags"), list) else str(item.get("ner_tags", ""))
            preds.append(response[:200])
            golds.append(gold_entities[:200])
            # Simple token overlap as a proxy for F1 in this offline runner
            pred_tokens = set(re.findall(r"\b[A-Z][a-z]+\b", response))
            gold_tokens = set(re.findall(r"\b[A-Z][a-z]+\b", gold_entities))
            precision = len(pred_tokens & gold_tokens) / len(pred_tokens) if pred_tokens else 0.0
            recall = len(pred_tokens & gold_tokens) / len(gold_tokens) if gold_tokens else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            task.per_sample.append({"f1": f1})

        task.metrics["f1_approx"] = sum(s["f1"] for s in task.per_sample) / len(task.per_sample)
        print(f"  finben_ner: f1_approx={task.metrics['f1_approx']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  finben_ner ERROR: {exc}")
    return task


async def run_finben_fiqa_sentiment(
    api_endpoint: str,
    n_samples: int = 150,
    dry_run: bool = False,
) -> FinBENTaskResult:
    """Category 2: FiQA aspect-based sentiment (TheFinAI/fiqa-sentiment-classification).

    Metric: weighted F1. More nuanced than FPB.
    """
    task = FinBENTaskResult(task_name="finben_fiqa_sentiment", dataset_id="TheFinAI/fiqa-sentiment-classification", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] finben_fiqa_sentiment: would load {n_samples} samples from TheFinAI/fiqa-sentiment-classification")
        return task
    try:
        from datasets import load_dataset
        ds = load_dataset("TheFinAI/fiqa-sentiment-classification", split="test").select(range(min(n_samples, 150)))
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [
                _call_agent(client, api_endpoint,
                            f"Classify the financial sentiment as positive, negative, or neutral: {item.get('sentence', item.get('text', ''))[:300]}",
                            semaphore)
                for item in ds
            ]
            responses = await asyncio.gather(*coros)

        preds, golds = [], []
        for item, response in zip(ds, responses):
            gold = str(item.get("label", "neutral")).lower()
            pred_lower = response.lower()
            pred = "positive" if "positive" in pred_lower else ("negative" if "negative" in pred_lower else "neutral")
            preds.append(pred)
            golds.append(gold)
            task.per_sample.append({"gold": gold, "pred": pred})

        task.metrics["f1_weighted"] = compute_f1_score(preds, golds)
        print(f"  finben_fiqa_sentiment: f1={task.metrics['f1_weighted']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  finben_fiqa_sentiment ERROR: {exc}")
    return task


async def run_finben_ectsum(
    api_endpoint: str,
    n_samples: int = 50,
    dry_run: bool = False,
) -> FinBENTaskResult:
    """Category 3: Earnings call summarisation (TheFinAI/flare-ectsum).

    Metric: ROUGE-1/2/L. Tests synthesis quality — directly relevant to PriceAssistAgent.
    """
    task = FinBENTaskResult(task_name="finben_ectsum", dataset_id="TheFinAI/flare-ectsum", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] finben_ectsum: would load {n_samples} samples from TheFinAI/flare-ectsum")
        return task
    try:
        from datasets import load_dataset
        ds = load_dataset("TheFinAI/flare-ectsum", split="test").select(range(min(n_samples, 50)))
        semaphore = asyncio.Semaphore(3)
        preds, refs = [], []
        async with httpx.AsyncClient() as client:
            coros = [
                _call_agent(client, api_endpoint,
                            f"Summarise this earnings call transcript in 3–5 bullet points:\n{item.get('text', '')[:800]}",
                            semaphore)
                for item in ds
            ]
            responses = await asyncio.gather(*coros)

        for item, response in zip(ds, responses):
            ref = item.get("summary", item.get("label", ""))
            preds.append(response)
            refs.append(str(ref))

        task.metrics.update(compute_rouge_scores(preds, refs))
        print(f"  finben_ectsum: ROUGE-1={task.metrics.get('rouge1', 0):.3f} ROUGE-L={task.metrics.get('rougeL', 0):.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  finben_ectsum ERROR: {exc}")
    return task


async def run_finben_headlines(
    api_endpoint: str,
    n_samples: int = 200,
    dry_run: bool = False,
) -> FinBENTaskResult:
    """Category 4: Financial headline classification (TheFinAI/flare-headlines).

    Tests ComplianceAgent's financial risk classification.
    Metric: Accuracy + F1.
    """
    task = FinBENTaskResult(task_name="finben_headlines", dataset_id="TheFinAI/flare-headlines", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] finben_headlines: would load {n_samples} samples from TheFinAI/flare-headlines")
        return task
    try:
        from datasets import load_dataset
        from sklearn.metrics import accuracy_score
        ds = load_dataset("TheFinAI/flare-headlines", split="test").select(range(min(n_samples, 200)))
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [
                _call_agent(client, api_endpoint,
                            f"Is this financial headline price-sensitive (yes/no)? Headline: {item.get('text', item.get('headline', ''))[:200]}",
                            semaphore)
                for item in ds
            ]
            responses = await asyncio.gather(*coros)

        preds, golds = [], []
        for item, response in zip(ds, responses):
            gold_raw = str(item.get("label", "no")).lower()
            gold = "yes" if gold_raw in ("1", "true", "yes", "positive") else "no"
            pred = "yes" if "yes" in response.lower()[:50] else "no"
            preds.append(pred)
            golds.append(gold)
            task.per_sample.append({"gold": gold, "pred": pred})

        task.metrics["accuracy"] = accuracy_score(golds, preds)
        task.metrics["f1_weighted"] = compute_f1_score(preds, golds)
        print(f"  finben_headlines: accuracy={task.metrics['accuracy']:.3f} f1={task.metrics['f1_weighted']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  finben_headlines ERROR: {exc}")
    return task


async def run_finben_finqa(
    api_endpoint: str,
    n_samples: int = 100,
    dry_run: bool = False,
) -> FinBENTaskResult:
    """Category 5: FinBEN QA — same dataset as FLARE FinQA, different framing.

    Compares RAGAgent vs PriceAssistAgent on the same numerical reasoning questions.
    """
    task = FinBENTaskResult(task_name="finben_finqa", dataset_id="TheFinAI/flare-finqa", n_samples=n_samples)
    if dry_run:
        print(f"  [DRY RUN] finben_finqa: would load {n_samples} samples from TheFinAI/flare-finqa")
        return task
    try:
        from datasets import load_dataset
        ds = load_dataset("TheFinAI/flare-finqa", split="test").select(range(min(n_samples, 100)))
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [
                _call_agent(client, api_endpoint,
                            f"Answer this financial question precisely:\n{item.get('question', '')}",
                            semaphore)
                for item in ds
            ]
            responses = await asyncio.gather(*coros)

        preds = [r for r in responses]
        golds = [str(item.get("answer", "")) for item in ds]
        task.metrics["exact_match"] = compute_exact_match(preds, golds)
        f1_vals = [_compute_token_f1(p, g) for p, g in zip(preds, golds)]
        task.metrics["token_f1"] = sum(f1_vals) / len(f1_vals) if f1_vals else 0.0
        print(f"  finben_finqa: EM={task.metrics['exact_match']:.3f} F1={task.metrics['token_f1']:.3f}")
    except Exception as exc:
        task.error = str(exc)
        print(f"  finben_finqa ERROR: {exc}")
    return task


def _compute_token_f1(pred: str, gold: str) -> float:
    pred_tokens = set(re.sub(r"\s+", " ", pred.lower().strip()).split())
    gold_tokens = set(re.sub(r"\s+", " ", gold.lower().strip()).split())
    if not pred_tokens or not gold_tokens:
        return 0.0
    precision = len(pred_tokens & gold_tokens) / len(pred_tokens)
    recall = len(pred_tokens & gold_tokens) / len(gold_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


async def run_all_finben_tasks(
    endpoints: Dict[str, str],
    sample_sizes: Optional[Dict[str, int]] = None,
    dry_run: bool = False,
) -> List[FinBENTaskResult]:
    """Runs all FinBEN tasks via the unified task_registry (all tiers).

    Legacy per-function runners are kept for backward compat with --mode single.
    New tasks (and all 36-dataset coverage) run via task_registry.
    """
    from workflow_evaluations.config import BENCHMARK_SAMPLE_SIZES
    from financial_benchmarks.task_registry import TASK_REGISTRY, RUNNER_DISPATCH
    sizes = sample_sizes or BENCHMARK_SAMPLE_SIZES
    fallback = endpoints.get("api", "http://localhost:8000")
    _agent_key = {"RAGAgent": "rag", "ComplianceAgent": "compliance",
                  "DataAgent": "data", "PriceAssistAgent": "price_assist"}

    finben_tasks = [
        name for name, info in TASK_REGISTRY.items()
        if name.startswith("finben_") and info["tier"] == 1
    ]

    print(f"\n--- FinBEN Benchmarks ({len(finben_tasks)} tasks) ---")
    results = []
    for name in finben_tasks:
        info = TASK_REGISTRY[name]
        n    = sizes.get(name, 100)
        api  = endpoints.get(_agent_key.get(info["agent"], "api"), fallback)
        if name == "finben_fiqa":
            # The fiqa-sentiment-classification dataset uses {label: int, sentence: str}
            # schema, not the FLARE MC {choices, gold} schema. Use the legacy runner
            # which reads item["label"] and maps the response to positive/negative/neutral.
            legacy = await run_finben_fiqa_sentiment(api, n_samples=n)
            legacy.task_name = name
            r = legacy
        else:
            runner = RUNNER_DISPATCH[info["type"]]
            r = await runner(name, api, n_samples=n, dry_run=dry_run)
        if r.error:
            print(f"  {name}: ERROR — {r.error}")
        else:
            metrics_str = ", ".join(f"{k}={v:.3f}" for k, v in r.metrics.items()) or "no metrics"
            print(f"  {name}: {metrics_str}")
        # Wrap in FinBENTaskResult for backward compat with benchmark_report.py
        compat = FinBENTaskResult(
            task_name=r.task_name,
            dataset_id=r.dataset_id,
            n_samples=r.n_samples,
            metrics=r.metrics,
            per_sample=r.per_sample,
            error=r.error,
        )
        results.append(compat)
    return results
