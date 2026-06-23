"""Groq LLM implementation."""

from ..config.llm import LLMConfig
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .base import BaseLLM, Message

logger = get_logger(__name__)


class GroqLLM(BaseLLM):
    """Groq chat completions via the async Groq SDK."""

    def __init__(self, config: LLMConfig) -> None:
        from groq import AsyncGroq

        self._config = config
        self._client = AsyncGroq(
            api_key=config.groq_api_key, timeout=config.timeout_seconds
        )

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def generate(self, messages: list[Message]) -> str:
        response = await self._client.chat.completions.create(
            model=self._config.model_name,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        content = response.choices[0].message.content or ""
        logger.info("Groq generation complete", model=self._config.model_name, chars=len(content))
        return content

    async def health_check(self) -> bool:
        return bool(self._config.groq_api_key)
