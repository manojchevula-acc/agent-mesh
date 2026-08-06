"""In-memory fakes for external dependencies.

A real module rather than conftest-only definitions, so test modules can import
them directly (``tests/`` is placed on sys.path by conftest.py).
"""

import hashlib
import math
import random
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gernas_rag.embeddings.base import (  # noqa: E402
    BaseEmbedder,
    EmbeddingOutput,
    EmbeddingSpace,
)
from gernas_rag.embeddings.multimodal.base import BaseMultimodalEmbedder  # noqa: E402
from gernas_rag.images.store import BaseAssetStore, StoredAsset  # noqa: E402
from gernas_rag.llm.base import BaseLLM, Message  # noqa: E402
from gernas_rag.models.asset import EmbeddedImage, ImageAsset  # noqa: E402
from gernas_rag.models.chunk import Chunk, EmbeddedChunk  # noqa: E402
from gernas_rag.models.retrieval import DocumentFilter  # noqa: E402
from gernas_rag.vectordb.base import BaseVectorDB, SearchResult  # noqa: E402
from gernas_rag.vectordb.image_store import BaseImageStore, ImageSearchResult  # noqa: E402


# ── Fakes ─────────────────────────────────────────────────────────────
class FakeEmbedder(BaseEmbedder):
    """Deterministic embedder — hashes text into a small dense + sparse vector."""

    def __init__(self, dim: int = 8, sparse: bool = True) -> None:
        self._dim = dim
        self._sparse = sparse

    def _vec(self, text: str) -> list[float]:
        seed = sum(ord(c) for c in text) or 1
        return [((seed * (i + 1)) % 97) / 97.0 for i in range(self._dim)]

    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        dense = [self._vec(t) for t in texts]
        if self._sparse:
            indices = [[1, 2, 3] for _ in texts]
            values = [[0.5, 0.3, 0.2] for _ in texts]
        else:
            indices, values = [], []
        return EmbeddingOutput(dense_vectors=dense, sparse_indices=indices, sparse_values=values)

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    @property
    def dense_dim(self) -> int:
        return self._dim

    @property
    def supports_sparse(self) -> bool:
        return self._sparse


class FakeVectorDB(BaseVectorDB):
    """In-memory vector DB. Stores embedded chunks and returns them by insertion order."""

    def __init__(self) -> None:
        self.store: dict[str, EmbeddedChunk] = {}
        self.collections: set[str] = set()

    async def create_collection(self, name: str, dense_dim: int) -> None:
        self.collections.add(name)

    async def delete_collection(self, name: str) -> None:
        self.collections.discard(name)
        self.store.clear()

    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        for c in chunks:
            self.store[c.chunk.id] = c
        return len(chunks)

    def _results(self, top_k: int, filters: DocumentFilter | None) -> list[SearchResult]:
        out: list[SearchResult] = []
        for i, ec in enumerate(self.store.values()):
            if ec.chunk.is_parent:
                continue
            meta = {**ec.chunk.metadata.model_dump(mode="json"), "chunk_id": ec.chunk.id}
            out.append(SearchResult(ec.chunk.id, ec.chunk.text, 1.0 - i * 0.01, meta, i))
            if len(out) >= top_k:
                break
        return out

    async def dense_search(self, query_vector, top_k, filters=None):
        return self._results(top_k, filters)

    async def sparse_search(self, query_indices, query_values, top_k, filters=None):
        return self._results(top_k, filters)

    async def get_by_ids(self, ids: list[str]) -> list[Chunk]:
        return [self.store[i].chunk for i in ids if i in self.store]

    async def health_check(self) -> bool:
        return True


class FakeLLM(BaseLLM):
    """Records the last message list so tests can assert on the request shape."""

    def __init__(self, supports_vision: bool = False, fail: bool = False) -> None:
        self._supports_vision = supports_vision
        self._fail = fail
        self.last_messages: list[Message] | None = None
        self.call_count = 0

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision

    async def generate(self, messages: list[Message]) -> str:
        self.last_messages = messages
        self.call_count += 1
        if self._fail:
            raise RuntimeError("simulated vision model outage")
        user = next((m for m in reversed(messages) if m.role == "user"), None)
        text = user.flatten() if user else ""
        return f"FAKE-ANSWER based on {len(text)} chars of context."

    async def health_check(self) -> bool:
        return True


