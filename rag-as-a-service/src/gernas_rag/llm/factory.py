"""LLM factory.

With ``vision_enabled=false`` this returns exactly what it returned before: the
plain text LLM, with no router in the path.
"""

from ..config.llm import LLMConfig
from ..utils.logging import get_logger
from .base import BaseLLM

logger = get_logger(__name__)


def _build(config: LLMConfig, model_override: str | None = None) -> BaseLLM:
    provider = config.provider.lower()
    match provider:
        case "groq":
            from .groq_llm import GroqLLM

            return GroqLLM(config, model_override=model_override)
        case "anthropic":
            from .anthropic_llm import AnthropicLLM

            return AnthropicLLM(config)
        case "huggingface":
            from .huggingface_llm import HuggingFaceLLM

            return HuggingFaceLLM(config)
        case "openai_compat":
            from .openai_compat import OpenAICompatLLM

            return OpenAICompatLLM(config)
        case _:
            raise ValueError(f"Unsupported LLM provider: {config.provider}")


def get_llm(config: LLMConfig) -> BaseLLM:
    text_llm = _build(config)
    if not config.vision_enabled:
        return text_llm  # identical to the pre-vision behaviour

    if config.provider.lower() != "groq":
        logger.warning(
            "Vision generation is only implemented for Groq; falling back to text-only",
            provider=config.provider,
        )
        return text_llm

    from .router import VisionRouter

    vision_llm = _build(config, model_override=config.vision_model_name)
    logger.info(
        "Vision routing enabled",
        text_model=config.model_name,
        vision_model=config.vision_model_name,
    )
    return VisionRouter(text_llm, vision_llm, config.vision_fallback_to_text)
