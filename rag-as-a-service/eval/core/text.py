"""Text normalisation and edit-distance helpers.

Deliberately dependency-free: character error rate is the only place the suite
needs edit distance, and a 30-line DP is cheaper to reason about (and to keep
deterministic across environments) than another third-party pin.
"""

from __future__ import annotations

import re
import unicodedata

# Inline citation markers the generator appends, e.g. "[1]", "[2][3]" or the
# legacy "【1 · 4.85】" form. Stripped before any text comparison so a citation
# index is never scored as an answer token or a hallucinated number.
CITATION_RE = re.compile(r"【[^】]*】|\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]|\[[^\]]*·[^\]]*\]")

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w%.\-/]+", re.UNICODE)
_DASH_RE = re.compile(r"[‐-―−]")


def strip_citations(text: str) -> str:
    """Remove inline citation markers and tidy the whitespace they leave behind."""
    cleaned = CITATION_RE.sub(" ", text)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def normalize(text: str) -> str:
    """Casefold, unify unicode dashes/spaces, collapse whitespace.

    Applied before CER so that a caption differing from the transcription only
    in an en-dash or a double space does not register as a transcription error.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)
    return _WS_RE.sub(" ", text).strip().casefold()


def content_tokens(text: str, min_length: int = 3) -> set[str]:
    """Lowercased alphanumeric tokens, used for coarse overlap checks."""
    cleaned = _PUNCT_RE.sub(" ", normalize(text))
    return {t for t in cleaned.split() if len(t) >= min_length}


def sentences(text: str) -> list[str]:
    """Split into rough sentences. Good enough to attach a citation to a claim."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def levenshtein(a: str, b: str) -> int:
    """Edit distance with the usual unit costs, computed in O(min(len)) space."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):  # Keep the inner loop over the shorter string.
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        previous = current
    return previous[-1]


def char_error_rate(reference: str, hypothesis: str) -> float | None:
    """CER = edit_distance / len(reference), on normalised text.

    Returns ``None`` for an empty reference (undefined rather than 0.0 — an
    empty ground truth is missing data, not a perfect score). Capped at 1.0 so a
    runaway hypothesis cannot skew an average without bound.
    """
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return None
    return min(levenshtein(ref, hyp) / len(ref), 1.0)


# Characters that never legitimately end a completed transcription: a dangling
# separator or an unclosed delimiter means the model stopped mid-row/mid-clause.
_TRUNCATION_SUFFIXES = ("|", ",", ":", ";", "-", "/", "(", "[", "{", "…")


def looks_truncated(text: str) -> bool:
    """Heuristic: did this caption stop mid-transcription?

    A VLM transcription that hit ``max_tokens`` characteristically ends on a
    dangling separator or with brackets left open. A legitimate transcription
    often ends on a bare value ("35 bps") with no punctuation at all, so absence
    of terminal punctuation is *not* on its own treated as truncation. Raises a
    WARN only — never fails a build by itself.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(_TRUNCATION_SUFFIXES):
        return True
    return stripped.count("(") > stripped.count(")") or stripped.count("[") > stripped.count("]")
