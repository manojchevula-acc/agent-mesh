"""Conversational memory package (Option B — MAF thread memory + JSONL persistence).

Public API:
    ConversationStore        — high-level memory facade used by the mesh
    get_conversation_store    — factory bound to the configured backend
    ConversationBackend       — backend interface (for custom/Redis backends)
"""
from src.memory.base import ConversationBackend
from src.memory.conversation_store import ConversationStore, get_conversation_store

__all__ = ["ConversationBackend", "ConversationStore", "get_conversation_store"]
