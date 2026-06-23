"""Primary vector DB implementation — async Qdrant with native hybrid search."""

from typing import Any

from ..config.vectordb import VectorDBConfig
from ..models.chunk import Chunk, ChunkMetadata, EmbeddedChunk
from ..models.retrieval import DocumentFilter
from ..utils.hashing import make_point_uuid
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .base import BaseVectorDB, SearchResult

logger = get_logger(__name__)

# Distance metric mapping (string config -> Qdrant enum), resolved lazily.
_DISTANCE_MAP = {"cosine": "COSINE", "dot": "DOT", "euclidean": "EUCLID"}


class QdrantVectorDB(BaseVectorDB):
    """Async Qdrant implementation using AsyncQdrantClient.

    Supports native hybrid search (dense + sparse) via named vectors. Point ids are
    deterministic UUIDv5 values derived from the chunk id, so re-ingestion is
    idempotent.
    """

    def __init__(self, config: VectorDBConfig) -> None:
        from qdrant_client import AsyncQdrantClient

        self._config = config
        if config.qdrant_path:
            # Embedded local engine — no server required.
            self._client = AsyncQdrantClient(path=config.qdrant_path)
        else:
            self._client = AsyncQdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key,
                prefer_grpc=config.qdrant_prefer_grpc,
            )

    # ── Collection management ─────────────────────────────────────────
    async def create_collection(self, name: str, dense_dim: int) -> None:
        from qdrant_client.models import (
            Distance,
            SparseIndexParams,
            SparseVectorParams,
            VectorParams,
        )

        exists = await self._client.collection_exists(name)
        if exists:
            logger.info("Collection already exists", collection=name)
            return

        distance = getattr(Distance, _DISTANCE_MAP.get(self._config.distance_metric, "COSINE"))
        await self._client.create_collection(
            collection_name=name,
            vectors_config={"dense": VectorParams(size=dense_dim, distance=distance)},
            sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams())},
            on_disk_payload=self._config.on_disk_payload,
            replication_factor=self._config.replication_factor,
        )

        # Create payload indexes for fast metadata filtering.
        for field_name, field_type in [
            ("document_type", "keyword"),
            ("product_applicability", "keyword"),
            ("deprecated", "bool"),
            ("effective_date", "keyword"),
        ]:
            await self._client.create_payload_index(name, field_name, field_type)
        logger.info("Collection created", collection=name, dense_dim=dense_dim)

    async def delete_collection(self, name: str) -> None:
        await self._client.delete_collection(collection_name=name)
        logger.info("Collection deleted", collection=name)

    # ── Write path ────────────────────────────────────────────────────
    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        from qdrant_client.models import PointStruct, SparseVector

        points = []
        for c in chunks:
            vector: dict[str, Any] = {"dense": c.dense_vector}
            if c.sparse_indices:
                vector["sparse"] = SparseVector(
                    indices=c.sparse_indices, values=c.sparse_values
                )
            payload = {
                **c.chunk.metadata.model_dump(mode="json"),
                "text": c.chunk.text,
                "is_parent": c.chunk.is_parent,
                "chunk_id": c.chunk.id,
            }
            points.append(
                PointStruct(id=make_point_uuid(c.chunk.id), vector=vector, payload=payload)
            )

        if not points:
            return 0
        await self._client.upsert(collection_name=self._config.collection_name, points=points)
        logger.info("Upserted points", collection=self._config.collection_name, count=len(points))
        return len(points)

    # ── Read path ─────────────────────────────────────────────────────
    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def dense_search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[SearchResult]:
        response = await self._client.query_points(
            collection_name=self._config.collection_name,
            query=query_vector,
            using="dense",
            query_filter=self._build_filter(filters),
            limit=top_k,
            with_payload=True,
        )
        return self._to_results(response.points)

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def sparse_search(
        self,
        query_indices: list[int],
        query_values: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[SearchResult]:
        from qdrant_client.models import SparseVector

        if not query_indices:
            return []
        response = await self._client.query_points(
            collection_name=self._config.collection_name,
            query=SparseVector(indices=query_indices, values=query_values),
            using="sparse",
            query_filter=self._build_filter(filters),
            limit=top_k,
            with_payload=True,
        )
        return self._to_results(response.points)

    async def get_by_ids(self, ids: list[str]) -> list[Chunk]:
        if not ids:
            return []
        point_ids = [make_point_uuid(i) for i in ids]
        records = await self._client.retrieve(
            collection_name=self._config.collection_name,
            ids=point_ids,
            with_payload=True,
        )
        return [self._payload_to_chunk(r.payload) for r in records if r.payload]

    async def health_check(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False

    # ── Helpers ───────────────────────────────────────────────────────
    def _build_filter(self, filters: DocumentFilter | None) -> Any:
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        must = [FieldCondition(key="deprecated", match=MatchValue(value=False))]
        if filters:
            if filters.document_type:
                must.append(
                    FieldCondition(
                        key="document_type", match=MatchAny(any=filters.document_type)
                    )
                )
            if filters.product_applicability:
                must.append(
                    FieldCondition(
                        key="product_applicability",
                        match=MatchAny(any=filters.product_applicability),
                    )
                )
        return Filter(must=must)

    @staticmethod
    def _to_results(points: list[Any]) -> list[SearchResult]:
        results: list[SearchResult] = []
        for i, p in enumerate(points):
            payload = p.payload or {}
            chunk_id = payload.get("chunk_id", str(p.id))
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    text=payload.get("text", ""),
                    score=float(p.score),
                    metadata=payload,
                    rank=i,
                )
            )
        return results

    @staticmethod
    def _payload_to_chunk(payload: dict[str, Any]) -> Chunk:
        meta_fields = ChunkMetadata.model_fields.keys()
        meta_data = {k: v for k, v in payload.items() if k in meta_fields}
        metadata = ChunkMetadata(**meta_data)
        return Chunk(
            id=payload.get("chunk_id", ""),
            text=payload.get("text", ""),
            metadata=metadata,
            is_parent=payload.get("is_parent", False),
        )
