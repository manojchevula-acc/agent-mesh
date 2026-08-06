"""ResponseGenerator — builds a grounded prompt and calls the LLM."""

import re
from typing import TYPE_CHECKING

from ..config.settings import Settings
from ..llm.base import BaseLLM, ContentPart, Message, TextPart
from ..models.retrieval import RetrievedChunk, RetrievedImage
from ..utils.logging import get_logger

if TYPE_CHECKING:
    from .image_payload import ImagePayloadBuilder

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are GERNAS, FAB's regulatory and credit-policy assistant. Answer strictly "
    "from the provided context. Each context block is numbered [1], [2], etc. "
    "Cite every factual claim with the corresponding context number(s), e.g. [1] or [2][3]. "
    "After your answer append a 'Sources:' section listing only the numbers you cited, "
    "each with the document name and section. "
    "If the context does not contain the answer, say so explicitly and do not speculate. "
    "Flag any context marked as ⚠ STALE."
)

# Used when the VISION model is serving the request.
_VISION_ADDENDUM = (
    "Some context blocks are FIGURES from the source documents, labelled [I1], "
    "[I2], etc. The figure image itself follows each label — you can see it. "
    "Read values, axis labels and cell contents directly from the image and cite "
    "them as [I1]. If a value is illegible or ambiguous in the image, say so "
    "explicitly rather than guessing. Do not state a number you cannot actually "
    "read. If a figure contradicts the text context, report both and flag the "
    "discrepancy."
)

# Used when the TEXT model serves a request that HAD figures (fallback path).
_TEXT_FALLBACK_ADDENDUM = (
    "Some context blocks are FIGURES, labelled [I1], [I2], etc. You cannot see "
    "these images — you have only their caption and surrounding text. Refer to a "
    "figure when relevant and cite it as [I1], but describe only what the caption "
    "and context state. Never describe visual details you were not told about."
)

_WEAK_TEXT_HINT = (
    "The text context is weak for this question. The figures below may answer it "
    "directly — prioritise them."
)

_CITATION_RE = re.compile(r"\[(I?)(\d+)\]")
_MATCHED_PASSAGE_CHARS = 300


class ResponseGenerator:
    """Assembles retrieved chunks (and figures) into a grounded prompt."""

    def __init__(
        self,
        settings: Settings,
        llm: BaseLLM,
        payload_builder: "ImagePayloadBuilder | None" = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._payload = payload_builder  # None => vision disabled

    # ── Context building ──────────────────────────────────────────────
    def _build_context(self, chunks: list[RetrievedChunk]) -> str:
        blocks: list[str] = []
        for i, c in enumerate(chunks, start=1):
            # Prefer the human-readable section heading; fall back to clause_reference.
            section_label = c.section_heading or c.clause_reference
            header = f"[{i}] Source: {c.source}"
            if section_label:
                header += f" · Section: {section_label}"
            if c.effective_date:
                header += f" · Effective: {c.effective_date}"
            if c.freshness_warning:
                header += " · ⚠ STALE"

            if c.content_type == "table":
                # Fence the grid so the model does not reflow it into prose, and
                # name the ambiguity rather than letting it be resolved silently.
                header += " · TABLE"
                if c.table_part:
                    header += f" (part {c.table_part} — header row repeated)"
                body = (
                    f"```\n{c.text}\n```\n"
                    "If a value's column is ambiguous in this flattened table, "
                    "say so rather than guessing."
                )
            elif c.parent_text and c.text != c.parent_text:
                # Show the broader parent section for context, but call out the
                # matched passage so the LLM can cite precisely.
                matched = c.text[:_MATCHED_PASSAGE_CHARS].strip()
                if len(c.text) > _MATCHED_PASSAGE_CHARS:
                    matched += "…"
                body = f"{c.parent_text}\n\n[Matched passage: {matched}]"
            else:
                body = c.text
            blocks.append(f"{header}\n{body}")
        return "\n\n---\n\n".join(blocks)

    def _image_header(self, index: int, image: RetrievedImage) -> str:
        header = f"\n[I{index}] Figure · Source: {image.source}"
        if image.page_number:
            header += f" · Page {image.page_number}"
        if image.nearest_heading:
            header += f" · Section: {image.nearest_heading}"
        if image.role and image.role != "unknown":
            header += f" · {image.role}"
        if image.caption:
            header += f"\nCaption: {image.caption}"
        return header

    def _build_image_context(self, images: list[RetrievedImage]) -> str:
        """Text-only rendering, used when pixels cannot be sent."""
        blocks = []
        for i, im in enumerate(images, start=1):
            body = im.caption or "(no caption in source document)"
            blocks.append(f"{self._image_header(i, im).lstrip()}\n{body}\n(reference: {im.uri})")
        return "\n\n---\n\n".join(blocks)

    # ── Generation ────────────────────────────────────────────────────
    async def generate(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        images: list[RetrievedImage] | None = None,
    ) -> str:
        images = images or []
        if not chunks and not images:
            logger.info("No context to generate from", query=query[:80])
            return "I could not find any relevant policy context to answer this question."

        vision_on = bool(images) and self._payload is not None
        system = _SYSTEM_PROMPT
        if images:
            system += " " + (_VISION_ADDENDUM if vision_on else _TEXT_FALLBACK_ADDENDUM)

        # Weak-text rescue: without this, a question answerable only by a figure
        # hits the "no relevant context" path even though retrieval succeeded.
        threshold = self._settings.multimodal.retrieval.weak_text_threshold
        text_is_weak = not chunks or max(c.score for c in chunks) < threshold
        if text_is_weak and images:
            system += " " + _WEAK_TEXT_HINT

        parts: list[ContentPart] = []
        if chunks:
            parts.append(TextPart(f"Context:\n{self._build_context(chunks)}"))

        if images:
            built = self._payload.build_all(images) if vision_on else []
            if built:
                parts.append(TextPart("\nFigures:"))
                for i, (image, image_part) in enumerate(built, start=1):
                    # INTERLEAVE: the label immediately precedes its own image so
                    # [I2] cannot bind to the wrong picture.
                    parts.append(TextPart(self._image_header(i, image)))
                    parts.append(TextPart("Image:"))
                    parts.append(image_part)
            else:
                parts.append(TextPart(f"\nFigures:\n{self._build_image_context(images)}"))

        parts.append(
            TextPart(
                f"\nQuestion: {query}\n\n"
                "Answer using only the context and figures above. Cite text with [N] "
                "and figures with [IN], and end with a 'Sources:' block."
            )
        )

        answer = await self._llm.generate(
            [
                Message(role="system", content=system),
                Message(role="user", content=parts),
            ]
        )
        logger.info(
            "Answer generated",
            query=query[:80],
            chars=len(answer),
            images=len(images),
            vision=vision_on,
        )
        return self._validate_citations(answer, len(chunks), len(images))

    @staticmethod
    def _validate_citations(answer: str, n_chunks: int, n_images: int) -> str:
        """Log — do not rewrite — citations outside the supplied ranges.

        Rewriting risks corrupting a correct answer; logging turns silent
        mis-citation into an observable metric.
        """
        bad: list[str] = []
        for prefix, number in _CITATION_RE.findall(answer):
            index = int(number)
            limit = n_images if prefix == "I" else n_chunks
            if index < 1 or index > limit:
                bad.append(f"[{prefix}{number}]")
        if bad:
            logger.warning(
                "Answer cites out-of-range sources",
                bad=sorted(set(bad)),
                n_chunks=n_chunks,
                n_images=n_images,
            )
        return answer
