"""Anthropic LLM implementation."""

from ..config.llm import LLMConfig
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .base import BaseLLM, Message

logger = get_logger(__name__)


class AnthropicLLM(BaseLLM):
    """Anthropic Messages API via the async Anthropic SDK.

    System messages are passed via the dedicated ``system`` parameter; the rest are
    sent as the conversation turns.
    """

    def __init__(self, config: LLMConfig) -> None:
        from anthropic import AsyncAnthropic

        self._config = config
        self._client = AsyncAnthropic(
            api_key=config.anthropic_api_key, timeout=float(config.timeout_seconds)
        )

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def generate(self, messages: list[Message]) -> str:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        response = await self._client.messages.create(
            model=self._config.model_name,
            system=system or None,
            messages=turns,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        content = "".join(parts)
        logger.info("Anthropic generation complete", model=self._config.model_name, chars=len(content))
        return content

    async def health_check(self) -> bool:
        return bool(self._config.anthropic_api_key)
