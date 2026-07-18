"""RAG citation and hallucination evaluators for FAB AgentMesh.

Checks that RAGAgent responses include valid document citations and
that the answer is grounded in the retrieved context.
"""
from __future__ import annotations
import re
from typing import List, Optional, Set
from .compliance_evaluator import EvalScore

# U+202F (NARROW NO-BREAK SPACE) is emitted by Claude in formatted headings
# between tokens like "Basel III" or "Tier 1". Python \s in ASCII
# mode does not match it, so we include it explicitly in whitespace classes.
_WS = r"[\s  ]+"

# Citation patterns: any of these indicate the response cites a source.
_CITATION_PATTERNS = [
    re.compile(r"\[Source[:\s][^\]]+\]", re.IGNORECASE),
    re.compile(r"\*Source[:\s][^*]+\*", re.IGNORECASE),   # *Source: Doc_Name, Section X.X*
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


def _normalize_doc_name(name: str) -> str:
    """Normalize a document name for fuzzy substring matching.

    Replaces underscores with spaces and strips trailing version tags (v1.2, v2.4)
    so 'FAB_Credit_Pricing_Policy_v2.4' matches corpus entry 'FAB Credit Pricing Policy'.
    """
    name = re.sub(r"_", " ", name)
    name = re.sub(r"\s+v\d+[\d.]*\s*$", "", name, flags=re.IGNORECASE)
    return name.lower().strip()


def citation_present_and_valid(response_text: str) -> EvalScore:
    """Checks that a RAGAgent response includes at least one document citation.

    Score 1.0: strong citation found (specific document name or structured reference).
    Score 0.5: weak citation (vague policy reference without document name).
    Score 0.0: no citation found.
    """
    if not response_text:
        empty_checks = [
            {"name": "Known corpus document referenced", "passed": False, "detail": "Empty response"},
            {"name": "Structured citation pattern matched", "passed": False, "detail": "Empty response"},
            {"name": "General policy language detected", "passed": False, "detail": "Empty response"},
        ]
        return EvalScore(0.0, "NO_CITATION", "Empty response", checks=empty_checks)

    # Normalise narrow no-break space (U+202F) to ASCII space so corpus doc name
    # substring checks match regardless of which space variant the LLM emits.
    normalised = response_text.replace(" ", " ")

    # Run all three tiers up-front so checks always show the full picture.
    # normalised_norm converts underscores→spaces and strips version suffixes so that
    # agent citations like 'FAB_Credit_Pricing_Policy_v2.4' match corpus names.
    normalised_norm = _normalize_doc_name(normalised)
    corpus_match: Optional[str] = None
    for doc in _KNOWN_CORPUS_DOCS:
        if _normalize_doc_name(doc) in normalised_norm:
            corpus_match = doc
            break

    pattern_match: Optional[str] = None
    for pattern in _CITATION_PATTERNS:
        m = pattern.search(normalised)
        if m:
            pattern_match = m.group()[:60]
            break

    # Use normalised_norm so that underscore-formatted doc names (e.g. Pricing_Policy)
    # are converted to spaces before word-boundary matching fires.
    has_vague = bool(re.search(r"\bpolic(y|ies)\b|\bguideline|\bregulat", normalised_norm, re.IGNORECASE))

    checks = [
        {"name": "Known corpus document referenced (FAB/CBUAE/Basel III/…)",
         "passed": bool(corpus_match),
         "detail": f"Found: '{corpus_match}'" if corpus_match else "None of the 10 known corpus documents found"},
        {"name": "Structured citation pattern matched ([Source: …], 'According to', 'as per', …)",
         "passed": bool(pattern_match),
         "detail": f"Matched: '{pattern_match}'" if pattern_match else "No structured citation pattern found"},
        {"name": "General policy / regulation language detected",
         "passed": has_vague,
         "detail": "Policy/guideline/regulation language present" if has_vague else "No policy language found"},
    ]

    if corpus_match:
        return EvalScore(1.0, "STRONG_CITATION", f"References known document: {corpus_match}", checks=checks)
    if pattern_match:
        return EvalScore(1.0, "CITATION_FOUND", f"Citation pattern matched: '{pattern_match}'", checks=checks)
    if has_vague:
        return EvalScore(0.5, "WEAK_CITATION", "Policy language present but no specific document cited", checks=checks)
    return EvalScore(0.0, "NO_CITATION", "No citation or policy reference found", checks=checks)


# Markers that indicate the RAGAgent returned an error rather than grounded content.
# When context chunks consist solely of error messages, Jaccard overlap is meaningless.
_RAG_ERROR_MARKERS = (
    "knowledge base is currently unavailable",
    "rag_unavailable",
    "rag unavailable",
    "no relevant policy documents were found",
    "knowledge base unavailable",
    "currently unavailable",
)


def _is_rag_error_output(chunks: List[str]) -> bool:
    """Return True if every non-empty chunk is an error/unavailability message."""
    non_empty = [c for c in chunks if c and c.strip()]
    if not non_empty:
        return False
    return all(
        any(m in chunk.lower() for m in _RAG_ERROR_MARKERS)
        for chunk in non_empty
    )


def rag_answer_not_hallucinated(response_text: str, context_chunks: List[str]) -> EvalScore:
    """Checks that the RAGAgent answer is grounded in retrieved context chunks.

    Uses Jaccard token overlap between the answer and the concatenated chunks.
    Score 1.0: overlap > 0.30 (well-grounded).
    Score 0.5: overlap 0.10-0.30 (partially grounded).
    Score 0.0: overlap < 0.10 (potential hallucination).

    Returns NOT_APPLICABLE (0.5) when the RAGAgent returned an error/unavailability
    message — low Jaccard in that case is expected, not a hallucination signal.
    """
    if not response_text or not context_chunks:
        no_ctx_checks = [
            {"name": "Context chunks provided", "passed": bool(context_chunks),
             "detail": f"{len(context_chunks)} chunks" if context_chunks else "No context chunks available"},
            {"name": "Jaccard token overlap computed", "passed": False,
             "detail": "Cannot evaluate without both response and context"},
            {"name": "Answer grounding verdict", "passed": False, "detail": "N/A — missing input"},
        ]
        return EvalScore(0.5, "NO_CONTEXT", "Cannot evaluate without context chunks", checks=no_ctx_checks)

    # When the RAGAgent returned an error/unavailability message, Jaccard overlap
    # would be near zero by design — that is NOT a hallucination signal.
    if _is_rag_error_output(context_chunks):
        error_checks = [
            {"name": "Context chunks provided", "passed": True,
             "detail": f"{len(context_chunks)} chunk(s) — all are RAG error/unavailability messages"},
            {"name": "Jaccard token overlap computed", "passed": True,
             "detail": "Skipped — RAG returned an error, not grounded content"},
            {"name": "Answer grounding verdict", "passed": True,
             "detail": "NOT_APPLICABLE — RAG unavailable; hallucination check excluded"},
        ]
        return EvalScore(0.5, "RAG_UNAVAILABLE",
                         "RAG returned error/unavailability — hallucination check not applicable",
                         checks=error_checks)

    def tokenise(text: str) -> Set[str]:
        # Include both alphabetic words (3+ chars) AND numeric tokens so that
        # financial figures shared between context and response count as grounding
        # evidence.  Without numbers, "4.5% pricing floor" and "minimum: 4.50%"
        # share almost no tokens despite being semantically identical.
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        nums  = re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text)
        return set(words) | set(nums)

    answer_tokens = tokenise(response_text)
    context_tokens = tokenise(" ".join(context_chunks))

    if not answer_tokens or not context_tokens:
        empty_tok_checks = [
            {"name": "Context chunks provided", "passed": True, "detail": f"{len(context_chunks)} chunks"},
            {"name": "Jaccard token overlap computed", "passed": False, "detail": "Tokenisation produced no terms"},
            {"name": "Answer grounding verdict", "passed": False, "detail": "N/A — no tokens"},
        ]
        return EvalScore(0.5, "EMPTY_TOKENS", "Tokenisation produced no terms", checks=empty_tok_checks)

    overlap = len(answer_tokens & context_tokens) / len(answer_tokens | context_tokens)
    grounded = overlap >= 0.30
    partial = overlap >= 0.10

    checks = [
        {"name": "Context chunks provided", "passed": True, "detail": f"{len(context_chunks)} chunk(s) retrieved"},
        {"name": f"Jaccard token overlap: {overlap:.3f}",
         "passed": partial,
         "detail": f"Overlap={overlap:.3f} — threshold ≥0.30 → GROUNDED, ≥0.10 → PARTIAL, <0.10 → HALLUCINATION_RISK"},
        {"name": "Answer grounding verdict",
         "passed": grounded,
         "detail": "GROUNDED" if grounded else ("PARTIAL" if partial else "HALLUCINATION_RISK")},
    ]

    if grounded:
        return EvalScore(1.0, "GROUNDED", f"Jaccard overlap={overlap:.2f}", checks=checks)
    if partial:
        return EvalScore(0.5, "PARTIAL", f"Jaccard overlap={overlap:.2f}", checks=checks)
    return EvalScore(0.0, "HALLUCINATION_RISK",
                     f"Jaccard overlap={overlap:.2f} -- answer poorly grounded in retrieved chunks",
                     checks=checks)
