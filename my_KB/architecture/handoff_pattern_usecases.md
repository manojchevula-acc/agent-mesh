# Handoff Pattern Use Cases — FAB AgentMesh (MAF)
## Focus: Use Cases 2, 3, and 5

> **Status:** Plan — not yet implemented  
> **Author:** Research & planning session, 2026-08-10  
> **Branch context:** `workflow_evaluations+v1`

---

## Background

FAB AgentMesh runs a Microsoft Agent Framework (MAF) `WorkflowBuilder` pipeline:

```
InputGuardrailExecutor → RBACValidationExecutor → CacheCheckExecutor
  → ComplianceExecutor → DomainExecutor → OutputRedactionExecutor
```

Prior analysis (`plan_for_handoff_orchestration_maf.md`) ruled out replacing this architecture wholesale with `HandoffBuilder`. The three use cases below are entirely **additive** — the happy path of the existing read/query pipeline is not touched.

---

---

# USE CASE 3: Tool-Level HITL via `@tool(approval_mode="always_require")`
*(Easiest to implement, highest immediate value)*

---

## What Problem Does It Solve?

**Bob (credit_officer) cannot use the system for any query without a human approving it — even a read-only data lookup.**

Look at `src/mesh/workflow.py` lines 1033–1059 inside `ComplianceExecutor.run()`:

```python
# ── HITL gate: credit_officer requires human approval after compliance passes ──
if state.role == "credit_officer":
    from src.hitl.approval_store import approval_store
    aid = approval_store.create(
        user_name=state.user_name,
        role=state.role,
        query=state.query,
        ...
    )
    state.hitl_pending = True
    state.hitl_approval_id = aid
    ...
    await ctx.yield_output(state)
    return
```

This single `if state.role == "credit_officer":` check means **100% of Bob's queries pause for human approval**, regardless of what he's asking. If Bob asks "show me the customer 360 dashboard for client XYZ?" — a read-only lookup with zero risk — it still triggers a full approval gate.

**The real risk is not reading — it is writing.** When write-capable tools (`submit_pricing_change`, `adjust_credit_limit`, `generate_term_sheet`) are added, THOSE need HITL. Not a profitability summary read.

---

## Why the Current Approach Doesn't Solve This

The current HITL gates on **role** (who is asking), not on **action** (what is about to happen). It cannot distinguish between:
- `query_structured_data("show profitability summary for CUST001")` — read-only, zero risk
- `submit_pricing_change(customer="CUST001", new_rate=4.5)` — writes to core banking, irreversible

Both look identical at the `state.role == "credit_officer"` check. The gate fires before the LLM even decides which tool to call.

---

## How the Handoff Pattern Solves This

MAF's `@tool(approval_mode="always_require")` decorator moves the HITL check from the pipeline level to the **tool level** — it gates at the moment a specific tool is called by the LLM, not at the moment a specific role is recognized.

**Step 1 — Remove the blanket credit_officer gate**

Delete the `if state.role == "credit_officer":` block at lines 1033–1059 in `workflow.py`. Credit_officer goes straight to `DomainExecutor`, matching every other role.

**Step 2 — Add `approval_mode` to specific tools in `src/tools/collaboration_tools.py`**

```python
# Read-only tools — no change, still free to call
@tool(name="query_structured_data")
async def query_structured_data(question: str) -> str: ...

@tool(name="query_knowledge_base")
async def query_knowledge_base(question: str) -> str: ...

# New write-capable tools — gated with approval_mode
@tool(name="submit_pricing_change", approval_mode="always_require")
async def submit_pricing_change(customer_id: str, new_rate: float, effective_date: str) -> str:
    """Submits a pricing change to core banking. Requires human approval before execution."""
    ...

@tool(name="adjust_credit_limit", approval_mode="always_require")
async def adjust_credit_limit(customer_id: str, new_limit: float, justification: str) -> str:
    """Adjusts a customer credit limit. Requires human approval before execution."""
    ...
```

