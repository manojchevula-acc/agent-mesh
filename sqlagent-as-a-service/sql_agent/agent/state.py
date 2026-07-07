"""Section 10.1 — Agent state schema."""

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    caller_agent: str
    auth_scopes: set
    tool_call_count: int
    dynamic_call_count: int
    correlation_id: str
    # --- Identity + light state (persisted per-thread by the checkpointer) ---
    user_id: str
    session_id: str
    resolved_entities: dict[str, Any]   # e.g. {"customer": "CUST002"} for follow-ups
    # Advisory intent classification for the current turn (Component A). Shadow-first:
    # logged and available downstream; only alters routing when enforcement is enabled.
    intent: dict[str, Any]              # {tier, domain, entities, missing, confidence, ...}
