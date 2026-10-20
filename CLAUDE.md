# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AgentMesh 15.0.6.2026** — a Microsoft Agent Framework (MAF) Python SDK reference demo for First Abu Dhabi Bank (FAB). It is a distributed AI agent mesh for banking pricing queries, organized as a monorepo with three independently deployable services plus a React frontend.

Python 3.13+ is required. On Windows, keep the venv at a short path to avoid MAX_PATH (260-char) issues.

---

## Install

```powershell
# Short path to avoid Windows MAX_PATH limit
py -3.13 -m venv C:\fab-venv
C:\fab-venv\Scripts\pip install --pre -r requirements.txt
# --pre is required because agent-framework-* packages are pre-release builds
```

---

## Running the Full Stack

### 1. Backing Services (each in its own terminal)

```powershell
# DataLayer MCP server
cd datalayer-as-service
$env:MCP_TRANSPORT="http"; $env:MCP_PORT="9100"; python -m mcp_server.server

# RAG REST API
cd rag-as-a-service
uvicorn gernas_rag.main:app --app-dir src   # :8000

# RAG MCP server
$env:MCP_TRANSPORT="http"; $env:MCP_PORT="9000"; python -m mcp_integration.server
```

### 2. Agent Mesh

```powershell
cd agent-mesh
python launch_mesh.py          # starts all 4 A2A nodes

# In a second terminal — pick one client:
python run.py                  # CLI client
python api_server.py           # REST + SSE bridge for React UI (:8000)
python devui_app.py            # single-process DevUI, no separate launch needed (:8090)
```

Start a single node directly:
```powershell
python a2a_server.py --agent data_agent --port 8016
```

### 3. React Frontend

```powershell
cd agent-mesh/frontend
npm install
npm run dev        # Vite dev server, proxies /api → :8000
npm run build
npm run lint
npm run typecheck
```

### One-time DataLayer Setup

```powershell
cd datalayer-as-service
python 01_validate_raw_data.py
python 02_create_curated_data.py
python 03_load_curated_to_mysql.py
python 04_create_semantic_views.py
```

### One-time RAG Ingestion

```powershell
cd rag-as-a-service
python scripts/ingest_docs.py
```

---

## Tests

```powershell
# Main offline suite — no live servers or LLM needed (mocks A2A + MCP)
cd agent-mesh
python -m pytest tests/ -v

# Single file
python -m pytest tests/test_cache.py -v
python -m pytest tests/test_memory.py -v
python -m pytest tests/test_rbac_matrix.py -v
python -m pytest tests/test_cache_phases.py -v
python -m pytest tests/test_config.py -v

# RAG service tests
cd rag-as-a-service
python -m pytest tests/ -v
```

The main test suite patches `ask_remote` (the A2A call boundary) and covers guardrails, auth/roles, compliance gate, routing, collaboration tools, and PII redaction. No real Groq/Qdrant/MySQL connection is needed to run it.

---

## Architecture

### Three Independent Services

| Service | Directory | What it does |
|---------|-----------|--------------|
| **Agent Mesh** | `agent-mesh/` | MAF orchestration: 4 A2A agent nodes + API server + React UI |
| **DataLayer-as-a-Service** | `datalayer-as-service/` | Structured banking data via FastMCP → MySQL semantic views |
| **RAG-as-a-Service** | `rag-as-a-service/` | Hybrid RAG retrieval via FastAPI + Qdrant + BGE-M3 embeddings |

### Agent Mesh Topology

| Agent | Port | Role |
|-------|------|------|
| `compliance` | 8015 | LLM semantic safety guardrail — hard gate, fails closed |
| `data_agent` | 8016 | Thin MCP client → DataLayer (:9100) |
| `rag_agent` | 8017 | Thin MCP client → RAG MCP server (:9000) |
| `price_assist` | 8018 | Primary banking orchestrator; delegates to DataAgent + RAGAgent via peer A2A |

### Request Pipeline (`src/mesh/workflow.py`)

