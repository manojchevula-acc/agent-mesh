"""The ``kg_lookup`` StateGraph node body.

Placed between ``intent`` and ``agent`` (see agent/graph.py). It performs the KG doc's §4.1
"Metadata KG Lookup" and "Join Path Retrieval" stages ONCE per turn on the latest user turn,
publishes the result into state (for the checkpointer and the audit trail) and into a
ContextVar (for the dynamic pipeline inside the tool).

Why a node rather than pipeline-only:
  * Once per TURN, not once per dynamic call — a turn that self-corrects three times must not
    pay for three embedding calls. The lookup is a property of the QUESTION, not of an
    attempt at it.
  * state["kg_context"] is checkpointed, which makes the audit record part of the conversation
    record rather than a log line the pipeline happened to emit.
  * It runs before tool SELECTION, so any future KG-aware routing has the subschema already.

Node bodies in this codebase stay thin, with the real work in a domain module (compare
intent_node -> routing/intent_classifier). This follows the same shape.

Shadow-first, like every upgrade here: with kg_enabled=False the node returns {} immediately
and the turn is byte-identical to today's.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from sql_agent.config import settings
from sql_agent.kg.context import set_kg_lookup
from sql_agent.kg.retrieval import lookup
from sql_agent.logging_config import get_logger

log = get_logger("kg.node")


def kg_lookup_node(state) -> dict:
    """Resolve the latest user turn against the metadata KG.

    Returns {"kg_context": <audit dict>} so the result is checkpointed with the turn, or {}
    when the KG is off or nothing resolved.
    """
    if not settings.kg_enabled:
        return {}

    cid = state.get("correlation_id") or "-"
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
    )
    if last_human is None or not isinstance(last_human.content, str):
        return {}

    # The intent classifier's table hints, when it ran, seed the lookup the same way they
    # seed schema retrieval today — ADDITIVE only; the KG never treats them as a filter.
    tables_hint = list(state.get("intent", {}).get("tables") or []) or None

    result = lookup(last_human.content, tables_hint)
    set_kg_lookup(result)

    if result.is_empty:
        log.info("[%s] KG lookup | nothing resolved | proceeding on the semantic layer", cid)
        return {}

    log.info("[%s] KG lookup | signals=%s tables=%s terms=%s | %dms", cid,
             result.signals_used, result.tables, result.terms or "none", result.latency_ms)
    return {"kg_context": result.as_dict()}


__all__ = ["kg_lookup_node"]
