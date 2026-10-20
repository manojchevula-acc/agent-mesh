"""PII scrubbing utilities applied before any free text enters telemetry.

Pattern classes: secrets (bearer tokens, provider API keys, ``key=value``
credentials), Emirates ID, IBAN, Luhn-valid card numbers, NI numbers, e-mail,
phone and account numbers. The Collector masks the same classes again at the
gateway; this in-process pass is defence in depth.
"""

from __future__ import annotations

import re
from collections.abc import Callable


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card(match: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", match.group(0))
    return "[CARD]" if _luhn_ok(digits) else match.group(0)


# Ordered from most-specific to least-specific to avoid partial matches.
_PII_PATTERNS: list[tuple[str, str | Callable[[re.Match[str]], str]]] = [
    # Secrets: bearer tokens, provider API keys, key=value credentials
    (r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer [REDACTED]"),
    (r"\b(?:sk|gsk|pk|rk)[-_][A-Za-z0-9_\-]{16,}", "[API_KEY]"),
    (r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]"),
    # Emirates ID: 784-YYYY-NNNNNNN-C (dashes optional)
    (r"\b784-?\d{4}-?\d{7}-?\d\b", "[EMIRATES_ID]"),
    # UAE IBAN (AE + 2 check digits + 19 digits), optionally space-grouped
    (r"\bAE\d{2}(?:\s?\d){19}\b", "[IBAN]"),
    # Other IBANs (simplified; GB and most EU formats)
    (r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b", "[IBAN]"),
    # Card numbers: 13-19 digits (optionally grouped), replaced only if Luhn-valid
    (r"\b\d(?:[ -]?\d){12,18}\b", _card),
    # UK NI numbers
    (r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", "[NI_NUMBER]"),
    # Email addresses
    (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL]"),
    # Phone numbers (E.164 and local formats, incl. UAE +971 / 05x). Not inside an
    # identifier: hex run / trace / span ids must survive scrubbing intact.
    (r"(?<![A-Za-z0-9_])\+?\d[\d\s\-().]{8,}\d(?![A-Za-z0-9_])", "[PHONE]"),
    # Generic account numbers (14-19 consecutive digits that were not Luhn-valid cards)
    (r"\b\d{14,19}\b", "[ACCOUNT_NUMBER]"),
]

# Case-sensitive by default (IBANs and NI numbers are upper case; a case-insensitive
# IBAN pattern matches lower-case hex ids). Patterns opt in with an inline (?i).
_COMPILED = [(re.compile(p), r) for p, r in _PII_PATTERNS]
_SECRETS = _COMPILED[:3]


def scrub_pii(text: str) -> str:
    """Replace known PII / secret patterns with safe placeholders."""
    if not text:
        return text
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text


def scrub_secrets(text: str) -> str:
    """Replace credentials only (bearer tokens, API keys, ``secret=...``); identifiers are left intact."""
    if not text:
        return text
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text