When PriceAssistAgent calls one of these tools, MAF automatically intercepts the call, fires a `function_approval_request` event, and pauses execution. The LLM never reaches the tool body until a human approves.

**Step 3 — Wire `function_approval_request` in `api_server.py`**

The SSE endpoint `/api/query` (which already handles `hitl`, `intent_suggestion`, and `reasoning` events) gains a new event type: `function_approval_request`. It carries the tool name, exact arguments, and requesting user/role.

**Step 4 — React: Approval card shows tool arguments**

The existing `ApprovalModal.tsx` shows the user's query text. The new tool-level approval card shows:
```
Tool: submit_pricing_change
Customer: CUST001 (Ahmed Al-Mansouri)  
New Rate: 4.25% → 4.50%
Effective Date: 2026-09-01
Requested by: Bob (credit_officer)
```
The approver sees exactly what is about to happen.

---

## What Changes vs What Stays the Same

| Component | Current | After UC-3 |
|-----------|---------|-----------|
| `workflow.py` lines 1033–1059 | Blocks ALL credit_officer queries | **REMOVED** — credit_officer goes straight to DomainExecutor |
| `query_structured_data` | Free to call | Still free — no approval |
| `query_knowledge_base` | Free to call | Still free — no approval |
| `submit_pricing_change` (new) | Doesn't exist | Exists, gated with `approval_mode="always_require"` |
| `adjust_credit_limit` (new) | Doesn't exist | Exists, gated with `approval_mode="always_require"` |
| `approval_store.py` | Stores HITL requests | Still used; MAF routes `function_approval_request` through it |
| `ApprovalPage.tsx` / `ApprovalModal.tsx` | Shows compliance verdict + query text | Extended to show tool name + exact arguments |

---

## Verification

1. **Before UC-3:** Bob (credit_officer) asks "show me profitability summary for CUST001." → HITL approval required. Approver must click approve for a read-only data lookup.

2. **After UC-3 — read query:** Same query → No HITL. Bob gets the answer directly. `state.trail`: `guardrail_pass → rbac_pass → cache_miss → compliance_pass → domain_answer:price_assist → output_redacted`. No pause.

3. **After UC-3 — write operation:** Bob asks "adjust credit limit for CUST001 to AED 5M." PriceAssist decides to call `adjust_credit_limit`. MAF fires `function_approval_request` SSE with `{"tool": "adjust_credit_limit", "args": {"customer_id": "CUST001", "new_limit": 5000000}}`. React shows detailed approval card. Approver approves. Tool executes. Bob gets confirmation.

4. **Audit:** `data/audit_trail.jsonl` contains an entry with the tool name, full arguments, approver identity, decision, and timestamp.

---

---

# USE CASE 2: Compliance-Triggered Investigation Handoff

---

## Important Split: Two Sub-Approaches

> **You asked a great question:** "Can't we just change the prompt of the existing ComplianceAgent to produce a richer explanation, instead of adding new agents?"  
> The honest answer is: **yes, for the explanation part — a prompt change is sufficient and simpler.** HandoffBuilder adds value ONLY for the parts that require external capability (RAG policy lookup, Carol HITL escalation). This section covers both sub-approaches so you can choose.

---

## What Problem Does It Solve?

**When a request fails compliance, the user gets a generic blocked message with zero actionable information. The structured intelligence ComplianceAgent already generated is thrown away.**

Look at `workflow.py` lines 973–1005, the failure branch inside `ComplianceExecutor.run()`:

```python
if "compliance_failed" in verdict.lower():
    ...
    state.blocked = True
    state.block_stage = "compliance"
    state.answer = "Request blocked by the Compliance agent (semantic safety review)."
    state.trail.append("compliance_failed")
    ...
    await ctx.yield_output(state)
    return
```

`state.answer` becomes: **"Request blocked by the Compliance agent (semantic safety review)."**  
That's the entire user-facing response. It doesn't say which of the 7 safety categories was triggered, what specifically caused the failure, or what the user should do next.

