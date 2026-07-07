"""Parses schema.yaml into typed objects and derives the allow-lists.

The validator's table/column whitelist and the dynamic-generation prompt both derive
from THIS module, so the allow-list cannot drift between generation and validation
(Design Document §4.5, "one artefact, enforced in two places").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

SCHEMA_PATH = Path(__file__).with_name("schema.yaml")

_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    sensitivity: str = "internal"
    filterable: bool = False
    unit: str | None = None
    values: tuple[str, ...] = field(default_factory=tuple)
    desc: str = ""
    note: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    grain: str
    primary_key: str
    sensitivity_default: str
    columns: dict[str, Column]
    joins: dict[str, str]
    schema: str = ""  # owning DB schema (e.g. fab_curated / fab_semantic); "" = unqualified
    is_view: bool = False  # a pre-joined fab_semantic read model — query standalone, don't join

    @property
    def filterable_columns(self) -> list[str]:
        """Columns the semi-dynamic clause-builder is allowed to emit conditions on."""
        return [c.name for c in self.columns.values() if c.filterable]

    @property
    def qualified_name(self) -> str:
        """schema.table when a schema is declared, else the bare table name.

        This is the name the tier-3 generator must use in FROM/JOIN clauses so the
        SQL resolves correctly regardless of the connection's default schema — the
        views live in a different schema (fab_semantic) than the base tables."""
        return f"{self.schema}.{self.name}" if self.schema else self.name


@dataclass(frozen=True)
class SemanticLayer:
    tables: dict[str, Table]
    blocked_columns: frozenset[str]


def _coerce_column(name: str, spec: dict) -> Column:
    return Column(
        name=name,
        type=spec.get("type", "str"),
        sensitivity=spec.get("sensitivity", "internal"),
        filterable=bool(spec.get("filterable", False)),
        unit=spec.get("unit"),
        values=tuple(spec.get("values", []) or []),
        desc=spec.get("desc", ""),
        note=spec.get("note"),
        format=spec.get("format"),
    )


@lru_cache(maxsize=1)
def load_semantic_layer() -> SemanticLayer:
    raw = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))

    tables: dict[str, Table] = {}
    for tname, tspec in raw["tables"].items():
        columns = {
            cname: _coerce_column(cname, cspec)
            for cname, cspec in tspec["columns"].items()
        }
        schema = tspec.get("schema", raw.get("default_schema", ""))
        tables[tname] = Table(
            name=tname,
            grain=tspec.get("grain", ""),
            primary_key=tspec.get("primary_key", ""),
            sensitivity_default=tspec.get("sensitivity_default", "internal"),
            columns=columns,
            joins=tspec.get("joins", {}) or {},
            # Per-table schema, falling back to the file-level default_schema.
            schema=schema,
            # A fab_semantic object is a pre-joined view; overridable via `view:` in yaml.
            is_view=bool(tspec.get("view", schema == "fab_semantic")),
        )

    blocked = {c.lower() for c in raw.get("blocked_columns", [])}
    # Merge any per-table blocked_columns declarations.
    for tspec in raw["tables"].values():
        blocked.update(c.lower() for c in (tspec.get("blocked_columns") or []))

    return SemanticLayer(tables=tables, blocked_columns=frozenset(blocked))


def table_columns() -> dict[str, frozenset[str]]:
    """Map each governed table to the (lower-cased) set of its declared columns. Used by
    the validator's column-binding check to verify a qualified column (table.col) actually
    belongs to that table — the semantic layer is the authority on which columns exist."""
    layer = load_semantic_layer()
    return {
        name: frozenset(c.lower() for c in table.columns)
        for name, table in layer.tables.items()
    }


def relationship_graph() -> dict[str, dict[str, str]]:
    """Undirected adjacency of table -> {neighbour_table: join_rule} from the declared
    ``joins:`` blocks in schema.yaml. Both directions are populated so join-path search
    and join-closure can traverse either way. This is the FK graph the dynamic tier's
    deterministic join resolution and the validator's join-graph check consume."""
    layer = load_semantic_layer()
    graph: dict[str, dict[str, str]] = {name: {} for name in layer.tables}
    for name, table in layer.tables.items():
        for other, rule in table.joins.items():
            if other not in layer.tables:
                continue  # ignore joins to tables outside the governed layer
            graph[name][other] = rule
            graph[other].setdefault(name, rule)  # ensure reverse edge exists
    return graph


