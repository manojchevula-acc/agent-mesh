"""Renders schema.yaml into the {schema_context} prompt text for tier-3 generation.

Only the allowed surface is rendered (blocked columns are absent by construction).
No data rows are ever included — schema in the prompt, never data (Design Document §2.3).

Everything the model reads about tables, columns, purposes, and joins is DERIVED from the
semantic layer here — nothing is hardcoded in a prompt. Adding a table/view/join in
production is a schema change only; the rendered context (and the AVAILABLE JOINS map)
updates automatically.
"""

from __future__ import annotations

from .loader import SemanticLayer, load_semantic_layer, relationship_graph


def _render_column(table_name: str, col) -> str:
    bits = [f"{col.type}"]
    if col.values:
        bits.append("enum=[" + ", ".join(col.values) + "]")
    if col.unit:
        bits.append(f"unit={col.unit}")
    if col.format:
        bits.append(f"format={col.format!r}")
    meta = ", ".join(bits)
    desc = f" — {col.desc}" if col.desc else ""
    note = f" [NOTE: {col.note}]" if col.note else ""
    return f"    - {col.name} ({meta}){desc}{note}"


def _render_available_joins(selected: dict) -> list[str]:
    """A consolidated, SCOPED join map: only the pairs among the rendered tables that the
    schema's relationship graph actually declares, with the real join predicate.

    Derived from relationship_graph() (itself parsed from schema.yaml's ``joins:``), so it
    reflects whatever joins exist — 5 tables or 500 — with nothing hardcoded. This is the
    authoritative "which joins may I use" signal for the planner and generator; a pair not
    listed here is not a permitted join.
    """
    names = set(selected)
    graph = relationship_graph()
    seen: set[frozenset] = set()
    pairs: list[str] = []
    for a in sorted(names):
        for b, rule in sorted(graph.get(a, {}).items()):
            if b not in names:
                continue  # scope: only joins BETWEEN the rendered tables
            key = frozenset((a, b))
            if key in seen:
                continue
            seen.add(key)
            pairs.append(f"  - {a} <-> {b}  ON ({rule})")
    if not pairs:
        return ["AVAILABLE JOINS (only these table pairs may be joined):",
                "  (none — every table above is standalone for this question)"]
    return ["AVAILABLE JOINS (only these table pairs may be joined; never assume any other "
            "relationship):", *pairs]


def render_schema_context(
    layer: SemanticLayer | None = None, tables: set[str] | None = None
) -> str:
    """Returns the schema grounding block for the planner / DYNAMIC_SQL_GENERATION_PROMPT.

    When ``tables`` is given, only those tables are rendered (scoped schema retrieval,
    Component B) and the AVAILABLE JOINS map is scoped to them. When it is None, the full
    allowed surface is rendered — the original behaviour and the safe fallback. Pruning
    here narrows only what the model SEES; the validator's allow-list is unchanged.
    """
    layer = layer or load_semantic_layer()
    selected = (
        layer.tables
        if tables is None
        else {n: t for n, t in layer.tables.items() if n in tables}
    )
    lines: list[str] = []
    for table in selected.values():
        # Use the schema-qualified name so generated SQL references the right schema
        # (base tables in fab_curated, views in fab_semantic) regardless of the
        # connection's default schema. Views are labelled so the generator prefers a
        # single pre-joined view and never tries to join one.
        if table.is_view:
            join_note = (
                " — may ALSO be joined to customer_master (see AVAILABLE JOINS) for a "
                "customer attribute (e.g. industry, region) this view lacks; never to "
                "anything else" if table.joins else ", do NOT join"
            )
            lines.append(
                f"VIEW {table.qualified_name}  (grain: {table.grain}; pk: "
                f"{table.primary_key})  [pre-joined read model — query STANDALONE{join_note}]"
            )
        else:
            lines.append(
                f"TABLE {table.qualified_name}  (grain: {table.grain}; pk: {table.primary_key})"
            )
        # Purpose — the "what this is for / when to pick it" line, sourced from schema.yaml.
        # Rendered right under the header so the model can distinguish near-synonym tables.
        if table.purpose:
            lines.append(f"  purpose: {table.purpose}")
        for col in table.columns.values():
            lines.append(_render_column(table.name, col))
        lines.append("")

    # Consolidated, scoped join map (replaces the old per-table joins: blocks, which listed
    # neighbours that may not even be in this candidate set). Derived from the graph.
    lines.extend(_render_available_joins(selected))
    return "\n".join(lines).rstrip()
