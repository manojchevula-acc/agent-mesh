"""Swappable metadata-KG backend — WHERE the graph lives and how it is traversed.

Same separation as semantic_layer/vector_index.py: builder.py decides WHAT the graph
contains, this module decides where it is stored.

  memory — the JSON artifact held in RAM; traversal is a bounded, view-scope-filtered BFS.
           Zero infra, no driver, no server. Correct at this scale: 21 tables / 15 edges is
           a few hundred KB and a BFS over it is microseconds, against a Neo4j round trip of
           low single-digit milliseconds. Default.
  neo4j  — a real graph database. Structural reads come from a per-process snapshot (the
           graph is immutable between builds, so re-fetching per request buys only latency),
           while the genuinely query-shaped operations — shortest-path join discovery and the
           Approach B templates — run as live Cypher. Opt-in via KG_BACKEND=neo4j.

Both satisfy one Protocol, so nothing downstream knows which is in use and moving to Neo4j
is a config change. The neo4j driver is imported lazily so this package imports cleanly
without the optional ``kg`` extra installed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from functools import lru_cache
from typing import Protocol, runtime_checkable

from sql_agent.config import settings
from sql_agent.kg.schema import (
    STATUS_ACTIVE,
    ColumnNode,
    ForeignKeyEdge,
    MetadataGraph,
    TableNode,
)
from sql_agent.logging_config import get_logger

log = get_logger("kg.client")

# The one table a view may legally be joined to (validator check #9). Sourced here rather
# than hardcoded in the traversal so the rule has a single name.
VIEW_JOIN_PARTNER = "customer_master"


@runtime_checkable
class KnowledgeGraph(Protocol):
    """The query surface the agent uses. Deliberately small: each method answers one of the
    questions the KG doc's §4.1 pipeline stages actually ask."""

    def load(self, graph: MetadataGraph) -> None: ...
    def snapshot(self) -> MetadataGraph: ...
    def table(self, name: str) -> TableNode | None: ...
    def columns_of(self, table: str) -> list[ColumnNode]: ...
    def resolve_term(self, term: str) -> list[tuple[str, str]]: ...
    def join_path(self, source: str, target: str) -> list[str]: ...
    def close(self) -> None: ...


def view_scope_ok(path: list[str], views: set[str]) -> bool:
    """Whether a join path is legal under validator check #9.

    A view is one-row-per-entity, so chaining a further table onto its join partner fans out
    its rows. Check #9 therefore permits a view to sit ONLY alone or alongside
    customer_master — never with a third table, and never with a second view.

    This must be applied DURING traversal, not after. Plain BFS over the 15 edges happily
    returns ``margin_analysis -> customer_master -> pricing_recommendation_view``, which the
    validator rejects outright; unfiltered, the KG would hand the generator a path guaranteed
    to fail and burn a self-correction attempt (design §4.4).
    """
    in_path = [t for t in path if t in views]
    if not in_path:
        return True
    if len(in_path) > 1:
        return False
    return set(path) <= {in_path[0], VIEW_JOIN_PARTNER}


def bfs_join_path(adjacency, views, source, target, max_hops) -> list[str]:
    """Shortest legal path over the undirected active-edge adjacency.

    Bounded on purpose: customer_master neighbours 11 of the 15 edges, so an unbounded search
    returns long, technically-connected, semantically-meaningless chains. A pair needing more
    than ``max_hops`` is treated as "not meaningfully joinable", which is a better answer for
    the generator than a speculative path.
    """
    if source == target:
        return [source]
    if source not in adjacency or target not in adjacency:
        return []
    seen = {source}
    queue: deque[list[str]] = deque([[source]])
    while queue:
        path = queue.popleft()
        if len(path) > max_hops:
            continue
        for neighbour in adjacency.get(path[-1], {}):
            if neighbour in seen:
                continue
            candidate = path + [neighbour]
            if not view_scope_ok(candidate, views):
                continue          # illegal under check #9 — do not expand or return it
            if neighbour == target:
                return candidate
            seen.add(neighbour)
            queue.append(candidate)
    return []


class _GraphOps:
    """Structural reads shared by both backends, over a held MetadataGraph snapshot."""

    def __init__(self) -> None:
        self._graph = MetadataGraph()
        self._adjacency: dict[str, dict[str, ForeignKeyEdge]] = {}
        self._views: set[str] = set()

    def load(self, graph: MetadataGraph) -> None:
        self._graph = graph
        self._adjacency = graph.adjacency()
        self._views = graph.views()

    def snapshot(self) -> MetadataGraph:
        return self._graph

    def adjacency(self) -> dict[str, dict[str, ForeignKeyEdge]]:
        return self._adjacency

    def table(self, name: str) -> TableNode | None:
        return self._graph.tables.get(name)

    def columns_of(self, table: str) -> list[ColumnNode]:
        return self._graph.columns_of(table)

    def resolve_term(self, term: str) -> list[tuple[str, str]]:
        """DEFINES traversal: term -> [(table, "table.column"), ...]."""
        return [(k.rsplit(".", 1)[0], k) for k in self._graph.columns_for_term(term)]

    def neighbours(self, table: str) -> list[str]:
        return sorted(self._adjacency.get(table, {}))

    def close(self) -> None:
        return None


