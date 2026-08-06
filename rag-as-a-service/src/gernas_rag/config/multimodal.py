"""Multimodal (image + text shared-space) configuration.

A new top-level ``multimodal:`` block rather than nested keys under ``embedding:``
— the flat ``settings.embedding`` block is consumed in four places and nesting it
would break all of them.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ── Providers ────────────────────────────────────────────────────────────
class MultimodalProvider(str, Enum):
    """Backend that knows how to load and call a family of dual encoders.

    Deliberately a SEPARATE enum from ``EmbeddingProvider``: adding values there
    would silently change reranker selection in ``retrieval/pipeline.py``.
    """

    HF_DUAL_ENCODER = "hf_dual_encoder"  # SigLIP, SigLIP-2, CLIP via transformers
    OPEN_CLIP = "open_clip"  # open_clip_torch (LAION checkpoints)
    SENTENCE_TRANSFORMER = "st"  # sentence-transformers (jina-clip-v2, ST CLIP)
    BGE_VL = "bge_vl"  # BAAI BGE-VL custom API


class ImageExtractionBackend(str, Enum):
    PYMUPDF = "pymupdf"  # Fast, raster + bbox, no caption linkage
    DOCLING = "docling"  # Structure-aware, caption linkage, heavier
    AUTO = "auto"  # Docling when the text extractor is Docling, else PyMuPDF


class FusionMode(str, Enum):
    OFF = "off"
    SIDE_CAR = "side_car"
    UNIFIED_RRF = "unified_rrf"


class ImageIntent(str, Enum):
    ALWAYS = "always"
    NEVER = "never"
    HEURISTIC = "heuristic"


# ── Embedding ────────────────────────────────────────────────────────────
class MultimodalEmbeddingConfig(BaseModel):
    """The only block an operator must edit to swap models."""

    type: Literal["multimodal"] = "multimodal"

    # ``provider`` may be omitted — it is then resolved from model_registry.yaml.
    provider: MultimodalProvider | None = None
    model_name: str = "google/siglip2-base-patch16-224"
    revision: str | None = None  # Pin for reproducibility; None = main

    # ── Runtime ──────────────────────────────────────────────────────
    device: str = "cpu"  # 'cpu' | 'cuda' | 'mps'
    dtype: str = "float32"  # 'float32' | 'bfloat16' | 'float16'
    torch_num_threads: int | None = None  # None => min(8, cpu_count // 2)
    quantize_dynamic_int8: bool = False  # CPU-only speedup for Linear layers
    trust_remote_code: bool = False  # Must be explicit per model
    local_files_only: bool = False  # Air-gapped operation
    cache_dir: str | None = None  # HF cache override

    # ── Vector semantics ─────────────────────────────────────────────
    embedding_dim: int | None = None  # None => probed at load, then asserted
    truncate_dim: int | None = None  # Matryoshka truncation (jina-clip-v2)
    normalize: bool = True  # L2-normalise => cosine == dot
    distance_metric: str = "cosine"

    # ── Throughput ───────────────────────────────────────────────────
    text_batch_size: int = 16
    image_batch_size: int = 8
    max_text_length: int | None = None  # None => model default (SigLIP-2: 64)

    # ── Lifecycle ────────────────────────────────────────────────────
    lazy_load: bool = True  # Load weights on first use, not at boot
    warmup_on_start: bool = False  # Pay the first-call cost at startup instead
    embedding_cache_enabled: bool = True
    embedding_cache_ttl_seconds: int = 604800  # 7 days

    @model_validator(mode="after")
    def _validate_device_dtype(self) -> "MultimodalEmbeddingConfig":
        if self.device == "cpu" and self.dtype == "float16":
            # fp16 on x86 CPU is emulated and typically SLOWER than fp32.
            raise ValueError(
                "dtype=float16 is not supported on CPU; use float32 "
                "(or bfloat16 on AVX512-BF16 hardware)."
            )
        return self


# ── Image extraction ─────────────────────────────────────────────────────
class ImageExtractionConfig(BaseModel):
    enabled: bool = True
    backend: ImageExtractionBackend = ImageExtractionBackend.AUTO

    # Rejection thresholds — tuned to drop logos, rules, bullets, icons.
    min_width: int = 96
    min_height: int = 96
    min_area_px: int = 20_000  # ~140x140
    max_aspect_ratio: float = 12.0  # Rejects header rules / separator bars
    near_uniform_std_threshold: float = 6.0  # Rejects blank / solid-fill blocks

    # Volume caps — protect ingestion latency on pathological documents.
    max_images_per_page: int = 8
    max_images_per_document: int = 200

    # Deduplication.
    dedup_exact: bool = True  # sha256 of normalised bytes
    dedup_perceptual: bool = True  # dHash
    phash_hamming_threshold: int = 4  # <= threshold => near-duplicate

    # Context resolution.
    caption_window_chars: int = 600
    write_image_stub_chunks: bool = True  # Text-collection stubs

    # ── Tables (D8) ──────────────────────────────────────────────────
    extract_table_crops: bool = True
    table_render_dpi: int = 200  # Higher than figures: small cell text must stay
    # legible after the 768px vision-payload downscale
    table_crop_pad_pt: float = 4.0

    # Full-page renders (ColPali-style groundwork). Off — expensive.
    include_page_renders: bool = False
    page_render_dpi: int = 144


# ── Storage ──────────────────────────────────────────────────────────────
class AssetStorageConfig(BaseModel):
    backend: Literal["local", "s3"] = "local"
    root: str = "./image_store"
    image_format: str = "WEBP"  # WEBP ~30% smaller than PNG at q=90
    quality: int = 90
    max_side_px: int = 1024  # Stored asset ceiling
    thumbnail_side_px: int = 320
    serve_base_url: str = "/api/v1/assets"


# ── Retrieval ────────────────────────────────────────────────────────────
class MultimodalRetrievalConfig(BaseModel):
    mode: FusionMode = FusionMode.SIDE_CAR

    image_top_k: int = 20  # Candidates pulled from the image ANN
    image_final_k: int = 4  # Returned after gating
    image_score_floor: float | None = None  # None => registry default per model
    image_score_margin_ratio: float = 0.55  # Keep i iff s_i >= ratio * s_max

    # unified_rrf only
    rrf_k: int = 60
    text_weight: float = 1.0
    image_weight: float = 0.6

    # Query routing
    image_intent: ImageIntent = ImageIntent.HEURISTIC
    intent_keywords: list[str] = Field(
        default_factory=lambda: [
            "diagram", "chart", "figure", "graph", "image", "picture", "screenshot",
            "flow", "flowchart", "workflow", "matrix", "illustration", "exhibit",
            "annexure", "appendix", "map", "layout", "architecture", "show me",
            "what does it look like", "visual", "plot", "table image", "org chart",
        ]
    )

    # Generation
    max_images_in_context: int = 3  # Image descriptors injected into the prompt
    weak_text_threshold: float = 0.15  # Below this, prioritise figures (§8.7.6)

    # Image-as-query
    enable_image_query: bool = False
    max_query_image_bytes: int = 8_388_608  # 8 MiB


# ── Root ─────────────────────────────────────────────────────────────────
class MultimodalConfig(BaseModel):
    """Top-level feature block.

    ``enabled=False`` => the service behaves exactly as it does today, with no
    new model loaded and no new collection created.
    """

    enabled: bool = False

    image_collection_base: str = "fab_gernas_images"
    # Explicit override; normally derived as {base}__{slug}__d{dim}.
    image_collection_name: str | None = None

    embedding: MultimodalEmbeddingConfig = Field(default_factory=MultimodalEmbeddingConfig)
    extraction: ImageExtractionConfig = Field(default_factory=ImageExtractionConfig)
    storage: AssetStorageConfig = Field(default_factory=AssetStorageConfig)
    retrieval: MultimodalRetrievalConfig = Field(default_factory=MultimodalRetrievalConfig)
