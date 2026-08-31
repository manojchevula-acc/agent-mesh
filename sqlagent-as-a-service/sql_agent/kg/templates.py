"""Approach B — parameterised Cypher templates for the well-known access patterns.

KG doc §5.2: rather than DISCOVERING the relevant metadata per question, pre-author one
fixed, tested query per recurring access pattern and match the question to it. The join was
solved once, at authoring time, so this path carries no embedding call, no vector search and
no traversal discovery — and the audit trail is the template name plus its bound parameters,
which is the whole explanation of what metadata was used and why.

TWO DEVIATIONS FROM THE DOC, both driven by what this codebase actually is (design §7.4):

1. Matching is DETERMINISTIC, not LLM tool-selection. This deployment runs with
   PARAMETERISED_TOOLS_ENABLED=false and SEMI_DYNAMIC_TOOLS_ENABLED=false, so the ReAct agent
   is bound exactly one tool and there is no live tool-selection step to hang these on.
   Matching "CUST002" is a regex job with one right answer; deciding it with a model would add
   a round trip, a token bill and non-determinism to an audited path. The templates are ALSO
   exposed as agent tools (tools/kg/metadata_tools.py) for when the fixed tiers return.

2. Templates declare an ANCHOR COLUMN, not a table list. Hardcoding "a customer question
   touches these nine views" goes stale the moment a view is added to schema.yaml. Each
   template names the column identifying its entity, and the query returns every governed
   object carrying it — so the catalogue grows WITH the schema, answering the doc's own stated
   Approach B limitation that "the tool catalogue grows linearly with access patterns".

The Cypher below is what runs on the neo4j backend. The memory backend answers the identical
question over the in-process graph and returns the identical payload shape, so the strategy
layer and the audit record cannot tell them apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sql_agent.logging_config import get_logger

log = get_logger("kg.templates")


@dataclass(frozen=True)
class KGTemplate:
    """One pre-authored access pattern."""

    name: str
    description: str
    params: tuple[str, ...]
    anchor_column: str
    cypher: str
    # Extraction patterns, tried in order. Each exposes a named group matching the template's
    # entity parameter, so a match yields its bound value directly.
    patterns: tuple[re.Pattern, ...] = field(default_factory=tuple)

    def match(self, question: str) -> dict[str, str] | None:
        for pattern in self.patterns:
            found = pattern.search(question or "")
            if found:
                return {k: v.upper() for k, v in found.groupdict().items() if v}
        return None


# Entity id formats as they appear in this dataset and in the eval gold sets (CUST002,
# DEAL010, PROD002). Word-anchored so an id embedded in prose still matches.
_CUSTOMER_ID = re.compile(r"\b(?P<customer_id>CUST\d{2,})\b", re.IGNORECASE)
_DEAL_ID = re.compile(r"\b(?P<deal_id>DEAL\d{2,})\b", re.IGNORECASE)
_PRODUCT_ID = re.compile(r"\b(?P<product_id>PROD\d{2,})\b", re.IGNORECASE)

# One fixed, pre-validated Cypher shared by the entity templates: "which governed objects
# carry this entity's identifying column, and what are their columns?"
_ENTITY_SUBSCHEMA_CYPHER = """
MATCH (t:Table)-[:HAS_COLUMN]->(anchor:Column {name: $anchor_column})
MATCH (t)-[:HAS_COLUMN]->(c:Column)
RETURN t.name          AS table,
       t.db_schema     AS db_schema,
       t.grain         AS grain,
       t.purpose       AS purpose,
       t.is_view       AS is_view,
       collect(c.name) AS columns
ORDER BY t.is_view, t.name
"""

# The doc's third worked example, get_join_path(table_a, table_b). Parameterised by TABLE
# names rather than an entity id, so it is called explicitly rather than pattern-matched.
JOIN_PATH_CYPHER = """
MATCH (a:Table {name: $table_a}), (b:Table {name: $table_b}),
      p = shortestPath((a)-[:HAS_FOREIGN_KEY*1..3]-(b))
