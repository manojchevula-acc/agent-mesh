"""Human-in-the-Loop approval store.

In-memory store using asyncio.Event for zero-polling approval signalling.
All access happens in the single asyncio event loop that uvicorn runs,
so no locks are needed.

For multi-worker deployments, replace with a Redis-backed store.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ApprovalRequest:
    approval_id: str
    user_name: str
    role: str
    query: str
    compliance_verdict: str
    compliance_reasoning: list = field(default_factory=list)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: Optional[bool] = None


class ApprovalStore:
    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}

    def create(
        self,
        user_name: str,
        role: str,
        query: str,
        compliance_verdict: str,
        compliance_reasoning: list | None = None,
    ) -> str:
        aid = uuid.uuid4().hex[:12].upper()
        self._pending[aid] = ApprovalRequest(
            approval_id=aid,
            user_name=user_name,
            role=role,
            query=query,
            compliance_verdict=compliance_verdict,
            compliance_reasoning=compliance_reasoning or [],
        )
        return aid

    async def wait_for_approval(self, approval_id: str, timeout: float = 120.0) -> bool:
        """Await reviewer decision. Returns True=approved, False=rejected/timed-out."""
        req = self._pending.get(approval_id)
        if req is None:
            return False
        try:
            await asyncio.wait_for(req.event.wait(), timeout=timeout)
            return req.approved is True
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(approval_id, None)

    def approve(self, approval_id: str) -> bool:
        return self._signal(approval_id, approved=True)

    def reject(self, approval_id: str) -> bool:
        return self._signal(approval_id, approved=False)

    def _signal(self, approval_id: str, approved: bool) -> bool:
        req = self._pending.get(approval_id)
        if req is None:
            return False
        req.approved = approved
        req.event.set()
        return True

    def get(self, approval_id: str) -> Optional[dict]:
        """Return approval details without consuming the request (for the approval page fetch)."""
        req = self._pending.get(approval_id)
        if req is None:
            return None
        return {
            "approval_id": req.approval_id,
            "user_name": req.user_name,
            "role": req.role,
            "query": req.query,
            "compliance_verdict": req.compliance_verdict,
            "compliance_reasoning": req.compliance_reasoning,
        }

    def get_pending(self) -> list[dict]:
        return [
            {
                "approval_id": r.approval_id,
                "user_name": r.user_name,
                "role": r.role,
                "query": r.query[:200],
            }
            for r in self._pending.values()
        ]


approval_store = ApprovalStore()
