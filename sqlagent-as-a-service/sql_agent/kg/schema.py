"""Node/edge model for the metadata Knowledge Graph, plus the serialisable artifact.

This is the KG's contract. Everything else in ``sql_agent/kg`` either produces one of these
objects (builder.py) or consumes one, so the graph shape is declared in exactly one place —
the same "one artefact, enforced in two places" discipline the semantic layer follows.

NODES
  Table   — one governed table or view (fab_curated base tables, fab_semantic views).
  Column  — one column of one table. Keyed ``table.column``; never a bare column name,
            because the same business column (customer_segment) exists on several tables
            and the join/type checks need to know WHICH one.
  Term    — one canonical business-glossary term. The ONLY node type that gets embedded
            (design §7.1: column-node embeddings measured +0 recall).

EDGES
  HAS_COLUMN       Table  -> Column
  HAS_FOREIGN_KEY  Table  -> Table    (a joinable relationship, with its column pairs)
  REFERENCES       Column -> Column   (the column-level view of the same relationship)
  DEFINES          Term   -> Column   (business vocabulary -> physical column)
  HAS_TERM         Table  -> Term     (derived; term-seeded table recall)

HAS_FOREIGN_KEY and REFERENCES are two projections of ONE ForeignKeyEdge rather than two
independently stored edges — storing them separately is how a metadata graph drifts against
itself. ``column_pairs`` carries the composite case (customer_master <-> pricing_policy joins
on customer_segment AND risk_category), which a single from/to pair cannot express and which
check #12 needs.

NO ROW DATA. Column nodes carry declared ENUM values — governance metadata already in
schema.yaml and already rendered into the generation prompt — and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

# Provenance. Every node/edge records where its facts came from, so an auditor can answer
# "why did the agent believe these two tables join?" without re-deriving it.
SOURCE_INFORMATION_SCHEMA = "information_schema"
SOURCE_SEMANTIC_LAYER = "semantic_layer"
SOURCE_DATA_DICTIONARY = "data_dictionary"
SOURCE_GLOSSARY = "business_glossary"
SOURCE_INFERRED = "inferred"

# Edge lifecycle. Only ACTIVE edges are traversed and enforced. PROPOSED edges (name
# matching, data_dictionary prose) are recorded for review and never acted on — guessed
# joins are the failure mode this layer removes; the KG must not reintroduce them.
STATUS_ACTIVE = "active"
STATUS_PROPOSED = "proposed"

MANY_TO_ONE = "many-to-one"
ONE_TO_MANY = "one-to-many"
ONE_TO_ONE = "one-to-one"
MANY_TO_MANY = "many-to-many"


@dataclass(frozen=True)
class TableNode:
    name: str
    db_schema: str = ""            # fab_curated | fab_semantic
    is_view: bool = False
    grain: str = ""
    purpose: str = ""
    primary_key: str = ""
    row_estimate: int = 0          # information_schema.TABLES.TABLE_ROWS (views: 0)
    search_terms: str = ""
    source: str = SOURCE_SEMANTIC_LAYER

    @property
    def qualified_name(self) -> str:
        return f"{self.db_schema}.{self.name}" if self.db_schema else self.name


@dataclass(frozen=True)
class ColumnNode:
    table: str
    name: str
    data_type: str = ""            # physical, e.g. decimal(10,4)
    logical_type: str = "str"      # str|int|float|date|enum|bool
    nullable: bool = True
    is_primary_key: bool = False
    is_unique: bool = False        # drives cardinality inference
    sensitivity: str = "internal"
    filterable: bool = False
    unit: str | None = None
    enum_values: tuple[str, ...] = ()
    description: str = ""
    source: str = SOURCE_SEMANTIC_LAYER

    @property
    def key(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def is_numeric(self) -> bool:
        return self.logical_type in ("int", "float")


@dataclass(frozen=True)
class TermNode:
    name: str
    category: str = ""
    definition: str = ""           # embedded; see design §7.3 — thin without this
    source: str = SOURCE_GLOSSARY


@dataclass(frozen=True)
class ForeignKeyEdge:
    """One joinable relationship between two tables.

    ``column_pairs`` is the whole join predicate: a composite relationship carries every
    pair, so a query joining on a strict SUBSET is detectable (that subset is a silent
    fan-out, not a valid join).
    """

    from_table: str
    to_table: str
    column_pairs: tuple[tuple[str, str], ...] = ()
    constraint: str = ""
    cardinality: str = MANY_TO_ONE
    rule: str = ""                 # human-readable predicate, as rendered to the prompt
    source: str = SOURCE_SEMANTIC_LAYER
    status: str = STATUS_ACTIVE

    @property
    def pair_key(self) -> frozenset[str]:
        return frozenset((self.from_table, self.to_table))

    def flipped(self) -> "ForeignKeyEdge":
        """Same relationship seen from the other table (traversal is undirected; only
        cardinality and column order flip)."""
        flip = {MANY_TO_ONE: ONE_TO_MANY, ONE_TO_MANY: MANY_TO_ONE}
        return ForeignKeyEdge(
            from_table=self.to_table,
            to_table=self.from_table,
            column_pairs=tuple((b, a) for a, b in self.column_pairs),
            constraint=self.constraint,
            cardinality=flip.get(self.cardinality, self.cardinality),
            rule=self.rule,
            source=self.source,
            status=self.status,
        )

    def normalised_pairs(self) -> set[frozenset[str]]:
        """Column pairs as unordered {table.col, table.col} sets — the form check #10
        compares an equi-join predicate against, since SQL may write either side first."""
        return {
            frozenset((f"{self.from_table}.{a}", f"{self.to_table}.{b}"))
            for a, b in self.column_pairs
        }

    def on_clause(self) -> str:
        return " AND ".join(
            f"{self.from_table}.{a} = {self.to_table}.{b}" for a, b in self.column_pairs
        )


@dataclass
class MetadataGraph:
    """The whole KG as one serialisable artifact.

    Small by construction — proportional to tables x columns, never to row count. For this
    schema: 21 tables / 347 columns / 23 terms / 15 active edges.
    """

    tables: dict[str, TableNode] = field(default_factory=dict)
    columns: dict[str, ColumnNode] = field(default_factory=dict)   # keyed "table.column"
    terms: dict[str, TermNode] = field(default_factory=dict)
    foreign_keys: list[ForeignKeyEdge] = field(default_factory=list)
    defines: list[tuple[str, str]] = field(default_factory=list)   # (term, "table.column")
    built_at: str = ""
    source_database: str = ""
    fingerprint: str = ""

    # --- derived ------------------------------------------------------------------
    def columns_of(self, table: str) -> list[ColumnNode]:
        return [c for c in self.columns.values() if c.table == table]

    def active_edges(self) -> list[ForeignKeyEdge]:
        return [e for e in self.foreign_keys if e.status == STATUS_ACTIVE]

    def views(self) -> set[str]:
        return {n for n, t in self.tables.items() if t.is_view}

    def adjacency(self) -> dict[str, dict[str, ForeignKeyEdge]]:
        """Undirected table -> {neighbour: edge}, ACTIVE edges only. Both directions are
        populated (reverse carries flipped column order + cardinality). Mirrors
        semantic_layer.loader.relationship_graph, but edge-typed rather than a rule string."""
        graph: dict[str, dict[str, ForeignKeyEdge]] = {name: {} for name in self.tables}
        for edge in self.active_edges():
            if edge.from_table not in graph or edge.to_table not in graph:
                continue
            graph[edge.from_table][edge.to_table] = edge
            graph[edge.to_table].setdefault(edge.from_table, edge.flipped())
        return graph

    def edge_between(self, a: str, b: str) -> ForeignKeyEdge | None:
        """The ACTIVE edge joining a and b, oriented FROM a, or None."""
        for edge in self.active_edges():
            if edge.pair_key == frozenset((a, b)):
                return edge if edge.from_table == a else edge.flipped()
        return None

    def columns_for_term(self, term: str) -> list[str]:
        return [c for t, c in self.defines if t == term]

    def tables_for_term(self, term: str) -> list[str]:
        """HAS_TERM, derived: every table owning a column this term DEFINES."""
        return sorted({c.rsplit(".", 1)[0] for c in self.columns_for_term(term)})

    def terms_for_column(self, column_key: str) -> list[str]:
        return [t for t, c in self.defines if c == column_key]

    # --- serialisation -------------------------------------------------------------
    def compute_fingerprint(self) -> str:
        """Stable hash of STRUCTURAL content (not built_at), so a no-op rebuild is
        detectable and a migration shows as a new fingerprint. Recorded on every audit
        record, so a later migration cannot retroactively change the explanation of an
        old answer."""
        payload = {
            "tables": sorted(
                (t.name, t.db_schema, t.is_view, t.primary_key) for t in self.tables.values()
            ),
            "columns": sorted(
                (c.key, c.data_type, c.logical_type, c.nullable, tuple(c.enum_values))
                for c in self.columns.values()
            ),
            "edges": sorted(
                (e.from_table, e.to_table, e.column_pairs, e.cardinality, e.status)
                for e in self.foreign_keys
            ),
            "defines": sorted(self.defines),
        }
        blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at,
            "source_database": self.source_database,
            "fingerprint": self.fingerprint or self.compute_fingerprint(),
            "tables": [asdict(t) for t in self.tables.values()],
            "columns": [asdict(c) for c in self.columns.values()],
            "terms": [asdict(t) for t in self.terms.values()],
            "foreign_keys": [asdict(e) for e in self.foreign_keys],
            "defines": [list(d) for d in self.defines],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetadataGraph":
        tables = {t["name"]: TableNode(**t) for t in data.get("tables", [])}
        columns: dict[str, ColumnNode] = {}
        for spec in data.get("columns", []):
            col = ColumnNode(**{**spec, "enum_values": tuple(spec.get("enum_values") or ())})
            columns[col.key] = col
        edges = [
            ForeignKeyEdge(**{
                **spec,
                "column_pairs": tuple(tuple(p) for p in (spec.get("column_pairs") or ())),
            })
            for spec in data.get("foreign_keys", [])
        ]
        return cls(
            tables=tables,
            columns=columns,
            terms={t["name"]: TermNode(**t) for t in data.get("terms", [])},
            foreign_keys=edges,
            defines=[tuple(d) for d in data.get("defines", [])],
            built_at=data.get("built_at", ""),
            source_database=data.get("source_database", ""),
            fingerprint=data.get("fingerprint", ""),
        )
