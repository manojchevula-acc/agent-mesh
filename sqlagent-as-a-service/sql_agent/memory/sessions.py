"""Session bookkeeping. A session belongs to one user; session_id is the memory thread."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import get_engine, sessions, turns


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def touch_session(session_id: str, user_id: str, title: str | None = None) -> None:
    """Upsert the session and bump last_active. No-op if no metadata DB configured."""
    engine = get_engine()
    if engine is None:
        return
    with engine.begin() as conn:
        stmt = pg_insert(sessions).values(
            session_id=session_id, user_id=user_id, title=title,
        ).on_conflict_do_update(
            index_elements=["session_id"],
            set_={"last_active_at": func.now()},
        )
        conn.execute(stmt)


def rename_session(session_id: str, user_id: str, title: str) -> bool:
    """Set a chat's display title. Scoped to the owning user so one user can't
    rename another's chat. Returns True if a row was updated, False otherwise
    (no metadata DB, or the session doesn't belong to this user)."""
    engine = get_engine()
    if engine is None:
        return False
    with engine.begin() as conn:
        result = conn.execute(
            update(sessions)
            .where(sessions.c.session_id == session_id, sessions.c.user_id == user_id)
            .values(title=title)
        )
    return result.rowcount > 0


def delete_session(session_id: str, user_id: str) -> bool:
    """Delete a chat and its readable turns. Scoped to the owning user. The
    checkpointer's working-memory blobs for this thread are left to age out —
    they're keyed by thread_id and harmless once the session row is gone.
    Returns True if the session existed and belonged to this user."""
    engine = get_engine()
    if engine is None:
        return False
    with engine.begin() as conn:
        deleted = conn.execute(
            delete(sessions)
            .where(sessions.c.session_id == session_id, sessions.c.user_id == user_id)
        )
        if deleted.rowcount > 0:
            conn.execute(delete(turns).where(turns.c.session_id == session_id))
    return deleted.rowcount > 0


def list_sessions(user_id: str, limit: int = 50) -> list[dict]:
    engine = get_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(sessions).where(sessions.c.user_id == user_id)
            .order_by(sessions.c.last_active_at.desc()).limit(limit)
        )
        return [dict(r._mapping) for r in rows]
