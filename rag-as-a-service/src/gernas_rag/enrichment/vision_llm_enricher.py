"""Vision-LLM enricher — describes a figure/table image as text at ingest.

Deliberately talks to the Anthropic/OpenAI SDK directly rather than going through
``llm/base.py::BaseLLM`` (whose ``Message.content`` is widened only for the
hydration path). Credentials are reused from ``LLMConfig`` so no new secret is
provisioned. The prompt instructs transcription, not inference, to keep the
indexed text auditable and hallucination-resistant (POC §13.7).
"""

import base64
from typing import Any

from ..config.enrichment import EnrichmentConfig
from ..utils.logging import get_logger
from .base import BaseEnricher, EnrichmentInput, EnrichmentOutput

logger = get_logger(__name__)

_TRANSCRIBE_PROMPT = (
    "Transcribe this {element_type} from a banking policy document. "
    "List every visible label, unit, row/column header, axis, and numeric value "
    "exactly as shown. Do not infer or summarise trends — transcribe only what is "
    "legibly printed. If a value is illegible, write [illegible] rather than guessing."
)


class VisionLLMEnricher(BaseEnricher):
    def __init__(self, config: EnrichmentConfig, api_key: str | None) -> None:
        self._config = config
        self._api_key = api_key
        self._client: Any | None = None  # Lazy — mirrors BGEM3Embedder / Reranker.

    def _load(self) -> None:
        if self._client is not None:
            return
        if self._config.provider == "openai":
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key)
        else:  # anthropic (default)
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def enrich(self, item: EnrichmentInput) -> EnrichmentOutput:
        prompt = _TRANSCRIBE_PROMPT.format(element_type=item.element_type)
        if item.context_text:
            prompt += f"\n\nDocument section: {item.context_text}"
        try:
            self._load()
            b64 = base64.b64encode(item.image_bytes).decode("ascii")
            if self._config.provider == "openai":
                text = await self._call_openai(b64, item.mime_type, prompt)
            else:
                text = await self._call_anthropic(b64, item.mime_type, prompt)
            return EnrichmentOutput(
                caption_text=text.strip(),
                model_name=self._config.vlm_model_name,
                ok=bool(text.strip()),
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, never break ingestion.
            logger.warning(
                "VLM enrichment failed; degrading to source text",
                model=self._config.vlm_model_name,
                error=str(exc),
            )
            return EnrichmentOutput(caption_text="", model_name=None, ok=False)

    async def _call_anthropic(self, b64: str, mime_type: str, prompt: str) -> str:
        response = await self._client.messages.create(
            model=self._config.vlm_model_name,
            max_tokens=self._config.max_tokens,
            timeout=float(self._config.timeout_seconds),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime_type, "data": b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")

    async def _call_openai(self, b64: str, mime_type: str, prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._config.vlm_model_name,
            max_tokens=self._config.max_tokens,
            timeout=float(self._config.timeout_seconds),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        return response.choices[0].message.content or ""
