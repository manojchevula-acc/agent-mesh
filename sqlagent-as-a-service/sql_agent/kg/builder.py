"""Build the metadata Knowledge Graph from ``information_schema`` + the semantic layer.

Four sources, merged with explicit precedence so the KG can never contradict the security
boundary (design §8.1):

  1. information_schema           — AUTHORITATIVE for what physically exists.
  2. schema.yaml                  — AUTHORITATIVE for GOVERNANCE (only declared objects
                                    enter the KG) and for RELATIONSHIPS.
  3. fab_curated.data_dictionary  — supplementary descriptions; join prose -> PROPOSED only.
  4. business_glossary.yaml       — Term nodes, DEFINES edges, and the text that is embedded.

FOREIGN KEYS — a deployment fact worth stating plainly: this database declares ZERO foreign
key constraints (information_schema.key_column_usage returns nothing for fab_curated), and
fab_semantic is entirely views, which cannot carry constraints at all. So join edges come
from schema.yaml's curated ``joins:`` blocks — the same 15 pairs
semantic_layer.loader.relationship_graph() already feeds the live join resolver. The builder
still reads key_column_usage FIRST and prefers a real constraint whenever one appears, so
adding FKs later strengthens the KG with no code change.

INFERENCE IS NEVER ACTIVATED. Name-matched candidate joins and the data_dictionary's prose
hints are written as status="proposed": never traversed, never enforced. Promotion is a human
decision recorded in schema.yaml.

NO ROW DATA IS READ. Every query below targets information_schema or the curated
data_dictionary metadata table. Not one statement reads a business fact row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from sql_agent.config import settings
from sql_agent.db.connection import get_engine
from sql_agent.kg.schema import (
    MANY_TO_MANY,
    MANY_TO_ONE,
    ONE_TO_MANY,
    ONE_TO_ONE,
    SOURCE_DATA_DICTIONARY,
    SOURCE_GLOSSARY,
    SOURCE_INFERRED,
    SOURCE_INFORMATION_SCHEMA,
    SOURCE_SEMANTIC_LAYER,
    STATUS_ACTIVE,
    STATUS_PROPOSED,
    ColumnNode,
    ForeignKeyEdge,
    MetadataGraph,
    TableNode,
    TermNode,
)
from sql_agent.logging_config import get_logger
from sql_agent.semantic_layer.glossary import _entries
from sql_agent.semantic_layer.loader import load_semantic_layer

log = get_logger("kg.builder")

# data_dictionary keys rows by source CSV name ("raw_operations_cost.csv"), not table name.
_DD_FILE = re.compile(r"^(?:raw_)?(?P<table>.+?)(?:\.csv)?$", re.IGNORECASE)


@dataclass
class DriftReport:
    """information_schema vs schema.yaml — the doc §10 "schema drift" mitigation.

    ``scripts/build_metadata_kg.py --check`` exits non-zero when this is blocking, so a
    migration that moves a column fails the pipeline instead of quietly de-grounding the
    agent.
    """

    missing_tables: list[str] = field(default_factory=list)     # declared, absent from DB
    missing_columns: list[str] = field(default_factory=list)    # declared, absent from DB
    undeclared_tables: list[str] = field(default_factory=list)  # in DB, not governed
    type_changes: list[str] = field(default_factory=list)

    @property
    def has_blocking_drift(self) -> bool:
        """Undeclared tables are informational (the DB legitimately holds ungoverned
        tables); a DECLARED object that no longer exists is a broken semantic layer."""
        return bool(self.missing_tables or self.missing_columns)

    def as_dict(self) -> dict:
        return {
            "missing_tables": self.missing_tables,
            "missing_columns": self.missing_columns,
            "undeclared_tables": self.undeclared_tables,
            "type_changes": self.type_changes,
        }

    def render(self) -> str:
        if not any(self.as_dict().values()):
            return "No drift: information_schema matches the declared semantic layer."
        lines = []
        for label, items in self.as_dict().items():
            if items:
                lines.append(f"{label} ({len(items)}):")
                lines.extend(f"  - {i}" for i in items)
        return "\n".join(lines)


# --- information_schema readers -------------------------------------------------------


def _fetch_tables(conn, schemas: list[str]) -> dict[str, dict]:
    # MySQL's information_schema always returns its OWN columns in upper-case (TABLE_SCHEMA,
    # TABLE_NAME, TABLE_TYPE), whatever case the query used — but honours the case of an
    # explicit AS alias on a computed expression (IFNULL(...) AS table_rows). Row attribute
    # access is case-sensitive, so the two families must be read with different case here.
    rows = conn.execute(
        text("SELECT table_schema, table_name, table_type, IFNULL(table_rows, 0) AS table_rows "
             "FROM information_schema.tables WHERE table_schema IN :schemas")
        .bindparams(schemas=tuple(schemas))
    )
    return {
        r.TABLE_NAME: {
            "db_schema": r.TABLE_SCHEMA,
            "is_view": r.TABLE_TYPE.upper() == "VIEW",
            "row_estimate": int(r.table_rows or 0),
        }
        for r in rows
    }


def _fetch_columns(conn, schemas: list[str]) -> dict[str, dict]:
    """Physical column facts keyed ``table.column``.

    ``column_type`` (not ``data_type``) carries precision/length and the inline ENUM member
    list, which is what a type check actually needs."""
    rows = conn.execute(
        text("SELECT table_name, column_name, column_type, is_nullable, column_key, "
             "column_comment FROM information_schema.columns WHERE table_schema IN :schemas")
        .bindparams(schemas=tuple(schemas))
    )
    return {
        f"{r.TABLE_NAME}.{r.COLUMN_NAME}": {
            "data_type": str(r.COLUMN_TYPE or ""),
            "nullable": str(r.IS_NULLABLE).upper() == "YES",
            "is_primary_key": str(r.COLUMN_KEY or "").upper() == "PRI",
            "is_unique": str(r.COLUMN_KEY or "").upper() in ("PRI", "UNI"),
            "comment": str(r.COLUMN_COMMENT or ""),
        }
        for r in rows
    }


def _fetch_declared_foreign_keys(conn, schemas: list[str]) -> list[dict]:
    """Real FK constraints, grouped per constraint so composite keys stay together.
    Returns [] on this deployment; kept so a schema that adds FKs upgrades provenance."""
    rows = conn.execute(
        text("SELECT constraint_name, table_name, column_name, referenced_table_name, "
             "referenced_column_name, ordinal_position "
             "FROM information_schema.key_column_usage "
             "WHERE referenced_table_name IS NOT NULL AND table_schema IN :schemas "
             "ORDER BY constraint_name, ordinal_position")
        .bindparams(schemas=tuple(schemas))
    )
    grouped: dict[tuple[str, str], dict] = {}
    for r in rows:
        entry = grouped.setdefault(
            (r.CONSTRAINT_NAME, r.TABLE_NAME),
            {"constraint": r.CONSTRAINT_NAME, "from_table": r.TABLE_NAME,
             "to_table": r.REFERENCED_TABLE_NAME, "column_pairs": []},
        )
        entry["column_pairs"].append((r.COLUMN_NAME, r.REFERENCED_COLUMN_NAME))
    return list(grouped.values())


def _fetch_data_dictionary(conn) -> tuple[dict[str, str], dict[str, str]]:
    """fab_curated.data_dictionary -> (column descriptions, per-table join prose).

    Best-effort: the table is a curated convenience, not a contract, so any failure degrades
    to empty rather than failing the build."""
    descriptions: dict[str, str] = {}
    join_prose: dict[str, str] = {}
    try:
        rows = conn.execute(text(
            "SELECT file_name, column_name, column_description, join_usage "
            "FROM fab_curated.data_dictionary"))
    except Exception as exc:  # noqa: BLE001 — supplementary source; never fail the build
        log.info("data_dictionary unavailable | %s | continuing without it", exc)
        return {}, {}
    for r in rows:
        match = _DD_FILE.match(str(r.file_name or "").strip())
        if not match:
            continue
        table = match.group("table").lower()
        if r.column_description:
            descriptions[f"{table}.{str(r.column_name).lower()}"] = str(r.column_description)
        if r.join_usage:
            join_prose.setdefault(table, str(r.join_usage))
    return descriptions, join_prose


# --- join-rule parsing ----------------------------------------------------------------


def parse_join_rule(rule: str) -> tuple[tuple[tuple[str, str], ...], list[str]]:
    """Turn a schema.yaml ``joins:`` rule into explicit (from_col, to_col) pairs.

    All three shapes live in schema.yaml today and must round-trip (design §8.3):

      "customer_id -> historical_deals.customer_id"     explicit, qualified
      "currency -> ... AND tenor -> ..."                composite, AND-separated
      "customer_segment + risk_category"                shorthand: same names both sides
      "product_id"                                      shorthand: one shared name

    Returns (pairs, unparsed_fragments). Anything unparsed is reported by the caller rather
    than guessed at — a half-understood join predicate is worse than a missing one.
    """
    pairs: list[tuple[str, str]] = []
    unparsed: list[str] = []
    if not rule or not rule.strip():
        return (), []

    for fragment in re.split(r"\s+AND\s+", rule.strip(), flags=re.IGNORECASE):
        fragment = fragment.strip()
        if not fragment:
            continue
        if "->" in fragment:
            left, right = (p.strip() for p in fragment.split("->", 1))
            lcol, rcol = left.rsplit(".", 1)[-1], right.rsplit(".", 1)[-1]
            (pairs.append((lcol, rcol)) if lcol and rcol else unparsed.append(fragment))
            continue
        shared = [tok.strip() for tok in fragment.split("+") if tok.strip()]
        if shared and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t) for t in shared):
            pairs.extend((t, t) for t in shared)
        else:
            unparsed.append(fragment)

    seen: set[tuple[str, str]] = set()
    ordered = [p for p in pairs if not (p in seen or seen.add(p))]
    return tuple(ordered), unparsed


def _infer_cardinality(column_pairs, from_table, to_table, columns) -> str:
    """Infer cardinality from key uniqueness (design §6.1).

    Right-hand columns unique -> many-to-one; both -> one-to-one; neither -> many-to-many,
    the shape that silently multiplies rows and that check #12 flags. MySQL exposes no key
    metadata for views, so a view's declared primary_key stands in (populated onto the
    ColumnNode by _column_nodes) — without this every view-side join looks many-to-many.
    """
    if not column_pairs:
        return MANY_TO_MANY

    def unique_side(table: str, cols: tuple[str, ...]) -> bool:
        nodes = [columns.get(f"{table}.{c}") for c in cols]
        return bool(nodes) and all(n is not None and n.is_unique for n in nodes)

    left = unique_side(from_table, tuple(a for a, _ in column_pairs))
    right = unique_side(to_table, tuple(b for _, b in column_pairs))
    if left and right:
        return ONE_TO_ONE
    if right:
        return MANY_TO_ONE
    if left:
        return ONE_TO_MANY
    return MANY_TO_MANY


# --- node construction ----------------------------------------------------------------


def _table_nodes(layer, physical: dict[str, dict]) -> dict[str, TableNode]:
    nodes: dict[str, TableNode] = {}
    for name, table in layer.tables.items():
        facts = physical.get(name, {})
        nodes[name] = TableNode(
            name=name,
            # Prefer the PHYSICAL schema when found, so a view moved between schemas
            # surfaces at its real location rather than the declared one.
            db_schema=facts.get("db_schema") or table.schema,
            is_view=bool(facts.get("is_view", table.is_view)),
            grain=table.grain,
            purpose=table.purpose,
            primary_key=table.primary_key,
            row_estimate=int(facts.get("row_estimate", 0)),
            search_terms=table.search_terms,
            source=SOURCE_INFORMATION_SCHEMA if facts else SOURCE_SEMANTIC_LAYER,
        )
    return nodes


def _column_nodes(layer, physical, dd_descriptions) -> dict[str, ColumnNode]:
    """One ColumnNode per DECLARED column. Physical facts layered on where the column exists;
    governance facts always from schema.yaml.

    Description precedence: schema.yaml ``desc:`` -> COLUMN_COMMENT -> data_dictionary. On
    this deployment no column carries a comment (0/592), so the curated desc wins and the
    data dictionary fills gaps on the newer raw-sourced tables.
    """
    nodes: dict[str, ColumnNode] = {}
    for table_name, table in layer.tables.items():
        for col in table.columns.values():
            key = f"{table_name}.{col.name}"
            facts = physical.get(key, {})
            declared_pk = col.name == table.primary_key
            nodes[key] = ColumnNode(
                table=table_name,
                name=col.name,
                data_type=facts.get("data_type", ""),
                logical_type=col.type,
                nullable=bool(facts.get("nullable", True)),
                is_primary_key=bool(facts.get("is_primary_key", declared_pk)),
                # `or declared_pk`: views have no physical key metadata (see
                # _infer_cardinality) — fall back to the declared primary key.
                is_unique=bool(facts.get("is_unique", declared_pk)) or declared_pk,
                sensitivity=col.sensitivity or table.sensitivity_default,
                filterable=col.filterable,
                unit=col.unit,
                enum_values=tuple(col.values),
                description=col.desc or facts.get("comment") or dd_descriptions.get(key, ""),
                source=SOURCE_INFORMATION_SCHEMA if facts else SOURCE_SEMANTIC_LAYER,
            )
    return nodes


def _term_nodes(columns) -> tuple[dict[str, TermNode], list[tuple[str, str]]]:
    """Term nodes + DEFINES edges from business_glossary.yaml.

    The glossary maps a term to BARE column names, which may exist on several tables. Each is
    expanded to every governed ``table.column`` carrying that name — precisely the
    disambiguation the KG is for: "policy margin" resolves to
    pricing_policy.min_expected_margin_pct AND
    pricing_recommendation_view.policy_min_expected_margin_pct, and the caller sees both
    candidates with their parent tables instead of the agent picking one by chance.

    ``definition`` is read from the YAML when present. It is what gets EMBEDDED (design
    §7.3), so its absence directly caps semantic recall — hence the Stage-0 work item.
    Falls back to the first resolved column's description so the field is never empty.
    """
    by_bare: dict[str, list[str]] = {}
    for key, col in columns.items():
        by_bare.setdefault(col.name.lower(), []).append(key)

    terms: dict[str, TermNode] = {}
    defines: list[tuple[str, str]] = []
    for entry in _entries():
        name = str(entry["term"]).strip()
        if not name:
            continue
        targets: list[str] = []
        for bare in entry.get("columns") or []:
            targets.extend(by_bare.get(str(bare).lower(), []))
        definition = str(entry.get("definition") or "").strip()
        if not definition and targets:
            definition = columns[targets[0]].description
        terms[name] = TermNode(name=name, category=str(entry.get("category") or ""),
                               definition=definition, source=SOURCE_GLOSSARY)
        defines.extend((name, t) for t in sorted(set(targets)))
    return terms, defines


# --- edge construction ----------------------------------------------------------------


def _foreign_key_edges(layer, columns, declared_fks, join_prose) -> list[ForeignKeyEdge]:
    """ACTIVE edges from real FK constraints and schema.yaml joins; PROPOSED from prose."""
    edges: list[ForeignKeyEdge] = []
    seen: set[frozenset[str]] = set()

    # 1. Real FK constraints win on provenance wherever they exist.
    for fk in declared_fks:
        if fk["from_table"] not in layer.tables or fk["to_table"] not in layer.tables:
            continue
        pairs = tuple(fk["column_pairs"])
        seen.add(frozenset((fk["from_table"], fk["to_table"])))
        edge = ForeignKeyEdge(
            from_table=fk["from_table"], to_table=fk["to_table"], column_pairs=pairs,
            constraint=fk["constraint"],
            cardinality=_infer_cardinality(pairs, fk["from_table"], fk["to_table"], columns),
            source=SOURCE_INFORMATION_SCHEMA, status=STATUS_ACTIVE,
        )
        edges.append(ForeignKeyEdge(**{**edge.__dict__, "rule": edge.on_clause()}))

    # 2. The curated relationship graph — the only join source on this deployment.
    for from_table, table in layer.tables.items():
        for to_table, rule in (table.joins or {}).items():
            if to_table not in layer.tables:
                continue  # a join outside the governed layer is not an edge
            key = frozenset((from_table, to_table))
            if key in seen:
                continue  # already covered by a real constraint
            pairs, unparsed = parse_join_rule(rule)
            if unparsed:
                log.warning("KG build | unparsed join fragment(s) on %s <-> %s | %s",
                            from_table, to_table, unparsed)
            # Keep only pairs whose columns exist on BOTH sides — an edge naming a
            # non-existent column would make check #10 unenforceable.
            valid = tuple((a, b) for a, b in pairs
                          if f"{from_table}.{a}" in columns and f"{to_table}.{b}" in columns)
            if len(valid) != len(pairs):
                log.warning("KG build | dropped join pair(s) with unknown column(s) on "
                            "%s <-> %s | %s", from_table, to_table,
                            [p for p in pairs if p not in valid])
            if not valid:
                log.warning("KG build | join %s <-> %s yielded no usable column pairs | "
                            "rule=%r", from_table, to_table, rule)
                continue
            seen.add(key)
            edges.append(ForeignKeyEdge(
                from_table=from_table, to_table=to_table, column_pairs=valid,
                cardinality=_infer_cardinality(valid, from_table, to_table, columns),
                rule=rule, source=SOURCE_SEMANTIC_LAYER, status=STATUS_ACTIVE,
            ))

    # 3. data_dictionary prose -> PROPOSED. Never traversed, never enforced; recorded so a
    #    reviewer can see a relationship the curated layer has not yet declared.
    for table, prose in join_prose.items():
        if table not in layer.tables:
            continue
        for other in layer.tables:
            if other == table or frozenset((table, other)) in seen:
                continue
            if re.search(rf"\b{re.escape(other)}\b", prose, re.IGNORECASE):
                edges.append(ForeignKeyEdge(
                    from_table=table, to_table=other, column_pairs=(),
                    cardinality=MANY_TO_MANY, rule=prose.strip()[:300],
                    source=SOURCE_DATA_DICTIONARY, status=STATUS_PROPOSED,
                ))
    return edges


def _proposed_name_matches(layer, columns, existing) -> list[ForeignKeyEdge]:
    """Candidate joins found by name matching: a column named exactly like another table's
    primary key. PROPOSED only — this is the heuristic the design refuses to trust at query
    time, surfaced for review rather than acted on."""
    have = {e.pair_key for e in existing}
    pk_owner: dict[str, str] = {}
    for name, table in layer.tables.items():
        if table.primary_key:
            pk_owner.setdefault(table.primary_key.lower(), name)

    proposals: list[ForeignKeyEdge] = []
    for key, col in columns.items():
        owner = pk_owner.get(col.name.lower())
        if not owner or owner == col.table or frozenset((col.table, owner)) in have:
            continue
        have.add(frozenset((col.table, owner)))
        proposals.append(ForeignKeyEdge(
            from_table=col.table, to_table=owner, column_pairs=((col.name, col.name),),
            cardinality=MANY_TO_ONE,
            rule=f"{key} = {owner}.{col.name}  (name match, unreviewed)",
            source=SOURCE_INFERRED, status=STATUS_PROPOSED,
        ))
    return proposals


# --- drift ------------------------------------------------------------------------------

# Declared logical type -> substrings that make a physical MySQL type compatible.
_TYPE_COMPAT = {
    "int": ("int", "bigint", "smallint", "tinyint", "mediumint", "decimal", "numeric"),
    "float": ("float", "double", "decimal", "numeric", "real"),
    "date": ("date", "datetime", "timestamp"),
    "bool": ("tinyint", "bit", "bool"),
    "str": ("char", "text", "enum", "set", "blob", "json"),
    "enum": ("char", "text", "enum", "set"),
}


def _type_compatible(logical_type: str, physical_type: str) -> bool:
    """Deliberately permissive: catches a column that changed CLASS (a numeric that became
    text after a migration, silently breaking aggregation), not precision changes."""
    allowed = _TYPE_COMPAT.get(logical_type.lower())
    if not allowed or not physical_type:
        return True
    return any(token in physical_type for token in allowed)


def _drift(layer, physical_tables, physical_columns) -> DriftReport:
    report = DriftReport()
    for name, table in layer.tables.items():
        if name not in physical_tables:
            report.missing_tables.append(name)
            continue
        for col in table.columns.values():
            key = f"{name}.{col.name}"
            facts = physical_columns.get(key)
            if facts is None:
                report.missing_columns.append(key)
                continue
            physical = facts["data_type"].lower()
            if not _type_compatible(col.type, physical):
                report.type_changes.append(
                    f"{key}: declared {col.type!r} vs physical {physical!r}")
    report.undeclared_tables = sorted(set(physical_tables) - set(layer.tables))
    return report


# --- public API --------------------------------------------------------------------------


def governed_schemas(layer=None) -> list[str]:
    """The DB schemas the governed semantic layer spans (fab_curated / fab_semantic here).
    DERIVED, never hardcoded — so fab_data is excluded by construction, and adding a schema
    to schema.yaml extends introspection automatically."""
    layer = layer or load_semantic_layer()
    return sorted({t.schema for t in layer.tables.values() if t.schema})


def build_metadata_graph() -> tuple[MetadataGraph, DriftReport]:
    """Introspect the database and assemble the KG. Returns (graph, drift_report)."""
    layer = load_semantic_layer()
    schemas = governed_schemas(layer)
    log.info("KG build | introspecting schemas=%s", schemas)

    with get_engine().connect() as conn:
        physical_tables = _fetch_tables(conn, schemas)
        physical_columns = _fetch_columns(conn, schemas)
        declared_fks = _fetch_declared_foreign_keys(conn, schemas)
        dd_descriptions, join_prose = _fetch_data_dictionary(conn)
        database = conn.engine.url.database or ""

    tables = _table_nodes(layer, physical_tables)
    columns = _column_nodes(layer, physical_columns, dd_descriptions)
    terms, defines = _term_nodes(columns)
    edges = _foreign_key_edges(layer, columns, declared_fks, join_prose)
    edges.extend(_proposed_name_matches(layer, columns, edges))

    graph = MetadataGraph(
        tables=tables, columns=columns, terms=terms, foreign_keys=edges, defines=defines,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_database=database,
    )
    graph.fingerprint = graph.compute_fingerprint()
    active = len(graph.active_edges())
    log.info("KG build | tables=%d columns=%d terms=%d defines=%d edges=%d "
             "(active=%d proposed=%d) fingerprint=%s",
             len(tables), len(columns), len(terms), len(defines), len(edges),
             active, len(edges) - active, graph.fingerprint)
    if declared_fks:
        log.info("KG build | %d FK constraint(s) read from information_schema",
                 len(declared_fks))
    else:
        log.info("KG build | no FK constraints declared in the database — join edges come "
                 "from the curated schema.yaml relationship graph (see module docstring)")
    # Terms with no definition are the Stage-0 backlog: they embed poorly (design §7.3).
    undefined = [n for n, t in terms.items() if not t.definition]
    if undefined:
        log.warning("KG build | %d/%d term(s) have no definition — semantic term matching "
                    "is capped until these are written: %s",
                    len(undefined), len(terms), undefined[:10])
    return graph, _drift(layer, physical_tables, physical_columns)


def artifact_path() -> Path:
    return Path(settings.kg_artifact_path)


def write_artifact(graph: MetadataGraph, path: Path | None = None) -> Path:
    """Persist the KG as JSON. This file is the memory backend's whole store and the input
    the neo4j backend upserts from, so one build feeds both."""
    target = Path(path or artifact_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(graph.to_dict(), indent=2, default=str), encoding="utf-8")
    return target


def read_artifact(path: Path | None = None) -> MetadataGraph | None:
    """Load a previously-built KG, or None when it has not been built yet — the agent then
    logs and falls back to today's semantic-layer behaviour rather than failing the turn."""
    target = Path(path or artifact_path())
    if not target.exists():
        return None
    return MetadataGraph.from_dict(json.loads(target.read_text(encoding="utf-8")))


__all__ = ["DriftReport", "artifact_path", "build_metadata_graph", "governed_schemas",
           "parse_join_rule", "read_artifact", "write_artifact"]
