"""Content-addressed asset storage, including the path-traversal guard."""

import pytest

from gernas_rag.config.multimodal import AssetStorageConfig
from gernas_rag.images.store import LocalAssetStore, get_asset_store
from gernas_rag.utils.hashing import make_asset_id, make_space_id, slugify_model


@pytest.fixture
def store(tmp_path) -> LocalAssetStore:
    return LocalAssetStore(AssetStorageConfig(root=str(tmp_path), image_format="WEBP"))


# ── Content addressing ───────────────────────────────────────────────────
def test_same_bytes_produce_one_file(store, tmp_path):
    first = store.put(b"identical-bytes")
    second = store.put(b"identical-bytes")
    assert first.asset_id == second.asset_id
    assert len(list(tmp_path.rglob("*.webp"))) == 1


def test_different_bytes_produce_different_ids(store):
    assert store.put(b"one").asset_id != store.put(b"two").asset_id


def test_asset_id_is_32_hex_chars():
    asset_id = make_asset_id(b"payload")
    assert len(asset_id) == 32
    assert all(c in "0123456789abcdef" for c in asset_id)


def test_roundtrip(store):
    stored = store.put(b"figure-bytes")
    assert store.get(stored.asset_id) == b"figure-bytes"


def test_thumbnail_roundtrip(store):
    stored = store.put(b"figure-bytes", thumbnail=b"thumb-bytes")
    assert store.get_thumbnail(stored.asset_id) == b"thumb-bytes"
    assert stored.thumb_uri is not None


def test_thumbnail_falls_back_to_full_image(store):
    stored = store.put(b"no-thumb")
    assert store.get_thumbnail(stored.asset_id) == b"no-thumb"


# ── Security ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "not-hex-at-all",
        "abc",  # too short
        "A" * 32,  # uppercase
        "",
        "a" * 31 + "/",
    ],
)
def test_invalid_asset_ids_are_rejected(store, bad_id):
    """asset_id arrives from an HTTP path parameter — this is a security control."""
    with pytest.raises(ValueError, match="Invalid asset id"):
        store.get(bad_id)


def test_exists_returns_false_for_invalid_ids(store):
    assert store.exists("../../etc/passwd") is False


# ── Filesystem hygiene ───────────────────────────────────────────────────
def test_no_temp_files_left_behind(store, tmp_path):
    store.put(b"payload", thumbnail=b"thumb")
    assert list(tmp_path.rglob("*.tmp")) == []


def test_sharded_by_first_two_chars(store, tmp_path):
    stored = store.put(b"payload")
    assert (tmp_path / stored.asset_id[:2]).is_dir()


def test_delete_removes_both_files(store):
    stored = store.put(b"payload", thumbnail=b"thumb")
    store.delete(stored.asset_id)
    assert not store.exists(stored.asset_id)


def test_delete_is_idempotent(store):
    store.delete("a" * 32)  # must not raise


def test_unsupported_backend_raises():
    with pytest.raises(ValueError, match="Unsupported asset storage backend"):
        get_asset_store(AssetStorageConfig(backend="s3"))


# ── Space identity ───────────────────────────────────────────────────────
def test_space_id_changes_with_every_component():
    base = ("hf_dual_encoder", "google/siglip2-base-patch16-224", None, 768, True, "cosine")
    baseline = make_space_id(*base)
    assert make_space_id("open_clip", *base[1:]) != baseline
    assert make_space_id(base[0], "other/model", *base[2:]) != baseline
    assert make_space_id(*base[:2], "abc123", *base[3:]) != baseline
    assert make_space_id(*base[:3], 512, *base[4:]) != baseline
    assert make_space_id(*base[:4], False, base[5]) != baseline
    assert make_space_id(*base[:5], "dot") != baseline


def test_space_id_is_stable():
    args = ("hf_dual_encoder", "google/siglip2-base-patch16-224", None, 768, True, "cosine")
    assert make_space_id(*args) == make_space_id(*args)


def test_collection_names_never_collide_across_models():
    from gernas_rag.embeddings.base import EmbeddingSpace

    def name(model: str, dim: int) -> str:
        return EmbeddingSpace(
            space_id="x", provider="p", model_name=model, dim=dim
        ).collection_name("imgs")

    names = {
        name("google/siglip2-base-patch16-224", 768),
        name("google/siglip2-base-patch16-512", 768),
        name("laion/CLIP-ViT-B-32-laion2B-s34B-b79K", 512),
    }
    assert len(names) == 3
    assert "imgs__siglip2_base_patch16_224__d768" in names


def test_slugify_model():
    assert slugify_model("google/siglip2-base-patch16-224") == "siglip2_base_patch16_224"
    assert slugify_model("BAAI/BGE-VL-base") == "bge_vl_base"
