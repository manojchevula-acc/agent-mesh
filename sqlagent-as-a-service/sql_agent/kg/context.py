"""Carries the turn's KG lookup from the graph node into the dynamic pipeline.

The KG doc models its stages as one linear flow. Here the flow is split: the StateGraph runs
per TURN (intent -> kg_lookup -> agent -> tools -> agent), while SQL generation lives inside
the analytical_query tool and runs per CALL. The kg_lookup node resolves the question once,
and the pipeline inside the tool needs that result — but a ToolNode-dispatched tool receives
only its declared arguments, not graph state.

A ContextVar bridges the two, exactly as tools/registry.py already does for caller auth
scopes. Set once per turn by the node, read by the pipeline.

Falling back is a first-class path, not an error case: when nothing was published (the node
is disabled, or the pipeline is being driven by eval/run_agent.py or a test), the pipeline
performs its own lookup. Grounding is therefore identical whether the code is reached through
the graph or called directly — the ContextVar only saves repeating it.
"""

from __future__ import annotations

from contextvars import ContextVar

from sql_agent.kg.retrieval import KGLookup

_current_lookup: ContextVar[KGLookup | None] = ContextVar("_current_kg_lookup", default=None)


def set_kg_lookup(lookup: KGLookup | None) -> None:
    _current_lookup.set(lookup)


def get_kg_lookup() -> KGLookup | None:
    """The lookup published for this turn, or None when the pipeline should do its own."""
    return _current_lookup.get()


def clear_kg_lookup() -> None:
    _current_lookup.set(None)


__all__ = ["clear_kg_lookup", "get_kg_lookup", "set_kg_lookup"]
