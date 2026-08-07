"""ResponseGenerator — builds a grounded prompt and calls the LLM.

When hydration is enabled (§13), the generator resolves each retrieved media
chunk's ``artifact_ref`` back to image bytes and routes the request to a
vision-capable LLM — but only when the retrieved set actually contains an image,
and only on the answer-synthesis path. The text summary is always kept in context;
the image augments it, never replaces it. Hydration lives here (not in retrieval)
so the cached ``RetrieveResponse`` never carries image bytes.
"""

import re
from dataclasses import dataclass

from ..config.settings import Settings
from ..llm.base import BaseLLM, ImagePart, Message, TextPart
from ..models.retrieval import RetrievedChunk
from ..storage.artifact_store import BaseArtifactStore
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GenerationTrace:
    """What actually happened during one answer synthesis.

    Whether the vision path ran is otherwise invisible to the caller: hydration
    degrades to text silently on a missing artifact, an oversize image or an
    unconfigured vision LLM. Evaluation needs that distinction to attribute a
    failure, and production monitoring needs it to know the multimodal path is
    live at all — so it is recorded rather than inferred.
    """

    context_blocks: int = 0
    hydration_eligible: int = 0  # Chunks hydration would have loaded an image for.
    images_hydrated: int = 0  # Images actually resolved from the artifact store.
    vision_used: bool = False  # True only when the vision LLM answered.
    prompt_chars: int = 0

_SYSTEM_PROMPT = (
    "You are GERNAS, FAB's regulatory and credit-policy assistant. Answer strictly "
    "from the provided context. Each context block is numbered [1], [2], etc. "
    "Cite every factual claim with the corresponding context number(s), e.g. [1] or [2][3]. "
    "After your answer append a 'Sources:' section listing only the numbers you cited, "
    "each with the document name and section. "
    "Context blocks marked [Figure] or [Table] are machine-transcribed from images; "
    "treat their contents as authoritative text but note the transcription source if asked. "
    # A policy answer is only actionable if it carries the actual figures. Without
    # this, the model reliably describes the mechanism and omits the number — e.g.
    # naming Credit Spread as a component and citing the policy it is governed by,
    # while never stating the 65-450 bps range printed in the very chunk it cited
    # (eval stage 4, answer_numeric_recall 0.75 with the correct chunk at rank 1).
    "When the context states a specific quantity that answers the question — a rate, "
    "threshold, limit, range, deadline, duration or amount — restate it verbatim, "
    "with its unit, rather than describing it in general terms. "
    "If the context does not contain the answer, say so explicitly and do not speculate. "
    "Flag any context marked as ⚠ STALE."
)

# Inline citation markers, e.g. "[1]" or "[2][3]" — matches what the system
# prompt above tells the model to emit.
_CITATION_RE = re.compile(r"\[\s*(\d+)\s*\]")


