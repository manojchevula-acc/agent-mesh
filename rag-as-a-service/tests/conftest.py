"""Shared test fixtures.

The in-memory fakes live in tests/fakes.py so test modules can import them
directly; ``tests/`` is inserted into sys.path below.
"""

import sys
from pathlib import Path

import pytest

# Make the src layout and the tests dir importable without installation.
_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT.parent / "src"
for _path in (_SRC, _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fakes import (  # noqa: E402
    FakeAssetStore,
    FakeEmbedder,
    FakeImageStore,
    FakeLLM,
    FakeMultimodalEmbedder,
    FakeVectorDB,
)
from gernas_rag.config.settings import Settings  # noqa: E402
from gernas_rag.extraction.base import (  # noqa: E402
    ElementType,
    ExtractedElement,
    ExtractionResult,
)
from gernas_rag.models.asset import ImageAsset  # noqa: E402
from gernas_rag.models.chunk import Chunk, ChunkMetadata  # noqa: E402

__all__ = [
    "FakeAssetStore",
    "FakeEmbedder",
    "FakeImageStore",
    "FakeLLM",
    "FakeMultimodalEmbedder",
    "FakeVectorDB",
    "make_image_asset",
]


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
def llm_factory():
    """The FakeLLM class itself, for tests that need custom constructor args."""
    return FakeLLM


@pytest.fixture
def image_asset_factory():
    return make_image_asset


@pytest.fixture
def fake_multimodal_embedder() -> FakeMultimodalEmbedder:
    return FakeMultimodalEmbedder()


@pytest.fixture
def fake_image_store() -> FakeImageStore:
    return FakeImageStore()


@pytest.fixture
def fake_asset_store() -> FakeAssetStore:
    return FakeAssetStore()


@pytest.fixture
def sample_table_markdown() -> str:
    """A 6-row pricing table embedded in prose — the D8 fixture."""
    return (
        "# Pricing Policy\n\n"
        "## 4.2 Pricing floors\n\n"
        "Table 3: Minimum pricing floors by rating\n\n"
        "| Rating | Tenor | AED bps | USD bps |\n"
        "| ------ | ----- | ------- | ------- |\n"
        "| AAA    | 1-3y  | 120     | 130     |\n"
        "| AA     | 1-3y  | 150     | 160     |\n"
        "| A      | 3-5y  | 210     | 220     |\n"
        "| BBB    | 3-5y  | 310     | 325     |\n"
        "| BB     | 3-5y  | 260     | 275     |\n"
        "| B      | 5-7y  | 450     | 470     |\n\n"
        "The floors above apply to all corporate term loans denominated in AED. "
        "Any deviation requires Segment Credit Head approval under the delegated "
        "authority matrix set out in section 5.1 of this policy document.\n"
    )


def make_image_asset(asset_id: str, concept: str, **overrides) -> ImageAsset:
    """Build a minimal ImageAsset for tests."""
    defaults = dict(
        id=asset_id,
        content_sha256=asset_id * 2,
        phash="0" * 16,
        document_name="doc",
        document_type="pricing_policy",
        page_number=1,
        width=400,
        height=300,
        uri=f"/api/v1/assets/{asset_id}",
        storage_path=f"memory://{asset_id}",
        caption=concept,
    )
    defaults.update(overrides)
    return ImageAsset(**defaults)


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
