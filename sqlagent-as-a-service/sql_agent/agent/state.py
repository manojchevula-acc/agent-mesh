"""Section 10.1 — Agent state schema."""

from typing import Any, TypedDict

from agent_framework import Message


class AgentState(TypedDict, total=False):
    # MAF has no state reducers, so the append-with-id-replace semantics that
    # LangGraph's add_messages provided are implemented by add_messages() below and
    # applied explicitly by the executors that append.
    messages: list
    caller_agent: str
    auth_scopes: set
    tool_call_count: int
    dynamic_call_count: int
    correlation_id: str
    # Superstep counter — the MAF stand-in for LangGraph's config recursion_limit=25.
    # Incremented once per executor invocation; see workflow.py _guard_supersteps.
    step_count: int
    # --- Identity + light state (persisted per-thread by the conversation store) ---
    user_id: str
    session_id: str
    resolved_entities: dict[str, Any]   # e.g. {"customer": "CUST002"} for follow-ups
    # Advisory intent classification for the current turn (Component A). Shadow-first:
    # logged and available downstream; only alters routing when enforcement is enabled.
    intent: dict[str, Any]              # {tier, domain, entities, missing, confidence, ...}
    # Metadata-KG grounding for the current turn (see sql_agent/kg/node.py). Checkpointed
    # so the audit trail — which terms resolved to which columns, which join edges were
    # retrieved, under which KG fingerprint — is part of the conversation record rather
    # than only a log line. Absent when the KG is disabled or resolved nothing.
    kg_context: dict[str, Any]


def add_messages(existing: list, new: list) -> list:
    """Port of langgraph.graph.message.add_messages.

    Append `new` onto `existing`, REPLACING in place any message whose id already
    appears. This codebase only ever appends, but the replace branch is kept so the
    reducer stays a faithful drop-in (e.g. if a future step edits a message).
    """
    merged = list(existing or [])
    index = {getattr(m, "message_id", None): i for i, m in enumerate(merged)
             if getattr(m, "message_id", None)}
    for m in new or []:
        mid = getattr(m, "message_id", None)
        if mid and mid in index:
            merged[index[mid]] = m
        else:
            if mid:
                index[mid] = len(merged)
            merged.append(m)
    return merged


def merge_state(prior: AgentState | None, incoming: AgentState) -> AgentState:
    """Port of LangGraph's channel-update semantics on a checkpointed thread.

    `messages` goes through the reducer; every other key in `incoming` OVERWRITES
    the checkpointed value; keys absent from `incoming` (kg_context, intent,
    resolved_entities) keep the checkpointed value. This is exactly what happened
    when service/api.py passed a partial state to graph.invoke() with a thread_id,
    and it is why the API can send tool_call_count=0 each turn while kg_context
    survives.
    """
    if not prior:
        return dict(incoming)
    merged = {**prior, **incoming}
    merged["messages"] = add_messages(prior.get("messages", []),
                                      incoming.get("messages", []))
    return merged
