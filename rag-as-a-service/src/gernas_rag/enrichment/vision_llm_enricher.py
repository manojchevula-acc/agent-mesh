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
from ..utils.retry import async_retry
from .base import BaseEnricher, EnrichmentInput, EnrichmentOutput

logger = get_logger(__name__)

# A caption is two surfaces at once, and they have opposite requirements.
#
# The *retrieval* surface is whatever the embedder and cross-encoder see. A
# pure verbatim transcription is nearly all layout scaffolding ("### Title &
# Subtitle", "#### Main Process Flow Boxes (Left to Right)", "| --- | --- |"),
# which shares almost no vocabulary with a natural-language question — figures
# measurably under-retrieved against prose chunks (eval stage 3
# MODALITY_RECALL_GAP: figure hit@5 0.69 vs text 0.81), and the questions that
# missed entirely were ones whose answer lives in a figure.
#
# The *answer* surface is what the generator reads to state a number, and there
# a transcription is exactly right — inference is how a caption invents a value
# the image never showed.
#
# So: ask for a prose lead the retriever can match, then the verbatim data the
# generator can quote, and keep the no-inference rule on the part that carries
# the numbers. The "no preamble" instruction drops the "Here is the exact
# transcription of..." boilerplate that otherwise opens every caption and
# dilutes the embedding before a single real term appears.
_TRANSCRIBE_PROMPT = (
    "Transcribe this {element_type} from a banking policy document.\n\n"
    "Reply with exactly these two sections and no preamble:\n\n"
    "Summary: one or two plain-prose sentences naming what this {element_type} "
    "covers and the subjects, metrics and categories it names, phrased the way a "
    "policy reader would ask about it. Use the document's own terminology and "
    "spell out abbreviations where the image does. Describe only what is visibly "
    "labelled — no trends, conclusions or commentary.\n\n"
    "Details: every visible label, unit, row/column header, axis and numeric "
    "value exactly as shown. Do not infer or summarise — transcribe only what is "
    "legibly printed. If a value is illegible, write [illegible] rather than "
    "guessing."
)


class VisionLLMEnricher(BaseEnricher):
    def __init__(self, config: EnrichmentConfig, api_key: str | None) -> None:
        self._config = config
        self._api_key = api_key
        self._client: Any | None = None  # Lazy — mirrors BGEM3Embedder / Reranker.

    def _load(self) -> None:
        if self._client is not None:
            return
        if self._config.provider in ("openai", "openai_compat"):
            from openai import AsyncOpenAI

            # base_url is None for real OpenAI; set it to target a free-tier
            # vision endpoint instead (e.g. Gemini's OpenAI-compatible API).
            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._config.base_url)
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
            if self._config.provider in ("openai", "openai_compat"):
                text, truncated = await self._call_openai(b64, item.mime_type, prompt)
            else:
                text, truncated = await self._call_anthropic(b64, item.mime_type, prompt)
            if truncated:
                # Fail-soft keeps the partial caption (better than nothing) but this
                # must be loud: a silently clipped transcription reads as complete
                # and nobody would think to check it against the source image.
                logger.warning(
                    "VLM caption hit max_tokens and was truncated mid-transcription; "
                    "raise enrichment.max_tokens if this recurs",
                    model=self._config.vlm_model_name,
                    max_tokens=self._config.max_tokens,
                    caption_chars=len(text),
                )
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

    # Every other LLM call-site in this codebase retries transient failures
    # (see anthropic_llm.py, groq_llm.py, openai_compat.py) — this one didn't,
    # so a single network timeout permanently dropped an otherwise-fine figure
    # (observed: "Request timed out." on a 20s budget, no second attempt, image
    # left orphaned). Same policy as everywhere else: 3 attempts, 1s/2s backoff.
    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def _call_anthropic(self, b64: str, mime_type: str, prompt: str) -> tuple[str, bool]:
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
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return text, response.stop_reason == "max_tokens"

    @async_retry(max_attempts=3, backoff_factor=2.0)
    async def _call_openai(self, b64: str, mime_type: str, prompt: str) -> tuple[str, bool]:
        kwargs: dict[str, Any] = {}
        if self._is_gemini():
            # Gemini "thinking" models spend part of max_tokens on hidden reasoning
            # tokens before writing any visible output — observed eating ~960 of a
            # 1024-token budget on a single transcription call, leaving only a few
            # dozen tokens for the actual answer, even though finish_reason correctly
            # reports "length". This is a pure transcription task with nothing to
            # reason about, so turn thinking down as far as this endpoint allows
            # ("none" is rejected here; "low" is the minimum it accepts).
            kwargs["reasoning_effort"] = "low"
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
            **kwargs,
        )
        choice = response.choices[0]
        return choice.message.content or "", choice.finish_reason == "length"

    def _is_gemini(self) -> bool:
        return "generativelanguage.googleapis.com" in (self._config.base_url or "")
