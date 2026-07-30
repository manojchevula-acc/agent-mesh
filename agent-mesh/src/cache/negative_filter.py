"""Shared 'negative answer' detector for the semantic cache.

A negative answer ("No … data found", "unable to retrieve", "data unavailable")
must not be cached — otherwise a future identical query that would now succeed
gets served the stale "not found". Used by both the live store path
(orchestrator) and the audit-trail ingest.
"""
from __future__ import annotations

import re

_NEGATIVE_ANSWER_RE = re.compile(
    r"(no\b.*?\b(?:data|records?|results?|information|entries)\b.*?\bfound|"
    r"unable to retrieve|data[-\s]?unavailable|please try again or contact)",
    re.IGNORECASE | re.DOTALL,
)


def is_negative_answer(text: str) -> bool:
    """True for 'no data found' / 'unable to retrieve' style non-answers."""
    return bool(_NEGATIVE_ANSWER_RE.search(text or ""))
