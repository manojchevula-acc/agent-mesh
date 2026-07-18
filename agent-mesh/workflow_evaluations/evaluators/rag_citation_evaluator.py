"""RAG citation and hallucination evaluators for FAB AgentMesh.

Checks that RAGAgent responses include valid document citations and
that the answer is grounded in the retrieved context.

Citation evaluation strategy — two-tier:
  Tier 1 (deterministic): corpus-document substring match, 8 structured citation
    patterns ([Source: …], "According to", "as per", FAB/CBUAE/Basel III anchors),
    and a vague policy-language fallback.
  Tier 2 (LLM judge): runs only when deterministic score < 1.0 (i.e., when the
    regex checks couldn't find a strong citation).  The LLM can recognise citations
    phrased in non-standard ways (e.g. "as stated in the Concentration Limits
    Circular" or a multi-line footnote) that the fixed patterns miss.  If the judge
    upgrades the verdict, the LLM reasoning is added as an additional check entry.
    If GROQ_API_KEY is absent the function silently keeps the deterministic result.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Set

from .compliance_evaluator import EvalScore

# ---------------------------------------------------------------------------
# LLM judge infrastructure (same Groq/Cerebras endpoint as task_adherence)
# ---------------------------------------------------------------------------

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

_CITATION_JUDGE_PROMPT = """\
You are evaluating whether a banking AI assistant (FAB — First Abu Dhabi Bank) \
properly cited its sources in its response.

AGENT RESPONSE:
{response}

=== Evaluation task ===
Does the response cite a specific authoritative source for the information it provides?

Score:
  1.0 = STRONG_CITATION
        The response names a specific document, circular, policy, or regulation
        (e.g. "FAB Credit Pricing Policy v2.4", "CBUAE Circular 2024/BSE/047",
        "Basel III framework", "Model Risk Management Policy").
        Naming a standard industry acronym alone (e.g. "per Basel III") counts.

  0.5 = WEAK_CITATION
        The response references "policy", "guidelines", "regulations", or "framework"
        but does NOT name the specific document.

  0.0 = NO_CITATION
        The response gives information or advice with no policy or document reference at all.

Return ONLY valid JSON (no markdown fences):
{{
  "score": 1.0,
  "label": "STRONG_CITATION|WEAK_CITATION|NO_CITATION",
  "reason": "one sentence explaining the verdict",
  "cited_source": "name of the cited document if found, else null"
}}"""


def _call_citation_llm_judge(response_text: str) -> Optional[dict]:
    """Call the LLM judge to assess citation quality.

    Returns a parsed dict with keys: score, label, reason, cited_source.
    Returns None when GROQ_API_KEY is absent or on any exception.
    """
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = (
        os.getenv("EVAL_JUDGE_MODEL")
        or os.getenv("GROQ_MODEL")
        or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
    )
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        prompt = _CITATION_JUDGE_PROMPT.format(response=response_text[:1200])
        resp = client.chat.completions.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= 0:
            return None
        data = json.loads(raw[start:end])
        raw_score = float(data.get("score", 0.5))
        if raw_score >= 0.75:
            data["score"] = 1.0
        elif raw_score >= 0.25:
            data["score"] = 0.5
        else:
            data["score"] = 0.0
        return data
    except Exception:
        return None

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

    Primary (LLM judge): semantic assessment — did the agent cite an authoritative
      source?  Returns 1-2 broad checks derived from the LLM reasoning.

    Fallback (LLM unavailable): deterministic corpus + pattern matching used as a
      broad signal, not a battery of narrow regex checks.

    Score 1.0: strong/specific citation (named document, circular, framework).
    Score 0.5: weak citation (policy/regulation referenced but not named).
    Score 0.0: no citation found.
    """
    if not response_text:
        return EvalScore(0.0, "NO_CITATION", "Empty response", checks=[
            {"name": "LLM citation quality verdict", "passed": False,
             "detail": "No response to evaluate"},
        ])

    # --- LLM judge (primary) ---
    llm = _call_citation_llm_judge(response_text)

    if llm is not None:
        score  = llm.get("score", 0.0)
        label  = str(llm.get("label", "NO_CITATION"))
        reason = str(llm.get("reason", ""))[:200]
        source = llm.get("cited_source") or None

        detail = f"{label} — {reason}"
        if source:
            detail += f" | Source identified: ‘{source}’"

        checks = [
            {"name": "LLM citation quality verdict",
             "passed": score >= 1.0,
             "detail": detail},
        ]
        if source:
            checks.append({
                "name": "Specific authoritative source named",
                "passed": True,
                "detail": source,
            })

        return EvalScore(score, label, reason or "LLM citation assessment", checks=checks)

    # --- Fallback: deterministic (LLM unavailable) ---
    # Normalise narrow no-break space (U+202F) to ASCII space so corpus doc name
    # substring checks match regardless of which space variant the LLM emits.
    normalised = response_text.replace(" ", " ")
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

    has_vague = bool(re.search(r"polic(y|ies)|guideline|regulat", normalised_norm, re.IGNORECASE))

    checks = [
        {"name": "LLM citation quality verdict",
         "passed": bool(corpus_match or pattern_match),
         "detail": "JUDGE_UNAVAILABLE — using deterministic fallback"},
        {"name": "Source attribution present in response",
         "passed": bool(corpus_match or pattern_match or has_vague),
         "detail": (f"Named source: ‘{corpus_match or pattern_match}’"
                    if (corpus_match or pattern_match)
                    else ("Policy/regulation language present" if has_vague
                          else "No citation or policy reference detected"))},
    ]

    if corpus_match:
        return EvalScore(1.0, "STRONG_CITATION", f"References known document: {corpus_match}", checks=checks)
    if pattern_match:
        return EvalScore(1.0, "CITATION_FOUND", f"Citation pattern matched: ‘{pattern_match}’", checks=checks)
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

