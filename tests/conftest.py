"""Shared test fixtures and in-memory fakes for external dependencies."""

import sys
from pathlib import Path

import pytest

# Make the src layout importable without installation.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gernas_rag.config.settings import Settings  # noqa: E402
from gernas_rag.embeddings.base import BaseEmbedder, EmbeddingOutput  # noqa: E402
from gernas_rag.extraction.base import (  # noqa: E402
    ElementType,
    ExtractedElement,
    ExtractionResult,
)
from gernas_rag.llm.base import BaseLLM, Message  # noqa: E402
from gernas_rag.models.chunk import Chunk, ChunkMetadata, EmbeddedChunk  # noqa: E402
from gernas_rag.models.retrieval import DocumentFilter  # noqa: E402
from gernas_rag.vectordb.base import BaseVectorDB, SearchResult  # noqa: E402


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
    async def generate(self, messages: list[Message]) -> str:
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return f"FAKE-ANSWER based on {len(user)} chars of context."

    async def health_check(self) -> bool:
        return True


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        api_key=None,
        redis_enabled=False,
    )


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_vectordb() -> FakeVectorDB:
    return FakeVectorDB()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def sample_chunk() -> Chunk:
    meta = ChunkMetadata(document_name="doc", document_type="pricing_policy")
    return Chunk(id="abc123", text="Sample clause text", metadata=meta)


@pytest.fixture
def sample_extraction() -> ExtractionResult:
    markdown = (
        "# Pricing Policy\n\n"
        "## 4.2 Pricing floors\n\n"
        "4.2.1 The minimum floor for a BB-rated 3-5 year AED corporate term loan "
        "is 260 basis points over FTP. This clause applies to all corporate term "
        "loans denominated in AED.\n\n"
        "## 5.1 Approval authority\n\n"
        "A BBB-rated AED 25-100M facility requires approval from the Segment Credit "
        "Head before disbursement under the delegated authority matrix.\n"
    )
    elements = [
        ExtractedElement(ElementType.HEADING, "Pricing Policy", level=1),
        ExtractedElement(ElementType.PARAGRAPH, "4.2.1 ...", level=0),
    ]
    return ExtractionResult(
        elements=elements, raw_markdown=markdown, page_count=1, file_path="doc.pdf"
    )
