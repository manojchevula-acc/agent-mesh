"""CLI script to run RAGAS evaluation against the ingested documents.

Usage:
    python scripts/run_evaluation.py                 # full eval (needs ground truth)
    python scripts/run_evaluation.py --reference-free # no ground truth required
"""

import argparse
import asyncio
import json
import sys

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.evaluation.evaluator import RAGEvaluator  # noqa: E402
from gernas_rag.generation.generator import ResponseGenerator  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402


async def main(reference_free: bool) -> None:
    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    llm = get_llm(settings.llm)
    pipeline = RetrievalPipeline(settings, embedder, vectordb)
    generator = ResponseGenerator(settings, llm)
    evaluator = RAGEvaluator(pipeline, generator, settings)

    results = await evaluator.run(reference_free=reference_free)
    print(json.dumps(results, indent=2))
    print("\n" + ("✅ All metrics PASS" if results["all_pass"] else "❌ Some metrics FAIL"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation over the FAB test set.")
    parser.add_argument(
        "--reference-free",
        action="store_true",
        help="Evaluate without ground truth (faithfulness, answer_relevancy, context_utilization).",
    )
    args = parser.parse_args()
    asyncio.run(main(reference_free=args.reference_free))
