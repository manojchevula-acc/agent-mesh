# Human-in-the-Loop (HITL)

HITL is a governance control that pauses execution and requires a human reviewer to approve or reject a request before the domain answer is generated.

---

## Files

| File | Purpose |
|---|---|
| `src/hitl/approval_store.py` | In-memory approval store with async event signalling |
| `src/mesh/workflow.py:ComplianceExecutor` | Triggers HITL after compliance passes for credit officers |
| `src/mesh/orchestrator.py` | Awaits approval, resumes or rejects workflow |
| `api_server.py` | REST endpoints for approval actions |
| `frontend/src/pages/ApprovalPage.tsx` | Standalone reviewer UI (shareable link) |
| `frontend/src/components/ApprovalModal.tsx` | In-chat HITL approval modal |

---

## When HITL Triggers

HITL fires **only** when both conditions are true:
1. The requesting user's role is `credit_officer`
2. ComplianceAgent returned `COMPLIANCE_PASSED`

All other roles (including `compliance_officer`, `relationship_manager`) proceed directly to domain execution after compliance passes.

---

## Flow

```
ComplianceExecutor
    │
    ├── compliance FAILED → return blocked message
    │
    └── compliance PASSED + role == credit_officer
            │
            ▼
    ApprovalStore.create(request_id, query, user)
            │
            ▼
    Emit SSE event: {"type": "hitl", "approval_id": "..."}
            │                      ↕ browser receives, shows ApprovalModal
            ▼
    approval_store.wait_for_approval(id, timeout=120s)
            │
            ├── APPROVED (within 120s)
            │       └─► build_hitl_resume_workflow().run(state)
            │               → DomainExecutor → OutputRedactionExecutor
            │               → return final answer
            │
            ├── REJECTED
            │       └─► return "Request declined by reviewer"
            │
            └── TIMEOUT (120s elapsed)
                    └─► return "Approval request timed out"
```

---

## ApprovalStore Implementation

**File:** `src/hitl/approval_store.py`

Uses `asyncio.Event` for zero-polling signalling — no polling loops or sleep calls.

```python
# Internal structure per pending request
{
    "approval_id": str,
    "request_id": str,
    "query": str,
    "user": str,
    "event": asyncio.Event,     # set() when reviewer acts
    "decision": "approved" | "rejected" | None
}
```

Key methods:
- `create(request_id, query, user)` → returns `approval_id`
- `wait_for_approval(approval_id, timeout=120)` → awaits event, returns decision
- `approve(approval_id)` → sets event with `decision="approved"`
- `reject(approval_id)` → sets event with `decision="rejected"`

---

## REST API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/approvals/{id}` | Get pending approval details (query, user, status) |
| `POST` | `/api/approvals/{id}/approve` | Approve — resumes domain execution |
| `POST` | `/api/approvals/{id}/reject` | Reject — returns declined message to user |

---

## UI Components

**`ApprovalPage.tsx`** — standalone page at `/approvals/{id}`. A reviewer can open this link (e.g. shared via Slack/email) to see the query, user, and approve/reject without being in the same chat session.

**`ApprovalModal.tsx`** — in-chat modal that appears when the SSE `hitl` event is received. Shows the pending request inline and allows approval/rejection within the chat interface.

---

## Post-Approval Workflow

After approval, the orchestrator runs `build_hitl_resume_workflow()` which skips the guardrail and compliance executors (already passed) and runs only:
```
DomainExecutor → OutputRedactionExecutor
```
This avoids redundant LLM calls while still applying output PII redaction.
