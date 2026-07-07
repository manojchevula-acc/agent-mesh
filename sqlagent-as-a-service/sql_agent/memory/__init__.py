"""Conversation memory subsystem — checkpointer, sessions, readable turns, examples.

Public surface used by the agent + service. Everything degrades to a no-op when no
metadata DB is configured (dev), so the agent runs with just the in-memory checkpointer.
"""

from .checkpointer import get_checkpointer
from .db import init_tables
from .examples import approved_examples, render_examples_block
from .sessions import (
    delete_session,
    list_sessions,
    new_session_id,
    rename_session,
    touch_session,
)
from .turns import list_turns, record_turn

__all__ = [
    "get_checkpointer", "init_tables",
    "new_session_id", "touch_session", "list_sessions",
    "rename_session", "delete_session",
    "record_turn", "list_turns",
    "approved_examples", "render_examples_block",
]