**Meanwhile**, just 5 lines earlier at line 965:
```python
_reasoning_entries, verdict = extract_reasoning(verdict, "compliance")
```
`_reasoning_entries` contains a structured JSON block from ComplianceAgent with the exact failure category, severity, role_authorization decision, and recommendation — and **this is thrown away on the failure path**.

---

## Sub-Approach A: Prompt Change Only (Simpler, No New Agents)

### What changes

**1. `compliance_agent.py` system prompt** — Extend the ComplianceAgent's prompt to require a structured `COMPLIANCE_FAILED` response that already contains the explanation:

The current prompt asks ComplianceAgent to output `COMPLIANCE_FAILED: <brief reason>`. Change this to require:
```
COMPLIANCE_FAILED: scope_violation
Category: Role Authorization Failure
User authorized for: pricing queries, portfolio summaries, customer_360 (own portfolio only)
Not authorized for: credit_rating, rwa_impact, margin_analysis
Reason: Requested credit risk data (credit_rating) is outside the relationship_manager scope
Recommended action: Request this information through a credit_officer, or ask your Operations Manager to widen your scope
```

The ComplianceAgent LLM already KNOWS all this — it performs the 7-category check and the role_authorization check in its reasoning. The only change is asking it to format the FAILED response with this detail in the output text, not just in the `<llm_reasoning>` block.

**2. `workflow.py` ComplianceExecutor failure branch** — Instead of setting `state.answer` to the generic static string, compose it from the already-extracted `_reasoning_entries`:

```python
if "compliance_failed" in verdict.lower():
    # Use the structured reasoning that's already extracted
    _cat = "compliance"
    _rec = "Please contact your Operations Manager to review your access scope."
    if _reasoning_entries:
        _data = _reasoning_entries[0].data or {}
        _cat = _data.get("category_triggered", "compliance")
        _rec = _data.get("recommendation", _rec)

    state.answer = (
        f"Your request could not be processed. "
        f"Reason: {verdict.split('COMPLIANCE_FAILED:')[-1].strip()}\n\n"
        f"{_rec}"
    )
```

No new agents. No HandoffBuilder. Two file changes.

### What this covers
- ✅ Which category was triggered (scope_violation, pii_exfiltration, etc.)
- ✅ What the user's authorized scope is
- ✅ What role would have access
- ✅ What to do next (general guidance)
- ✅ Distinguishing adversarial vs innocent failures (ComplianceAgent prompt can instruct: "for prompt_injection or social_engineering, output only COMPLIANCE_FAILED with no explanation")

### What this does NOT cover
- ❌ Looking up the specific CBUAE circular number or policy section (requires RAG)
- ❌ Escalating to Carol (compliance officer) for borderline review (requires HITL)
- ❌ Dynamic policy lookup if FAB policies change (static explanation vs. searched policy)

---

## Sub-Approach B: HandoffBuilder Chain (More Powerful, For Policy Lookup + Carol HITL)

HandoffBuilder is only needed when you want to go BEYOND what the ComplianceAgent LLM already knows and add:
1. **Live policy lookup** — search the actual CBUAE circulars / FAB policy documents in the RAG store and cite the specific clause that governs the restriction
2. **Carol HITL escalation** — for borderline cases, route to Carol (compliance officer) to manually review and override

```
ComplianceExplainerAgent → PolicyAdvisorAgent (RAG lookup) → optional HITL for Carol
```

**When does the RAG lookup actually matter?**  
When Alice asks something borderline and the explanation must cite "per CBUAE Circular 2024-18, Article 4.3, relationship managers are restricted from direct access to credit risk data" rather than just "you don't have access to credit data." The specific circular citation is in the Qdrant store — ComplianceAgent's LLM doesn't reliably know it without RAG.

**When does Carol HITL matter?**  
When `severity == "medium"` (not clearly adversarial, not clearly innocent), a compliance officer review before giving a detailed explanation or allowing escalation is a regulatory best practice.

### Step 1 — Route by category severity (same as Sub-Approach A)

