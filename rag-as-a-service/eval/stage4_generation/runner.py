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
from gernas_rag.models.retrieval import RetrieveRequest
from gernas_rag.retrieval.pipeline import RetrievalPipeline
from gernas_rag.vectordb.base import BaseVectorDB

from ..core.corpus import ChunkIndex
from ..core.models import GenerationContext, GenerationRunRecord


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
    ) -> None:
        self._pipeline = RetrievalPipeline(settings, embedder, vectordb)
        self._generator, self.effective_settings = build_generator(
            settings, llm, force_hydration=force_hydration
        )
        self._top_k = top_k
        # Retrieved chunks carry no chunk_id, so it is recovered by matching
        # (document, clause, text) against the index. Without it, stage 4 cannot
        # be joined to stage 3's judgments.
        self._chunk_lookup = _build_lookup(chunk_index) if chunk_index else {}

    async def run(self, question_id: str, question: str) -> GenerationRunRecord:
        start = time.perf_counter()
        response = await self._pipeline.retrieve(
            RetrieveRequest(query=question, top_k=self._top_k, generate_answer=False)
        )
        retrieval_ms = round((time.perf_counter() - start) * 1000, 2)

        start = time.perf_counter()
        answer, trace = await self._generator.generate_with_trace(question, response.chunks)
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
            for i, chunk in enumerate(response.chunks, start=1)
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
        )

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
        }


def _lookup_key(document: str, clause: str, text: str) -> tuple[str, str, str]:
    return (document, clause or "", (text or "")[:160])


def _build_lookup(index: ChunkIndex) -> dict[tuple[str, str, str], str]:
    return {
        _lookup_key(chunk.document, chunk.clause_reference, chunk.text): chunk.chunk_id
        for chunk in index.chunks
    }
