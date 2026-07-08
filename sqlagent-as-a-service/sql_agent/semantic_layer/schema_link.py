"""Schema-linking planner — the explicit table/join decider (Component B / §5.2.4).

Runs AFTER retrieval and reasons over the RETRIEVED candidates only. It returns a
structured, inspectable query plan (tables, columns, join pairs, group_by, aggregations,
filters). Its table/column choices are intersected back onto the candidate set, so it can
only narrow — never introduce a table/column retrieval did not surface. It never chooses
join KEYS (that is joins.py).

Flag-gated by ``schema_link_enabled``; when off, the planner is a no-op that hands all
candidates to generation (implicit-decider mode). Never raises — on any failure it falls
back to "use all candidates" so the pipeline continues.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sql_agent.agent.prompts import SCHEMA_LINK_PROMPT
from sql_agent.config import settings
from sql_agent.llm import Step, get_llm, log_usage
from sql_agent.logging_config import get_logger
from sql_agent.semantic_layer.loader import load_semantic_layer
from sql_agent.semantic_layer.renderer import render_schema_context

log = get_logger("schema_link")


@dataclass(frozen=True)
class QueryPlan:
    tables: set[str]
    columns: set[str] = field(default_factory=set)
    join_pairs: list[tuple[str, str]] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    aggregations: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)


def _parse(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


def _bare(name: str) -> str:
    """Strip any schema prefix (e.g. 'fab_semantic.rwa_impact_view' -> 'rwa_impact_view').

    Candidates are keyed by bare table name, but the planner echoes back the
    schema-qualified names it saw in the rendered candidate schema. Normalise before
    matching so the planner's choice is honoured instead of falling back to all candidates.
    """
    return name.rsplit(".", 1)[-1] if isinstance(name, str) else name


def link_schema(question: str, candidates: set[str]) -> QueryPlan:
    """Decide the exact tables/columns/join-pairs from the candidate set."""
    if not settings.schema_link_enabled or not candidates:
        # Implicit-decider mode: hand all candidates to generation, no explicit plan.
        return QueryPlan(tables=set(candidates))

    candidate_schema = render_schema_context(tables=candidates)
    try:
        raw = get_llm(Step.PLAN).invoke(
            SCHEMA_LINK_PROMPT.format(question=question, candidate_schema=candidate_schema)
        )
        log_usage(Step.PLAN, raw)
        text = raw.content if hasattr(raw, "content") else str(raw)
        data = _parse(text)
    except Exception as exc:  # noqa: BLE001 — planner must never break the turn
        log.warning("schema-link failed | %s | using all candidates", exc)
        return QueryPlan(tables=set(candidates))

    layer = load_semantic_layer()
    # INTERSECT with candidates — the planner can only narrow, never introduce. Bare-match
    # because the planner echoes schema-qualified names (fab_semantic.x) but candidates are
    # keyed by bare name; without this every view fell back to "use all candidates".
    tables = {_bare(t) for t in data.get("tables", []) if _bare(t) in candidates}
    if not tables:
        tables = set(candidates)  # safe fallback (also covers the explicit "cannot answer")
    allowed_cols = {
        c.name for t in tables for c in layer.tables[t].columns.values()
    }
    columns = {c for c in data.get("columns", []) if c in allowed_cols}
    join_pairs = [
        (_bare(p[0]), _bare(p[1]))
        for p in data.get("join_path", [])
        if isinstance(p, (list, tuple)) and len(p) == 2
        and _bare(p[0]) in tables and _bare(p[1]) in tables
    ]

    plan = QueryPlan(
        tables=tables,
        columns=columns,
        join_pairs=join_pairs,
        group_by=list(data.get("group_by", []) or []),
        aggregations=list(data.get("aggregations", []) or []),
        filters=list(data.get("filters", []) or []),
    )
    log.info("PLAN tables=%s cols=%d joins=%s", sorted(plan.tables), len(plan.columns),
             plan.join_pairs)
    return plan
