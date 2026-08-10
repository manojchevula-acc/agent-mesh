"""Human-in-the-Loop approval store.

In-memory store using asyncio.Event for zero-polling approval signalling.
All access happens in the single asyncio event loop that uvicorn runs,
so no locks are needed.

For multi-worker deployments, replace with a Redis-backed store.

UC-5 addition: checkpoint persistence in data/checkpoints/{approval_id}.json
so pending approvals survive server restarts and have no timeout.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.mesh.workflow import MeshState

CHECKPOINT_DIR = Path("data/checkpoints")


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
    # UC-3: tool-level HITL fields
    hitl_type: str = "role_approval"       # "role_approval" | "tool_approval"
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)


class ApprovalStore:
    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._live_waiters: set[str] = set()   # UC-5: tracks coroutines in wait_for_approval

    # ── Role-level approval (existing) ──────────────────────────────────────

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
            hitl_type="role_approval",
        )
        return aid

    # ── Tool-level approval (UC-3) ───────────────────────────────────────────

    def create_tool_approval(self, tool_name: str, tool_args: dict) -> str:
        """Create a tool-level approval request.

        User/role context is not known yet at tool-call time — call backfill()
        from DomainExecutor once the signal is detected and state is available.
        """
        aid = uuid.uuid4().hex[:12].upper()
        self._pending[aid] = ApprovalRequest(
            approval_id=aid,
            user_name="",
            role="",
            query="",
            compliance_verdict="",
            hitl_type="tool_approval",
            tool_name=tool_name,
            tool_args=tool_args,
        )
        return aid

    def backfill(self, approval_id: str, user_name: str, role: str, query: str) -> None:
        """DomainExecutor calls this to add user context after detecting the tool signal."""
        if req := self._pending.get(approval_id):
            req.user_name = user_name
            req.role = role
            req.query = query

    # ── Wait / signal ────────────────────────────────────────────────────────

    async def wait_for_approval(self, approval_id: str, timeout: Optional[float] = None) -> bool:
        """Await reviewer decision.

        Returns True = approved, False = rejected.
        timeout=None means wait indefinitely (UC-5 default — no 120s cutoff).
        """
        req = self._pending.get(approval_id)
        if req is None:
            return False
        self._live_waiters.add(approval_id)
        try:
            if timeout is not None:
                await asyncio.wait_for(req.event.wait(), timeout=timeout)
            else:
                await req.event.wait()
            return req.approved is True
        except asyncio.TimeoutError:
            return False
        finally:
            self._live_waiters.discard(approval_id)
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

    def is_live(self, approval_id: str) -> bool:
        """True when a coroutine is actively blocked in wait_for_approval for this ID."""
        return approval_id in self._live_waiters

    # ── Read-only queries ────────────────────────────────────────────────────

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
            "hitl_type": req.hitl_type,
            "tool_name": req.tool_name,
            "tool_args": req.tool_args,
        }

    def get_pending(self) -> list[dict]:
        return [
            {
                "approval_id": r.approval_id,
                "user_name": r.user_name,
                "role": r.role,
                "query": r.query[:200],
                "hitl_type": r.hitl_type,
                "tool_name": r.tool_name,
            }
            for r in self._pending.values()
        ]

    # ── Checkpoint persistence (UC-5) ────────────────────────────────────────

    def save_checkpoint(self, approval_id: str, state: "MeshState") -> None:
        """Persist MeshState to disk before yielding — survives server restart."""
        try:
            CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
            path = CHECKPOINT_DIR / f"{approval_id}.json"
            path.write_text(json.dumps(dataclasses.asdict(state), default=str))
        except Exception:
            pass  # non-fatal — HITL still works in-memory if disk write fails

    def load_checkpoint(self, approval_id: str) -> Optional["MeshState"]:
        """Load MeshState from disk checkpoint. Returns None if file not found."""
        try:
            from src.mesh.workflow import MeshState
            path = CHECKPOINT_DIR / f"{approval_id}.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            valid = set(MeshState.__dataclass_fields__)
            return MeshState(**{k: v for k, v in data.items() if k in valid})
        except Exception:
            return None

    def delete_checkpoint(self, approval_id: str) -> None:
        """Remove checkpoint file after approval/rejection is resolved."""
        try:
            (CHECKPOINT_DIR / f"{approval_id}.json").unlink(missing_ok=True)
        except Exception:
            pass

    def restore(self, approval_id: str, state: "MeshState") -> None:
        """Re-hydrate an ApprovalRequest from a disk checkpoint on server startup."""
        hitl_details = getattr(state, "hitl_details", {}) or {}
        req = ApprovalRequest(
            approval_id=approval_id,
            user_name=state.user_name,
            role=state.role,
            query=state.query,
            compliance_verdict=getattr(state, "compliance_verdict", ""),
            hitl_type=getattr(state, "hitl_type", "role_approval") or "role_approval",
            tool_name=hitl_details.get("tool_name", ""),
            tool_args=hitl_details.get("tool_args", {}),
        )
        self._pending[approval_id] = req


approval_store = ApprovalStore()
