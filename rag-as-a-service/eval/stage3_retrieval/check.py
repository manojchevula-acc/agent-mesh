"""Stage 3 — Retrieval quality & ordering.

RAG-Check calls this failure *selection-hallucination*: the right answer exists
in the corpus, but the selection step never puts it in front of the generator.
No prompt change can recover from it, which is why it is measured separately
from answer quality.

Graded on DOCUMENT + FACT CONTAINMENT, not on section_heading or
clause_reference — see eval/common/gold.py for the measured reason those two
fields are unusable as targets on this corpus.

Modality is REPORTED, never scored. The gold set marks a source 'figure' when
the answer lives in a visual element, but this pipeline may legitimately serve
that as a table-text chunk (D8 dual representation) — which is a *better*
outcome, since the values become lexically searchable. Failing it would punish
the pipeline for doing the right thing.
"""

import asyncio
import sys

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.models.retrieval import RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.multimodal_pipeline import MultimodalRetrievalPipeline  # noqa: E402
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

from ..common.gold import GoldCase, fact_coverage, load_gold  # noqa: E402
from ..core import report  # noqa: E402


def build_pipeline(settings):
    """Construct the REAL retrieval pipeline, exactly as the service does.

    Deliberately not a simplified stand-in: an eval that exercises a different
    code path than production measures something production does not do.
    """
    vectordb = get_vectordb(settings.vectordb)
    embedder = get_embedder(settings.embedding)
    mm_embedder = image_store = None
    floor = 0.10

    if settings.multimodal.enabled:
        from gernas_rag.embeddings.multimodal.factory import (
            get_multimodal_embedder,
            registry_score_floor,
        )
        from gernas_rag.vectordb.image_factory import get_image_store

        mm_embedder = get_multimodal_embedder(settings.multimodal.embedding)
        floor = registry_score_floor(settings.multimodal.embedding)
        collection = settings.multimodal.image_collection_name or mm_embedder.space.collection_name(
            settings.multimodal.image_collection_base
        )
        image_store = get_image_store(settings.vectordb, collection, vectordb)

    return MultimodalRetrievalPipeline(
        settings,
        RetrievalPipeline(settings, embedder, vectordb),
        mm_embedder,
        image_store,
        floor,
    )


def _rank_of_expected(chunks, expected_documents: list[str]) -> int | None:
    """1-based rank of the first chunk from an expected document."""
    wanted = {d.lower() for d in expected_documents}
    for rank, chunk in enumerate(chunks, 1):
        if any(w in (chunk.source or "").lower() for w in wanted):
            return rank
    return None


def run(limit: int | None = None, only: str | None = None, top_k: int | None = None):
    settings = get_settings()
    cases = [c for c in load_gold() if c.answerable]
    if only:
        wanted = {s.strip() for s in only.split(",")}
        cases = [c for c in cases if c.id in wanted]
    if limit:
        cases = cases[:limit]

    k = top_k or settings.retrieval.final_top_k

    # There is no settings.retrieval.reranker_model: RetrievalPipeline derives
    # the reranker from the EMBEDDING config via
    # Reranker.from_embedding_config(), and skips it entirely for the
    # sentence-transformer provider. Mirror that logic so the report states
    # what actually ran rather than what a config field suggests.
    from gernas_rag.config.embedding import EmbeddingProvider, RerankerConfig

    reranked = settings.embedding.provider != EmbeddingProvider.SENTENCE_TRANSFORMER

    result = report.StageResult(
        stage="stage3",
        title="Stage 3 — Retrieval Quality & Ordering",
        context={
            "final_top_k": k,
            "reranker": RerankerConfig().model_name if reranked else "off",
            "multimodal": settings.multimodal.enabled,
            "image_intent": settings.multimodal.retrieval.image_intent.value,
            "cases": len(cases),
        },
    )

    pipeline = build_pipeline(settings)

    hits = 0
    reciprocal_ranks: list[float] = []
    precisions: list[float] = []
    fact_recalls: list[float] = []
    images_returned = 0

    async def _drive() -> None:
        nonlocal hits, images_returned
        for case in cases:
            response = await pipeline.retrieve(
                RetrieveRequest(query=case.question, top_k=k, include_images=True)
            )
            chunks, images = response.chunks, response.images
            if images:
                images_returned += 1

            rank = _rank_of_expected(chunks, case.expected_documents)
            if rank is not None:
                hits += 1
                reciprocal_ranks.append(1.0 / rank)
            else:
                reciprocal_ranks.append(0.0)

            wanted = {d.lower() for d in case.expected_documents}
            relevant = sum(
                1 for c in chunks if any(w in (c.source or "").lower() for w in wanted)
            )
            precisions.append(relevant / len(chunks) if chunks else 0.0)

            # Fact containment across the WHOLE retrieved context (text chunks
            # plus image captions) — the generator sees all of it, so grading
            # chunk-by-chunk would understate what was actually available.
            context = "\n".join(
                [c.text or "" for c in chunks]
                + [f"{i.caption} {getattr(i, 'nearest_heading', '')}" for i in images]
            )
            covered, missing = fact_coverage(case.facts, context)
            fact_recalls.append(covered / len(case.facts) if case.facts else 1.0)

            result.rows.append(
                {
                    "id": case.id,
                    "name": case.name[:34],
                    "rank": rank if rank else "MISS",
                    "facts": f"{covered}/{len(case.facts)}" if case.facts else "—",
                    "imgs": len(images),
                    "modality": ",".join(sorted(set(case.modalities))) or "—",
                    "missing": ", ".join(missing[:3]),
                }
            )

    asyncio.run(_drive())

    n = len(cases)
    result.add("hit_rate_at_k", hits / n if n else report.NA, f"{hits}/{n} at k={k}")
    result.add(
        "mrr",
        sum(reciprocal_ranks) / n if n else report.NA,
        "1/rank of first chunk from an expected document",
    )
    result.add(
        "context_precision",
        sum(precisions) / n if n else report.NA,
        "share of retrieved chunks from an expected document",
    )
    result.add(
        "recall_at_k",
        sum(fact_recalls) / n if n else report.NA,
        "gold facts present in the retrieved context",
    )

    # The RS-proxy and its calibration need a judge pass over query/image pairs;
    # not wired yet, so they report n/a rather than a fabricated number.
    result.add("image_relevancy", report.NA, "needs the judge pass — not yet implemented")
    result.add("score_gate_agreement", report.NA, "needs image_relevancy first")

    result.notes.append(
        f"Image branch returned results on {images_returned}/{n} queries "
        f"(image_intent={settings.multimodal.retrieval.image_intent.value})."
    )
    result.notes.append(
        "Modality mismatches are reported in the table, never scored: a 'figure' "
        "gold source served as a table-text chunk is the D8 dual representation "
        "working as designed."
    )
    unverified = [c.id for c in cases if not c.pipeline_verified]
    if unverified:
        result.notes.append(
            f"{len(unverified)} case(s) lack pipeline_verified on any expected "
            f"source ({', '.join(unverified[:10])}) — a miss there may be stale "
            "ground truth rather than a retrieval defect."
        )
    return result