- `prompt_injection`, `social_engineering`, `data_poisoning` → Hard block silently (adversarial)
- `scope_violation`, `role_authorization` → Run HandoffBuilder chain
- `pii_exfiltration`, `destructive_intent` → Medium path (optional judge call)

### Step 2 — PolicyAdvisorAgent

Calls `query_knowledge_base` (the existing RAGAgent tool, already available in the mesh) to search FAB policy docs for the specific clause. Reuses existing infrastructure — no new RAG server needed.

### Step 3 — Optional HITL for Carol

When `severity == "medium"` AND `category == "scope_violation"`: fire a `hitl` SSE event to Carol's dashboard with the pre-filled explanation + policy citation. Carol can override the block or confirm it with a written reason.

---

## Recommendation

**Start with Sub-Approach A** (prompt change + use `_reasoning_entries`). It solves 80% of the problem in 2 file changes with zero new infrastructure. The explanation will be much richer than the current generic message.

**Add Sub-Approach B later** only if you need cited policy references (CBUAE circular numbers) in the failure explanation or need Carol to manually review borderline cases.

---

## What Changes (Sub-Approach A Only)

| Component | Current | After Sub-Approach A |
|-----------|---------|---------------------|
| `compliance_agent.py` system prompt | `COMPLIANCE_FAILED: <brief reason>` | Structured failure format with category + authorized scope + recommendation |
| `workflow.py` failure branch lines 980–998 | `state.answer = "Request blocked by the Compliance agent..."` | Composed from `_reasoning_entries[0].data` (already extracted) |
| `_reasoning_entries` on failure | Discarded | Used to populate `state.answer` |
| Hard blocks (injection/poisoning) | Generic message | Same — ComplianceAgent prompt instructs "no detail for adversarial categories" |
| All other files | Unchanged | Unchanged |

---

## Verification (Sub-Approach A)

1. **Happy path unchanged:** Alice's legitimate query — Compliance passes — UC-2 code never runs. `state.trail` shows `compliance_pass`.

2. **Scope violation test:** Alice asks "show me Bob's credit limit." ComplianceAgent returns `COMPLIANCE_FAILED: scope_violation` with structured reasoning. `state.answer` now contains: which category triggered, Alice's authorized scope, what role would have access, and what she should do. Compare to current generic message.

3. **Hard block test:** Send a prompt injection attempt. ComplianceAgent returns `COMPLIANCE_FAILED: prompt_injection`. Because the prompt instructs no detail for adversarial categories, `state.answer` remains generic. No information leaks.

4. **Regression:** All tests that mock `ask_remote("compliance", ...)` still pass. The failure branch code changes only affect how `state.answer` is composed — the `ctx.yield_output(state)` call is unchanged.

---

---

# USE CASE 5: Durable HITL via `FileCheckpointStorage`

---

## What Problem Does It Solve?

**The HITL feature has a hardcoded 120-second timeout. Any credit officer query that doesn't get approved within 2 minutes is automatically rejected. This makes HITL unusable for real banking workflows.**

The HITL mechanism (in `src/mesh/orchestrator.py` + `src/hitl/approval_store.py`) works like this:

1. `ComplianceExecutor` creates an `ApprovalStore` entry — **an in-memory Python dict**
2. Calls `ctx.yield_output(state)` — pipeline pauses
3. `orchestrator.py` awaits `approval_store.wait_for_approval(aid)` — an **`asyncio.Event` with a 120-second timeout**

```python
# Current in orchestrator.py (simplified):
result = await approval_store.wait_for_approval(aid)  # asyncio.Event, 120s timeout
if result == "approved":
    # resume pipeline
elif result == "timeout":
    return "Request timed out — please resubmit"
```

**Problems with this:**
- **Server restart:** The `asyncio.Event` is in-memory. If the server crashes or is restarted while waiting for approval, the event is gone permanently. The user gets no response.
- **120-second timeout:** If the approver isn't at their desk within 2 minutes, the query is auto-rejected. The user must resubmit the entire pipeline.
- **End of day:** A deal submitted at 4:55 PM requiring credit officer sign-off will time out at 4:57 PM. The approver won't look at it until tomorrow morning. Currently impossible to handle.

