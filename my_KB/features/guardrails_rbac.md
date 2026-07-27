# Guardrails & RBAC — Security Model

AgentMesh uses **defense-in-depth**: three security layers applied in sequence before any domain answer is generated.

```
Layer 1: Deterministic Guardrails    ← regex, no LLM, no network
Layer 2: RBAC Validation             ← role permissions lookup
Layer 3: LLM Semantic Compliance     ← ComplianceAgent A2A call
Layer 4: Output PII Redaction        ← regex on final answer
```

---

## Layer 1 — Deterministic Input Guardrails

**File:** `src/guardrails/deterministic_filters.py:screen_input()`

Runs **before any LLM call**. Zero latency impact.

**Checks:**
| Category | Example patterns |
|---|---|
| Prompt injection | `ignore previous instructions`, `jailbreak`, `act as`, `forget your instructions` |
| PII in input | email addresses, SSN format (`\d{3}-\d{2}-\d{4}`), credit card (`\d{4}[\s-]\d{4}...`), phone numbers |
| Destructive intent | `delete`, `drop table`, `rm -rf`, `format c:`, `truncate`, `wipe` |

If any pattern matches → workflow terminates immediately, returns error to user, no further processing.

**Output PII Redaction** (`redact_pii()`):  
Applied by `OutputRedactionExecutor` on the final answer before it reaches the user:
- `[REDACTED_EMAIL]`
- `[REDACTED_SSN]`
- `[REDACTED_CC]`
- `[REDACTED_PHONE]`

---

## Layer 2 — RBAC (Role-Based Access Control)

**Files:** `src/auth/identity_provider.py`, `src/auth/role_permissions.py`

### Banking Roles

| Role | Scope |
|---|---|
| `customer` | Own account data + public banking knowledge only |
| `relationship_manager` | Customer portfolio, pricing tools, products |
| `branch_operations_officer` | Branch operations, service requests, policy |
| `credit_officer` | Credit products, loan workflows, customer risk data |
| `compliance_officer` | Policy documents, compliance reports, audit, regulatory |
| `operations_manager` | **Full access** — no restrictions |
| `platform_administrator` | **Full access** — no restrictions |

### How RBAC works

`src/auth/role_permissions.py:ROLE_PERMISSIONS` maps each role to:
```python
{
    "allowed_tasks": [...],   # whitelist of permitted task categories
    "denied_tasks": [...],    # hard-blocked task categories
    "scope": "..."            # scope description injected into ComplianceAgent prompt
}
```

`RBACValidationExecutor` in the workflow:
1. Resolves the user's role from `src/auth/identity_provider.py` (mock FAB corporate directory)
2. Looks up `ROLE_PERMISSIONS[role]`
3. If the query task matches `denied_tasks` → workflow terminates with 403-style message
4. Otherwise → injects `allowed_tasks` + `scope` into `MeshState.rbac_scope`

The resolved scope is passed to ComplianceAgent as part of its context, so the LLM safety check also enforces role boundaries.

### HITL for Credit Officers

`credit_officer` is a special role — even after compliance passes, their requests require human approval before domain execution. See [hitl.md](hitl.md).

---

## Layer 3 — LLM Semantic Compliance

**File:** `src/agents/compliance_agent.py`  
**Invoked via:** `ComplianceExecutor` → A2A → ComplianceAgent :8015

### 7-Category Safety Check

The LLM reviews the query against all 7 categories simultaneously:

1. **Prompt injection / jailbreak** — attempts to override system instructions
2. **PII exfiltration** — requests to reveal/export personal data
3. **Destructive commands** — instructions that could damage data or systems
4. **Social engineering** — manipulation attempts to escalate access
5. **Context poisoning** — injecting false context to alter behavior
6. **Scope violation** — request outside the user's RBAC-defined scope
7. **Authorization** — is this role permitted for this specific task?

### Verdict Format

```
COMPLIANCE_PASSED
<llm_reasoning>{"phase":"safety_review","checks":[...],"risk_signals":[],"authorization":"approved"}</llm_reasoning>
```

or

```
COMPLIANCE_FAILED: <specific reason>
<llm_reasoning>{"phase":"safety_review","checks":[...],"risk_signals":["..."],"authorization":"denied"}</llm_reasoning>
```

### Fast-path for Admin Roles

`platform_administrator` and `operations_manager` **bypass the A2A call entirely** — `ComplianceExecutor` stamps `COMPLIANCE_PASSED` directly. This avoids latency for trusted admin operations.

---

## Summary — What Each Layer Catches

| Threat | Layer that catches it |
|---|---|
| Known injection patterns | Layer 1 (deterministic) |
| PII in user input | Layer 1 (deterministic) |
| Destructive commands | Layer 1 (deterministic) |
| Task outside user's role | Layer 2 (RBAC) |
| Novel jailbreak attempts | Layer 3 (LLM) |
| Social engineering | Layer 3 (LLM) |
| Context poisoning | Layer 3 (LLM) |
| PII leaked in LLM output | Layer 4 (output redaction) |
