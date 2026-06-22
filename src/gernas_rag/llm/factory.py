"""LLM factory."""

from ..config.llm import LLMConfig
from .base import BaseLLM


def get_llm(config: LLMConfig) -> BaseLLM:
    provider = config.provider.lower()
    match provider:
        case "groq":
            from .groq_llm import GroqLLM

            return GroqLLM(config)
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
