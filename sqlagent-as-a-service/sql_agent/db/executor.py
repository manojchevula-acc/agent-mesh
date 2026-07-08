"""execute(sql, params) -> rows, with timing and the per-statement timeout.

Every query — from every tier, including hand-written parameterised SQL — passes
through the six-check validator here before it touches the database. There is no
fast path (Design Document §7; Technical Spec §8).
"""

from __future__ import annotations

import re
import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.validation.exceptions import ExecutionCostError, QueryTimeoutError
from sql_agent.validation.sql_validator import SQLValidator

from .connection import get_engine

log = get_logger("db")


class Executor:
    """Read-only executor. Validates, then runs the SELECT, returning rows + timing."""

    def __init__(self) -> None:
        self._validator = SQLValidator()

    def execute(
        self, sql: str, params: dict | None = None, allowed_join_pairs=None,
        strict_columns: bool = False,
    ) -> "QueryResult":
        """Validate the SQL, then execute read-only.

        ``allowed_join_pairs`` is passed through to the validator's join-graph check (#7)
        and ``strict_columns`` enables the column-binding check (#8); both are supplied
        only by the dynamic tier (other tiers pass the defaults and skip those checks).
        When the execution guard is enabled, an EXPLAIN cost pre-flight runs before the
        query touches a potentially large table.

        Returns a QueryResult carrying the rows, the latency, and whether the row cap
        was hit (truncation flag).
        """
        params = params or {}
        safe_sql = self._validator.validate(
            sql, allowed_join_pairs=allowed_join_pairs, strict_columns=strict_columns
        )
        log.info("DB execute | sql=%s | params=%s", safe_sql, params)

        engine = get_engine()
        if settings.execution_guard_enabled:
            self._explain_guard(engine, safe_sql, params)
        started_at = time.time()
        try:
            with engine.connect() as conn:
                self._apply_statement_timeout(conn)
                cursor = conn.execute(text(safe_sql), params)
                rows = [dict(r._mapping) for r in cursor]
        except OperationalError as exc:  # statement timeout surfaces here
            if "timeout" in str(exc).lower():
                log.warning("DB timeout after %ss | sql=%s",
                            settings.statement_timeout_seconds, safe_sql)
                raise QueryTimeoutError(
                    f"Query exceeded {settings.statement_timeout_seconds}s timeout"
                ) from exc
            raise
        latency_ms = round((time.time() - started_at) * 1000)
        log.debug("DB done | rows=%d | latency=%dms", len(rows), latency_ms)

        truncated = len(rows) >= settings.row_cap
        return QueryResult(rows=rows, latency_ms=latency_ms, truncated=truncated,
                           started_at=started_at, sql=safe_sql, params=params)

    def _explain_guard(self, engine, sql: str, params: dict) -> None:
        """Check #8 — reject over-cost / unbounded-scan plans before executing against a
        large table. EXPLAIN itself does not run the query. Best-effort: an EXPLAIN that
        fails or that we cannot parse must NOT block a valid read (defence in depth)."""
        try:
            with engine.connect() as conn:
                dialect = conn.engine.dialect.name
                rows = conn.execute(text(f"EXPLAIN {sql}"), params).fetchall()
            cost = self._estimated_cost(rows, dialect)
        except Exception as exc:  # noqa: BLE001 — never block a read on the guard itself
            log.debug("EXPLAIN guard skipped | %s", exc)
            return
        if cost is not None and cost > settings.execution_cost_max:
            log.warning("EXPLAIN guard reject | est_cost=%s > %s",
                        cost, settings.execution_cost_max)
            raise ExecutionCostError(
                f"Estimated plan cost {cost:.0f} exceeds limit "
                f"{settings.execution_cost_max:.0f}"
            )

    @staticmethod
    def _estimated_cost(rows, dialect: str) -> float | None:
        """Extract an estimated cost/row-count from an EXPLAIN result. Dialect-specific
        and intentionally forgiving — returns None when it cannot be determined."""
        text_plan = " ".join(str(c) for r in rows for c in r)
        if dialect == "postgresql":
            # "... (cost=0.00..123.45 rows=10 width=8)" — take the upper bound.
            m = re.findall(r"cost=[\d.]+\.\.([\d.]+)", text_plan)
            return max((float(x) for x in m), default=None)
        if dialect == "mysql":
            # MySQL EXPLAIN exposes an estimated 'rows' examined per step.
            m = re.findall(r"\b(\d+)\b", text_plan)
            return max((float(x) for x in m), default=None)
        return None

    @staticmethod
    def _apply_statement_timeout(conn) -> None:
        """Best-effort per-statement timeout. Syntax is dialect-specific."""
        ms = settings.statement_timeout_seconds * 1000
        dialect = conn.engine.dialect.name  # 'postgresql' | 'mysql' | 'mssql' | ...
        try:
            if dialect == "postgresql":
                conn.execute(text(f"SET statement_timeout = {ms}"))
            elif dialect == "mysql":
                # MySQL 5.7+/8.0: per-statement SELECT timeout, in milliseconds.
                # (MariaDB uses max_statement_time in seconds; harmless if it errors.)
                conn.execute(text(f"SET SESSION max_execution_time = {ms}"))
            elif dialect == "sqlite":
                # SQLite has no statement timeout; busy_timeout bounds lock waits.
                conn.execute(text(f"PRAGMA busy_timeout = {ms}"))
            elif dialect in ("mssql", "pyodbc"):
                # Azure SQL: enforced via connection attribute / LOCK_TIMEOUT.
                conn.execute(text(f"SET LOCK_TIMEOUT {ms}"))
        except Exception:
            # Timeout enforcement is defence in depth; never block a valid read on it.
            pass


class QueryResult:
    """Lightweight carrier so tools/formatters get rows plus execution metadata."""

    def __init__(self, rows: list[dict], latency_ms: int, truncated: bool,
                 started_at: float, sql: str, params: dict | None = None) -> None:
        self.rows = rows
        self.latency_ms = latency_ms
        self.truncated = truncated
        self.started_at = started_at
        self.sql = sql
        self.params = params or {}

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)


# Module-level singleton imported as `from sql_agent.db import db`.
db = Executor()
