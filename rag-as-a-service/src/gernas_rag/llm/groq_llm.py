"""Groq LLM implementation — handles both text-only and vision requests."""

from ..config.llm import LLMConfig
from ..utils.logging import get_logger
from ..utils.retry import async_retry
from .base import BaseLLM, ImagePart, Message, TextPart

logger = get_logger(__name__)


class GroqLLM(BaseLLM):
    """Groq chat completions via the async Groq SDK.

    The router constructs two instances: one for ``model_name`` and one for
    ``vision_model_name``. Timeouts and token budgets differ because image
    prefill is slower and figure answers run longer.
    """

    def __init__(self, config: LLMConfig, model_override: str | None = None) -> None:
        from groq import AsyncGroq

        self._config = config
        self._model = model_override or config.model_name
        self._is_vision = bool(model_override) and model_override == config.vision_model_name
        timeout = (
            config.vision_timeout_seconds if self._is_vision else config.timeout_seconds
        )
        self._client = AsyncGroq(api_key=config.groq_api_key, timeout=timeout)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def supports_vision(self) -> bool:
        return self._is_vision

    @staticmethod
    def _serialise(message: Message) -> dict:
        """Message -> Groq/OpenAI chat format.

        A plain string stays a plain string, so text-only requests are
        byte-identical to the ones this service sends today.
        """
        if isinstance(message.content, str):
            return {"role": message.role, "content": message.content}

        parts: list[dict] = []
        for part in message.content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                parts.append({"type": "image_url", "image_url": {"url": part.data_uri}})
        return {"role": message.role, "content": parts}

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def generate(self, messages: list[Message]) -> str:
        has_images = any(m.has_images for m in messages)
        if has_images and not self._is_vision:
            raise ValueError(
                f"Model '{self._model}' cannot accept images. Route through "
                "VisionRouter, or set llm.vision_model_name."
            )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[self._serialise(m) for m in messages],
            temperature=self._config.temperature,
            max_tokens=(
                self._config.vision_max_tokens if has_images else self._config.max_tokens
            ),
        )
        content = response.choices[0].message.content or ""
        logger.info(
            "Groq generation complete",
            model=self._model,
            images=has_images,
            chars=len(content),
        )
        return content

    async def health_check(self) -> bool:
        return bool(self._config.groq_api_key)