In a real bank, approval workflows take hours to days. The 120-second HITL is a demo mechanism. It cannot be used for production credit workflows.

---

## Why the Current Approach Doesn't Solve This

The `asyncio.Event` approach was chosen because it fits naturally into async Python with no additional dependencies. For a demo where someone watches the screen and clicks approve within 2 minutes, it works.

But `asyncio.Event` has a fundamental constraint: it lives in the process's memory. The moment the Python process exits (deliberate restart, crash, deployment), every pending event is gone. The existing `ApprovalStore` class stores state in a Python dict (`_store: dict[str, ApprovalEntry]`) — this dict is never written to disk in a resumable format. Even though `data/audit_trail.jsonl` records that a HITL was created, it cannot reconstruct the pending `asyncio.Event` on restart.

---

## How the Handoff Pattern Solves This

MAF's `FileCheckpointStorage` persists the complete workflow state to disk at every HITL gate. A paused workflow can be resumed from disk on any restart, hours or days later.

**Current flow (in-memory, 120s timeout):**
```
ComplianceExecutor → creates ApprovalStore entry (in-memory dict) 
                   → ctx.yield_output(state)  [state only in memory]
orchestrator.py → asyncio.Event.wait(timeout=120)
                → TIMEOUT: "Request timed out"
                → APPROVE: build_hitl_resume_workflow().run(state)  [state from memory]
```

**After UC-5 (file-backed, no timeout):**
```
ComplianceExecutor → creates ApprovalStore entry
                   → saves state to FileCheckpointStorage  [data/checkpoints/{aid}.json]
                   → ctx.yield_output(state)
orchestrator.py → asyncio.Event.wait(timeout=7_days)  [or no timeout]
  -- server can restart here --
  -- on startup: api_server.py scans data/checkpoints/ → re-hydrates ApprovalStore --
                → APPROVE (any time): loads state from checkpoint → build_hitl_resume_workflow()
                → REJECT: deletes checkpoint → returns rejection
```

**Concrete code changes:**

**1. `src/hitl/approval_store.py`**

Add checkpoint persistence alongside the existing in-memory dict:

```python
CHECKPOINT_DIR = Path("data/checkpoints")

class ApprovalStore:
    def save_checkpoint(self, approval_id: str, state: MeshState) -> None:
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        path = CHECKPOINT_DIR / f"{approval_id}.json"
        path.write_text(json.dumps(dataclasses.asdict(state), default=str))

    def load_checkpoint(self, approval_id: str) -> MeshState | None:
        path = CHECKPOINT_DIR / f"{approval_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return MeshState(**{k: v for k, v in data.items() if k in MeshState.__dataclass_fields__})

    def delete_checkpoint(self, approval_id: str) -> None:
        path = CHECKPOINT_DIR / f"{approval_id}.json"
        path.unlink(missing_ok=True)
```

**2. `src/mesh/workflow.py` — `ComplianceExecutor`**

Add `save_checkpoint` call after creating the `ApprovalStore` entry (before `yield_output`):

```python
# After approval_store.create(...)
approval_store.save_checkpoint(aid, state)  # ← new line
...
await ctx.yield_output(state)
```

**3. `api_server.py` — startup lifespan**

On server start, scan `data/checkpoints/` and re-hydrate any pending approvals:

```python
async def lifespan(app):
    # ... existing startup (A2A nodes, OTel) ...
    # Re-hydrate pending HITL approvals from disk
    for checkpoint_file in CHECKPOINT_DIR.glob("*.json"):
        approval_id = checkpoint_file.stem
        state = approval_store.load_checkpoint(approval_id)
        if state:
            approval_store.restore(approval_id, state)  # re-adds to in-memory dict
    yield
    # ... existing shutdown ...
```

**4. `orchestrator.py`**

Remove the 120-second timeout on `wait_for_approval` (or set it to days):

