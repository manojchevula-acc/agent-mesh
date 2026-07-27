# Workflow Orchestration — MAF WorkflowBuilder

AgentMesh expresses the entire request pipeline as a typed **MAF WorkflowBuilder DAG**. A single `MeshState` dataclass flows through each executor node; executors either advance the state or terminate the workflow early.

---

## Files

| File | Purpose |
|---|---|
| `src/mesh/workflow.py` | WorkflowBuilder definitions + all executor classes |
| `src/mesh/orchestrator.py` | `run_mesh()` entry point — creates tracer, fires workflow, handles SSE streaming |

---

## MeshState — The Shared Data Object

```python
@dataclass
class MeshState:
    # Input
    query: str
    user: str
    role: str
    session_id: str
    request_id: str

    # RBAC (filled by RBACValidationExecutor)
    rbac_scope: dict | None

    # Compliance (filled by ComplianceExecutor)
    compliance_verdict: str | None        # "COMPLIANCE_PASSED" | "COMPLIANCE_FAILED"
    compliance_reasoning: str | None

    # Domain (filled by DomainExecutor)
    domain_answer: str | None
    route: str | None                     # "Data Layer Service" | "RAG Service" | "Hybrid"
    llm_reasoning: list[ReasoningEntry]

    # Output (filled by OutputRedactionExecutor)
    final_answer: str | None

    # Control
    blocked: bool
    block_reason: str | None
    hitl_pending: bool
```

---

## Full Pipeline — `build_mesh_workflow()`

```
InputGuardrailExecutor
    └─► RBACValidationExecutor
            └─► ComplianceExecutor
                    └─► DomainExecutor
                            └─► OutputRedactionExecutor
```

Each executor calls either:
- `ctx.send_message(state)` — advance to the next node
- `ctx.yield_output(state)` — terminate workflow early (blocked/error path)

---

## Executor Reference

### InputGuardrailExecutor
**Source:** `src/mesh/workflow.py`  
**Calls:** `src/guardrails/deterministic_filters.py:screen_input()`

Runs deterministic regex patterns before any LLM is called. On match → sets `state.blocked=True` and `ctx.yield_output`. No network calls.

---

### RBACValidationExecutor
**Source:** `src/mesh/workflow.py`  
**Calls:** `src/auth/role_permissions.py:ROLE_PERMISSIONS`

Resolves `state.role` → permission record → fills `state.rbac_scope`. If the task is explicitly denied for the role → blocks immediately.

---

### ComplianceExecutor
**Source:** `src/mesh/workflow.py`  
**Calls:** `src/a2a/clients.py:ask_remote()` → ComplianceAgent :8015

Key logic:
1. If role is `platform_administrator` or `operations_manager` → skip A2A, stamp passed
2. Otherwise → A2A call to ComplianceAgent with query + RBAC scope
3. Parse verdict from response
4. If `COMPLIANCE_FAILED` → `ctx.yield_output` with blocked message
5. If `credit_officer` and passed → trigger HITL (see [features/hitl.md](../features/hitl.md))

Also extracts `<llm_reasoning>` blocks from the compliance response and stores them in `state.llm_reasoning`.

---

### DomainExecutor
**Source:** `src/mesh/workflow.py`  
**Calls:** `src/a2a/clients.py:ask_remote()` → PriceAssistAgent :8018

Key logic:
1. Load rolling conversation summary from `ConversationStore.load_with_summary(session_id)`
2. Inject `[Conversation Summary]` block into PriceAssistAgent prompt (if summary exists)
3. A2A call to PriceAssistAgent
4. **Retry loop** (up to 2 retries) — detects and retries on three anti-patterns:
   - **Tool-call echo**: answer is the raw tool invocation text
   - **Meta-response**: answer contains "I have retrieved" / "Based on the data" without actual content
   - **Bracket placeholder**: answer contains `[RESULT]`, `[DATA]`, `[ANSWER]` template text
5. Parse `route` from answer (Data Layer / RAG / Hybrid) via `infer_route_and_scores()`
6. Extract all `<llm_reasoning>` blocks from PriceAssist + peer agent responses
7. Fire async `summarize_and_persist()` (non-blocking) — rolling LLM summarization for next turn

---

### OutputRedactionExecutor
**Source:** `src/mesh/workflow.py`  
**Calls:** `src/guardrails/deterministic_filters.py:redact_pii()`

Applies regex-based PII redaction to `state.domain_answer` → produces `state.final_answer`.  
Replacements: `[REDACTED_EMAIL]`, `[REDACTED_SSN]`, `[REDACTED_CC]`, `[REDACTED_PHONE]`

---

## Workflow Variants

### `build_hitl_resume_workflow()`
Used after a credit officer's request is approved by a human reviewer. Skips guardrails and compliance (already passed), runs only:
```
DomainExecutor → OutputRedactionExecutor
```

### `build_devui_workflow()`
Used by `devui_app.py` for the MAF DevUI tool. Prepends a `DevUIEntryExecutor` that adapts the plain-string DevUI input format into a `MeshState`:
```
DevUIEntryExecutor → InputGuardrailExecutor → RBACValidationExecutor → ComplianceExecutor → DomainExecutor → OutputRedactionExecutor
```

---

## OTel Spans

MAF's `WorkflowBuilder` emits native OTel spans automatically:
- `workflow.run` — root span for the entire workflow
- `executor.process` — child span per executor

These join the distributed trace that spans all A2A hops (ComplianceAgent, PriceAssistAgent, DataAgent, RAGAgent) via W3C trace context propagation.

---

## Orchestrator (`src/mesh/orchestrator.py`)

`run_mesh(query, user, role, session_id)` is the public entry point:

1. Generates `request_id` (UUID)
2. Creates `ExecutionTracer` and registers CLI/API listeners
3. Sets W3C baggage (`fab.request_id`, `fab.user`, `fab.role`, `fab.session_id`)
4. Calls `build_mesh_workflow().run(state)`
5. Collects `ExecutionSummary` and returns `MeshResult`
6. For HITL scenarios: pauses on `hitl_pending`, awaits `ApprovalStore`, then re-runs `build_hitl_resume_workflow()`

`MeshResult` contains: `answer`, `route`, `request_id`, `trace_id`, `session_id`, `events` (execution trace), `llm_reasoning` (all reasoning entries).