WHERE all(r IN relationships(p) WHERE r.status = 'active')
RETURN [n IN nodes(p) | n.name] AS tables,
       [r IN relationships(p) | r.rule] AS rules
"""

# Declaration order IS match precedence. Deal before product before customer: a question
# naming both a deal and its customer is a deal question.
TEMPLATES: tuple[KGTemplate, ...] = (
    KGTemplate(
        name="get_deal_metadata",
        description="Metadata for a question about ONE named deal: every governed object "
                    "keyed by deal_id, with its columns, grain and purpose.",
        params=("deal_id",), anchor_column="deal_id",
        cypher=_ENTITY_SUBSCHEMA_CYPHER, patterns=(_DEAL_ID,),
    ),
    KGTemplate(
        name="get_product_metadata",
        description="Metadata for a question about ONE named product: every governed object "
                    "keyed by product_id, with its columns, grain and purpose.",
        params=("product_id",), anchor_column="product_id",
        cypher=_ENTITY_SUBSCHEMA_CYPHER, patterns=(_PRODUCT_ID,),
    ),
    KGTemplate(
        name="get_customer_metadata",
        description="Metadata for a question about ONE named customer: every governed object "
                    "keyed by customer_id, with its columns, grain and purpose.",
        params=("customer_id",), anchor_column="customer_id",
        cypher=_ENTITY_SUBSCHEMA_CYPHER, patterns=(_CUSTOMER_ID,),
    ),
)

TEMPLATES_BY_NAME = {t.name: t for t in TEMPLATES}


def match_template(question: str) -> tuple[KGTemplate, dict[str, str]] | None:
    """The first template whose pattern fires, with its bound parameters, or None."""
    for template in TEMPLATES:
        params = template.match(question)
        if params:
            return template, params
    return None


def _run_in_memory(template: KGTemplate, client) -> list[dict]:
    """The memory backend's answer to _ENTITY_SUBSCHEMA_CYPHER — same shape, no server."""
    graph = client.snapshot()
    anchor = template.anchor_column.lower()
    tables = sorted({c.table for c in graph.columns.values() if c.name.lower() == anchor})
    rows = []
    for name in tables:
        node = graph.tables.get(name)
        if node is None:
            continue
        rows.append({"table": node.name, "db_schema": node.db_schema, "grain": node.grain,
                     "purpose": node.purpose, "is_view": node.is_view,
                     "columns": [c.name for c in graph.columns_of(name)]})
    rows.sort(key=lambda r: (r["is_view"], r["table"]))  # mirrors the Cypher ORDER BY
    return rows


def run_template(template: KGTemplate, params: dict, client) -> list[dict]:
    """Execute a template against whichever backend is configured.

    Parameters are always BOUND, never interpolated — on the neo4j path through the driver's
    parameter map, on the memory path only ever compared as strings. A template can therefore
    never carry query text from a user question.
    """
    if hasattr(client, "run_cypher"):
        return client.run_cypher(template.cypher,
                                 {"anchor_column": template.anchor_column, **params})
    return _run_in_memory(template, client)


def run_join_path(table_a: str, table_b: str, client) -> dict:
    """get_join_path(table_a, table_b) — the doc's third worked template.

    Uses the client's join_path, so the view-scope filter (check #9) applies here too: an
    illegal path returns empty rather than a chain the validator would reject.
    """
    path = client.join_path(table_a, table_b)
    graph = client.snapshot()
    joins = []
    for left, right in zip(path, path[1:]):
        edge = graph.edge_between(left, right)
        if edge is not None:
            joins.append({"from_table": edge.from_table, "to_table": edge.to_table,
                          "on": [f"{edge.from_table}.{a} = {edge.to_table}.{b}"
                                 for a, b in edge.column_pairs],
                          "cardinality": edge.cardinality, "source": edge.source})
    return {"tables": path, "joins": joins}


__all__ = ["JOIN_PATH_CYPHER", "KGTemplate", "TEMPLATES", "TEMPLATES_BY_NAME",
           "match_template", "run_join_path", "run_template"]
