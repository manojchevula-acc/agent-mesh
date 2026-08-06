"""Qdrant implementation of the image collection.

Mirrors QdrantVectorDB's conventions: AsyncQdrantClient, UUIDv5 point ids,
@async_retry on the write/read paths, payload indexes on the filter fields.
"""

from typing import Any

from ..config.vectordb import VectorDBConfig
from ..models.asset import EmbeddedImage, ImageAsset
from ..models.retrieval import DocumentFilter
from ..utils.hashing import make_point_uuid
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .image_store import BaseImageStore, ImageSearchResult

logger = get_logger(__name__)

_DISTANCE_MAP = {"cosine": "COSINE", "dot": "DOT", "euclidean": "EUCLID"}

# Mirrors the text collection's filter surface so one DocumentFilter works
# unchanged against both indexes.
_PAYLOAD_INDEXES = [
    ("asset_id", "keyword"),
    ("document_name", "keyword"),
    ("document_type", "keyword"),
    ("product_applicability", "keyword"),
    ("deprecated", "bool"),
    ("effective_date", "keyword"),
    ("page_number", "integer"),
    ("role", "keyword"),
    ("parent_chunk_id", "keyword"),
    ("space_id", "keyword"),
]


class QdrantImageStore(BaseImageStore):
    def __init__(
        self,
        config: VectorDBConfig,
        collection_name: str,
        client: Any | None = None,
    ) -> None:
        """``client`` shares an existing connection.

        This is REQUIRED in embedded mode (``qdrant_path`` set): the local engine
        takes an exclusive lock on the storage directory, so opening a second
        client on the same path raises "already accessed by another instance".
        In server mode a separate client is harmless, but sharing is still
        cheaper.
        """
        from qdrant_client import AsyncQdrantClient

        self._config = config
        self._collection = collection_name
        if client is not None:
            self._client = client
        elif config.qdrant_path:
            self._client = AsyncQdrantClient(path=config.qdrant_path)
        else:
            self._client = AsyncQdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key,
                prefer_grpc=config.qdrant_prefer_grpc,
            )

    @property
    def collection_name(self) -> str:
        return self._collection

    # ── Collection management ─────────────────────────────────────────
    async def create_collection(self, name: str, dim: int, metric: str = "cosine") -> None:
        from qdrant_client.models import Distance, VectorParams

        if await self._client.collection_exists(name):
            # Dimension drift check — refuse to serve a mismatched collection
            # rather than silently returning nonsense neighbours.
            existing = await self._existing_dim(name)
            if existing is not None and existing != dim:
                raise ValueError(
                    f"Collection '{name}' has dim {existing} but the configured "
                    f"model produces {dim}. Delete/recreate it, or change the model."
                )
            logger.info("Image collection already exists", collection=name)
            return

        distance = getattr(Distance, _DISTANCE_MAP.get(metric, "COSINE"))
        await self._client.create_collection(
            collection_name=name,
            # Single named 'dense' vector; NO sparse config — CLIP-family
            # encoders emit no lexical vector.
            vectors_config={"dense": VectorParams(size=dim, distance=distance)},
            on_disk_payload=self._config.on_disk_payload,
            replication_factor=self._config.replication_factor,
        )
        for field_name, field_type in _PAYLOAD_INDEXES:
            await self._client.create_payload_index(name, field_name, field_type)
        logger.info("Image collection created", collection=name, dim=dim)

    async def _existing_dim(self, name: str) -> int | None:
        try:
            info = await self._client.get_collection(name)
            vectors = info.config.params.vectors
            if isinstance(vectors, dict):
                return int(vectors["dense"].size)
            return int(vectors.size)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read collection dim", collection=name, error=str(exc))
            return None

    async def delete_collection(self, name: str) -> None:
        await self._client.delete_collection(collection_name=name)

    # ── Write path ────────────────────────────────────────────────────
    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def upsert_images(self, images: list[EmbeddedImage]) -> int:
        from qdrant_client.models import PointStruct

        if not images:
            return 0
        points = [
            PointStruct(
                # Keyed by space AND asset so the same image indexed under two
                # spaces can never collide if collections are ever merged.
                id=make_point_uuid(f"{img.space_id}:{img.asset.id}"),
                vector={"dense": img.dense_vector},
                payload={**img.asset.model_dump(mode="json"), "space_id": img.space_id},
            )
            for img in images
        ]
        await self._client.upsert(collection_name=self._collection, points=points)
        logger.info("Upserted image points", collection=self._collection, count=len(points))
        return len(points)

    # ── Read path ─────────────────────────────────────────────────────
    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def dense_search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[ImageSearchResult]:
        response = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            using="dense",
            query_filter=self._build_filter(filters),
            limit=top_k,
            with_payload=True,
        )
        return [
            ImageSearchResult(
                asset_id=(p.payload or {}).get("id", str(p.id)),
                score=float(p.score),
                payload=p.payload or {},
                rank=i,
            )
            for i, p in enumerate(response.points)
        ]

    async def get_by_ids(self, asset_ids: list[str]) -> list[ImageAsset]:
        if not asset_ids:
            return []
        from qdrant_client.models import FieldCondition, Filter, MatchAny

        response = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="asset_id", match=MatchAny(any=asset_ids))]
            ),
            limit=len(asset_ids),
            with_payload=True,
        )
        records = response[0] if isinstance(response, tuple) else response
        return [self._payload_to_asset(r.payload) for r in records if r.payload]

    async def count(self) -> int:
        try:
            result = await self._client.count(collection_name=self._collection, exact=True)
            return int(result.count)
        except Exception:  # noqa: BLE001 - count is diagnostic only
            return 0

    async def delete_by_document(self, document_name: str) -> int:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        await self._client.delete(
            collection_name=self._collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_name", match=MatchValue(value=document_name)
                        )
                    ]
                )
            ),
        )
        logger.info("Deleted images for document", document=document_name)
        return 1

    async def health_check(self) -> bool:
        try:
            return await self._client.collection_exists(self._collection)
        except Exception:  # noqa: BLE001
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
    def _payload_to_asset(payload: dict[str, Any]) -> ImageAsset:
        fields = ImageAsset.model_fields.keys()
        return ImageAsset(**{k: v for k, v in payload.items() if k in fields})
