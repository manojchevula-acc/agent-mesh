"""Answer-time hydration configuration.

Hydration loads the original image back from the artifact store (by
``artifact_ref``) and passes it to a vision-capable LLM during answer synthesis.
It is independent of ingest-time enrichment: you can build media chunks
(enrichment) while answering from their text summaries only (hydration off).
Off by default; ``enabled=false`` is a proven no-op.
"""

from pydantic import BaseModel, Field


class HydrationConfig(BaseModel):
    enabled: bool = False  # Master switch — off by default.
    mode: str = "conditional"  # 'off' | 'conditional' | 'always'
    trigger_modalities: list[str] = Field(
        default_factory=lambda: ["figure", "table", "page_image"]
    )
    vision_provider: str = "anthropic"  # Separate from the primary answer LLM.
    vision_model_name: str = "claude-sonnet-5"  # Vision-capable model.
    max_image_bytes: int = 5_000_000  # Skip oversize artifacts; keep the text summary.
