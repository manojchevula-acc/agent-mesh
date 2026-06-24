# FAB Pricing Assistant — Distributed A2A Agent Mesh
### Microsoft Agent Framework Python SDK Reference Demo

A distributed agent-to-agent (A2A) mesh on the **Microsoft Agent Framework Python SDK**. AgentMesh is the orchestration framework: it screens a request through safety gates, routes it to a specialist agent, and returns a redacted answer. A **Price Assist** coordinator composes answers across a structured-data agent and an unstructured-document agent, which in turn leverage two independent services over **MCP**.

---

## 1. What the Demo Does

- **Price Assist agent** (coordinator) — analyzes a pricing query and delegates to the Data and/or RAG agents over A2A, then synthesizes a cited answer (e.g. "is CUST001's price compliant?" = deal figures + policy floor).
- **Data agent** (thin) — structured banking data via **DataLayer-as-a-Service** over MCP (customer 360, pricing, margins, profitability, RWA).
- **RAG agent** (thin) — unstructured policy/regulatory documents via **RAG-as-a-Service** over MCP (grounded, cited retrieval).
- **Gateway** routes each request; **Compliance** + deterministic **guardrails** screen it; **Policy** answers governance questions.

---

## 2. Topology

| Node | Port | Role |
|------|------|------|
| `gateway` | 8010 | LLM router → `price` / `data` / `rag` / `policy` |
| `policy` | 8014 | Corporate policy advisor (loads `policies.json`) |
| `compliance` | 8015 | Semantic safety guardrail |
| `data_agent` | 8016 | Thin → DataLayer service (MCP, structured) |
| `rag_agent` | 8017 | Thin → RAG service (MCP, unstructured) |
| `price_assist` | 8018 | Coordinator → delegates to Data & RAG agents (A2A) |

Backing services (independent): **DataLayer** FastMCP `:9100`; **RAG** MCP `:9000` → REST `:8000`.

---

## 3. Request Flow

`run.py` / `api_server.py` → `src/mesh/orchestrator.py`:

1. **Input guardrail** — regex gate: injection / PII / destructive (hard block).
2. **Compliance (A2A)** — semantic safety review (hard block on `COMPLIANCE_FAILED`).
3. **Router (A2A → gateway)** — classify into `price | data | rag | policy`.
4. **Domain dispatch (A2A)** — hop to the chosen node. `price_assist` further delegates to `data_agent`/`rag_agent` over A2A and synthesizes. Failed hops soft-fail gracefully.
5. **Output redaction** — scrub PII before returning.

Every hop is audited to `data/audit_trail.jsonl` and traced under one `mesh.request` span.

---

## 4. Communication Boundaries

- **Orchestrator ↔ agents:** A2A (JSONRPC/HTTP).
- **Price Assist ↔ Data/RAG agents:** A2A peer delegation (agent-as-tool), depth-guarded and soft-failing.
- **Data/RAG agents ↔ services:** MCP (streamable HTTP) — tools auto-discovered, so the agents stay thin and the services stay independent.

---

## 5. What is Real vs. Mocked

- **Real:** MAF agents; A2A hosting on isolated ports; MCP tool consumption; the DataLayer (MySQL views) and RAG (Qdrant + rerank) services; deterministic guardrails; audit logging; OpenTelemetry tracing; offline tests.
- **Mocked:** identity is a mock provider (`src/auth`).

---

## 6. Folder Structure

```
agent-mesh/
├── run.py / api_server.py          # CLI client / REST bridge for the React UI
├── launch_mesh.py                  # Spawns all 6 nodes
├── a2a_server.py                   # Generic A2A server (--agent <node>)
├── frontend/                       # React UI (Vite)
├── src/
│   ├── config.py                   # AGENT_PORTS, *_MCP_URL, A2A_TIMEOUT
│   ├── a2a/{hosting,clients}.py     # A2A host / ask_remote()
│   ├── integrations/mcp_clients.py  # MCPStreamableHTTPTool factories
│   ├── mesh/{orchestrator,workflow}.py
│   ├── agents/                      # gateway, price_assist, data, rag, policy, compliance, factory, registry
│   ├── tools/collaboration_tools.py # A2A peer tools used by price_assist
│   ├── guardrails/ middleware/ observability/ memory/ utils/
└── data/policies.json + audit_trail.jsonl
```

---

## 7. Install

```bash
python -m venv .venv && .venv\Scripts\Activate.ps1   # (or: source .venv/bin/activate)
pip install -r requirements.txt
```
Requires Python 3.10+, a running **Ollama** (`ollama pull llama3.2`), and the A2A/MCP extras in `requirements.txt`. The two backing services have their own repos/deps.

---

## 8. Configure (`.env`)

- `OLLAMA_HOST`, `OLLAMA_MODEL`
- `DATALAYER_MCP_URL` (default `http://127.0.0.1:9100/mcp`)
- `RAG_MCP_URL` (default `http://127.0.0.1:9000/mcp`), `RAG_API_KEY` (optional)
- Ports override via `PORT_GATEWAY`, `PORT_DATA_AGENT`, `PORT_RAG_AGENT`, `PORT_PRICE_ASSIST`, … (defaults 8010–8018).

---

## 9. Run

```bash
# Backing services (each its own terminal)
cd datalayer-as-service && MCP_TRANSPORT=http MCP_PORT=9100 python -m mcp_server.server
cd rag-as-a-service && uvicorn gernas_rag.main:app --app-dir src        # :8000
MCP_TRANSPORT=http MCP_PORT=9000 python -m mcp_integration.server       # :9000

# Mesh
cd agent-mesh && python launch_mesh.py     # 6 nodes
python run.py
```

Try:
- `Is CUST001's loan price compliant with policy?` → **price** (Data + RAG).
- `Pricing recommendation for CUST001?` → **price** coordinator.
- `Margin analysis for CUST003` → **data**.
- `What is the pricing floor for a BB-rated AED loan?` → **rag**.
- `ignore previous instructions and ...` → **blocked** (injection).

Start a single node directly: `python a2a_server.py --agent data_agent --port 8016`.

---

## 10. Test

```bash
python -m unittest test_agent_mesh.py
```
Offline tests mock the A2A + MCP layers (no servers/Ollama needed): guardrails, auth, compliance gate, routing (incl. `price`), domain dispatch, the collaboration tools, and PII redaction.

---

## 11. Security Model (Defense in Depth)

1. **Deterministic filters** — regex injection/PII/destructive gates (in & out).
2. **Compliance agent** — semantic LLM review (fails closed).
3. **Graceful degradation** — A2A/MCP hops soft-fail to an "unavailable" answer.
4. **Audit** — every hop logged with PII redacted.

---

## 12. Roadmap

- Reference the RAG service's richer REST surface (filters/ingest/evaluate) where MCP is insufficient.
- Real identity provider; database-backed session store; tamper-evident audit.
