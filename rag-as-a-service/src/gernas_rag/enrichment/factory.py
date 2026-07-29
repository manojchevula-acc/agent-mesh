"""Enrichment factory — builds the enricher from config, reusing LLM credentials."""

from ..config.enrichment import EnrichmentConfig
from ..config.llm import LLMConfig
from .base import BaseEnricher
from .table_enricher import TableEnricher
from .vision_llm_enricher import VisionLLMEnricher


def get_enricher(config: EnrichmentConfig, llm_config: LLMConfig) -> BaseEnricher:
    """Return a confidence-gated vision enricher.

    The VLM credentials are reused from ``LLMConfig`` (the same keys used for
    answer generation), so enabling enrichment needs no new secret.
    """
    if config.provider == "anthropic":
        api_key = llm_config.anthropic_api_key
    elif config.provider in ("openai", "openai_compat"):
        # 'openai_compat' reuses the same generic key field — point config.base_url
        # at a free-tier vision endpoint (e.g. Gemini) to use a different key/host
        # than the primary answer LLM.
        api_key = llm_config.openai_api_key
    else:
        raise ValueError(f"Unsupported enrichment provider: {config.provider}")
    vlm = VisionLLMEnricher(config, api_key)
    return TableEnricher(vlm, config.table_confidence_threshold)