Every request flows through a typed `WorkflowBuilder` graph. State is `MeshState`; each stage is an `Executor` that either continues (`ctx.send_message`) or terminates early (`ctx.yield_output`):

```
User input
  → InputGuardrailExecutor    — regex hard-block: injection / PII / destructive patterns
  → RBACValidationExecutor    — 7 FAB banking roles; unrecognized role → block
  → CacheCheckExecutor        — semantic ChromaDB cache (off by default, ENABLE_RESPONSE_CACHE)
  → ComplianceExecutor ──A2A──▶ ComplianceAgent (:8015)
                                  LLM semantic safety + per-role authorization
                                  Bypassed for: platform_administrator, operations_manager
                                  HITL pause for: credit_officer
  → DomainExecutor ────A2A───▶ PriceAssistAgent (:8018)
                                  Classifies intent: data / knowledge / hybrid
                                  Delegates via @tool functions in collaboration_tools.py:
                                    query_structured_data ──A2A──▶ DataAgent ──MCP──▶ DataLayer
                                    query_knowledge_base  ──A2A──▶ RAGAgent  ──MCP──▶ RAG
  → OutputRedactionExecutor   — regex PII scrub on the final answer
```

Every hop writes to `data/audit_trail.jsonl` and emits OpenTelemetry spans with W3C `traceparent` propagated across A2A boundaries.

### Communication Boundaries

| Boundary | Protocol |
|----------|----------|
| Orchestrator ↔ ComplianceAgent / PriceAssistAgent | A2A (JSONRPC over HTTP) |
| PriceAssistAgent ↔ DataAgent / RAGAgent | A2A peer delegation (`agent-as-tool` pattern) |
| DataAgent ↔ DataLayer-as-a-Service | MCP streamable HTTP |
| RAGAgent ↔ RAG-as-a-Service | MCP streamable HTTP |
| API server ↔ React UI | REST + SSE for streaming |

### Key Source Files

- `agent-mesh/src/config.py` — all env vars in one place
- `agent-mesh/src/mesh/orchestrator.py` — `handle_request()`, `MeshResult`
- `agent-mesh/src/mesh/workflow.py` — `MeshState`, all 6 Executor classes, `build_mesh_workflow()`
- `agent-mesh/src/agents/price_assist_agent.py` — primary banking orchestrator
- `agent-mesh/src/agents/compliance_agent.py` — semantic safety agent
- `agent-mesh/src/agents/data_agent.py` — thin MCP client (18 SQL view tools)
- `agent-mesh/src/agents/rag_agent.py` — thin MCP client (search_documents tool)
- `agent-mesh/src/agents/agent_factory.py` — `create_demo_agent()` factory (Groq client + middleware)
- `agent-mesh/src/agents/node_registry.py` — node name → builder registry
- `agent-mesh/src/tools/collaboration_tools.py` — `query_structured_data` / `query_knowledge_base` (patched in offline tests)
- `agent-mesh/src/a2a/clients.py` — `ask_remote()`, `get_remote_agent()`
- `agent-mesh/src/auth/identity_provider.py` — `BankingRole` enum, mock user directory, `login()`
- `agent-mesh/src/auth/role_permissions.py` — `ROLE_PERMISSIONS` matrix
- `agent-mesh/src/guardrails/deterministic_filters.py` — regex injection/PII/destructive gates
- `agent-mesh/src/cache/semantic_cache.py` — ChromaDB semantic cache
- `agent-mesh/src/hitl/approval_store.py` — `asyncio.Event`-based HITL approval gate
- `agent-mesh/src/memory/conversation_store.py` — JSONL conversation persistence
- `agent-mesh/src/tracing/llm_reasoning.py` — `extract_reasoning()` / `strip_reasoning_markers()`
- `gernas-sdk/src/gernas/__init__.py` — public API of the Gernas SDK (telemetry); `agent-mesh/src/observability/gernas_telemetry.py` — the mesh's single integration point

### Design Patterns to Know

**Defense-in-depth security (3 layers):** Deterministic regex (layer 1) → LLM semantic compliance via ComplianceAgent (layer 2) → per-role prompt scope in PriceAssistAgent (layer 3). ComplianceAgent always fails closed.

