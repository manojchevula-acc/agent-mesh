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
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.config.multimodal import FusionMode  # noqa: E402
from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.models.retrieval import RetrievedChunk, RetrievedImage, RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.fusion import rrf_fuse  # noqa: E402
from gernas_rag.retrieval.multimodal_pipeline import MultimodalRetrievalPipeline  # noqa: E402
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

from ..common.gold import GoldCase, fact_coverage, load_gold  # noqa: E402
from ..core import report  # noqa: E402
from ..core.cases import CaseStore  # noqa: E402

RETRIEVAL_EXPORT_PATH = Path("data/eval/runs/stage3_retrieval.json")


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


# ── data/eval/runs/stage3_retrieval.json export ──────────────────────────
# A separate, additive artifact from the CaseStore-driven
# `stage3_retrieval_cases.jsonl` that `stage4 --reuse-retrieval` reads — but
# built from the SAME accumulated store. Rewritten in full on every run, but
# its content is cumulative: every case ever recorded, ordered by id, not
# just whatever this particular invocation retrieved.


def _text_hit(c: RetrievedChunk, rank: int) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "rank": rank,
        "score": c.score,
        "document": c.source,
        "modality": c.content_type,  # 'text' | 'table' | 'list' | 'image_stub'
        "clause_reference": c.clause_reference,
        "is_parent": c.is_parent,
        "text": c.text,
    }


def _image_hit(im: RetrievedImage, rank: int) -> dict:
    return {
        "chunk_id": im.asset_id,
        "rank": rank,
        "score": im.score,  # SigLIP-2 cosine — NOT comparable to a text score
        "document": im.source,
        "modality": im.role,  # 'figure' | 'table_image' | 'diagram' | 'unknown'
        "clause_reference": "",  # no formal clause reference exists for a figure
        "is_parent": False,
        # Mirrors the exact string this stage already builds for fact-coverage
        # scoring (`fact_coverage` below), so this field and that score agree.
        "text": f"{im.caption} {im.nearest_heading}".strip(),
    }


def _build_hits(
    chunks: list[RetrievedChunk], images: list[RetrievedImage], settings
) -> list[dict]:
    """Order hits exactly as production's configured fusion mode would.

    side_car / off: text hits in their served order, images appended after —
    the same two-list shape /retrieve actually returns, concatenated.

    unified_rrf: re-applies the SAME `rrf_fuse` production uses internally
    (multimodal_pipeline.py `_apply_unified_order`) over both modalities, not
    just the text-only slice production keeps — because text and image scores
    are not comparable, this is the one place a single combined order is
    actually well-defined, per SYSTEM_ARCHITECTURE.md §5.2.
    """
    cfg = settings.multimodal.retrieval
    if images and cfg.mode is FusionMode.UNIFIED_RRF:
        fused = rrf_fuse(chunks, images, cfg.rrf_k, cfg.text_weight, cfg.image_weight)
        chunk_by_id = {c.chunk_id: c for c in chunks}
        image_by_id = {im.asset_id: im for im in images}
        hits = []
        for f in fused:
            if f.kind == "text":
                c = chunk_by_id.get(f.identifier)
                if c is not None:
                    hits.append(_text_hit(c, f.rank))
            else:
                im = image_by_id.get(f.identifier)
                if im is not None:
                    hits.append(_image_hit(im, f.rank))
        return hits

    hits = [_text_hit(c, i) for i, c in enumerate(chunks)]
    offset = len(hits)
    hits += [_image_hit(im, offset + i) for i, im in enumerate(images)]
    return hits


def _write_retrieval_export(settings, k: int, depth: int, records: list[dict]) -> Path:
    RETRIEVAL_EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RETRIEVAL_EXPORT_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "collection": settings.vectordb.collection_name,
                    "dense_top_k": settings.retrieval.dense_top_k,
                    "sparse_top_k": settings.retrieval.sparse_top_k,
                    "rrf_k": settings.retrieval.rrf_k,
                    "pre_rerank_top_k": settings.retrieval.pre_rerank_top_k,
                    "final_top_k": k,
                    "rank_depth": depth,
                    "embedding_model": settings.embedding.model_name,
                    "freshness_penalty_enabled": settings.retrieval.freshness_penalty_enabled,
                },
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return RETRIEVAL_EXPORT_PATH


def _id_key(case_id: str) -> int:
    return int(case_id) if case_id.isdigit() else 0


