"""Write/read feedback rows. Screens correction text against blocked columns."""

from __future__ import annotations

from sqlalchemy import insert, select

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.memory.db import feedback, get_engine
from sql_agent.semantic_layer.loader import BLOCKED_COLUMNS

log = get_logger("feedback")


def _screen(text: str | None) -> str | None:
    if not text:
        return text
    if any(col in text.lower() for col in BLOCKED_COLUMNS):
        log.warning("feedback correction dropped: referenced a blocked column")
        return None
    return text


def record(*, session_id, user_id, turn_ref=None, signal_type, polarity="neutral",
           stage=None, correction_text=None) -> None:
    if not settings.feedback_enabled:
        return
    engine = get_engine()
    if engine is None:
        log.info("feedback (no metadata DB) | %s/%s | %s", session_id, stage, polarity)
        return
    with engine.begin() as conn:
        conn.execute(insert(feedback).values(
            session_id=session_id, user_id=user_id, turn_ref=turn_ref,
            signal_type=signal_type, polarity=polarity, stage=stage,
            correction_text=_screen(correction_text), status="new",
        ))
    log.info("feedback recorded | %s | %s | %s", session_id, signal_type, polarity)


def review_queue(limit: int = 100) -> list[dict]:
    engine = get_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(feedback).where(feedback.c.status == "new")
            .order_by(feedback.c.created_at.desc()).limit(limit)
        )
        return [dict(r._mapping) for r in rows]
