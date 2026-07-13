"""Read approved curated examples to inject as few-shot into analytical generation."""

from __future__ import annotations

from sqlalchemy import select

from sql_agent.config import settings

from .db import examples, get_engine


def approved_examples(limit: int | None = None) -> list[dict]:
    if not settings.examples_enabled:
        return []
    engine = get_engine()
    if engine is None:
        return []
    limit = limit or settings.max_fewshot_examples
    with engine.connect() as conn:
        rows = conn.execute(
            select(examples).where(examples.c.status == "approved").limit(limit)
        )
        return [dict(r._mapping) for r in rows]


def all_approved_examples() -> list[dict]:
    """Every approved example (no limit) — the corpus the Pattern Retriever ranks over.

    Unlike ``approved_examples`` (a static head slice injected into the ReAct prompt for
    tool selection), this returns the full approved set so ``relevant_examples`` can pick
    the ones most relevant to a specific dynamic-tier question.
    """
    if not settings.examples_enabled:
        return []
    engine = get_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(examples).where(examples.c.status == "approved")
        )
        return [dict(r._mapping) for r in rows]


def relevant_examples(
    question: str,
    k: int | None = None,
    tier: str | None = None,
    tables_hint: list[str] | None = None,
) -> list[dict]:
    """Intent-aware few-shot: the top-``k`` approved examples most relevant to
    ``question`` (hybrid dense+BM25 RRF, soft-boosted by ``tier`` / ``tables_hint``).

    Returns [] when examples are disabled or none are stored, so the dynamic-tier
    generator prompt is byte-identical to today's when the feature is off.
    """
    rows = all_approved_examples()
    if not rows:
        return []
    # Imported lazily so this module (and the memory package) imports cleanly without the
    # optional `retrieval` extra when the feature is off.
    from .example_index import rank_examples

    return rank_examples(question, rows, tier=tier, tables_hint=tables_hint, k=k)


def render_examples_block(rows: list[dict]) -> str:
    """Render approved examples as a few-shot block for the generation prompt."""
    if not rows:
        return ""
    lines = ["Worked examples (for guidance; the schema and safety rules still apply):"]
    for r in rows:
        lines.append(f"- Q: {r['question']}")
        if r.get("validated_sql"):
            lines.append(f"  SQL: {r['validated_sql']}")
    return "\n".join(lines)