**Thin agent + external service:** `DataAgent` and `RAGAgent` hold zero business logic; they auto-discover tools from their MCP servers. Adding a new SQL view in DataLayer automatically exposes a new tool.

**Agent-as-tool peer delegation:** `PriceAssistAgent` calls `@tool`-decorated async functions in `collaboration_tools.py`. These call `ask_remote()` under the hood — the seam that offline tests patch.

**Semantic response cache:** Post-RBAC ChromaDB cache using `all-MiniLM-L6-v2`. Multi-phase: dense retrieval → entity gate → cross-encoder rerank → LLM judge (gray zone). Disabled by default.

**HITL approval gate:** `credit_officer` role triggers an `asyncio.Event`-based pause after compliance passes. Checkpoints at `data/checkpoints/{id}.json` for crash recovery.

**Rolling conversation memory:** Per-session JSONL. A ≤200-word summary of prior turns is injected into the PriceAssistAgent prompt. Summarization runs as a background `asyncio.create_task()` to avoid adding latency.

**LLM reasoning transparency:** Agents emit `<llm_reasoning>{...}</llm_reasoning>` blocks inline. `extract_reasoning()` captures these into structured trace entries for the React UI's "AI Reasoning" tab, then `strip_reasoning_markers()` cleans the user-visible answer. Cross-process reasoning is collected via a per-request temp file `data/logs/.peer_{REQUEST_ID}.json`.

**Retry + graceful degradation:** `DomainExecutor` detects tool-call echo, meta-response, and hallucinated placeholders via regex and retries once with a corrective prompt. Failed A2A/MCP hops soft-fail to an "unavailable" string rather than crashing the workflow.

**Gernas SDK telemetry (`gernas-sdk/`, evolved from Beacon Kit):** `pip install -e ./gernas-sdk`; one `init_gernas()` call per process (via `gernas_telemetry.init_telemetry`), configured only by `GERNAS_*` env vars (mandatory identity is validated, never invented). Telemetry goes SDK → OTel Collector (AKS gateway; locally `python gernas-sdk/scripts/run_collector.py`) → Azure Data Explorer (data plane) + Opik. Blueprint-named attributes (`gernas.<field>`); request → `use_case.request` → `agent.run` (mesh orchestrator) → `agent.step` per workflow executor → A2A / MAF `agent.run` per node → `llm.call` / `tool.call` (MCP = `tool_type=mcp`). See `gernas-sdk/docs/user-guide/GERNAS_SDK_INSTRUMENTATION_GUIDE.md`.

---

## Environment Variables

All loaded from `agent-mesh/.env`. Key variables:

### LLM

| Variable | Default | Purpose |
|----------|---------|---------|
| `GROQ_API_KEY` | *(required)* | API key for Groq (or any OpenAI-compatible endpoint) |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Default model |
| `COMPLIANCE_MODEL` | `openai/gpt-oss-20b` | ComplianceAgent model |
| `DATA_AGENT_MODEL` | `qwen/qwen3.8-27b` | DataAgent model |
| `RAG_AGENT_MODEL` | `qwen/qwen3.8-27b` | RAGAgent model |
| `PRICE_ASSIST_MODEL` | `openai/gpt-oss-120b` | PriceAssistAgent model |
| `*_API_KEY` | falls back to `GROQ_API_KEY` | Per-agent keys for spreading rate limits |

### External Services

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATALAYER_MCP_URL` | `http://127.0.0.1:9100/mcp` | DataLayer MCP endpoint |
| `RAG_MCP_URL` | `http://127.0.0.1:9000/mcp` | RAG MCP endpoint |
| `A2A_TIMEOUT` | `60.0` | A2A call timeout (seconds) |
| `MCP_REQUEST_TIMEOUT` | `30` | MCP call timeout (seconds) |

