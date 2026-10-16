"""Prompt provenance (WP1-CORE-04 step 3).

The template id and version stay stable even if someone edits a prompt in
production; the SHA-256 of the text actually sent does not. Only the hash is
recorded — never the prompt text.
"""
from __future__ import annotations

import hashlib
from typing import Any

from opentelemetry import trace

from gernas.attributes import Ctx
from gernas.tracer import safe_set


def prompt_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def record_prompt(span: Any, template_id: str | None, version: str | None, text: str | None) -> str | None:
    """Stamp template id, version and SHA-256 of *text* on *span* (current span if None)."""
    span = span if span is not None else trace.get_current_span()
    safe_set(span, Ctx.PROMPT_TEMPLATE_ID, template_id)
    safe_set(span, Ctx.PROMPT_VERSION, None if version is None else str(version))
    if text is None:
        return None
    digest = prompt_sha256(text)
    safe_set(span, Ctx.PROMPT_SHA256, digest)
    return digest
