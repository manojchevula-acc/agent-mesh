# Use Case 3: Tool-Level HITL (Human-in-the-Loop)
**Pattern:** Replace role-based blanket approval gate with tool-specific approval gates  
**MAF Feature:** `@tool(approval_mode="always_require")` + interceptor pattern  
**Status:** Not yet implemented  
**Branch context:** `workflow_evaluations+v1`

---

## 1. The Problem

### What Happens Today

When Bob (credit_officer) sends any query through FAB AgentMesh, the system
pauses and waits for a human to approve it before doing anything. Even the
most harmless read-only query — "show me the profitability summary for CUST001"
— triggers a full approval gate.

This happens because of a single check inside `ComplianceExecutor.run()`
in `src/mesh/workflow.py` (lines 1033–1059):

```python
# ── HITL gate: credit_officer requires human approval after compliance passes ──
if state.role == "credit_officer":
    from src.hitl.approval_store import approval_store
    aid = approval_store.create(
        user_name=state.user_name,
        role=state.role,
        query=state.query,
        compliance_verdict=verdict,
        compliance_reasoning=_reasoning_dicts,
    )
    state.hitl_pending = True
    state.hitl_approval_id = aid
    state.hitl_details = { ... }
    await ctx.yield_output(state)   # pipeline pauses here — waits up to 120s
    return
```

This block fires the moment the system sees `role == "credit_officer"` —
before PriceAssistAgent even sees the query, before any intent classification,
before any tool is called. The system has no idea yet whether Bob is asking
for a read-only data lookup or trying to submit a pricing change.

### The Approval Store Mechanism

Behind that block, `src/hitl/approval_store.py` manages the wait:

```
approval_store.create()                    → generates approval_id, stores asyncio.Event
orchestrator.py awaits                     → approval_store.wait_for_approval(aid, timeout=120.0)
Human POSTs /api/approvals/{id}/approve   → signals the asyncio.Event
Orchestrator resumes                       → build_hitl_resume_workflow() runs DomainExecutor
```

The `asyncio.Event` is in-memory. 120-second timeout. If no one approves within
2 minutes, the query is automatically rejected.

### The Real Problem

The gate is based on **WHO is asking** (role = credit_officer), not on
**WHAT is about to happen** (tool action = read vs write).

These two scenarios look identical to the gate:

| Scenario | Risk Level | Current Behavior |
|----------|-----------|-----------------|
| Bob asks "show profitability for CUST001" | Zero — read-only | Full HITL gate fires |
| Bob asks "change pricing for CUST001 to 4.5%" | High — irreversible write | Full HITL gate fires |

Both trigger the same approval workflow. Approvers see routine read requests
all day, start rubber-stamping everything without reading, and the gate
provides no real governance value.

**The write operation is where the real risk is.** Reads should be free.
Write actions should be gated — but only at the moment the specific write
tool is actually called by the LLM, with the exact arguments visible to
the approver.

---

## 2. What the Fix Looks Like

### The Core Idea

Move the HITL gate from **pipeline level** (fires on role, before any tool call)
to **tool level** (fires on specific write tool calls, with full argument context).

```
BEFORE:
  role == credit_officer → PAUSE → approve → run query

AFTER:
  role == credit_officer → run query freely
      → IF LLM calls submit_pricing_change() → PAUSE → approve → execute tool
      → IF LLM calls query_structured_data() → no gate → answer directly
```

### Two Phases

**Phase 1 (immediate):** Remove the blanket credit_officer gate.
Bob can do all read-only queries without approval. One file change, done.

**Phase 2 (write tools):** When write-capable tools are added
(`submit_pricing_change`, `adjust_credit_limit`), gate them at the tool level
using an interceptor pattern inside the tool function itself.

---

## 3. Phase 1 — Remove the Blanket Gate

### What Changes

**File: `agent-mesh/src/mesh/workflow.py`**

Delete lines 1033–1059 — the entire `if state.role == "credit_officer":` block
inside `ComplianceExecutor.run()`. The method ends with:

```python
        _emit_stream_event({"stage": "compliance", "status": "completed",
                            "message": "Compliance check passed"})
        log_state_handoff("compliance", "domain", state, ...)
        await ctx.send_message(state)   # ← all roles including credit_officer reach domain
```

### What the Flow Looks Like After Phase 1

