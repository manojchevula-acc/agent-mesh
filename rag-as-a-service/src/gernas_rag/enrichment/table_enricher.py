"""Table enricher — confidence-gated wrapper around the vision enricher.

Most born-digital pricing grids are parsed accurately by Docling and don't need a
vision pass; only low-confidence tables are re-transcribed by the VLM. The primary
gate happens at extraction (an image crop is only attached to low-confidence
tables), and this wrapper re-checks the confidence as defence-in-depth so a
high-confidence table is never sent to the VLM even if a crop leaks through.
Figures always go straight to the VLM.
"""

from .base import BaseEnricher, EnrichmentInput, EnrichmentOutput


class TableEnricher(BaseEnricher):
    def __init__(self, vlm: BaseEnricher, confidence_threshold: float) -> None:
        self._vlm = vlm
        self._threshold = confidence_threshold

    async def enrich(self, item: EnrichmentInput) -> EnrichmentOutput:
        if (
            item.element_type == "table"
            and item.confidence is not None
            and item.confidence >= self._threshold
        ):
            # Docling already parsed this table well — keep its text, no VLM call.
            return EnrichmentOutput(caption_text="", model_name=None, ok=False)
        return await self._vlm.enrich(item)
