"""Enrichment abstract base class and shared dataclasses.

An enricher turns an image (figure or table crop) into a text caption suitable
for embedding. Same shape as BaseEmbedder / BaseLLM / BaseExtractor — one
abstract method with dataclass I/O.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EnrichmentInput:
    image_bytes: bytes
    mime_type: str  # "image/png"
    element_type: str  # "figure" | "table"
    context_text: str = ""  # Nearest heading / existing Docling caption, if any.
    confidence: float | None = None  # Docling table-structure confidence (tables only).


@dataclass
class EnrichmentOutput:
    caption_text: str
    model_name: str | None
    ok: bool  # False on fail-soft degrade or "no enrichment needed".


class BaseEnricher(ABC):
    @abstractmethod
    async def enrich(self, item: EnrichmentInput) -> EnrichmentOutput:
        """Describe an image region as text. Must never raise — degrade to ok=False."""
        ...