class MemoryGraph(_GraphOps):
    """In-process graph. The whole KG is the JSON artifact; traversal is a bounded,
    view-scope-filtered BFS."""

    def join_path(self, source: str, target: str) -> list[str]:
        return bfs_join_path(self._adjacency, self._views, source, target,
                             settings.kg_max_join_hops)


class Neo4jGraph(_GraphOps):
    """Neo4j-backed graph.

    ``load`` upserts the artifact with MERGE (idempotent, so a rebuild that changed nothing is
    a no-op at the data level). Structural reads come from the hydrated snapshot; join_path
    and run_cypher go to the server, so path discovery and the Approach B templates are
    genuine Cypher rather than Python pretending to be Cypher.
    """

    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        super().__init__()
        self._uri, self._user, self._password, self._database = uri, user, password, database
        self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase  # lazy: optional `kg` extra

            self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        return self._driver

    def run_cypher(self, cypher: str, params: dict | None = None) -> list[dict]:
        with self.driver.session(database=self._database) as session:
            return [record.data() for record in session.run(cypher, **(params or {}))]

    # -- write -------------------------------------------------------------------
    def load(self, graph: MetadataGraph) -> None:
        """MERGE the artifact into Neo4j, then hydrate the local snapshot.

        Node keys mirror the artifact's: Table by ``name``, Column by ``key``
        (table.column), Term by ``name`` — so a rebuild updates in place rather than
        duplicating. Column pairs are stored as a flat "from|to" string list because Neo4j
        properties cannot hold nested arrays.
        """
        super().load(graph)
        for constraint in (
            "CREATE CONSTRAINT kg_table_name IF NOT EXISTS "
            "FOR (t:Table) REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT kg_column_key IF NOT EXISTS "
            "FOR (c:Column) REQUIRE c.key IS UNIQUE",
            "CREATE CONSTRAINT kg_term_name IF NOT EXISTS "
            "FOR (t:Term) REQUIRE t.name IS UNIQUE",
        ):
            self.run_cypher(constraint)

        self.run_cypher(
            """
            UNWIND $tables AS t
            MERGE (n:Table {name: t.name})
            SET n.db_schema = t.db_schema, n.is_view = t.is_view, n.grain = t.grain,
                n.purpose = t.purpose, n.primary_key = t.primary_key,
                n.row_estimate = t.row_estimate, n.search_terms = t.search_terms,
                n.source = t.source
            """,
            {"tables": [asdict(t) for t in graph.tables.values()]},
        )
        self.run_cypher(
            """
            UNWIND $columns AS c
            MERGE (n:Column {key: c.key})
            SET n.table = c.table, n.name = c.name, n.data_type = c.data_type,
                n.logical_type = c.logical_type, n.nullable = c.nullable,
                n.is_primary_key = c.is_primary_key, n.is_unique = c.is_unique,
                n.sensitivity = c.sensitivity, n.filterable = c.filterable,
                n.unit = c.unit, n.enum_values = c.enum_values,
                n.description = c.description, n.source = c.source
            WITH n, c
            MATCH (t:Table {name: c.table})
            MERGE (t)-[:HAS_COLUMN]->(n)
            """,
            {"columns": [{**asdict(c), "key": c.key, "enum_values": list(c.enum_values)}
                         for c in graph.columns.values()]},
        )
        self.run_cypher(
            """
            UNWIND $terms AS t
            MERGE (n:Term {name: t.name})
            SET n.category = t.category, n.definition = t.definition, n.source = t.source
            """,
            {"terms": [asdict(t) for t in graph.terms.values()]},
        )
        self.run_cypher(
            """
            UNWIND $edges AS e
            MATCH (a:Table {name: e.from_table}), (b:Table {name: e.to_table})
            MERGE (a)-[r:HAS_FOREIGN_KEY {from_table: e.from_table, to_table: e.to_table}]->(b)
            SET r.column_pairs = e.column_pairs, r.cardinality = e.cardinality,
                r.rule = e.rule, r.source = e.source, r.status = e.status,
                r.constraint_name = e.constraint_name
            """,
            {"edges": [{"from_table": e.from_table, "to_table": e.to_table,
                        "column_pairs": [f"{a}|{b}" for a, b in e.column_pairs],
                        "cardinality": e.cardinality, "rule": e.rule, "source": e.source,
                        "status": e.status, "constraint_name": e.constraint}
                       for e in graph.foreign_keys]},
        )
        # Column-level REFERENCES: the same relationship projected onto columns, so a
        # column-seeded traversal reaches the related column without hopping up and back.
        self.run_cypher(
            """
            UNWIND $pairs AS p
            MATCH (a:Column {key: p.from_key}), (b:Column {key: p.to_key})
            MERGE (a)-[:REFERENCES]->(b)
            """,
            {"pairs": [{"from_key": f"{e.from_table}.{a}", "to_key": f"{e.to_table}.{b}"}
                       for e in graph.foreign_keys if e.status == STATUS_ACTIVE
                       for a, b in e.column_pairs]},
        )
        self.run_cypher(
            """
            UNWIND $defines AS d
            MATCH (t:Term {name: d.term}), (c:Column {key: d.column})
            MERGE (t)-[:DEFINES]->(c)
            WITH t, c
            MATCH (tb:Table {name: c.table})
            MERGE (tb)-[:HAS_TERM]->(t)
            """,
            {"defines": [{"term": t, "column": c} for t, c in graph.defines]},
        )
        log.info("KG neo4j | upserted %d tables / %d columns / %d terms / %d edges",
                 len(graph.tables), len(graph.columns), len(graph.terms),
                 len(graph.foreign_keys))

    # -- read --------------------------------------------------------------------
    def join_path(self, source: str, target: str) -> list[str]:
        """Shortest ACTIVE-edge path, as real Cypher, then view-scope filtered.

        The hop bound is interpolated rather than parameterised because Cypher does not
        accept a parameter inside a variable-length pattern; it is coerced to an int first so
        the value can never carry query text. The view-scope rule is applied to the returned
        path in Python — expressing check #9's "at most one view, and only alongside
        customer_master" in Cypher is possible but far less readable than the predicate the
        validator itself uses.
        """
        if source == target:
            return [source]
        hops = max(1, int(settings.kg_max_join_hops))
        rows = self.run_cypher(
            f"""
            MATCH (a:Table {{name: $source}}), (b:Table {{name: $target}}),
                  p = shortestPath((a)-[:HAS_FOREIGN_KEY*1..{hops}]-(b))
            WHERE all(r IN relationships(p) WHERE r.status = '{STATUS_ACTIVE}')
            RETURN [n IN nodes(p) | n.name] AS tables
            """,
            {"source": source, "target": target},
        )
        path = list(rows[0]["tables"]) if rows else []
        return path if view_scope_ok(path, self._views) else []

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
            self._driver = None


