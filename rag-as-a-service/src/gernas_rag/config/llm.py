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
