# AgentMesh — Project Overview

**FAB AgentMesh** is an enterprise multi-agent AI platform built for **First Abu Dhabi Bank (FAB)**. Bank staff (relationship managers, compliance officers, credit officers, customers) query structured banking data and regulatory/policy knowledge through a single conversational interface, with full governance, security, and explainability baked in.

---

## What It Does

- Routes natural-language questions to the right specialist agent (structured data vs. policy/knowledge)
- Applies multi-layer security: deterministic filters → RBAC → LLM semantic compliance → output PII redaction
- Maintains multi-turn conversation memory with rolling LLM summarization
- Streams real-time pipeline stage updates to the UI via SSE
- Provides full observability: OTel distributed traces, business metrics, structured logs, audit trail
- Human-in-the-Loop (HITL) approval for sensitive roles (credit officers)

---

## Repository Layout

```
agent-mesh-15062026/
└── agent-mesh/
    ├── launch_mesh.py          # Spawns all 4 A2A agent nodes as subprocesses
    ├── a2a_server.py           # Hosts one agent node as an A2A HTTP server
    ├── api_server.py           # REST + SSE API for the React frontend (port 8000)
    ├── run.py                  # Interactive CLI REPL
    ├── devui_app.py            # Single-process DevUI entrypoint (port 8090)
    ├── src/
    │   ├── config.py           # All configuration (reads from .env)
    │   ├── agents/             # 4 agent definitions + factory + registry
    │   ├── a2a/                # A2A protocol clients & hosting
    │   ├── auth/               # Identity provider + RBAC permissions
    │   ├── feedback/           # Thumbs up/down store
    │   ├── guardrails/         # Deterministic input/output filters
    │   ├── hitl/               # Human-in-the-Loop approval store
    │   ├── integrations/       # MCP client factories
    │   ├── memory/             # Conversation memory (JSONL + Redis stub)
    │   ├── mesh/               # Workflow graph + orchestrator
    │   ├── middleware/         # Audit + tool-call logging middleware
    │   ├── observability/      # OTel setup, metrics, logging, baggage
    │   ├── tools/              # Cross-agent collaboration tools
    │   ├── tracing/            # Execution trace, LLM reasoning, CLI renderer
    │   └── utils/              # Console logger
    ├── frontend/               # React 18 + TypeScript + Vite + Tailwind
    └── workflow_evaluations/   # Evaluation suite (15 evaluators + benchmarks)
```

---

## Entry Points

| Command | File | Purpose |
|---|---|---|
| `python launch_mesh.py` | `launch_mesh.py` | Start all 4 A2A nodes + wait |
| `python a2a_server.py --agent <name> [--port N]` | `a2a_server.py` | Start one agent node |
| `python api_server.py` | `api_server.py` | Start REST/SSE API (port 8000) |
| `python run.py [--verbose] [-v] [--explain] [-e]` | `run.py` | Interactive CLI |
| `python devui_app.py` | `devui_app.py` | MAF DevUI (port 8090) |
| `python workflow_evaluations/run_evaluation.py --mode <mode>` | `run_evaluation.py` | Run evals |

**CLI flags (`run.py`):**
- `--verbose` / `-v` — show confidence scores, routing detail, timing
- `--explain` / `-e` — show alternative domain scores and rejection rationale

**Evaluation modes:** `ci` · `full` · `benchmarks` · `replay` · `single` · `demo` · `redteam`

---

## Agent Ports

| Agent | Port |
|---|---|
| ComplianceAgent | 8015 |
| DataAgent | 8016 |
| RAGAgent | 8017 |
| PriceAssistAgent | 8018 |

---

## Key Configuration (`src/config.py` + `.env`)

### LLM / API

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | (required) | LLM API key |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | LLM provider endpoint |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Default model |
| `COMPLIANCE_MODEL` | `openai/gpt-oss-20b` | ComplianceAgent model |
| `DATA_AGENT_MODEL` | `qwen/qwen3.6-27b` | DataAgent model |
| `RAG_AGENT_MODEL` | `qwen/qwen3.6-27b` | RAGAgent model |
| `PRICE_ASSIST_MODEL` | `openai/gpt-oss-120b` | PriceAssistAgent model |
| `*_API_KEY` (per-agent) | falls back to `GROQ_API_KEY` | Per-agent keys for rate-limit spreading |

### Feature Flags

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_PRICE_ASSIST` | `true` | When `false`, routes directly to DataAgent |
| `ENABLE_COMPLIANCE` | `true` | When `false`, bypasses compliance (stamps pass) |
| `ENABLE_CONVERSATION_MEMORY` | `true` | Enable multi-turn memory |
| `ENABLE_ROLLING_SUMMARIZATION` | `true` | LLM summarization after each turn |

### Memory

| Variable | Default | Purpose |
|---|---|---|
| `CONVERSATION_BACKEND` | `jsonl` | Storage backend: `jsonl` or `redis` |
| `CONVERSATION_MAX_TURNS` | `3` | Raw-history turns cap (legacy) |
| `SUMMARY_MODEL` | falls back to `GROQ_MODEL` | Model for rolling summarization |

### Observability

| Variable | Default | Purpose |
|---|---|---|
| `OBS_PROFILE` | `dev` | `dev` · `grafana` · `prod` · `off` |
| `GRAFANA_OTLP_ENDPOINT` | | Grafana Cloud OTLP endpoint |
| `GRAFANA_INSTANCE_ID` | | Grafana instance ID |
| `GRAFANA_API_TOKEN` | | Grafana API token |

### MCP Services

| Variable | Default | Purpose |
|---|---|---|
| `DATALAYER_MCP_URL` | `http://127.0.0.1:9100/mcp` | DataLayer-as-a-Service MCP URL |
| `RAG_MCP_URL` | `http://127.0.0.1:9000/mcp` | RAG-as-a-Service MCP URL |

### Data Files

| Variable | Default | Purpose |
|---|---|---|
| `FEEDBACK_LOG_FILE` | `data/feedback.jsonl` | Feedback store |
| `AUDIT_LOG_FILE` | `data/audit_trail.jsonl` | Audit trail |
| `LOG_FILE` | `data/logs/agent_mesh.log` | Application log |

---

## Technology Stack

| Layer | Technology |
|---|---|
| Agent runtime | Microsoft Agent Framework (MAF) `>=1.9.0` |
| Agent protocol | Google A2A (JSON-RPC/HTTP) |
| Tool protocol | MCP (Model Context Protocol) — StreamableHTTP transport |
| LLM provider | Groq (default) via OpenAI-compat API; swappable to Ollama, Azure AI Foundry, Anthropic |
| API server | Starlette + uvicorn |
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS + TanStack Query |
| Observability | OpenTelemetry (traces, metrics, logs) |
| Conversation memory | JSONL files (active) / Redis (stub) |
| Language | Python 3.13+ |

---

## External Services (not in this repo)

| Service | Port | What it provides |
|---|---|---|
| DataLayer-as-a-Service | 9100 | 18 SQL-view MCP tools over structured banking data |
| RAG-as-a-Service | 9000 | `search_documents` MCP tool over policy/regulatory documents |
| Grafana Cloud | — | OTel backend: Tempo (traces), Mimir (metrics), Loki (logs) |
| Azure Monitor | — | Production OTel backend |
