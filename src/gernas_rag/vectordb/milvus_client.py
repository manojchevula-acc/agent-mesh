"""Alternative vector DB implementation — Milvus (billion-scale)."""

import asyncio
from functools import partial
from typing import Any

from ..config.vectordb import VectorDBConfig
from ..models.chunk import Chunk, ChunkMetadata, EmbeddedChunk
from ..models.retrieval import DocumentFilter
from ..utils.hashing import make_point_uuid
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .base import BaseVectorDB, SearchResult

logger = get_logger(__name__)

_TEXT_MAX_LEN = 65535
_META_MAX_LEN = 8192


class MilvusVectorDB(BaseVectorDB):
    """Milvus implementation using pymilvus.

    pymilvus' high-level ``MilvusClient`` is synchronous, so all calls are
    dispatched to a thread pool executor to keep the event loop free. Dense ANN
    and sparse (BM25/SPLADE) search are both supported through separate vector
    fields on a single collection.
    """

    def __init__(self, config: VectorDBConfig) -> None:
        from pymilvus import MilvusClient

        self._config = config
        uri = f"http://{config.milvus_host}:{config.milvus_port}"
        self._client = MilvusClient(uri=uri, token=config.milvus_token or "")

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def create_collection(self, name: str, dense_dim: int) -> None:
        from pymilvus import DataType

        def _create() -> None:
            if self._client.has_collection(name):
                logger.info("Collection already exists", collection=name)
                return
            schema = self._client.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dense_dim)
            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_field("text", DataType.VARCHAR, max_length=_TEXT_MAX_LEN)
            schema.add_field("metadata", DataType.VARCHAR, max_length=_META_MAX_LEN)
            schema.add_field("document_type", DataType.VARCHAR, max_length=64)
            schema.add_field("deprecated", DataType.BOOL)

            index_params = self._client.prepare_index_params()
            index_params.add_index(field_name="dense", index_type="HNSW", metric_type="COSINE")
            index_params.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
            self._client.create_collection(name, schema=schema, index_params=index_params)
            self._client.load_collection(name)
            logger.info("Collection created", collection=name, dense_dim=dense_dim)

        await self._run(_create)

    async def delete_collection(self, name: str) -> None:
        await self._run(self._client.drop_collection, name)
        logger.info("Collection deleted", collection=name)

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        import json

        if not chunks:
            return 0
        rows = []
        for c in chunks:
            sparse = (
                {int(i): float(v) for i, v in zip(c.sparse_indices, c.sparse_values)}
                if c.sparse_indices
                else {0: 0.0}
            )
            rows.append(
                {
                    "id": make_point_uuid(c.chunk.id),
                    "dense": c.dense_vector,
                    "sparse": sparse,
                    "text": c.chunk.text[:_TEXT_MAX_LEN],
                    "metadata": json.dumps(
                        {**c.chunk.metadata.model_dump(mode="json"), "chunk_id": c.chunk.id,
                         "is_parent": c.chunk.is_parent}
                    )[:_META_MAX_LEN],
                    "document_type": c.chunk.metadata.document_type.value,
                    "deprecated": c.chunk.metadata.deprecated,
                }
            )
        await self._run(self._client.upsert, self._config.collection_name, rows)
        logger.info("Upserted points", collection=self._config.collection_name, count=len(rows))
        return len(rows)

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def dense_search(
        self, query_vector: list[float], top_k: int, filters: DocumentFilter | None = None
    ) -> list[SearchResult]:
        results = await self._run(
            self._client.search,
            self._config.collection_name,
            data=[query_vector],
            anns_field="dense",
            limit=top_k,
            filter=self._build_filter(filters),
            output_fields=["text", "metadata"],
        )
        return self._to_results(results)

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def sparse_search(
        self,
        query_indices: list[int],
        query_values: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[SearchResult]:
        if not query_indices:
            return []
        sparse = {int(i): float(v) for i, v in zip(query_indices, query_values)}
        results = await self._run(
            self._client.search,
            self._config.collection_name,
            data=[sparse],
            anns_field="sparse",
            limit=top_k,
            filter=self._build_filter(filters),
            output_fields=["text", "metadata"],
        )
        return self._to_results(results)

    async def get_by_ids(self, ids: list[str]) -> list[Chunk]:
        import json

        if not ids:
            return []
        point_ids = [make_point_uuid(i) for i in ids]
        records = await self._run(
            self._client.get,
            self._config.collection_name,
            ids=point_ids,
            output_fields=["text", "metadata"],
        )
        chunks: list[Chunk] = []
        for r in records or []:
            meta = json.loads(r.get("metadata", "{}"))
            chunks.append(self._meta_to_chunk(r.get("text", ""), meta))
        return chunks

    async def health_check(self) -> bool:
        try:
            await self._run(self._client.list_collections)
            return True
        except Exception:
            return False

    # ── Helpers ───────────────────────────────────────────────────────
    def _build_filter(self, filters: DocumentFilter | None) -> str:
        clauses = ["deprecated == false"]
        if filters and filters.document_type:
            types = ", ".join(f'"{t}"' for t in filters.document_type)
            clauses.append(f"document_type in [{types}]")
        return " and ".join(clauses)

    @staticmethod
    def _to_results(results: Any) -> list[SearchResult]:
        import json

        out: list[SearchResult] = []
        hits = results[0] if results else []
        for i, hit in enumerate(hits):
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            meta = entity.get("metadata", "{}")
            meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})
            out.append(
                SearchResult(
                    chunk_id=meta_dict.get("chunk_id", str(hit.get("id", ""))),
                    text=entity.get("text", ""),
                    score=float(hit.get("distance", 0.0)),
                    metadata=meta_dict,
                    rank=i,
                )
            )
        return out

    @staticmethod
    def _meta_to_chunk(text: str, meta: dict[str, Any]) -> Chunk:
        meta_fields = ChunkMetadata.model_fields.keys()
        meta_data = {k: v for k, v in meta.items() if k in meta_fields}
        return Chunk(
            id=meta.get("chunk_id", ""),
            text=text,
            metadata=ChunkMetadata(**meta_data),
            is_parent=meta.get("is_parent", False),
        )