def join_closure(tables: set[str]) -> set[str]:
    """Expand a table set to include tables reachable via one declared join hop, so a
    join a selected table needs is never missing from the rendered schema. Bounded to
    the governed allow-list."""
    graph = relationship_graph()
    closed = set(tables)
    for t in list(tables):
        closed.update(graph.get(t, {}).keys())
    return {t for t in closed if t in ALLOWED_TABLES}


def enum_values(table: str, column: str) -> tuple[str, ...]:
    """The declared enum values for a column, or () if it has none."""
    t = load_semantic_layer().tables.get(table)
    if not t:
        return ()
    col = t.columns.get(column)
    return col.values if col else ()


def _phrase_in(needle: str, haystack: str) -> bool:
    """True if `needle` appears as a whole word/phrase inside `haystack`."""
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


# A duration expressed as a number + optional unit, e.g. "1Y", "1 year", "60-month",
# "12M", or a bare "12". Anchored so it only fires on a clean tenor token.
_NUM_UNIT = re.compile(
    r"(?i)^\s*(\d+)\s*[-\s]?\s*(y|yr|yrs|year|years|m|mo|mos|month|months)?\s*$"
)


def _to_months(raw: str) -> int | None:
    """Interpret a duration string as a MONTH count so unit differences are honoured.

    The governed tenors are month codes (1M..60M). Matching on the bare digit made
    "1Y" (1 year) collide with "1M" (1 month); converting to months first fixes that:
    "1Y"/"1 year" -> 12, "2Y" -> 24, "60-month"/"60M"/"60" -> 60. Returns None when no
    number is present.
    """
    m = _NUM_UNIT.match(raw)
    if m:
        n = int(m.group(1))
        unit = (m.group(2) or "m").lower()
        return n * 12 if unit.startswith("y") else n
    # Fallback: a number embedded in longer text -> treat as months (legacy behaviour).
    d = _DIGITS.search(raw)
    return int(d.group()) if d else None


def canonicalize(value, allowed: tuple[str, ...]):
    """Map a user-supplied filter value onto the column's declared governed value.

    The LLM tends to echo the user's wording ("Term Loan", "60-month") rather than
    the governed token ("Loan", "60M"), which then matches zero rows. This snaps the
    value back to the enum deterministically:
      1. exact, case-insensitive;
      2. tenor-style numeric codes (1M, 60M) — match on the number;
      3. a unique whole-word containment either direction ("Term Loan" -> "Loan").
    If nothing matches unambiguously the original value is returned unchanged, so
    behaviour is never worse than before.
    """
    if value is None or not allowed:
        return value
    raw = str(value).strip()
    if not raw:
        return value

    # 1. exact, case-insensitive
    for a in allowed:
        if a.lower() == raw.lower():
            return a

    # 2. tenor/duration codes: governed values are digits + a MONTH suffix (1M..60M).
    #    Interpret the input as a month count so year forms map correctly
    #    ("1Y"/"1 year" -> 12M, NOT 1M) instead of matching on the bare digit.
    if all(re.fullmatch(r"\d+[Mm]", a) for a in allowed):
        months = _to_months(raw)
        if months is not None:
            hits = [a for a in allowed
                    if (d := _DIGITS.search(a)) and int(d.group()) == months]
            if len(hits) == 1:
                return hits[0]
            # A duration was clearly expressed but matches no governed tenor. Do NOT
            # fall through to fuzzy containment (which mis-snapped "1Y" -> "1M"): return
            # the value unchanged so the caller's lookup misses cleanly rather than
            # silently pricing off the wrong tenor.
            return value

    # 3. unique whole-word containment either direction
    rl = raw.lower()
    hits = [a for a in allowed if _phrase_in(a.lower(), rl) or _phrase_in(rl, a.lower())]
    if len(hits) == 1:
        return hits[0]

    return value


def canonicalize_enum(table: str, column: str, value):
    """Convenience: canonicalize `value` against ``<table>.<column>``'s enum."""
    return canonicalize(value, enum_values(table, column))


_LAYER = load_semantic_layer()

# Allow-lists derived once and imported by the validator (Section 8) and tools.
ALLOWED_TABLES: frozenset[str] = frozenset(_LAYER.tables.keys())
ALLOWED_COLUMNS: frozenset[str] = frozenset(
    col.name.lower()
    for table in _LAYER.tables.values()
    for col in table.columns.values()
)
BLOCKED_COLUMNS: frozenset[str] = _LAYER.blocked_columns
