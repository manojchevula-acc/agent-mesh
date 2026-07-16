"""Business glossary (Component B) — business vocabulary -> physical column names.

Backed by ``sql_agent/data/business_glossary.yaml`` so the vocabulary can be extended
by editing data, not code. Two consumers:

  glossary_expand   -> appends mapped physical column names to a question, so both
                       retrieval rankers (dense + BM25) see the physical vocabulary
                       even when the user only used the business term. Purely
                       additive; never removes the user's own wording.
  matched_terms     -> returns which CANONICAL business terms (not columns) appear in
                       a text, for few-shot metadata (``business_terms``), the prompt's
                       glossary block, and the retrieval scoring's term-overlap factor.

``sql_agent/semantic_layer/catalog.py`` re-exports ``glossary_expand`` for backward
compatibility with existing callers (``selector.py``, ``memory/example_index.py``).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

GLOSSARY_PATH = Path(__file__).resolve().parents[1] / "data" / "business_glossary.yaml"


@lru_cache(maxsize=1)
def _entries() -> list[dict]:
    """Load and cache the glossary YAML: [{"term", "columns", "category"}, ...]."""
    data = yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8")) or {}
    return data.get("terms", []) or []


@lru_cache(maxsize=1)
def glossary_map() -> dict[str, list[str]]:
    """term -> columns, for callers that only need the column mapping."""
    return {e["term"]: list(e.get("columns") or []) for e in _entries()}


def glossary_expand(question: str) -> str:
    """Append the physical column names for any business synonym present in
    ``question``. Returns the original text plus the mapped tokens; purely additive."""
    lower = question.lower()
    extra: list[str] = []
    for entry in _entries():
        term = entry["term"]
        if re.search(rf"\b{re.escape(term)}\b", lower):
            extra.extend(entry.get("columns") or [])
    if not extra:
        return question
    return f"{question} {' '.join(dict.fromkeys(extra))}"


def matched_terms(text: str) -> list[str]:
    """Canonical business terms (e.g. "policy margin") whose synonym appears in
    ``text``, longest-term-first so a multi-word term matched inside a shorter one
    isn't duplicated as two separate hits (e.g. "policy margin" also containing
    "margin"). Order is stable (glossary file order) after the length sort."""
    lower = text.lower()
    hits: list[str] = []
    for entry in sorted(_entries(), key=lambda e: -len(e["term"])):
        term = entry["term"]
        if re.search(rf"\b{re.escape(term)}\b", lower):
            hits.append(term)
    return list(dict.fromkeys(hits))


def render_glossary_block(terms: list[str] | None = None) -> str:
    """Render a business-glossary section for the generation prompt (Phase 11).

    ``terms`` restricts the block to the given canonical terms (e.g. the ones
    ``matched_terms`` found in the live question); omit to render the full glossary.
    Returns "" for an empty glossary/selection so callers can skip the section cleanly.
    """
    entries = _entries()
    if terms is not None:
        wanted = set(terms)
        entries = [e for e in entries if e["term"] in wanted]
    if not entries:
        return ""
    lines = ["Business glossary (business term -> physical column(s)):"]
    for e in entries:
        cols = ", ".join(e.get("columns") or [])
        lines.append(f"- {e['term']} -> {cols}")
    return "\n".join(lines)
