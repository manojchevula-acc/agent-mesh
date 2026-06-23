"""Dev/test vector DB implementation — ChromaDB (dense-only)."""

import asyncio
from functools import partial
from typing import Any

from ..config.vectordb import VectorDBConfig
from ..models.chunk import Chunk, ChunkMetadata, EmbeddedChunk
from ..models.retrieval import DocumentFilter
from ..utils.hashing import make_point_uuid
from ..utils.logging import get_logger
from .base import BaseVectorDB, SearchResult

logger = get_logger(__name__)


class ChromaVectorDB(BaseVectorDB):
    """ChromaDB implementation for development / testing.

    ChromaDB has no native sparse vector support, so ``sparse_search`` always
    returns an empty list and hybrid search degrades gracefully to dense-only.
    The client is synchronous, so calls run in a thread pool executor.
    """

    def __init__(self, config: VectorDBConfig) -> None:
        import chromadb

        self._config = config
        if config.chroma_host:
            self._client = chromadb.HttpClient(host=config.chroma_host, port=config.chroma_port)
        else:
            self._client = chromadb.PersistentClient(path=config.chroma_path)
        self._collection: Any | None = None

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def create_collection(self, name: str, dense_dim: int) -> None:
        def _create() -> None:
            self._collection = self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"}
            )

        await self._run(_create)
        logger.info("Collection ready", collection=name, dense_dim=dense_dim)

    async def delete_collection(self, name: str) -> None:
        await self._run(self._client.delete_collection, name)
        self._collection = None
        logger.info("Collection deleted", collection=name)

    def _ensure_collection(self) -> Any:
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=self._config.collection_name, metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        if not chunks:
            return 0

        def _upsert() -> int:
            col = self._ensure_collection()
            col.upsert(
                ids=[make_point_uuid(c.chunk.id) for c in chunks],
                embeddings=[c.dense_vector for c in chunks],
                documents=[c.chunk.text for c in chunks],
                metadatas=[self._flatten_meta(c) for c in chunks],
            )
            return len(chunks)

        count = await self._run(_upsert)
        logger.info("Upserted points", collection=self._config.collection_name, count=count)
        return count

    async def dense_search(
        self, query_vector: list[float], top_k: int, filters: DocumentFilter | None = None
    ) -> list[SearchResult]:
        def _search() -> Any:
            col = self._ensure_collection()
            return col.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=self._build_filter(filters),
            )

        res = await self._run(_search)
        return self._to_results(res)

    async def sparse_search(
        self,
        query_indices: list[int],
        query_values: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[SearchResult]:
        # ChromaDB does not support sparse vectors.
        return []

    async def get_by_ids(self, ids: list[str]) -> list[Chunk]:
        if not ids:
            return []

        def _get() -> Any:
            col = self._ensure_collection()
            return col.get(ids=[make_point_uuid(i) for i in ids], include=["documents", "metadatas"])

        res = await self._run(_get)
        chunks: list[Chunk] = []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        for text, meta in zip(docs, metas):
            chunks.append(self._meta_to_chunk(text or "", meta or {}))
        return chunks

    async def health_check(self) -> bool:
        try:
            await self._run(self._client.heartbeat)
            return True
        except Exception:
            return False

    # ── Helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _flatten_meta(c: EmbeddedChunk) -> dict[str, Any]:
        meta = c.chunk.metadata.model_dump(mode="json")
        # Chroma metadata values must be scalars; join list fields.
        meta["product_applicability"] = ",".join(c.chunk.metadata.product_applicability)
        meta["chunk_id"] = c.chunk.id
        meta["is_parent"] = c.chunk.is_parent
        return {k: v for k, v in meta.items() if v is not None}

    def _build_filter(self, filters: DocumentFilter | None) -> dict[str, Any]:
        conditions: list[dict[str, Any]] = [{"deprecated": False}]
        if filters and filters.document_type:
            conditions.append({"document_type": {"$in": filters.document_type}})
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _to_results(res: Any) -> list[SearchResult]:
        out: list[SearchResult] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, _id in enumerate(ids):
            meta = metas[i] or {}
            distance = dists[i] if i < len(dists) else 0.0
            out.append(
                SearchResult(
                    chunk_id=meta.get("chunk_id", _id),
                    text=docs[i] if i < len(docs) else "",
                    score=1.0 - float(distance),  # cosine distance -> similarity
                    metadata=meta,
                    rank=i,
                )
            )
        return out

    @staticmethod
    def _meta_to_chunk(text: str, meta: dict[str, Any]) -> Chunk:
        data = dict(meta)
        if isinstance(data.get("product_applicability"), str):
            data["product_applicability"] = [
                p for p in data["product_applicability"].split(",") if p
            ]
        meta_fields = ChunkMetadata.model_fields.keys()
        meta_data = {k: v for k, v in data.items() if k in meta_fields}
        return Chunk(
            id=data.get("chunk_id", ""),
            text=text,
            metadata=ChunkMetadata(**meta_data),
            is_parent=bool(data.get("is_parent", False)),
        )
