"""RAG citation and hallucination evaluators for FAB AgentMesh.

Checks that RAGAgent responses include valid document citations and
that the answer is grounded in the retrieved context.
"""
from __future__ import annotations
import re
from typing import List, Optional, Set
from .compliance_evaluator import EvalScore

# U+202F (NARROW NO-BREAK SPACE) is emitted by Claude in formatted headings
# between tokens like "Basel III" or "Tier 1". Python \s in ASCII
# mode does not match it, so we include it explicitly in whitespace classes.
_WS = r"[\s ]+"

# Citation patterns: any of these indicate the response cites a source.
_CITATION_PATTERNS = [
    re.compile(r"\[Source[:\s][^\]]+\]", re.IGNORECASE),
    re.compile(r"According to [A-Z][^.]+", re.IGNORECASE),
    re.compile(r"as per [A-Z][^.]+", re.IGNORECASE),
    re.compile(r"per (the\s+)?[A-Z][^,.]+(?:policy|circular|guideline|document|framework|regulation)", re.IGNORECASE),
    re.compile(r"CBUAE" + _WS + r"Circular", re.IGNORECASE),
    re.compile(r"Basel" + _WS + r"III", re.IGNORECASE),
    re.compile(r"FAB" + _WS + r"(Credit|Pricing|Compliance|Policy|Risk)" + _WS + r"(Policy|Framework|Guidelines?)", re.IGNORECASE),
]

# Known document names in the Qdrant corpus.
_KNOWN_CORPUS_DOCS: Set[str] = {
    "FAB Credit Pricing Policy",
    "FAB Pricing Guidelines",
    "FAB Compliance Framework",
    "Basel III Capital Requirements",
    "CBUAE Circular 2024/BSE/047",
    "Model Risk Management Policy",
    "AML KYC Policy",
    "Loan Restructuring Policy",
    "FAB Product Guidelines",
    "Concentration Limits Framework",
}


def citation_present_and_valid(response_text: str) -> EvalScore:
    """Checks that a RAGAgent response includes at least one document citation.

    Score 1.0: strong citation found (specific document name or structured reference).
    Score 0.5: weak citation (vague policy reference without document name).
    Score 0.0: no citation found.
    """
    if not response_text:
        return EvalScore(0.0, "NO_CITATION", "Empty response")

    # Normalise narrow no-break space (U+202F) to ASCII space so corpus doc name
    # substring checks match regardless of which space variant the LLM emits.
    normalised = response_text.replace(" ", " ")

    # Check for strong citations (known corpus documents)
    for doc in _KNOWN_CORPUS_DOCS:
        if doc.lower() in normalised.lower():
            return EvalScore(1.0, "STRONG_CITATION", f"References known document: {doc}")

    # Check for pattern-based citations (run against normalised text)
    for pattern in _CITATION_PATTERNS:
        m = pattern.search(normalised)
        if m:
            cited = m.group()[:60]
            return EvalScore(1.0, "CITATION_FOUND", f"Citation pattern matched: '{cited}'")

    # Vague policy language
    if re.search(r"\bpolic(y|ies)\b|\bguideline|\bregulat", normalised, re.IGNORECASE):
        return EvalScore(0.5, "WEAK_CITATION", "Policy language present but no specific document cited")

    return EvalScore(0.0, "NO_CITATION", "No citation or policy reference found")


def rag_answer_not_hallucinated(response_text: str, context_chunks: List[str]) -> EvalScore:
    """Checks that the RAGAgent answer is grounded in retrieved context chunks.

    Uses Jaccard token overlap between the answer and the concatenated chunks.
    Score 1.0: overlap > 0.30 (well-grounded).
    Score 0.5: overlap 0.10-0.30 (partially grounded).
    Score 0.0: overlap < 0.10 (potential hallucination).
    """
    if not response_text or not context_chunks:
        return EvalScore(0.5, "NO_CONTEXT", "Cannot evaluate without context chunks")

    def tokenise(text: str) -> Set[str]:
        return {w.lower() for w in re.findall(r"\b[a-z]{3,}\b", text.lower())}

    answer_tokens = tokenise(response_text)
    context_tokens = tokenise(" ".join(context_chunks))

    if not answer_tokens or not context_tokens:
        return EvalScore(0.5, "EMPTY_TOKENS", "Tokenisation produced no terms")

    overlap = len(answer_tokens & context_tokens) / len(answer_tokens | context_tokens)

    if overlap >= 0.30:
        return EvalScore(1.0, "GROUNDED", f"Jaccard overlap={overlap:.2f}")
    if overlap >= 0.10:
        return EvalScore(0.5, "PARTIAL", f"Jaccard overlap={overlap:.2f}")
    return EvalScore(0.0, "HALLUCINATION_RISK", f"Jaccard overlap={overlap:.2f} -- answer poorly grounded in retrieved chunks")
