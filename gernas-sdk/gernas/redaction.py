"""PDPL redaction hook — runs on every attribute value before it is emitted.

Order of evaluation inside :func:`redact`:

1. an optional application hook installed with :func:`set_redaction_hook`
   (may return :data:`DROP` or a replacement value);
2. the registry action for the key (``registry/redaction-matrix.yaml``):
   ``drop`` → never emitted, ``fingerprint``/``tokenise`` → SHA-256,
   ``mask`` → PII-scrubbed, and only when ``GERNAS_CAPTURE_PAYLOADS`` grants it;
3. for keys that are not in the registry, string values are PII-scrubbed.

``gen_ai.prompt`` / ``gen_ai.completion`` and raw user ids are therefore dropped by
default (principle 8).
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from gernas.attributes import REDACTION_ACTIONS, REGISTRY
from gernas.utils.sanitizer import scrub_pii


class _Drop:
    __slots__ = ()

    def __repr__(self) -> str:
        return "DROP"


DROP: Any = _Drop()

RedactionHook = Callable[[str, Any], Any]
_hook: RedactionHook | None = None
_capture_payloads = False


def set_redaction_hook(hook: RedactionHook | None) -> None:
    """Install an application-level hook ``hook(key, value) -> value | DROP``."""
    global _hook
    _hook = hook


def set_capture_payloads(enabled: bool) -> None:
    """Allow ``mask``-action payload fields (redacted prompt/completion) to be emitted."""
    global _capture_payloads
    _capture_payloads = bool(enabled)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()


def redact(key: str, value: Any) -> tuple[Any, bool]:
    """Return ``(value_or_DROP, changed)`` for one attribute write."""
    changed = False
    if _hook is not None:
        new = _hook(key, value)
        if new is DROP:
            return DROP, True
        changed = new is not value
        value = new
    action = REDACTION_ACTIONS.get(key)
    if action is None:
        if key not in REGISTRY and isinstance(value, str):
            scrubbed = scrub_pii(value)
            if scrubbed != value:
                return scrubbed, True
        return value, changed
    if action == "drop":
        return DROP, True
    if action in ("fingerprint", "tokenise"):
        return fingerprint(value), True
    if action == "mask":
        if not _capture_payloads:
            return DROP, True
        return scrub_pii(str(value)), True
    return value, changed