def _make_client() -> KnowledgeGraph:
    if settings.kg_backend.strip().lower() == "neo4j":
        return Neo4jGraph(settings.neo4j_uri, settings.neo4j_user,
                          settings.neo4j_password, settings.neo4j_database)
    return MemoryGraph()


@lru_cache(maxsize=1)
def get_kg_client() -> KnowledgeGraph | None:
    """The configured KG client loaded from the built artifact — or None when the KG is off
    or has not been built.

    Returning None rather than raising is deliberate and mirrors how scoped schema retrieval
    degrades: a missing KG must cost the agent its KG grounding, never a turn. Every caller
    treats None as "run exactly as before the KG existed".
    """
    if not settings.kg_enabled:
        return None

    from sql_agent.kg.builder import artifact_path, read_artifact

    graph = read_artifact()
    if graph is None:
        log.warning("KG enabled but no artifact at %s — falling back to the semantic layer. "
                    "Build it with: uv run python scripts/build_metadata_kg.py",
                    artifact_path())
        return None

    client = _make_client()
    try:
        client.load(graph)
    except Exception as exc:  # noqa: BLE001 — a KG that won't load must not break the agent
        log.warning("KG backend %s failed to load | %s | falling back to the semantic layer",
                    settings.kg_backend, exc)
        client.close()
        return None
    log.info("KG ready | backend=%s tables=%d columns=%d terms=%d active_edges=%d "
             "fingerprint=%s built_at=%s", settings.kg_backend, len(graph.tables),
             len(graph.columns), len(graph.terms), len(graph.active_edges()),
             graph.fingerprint, graph.built_at or "-")
    return client


def reset_kg_client() -> None:
    """Drop the cached client, closing it first (tests, and after a rebuild in a long-lived
    process). Safe to call when nothing is cached."""
    if get_kg_client.cache_info().currsize:
        existing = get_kg_client()
        if existing is not None:
            existing.close()
    get_kg_client.cache_clear()
