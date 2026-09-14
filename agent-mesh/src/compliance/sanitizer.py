"""PII scrubbing utilities applied before any data enters compliance events."""
from __future__ import annotations

import re

_PII_PATTERNS: list[tuple[str, str]] = [
    (r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer [REDACTED]"),
    (r"sk-[A-Za-z0-9]{20,}", "[API_KEY]"),
    (r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b", "[IBAN]"),
    (r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", "[NI_NUMBER]"),
    (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL]"),
    (r"\+?\d[\d\s\-().]{8,}\d", "[PHONE]"),
    (r"\b\d{14,19}\b", "[ACCOUNT_NUMBER]"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _PII_PATTERNS]


def scrub_pii(text: str) -> str:
    """Replace known PII patterns with safe placeholders."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text
