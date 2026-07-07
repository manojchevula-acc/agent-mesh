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
