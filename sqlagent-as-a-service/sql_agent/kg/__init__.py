"""Metadata Knowledge Graph package (KG design doc §4 — metadata layer).

A SCHEMA-level graph: Table / Column / Term nodes joined by HAS_COLUMN, HAS_FOREIGN_KEY,
REFERENCES, DEFINES and HAS_TERM edges. Metadata ONLY — no transactional row data ever
enters this graph (see docs/KG_METADATA_LAYER_DESIGN.md §8.2).

  schema.py      — node/edge dataclasses + the serialisable MetadataGraph artifact
  builder.py     — information_schema + schema.yaml + data_dictionary + glossary -> artifact
  client.py      — WHERE the graph lives / how it is traversed (memory | neo4j)
  templates.py   — Approach B: pre-authored parameterised Cypher
  retrieval.py   — fused signal ranking (template / semantic / exact / lexical / ranked)
  constraints.py — the bundle the SQLValidator's KG checks enforce
  node.py        — the LangGraph ``kg_lookup`` node body
  context.py     — ContextVar carrying the turn's lookup into the dynamic pipeline

Every import here is cheap: neo4j, embeddings and the vector index are imported lazily
inside the module that needs them, so this package imports cleanly with the KG off.
"""

from sql_agent.kg.schema import (
    ColumnNode,
    ForeignKeyEdge,
    MetadataGraph,
    TableNode,
    TermNode,
)

__all__ = ["ColumnNode", "ForeignKeyEdge", "MetadataGraph", "TableNode", "TermNode"]
