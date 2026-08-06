"""Configuration contract for the multimodal feature."""

import pytest
from pydantic import ValidationError

from gernas_rag.config.multimodal import (
    FusionMode,
    ImageIntent,
    MultimodalConfig,
    MultimodalEmbeddingConfig,
)
from gernas_rag.config.settings import Settings


def test_feature_is_off_by_default():
    """The whole design rests on this: default config == today's behaviour."""
    assert MultimodalConfig().enabled is False
    assert Settings(_env_file=None).multimodal.enabled is False


def test_defaults_match_the_design():
    config = MultimodalConfig()
    assert config.embedding.model_name == "google/siglip2-base-patch16-224"
    assert config.embedding.device == "cpu"
    assert config.embedding.dtype == "float32"
    assert config.embedding.trust_remote_code is False  # supply-chain policy
    assert config.retrieval.mode is FusionMode.SIDE_CAR
    assert config.retrieval.image_intent is ImageIntent.HEURISTIC
    assert config.extraction.extract_table_crops is True


def test_fp16_on_cpu_is_rejected():
    """fp16 is emulated on x86 CPU and typically SLOWER than fp32."""
    with pytest.raises(ValidationError, match="float16 is not supported on CPU"):
        MultimodalEmbeddingConfig(device="cpu", dtype="float16")


def test_fp16_allowed_off_cpu():
    assert MultimodalEmbeddingConfig(device="cuda", dtype="float16").dtype == "float16"


def test_table_render_dpi_exceeds_figure_dpi():
    """Cell text must survive two downscales, so tables render at higher dpi."""
    config = MultimodalConfig().extraction
    assert config.table_render_dpi > config.page_render_dpi


def test_env_override_wins_over_defaults(monkeypatch):
    monkeypatch.setenv("RAG__MULTIMODAL__ENABLED", "true")
    monkeypatch.setenv(
        "RAG__MULTIMODAL__EMBEDDING__MODEL_NAME", "google/siglip2-base-patch16-512"
    )
    settings = Settings(_env_file=None)
    assert settings.multimodal.enabled is True
    assert settings.multimodal.embedding.model_name == "google/siglip2-base-patch16-512"


def test_vision_generation_defaults_off():
    settings = Settings(_env_file=None)
    assert settings.llm.vision_enabled is False
    assert settings.llm.vision_model_name == "qwen/qwen3.6-27b"
    # Groq docs conflict (3 vs 5); we take the conservative value.
    assert settings.llm.vision_max_images == 3


def test_judge_model_is_not_the_vision_generator():
    """R11: a model must not grade its own output."""
    settings = Settings(_env_file=None)
    assert settings.evaluation.judge_model != settings.llm.vision_model_name


def test_table_protection_on_by_default():
    assert Settings(_env_file=None).chunking.protect_tables is True