class ResponseGenerator:
    """Assembles retrieved chunks into a grounded prompt and calls the LLM."""

    def __init__(
        self,
        settings: Settings,
        llm: BaseLLM,
        artifact_store: BaseArtifactStore | None = None,
        vision_llm: BaseLLM | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm  # Existing default (text) LLM — unchanged.
        self._hydration = settings.hydration
        self._artifact_store = artifact_store
        self._vision_llm = vision_llm  # Separate vision-capable provider.

    def _build_context(self, chunks: list[RetrievedChunk]) -> str:
        blocks: list[str] = []
        for i, c in enumerate(chunks, start=1):
            # Prefer the human-readable section heading; fall back to clause_reference.
            section_label = c.section_heading or c.clause_reference
            header = f"[{i}] Source: {c.source}"
            if section_label:
                header += f" · Section: {section_label}"
            if c.modality and c.modality != "text":
                header += f" · [{c.modality.title()}]"
            if c.effective_date:
                header += f" · Effective: {c.effective_date}"
            if c.freshness_warning:
                header += " · ⚠ STALE"
            # When a broader parent section is available, show it for context but
            # also call out the specific matched passage so the LLM knows exactly
            # which sentence triggered retrieval and can cite precisely.
            if c.parent_text and c.text != c.parent_text:
                matched = c.text[:300].strip()
                if len(c.text) > 300:
                    matched += "…"
                body = f"{c.parent_text}\n\n[Matched passage: {matched}]"
            else:
                body = c.text
            blocks.append(f"{header}\n{body}")
        return "\n\n---\n\n".join(blocks)

    def _validate_citations(self, answer: str, num_blocks: int, query: str) -> None:
        """Warn when the model cites a context number that was never supplied.

        Context blocks are numbered [1]..[num_blocks] in the prompt, but nothing
        stops the model from citing a number outside that range (stage 4 eval's
        CITATION_OUT_OF_RANGE finding caught exactly this). That citation then
        points at nothing, which is silent in production — there is no exception,
        just a reference a user could click into and find nothing. This can't be
        corrected without risking mangling an otherwise-grounded sentence, but it
        must be visible, so it is logged loudly here rather than only caught
        after the fact by the offline eval suite.
        """
        cited = {int(n) for n in _CITATION_RE.findall(answer)}
        out_of_range = sorted(n for n in cited if n < 1 or n > num_blocks)
        if out_of_range:
            logger.warning(
                "Answer cites context block(s) outside the supplied range",
                query=query[:80],
                out_of_range=out_of_range,
                context_blocks=num_blocks,
            )

    def _should_hydrate(self, chunk: RetrievedChunk) -> bool:
        if not self._hydration.enabled or self._hydration.mode == "off":
            return False
        if not chunk.artifact_ref or self._artifact_store is None:
            return False
        if self._hydration.mode == "always":
            return True
        return chunk.modality in self._hydration.trigger_modalities  # "conditional"

    async def _hydrate(self, chunks: list[RetrievedChunk]) -> list[ImagePart]:
        """Resolve artifact_ref → image bytes for the chunks that qualify.

        De-duplicates by ref (row-group table chunks can share one image) and is
        fail-soft: a missing or oversize artifact is skipped, never raised.
        """
        parts: list[ImagePart] = []
        seen: set[str] = set()
        for c in chunks:
            if not self._should_hydrate(c) or c.artifact_ref in seen:
                continue
            try:
                data, mime = await self._artifact_store.get_bytes(c.artifact_ref)
                if len(data) > self._hydration.max_image_bytes:
                    continue  # Oversize guard — keep the text summary.
                seen.add(c.artifact_ref)
                parts.append(ImagePart(bytes=data, mime_type=mime, ref=c.artifact_ref))
            except Exception as exc:  # noqa: BLE001 — fail-soft to text-only.
                logger.warning("Hydration failed; using text only", ref=c.artifact_ref, error=str(exc))
        return parts

    async def generate(self, query: str, chunks: list[RetrievedChunk]) -> str:
        answer, _ = await self.generate_with_trace(query, chunks)
        return answer

    async def generate_with_trace(
        self, query: str, chunks: list[RetrievedChunk]
    ) -> tuple[str, GenerationTrace]:
        """Same as :meth:`generate`, plus a record of which path actually ran."""
        if not chunks:
            logger.info("No chunks to generate from", query=query[:80])
            return (
                "I could not find any relevant policy context to answer this question.",
                GenerationTrace(),
            )

        context = self._build_context(chunks)
        user_text = (
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Answer using only the context above. Cite every claim with [N] numbers "
            "and end with a 'Sources:' block."
        )
        eligible = sum(1 for c in chunks if self._should_hydrate(c))

        image_parts = await self._hydrate(chunks) if self._hydration.enabled else []
        if image_parts and self._vision_llm is not None:
            content: list = [TextPart(text=user_text), *image_parts]
            messages = [
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=content),
            ]
            answer = await self._vision_llm.generate(messages)
            logger.info(
                "Answer generated (vision)", query=query[:80], images=len(image_parts), chars=len(answer)
            )
            self._validate_citations(answer, len(chunks), query)
            return answer, GenerationTrace(
                context_blocks=len(chunks),
                hydration_eligible=eligible,
                images_hydrated=len(image_parts),
                vision_used=True,
                prompt_chars=len(user_text),
            )

        # Default text-only path — unchanged from single-modality behaviour.
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_text),
        ]
        answer = await self._llm.generate(messages)
        logger.info("Answer generated", query=query[:80], chars=len(answer))
        self._validate_citations(answer, len(chunks), query)
        return answer, GenerationTrace(
            context_blocks=len(chunks),
            hydration_eligible=eligible,
            images_hydrated=len(image_parts),
            vision_used=False,
            prompt_chars=len(user_text),
        )
