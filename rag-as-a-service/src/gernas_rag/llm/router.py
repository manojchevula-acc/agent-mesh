"""Routes each generation to the cheapest model that can serve it.

Sending every query to the vision model would be wasteful: most queries are pure
text, which the text model answers faster and more cheaply, and the vision model
is a PREVIEW model whose availability is not guaranteed.
"""

from .base import BaseLLM, Message
from ..utils.logging import get_logger

logger = get_logger(__name__)


class VisionRouter(BaseLLM):
    """Presents the BaseLLM interface and picks the backend per call.

    Implementing BaseLLM means ResponseGenerator is unaware routing exists — it
    holds one ``BaseLLM`` exactly as it does today.
    """

    def __init__(
        self,
        text_llm: BaseLLM,
        vision_llm: BaseLLM | None = None,
        fallback_to_text: bool = True,
    ) -> None:
        self._text = text_llm
        self._vision = vision_llm
        self._fallback = fallback_to_text

    @property
    def supports_vision(self) -> bool:
        return self._vision is not None

    async def generate(self, messages: list[Message]) -> str:
        if not any(m.has_images for m in messages):
            return await self._text.generate(messages)  # unchanged path

        if self._vision is None:
            if not self._fallback:
                raise ValueError("Vision generation requested but not configured")
            logger.warning("Vision model not configured; degrading to text")
            return await self._text.generate([m.text_only() for m in messages])

        try:
            return await self._vision.generate(messages)
        except Exception as exc:  # noqa: BLE001
            # Preview-model risk: deprecation, rate limit, outage. Degrading to
            # a caption-based answer beats failing the request.
            if not self._fallback:
                raise
            logger.warning(
                "Vision generation failed; falling back to text",
                model=getattr(self._vision, "model_name", "?"),
                error=str(exc),
            )
            return await self._text.generate([m.text_only() for m in messages])

    async def health_check(self) -> bool:
        return await self._text.health_check()
