"""PII scrubbing utilities applied before any data enters telemetry spans."""
from __future__ import annotations

import re

# Ordered from most-specific to least-specific to avoid partial matches
_PII_PATTERNS: list[tuple[str, str]] = [
    # Bearer tokens and API keys
    (r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer [REDACTED]"),
    (r"sk-[A-Za-z0-9]{20,}", "[API_KEY]"),
    # IBANs (simplified; covers GB and most EU formats)
    (r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b", "[IBAN]"),
    # UK NI numbers
    (r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", "[NI_NUMBER]"),
    # Email addresses
    (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL]"),
    # Phone numbers (E.164 and local UK/US formats)
    (r"\+?\d[\d\s\-().]{8,}\d", "[PHONE]"),
    # Generic account / card numbers (14–19 consecutive digits)
    (r"\b\d{14,19}\b", "[ACCOUNT_NUMBER]"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in _PII_PATTERNS]


def scrub_pii(text: str) -> str:
    """Replace known PII patterns with safe placeholders."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text
