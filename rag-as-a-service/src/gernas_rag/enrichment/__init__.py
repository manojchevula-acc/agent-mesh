"""Multimodal enrichment — vision-LLM captioning of figures/tables at ingest."""

from .base import BaseEnricher, EnrichmentInput, EnrichmentOutput
from .factory import get_enricher

__all__ = [
    "BaseEnricher",
    "EnrichmentInput",
    "EnrichmentOutput",
    "get_enricher",
]
