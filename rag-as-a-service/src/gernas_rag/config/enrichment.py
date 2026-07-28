"""Multimodal enrichment configuration (ingest-time image captioning).

Approach (A) — image-as-text: at ingest, figures and low-confidence tables are
described by a vision LLM and the description is embedded as an ordinary text
chunk, while the original image is kept in the artifact store for optional
answer-time hydration. Disabled by default — turning ``enabled`` on is the only
switch needed to start producing media chunks.
"""

from pydantic import BaseModel


class EnrichmentConfig(BaseModel):
    enabled: bool = False  # Safe default — existing ingestion behaviour is unchanged until opted in.
    provider: str = "anthropic"  # 'anthropic' | 'openai'
    vlm_model_name: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024
    timeout_seconds: int = 20
    min_image_bytes: int = 2048  # Skip decorative logos/rules/icons below this size.
    table_confidence_threshold: float = 0.7  # Docling table-structure confidence below which a VLM pass is used.
    max_concurrent: int = 4  # Bounded concurrency for VLM calls per document.
    images_scale: float = 2.0  # Docling picture rasterisation scale when enrichment is on.

    # 0 = always atomic (one chunk per media element). >0 splits oversized table
    # captions into row-group chunks sharing one artifact_ref. Keep <= embedding.max_length.
    max_media_chunk_tokens: int = 0
