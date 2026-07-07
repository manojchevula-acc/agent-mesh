"""Write/read the readable turn rows (architecture §5.1).

One row per answered turn: question, answer, tier/tool, and (analytical) validated SQL.
This is what the UI renders and auditors query — separate from the checkpointer blobs.
No-op when no metadata DB is configured (dev).
"""

from __future__ import annotations

from sqlalchemy import insert, select

from sql_agent.logging_config import get_logger

from .db import get_engine, turns

log = get_logger("memory.turns")


def record_turn(*, session_id, user_id, turn_ref, question, answer,
                tier=None, tool=None, validated_sql=None, rows_returned=None) -> None:
    engine = get_engine()
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(insert(turns).values(
                session_id=session_id, user_id=user_id, turn_ref=turn_ref,
                question=question, answer=answer, tier=tier, tool=tool,
                validated_sql=validated_sql, rows_returned=rows_returned,
            ))
    except Exception as exc:           # readable history must never fail the request
        log.error("turn persist failed | %s", exc)


def list_turns(session_id: str, limit: int = 200) -> list[dict]:
    """Return a session's turns oldest-first, for rendering the chat."""
    engine = get_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(turns).where(turns.c.session_id == session_id)
            .order_by(turns.c.created_at.asc()).limit(limit)
        )
        return [dict(r._mapping) for r in rows]
