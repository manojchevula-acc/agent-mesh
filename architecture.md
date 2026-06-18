# Architectural Design — Distributed A2A Agent Mesh

This document describes the architecture of the Role-Aware Enterprise Assistant: a
distributed multi-agent mesh where each agent is an isolated A2A server and
agents communicate over the Agent-to-Agent (A2A) protocol.

---

## Architecture Diagram

```mermaid
graph TD
    User([👤 User]) --> Auth[Mock Auth<br/>role: employee/hr/leadership]
    Auth --> Run[run.py client]
    Run --> Orch[Mesh Orchestrator<br/>src/mesh/orchestrator.py]

    subgraph Guardrails (deterministic)
        Screen[Input screen<br/>injection / PII / destructive]
        Redact[Output redaction]
    end

    Orch --> Screen
    Screen -->|A2A :8010| Gateway[Gateway / Router]
    Orch --> AC{Role access<br/>control}
    Orch -->|A2A :8015| Compliance[Compliance Agent]

    subgraph Domain Nodes
        Finance[Finance :8011<br/>leadership-only]
        HR[HR :8012]
        Job[Internal Job :8013]
    end

    AC --> Finance
    AC --> HR
    AC --> Job

    Finance -->|@tool + approval| FTools[finance_tools]
    HR -->|@tool| HTools[hr_tools]
    Job -->|@tool| JTools[job_tools]
    Finance -.->|consult_policy A2A :8014| Policy[Policy Agent]
    HR -.->|consult_policy A2A :8014| Policy
    Job -.->|consult_policy A2A :8014| Policy

    JTools --> KB[(job_postings.json)]
    Policy --> PKB[(policies.json)]
    Job --> Redact
    HR --> Redact
    Finance --> Redact

    Gateway & Finance & HR & Job & Policy & Compliance -.->|AuditMiddleware| Log[(audit_trail.jsonl)]
```

---

## Component Topology

Six independent nodes, each hosted as an isolated A2A server (own process + port):

1. **Gateway / Router (8010)** — LLM classifier mapping a request to a domain. Does not answer.
2. **Finance Agent (8011)** — leadership-only; budgets, summaries, approval-gated payments.
3. **HR Agent (8012)** — leave, benefits, HR policies for all employees.
4. **Internal Job Agent (8013)** — searches internal postings from `job_postings.json`.
5. **Policy Agent (8014)** — shared advisor; loads rules from `policies.json`.
6. **Compliance Agent (8015)** — shared semantic guardrail (injection / leakage / harm).

Hosting uses `A2AExecutor` + Starlette/uvicorn (`src/a2a/hosting.py`); clients use
`A2AAgent` (`src/a2a/clients.py`). The orchestrator (`src/mesh/orchestrator.py`)
coordinates the hops; it is invoked by the `run.py` client.

---

## Data Flow & Execution Sequence

For a finance request from a leadership user ("What's the engineering budget?"):

1. **Login**: mock auth resolves the user to role `leadership`.
2. **Input screen**: deterministic regex gate passes (no injection/PII/destructive).
3. **Route (A2A → 8010)**: Gateway classifies → `finance`.
4. **Access control**: `finance` requires `leadership` → allowed.
5. **Compliance (A2A → 8015)**: semantic review → `COMPLIANCE_PASSED`.
6. **Finance (A2A → 8011)**: agent calls `get_budget_report` (tool) and answers; may
   call `consult_policy` (A2A → 8014) for applicable rules.
7. **Output redaction**: PII scrubbed; answer returned to the user.
8. **Audit**: every hop is recorded to `audit_trail.jsonl` by each node's middleware.

The same employee asking the finance question is **denied at step 4**. A prompt-injection
or destructive request is **blocked at step 2** before any agent is contacted.

---

## Security Model (Defense in Depth)

- **Layer 1 — Deterministic filters** (`src/guardrails/deterministic_filters.py`):
  hard regex gates that run before any LLM sees input and again on output.
- **Layer 2 — Compliance agent**: semantic safety review over A2A.
- **Access control**: role-gated domains from `policies.json`.
- **Approval gate**: native `approval_mode="always_require"` on outbound payments.
- **Observability**: per-hop audit logging with PII redaction.

---

## Microsoft Agent Framework Capabilities Demonstrated

1. **A2A protocol** — agents hosted and consumed across isolated ports.
2. **Tool calling** — `@tool` functions (MCP-ready).
3. **Native human-in-the-loop** — approval-gated payment tool.
4. **Agent middleware** — audit + redaction pipeline.
5. **Local LLM** — `OllamaChatClient`.
