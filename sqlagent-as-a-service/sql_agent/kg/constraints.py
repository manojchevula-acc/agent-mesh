"""The constraint bundle the SQLValidator's KG checks (#10-#12) enforce.

The validator must not reach into the KG client: it is the security gate and has to stay
cheap, synchronous and dependency-light. So the pipeline resolves a small immutable bundle
ONCE — scoped to the tables the query may touch — and hands it to validate(), exactly the way
allowed_join_pairs is handed down today.

Scoping matters for more than speed. A bundle built from the planner's table set means the KG
checks judge the SQL against the same subschema the generator was SHOWN, so a rejection is
always something the model could have avoided from its own prompt.

None is a valid, expected value everywhere: KG off, KG unbuilt, or nothing resolved all
produce kg_constraints=None, and the validator then runs exactly the checks it ran before this
layer existed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sql_agent.kg.schema import MANY_TO_MANY, ONE_TO_MANY, ColumnNode, ForeignKeyEdge


@dataclass(frozen=True)
class KGConstraints:
    """Everything the KG-constrained validation stage needs, and nothing else."""

    tables: frozenset[str]
    columns: dict[str, ColumnNode]                  # keyed "table.column"
    edges: dict[frozenset[str], ForeignKeyEdge]     # keyed {table_a, table_b}
    fingerprint: str = ""

    def column(self, table: str, name: str) -> ColumnNode | None:
        return self.columns.get(f"{table}.{name}")

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def edge(self, a: str, b: str) -> ForeignKeyEdge | None:
        """The edge joining a and b, oriented FROM a."""
        edge = self.edges.get(frozenset((a, b)))
        if edge is None:
            return None
        return edge if edge.from_table == a else edge.flipped()

    def declared_pairs(self, a: str, b: str) -> set[frozenset[str]]:
        """Every {table.col, table.col} pair the KG declares for this relationship."""
        edge = self.edges.get(frozenset((a, b)))
        return edge.normalised_pairs() if edge else set()

    def fans_out(self, a: str, b: str) -> bool:
        """True when joining a to b can MULTIPLY a's rows — the one-to-many direction and
        many-to-many. That is what makes an aggregate over a's own columns double-count once
        b is joined in; check #12's whole subject."""
        edge = self.edge(a, b)
        return edge is not None and edge.cardinality in (ONE_TO_MANY, MANY_TO_MANY)

    def is_composite(self, a: str, b: str) -> bool:
        edge = self.edges.get(frozenset((a, b)))
        return bool(edge) and len(edge.column_pairs) > 1

    def as_dict(self) -> dict:
        """Compact form for the audit record — names only, never column descriptions."""
        return {
            "fingerprint": self.fingerprint,
            "tables": sorted(self.tables),
            "column_count": len(self.columns),
            "edges": sorted(f"{e.from_table}<->{e.to_table} [{e.cardinality}]"
                            for e in self.edges.values()),
        }


def constraints_for(tables: set[str], client=None) -> KGConstraints | None:
    """Build the bundle for ``tables``. Returns None when the KG is unavailable, which every
    caller treats as "skip the KG checks".

    Edges are included only when BOTH endpoints are in scope — a relationship to a table the
    query may not reference is not a constraint on this query.
    """
    if client is None:
        from sql_agent.kg.client import get_kg_client

        client = get_kg_client()
    if client is None or not tables:
        return None

    graph = client.snapshot()
    scoped = {t for t in tables if t in graph.tables}
    if not scoped:
        return None

    return KGConstraints(
        tables=frozenset(scoped),
        columns={k: c for k, c in graph.columns.items() if c.table in scoped},
        edges={e.pair_key: e for e in graph.active_edges()
               if e.from_table in scoped and e.to_table in scoped},
        fingerprint=graph.fingerprint,
    )


__all__ = ["KGConstraints", "constraints_for"]
