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
    provider: str = "anthropic"  # 'anthropic' | 'openai' | 'openai_compat'
    vlm_model_name: str = "claude-haiku-4-5-20251001"
    # Only used when provider='openai_compat' — points the OpenAI SDK at a free-tier
    # vision endpoint (e.g. Gemini's OpenAI-compatible API) instead of api.openai.com.
    # Independent of llm.openai_base_url so this can differ from the primary answer LLM.
    base_url: str | None = None
    # Dense multi-series charts need real headroom for an exhaustive transcription
    # (every axis, tick, legend entry, row) — 1024 clipped mid-list on ~40% of the
    # POC corpus. 4096 is comfortably under embedding.max_length in raw token terms
    # for typical figures; row-group splitting (max_media_chunk_tokens) exists for
    # the rare table that's still bigger than this.
    max_tokens: int = 4096
    timeout_seconds: int = 20
    min_image_bytes: int = 2048  # Skip decorative logos/rules/icons below this size.
    table_confidence_threshold: float = 0.7  # Docling table-structure confidence below which a VLM pass is used.
    max_concurrent: int = 4  # Bounded concurrency for VLM calls per document.
    images_scale: float = 2.0  # Docling picture rasterisation scale when enrichment is on.

    # 0 = always atomic (one chunk per media element). >0 splits oversized table
    # captions into row-group chunks sharing one artifact_ref. Keep <= embedding.max_length.
    max_media_chunk_tokens: int = 0
