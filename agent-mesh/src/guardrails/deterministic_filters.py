"""Deterministic guardrails (layer 1 of defense in depth).

Hard, regex/rule-based checks that run BEFORE any LLM sees the input and AGAIN on
output. They do not depend on model judgement, so they cannot be talked around by
prompt injection. The LLM-based ComplianceAgent provides a complementary second
layer (semantic review).

Detects:
- Prompt injection ("ignore previous instructions", system-prompt overrides, ...)
- PII (emails, SSNs, credit cards, phone numbers)
- Destructive / harmful intent (delete, drop table, wipe, rm -rf, exfiltrate, ...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# --- Pattern banks -----------------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)",
    r"disregard\s+(the\s+)?(system|previous|above)\b",
    r"forget\s+(everything|your\s+instructions|the\s+rules)",
    r"you\s+are\s+now\s+(a|an|in)\b",
    r"\bact\s+as\s+(?:a\s+)?(?:dan|jailbreak|developer\s+mode)",
    r"new\s+instructions?\s*:",
    r"system\s+prompt\s*[:=]",
    r"reveal\s+(your\s+)?(system\s+prompt|instructions|hidden)",
    r"override\s+(your\s+)?(safety|guardrails|policy)",
    r"pretend\s+(that\s+)?you\s+(are|have)\b",
]

_PII_PATTERNS = {
    "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "CREDIT_CARD": r"\b(?:\d[ -]?){13,16}\b",
    "PHONE": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
}

_DESTRUCTIVE_PATTERNS = [
    r"\b(delete|drop|truncate|wipe|erase|destroy|purge)\b.*\b(table|database|db|records?|files?|accounts?|data|users?)\b",
    r"\brm\s+-rf\b",
    r"\bdrop\s+table\b",
    r"\bformat\s+(the\s+)?(disk|drive|c:)\b",
    r"\b(shutdown|terminate|kill)\b.*\b(server|system|service|production)\b",
    r"\b(exfiltrate|leak|steal|dump)\b.*\b(data|records?|credentials?|secrets?)\b",
    r"\bgrant\s+(myself|me)\b.*\b(admin|root|access)\b",
    r"\b(disable|bypass)\b.*\b(security|auth|authentication|logging|audit)\b",
]

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]
_PII_RE = {label: re.compile(p) for label, p in _PII_PATTERNS.items()}
_DESTRUCTIVE_RE = [re.compile(p, re.IGNORECASE) for p in _DESTRUCTIVE_PATTERNS]


@dataclass
class GuardrailResult:
    allowed: bool
    violations: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.violations) if self.violations else "OK"


# --- Detectors ---------------------------------------------------------------

def detect_prompt_injection(text: str) -> List[str]:
    if not text:
        return []
    return [f"prompt_injection: matched '{r.pattern}'" for r in _INJECTION_RE if r.search(text)]


def detect_pii(text: str) -> List[str]:
    if not text:
        return []
    return [f"pii: {label} detected" for label, r in _PII_RE.items() if r.search(text)]


def detect_destructive_intent(text: str) -> List[str]:
    if not text:
        return []
    return [f"destructive_intent: matched '{r.pattern}'" for r in _DESTRUCTIVE_RE if r.search(text)]


def redact_pii(text: str) -> str:
    """Replaces PII spans with category tokens. Used on outputs/logs."""
    if not text or not isinstance(text, str):
        return text
    redacted = text
    for label, r in _PII_RE.items():
        redacted = r.sub(f"[REDACTED_{label}]", redacted)
    return redacted


def screen_input(text: str) -> GuardrailResult:
    """Hard gate for incoming user requests. Blocks injection / PII / destructive."""
    violations: List[str] = []
    categories: List[str] = []

    inj = detect_prompt_injection(text)
    pii = detect_pii(text)
    destr = detect_destructive_intent(text)

    if inj:
        categories.append("prompt_injection")
        violations.extend(inj)
    if pii:
        categories.append("pii")
        violations.extend(pii)
    if destr:
        categories.append("destructive_intent")
        violations.extend(destr)

    return GuardrailResult(allowed=not violations, violations=violations, categories=categories)
