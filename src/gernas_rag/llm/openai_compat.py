"""OpenAI-compatible LLM implementation (vLLM, TGI, LocalAI, etc.)."""

from ..config.llm import LLMConfig
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .base import BaseLLM, Message

logger = get_logger(__name__)


class OpenAICompatLLM(BaseLLM):
    """Any OpenAI-compatible chat completions endpoint via the async OpenAI SDK."""

    def __init__(self, config: LLMConfig) -> None:
        from openai import AsyncOpenAI

        self._config = config
        self._client = AsyncOpenAI(
            base_url=config.openai_base_url,
            api_key=config.openai_api_key or "not-needed",
            timeout=float(config.timeout_seconds),
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
        logger.info("OpenAI-compat generation complete", model=self._config.model_name, chars=len(content))
        return content

    async def health_check(self) -> bool:
        return bool(self._config.openai_base_url)
