"""Lightweight column-relevance detection for the live question (Phase 4).

Pure lexical — token overlap between the (glossary-expanded) question and each
candidate column's ``name + desc`` from the semantic layer — no embeddings, no LLM
call. Feeds the "relevant columns" step of retrieval filtering and the column-match
factor in the weighted few-shot score (``memory/example_ranker.py``).

Scoped to the tables schema-retrieval already selected for the question (``tables``),
mirroring how ``semantic_layer/selector.py`` scopes tables before the schema-link LLM
step — the same "retrieve a small candidate set with cheap signals first" pattern.
"""

from __future__ import annotations

import re

from sql_agent.semantic_layer.glossary import glossary_map
from sql_agent.semantic_layer.loader import load_semantic_layer

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "by", "to", "is", "are",
    "our", "their", "have", "has", "what", "which", "who", "how", "with", "at", "we",
    "us", "show", "list", "give", "me",
})


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def select_columns(question: str, tables: set[str] | None = None, top_k: int = 8) -> set[str]:
    """Columns from ``tables`` (or every governed table if ``None``) whose name/desc
    lexically overlaps ``question``, best matches first, capped at ``top_k``.

    A column whose name is mapped from a business-glossary term found in the question
    is always included (a glossary hit is a strong, curated signal) in addition to the
    token-overlap ranking. Never raises: an empty/unknown ``tables`` set degrades to
    an empty result rather than erroring, so callers can treat "no signal" safely.
    """
    layer = load_semantic_layer()
    wanted_tables = tables if tables is not None else set(layer.tables)

    q_tokens = _tokens(question)
    glossary_cols: set[str] = set()
    lower_q = question.lower()
    for term, cols in glossary_map().items():
        if re.search(rf"\b{re.escape(term)}\b", lower_q):
            glossary_cols.update(c.lower() for c in cols)

    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for tname in wanted_tables:
        table = layer.tables.get(tname)
        if table is None:
            continue
        for cname, col in table.columns.items():
            key = cname.lower()
            if key in seen:
                continue
            seen.add(key)
            doc_tokens = _tokens(f"{cname} {col.desc}")
            overlap = len(doc_tokens & q_tokens)
            if key in glossary_cols:
                overlap += 2  # curated signal outweighs incidental lexical overlap
            if overlap > 0:
                scored.append((overlap, key))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return {c for _, c in scored[:top_k]}
