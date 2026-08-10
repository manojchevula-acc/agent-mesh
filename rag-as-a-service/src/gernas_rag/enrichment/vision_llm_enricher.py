"""Vision-LLM enricher — describes a figure/table image as text at ingest.

Deliberately talks to the Anthropic/OpenAI SDK directly rather than going through
``llm/base.py::BaseLLM`` (whose ``Message.content`` is widened only for the
hydration path). Credentials are reused from ``LLMConfig`` so no new secret is
provisioned. The prompt keeps every quantity on a strict transcription footing —
inference is confined to describing relationships the image visibly draws, and
is barred from producing numbers — so the indexed text stays auditable and
hallucination-resistant (POC §13.7). See ``_TRANSCRIBE_PROMPT`` for why.
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
#
# The middle section ("Reading") exists because numbers alone are not what a
# chart *means* to a reader. A caption that lists 2019: 12%, 2020: 15%, 2021:
# 21% contains the trend but never says it, so a question asking whether
# exposure "increased", "worsened", "peaked" or "is trending upward" shares no
# vocabulary with the chunk and the figure loses both retrievers before the
# cross-encoder sees it — the same MODALITY_RECALL_GAP the Summary section was
# added to close, in its second form: shape questions rather than topic
# questions. It is also what a human takes away from a chart, so a generator
# answering "how has X moved" from the Details table alone has to re-derive it.
#
# The hallucination risk this reopens is bounded deliberately: Reading may only
# state relationships that are *visible as marks* (a line going up, one bar
# taller than another, a branch feeding into a box), never causes, forecasts or
# recommendations — and it must not print a number the image doesn't, because a
# computed delta ("a 9-point rise") is both an invented value and a
# CAPTION_NUMERIC_HALLUCINATED finding in eval stage 2. Directional words are
# free; arithmetic is not.
#
# The context text appended in ``enrich()`` (below) exists for the same reason
# extraction lost page 11 of a document once already: ``get_image()`` crops
# exactly the figure/table's own bounding box, and a title or caption commonly
# sits in a *separate* layout block immediately above or below it — outside
# that box. A chart titled "... (Q3-2022 to Q2-2024)" with the years printed
# nowhere inside the plot area itself indexes with no date at all if that title
# is never handed to the model in some form. It cannot be recovered from pixels
# it was never shown, so it has to arrive as text: ``DoclingExtractor`` resolves
# the layout model's own caption link (``item.captions``) and falls back to the
# nearest heading, and the prompt instructs the model to fold a date/label/figure
# number found there into Summary or Details — but never to restate the rest of
# it, which would just dilute the embedding with heading text every neighbouring
# chunk already carries.
#
# The final section ("Plotted series") exists because "transcribe only what is
# legibly printed" is the wrong rule for a line or bar chart: the values are
# encoded as *positions*, never printed, so obeying it literally indexes a
# description of the chart with none of its data. Stage 2b caught this directly —
# four figures failed CAPTION_NUMERIC_MISSING on whole series (15.8, 16.4, 16.9,
# 17.3 ... printed nowhere but plotted on every point), and gold questions like
# "what was the override rate in April" are unanswerable from such a caption even
# when the chunk is retrieved. Reading a point against the axis is therefore
# required, labelled "Approximate readings:" so the two kinds of number stay
# distinguishable. The no-arithmetic rule above is unchanged and still absolute:
# estimating a plotted point is observation, deriving a delta is invention.
_TRANSCRIBE_PROMPT = (
    "Transcribe and interpret this {element_type} from a banking policy document.\n\n"
    "Reply with exactly these three sections and no preamble:\n\n"
    "Summary: one or two plain-prose sentences naming what this {element_type} "
    "covers and the subjects, metrics and categories it names, phrased the way a "
    "policy reader would ask about it. Use the document's own terminology and "
    "spell out abbreviations where the image does.\n\n"
    "Reading: two to four plain-prose sentences stating what a reader would take "
    "away from the shape of this {element_type} — not its numbers again. Say the "
    "direction of each series or measure over its axis (rising, falling, flat, "
    "volatile, peaking then declining), which categories are highest and lowest "
    "and which are close together, where series cross over or diverge, any "
    "step-change or outlier, and the overall pattern of the distribution. For a "
    "table, compare rows and columns the same way (which row leads, which "
    "direction values move down a column). For a flowchart, process diagram or "
    "org chart, describe the structure instead: what flows into what, the "
    "direction of the sequence, where it branches or loops, and what the "
    "terminal states are. Use the ordinary words a person would use when asked "
    "about a trend or comparison, so the description matches how someone asks "
    "about it.\n\n"
    "Rules for Reading: describe only relationships that are visibly plotted or "
    "drawn. Do not explain causes, predict future values, judge whether the "
    "pattern is good or bad, or bring in outside knowledge. Do not state any "
    "number that is not printed on the image — refer to printed values if "
    "needed, but never calculate differences, growth rates, totals or averages. "
    "If the {element_type} shows no trend, comparison or flow (a single value, a "
    "logo, a decorative graphic), write 'Reading: none' and move on.\n\n"
    "Details: every visible label, unit, row/column header, axis and numeric "
    "value exactly as shown. Do not infer or summarise — transcribe only what is "
    "legibly printed. If a value is illegible, write [illegible] rather than "
    "guessing.\n\n"
    "Plotted series: when a chart encodes values by position rather than "
    "printing them — a line, bar, area or scatter plot — also list every data "
    "point, one line per series, giving a value for each x-axis category read "
    "against the axis ticks and gridlines (for example 'Override Rate: Jan 14, "
    "Feb 16, Mar 18, Apr 21, May 19, Jun 17'). Give the value of any dashed "
    "reference or threshold line as well. State the range spanned by the "
    "axis's own printed tick labels specifically — its lowest labelled number "
    "to its highest labelled number (e.g. 'Y-axis range: 90-160') — never every "
    "intermediate tick in between, and never the rendered edge of the plot area "
    "if a line or bar extends past the outermost label (a chart auto-scales to "
    "fit its data, so the drawn axis commonly runs past the last printed tick; "
    "report only what is actually printed as a number on the axis). "
    "Introduce these readings "
    "with 'Approximate readings:' so they are not mistaken for printed figures. "
    "This is the one place a value you read off the axis is wanted; it is still "
    "not licence to calculate — never state a difference, total, average or "
    "growth rate. If every point is already printed as a data label, transcribe "
    "those and do not estimate."
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
            prompt += (
                f"\n\nDocument section / caption (surrounding text, not part of the "
                f"image): {item.context_text}\n"
                "If this names a date, period, figure/table number or series this "
                "image covers that is not itself printed inside the image, state it "
                "once in Summary or Details — the title or caption often sits in its "
                "own block outside the cropped region and would otherwise be lost "
                "entirely. Do not restate the rest of this context, and never treat "
                "it as something to verify the image against."
            )
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