### Feature Flags

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_PRICE_ASSIST` | `true` | `false` = route directly to DataAgent, skip PriceAssist |
| `ENABLE_COMPLIANCE` | `true` | `false` = stamp pass, skip ComplianceAgent A2A call |
| `ENABLE_RESPONSE_CACHE` | `false` | Semantic ChromaDB cache |
| `ENABLE_CONVERSATION_MEMORY` | `true` | Per-session JSONL conversation store |
| `ENABLE_ROLLING_SUMMARIZATION` | `true` | Background turn summarization |

### Observability

| Variable | Default | Purpose |
|----------|---------|---------|
| `GERNAS_OTLP_ENDPOINT` | — | OTel Collector endpoint, e.g. `http://127.0.0.1:4317` |
| `GERNAS_USE_CASE_ID` / `GERNAS_APPLICATION_ID` / `GERNAS_BUSINESS_DOMAIN` / `GERNAS_ENVIRONMENT` | *(required; missing = telemetry off with a warning)* | Blueprint business / infrastructure context on every span |
| `GERNAS_USER_TOKEN_KEY` | *(unset = user identity not recorded)* | HMAC key for `tokenized_user_id` |
| `LLM_PROVIDER` | `groq` | Provider label on `llm.call` spans |
| `ENABLE_INSTRUMENTATION` / `ENABLE_SENSITIVE_DATA` / `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` | `false` | Framework-native (MAF / a2a-sdk) tracing off: the Gernas SDK is the only instrumentation path |
| `ADX_CLUSTER_URI` / `ADX_DATABASE` | — | Read by the Collector only: data plane in Azure Data Explorer (without them the local Collector writes `gernas-sdk/collector/out/`) |
| `OPIK_API_KEY` / `OPIK_WORKSPACE` / `OPIK_PROJECT_NAME` | — | Read only by the Collector's Opik exporter |
| `LOG_LEVEL` | `INFO` | Log verbosity |
| `LOG_JSON` | `false` | Structured JSON logging |

### DataLayer MySQL (`datalayer-as-service/.env`)

`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`

### RAG Service (`rag-as-a-service/.env` or `RAG__` prefix)

`RAG__VECTORDB__QDRANT_URL`, `RAG__REDIS_URL`, `RAG__LLM__PROVIDER`, `RAG__LLM__MODEL_NAME`, `RAG__EMBEDDING__MODEL_NAME`

---

## Banking Role Model

Identity is mocked (username → role). Real SSO is not implemented.

| Username | Role | Notes |
|----------|------|-------|
| `alice` | `relationship_manager` | Customer portfolio, pricing, products |
| `bob` | `credit_officer` | Credit products, risk docs; **HITL-gated** |
| `carol` | `compliance_officer` | Regulatory docs, audit, compliance reports |
| `dave` | `branch_operations_officer` | Operational procedures, service requests |
| `eve` | `operations_manager` | Full access; no HITL, compliance bypassed |
| `farida` | `platform_administrator` | Full access; no HITL, compliance bypassed |
| `cust001` | `customer` | Own account only |
| *(unknown)* | `customer` | Most restrictive (default) |

---

## Tech Stack

- **Agent framework:** `agent-framework` (MAF SDK), `agent-framework-a2a`, `a2a-sdk`
- **MCP:** `mcp[cli]` (client-side), `fastmcp` (DataLayer server)
- **LLM clients:** `agent-framework-openai` / Groq endpoint; `agent-framework-anthropic`, `agent-framework-ollama` also available
- **RAG:** Qdrant + BGE-M3 / FlagEmbedding, Redis cache, RAGAS evaluation
- **Semantic cache:** ChromaDB + `sentence-transformers` (all-MiniLM-L6-v2) + cross-encoder rerank
- **Database:** MySQL via PyMySQL + SQLAlchemy
- **Observability:** OpenTelemetry SDK + Gernas SDK (`gernas-telemetry`), OTel Collector (`otelcol-contrib`), Opik, `structlog`, `rich`
- **API servers:** Starlette (agent-mesh), FastAPI (RAG service)
- **Frontend:** Vite + React 18 + TailwindCSS + TanStack Query
- **Testing:** `pytest` + `pytest-asyncio`, `unittest`
