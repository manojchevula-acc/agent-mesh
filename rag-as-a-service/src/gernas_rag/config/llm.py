"""LLM provider configuration."""

from enum import Enum

from pydantic import BaseModel


class LLMProvider(str, Enum):
    GROQ = "groq"
    ANTHROPIC = "anthropic"
    HUGGINGFACE = "huggingface"
    OPENAI_COMPAT = "openai_compat"


class LLMConfig(BaseModel):
    provider: str = "groq"  # 'groq' | 'anthropic' | 'huggingface' | 'openai_compat'
    model_name: str = "openai/gpt-oss-120b"
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_seconds: int = 30

    # Provider-specific
    groq_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    hf_model_id: str = "mistralai/Mistral-7B-Instruct-v0.2"
    hf_device: str = "cpu"

    # ── Vision generation (D7) ────────────────────────────────────────
    # Routing is dynamic: only requests that carry images use the vision model,
    # so text-only queries keep today's model, latency and cost exactly.
    vision_enabled: bool = False
    vision_model_name: str = "qwen/qwen3.6-27b"  # Only Groq VLM as of 2026-08
    vision_max_images: int = 3  # Groq docs conflict (3 vs 5) — take the lower
    vision_max_tokens: int = 3072  # Figure answers run longer
    vision_timeout_seconds: int = 60  # Image prefill is slower than text
    vision_fallback_to_text: bool = True  # Degrade, never fail

    # Payload shaping — applied to the stored asset before base64 encoding.
    vision_image_max_side_px: int = 768  # Main lever on image token cost
    vision_image_format: str = "JPEG"  # Groq's documented example uses image/jpeg
    vision_image_quality: int = 85
