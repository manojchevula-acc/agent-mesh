"""Unit tests for the content-addressed artifact store."""

import pytest

from gernas_rag.storage.artifact_store import LocalArtifactStore


@pytest.mark.asyncio
async def test_put_returns_content_addressed_ref(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref = await store.put_bytes(b"hello-image-bytes", "image/png")
    assert ref.startswith("sha256:")
    assert ref.endswith(".png")


@pytest.mark.asyncio
async def test_put_is_idempotent(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref1 = await store.put_bytes(b"same-bytes", "image/png")
    ref2 = await store.put_bytes(b"same-bytes", "image/png")
    assert ref1 == ref2
    # Only one file on disk for identical content.
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_distinct_bytes_get_distinct_refs(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref_a = await store.put_bytes(b"image-a", "image/png")
    ref_b = await store.put_bytes(b"image-b", "image/png")
    assert ref_a != ref_b


@pytest.mark.asyncio
async def test_round_trip(tmp_path):
    store = LocalArtifactStore(tmp_path)
    data = b"round-trip-bytes"
    ref = await store.put_bytes(data, "image/png")
    got, mime = await store.get_bytes(ref)
    assert got == data
    assert mime == "image/png"


@pytest.mark.asyncio
async def test_get_missing_raises(tmp_path):
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        await store.get_bytes("sha256:deadbeef.png")
