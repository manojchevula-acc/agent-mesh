"""Run the live RAG pipeline over the gold set and record what the agent produced.

This is the EXPENSIVE step (embedder load, hybrid retrieval, LLM generation per
question) — run it once whenever the corpus, retrieval config, or generation
prompt changes. Its output is a plain record of what the agent did; no
judging/scoring happens here. To re-grade those recorded answers (e.g. after
tweaking the judge prompt) use scripts/compare_with_judge.py, which reads this
file's output and never touches the pipeline again.

Gold questions are numbered (see the "id" field in data/eval/gold_qa.json, e.g.
"1", "2", "13"). Use --id to target specific ones instead of the whole set.

Usage:
    python scripts/run_agent_on_gold.py
    python scripts/run_agent_on_gold.py --gold data/eval/gold_qa.json --top-k 5
    python scripts/run_agent_on_gold.py --limit 3       # quick smoke test (first N)
    python scripts/run_agent_on_gold.py --id 13,14,15   # only these ids (comma-separated)
    python scripts/run_agent_on_gold.py --id 13 --id 14 # or repeat the flag — same effect
        # Results for every other id already present in --output are left
        # untouched — this does NOT re-run the full set.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.generation.generator import ResponseGenerator  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402
from gernas_rag.models.retrieval import RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402


async def run(gold_path: Path, top_k: int, limit: int | None, ids: list[str] | None) -> list[dict]:
    gold_set = json.loads(gold_path.read_text(encoding="utf-8"))

    if ids:
        by_id = {item["id"]: item for item in gold_set}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise SystemExit(f"Unknown gold question id(s): {missing}")
        gold_set = [by_id[i] for i in ids]
    elif limit:
        gold_set = gold_set[:limit]

    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    llm = get_llm(settings.llm)
    pipeline = RetrievalPipeline(settings, embedder, vectordb)
    generator = ResponseGenerator(settings, llm)

    results: list[dict] = []
    for item in gold_set:
        request = RetrieveRequest(query=item["question"], generate_answer=False, top_k=top_k)
        response = await pipeline.retrieve(request)
        agent_answer = await generator.generate(item["question"], response.chunks)

        retrieved_sources = [
            {"document": c.source, "clause_reference": c.clause_reference, "modality": c.modality, "score": c.score}
            for c in response.chunks
        ]

        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "agent_answer": agent_answer,
                "retrieved_sources": retrieved_sources,
                "top_k": top_k,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"  done: {item['id']}  ({len(retrieved_sources)} chunks retrieved)")

    return results


async def main(gold_path: Path, top_k: int, limit: int | None, ids: list[str] | None, output: Path) -> None:
    print(f"Running agent over {gold_path} (top_k={top_k})" + (f", ids={ids}" if ids else "") + "...")
    new_results = await run(gold_path, top_k, limit, ids)

    # Merge into whatever's already recorded, keyed by id, so re-running one or a
    # few questions never wipes out previously recorded results for the rest.
    existing: dict[str, dict] = {}
    if output.exists():
        existing = {r["id"]: r for r in json.loads(output.read_text(encoding="utf-8"))}
    for r in new_results:
        existing[r["id"]] = r

    # Keep output ordered the same as the gold set, for readable diffs.
    gold_order = [item["id"] for item in json.loads(gold_path.read_text(encoding="utf-8"))]
    merged = [existing[i] for i in gold_order if i in existing]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"\nRan {len(new_results)} question(s); {len(merged)} total recorded in {output}")
    print("Next: python scripts/compare_with_judge.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=Path("data/eval/gold_qa.json"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N gold questions.")
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        default=None,
        help="Only run this gold question id, e.g. --id 13 or --id 13,14,15. Repeatable. Takes priority over --limit.",
    )
    parser.add_argument("--output", type=Path, default=Path("data/eval/agent_results.json"))
    args = parser.parse_args()
    ids = [i for raw in args.ids for i in raw.split(",")] if args.ids else None
    asyncio.run(main(args.gold, args.top_k, args.limit, ids, args.output))
