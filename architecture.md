# Architectural Design — Distributed A2A Agent Mesh

The FAB Pricing Assistant Mesh: a distributed multi-agent system where each agent
is an isolated A2A server. AgentMesh orchestrates a request through safety gates
and a gateway router to one specialist; a **Price Assist** coordinator composes
answers across a structured-data agent and an unstructured-document agent, which
consume two independent services over **MCP**.

---

## Architecture Diagram

```mermaid
graph TD
    User([👤 User]) --> Auth[Mock Auth<br/>role: employee/hr/leadership]
    Auth --> Run[run.py / api_server.py]
    Run --> Orch[Orchestrator<br/>src/mesh/orchestrator.py]

    subgraph Guardrails (deterministic)
        Screen[Input screen<br/>injection / PII / destructive]
        Redact[Output redaction]
    end

    Orch --> Screen
    Screen -->|A2A :8015| Compliance[Compliance Agent]
    Compliance -->|A2A :8010| Gateway[Gateway / Router]

    Gateway -->|price| Price[Price Assist :8018]
    Gateway -->|data| Data[Data Agent :8016]
    Gateway -->|rag| Rag[RAG Agent :8017]
    Gateway -->|policy| Policy[Policy Agent :8014]

    Price -. A2A .-> Data
    Price -. A2A .-> Rag
    Data -. MCP/HTTP .-> DL[(DataLayer :9100<br/>MySQL semantic views)]
    Rag -. MCP/HTTP .-> RG[(RAG :9000 → :8000<br/>Qdrant + rerank)]
    Policy --> PKB[(policies.json)]

    Price --> Redact
    Data --> Redact
    Rag --> Redact
    Policy --> Redact

    Gateway & Price & Data & Rag & Policy & Compliance -.->|AuditMiddleware| Log[(audit_trail.jsonl)]
```

---

## Component Topology

Six A2A nodes (own process + port):

1. **Gateway / Router (8010)** — LLM classifier → `price | data | rag | policy`. Does not answer.
2. **Compliance Agent (8015)** — semantic safety gate.
3. **Policy Agent (8014)** — corporate rules from `policies.json`.
4. **Data Agent (8016)** — thin; consumes DataLayer over MCP (structured).
5. **RAG Agent (8017)** — thin; consumes RAG over MCP (unstructured).
6. **Price Assist (8018)** — coordinator; delegates to Data & RAG over A2A and synthesizes.

Backing services run independently: **DataLayer** FastMCP `:9100`, **RAG** MCP `:9000` → REST `:8000`.

Hosting: `A2AExecutor` + Starlette/uvicorn (`src/a2a/hosting.py`); MCP-backed nodes open their MCP session for the node lifetime (`a2a_server.py`). Calls use `A2AAgent` (`src/a2a/clients.py`).

---

## Communication Boundaries

| Boundary | Mechanism |
|----------|-----------|
| Orchestrator ↔ agents | A2A (JSONRPC/HTTP) |
| Price Assist ↔ Data/RAG agents | A2A peer delegation (agent-as-tool; depth-guarded, soft-fail) |
| Data/RAG agents ↔ services | MCP (streamable HTTP; tools auto-discovered) |

---

## Data Flow & Execution Sequence

For "Is CUST001's loan price compliant with policy?":

1. **Login** → role recorded for audit.
2. **Input screen** → passes (no injection/PII/destructive).
3. **Compliance (A2A → 8015)** → `COMPLIANCE_PASSED`.
4. **Router (A2A → 8010)** → domain `price` → node `price_assist`.
5. **Price Assist (A2A → 8018)** → calls `query_structured_data` (A2A → data_agent → DataLayer: approved price/margin) **and** `query_policy_documents` (A2A → rag_agent → RAG: pricing floor), compares, answers.
6. **Output redaction** → PII scrubbed; answer returned.
7. **Audit** → each hop recorded.

A prompt-injection/destructive request is blocked at step 2; an unsafe request at step 3. If a downstream agent/service is offline, the domain hop **soft-fails** to an "unavailable" answer instead of crashing.

---

## Security Model (Defense in Depth)

- **Layer 1 — Deterministic filters**: regex gates before any LLM and again on output.
- **Layer 2 — Compliance agent**: semantic review over A2A (fails closed).
- **Layer 3 — Graceful degradation**: A2A/MCP hops soft-fail; depth guard bounds peer delegation.
- **Observability**: per-hop audit + one distributed trace spanning A2A and MCP hops.

---

## Microsoft Agent Framework Capabilities Demonstrated

1. **A2A protocol** — agents hosted/consumed across isolated ports.
2. **MCP tool consumption** — `MCPStreamableHTTPTool` auto-discovers external service tools.
3. **Agent-as-tool** — the Price Assist coordinator calls peer agents as tools.
4. **Workflow orchestration** — typed `WorkflowBuilder` pipeline with native spans.
5. **Agent middleware** — audit + redaction.
6. **Local LLM** — `OllamaChatClient`.