```python
# Before: asyncio.Event with 120s timeout
# After: asyncio.Event with no timeout (or very long timeout)
result = await approval_store.wait_for_approval(aid)  # no timeout
```

**5. `build_hitl_resume_workflow()` — no change needed**

This function already takes a `MeshState` and runs `DomainExecutor → OutputRedactionExecutor`. The only change is that the `MeshState` now comes from `approval_store.load_checkpoint(aid)` on restart instead of from memory. The function itself is identical.

---

## What Changes vs What Stays the Same

| Component | Current | After UC-5 |
|-----------|---------|-----------|
| `src/hitl/approval_store.py` | In-memory dict + asyncio.Event + 120s timeout | Gains `save_checkpoint()` / `load_checkpoint()` backed by `data/checkpoints/` |
| `workflow.py` ComplianceExecutor (lines 1041–1059) | Creates ApprovalStore entry | Also calls `save_checkpoint(aid, state)` before yielding |
| `orchestrator.py` HITL await | `wait_for_approval(aid)` — 120s timeout | Same call — no timeout (or 7-day timeout) |
| `api_server.py` lifespan startup | Starts A2A nodes, OTel | Also scans `data/checkpoints/` and re-hydrates pending approvals |
| `build_hitl_resume_workflow()` | Called with in-memory state | Called with state loaded from checkpoint — function unchanged |
| `data/checkpoints/` | Does not exist | New directory; `{approval_id}.json` per pending HITL |
| `audit_trail.jsonl` | Logs HITL created/approved/rejected | Also logs checkpoint saved/restored events |
| Non-HITL roles (Alice, Carol, Eve) | Pipeline runs end-to-end | Unchanged — no checkpoint file written |

---

## Verification

1. **Checkpoint written:** Bob (credit_officer) sends a query. Compliance passes. HITL fires. Verify `data/checkpoints/{approval_id}.json` exists with the full `MeshState` fields (query, role, user, compliance_verdict, permission_scope, etc.).

2. **Restart survival:** While an approval is pending, stop the server (`Ctrl+C`). Restart it. Navigate to `GET /api/approvals/{approval_id}` — the pending approval is still listed. The checkpoint was restored from disk on startup via the lifespan handler.

3. **Overnight approval:** Submit a credit_officer query. Don't approve. Restart the server multiple times. Next session, open the approval page, approve. `build_hitl_resume_workflow()` runs with the state loaded from the checkpoint file. Bob gets his answer.

4. **Reject + cleanup:** Reject a pending approval. Verify `data/checkpoints/{approval_id}.json` is deleted. `data/audit_trail.jsonl` shows the rejection with timestamp and approver identity.

5. **Timeout removed:** In `src/hitl/approval_store.py`, confirm `wait_for_approval()` no longer has a 120-second timeout. Pending approvals stay pending indefinitely until a human acts.

6. **Regression — other roles:** Alice, Carol, Eve queries run end-to-end. After their queries, `data/checkpoints/` is empty (no checkpoint written for non-HITL roles).

---

---

## Implementation Order

| Priority | Use Case | Why First |
|----------|----------|-----------|
| **1** | UC-3: Tool-Level HITL | Smallest change; removes approval fatigue immediately; no new agents needed |
| **2** | UC-5: Durable HITL | Makes the existing HITL feature production-viable; `approval_store.py` + `workflow.py` only |
| **3** | UC-2: Compliance Investigation | Most new code (new agents); highest governance value; safe to add last |

## What Is NOT Changing

- The `InputGuardrailExecutor → RBACValidationExecutor → CacheCheckExecutor → ComplianceExecutor → DomainExecutor → OutputRedactionExecutor` pipeline on the happy path
- `PriceAssistAgent → DataAgent/RAGAgent` agent-as-tools delegation pattern
- A2A cross-process architecture (4 OS processes)
- All existing OTel spans, `audit_trail.jsonl` logging, and `data/logs/state/` state traces
- All existing `test_agent_mesh.py` tests — mocking at `ask_remote` seam remains valid
