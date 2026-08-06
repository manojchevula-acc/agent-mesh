"""Registry, catalogue resolution and the licence / trust_remote_code gates."""

import pytest

from gernas_rag.config.multimodal import MultimodalEmbeddingConfig, MultimodalProvider
from gernas_rag.embeddings.multimodal.factory import (
    NC_OVERRIDE_ENV,
    get_multimodal_embedder,
    registry_score_floor,
)
from gernas_rag.embeddings.multimodal.registry import (
    get_provider_class,
    register_provider,
    registered_providers,
    resolve_spec,
)


# ── Catalogue resolution ─────────────────────────────────────────────────
def test_alias_resolves():
    spec = resolve_spec("siglip2-base")
    assert spec.hf_id == "google/siglip2-base-patch16-224"
    assert spec.dim == 768
    assert spec.provider == "hf_dual_encoder"
    assert spec.commercial_use is True


def test_hf_id_resolves_to_the_same_spec():
    assert resolve_spec("google/siglip2-base-patch16-224").alias == "siglip2-base"


def test_unknown_model_falls_back_without_raising():
    """A brand-new Hub checkpoint must work with no code or registry change."""
    spec = resolve_spec("some-org/brand-new-siglip3")
    assert spec.provider == "hf_dual_encoder"
    assert spec.dim is None  # probed at load instead


def test_provider_override_wins():
    spec = resolve_spec("siglip2-base", provider_override="open_clip")
    assert spec.provider == "open_clip"
    assert spec.hf_id == "google/siglip2-base-patch16-224"


def test_registry_score_floor_is_per_model():
    assert registry_score_floor(MultimodalEmbeddingConfig(model_name="siglip2-base")) == 0.10
    # CLIP is softmax-trained, so its similarity band differs and needs a higher floor.
    assert registry_score_floor(MultimodalEmbeddingConfig(model_name="openclip-b32")) == 0.20


# ── Provider registry ────────────────────────────────────────────────────
def test_core_provider_is_registered():
    assert "hf_dual_encoder" in registered_providers()
    assert get_provider_class("hf_dual_encoder").__name__ == "HFDualEncoderEmbedder"


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown multimodal provider"):
        get_provider_class("nonexistent_provider")


def test_duplicate_registration_raises():
    @register_provider("test_only_provider")
    class _First:  # noqa: D401
        pass

    with pytest.raises(ValueError, match="Duplicate multimodal provider"):

        @register_provider("test_only_provider")
        class _Second:
            pass


def test_re_registering_the_same_class_is_idempotent():
    """Module re-import must not explode."""

    @register_provider("test_idempotent_provider")
    class _Provider:
        pass

    register_provider("test_idempotent_provider")(_Provider)  # no raise


# ── Gates (fail closed) ──────────────────────────────────────────────────
def test_non_commercial_model_is_blocked(monkeypatch):
    monkeypatch.delenv(NC_OVERRIDE_ENV, raising=False)
    config = MultimodalEmbeddingConfig(model_name="jina-clip-v2", trust_remote_code=True)
    with pytest.raises(ValueError, match="non-commercial"):
        get_multimodal_embedder(config)


def test_non_commercial_model_allowed_with_explicit_override(monkeypatch):
    monkeypatch.setenv(NC_OVERRIDE_ENV, "1")
    config = MultimodalEmbeddingConfig(model_name="jina-clip-v2", trust_remote_code=True)
    # Construction is lazy — no weights are downloaded here.
    assert get_multimodal_embedder(config) is not None


def test_trust_remote_code_must_be_explicit(monkeypatch):
    monkeypatch.setenv(NC_OVERRIDE_ENV, "1")
    config = MultimodalEmbeddingConfig(model_name="jina-clip-v2", trust_remote_code=False)
    with pytest.raises(ValueError, match="trust_remote_code"):
        get_multimodal_embedder(config)


def test_default_model_needs_no_gates(monkeypatch):
    monkeypatch.delenv(NC_OVERRIDE_ENV, raising=False)
    config = MultimodalEmbeddingConfig()  # siglip2-base
    assert get_multimodal_embedder(config) is not None


def test_provider_enum_is_separate_from_text_providers():
    """Adding values to EmbeddingProvider would silently change reranker
    selection in retrieval/pipeline.py — hence a separate enum."""
    from gernas_rag.config.embedding import EmbeddingProvider

    text_values = {p.value for p in EmbeddingProvider}
    mm_values = {p.value for p in MultimodalProvider}
    assert not (text_values & mm_values)