def _concept(text: str) -> str:
    """Extract the 'concept' token a fake vector is keyed on."""
    lowered = str(text).lower()
    for concept in ("bar chart", "org chart", "flow diagram", "rate table", "building"):
        if concept in lowered:
            return concept
    return lowered.strip()[:32]


class FakeMultimodalEmbedder(BaseMultimodalEmbedder):
    """Deterministic dual encoder for tests.

    Crucially it fakes ALIGNMENT: text and images sharing a 'concept' map to the
    same unit vector, so gating and fusion logic can be exercised end-to-end
    without downloading weights.
    """

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim
        self.loaded = False
        self._space_obj = EmbeddingSpace(
            space_id="fake0000",
            provider="fake",
            model_name="fake/multimodal",
            revision=None,
            dim=dim,
            modalities=frozenset({"text", "image"}),
        )

    def _vec(self, concept: str) -> list[float]:
        seed = int(hashlib.md5(concept.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        values = [rng.gauss(0, 1) for _ in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]  # L2-normalised, like the real thing

    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        return EmbeddingOutput(dense_vectors=[self._vec(_concept(t)) for t in texts])

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    async def embed_images(self, images) -> EmbeddingOutput:
        # Test images are labelled by their bytes/filename so a matching text
        # query lands on the same vector.
        return EmbeddingOutput(
            dense_vectors=[self._vec(_concept(_label_of(i))) for i in images]
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space_obj

    def load(self) -> None:
        self.loaded = True

    async def health_check(self) -> bool:
        return True


def _label_of(image) -> str:
    if isinstance(image, bytes):
        return image.decode("utf-8", errors="ignore")
    return str(image)


class FakeImageStore(BaseImageStore):
    """In-memory image index. Ranks by TRUE cosine so gating thresholds are
    exercised for real rather than being short-circuited."""

    def __init__(self) -> None:
        self.store: dict[str, EmbeddedImage] = {}
        self.collections: dict[str, int] = {}

    async def create_collection(self, name: str, dim: int, metric: str = "cosine") -> None:
        self.collections[name] = dim

    async def upsert_images(self, images: list[EmbeddedImage]) -> int:
        for img in images:
            self.store[img.asset.id] = img
        return len(images)

    async def dense_search(self, query_vector, top_k, filters=None):
        scored = []
        for asset_id, img in self.store.items():
            score = sum(a * b for a, b in zip(query_vector, img.dense_vector))
            payload = img.asset.model_dump(mode="json")
            scored.append(ImageSearchResult(asset_id, float(score), payload))
        scored.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(scored):
            r.rank = i
        return scored[:top_k]

    async def get_by_ids(self, asset_ids: list[str]) -> list[ImageAsset]:
        return [self.store[i].asset for i in asset_ids if i in self.store]

    async def count(self) -> int:
        return len(self.store)

    async def delete_by_document(self, document_name: str) -> int:
        removed = [k for k, v in self.store.items() if v.asset.document_name == document_name]
        for k in removed:
            del self.store[k]
        return len(removed)

    async def health_check(self) -> bool:
        return True


class FakeAssetStore(BaseAssetStore):
    """dict-backed — no filesystem I/O in unit tests."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.thumbs: dict[str, bytes] = {}

    def put(self, data: bytes, thumbnail: bytes | None = None) -> StoredAsset:
        from gernas_rag.utils.hashing import make_asset_id

        asset_id = make_asset_id(data)
        self.blobs[asset_id] = data
        if thumbnail is not None:
            self.thumbs[asset_id] = thumbnail
        return StoredAsset(
            asset_id=asset_id,
            uri=f"/api/v1/assets/{asset_id}",
            path=f"memory://{asset_id}",
            thumb_uri=f"/api/v1/assets/{asset_id}/thumb" if thumbnail else None,
            byte_size=len(data),
        )

    def get(self, asset_id: str) -> bytes:
        if asset_id not in self.blobs:
            raise FileNotFoundError(asset_id)
        return self.blobs[asset_id]

    def get_thumbnail(self, asset_id: str) -> bytes:
        return self.thumbs.get(asset_id) or self.get(asset_id)

    def exists(self, asset_id: str) -> bool:
        return asset_id in self.blobs

    def delete(self, asset_id: str) -> None:
        self.blobs.pop(asset_id, None)
        self.thumbs.pop(asset_id, None)
