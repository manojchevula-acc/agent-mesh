"""Create the agent metadata tables and the conversation-thread table.

Usage (prod):
    AGENT_DB_DSN=postgresql+psycopg://user:pass@host/agentdb \
    CHECKPOINTER_BACKEND=postgres python scripts/init_agent_db.py

With no AGENT_DB_DSN this is a no-op (dev uses the in-memory store and the
metadata writers are no-ops).
"""

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.memory import init_tables

log = get_logger("init_agent_db")


def main() -> None:
    init_tables()  # turns / sessions / feedback / examples (no-op if AGENT_DB_DSN unset)
    if settings.checkpointer_backend == "postgres":
        from sql_agent.memory.conversation_store import get_conversation_store
        # Opens the pool and runs the agent_threads DDL.
        get_conversation_store()
    log.info("metadata DB ready (backend=%s, dsn_set=%s)",
             settings.checkpointer_backend, bool(settings.agent_db_dsn))


if __name__ == "__main__":
    main()
