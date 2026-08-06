"""Multimodal embedder factory — the single construction seam.

Licence and trust_remote_code gates live here rather than in documentation: in a
regulated environment, a config typo that silently pulls a non-commercial model
into production is a real incident, so we fail closed at startup.
"""

import os

from ...config.multimodal import MultimodalEmbeddingConfig
from ...utils.logging import get_logger
from .base import BaseMultimodalEmbedder
from .registry import ModelSpec, get_provider_class, resolve_spec

logger = get_logger(__name__)

# Set to "1" to acknowledge a non-commercial licence for research/benchmarking.
NC_OVERRIDE_ENV = "RAG__ALLOW_NON_COMMERCIAL_MODELS"


def get_multimodal_embedder(config: MultimodalEmbeddingConfig) -> BaseMultimodalEmbedder:
    spec = resolve_spec(
        config.model_name,
        config.provider.value if config.provider else None,
    )
    _enforce_gates(config, spec)

    cls = get_provider_class(spec.provider)
    logger.info(
        "Building multimodal embedder",
        provider=spec.provider,
        model=spec.hf_id,
        licence=spec.licence,
    )
    return cls(config, spec)


def _enforce_gates(config: MultimodalEmbeddingConfig, spec: ModelSpec) -> None:
    if not spec.commercial_use and os.getenv(NC_OVERRIDE_ENV) != "1":
        raise ValueError(
            f"Model '{spec.hf_id}' is licensed '{spec.licence}' (non-commercial). "
            f"Set {NC_OVERRIDE_ENV}=1 to use it for research/benchmarking only, "
            "after Legal sign-off."
        )
    if spec.trust_remote_code and not config.trust_remote_code:
        raise ValueError(
            f"Model '{spec.hf_id}' requires trust_remote_code=True. Set "
            "multimodal.embedding.trust_remote_code: true explicitly to accept "
            "executing model-authored code from the Hub."
        )


def registry_score_floor(config: MultimodalEmbeddingConfig) -> float:
    """Per-model default relevance floor, used when config leaves it null."""
    return resolve_spec(
        config.model_name, config.provider.value if config.provider else None
    ).score_floor
