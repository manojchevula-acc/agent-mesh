"""ResponseGenerator — builds a grounded prompt and calls the LLM."""

from ..config.settings import Settings
from ..llm.base import BaseLLM, Message
from ..models.retrieval import RetrievedChunk
from ..utils.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are GERNAS, FAB's regulatory and credit-policy assistant. Answer strictly "
    "from the provided context. Each context block is numbered [1], [2], etc. "
    "Cite every factual claim with the corresponding context number(s), e.g. [1] or [2][3]. "
    "After your answer append a 'Sources:' section listing only the numbers you cited, "
    "each with the document name and section. "
    "If the context does not contain the answer, say so explicitly and do not speculate. "
    "Flag any context marked as \u26a0 STALE."
)


class ResponseGenerator:
    """Assembles retrieved chunks into a grounded prompt and calls the LLM."""

    def __init__(self, settings: Settings, llm: BaseLLM) -> None:
        self._settings = settings
        self._llm = llm

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
                header += " · \u26a0 STALE"
            # When a broader parent section is available, show it for context but
            # also call out the specific matched passage so the LLM knows exactly
            # which sentence triggered retrieval and can cite precisely.
            if c.parent_text and c.text != c.parent_text:
                matched = c.text[:300].strip()
                if len(c.text) > 300:
                    matched += "\u2026"
                body = f"{c.parent_text}\n\n[Matched passage: {matched}]"
            else:
                body = c.text
            blocks.append(f"{header}\n{body}")
        return "\n\n---\n\n".join(blocks)

    async def generate(self, query: str, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            logger.info("No chunks to generate from", query=query[:80])
            return "I could not find any relevant policy context to answer this question."

        context = self._build_context(chunks)
        user_content = (
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Answer using only the context above. Cite every claim with [N] numbers "
            "and end with a 'Sources:' block."
        )
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]
        answer = await self._llm.generate(messages)
        logger.info("Answer generated", query=query[:80], chars=len(answer))
        return answer
