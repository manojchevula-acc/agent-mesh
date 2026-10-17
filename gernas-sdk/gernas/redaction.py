"""PDPL redaction — runs on every attribute value before the SDK emits it.

Blueprint §2 critical principle: no unrestricted raw prompts, raw responses,
customer data, account numbers, credentials, secrets or authentication tokens.

1. an optional application hook (:func:`set_redaction_hook`) may return :data:`DROP`
   or a replacement value;
2. keys on the registry denylist (raw user id, prompt/completion and framework
   payload keys) are dropped;
3. string values of keys that are not in the registry are PII-scrubbed; values of
   registry keys (identifiers, enums) are scrubbed for credentials only, so ids stay intact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gernas.attributes import DENYLIST, REGISTRY
from gernas.utils.sanitizer import scrub_pii, scrub_secrets


class _Drop:
    __slots__ = ()

    def __repr__(self) -> str:
        return "DROP"


DROP: Any = _Drop()
RedactionHook = Callable[[str, Any], Any]
_hook: RedactionHook | None = None


def set_redaction_hook(hook: RedactionHook | None) -> None:
    """Install an application hook ``hook(key, value) -> value | DROP`` (runs first)."""
    global _hook
    _hook = hook


def redact(key: str, value: Any) -> tuple[Any, bool]:
    """Return ``(value_or_DROP, changed)`` for one attribute write."""
    changed = False
    if _hook is not None:
        new = _hook(key, value)
        if new is DROP:
            return DROP, True
        changed = new is not value
        value = new
    if key in DENYLIST:
        return DROP, True
    if isinstance(value, str):
        scrubbed = scrub_secrets(value) if key in REGISTRY else scrub_pii(value)
        if scrubbed != value:
            return scrubbed, True
    return value, changed