```
Bob (credit_officer) asks "show profitability for CUST001"

    Guardrail ──PASS──► RBAC ──PASS──► Cache ──MISS──► Compliance ──PASS──► Domain
                                                                              ↓
                                                               PriceAssistAgent calls
                                                               query_structured_data()
                                                                              ↓
                                                               DataAgent → MySQL
                                                                              ↓
                                                               Answer returned
                                                               OutputRedaction
                                                                              ↓
                                                               Bob gets answer ✓
                                                               (no approval needed)
```

### Nothing Else Changes

All 5 other pipeline stages are untouched. The `approval_store.py`,
`orchestrator.py` HITL block, and React approval UI all remain in place
for Phase 2.

---

## 4. Phase 2 — Tool-Level Gate for Write Operations

### The Interceptor Pattern

Write-capable tools use an interceptor pattern: when the LLM calls one of
these tools, the tool does NOT execute the write immediately. Instead it:
1. Registers an approval request in `approval_store` with the exact tool arguments
2. Returns a special signal string back to PriceAssistAgent
3. PriceAssistAgent propagates this signal in its answer
4. `DomainExecutor` detects the signal, sets `state.hitl_pending = True` with tool details
5. `orchestrator.py` fires a HITL SSE event carrying the tool name and arguments
6. The React UI renders an approval card showing exactly what is about to happen
7. On approval — DomainExecutor re-runs so PriceAssistAgent can complete the action
8. On rejection — user is told the action was declined, no write happened

### Full Flow Diagram

```
Bob asks "change pricing for CUST001 to 4.5% effective Sep 2026"

    Guardrail → RBAC → Cache → Compliance → DomainExecutor
                                                ↓
                                    A2A call → PriceAssistAgent :8018
                                                ↓
                                    LLM classifies: write intent
                                    LLM calls submit_pricing_change(
                                        customer_id="CUST001",
                                        new_rate=4.5,
                                        effective_date="2026-09-01"
                                    )
                                                ↓
                                    Tool interceptor fires:
                                    - creates ApprovalStore entry
                                    - returns "AWAITING_TOOL_APPROVAL:{...json...}"
                                                ↓
                                    PriceAssistAgent returns this signal as its answer
                                                ↓
                                    DomainExecutor detects AWAITING_TOOL_APPROVAL prefix
                                    sets state.hitl_pending = True
                                    sets state.hitl_details = {
                                        type: "tool_approval",
                                        tool_name: "submit_pricing_change",
                                        tool_args: {customer_id, new_rate, effective_date}
                                    }
                                    ctx.yield_output(state)  ← pipeline pauses
                                                ↓
                                    orchestrator.py detects hitl_pending
                                    emits SSE: event: hitl
                                    {
                                      hitl_type: "tool_approval",
                                      tool_name: "submit_pricing_change",
                                      tool_args: {customer_id: "CUST001",
                                                  new_rate: 4.5,
                                                  effective_date: "2026-09-01"}
                                    }
                                                ↓
                        ┌───────────────────────┴────────────────────────┐
                        ↓ APPROVE                                REJECT ↓
              build_hitl_resume_workflow()              "Action declined.
              DomainExecutor reruns                      No changes made."
              PriceAssistAgent executes
              the actual write
              Answer → Redact → Bob
```

### What the Approver Sees in React UI

**Current approval modal (role-level HITL):**
```
Approval Required
User: Bob | Role: credit_officer
Query: "change pricing for CUST001 to 4.5% effective Sep 2026"
Compliance: PASSED
[Approve]  [Reject]
```

**New approval card (tool-level HITL):**
```
Tool Approval Required
Requested by: Bob (credit_officer)

Tool: submit_pricing_change

Arguments:
┌──────────────────┬──────────────────────┐
│ customer_id      │ CUST001              │
│ new_rate         │ 4.50%                │
│ effective_date   │ 2026-09-01           │
└──────────────────┴──────────────────────┘

[Approve]                        [Reject]
```

The approver sees exactly what is about to change in the system — not just
the text of Bob's query, but the precise parameters of the write operation.

---

## 5. File-by-File Changes

### Phase 1 Only

| File | Change |
|------|--------|
| `src/mesh/workflow.py` | Delete lines 1033–1059 (`if role == "credit_officer"` block) |

### Phase 2 (Additional)

