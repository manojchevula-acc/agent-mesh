"""Dialect helpers so the same agent runs on PostgreSQL, MySQL, or SQLite.

The active dialect is taken from ``settings.db_dialect`` if set, otherwise inferred
from the DSN scheme. Everything dialect-specific lives here:

  * String concatenation: Postgres/SQLite use ``a || b``; MySQL needs ``CONCAT(a, b)``
    (``||`` is logical OR in MySQL).
  * The sqlglot read-dialect used to PARSE/validate generated SQL.
  * The human label + notes injected into the tier-3 generation prompt, so the LLM
    writes SQL that is correct for the target engine.
  * Per-statement timeout syntax (see db/executor.py).

``LIMIT`` and the rest of the generated SQL are common to all three.
"""

from __future__ import annotations

from sql_agent.config import settings

POSTGRES = "postgresql"
MYSQL = "mysql"
SQLITE = "sqlite"

SUPPORTED = (POSTGRES, MYSQL, SQLITE)


def _normalize(name: str) -> str:
    name = name.strip().lower()
    if name in ("mysql", "mariadb"):
        return MYSQL
    if name in ("postgres", "postgresql", "pg", "psql"):
        return POSTGRES
    if name in ("sqlite", "sqlite3"):
        return SQLITE
    return name


def current_dialect() -> str:
    """Return the active dialect: 'postgresql' | 'mysql' | 'sqlite'.

    Prefers the explicit setting; otherwise infers from the DSN driver scheme
    (e.g. 'mysql+pymysql://...' -> mysql, 'sqlite:///file.db' -> sqlite,
    'postgresql+psycopg2://...' -> postgresql).
    """
    if settings.db_dialect:
        return _normalize(settings.db_dialect)
    scheme = settings.db_dsn.split("://", 1)[0].lower()
    if "mysql" in scheme or "mariadb" in scheme:
        return MYSQL
    if "sqlite" in scheme:
        return SQLITE
    return POSTGRES


# --- sqlglot integration (used by the validator to parse the right dialect) ----

_SQLGLOT = {POSTGRES: "postgres", MYSQL: "mysql", SQLITE: "sqlite"}


def sqlglot_dialect() -> str | None:
    """The sqlglot dialect name for parsing/validating generated SQL."""
    return _SQLGLOT.get(current_dialect())


# --- prompt grounding (so the generator writes correct SQL for this engine) -----

_LABELS = {POSTGRES: "PostgreSQL", MYSQL: "MySQL 8.0", SQLITE: "SQLite"}

_NOTES = {
    POSTGRES: (
        "- String concatenation: use a || b.\n"
        "- Row limiting: use LIMIT n.\n"
        "- Date literals: 'YYYY-MM-DD'."
    ),
    MYSQL: (
        "- String concatenation: use CONCAT(a, b). The || operator is NOT "
        "concatenation in MySQL.\n"
        "- Row limiting: use LIMIT n.\n"
        "- Quote identifiers with backticks only if needed.\n"
        "- Date literals: 'YYYY-MM-DD'."
    ),
    SQLITE: (
        "- String concatenation: use a || b.\n"
        "- Row limiting: use LIMIT n.\n"
        "- Dates are stored as TEXT in 'YYYY-MM-DD' form; compare them lexically."
    ),
}


def dialect_label() -> str:
    """Human-readable engine name for the generation prompt."""
    return _LABELS.get(current_dialect(), "PostgreSQL")


def dialect_notes() -> str:
    """Dialect-specific guidance injected into the generation prompt."""
    return _NOTES.get(current_dialect(), _NOTES[POSTGRES])


# --- portable SQL fragments used by the tools ---------------------------------

def concat(*parts: str) -> str:
    """Build a dialect-appropriate string concatenation of raw SQL expressions.

    Postgres / SQLite -> (a || b || c); MySQL -> CONCAT(a, b, c).
    """
    if current_dialect() == MYSQL:
        return "CONCAT(" + ", ".join(parts) + ")"
    return "(" + " || ".join(parts) + ")"


def csv_membership_clause(column: str, param: str) -> str:
    """WHERE fragment matching `param` as a whole token inside a comma-delimited
    `column` (e.g. eligible_segments / eligible_tenors), avoiding partial-match
    false positives like '1M' inside '12M' by padding with commas.

    Postgres/SQLite: (',' || col || ',') LIKE ('%,' || :param || ',%')
    MySQL:           CONCAT(',', col, ',') LIKE CONCAT('%,', :param, ',%')
    """
    padded_column = concat("','", column, "','")
    padded_pattern = concat("'%,'", f":{param}", "',%'")
    return f"{padded_column} LIKE {padded_pattern}"
