"""Structural introspection of a SQL string — which TABLES and COLUMNS does it touch?

Shared by materialize_gold.py (to verify the dataset's declared gold_tables/gold_columns
actually match the gold_sql) and by scorers.py (to score the agent's table/column
selection against the gold's). Parsing is done with sqlglot — the same parser the
production validator (sql_agent/validation/sql_validator.py) uses — so "which tables does
this query reference" means the same thing here as it does at the safety gate.

Deliberately structural, never string-based: `SELECT a FROM t` and `select A from T` have
the same footprint. Output aliases are excluded — in
    SELECT CASE ... END AS exposure_band ... GROUP BY exposure_band
`exposure_band` is a projection this query invents, not a column it reads, and counting it
would penalise an agent that solved the question with a different alias.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

DIALECT = "mysql"


def _select_aliases(ast) -> set[str]:
    """Names the query INVENTS (`... AS foo`) — these are outputs, not columns read."""
    aliases: set[str] = set()
    for alias in ast.find_all(exp.Alias):
        if alias.alias:
            aliases.add(alias.alias.lower())
        # a table alias (`FROM t AS x`) is handled separately; only projection aliases
        # can collide with column names in GROUP BY / ORDER BY.
    return aliases


def extract(sql: str) -> tuple[set[str], set[str]]:
    """Return (tables, columns) referenced by `sql`, both lowercase and unqualified.

    Tables are bare names (`fab_semantic.margin_analysis` -> `margin_analysis`), matching
    how the validator's whitelist and the semantic layer's schema.yaml key them. Columns
    are bare names too, since the same column may be qualified by alias, by table, or not
    at all depending on how the query was written.

    Raises nothing: an unparseable string yields empty sets, so a caller scoring a broken
    agent query gets "selected no tables" rather than an exception.
    """
    try:
        ast = sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception:  # noqa: BLE001 — unparseable SQL is a scoreable outcome, not a crash
        return set(), set()
    if ast is None:
        return set(), set()

    tables = {t.name.lower() for t in ast.find_all(exp.Table) if t.name}
    aliases = _select_aliases(ast)
    columns = {c.name.lower() for c in ast.find_all(exp.Column) if c.name}
    # `SELECT *` yields a Star, not a Column — nothing to add, and nothing to drop.
    return tables, columns - aliases


def tables_of(sql: str) -> set[str]:
    return extract(sql)[0]


def columns_of(sql: str) -> set[str]:
    return extract(sql)[1]


def overlap(gold: set[str], got: set[str]) -> dict:
    """Set comparison used by the selection scorers.

    `recall` is the headline number — of what gold needed, how much did the agent use?
    An agent that reads gold's tables plus an extra one has recall 1.0 but precision < 1;
    both are reported so the summary can distinguish "missed a join" from "over-joined".
    """
    gold, got = set(gold), set(got)
    if not gold:
        return {"recall": None, "precision": None, "missing": [], "extra": sorted(got)}
    hit = gold & got
    return {
        "recall": round(len(hit) / len(gold), 3),
        "precision": round(len(hit) / len(got), 3) if got else 0.0,
        "missing": sorted(gold - got),
        "extra": sorted(got - gold),
    }
