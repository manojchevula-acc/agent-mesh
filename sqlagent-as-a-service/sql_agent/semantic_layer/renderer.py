"""Renders schema.yaml into the {schema_context} prompt text for tier-3 generation.

Only the allowed surface is rendered (blocked columns are absent by construction).
No data rows are ever included — schema in the prompt, never data (Design Document §2.3).
"""

from __future__ import annotations

from .loader import SemanticLayer, load_semantic_layer


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


def render_schema_context(
    layer: SemanticLayer | None = None, tables: set[str] | None = None
) -> str:
    """Returns the schema grounding block for DYNAMIC_SQL_GENERATION_PROMPT.

    When ``tables`` is given, only those tables are rendered (scoped schema retrieval,
    Component B). When it is None, the full allowed surface is rendered — the original
    behaviour and the safe fallback. Pruning here narrows only what the generator SEES;
    the validator's allow-list is unchanged, so nothing is ever widened.
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
                " — may ALSO be joined to customer_master (see joins: below) for a "
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
        for col in table.columns.values():
            lines.append(_render_column(table.name, col))
        if table.joins:
            lines.append("  joins:")
            for other, rule in table.joins.items():
                lines.append(f"    - {other}: {rule}")
        lines.append("")
    return "\n".join(lines).rstrip()