| File | Change |
|------|--------|
| `src/tools/collaboration_tools.py` | Add `submit_pricing_change` and `adjust_credit_limit` tools with interceptor logic. Add `APPROVAL_SIGNAL_PREFIX` constant. Add both to `COORDINATION_TOOLS` list |
| `src/hitl/approval_store.py` | Add `tool_name: str = ""` and `tool_args: dict` fields to `ApprovalRequest` dataclass. Add `create_tool_approval(tool_name, tool_args) -> str` method. Add `backfill(aid, user_name, role, query)` method |
| `src/mesh/workflow.py` | Add `AWAITING_TOOL_APPROVAL` signal detection in `DomainExecutor.run()` before the existing retry pattern checks |
| `src/mesh/orchestrator.py` | Add `hitl_type == "tool_approval"` branch inside the existing HITL interception block (lines 205–237). Tool approval resume calls `build_hitl_resume_workflow()` same as role-level HITL |
| `frontend/src/components/chat/ApprovalModal.tsx` | Add conditional rendering: if `details.type == "tool_approval"`, show tool name + arguments table instead of query text |

### What Does NOT Change

- `src/hitl/approval_store.py` — `wait_for_approval()`, `approve()`, `reject()` methods unchanged
- `api_server.py` — `/api/approvals/{id}/approve` and `/reject` endpoints unchanged
- `src/mesh/workflow.py` — all 5 other executor stages unchanged
- `src/tools/collaboration_tools.py` — `query_structured_data` and `query_knowledge_base` unchanged
- All A2A infrastructure and all 4 agent processes — unchanged

---

## 6. Why This Is Better Than the Current Approach

| Dimension | Current (Role-Level) | Proposed (Tool-Level) |
|-----------|---------------------|----------------------|
| Gate trigger | Role = credit_officer | Specific write tool is called |
| When it fires | Before LLM sees the query | After LLM decides to take a write action |
| Approver sees | Query text only | Exact tool name + exact argument values |
| Read queries | Blocked (approval required) | Free — no gate |
| Write queries | Blocked (approval required) | Blocked — with full action context |
| Governance value | Low — approvers rubber-stamp reads | High — approvers see exactly what changes |
| Credit officer UX | Frustrating — every query waits | Natural — reads are instant |

---

## 7. Verification

### Phase 1

1. Login as Bob (credit_officer)
2. Ask: "show me the profitability summary for CUST001"
3. **Expect:** Answer arrives immediately — no HITL gate, no approval card in React UI
4. Check `state.trail` in the execution panel: `guardrail_pass → rbac_pass → cache_miss → compliance_pass → domain_answer:price_assist → output_redacted`
5. Check `data/audit_trail.jsonl`: no `hitl_pending: true` entry for this request

### Phase 2

1. Login as Bob (credit_officer)
2. Ask: "submit a pricing change for CUST001 to 4.5% effective September 2026"
3. **Expect:** SSE fires `event: hitl` with `hitl_type: "tool_approval"` and `tool_args`
4. **Expect:** React shows approval card with tool name + arguments table
5. Approve → Bob receives confirmation answer; check `data/audit_trail.jsonl` for approval entry with tool name, args, approver, timestamp
6. Repeat and Reject → Bob receives "action was declined" message; verify no write happened

### Regression

1. Alice (relationship_manager) — any query → no HITL
2. Eve (operations_manager) — any query → no HITL (compliance bypass still applies)
3. Carol (compliance_officer) — any query → no HITL
4. Bob read-only query → no HITL (Phase 1 removes the gate)
5. Run `test_agent_mesh.py` — all tests pass (mock at `ask_remote` seam, unchanged)

---

## 8. Related Files Reference

| File | Purpose in This Use Case |
|------|--------------------------|
| `src/mesh/workflow.py` | ComplianceExecutor (HITL gate removed), DomainExecutor (signal detection added) |
| `src/tools/collaboration_tools.py` | Tool definitions — read tools unchanged, write tools added |
| `src/hitl/approval_store.py` | ApprovalStore — extended for tool-level approval metadata |
| `src/mesh/orchestrator.py` | HITL interception block — extended for tool_approval branch |
| `frontend/src/components/chat/ApprovalModal.tsx` | Approval UI — extended for tool args rendering |
| `my_KB/architecture/plan_for_handoff_orchestration_maf.md` | Prior analysis that rejected wholesale HandoffBuilder replacement |
| `my_KB/architecture/handoff_pattern_usecases.md` | Parent document covering UC-2, UC-3, UC-5 |
