"""Text->image retrieval evaluation and score-floor calibration.

Two jobs:
  1. Recall@1/3/5 and MRR over tests/fixtures/multimodal_golden.yaml
  2. --sweep-floor: precision/recall against KNOWN-NEGATIVE queries, printing the
     recommended image_score_floor.

The floor is the single most important tunable in the image branch, and it MUST
be re-run after any change to the model, its revision, or int8 quantisation —
each shifts the similarity distribution.

Usage:
    python scripts/eval_multimodal.py
    python scripts/eval_multimodal.py --sweep-floor
"""

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402
from gernas_rag.vectordb.image_factory import get_image_store  # noqa: E402

_GOLDEN = Path("tests/fixtures/multimodal_golden.yaml")


def _load_golden() -> dict:
    if not _GOLDEN.exists():
        print(f"Golden set not found at {_GOLDEN}")
        sys.exit(1)
    return yaml.safe_load(_GOLDEN.read_text(encoding="utf-8")) or {}


async def _search(embedder, store, query: str, top_k: int):
    output = await embedder.embed_query(query)
    return await store.dense_search(output.dense_vectors[0], top_k)


async def evaluate(embedder, store, cases: list[dict], top_k: int = 5) -> dict:
    hits = {1: 0, 3: 0, 5: 0}
    reciprocal = 0.0
    for case in cases:
        results = await _search(embedder, store, case["query"], top_k)
        ids = [r.payload.get("id", r.asset_id) for r in results]
        expected = case["expected_asset_id"]
        if expected in ids:
            rank = ids.index(expected) + 1
            reciprocal += 1 / rank
            for k in hits:
                if rank <= k:
                    hits[k] += 1

    n = max(1, len(cases))
    return {
        "n": len(cases),
        "recall@1": hits[1] / n,
        "recall@3": hits[3] / n,
        "recall@5": hits[5] / n,
        "mrr": reciprocal / n,
    }


async def sweep_floor(embedder, store, positives: list[dict], negatives: list[str]) -> None:
    """Print the precision/recall curve used to pick image_score_floor."""
    pos_scores: list[float] = []
    for case in positives:
        results = await _search(embedder, store, case["query"], 5)
        expected = case["expected_asset_id"]
        for r in results:
            if r.payload.get("id", r.asset_id) == expected:
                pos_scores.append(r.score)
                break

    neg_scores: list[float] = []
    for query in negatives:
        results = await _search(embedder, store, query, 5)
        if results:
            neg_scores.append(max(r.score for r in results))

    print("\nFloor  Recall(pos)  FalsePos(neg)")
    print("-" * 34)
    best_floor, best_gap = 0.0, -1.0
    for step in range(0, 51):
        floor = step / 100
        recall = sum(1 for s in pos_scores if s >= floor) / max(1, len(pos_scores))
        false_pos = sum(1 for s in neg_scores if s >= floor) / max(1, len(neg_scores))
        if step % 2 == 0:
            print(f"{floor:>5.2f}  {recall:>11.0%}  {false_pos:>13.0%}")
        gap = recall - false_pos
        if gap > best_gap:
            best_floor, best_gap = floor, gap

    print(f"\nRecommended image_score_floor: {best_floor:.2f}")
    print("Set it under multimodal.retrieval.image_score_floor in config/local.yaml,")
    print("and record it in config/model_registry.yaml for this model.")
    if neg_scores and best_gap < 0.5:
        print(
            "\nWARNING: positives and negatives are poorly separated. No floor will "
            "cleanly split them — the model may be a bad fit for this corpus."
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep-floor", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    embedder = get_multimodal_embedder(settings.multimodal.embedding)
    space = embedder.space
    collection = settings.multimodal.image_collection_name or space.collection_name(
        settings.multimodal.image_collection_base
    )
    store = get_image_store(settings.vectordb, collection, get_vectordb(settings.vectordb))

    golden = _load_golden()
    positives = golden.get("cases", [])
    negatives = golden.get("negative_queries", [])

    print(f"Model      : {space.model_name} (dim={space.dim})")
    print(f"Collection : {collection}")
    print(f"Indexed    : {await store.count()} images\n")

    metrics = await evaluate(embedder, store, positives)
    for key, value in metrics.items():
        print(f"{key:<10} {value:.2%}" if key != "n" else f"{key:<10} {value}")

    if metrics["recall@1"] < 0.83:
        print("\nBELOW GATE: Recall@1 < 0.83 (design doc section 13.5)")

    if args.sweep_floor:
        await sweep_floor(embedder, store, positives, negatives)


if __name__ == "__main__":
    asyncio.run(main())
