"""Execute the production answer path over the gold set and record what happened.

Built through ``gernas_rag.generation.factory.build_generator`` — the same
function the FastAPI lifespan uses — so the artifact store and vision LLM are
attached exactly as they are in production. Constructing ``ResponseGenerator``
directly (as the older scripts did) silently disables hydration and turns every
recorded answer into a text-only one regardless of configuration.

Context blocks are recorded with the same 1-based numbering the generator writes
into the prompt, which is what makes citation checking meaningful downstream.
"""

from __future__ import annotations

import time
from typing import Any

from gernas_rag.config.settings import Settings
from gernas_rag.embeddings.base import BaseEmbedder
from gernas_rag.generation.factory import build_generator
from gernas_rag.llm.base import BaseLLM
from gernas_rag.models.retrieval import RetrievedChunk, RetrieveRequest
from gernas_rag.retrieval.pipeline import RetrievalPipeline
from gernas_rag.vectordb.base import BaseVectorDB

from ..core.corpus import ChunkIndex
from ..core.models import GenerationContext, GenerationRunRecord, RankedHit
from .retrieval_reuse import build_reused_chunks


class GenerationRunner:
    """Runs retrieve -> generate for one question and records the full trace."""

    def __init__(
        self,
        settings: Settings,
        embedder: BaseEmbedder,
        vectordb: BaseVectorDB,
        llm: BaseLLM,
        top_k: int,
        force_hydration: bool | None = None,
        chunk_index: ChunkIndex | None = None,
        reused_hits: dict[str, list[RankedHit]] | None = None,
        reuse_meta: dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._vectordb = vectordb
        self._pipeline = RetrievalPipeline(settings, embedder, vectordb)
        self._generator, self.effective_settings = build_generator(
            settings, llm, force_hydration=force_hydration
        )
        self._top_k = top_k
        self._chunk_index = chunk_index
        # Retrieved chunks carry no chunk_id, so it is recovered by matching
        # (document, clause, text) against the index. Without it, stage 4 cannot
        # be joined to stage 3's judgments.
        self._chunk_lookup = _build_lookup(chunk_index) if chunk_index else {}
        # question_id -> stage3's cached ranking, for --reuse-retrieval. A question
        # missing here (or with too few hits, or whose chunks no longer resolve)
        # falls back to a live retrieval call rather than failing the run.
        self._reused_hits = reused_hits or {}
        self._reuse_meta = reuse_meta or {}

    async def run(self, question_id: str, question: str) -> GenerationRunRecord:
        start = time.perf_counter()
        chunks, retrieval_source = await self._retrieve(question_id, question)
        retrieval_ms = round((time.perf_counter() - start) * 1000, 2)

        start = time.perf_counter()
        answer, trace = await self._generator.generate_with_trace(question, chunks)
        generation_ms = round((time.perf_counter() - start) * 1000, 2)

        contexts = [
            GenerationContext(
                index=i,
                chunk_id=self._chunk_lookup.get(_lookup_key(chunk.source, chunk.clause_reference, chunk.text), ""),
                document=chunk.source,
                modality=chunk.modality,
                clause_reference=chunk.clause_reference,
                artifact_ref=chunk.artifact_ref,
                text=chunk.text,
                parent_text=chunk.parent_text,
                score=chunk.score,
            )
            for i, chunk in enumerate(chunks, start=1)
        ]

        return GenerationRunRecord(
            id=question_id,
            question=question,
            answer=answer,
            contexts=contexts,
            images_hydrated=trace.images_hydrated,
            vision_used=trace.vision_used,
            hydration_eligible=trace.hydration_eligible,
            retrieval_latency_ms=retrieval_ms,
            generation_latency_ms=generation_ms,
            top_k=self._top_k,
            retrieval_source=retrieval_source,
        )

    async def _retrieve(self, question_id: str, question: str) -> tuple[list[RetrievedChunk], str]:
        """Reused chunks if stage3 covered this question with enough depth and its
        chunks still resolve; a live retrieval call otherwise.
        """
        hits = self._reused_hits.get(question_id)
        if hits and len(hits) >= self._top_k:
            chunks = await build_reused_chunks(hits, self._top_k, self._vectordb, self._settings)
            if len(chunks) == self._top_k:
                return chunks, "reused"
        response = await self._pipeline.retrieve(
            RetrieveRequest(query=question, top_k=self._top_k, generate_answer=False)
        )
        return response.chunks, "live"

    def config_snapshot(self) -> dict[str, Any]:
        settings = self.effective_settings
        return {
            "collection": settings.vectordb.collection_name,
            "top_k": self._top_k,
            "llm_provider": settings.llm.provider,
            "llm_model": settings.llm.model_name,
            "hydration_enabled": settings.hydration.enabled,
            "hydration_mode": settings.hydration.mode,
            "vision_model": settings.hydration.vision_model_name
            if settings.hydration.enabled
            else None,
            "enrichment_enabled": settings.enrichment.enabled,
            "freshness_penalty_enabled": settings.retrieval.freshness_penalty_enabled,
            **self._reuse_meta,
        }


def _lookup_key(document: str, clause: str, text: str) -> tuple[str, str, str]:
    return (document, clause or "", (text or "")[:160])


def _build_lookup(index: ChunkIndex) -> dict[tuple[str, str, str], str]:
    return {
        _lookup_key(chunk.document, chunk.clause_reference, chunk.text): chunk.chunk_id
        for chunk in index.chunks
    }
