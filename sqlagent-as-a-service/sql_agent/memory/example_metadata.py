"""Few-shot example metadata generation utility (Phase 3).

Combines the deterministic classifiers (``sql_pattern.classify_sql``,
``glossary.matched_terms``, ``routing.intent_tagger.tag_intent``) into the metadata
schema stored per example (``sql_agent/data/example_seed.yaml`` /
``examples.metadata``):

    tables, columns, joins, intent, sql_pattern, aggregations, filters,
    business_terms, complexity

Almost everything here is auto-derivable from the example's own ``validated_sql`` via
``sqlglot`` AST introspection — a curated example never needs its tables/columns/joins/
aggregations/filters hand-authored. Only ``business_terms`` (glossary hits in the
question) and ``intent``/``sql_pattern`` need no manual input either, since both are
rule-based. Used by ``scripts/generate_example_metadata.py`` (the seed set) and
partially — no SQL is known yet — by retrieval at query time to tag the LIVE question.
"""

from __future__ import annotations

from sql_agent.memory.sql_pattern import classify_sql
from sql_agent.routing.intent_tagger import tag_intent
from sql_agent.semantic_layer.glossary import matched_terms


def _complexity(tables: list[str], joins: list[str], aggregations: list[str],
                 patterns: list[str], has_group: bool) -> str:
    score = len(tables) + len(joins) + len(aggregations) + (1 if has_group else 0)
    if any(p in patterns for p in ("cte", "window_function", "subquery")):
        score += 2
    if score <= 1:
        return "low"
    if score <= 3:
        return "medium"
    return "high"


def generate(question: str, sql: str | None = None) -> dict:
    """Full metadata dict for one example (``question`` + its ``validated_sql``).

    Degrades gracefully when ``sql`` is missing/unparseable: tables/columns/joins/
    aggregations/filters/sql_pattern come back empty, but ``intent`` and
    ``business_terms`` are still derived from the question text alone — this is the
    same code path used to tag a live question, which never has SQL at retrieval time.
    """
    shape = classify_sql(sql)
    tables = shape["tables"] if shape else []
    columns = shape["columns"] if shape else []
    joins = shape["joins"] if shape else []
    aggregations = shape["aggs"] if shape else []
    filters = shape["where_cols"] if shape else []
    patterns = shape["patterns"] if shape else []
    has_group = bool(shape and shape["group_cols"])

    return {
        "tables": tables,
        "columns": columns,
        "joins": joins,
        "intent": tag_intent(question, sql),
        "sql_pattern": patterns,
        "aggregations": aggregations,
        "filters": filters,
        "business_terms": matched_terms(question),
        "complexity": _complexity(tables, joins, aggregations, patterns, has_group),
    }
