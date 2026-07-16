"""Catalog abstraction (Component B) — backward-compatible re-export.

At POC scale this is a thin layer over schema.yaml; at client scale it becomes the
generated metadata catalog (INFORMATION_SCHEMA + FK constraints + curated descriptions +
embeddings), produced offline by the data-pipeline.

The business glossary itself now lives in ``semantic_layer/glossary.py`` (YAML-backed,
see ``sql_agent/data/business_glossary.yaml``), so it can be extended without a code
change. ``glossary_expand`` is re-exported here unchanged so existing callers
(``selector.py``, ``memory/example_index.py``, ``scripts/build_example_index.py``)
don't need to change their import.
"""

from __future__ import annotations

from sql_agent.semantic_layer.glossary import glossary_expand

__all__ = ["glossary_expand"]
