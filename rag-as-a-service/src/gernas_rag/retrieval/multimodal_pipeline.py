"""MultimodalRetrievalPipeline — orchestrates the text and image branches.

WRAPS the existing RetrievalPipeline rather than modifying it. With images off it
is a pass-through, so the text path is provably unchanged (asserted by
tests/integration/test_text_regression.py).
"""

import asyncio
import base64
import time

from ..config.multimodal import FusionMode
from ..config.settings import Settings
from ..embeddings.multimodal.base import BaseMultimodalEmbedder
from ..models.retrieval import (
    ImageQuery,
    RetrievedImage,
    RetrieveRequest,
    RetrieveResponse,
)
from ..utils.logging import get_logger
from ..vectordb.image_store import BaseImageStore, ImageSearchResult
from .fusion import gate_images
from .intent import ImageIntentRouter
from .pipeline import RetrievalPipeline

logger = get_logger(__name__)


class MultimodalRetrievalPipeline:
    def __init__(
        self,
        settings: Settings,
        text_pipeline: RetrievalPipeline,
        multimodal_embedder: BaseMultimodalEmbedder | None = None,
        image_store: BaseImageStore | None = None,
        score_floor: float = 0.10,
    ) -> None:
        self._settings = settings
        self._text = text_pipeline
        self._embedder = multimodal_embedder
        self._image_store = image_store
        self._router = ImageIntentRouter(settings.multimodal.retrieval)
        self._registry_floor = score_floor
        self._enabled = (
            settings.multimodal.enabled
            and multimodal_embedder is not None
            and image_store is not None
        )

    async def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        cfg = self._settings.multimodal.retrieval
        want_images = (
            self._enabled
            and cfg.mode is not FusionMode.OFF
            and self._router.wants_images(request)
        )
        if not want_images:
            # Exactly today's behaviour — same object, same fields, same order.
            return await self._text.retrieve(request)

        start = time.perf_counter()
        # The branches are independent, so run them concurrently. On CPU they
        # contend for the same thread pool, so the saving is partial but real:
        # the image ANN and its Qdrant round-trip overlap the text rerank.
        text_task = asyncio.create_task(self._text.retrieve(request))
        image_task = asyncio.create_task(self._image_branch(request))
        text_response, image_results = await asyncio.gather(
            text_task, image_task, return_exceptions=True
        )

        if isinstance(text_response, BaseException):
            raise text_response  # A text failure IS fatal.
        if isinstance(image_results, BaseException):
            # The image branch is an enhancement, never a hard dependency.
            logger.warning(
                "Image branch failed; returning text-only", error=str(image_results)
            )
            image_results = []

        images = [self._to_retrieved(r, i) for i, r in enumerate(image_results)]
        images = self._promote_table_crops(text_response, images)

        if cfg.mode is FusionMode.UNIFIED_RRF:
            text_response = self._apply_unified_order(text_response, image_results)

        latency_ms = text_response.latency_ms + (time.perf_counter() - start) * 1000
        logger.info(
            "Multimodal retrieval complete",
            query=request.query[:80],
            text_chunks=len(text_response.chunks),
            images=len(images),
            mode=cfg.mode.value,
        )
        return text_response.model_copy(
            update={
                "images": images,
                "image_search_performed": True,
                "multimodal_space_id": self._embedder.space.space_id if self._embedder else None,
                "latency_ms": round(latency_ms, 2),
            }
        )

    # ── Image branch ──────────────────────────────────────────────────
    async def _image_branch(self, request: RetrieveRequest) -> list[ImageSearchResult]:
        cfg = self._settings.multimodal.retrieval
        assert self._embedder is not None and self._image_store is not None

        # Query vector: TEXT tower for text->image, VISION tower for image->image.
        # Both land in the same space, which is the whole point of the design.
        if request.query_image is not None:
            if not cfg.enable_image_query:
                raise ValueError(
                    "Image queries are disabled (multimodal.retrieval.enable_image_query)"
                )
            image_bytes = await self._load_query_image(request.query_image)
            output = await self._embedder.embed_image_query(image_bytes)
        else:
            output = await self._embedder.embed_query(request.query)

        raw = await self._image_store.dense_search(
            output.dense_vectors[0], cfg.image_top_k, request.filters
        )
        floor = (
            cfg.image_score_floor
            if cfg.image_score_floor is not None
            else self._registry_floor
        )
        return gate_images(raw, floor, cfg.image_score_margin_ratio, cfg.image_final_k)

    async def _load_query_image(self, query: ImageQuery) -> bytes:
        cfg = self._settings.multimodal.retrieval
        if query.asset_id:
            assert self._image_store is not None
            assets = await self._image_store.get_by_ids([query.asset_id])
            if not assets:
                raise ValueError(f"Unknown asset_id: {query.asset_id}")
            from ..images.store import get_asset_store

            store = get_asset_store(self._settings.multimodal.storage)
            return store.get(query.asset_id)
        if query.image_base64:
            data = base64.b64decode(query.image_base64, validate=True)
            if len(data) > cfg.max_query_image_bytes:
                raise ValueError("Query image exceeds max_query_image_bytes")
            return data
        raise ValueError("query_image requires either asset_id or image_base64")

    # ── Fusion helpers ────────────────────────────────────────────────
    def _promote_table_crops(
        self, response: RetrieveResponse, images: list[RetrievedImage]
    ) -> list[RetrievedImage]:
        """Surface the rendered crop of any retrieved table chunk.

        The TEXT retriever — with sparse matching on cell values like '310' and
        'BBB' — is better at finding the right table than the visual retriever
        is, so a table chunk hit is the highest-confidence signal available.
        """
        if not self._settings.llm.vision_enabled or self._image_store is None:
            return images

        cfg = self._settings.multimodal.retrieval
        present = {im.asset_id for im in images}
        promoted: list[RetrievedImage] = []
        for chunk in response.chunks:
            if chunk.content_type != "table" or not chunk.asset_id:
                continue
            if chunk.asset_id in present:
                continue
            present.add(chunk.asset_id)
            promoted.append(
                RetrievedImage(
                    asset_id=chunk.asset_id,
                    uri=f"{self._settings.multimodal.storage.serve_base_url.rstrip('/')}"
                    f"/{chunk.asset_id}",
                    source=chunk.source,
                    page_number=chunk.page_number,
                    caption=chunk.section_heading,
                    nearest_heading=chunk.section_heading,
                    role="table_image",
                    score=chunk.score,
                    promoted_from_text=True,
                )
            )

        budget = min(cfg.max_images_in_context, self._settings.llm.vision_max_images)
        # Promoted crops lead: they came from the higher-precision signal.
        return (promoted + images)[:budget] if promoted else images[:budget]

    def _apply_unified_order(
        self, response: RetrieveResponse, image_results: list[ImageSearchResult]
    ) -> RetrieveResponse:
        """Reorder text chunks by fused rank.

        In unified mode images compete with text for the context budget, so a
        strong image can displace a weak chunk. Not the default for exactly that
        reason.
        """
        from .fusion import rrf_fuse
        from ..vectordb.base import SearchResult

        cfg = self._settings.multimodal.retrieval
        pseudo = [
            SearchResult(chunk_id=str(i), text=c.text, score=c.score, metadata={}, rank=i)
            for i, c in enumerate(response.chunks)
        ]
        fused = rrf_fuse(pseudo, image_results, cfg.rrf_k, cfg.text_weight, cfg.image_weight)
        order = [int(f.identifier) for f in fused if f.kind == "text"]
        reordered = [response.chunks[i] for i in order if i < len(response.chunks)]
        return response.model_copy(update={"chunks": reordered})

    def _to_retrieved(self, result: ImageSearchResult, rank: int) -> RetrievedImage:
        p = result.payload
        return RetrievedImage(
            asset_id=p.get("id", result.asset_id),
            uri=p.get("uri", ""),
            thumbnail_uri=p.get("thumbnail_uri"),
            source=p.get("document_name", ""),
            page_number=p.get("page_number"),
            caption=p.get("caption", ""),
            nearest_heading=p.get("nearest_heading", ""),
            role=p.get("role", "unknown"),
            score=result.score,
            rank=rank,
            width=p.get("width", 0),
            height=p.get("height", 0),
            effective_date=p.get("effective_date", ""),
            freshness_warning=float(p.get("freshness_score", 1.0)) < 0.7,
        )
