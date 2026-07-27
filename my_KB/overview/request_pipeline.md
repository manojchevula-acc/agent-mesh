# Request Pipeline — End-to-End Flow

How a single user query travels through the entire AgentMesh system.

---

## Pipeline at a Glance

```
User Input
    │
    ▼
[1] InputGuardrailExecutor       ← deterministic regex: injection / PII / destructive intent
    │ blocked → return error
    ▼
[2] RBACValidationExecutor       ← resolve username → role → allowed/denied task scopes
    │ blocked → return 403
    ▼
[3] ComplianceExecutor           ← A2A call → ComplianceAgent (port 8015)
    │                               7-category LLM safety check + RBAC authorization
    │ COMPLIANCE_FAILED → return blocked message
    │ credit_officer role → trigger HITL approval (wait up to 120s)
    ▼
[4] DomainExecutor               ← A2A call → PriceAssistAgent (port 8018)
    │                               Intent classification → delegates to DataAgent / RAGAgent / both
    │                               Retry on: tool-echo, meta-response, bracket-placeholder
    ▼
[5] OutputRedactionExecutor      ← deterministic PII redaction on final answer
    │
    ▼
MeshResult → API response / CLI output
```

---

## Step-by-Step Detail

### Step 1 — Deterministic Input Guardrail
**File:** `src/guardrails/deterministic_filters.py:screen_input()`  
**What happens:**
- Regex scan for prompt injection patterns (`ignore previous instructions`, `jailbreak`, etc.)
- PII detection: email addresses, SSNs, credit card numbers, phone numbers
- Destructive intent keywords: `delete`, `drop table`, `rm -rf`, `format`, etc.

If any pattern matches → workflow terminates immediately, no LLM call is made.

---

### Step 2 — RBAC Validation
**File:** `src/mesh/workflow.py:RBACValidationExecutor`  
**File:** `src/auth/role_permissions.py`  
**What happens:**
- Resolves the requesting user's `BankingRole` from the identity provider
- Looks up `ROLE_PERMISSIONS[role]` → `allowed_tasks`, `denied_tasks`, `scope`
- Injects the resolved scope into `MeshState` for downstream use
- If the task matches `denied_tasks` → workflow terminates with a 403-style message

---

### Step 3 — LLM Compliance Check
**File:** `src/mesh/workflow.py:ComplianceExecutor`  
**Agent:** `src/agents/compliance_agent.py`  
**What happens:**
- Sends the user query + RBAC scope to ComplianceAgent over A2A (port 8015)
- ComplianceAgent runs 7-category safety review:
  1. Prompt injection / jailbreak attempt
  2. PII exfiltration
  3. Destructive commands
  4. Social engineering
  5. Context poisoning
  6. Scope violation (vs. RBAC scope)
  7. Authorization check (role allowed for this task?)
- Returns `COMPLIANCE_PASSED` or `COMPLIANCE_FAILED` with a `<llm_reasoning>` block
- Two roles bypass the A2A call entirely: `platform_administrator`, `operations_manager`

**HITL branch (credit officers):**  
If `role == credit_officer` and compliance passed → `ApprovalStore.create()` fires, a `hitl` SSE event is emitted to the frontend, the executor waits up to 120 s for human approval. On approval → `build_hitl_resume_workflow()`. On rejection → declined message returned.

---

### Step 4 — Domain Execution
**File:** `src/mesh/workflow.py:DomainExecutor`  
**Agent:** `src/agents/price_assist_agent.py`  
**What happens:**
- Sends query + conversation summary + RBAC scope to PriceAssistAgent over A2A (port 8018)
- PriceAssistAgent classifies intent:
  - **data** → calls `query_structured_data` tool → A2A → DataAgent (port 8016) → MCP → DataLayer
  - **knowledge** → calls `query_knowledge_base` tool → A2A → RAGAgent (port 8017) → MCP → RAG service
  - **hybrid** → calls both tools and synthesizes
- DomainExecutor retries (up to 2 times) on three anti-patterns:
  - **Tool-call echo** — answer is just the raw tool call
  - **Meta-response** — agent says "I have retrieved..." instead of answering
  - **Bracket placeholder** — answer contains `[RESULT]` or `[DATA]` template text

---

### Step 5 — Output PII Redaction
**File:** `src/guardrails/deterministic_filters.py:redact_pii()`  
**What happens:**
- Regex replacement on the final answer text
- Replaces any PII spans with typed placeholders:
  - Email → `[REDACTED_EMAIL]`
  - SSN → `[REDACTED_SSN]`
  - Credit card → `[REDACTED_CC]`
  - Phone → `[REDACTED_PHONE]`

---

## Conversation Memory Injection

Before Step 4, if `ENABLE_CONVERSATION_MEMORY=true`:
- `ConversationStore.load_with_summary(session_id)` loads the rolling summary record
- DomainExecutor injects it as a `[Conversation Summary]` block into PriceAssistAgent's prompt
- After Step 4, `summarize_and_persist()` fires as a non-blocking async task — LLM produces a ≤200-word summary of all prior turns and persists it to the JSONL session file

---

## Audit Trail

`AuditMiddleware` intercepts every agent invocation (ComplianceAgent + PriceAssistAgent + DataAgent + RAGAgent) and appends a JSONL record to `data/audit_trail.jsonl` with:
- `request_id`, `trace_id`, `span_id`, `session_id`
- `user`, `role`, `agent_name`
- PII-scrubbed `inputs` and `output`
- `status`, `latency_ms`, `input_tokens`, `output_tokens`

---

## SSE Streaming Events (API mode)

When using `POST /api/query/stream`, each pipeline stage emits SSE events to the browser:

| Event type | When fired |
|---|---|
| `stage` | Each executor starts/completes |
| `reasoning` | LLM reasoning block extracted |
| `hitl` | HITL approval required |
| `result` | Final answer ready |
| `done` | Stream complete |
| `error` | Any unhandled error |

---

## Workflow Variants

| Variant | Function | Used by |
|---|---|---|
| Full pipeline | `build_mesh_workflow()` | API server + CLI |
| Post-HITL resumption | `build_hitl_resume_workflow()` | After credit officer approval |
| DevUI single-process | `build_devui_workflow()` | `devui_app.py` |

---

## Bypass Modes

| Config | Effect |
|---|---|
| `ENABLE_COMPLIANCE=false` | Skips A2A call to ComplianceAgent; stamps `COMPLIANCE_PASSED` automatically |
| `ENABLE_PRICE_ASSIST=false` | DomainExecutor routes directly to DataAgent, skipping PriceAssistAgent |