def run(
    limit: int | None = None,
    only: str | None = None,
    top_k: int | None = None,
    rank_depth: int = 10,
    fresh: bool = False,
):
    settings = get_settings()
    # `all_cases` ignores --only — it's the full lookup table so a case
    # already accumulated in a PAST batch (e.g. ids 1-10 from an earlier
    # `--only` run) can still be resolved and reported now, even though this
    # invocation's --only only asks to RETRIEVE a different batch (ids 31-37).
    all_cases = [c for c in load_gold() if c.answerable]
    cases = all_cases
    if only:
        wanted = {s.strip() for s in only.split(",")}
        cases = [c for c in cases if c.id in wanted]

    k = top_k or settings.retrieval.final_top_k
    # Retrieve deeper than what production actually serves so the export below
    # can support recall@rank_depth-style analysis, without changing today's
    # metrics: the reranker only SORTS then TRUNCATES (reranker.py), so asking
    # for `depth` items and slicing back to `k` yields identical top-k results
    # to asking for `k` directly.
    depth = max(k, rank_depth)

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

    # Persist the retrieved context so stage 4 can reuse it instead of
    # retrieving the same thing again — stage 4 skips ~25s of local model work
    # per case, and the "stage 3 passed but stage 4 failed => generation
    # problem" inference becomes literally true rather than probably true,
    # because both stages then score the SAME context.
    #
    # Accumulates ACROSS runs, keyed by gold case id: a case already recorded
    # is skipped, never re-retrieved and never discarded — the same
    # checkpoint/resume contract stage 2b and stage 4 use. `--fresh` is the
    # only way to wipe it. Because a stored row can predate this run's
    # top_k/rank_depth, every row records the settings it was retrieved
    # under, and a mismatch against the CURRENT settings is surfaced in the
    # report rather than silently blended in.
    retrieval_store = CaseStore("stage3_retrieval")
    if fresh:
        retrieval_store.clear()

    already = retrieval_store.done_keys()
    pending = [c for c in cases if c.id not in already]
    # --limit means "N MORE cases this run", applied AFTER the checkpoint
    # filter — matching stage 2b/4, so repeated runs advance instead of
    # re-requesting the same first N forever.
    if limit:
        pending = pending[:limit]
    skipped = len(cases) - len(pending)

    pipeline = build_pipeline(settings) if pending else None

    async def _drive() -> None:
        for case in pending:
            response = await pipeline.retrieve(
                RetrieveRequest(query=case.question, top_k=depth, include_images=True)
            )
            retrieval_store.append(
                case.id,
                {
                    "id": case.id,
                    "question": case.question,
                    "top_k": k,
                    "rank_depth": depth,
                    "latency_ms": response.latency_ms,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "chunks": [c.model_dump(mode="json") for c in response.chunks],
                    "images": [i.model_dump(mode="json") for i in response.images],
                },
            )

    asyncio.run(_drive())

    # ── Score every ACCUMULATED case, not just this invocation's batch ─
    # The report and JSON export must reflect the UNION of every id ever
    # recorded (across all past --only batches) and whatever this run just
    # asked for — not `cases` alone. Scoring only `cases` was the bug: after
    # 4 separate `--only` batches covering ids 1-37, each run's report/export
    # showed only that run's ~7-10 ids, because ids stored by EARLIER batches
    # were silently excluded from the loop even though retrieval_store.rows()
    # already had them. Iterated in id order per case #.
    stored = {r["id"]: r for r in retrieval_store.rows()}
    by_id = {c.id: c for c in all_cases}
    requested_ids = {c.id for c in cases}
    report_ids = sorted(requested_ids | set(stored.keys()), key=_id_key)
    report_cases = [by_id[i] for i in report_ids if i in by_id]
    result.context["cases"] = len(report_cases)

    stale_config: list[str] = []
    not_yet_run: list[str] = []

    hit_count = 0
    reciprocal_ranks: list[float] = []
    precisions: list[float] = []
    fact_recalls: list[float] = []
    images_returned = 0
    export_records: list[dict] = []
    scored = 0

    for case in report_cases:
        row = stored.get(case.id)
        if row is None:
            # Only reachable the first time an id is requested while --limit
            # excludes it from `pending` — it has never been retrieved at all.
            not_yet_run.append(case.id)
            result.rows.append(
                {
                    "id": case.id, "name": case.name[:34], "rank": "not yet run",
                    "facts": "—", "imgs": "—", "modality": "—", "missing": "",
                }
            )
            continue

        if row.get("top_k") != k:
            stale_config.append(case.id)

        all_chunks = [RetrievedChunk(**c) for c in row["chunks"]]
        images = [RetrievedImage(**i) for i in row["images"]]
        chunks = all_chunks[:k]
        if images:
            images_returned += 1
        scored += 1

        export_records.append(
            {
                "id": case.id,
                "question": row.get("question", case.question),
                "latency_ms": row.get("latency_ms", 0.0),
                "generated_at": row.get("generated_at", ""),
                "hits": _build_hits(all_chunks, images, settings),
            }
        )

        rank = _rank_of_expected(chunks, case.expected_documents)
        if rank is not None:
            hit_count += 1
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

    export_path = _write_retrieval_export(settings, k, depth, export_records)
    print(f"  -> {export_path} ({len(export_records)} question(s), rank_depth={depth})")

    n = scored
    result.add("hit_rate_at_k", hit_count / n if n else report.NA, f"{hit_count}/{n} at k={k}")
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
        f"Image branch returned results on {images_returned}/{n} scored queries "
        f"(image_intent={settings.multimodal.retrieval.image_intent.value})."
    )
    result.notes.append(
        "Modality mismatches are reported in the table, never scored: a 'figure' "
        "gold source served as a table-text chunk is the D8 dual representation "
        "working as designed."
    )
    if skipped:
        result.notes.append(
            f"{skipped} case(s) were already recorded from a previous run and "
            "were not re-retrieved this run — scored from their stored results. "
            "Pass --fresh to discard everything and rescore from scratch."
        )
    if stale_config:
        result.notes.append(
            f"{len(stale_config)} case(s) were scored from a PREVIOUS run's "
            f"top_k, which differs from this run's top_k={k}: "
            f"{', '.join(stale_config[:10])}. Pass --fresh to rescore them "
            "at the current settings — otherwise this report blends configs."
        )
    if not_yet_run:
        result.notes.append(
            f"{len(not_yet_run)} case(s) have never been retrieved (excluded by "
            f"--limit on every run so far) and are excluded from every metric "
            f"above: {', '.join(not_yet_run[:10])}."
        )
    unverified = [c.id for c in report_cases if not c.pipeline_verified]
    if unverified:
        result.notes.append(
            f"{len(unverified)} case(s) lack pipeline_verified on any expected "
            f"source ({', '.join(unverified[:10])}) — a miss there may be stale "
            "ground truth rather than a retrieval defect."
        )
    return result