# Phrases that identify a generic agent fallback/error response as opposed to real domain content.
# When the *answer* itself is an error message (not just the context), Jaccard overlap between
# the error text and real context chunks will be near-zero — that is NOT a hallucination signal.
# Both B1 and C1 exhibit this: the agent returned "I was unable to retrieve…" while real (or
# empty) context chunks existed, producing HALLUCINATION_RISK (0.00) or EMPTY_TOKENS (0.50)
# inconsistently.  Detecting the error response early ensures both cases return NOT_APPLICABLE.
_AGENT_ERROR_RESPONSE_MARKERS = (
    "i was unable to retrieve",
    "unable to retrieve the required data",
    "please try again",
    "contact your relationship manager",
    "an error occurred",
    "could not retrieve",
    "failed to retrieve",
    "service is currently unavailable",
    "i'm unable to retrieve",
    "i am unable to retrieve",
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


def _is_agent_error_response(response: str) -> bool:
    """Return True if the agent answer is a generic fallback/error message.

    When the agent returned an error (e.g. "I was unable to retrieve the required
    data"), Jaccard overlap with real context chunks is near-zero by design — not
    because the answer is hallucinated, but because no domain content was produced.
    Treating this as HALLUCINATION_RISK double-penalises the same root cause that
    Tool Call Success already captures with TOOL_ERROR.
    """
    low = response.lower().strip()
    return any(m in low for m in _AGENT_ERROR_RESPONSE_MARKERS)


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

    # When the agent answer itself is a generic error/fallback message, Jaccard overlap
    # with real context chunks will be near-zero by design — that is NOT a hallucination
    # signal.  This catches cases like B1 where RAGAgent retrieved real chunks but the
    # pipeline still returned "I was unable to retrieve the required data."
    # Without this guard, B1 gets HALLUCINATION_RISK (0.00) while C1 (whose chunks
    # tokenise to nothing) gets EMPTY_TOKENS (0.50) — inconsistent for the same root cause.
    if _is_agent_error_response(response_text):
        agent_err_checks = [
            {"name": "Context chunks provided", "passed": bool(context_chunks),
             "detail": f"{len(context_chunks)} chunk(s)"},
            {"name": "Agent error/fallback response detected", "passed": True,
             "detail": "Response is a generic error message — hallucination check not applicable"},
            {"name": "Answer grounding verdict", "passed": True,
             "detail": "NOT_APPLICABLE — agent returned error, not domain content; "
                       "Tool Call Success evaluator captures this failure"},
        ]
        return EvalScore(0.5, "AGENT_ERROR_RESPONSE",
                         "Agent returned error/fallback — hallucination check not applicable",
                         checks=agent_err_checks)

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
