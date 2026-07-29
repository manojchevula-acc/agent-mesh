"""Intent-match user decision store.

In-memory store using asyncio.Event for zero-polling decision signalling.
All access happens in the single asyncio event loop that uvicorn runs,
so no locks are needed.

For multi-worker deployments, replace with a Redis-backed store.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IntentDecision:
    entry_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    accepted: Optional[bool] = None
    # Which specific candidate the user chose (may differ from primary entry_id)
    chosen_entry_id: Optional[str] = None


class IntentDecisionStore:
    """Stores pending intent-match decisions keyed by primary ChromaDB entry_id.

    The orchestrator calls wait_for_decision() to pause until the user
    resolves the suggestion via POST /api/cache/intent-decision.
    """

    def __init__(self) -> None:
        self._pending: dict[str, IntentDecision] = {}

    def create_pending(self, entry_id: str) -> None:
        """Register a new pending decision for the given entry_id."""
        self._pending[entry_id] = IntentDecision(entry_id=entry_id)

    async def wait_for_decision(self, entry_id: str, timeout: float = 60.0) -> tuple[bool, Optional[str]]:
        """Await user decision. Returns (accepted, chosen_entry_id).

        accepted=False / chosen_entry_id=None on rejection or timeout.
        On timeout, resolves as rejected (run fresh) — pipeline never hangs permanently.
        """
        dec = self._pending.get(entry_id)
        if dec is None:
            # Not registered yet — create on-the-fly (race between orchestrator and executor)
            dec = IntentDecision(entry_id=entry_id)
            self._pending[entry_id] = dec
        try:
            await asyncio.wait_for(dec.event.wait(), timeout=timeout)
            return (dec.accepted is True, dec.chosen_entry_id)
        except asyncio.TimeoutError:
            return (False, None)
        finally:
            self._pending.pop(entry_id, None)

    def resolve(self, entry_id: str, accepted: bool, chosen_entry_id: Optional[str] = None) -> bool:
        """Signal a user decision. chosen_entry_id is which candidate was selected.

        Returns True if the pending entry was found.
        """
        dec = self._pending.get(entry_id)
        if dec is None:
            return False
        dec.accepted = accepted
        dec.chosen_entry_id = chosen_entry_id or entry_id
        dec.event.set()
        return True

    def get_pending_ids(self) -> list[str]:
        """Return the list of entry_ids currently awaiting a decision."""
        return list(self._pending.keys())


intent_decision_store = IntentDecisionStore()
