# GERNAS Architecture — Implementation Status Analysis
**Project:** Agent Mesh 15.0.6.2026 (FAB Banking Agent Platform)  
**Date:** 2026-07-01  
**Branch:** maf-thread-memory-jsonl-persistence

---

## Full Capability Mapping

| Platform Scope | GERNAS Architecture Anchor | Capability | Status | What Is Implemented | What Is Missing / Gaps |
|---|---|---|---|---|---|
| Orchestrator | Agentic Mesh Layer – Orchestrator | Workflow Orchestration Engine | Implemented | `src/mesh/orchestrator.py` + `workflow.py` — `handle_request()` drives a 6-stage sequential pipeline (Guardrail → RBAC → Compliance → Domain → Redact) via Microsoft Agent Framework; `MeshState` dataclass flows through all executors | No visual workflow designer; workflow is code-only; no YAML/declarative definition |
| Orchestrator | Agentic Mesh Layer – Orchestrator | Workflow Designer | Not Implemented | None | No drag-and-drop or declarative workflow builder; `build_mesh_workflow()` is hardcoded in `workflow.py` |
| Orchestrator | Agentic Mesh Layer – Orchestrator | Agent Task Management | Partially Implemented | Agent invocations logged to `data/audit_trail.jsonl` via `audit_middleware.py`; A2A calls tracked with latency and status | No task queue, no priority management, no async task lifecycle (submit/cancel/poll); all tasks are synchronous request-response |
| Orchestrator | Agentic Mesh Layer – Orchestrator | Agent Routing | Implemented | `DomainExecutor` in `workflow.py`; `infer_route_and_scores()` in `execution_trace.py` — routes to Data Layer / RAG / Hybrid with confidence scoring and rationale bullets | Routing is heuristic/regex-based; no ML-based routing; no dynamic re-routing mid-execution |
| Orchestrator | Agentic Mesh Layer – Event Mesh | Event-Driven Processing | Partially Implemented | `ExecutionTracer.add_listener()` in `execution_trace.py` enables real-time in-process event push to UI; `emit()` notifies all registered listeners | No distributed event bus (no Kafka, no pub/sub broker); events are in-process only and do not cross node boundaries |
| Orchestrator | Agentic Mesh Layer – Mesh Gateways | Integration Framework | Partially Implemented | A2A protocol (`src/a2a/clients.py`, `hosting.py`) for agent-to-agent integration; MCP (`src/integrations/mcp_clients.py`) for tool integration over HTTP | No generic enterprise integration connectors (no REST adapters, no ETL, no enterprise service bus); only A2A + MCP patterns |
| Orchestrator | Discovery Layer – Agent Registry | Agent Integration | Implemented | `src/agents/node_registry.py` — `AGENT_REGISTRY` dict maps agent names to factory functions; `MCP_BACKED_NODES` marks tool-requiring agents; `a2a_server.py` dynamically spawns any node by name | Static registry (code-defined); no dynamic registration at runtime; no agent self-registration |
| Orchestrator | Discovery Layer – MCP Hub | Tool Invocation | Implemented | `src/integrations/mcp_clients.py` — `MCPStreamableHTTPTool` clients for DataLayer (5 tools: customer_360, pricing_recommendation, profitability_summary, margin_analysis, rwa_impact) and RAG (search_documents); tools auto-discovered from MCP servers | Single MCP endpoint per service; no MCP hub aggregating multiple providers; no tool versioning or discovery UI |
| Orchestrator | Agent Runtime | Human-in-the-Loop | Not Implemented | None | No approval workflow, no pause-for-review step, no human escalation path in pipeline; all decisions are fully automated |
| Orchestrator | Agent Runtime | Reliability & Recovery | Partially Implemented | Soft-fail peer delegation in `collaboration_tools.py` (returns error strings, not exceptions); retry logic in `DomainExecutor` (3 patterns: tool-call echo, meta-response, hallucination detection); graceful OTel no-ops throughout | No circuit breakers, no automatic agent failover/backup, no dead-letter queue, no retry with exponential backoff across A2A hops |
| Orchestrator | Agent Runtime | Workflow Version Management | Not Implemented | None | No workflow versioning, no A/B testing of workflow variants, no rollback beyond git; version is implicit in code |
| Telemetry | Platform Services – Telemetry | Platform Health Monitoring | Partially Implemented | `/api/mesh/status` fan-out to all 4 A2A nodes in `api_server.py`; `MeshStatusPage.tsx` in React shows per-node status, uptime, model, and error messages; refreshes every 15s | No deep health metrics (memory, CPU, queue depth); no alerting on unhealthy nodes; no history/trend of health state |
| Telemetry | Unified Model Management – LLM Traffic Observability | LLM Performance Monitoring | Partially Implemented | Agent Framework native OTel spans wrap LLM `get_response()` calls; duration histograms in `metrics.py`; `fab.a2a.calls.total` + duration metrics | No per-model P50/P95 latency dashboards; no token usage tracking; no model quality metrics (hallucination rate, refusal rate) |
| Telemetry | Unified Model Management – LLM Traffic Observability | AI Cost Monitoring | Not Implemented | None | No token counting, no per-request cost attribution, no cost dashboards, no budget limits; Groq API spend is untracked |
| Telemetry | Agent Runtime – Agent Observability | Agent Execution Monitoring | Implemented | `AuditMiddleware` in `src/middleware/audit_middleware.py` — captures inputs, outputs, latency_ms, status per agent invocation; logs to `audit_trail.jsonl`; OTel span per agent; `ExecutionTracer` emits stage events; `ExecutionPanel.tsx` renders per-step trace in UI | No anomaly detection on agent behaviour; no SLA breach alerting |
| Telemetry | Agent Runtime | Distributed Tracing | Implemented | W3C `traceparent`/`tracestate` + Baggage headers propagated across all A2A hops; `TraceContextMiddleware` in `hosting.py` continues caller's trace; `baggage.py` propagates request_id/user/role/session_id; OTLP/Grafana/Azure Monitor exporters in `setup.py` | Trace data goes to external backends (Grafana/Azure) — no in-app trace explorer; no sampling configuration |
| Telemetry | Agent Runtime | Centralized Logging | Implemented | `logging_config.py` — `RotatingFileHandler` (10 MB, 5 backups) + console; structured JSON formatter; trace-correlated (trace_id, span_id in every line); `CAT_*` named logger categories; logs to `data/logs/agent_mesh.log` | No log aggregation UI in app; logs require external tool (Grafana Loki, Azure Log Analytics) to query |
| Telemetry | Platform Services – Telemetry | Operational Metrics | Implemented | `metrics.py` — 10+ OTel counters + histograms: `fab.guardrail.requests.total`, `fab.mesh.requests.total`, `fab.a2a.calls.total`, `fab.mcp.calls.total`, `fab.compliance.requests.total`, `fab.conversation.*`, `fab.output.redaction.pii_hits`, all with duration histograms | Metrics exported to OTel backends only; no built-in metrics dashboard in the app |
| Telemetry | Platform Services – Telemetry | Dashboards & Reporting | Partially Implemented | `MeshStatusPage.tsx` shows live node health; `ExecutionPanel.tsx` per-request trace; CLI `cli_renderer.py` renders Rich terminal summary | No pre-built Grafana dashboards shipped; no business reporting, no trend analysis, no scheduled reports |
| Telemetry | Platform Services – Telemetry | Alerting & Notifications | Not Implemented | OTel exporters connected to Grafana/Azure Monitor (where alerts can be configured externally) | No alerting rules defined in codebase; no notification channels (email/Slack/PagerDuty) wired; no threshold-based alerting logic |
| Telemetry | Platform Services – Telemetry | Audit & Compliance Reporting | Partially Implemented | `data/audit_trail.jsonl` — agent invocation log with timestamp, user, role, inputs, outputs, status, latency; `data/trace_log.jsonl` — guardrail/compliance/access-control events; ComplianceAgent verdict logged | No audit report generation or export (PDF/CSV); no compliance dashboard; no regulatory report templates; audit data requires manual JSONL parsing |
| Control Center | User Interface | Central Administration Portal | Not Implemented | None | React UI is chat-only + mesh status; no admin pages for managing agents, users, config, policies, or system settings |
| Control Center | Discovery Layer – Agent Registry | Agent Lifecycle Management | Partially Implemented | `launch_mesh.py` spawns 4 A2A nodes as OS processes; `node_registry.py` lists agents with descriptions; Starlette `lifespan` in `api_server.py` for startup/shutdown | No per-agent start/stop/restart from UI; no rolling updates; no agent versioning or blue-green deployment; no health-based auto-restart |
| Control Center | Discovery Layer – Agent Discovery | Agent Catalog | Partially Implemented | `node_registry.py` `AGENT_REGISTRY` dict — 4 agents with names, factory refs, and description strings | No browsable catalog UI; no agent capability schemas; no search/filter; no versioning metadata; no published AgentCard browser |
| Control Center | Agent Runtime | Runtime Operations Console | Not Implemented | `MeshStatusPage.tsx` shows basic node health (status/uptime/model) | No ops console features: cannot restart agents from UI, view logs in UI, change config at runtime, or inspect in-flight requests |
| Control Center | Unified Model Management – Guardrails | AI Governance Management | Partially Implemented | `deterministic_filters.py` — regex guardrails (10 injection patterns, PII, 8 destructive intent patterns); `ComplianceAgent` — LLM semantic safety review; RBAC role enforcement in `workflow.py` | No governance policy management UI; no policy versioning; no bias/fairness detection; no model card management; guardrail rules are hardcoded |
| Control Center | Agent Runtime – Memory | Memory Management | Implemented | `src/memory/` — `ConversationStore` facade, `JsonlBackend` active default (per-session JSONL files), `RedisBackend` stub for future; configurable `max_turns` (default 8), backend selection via `Config`; `/api/conversations/{session_id}` for history retrieval; `useChat` hook restores session from localStorage | Redis backend is a stub (raises `NotImplementedError`); no memory admin UI (view/edit/clear sessions); no cross-session or long-term knowledge memory |
| Control Center | Unified Model Management | Model Administration | Partially Implemented | `src/config.py` — per-agent model overrides (`GROQ_MODEL`, per-agent API key + model config); fallback chain implemented | No model management UI; model changes require code/config edits; no model versioning, A/B testing, or hot-swap; no model registry |
| Control Center | User Interface | Platform Configuration | Partially Implemented | `Config` class in `src/config.py` — `.env`-driven, centralised, covers all subsystems (LLM, A2A, MCP, memory, observability, RBAC) | No configuration UI; all config via `.env` file or environment variables; no runtime config changes; no config versioning or history |
| Control Center | User Interface | Identity & Access Management | Partially Implemented | `identity_provider.py` — 7 FAB banking roles, `login()` + `list_users()` mock directory; RBAC enforced in `RBACValidationExecutor`; `LoginPage.tsx` + `SignupPage.tsx` in UI | Mock users only — no Azure AD/OIDC/SAML integration; no MFA; no SSO; no user provisioning/deprovisioning; no role assignment UI |
| Control Center | Platform Services – Security | Security Administration | Partially Implemented | Two-layer guardrails (deterministic + LLM-based); PII redaction on output; RBAC; W3C baggage carrying user identity across hops | No security admin UI; guardrail rules are hardcoded (no policy editor); no security incident response workflows; no IP allowlisting or rate limiting |
| Control Center | Platform Services – Data Governance | Governance Administration | Not Implemented | None | No data governance framework; no data lineage tracking; no data catalog; no retention policies; no data classification beyond PII redaction |
| Control Center | Platform Services – Cost Optimization (FinOps) | Cost Administration | Not Implemented | None | No LLM token accounting; no per-user/team cost attribution; no budget caps or alerts; no FinOps dashboard; Groq API spend is entirely untracked |

---

## Summary by Status

| Status | Count | Capabilities |
|---|---|---|
| **Implemented** | 10 | Workflow Orchestration Engine, Agent Routing, Agent Integration (Registry), Tool Invocation (MCP), Agent Execution Monitoring, Distributed Tracing, Centralized Logging, Operational Metrics, Memory Management, Agent Execution Monitoring |
| **Partially Implemented** | 14 | Event-Driven Processing, Integration Framework, Reliability & Recovery, Platform Health Monitoring, LLM Performance Monitoring, Dashboards & Reporting, Audit & Compliance Reporting, Agent Lifecycle Management, Agent Catalog, AI Governance Management, Model Administration, Platform Configuration, Identity & Access Management, Security Administration |
| **Not Implemented** | 9 | Workflow Designer, Human-in-the-Loop, Workflow Version Management, AI Cost Monitoring, Alerting & Notifications, Central Administration Portal, Runtime Operations Console, Governance Administration, Cost Administration (FinOps) |

---

## Priority Next Steps

### High Priority (Foundational Gaps)
1. **Human-in-the-Loop** — Add a pause/approval step in the workflow pipeline for high-risk decisions (e.g., pricing exceptions, compliance overrides). Required for enterprise trust.
2. **AI Cost Monitoring** — Instrument Groq API calls with token counts; attribute cost per user/session/role. FinOps baseline needed before scale-out.
3. **Azure AD / OIDC Integration** — Replace mock `identity_provider.py` with real SSO; `login()` should resolve against corporate directory.
4. **Alerting & Notifications** — Define OTel-based alerting rules (node down, guardrail spike, latency breach) and wire to a notification channel.

### Medium Priority (Operational Maturity)
5. **Redis Memory Backend** — Complete `redis_backend.py` (RPUSH/LRANGE/DEL) to enable multi-node session sharing; JSONL is single-node only.
6. **Agent Lifecycle Management UI** — Add start/stop/restart controls to `MeshStatusPage.tsx`; surface logs in UI.
7. **Workflow Version Management** — Introduce a workflow schema (YAML or dataclass) with a version field so workflow changes are auditable.
8. **Agent Catalog UI** — Expose `AGENT_REGISTRY` metadata (name, description, tools, model, port) as a browsable page in the React UI.

### Lower Priority (Governance & Reporting)
9. **Audit & Compliance Reporting** — Build a report generator over `audit_trail.jsonl` and `trace_log.jsonl` (CSV/PDF export, date-range filter).
10. **AI Governance Management** — Move guardrail rules out of code into a configurable policy store; add a policy editor UI.
11. **Governance Administration** — Implement basic data retention policies for JSONL conversation files and audit logs.
12. **Pre-built Grafana Dashboards** — Ship dashboard JSON for the `fab.*` OTel metrics already being exported.

---

## Key Files Reference

| Area | File | Notes |
|---|---|---|
| Orchestration | `agent-mesh/src/mesh/orchestrator.py` | Main request handler, OTel spans, memory I/O |
| Workflow | `agent-mesh/src/mesh/workflow.py` | 6 executor pipeline, route inference |
| Agents | `agent-mesh/src/agents/` | compliance, data, rag, price_assist, node_registry |
| A2A Protocol | `agent-mesh/src/a2a/clients.py`, `hosting.py` | Remote calls, trace propagation |
| MCP Integration | `agent-mesh/src/integrations/mcp_clients.py` | DataLayer + RAG tool factories |
| Guardrails | `agent-mesh/src/guardrails/deterministic_filters.py` | Regex injection/PII/destructive detection |
| Memory | `agent-mesh/src/memory/` | ConversationStore, JsonlBackend, RedisBackend stub |
| Observability | `agent-mesh/src/observability/` | setup.py, metrics.py, baggage.py, logging_config.py |
| API Server | `agent-mesh/api_server.py` | Starlette REST bridge for React UI |
| Frontend | `agent-mesh/frontend/src/` | 47 files — ChatPage, MeshStatusPage, ExecutionPanel, hooks |
| Config | `agent-mesh/src/config.py` | All env-driven settings |
| Auth | `agent-mesh/src/auth/identity_provider.py` | 7 banking roles, mock directory |


# AI Cost Tracing

# Clarification: Where Can Cost Data Be Seen?

## User Question
"Why GET /api/cost/summary returns per-agent breakdown — can't I see it in the React UI or Grafana?"

---

## What's Already Visible (from the last implementation)

### 1. React Chat UI — YES, inline per message
`CostBadge.tsx` is already wired into `MessageBubble.tsx`.
Every assistant reply already shows:
```
↑ 312 tok · ↓ 89 tok · ~$0.000041
```
This comes from the `token_usage` field embedded directly in the `/api/query` response —
**no separate API call needed**. The user sees it instantly when the answer arrives.

### 2. Grafana / OTLP — YES, but needs a dashboard
The OTel metrics (`fab.llm.tokens.input`, `fab.llm.tokens.output`, `fab.llm.cost.usd`,
`fab.llm.tokens.per_call`) are already being exported to whatever OTel backend is
configured (`OBS_PROFILE` in `.env` — dev/grafana/prod). They appear in Grafana's
Explore tab under the `agent_mesh` meter. **But no pre-built Grafana dashboard panel
was shipped** — the user has to create one manually or we build a dashboard JSON.

### 3. `/api/cost/summary?session_id=...` — Standalone API only
This endpoint returns the full per-agent breakdown for a session (which agent used
which model, how many tokens, how much cost). It is **not yet rendered anywhere in
the React UI** — it's only useful if someone calls it directly (Postman, curl, or a
future analytics page).

---

## The Gap the User is Pointing To

The user wants to SEE the per-agent breakdown in the UI — not just the total in the
message bubble. Right now `CostBadge` shows totals only. The per-agent breakdown
(price_assist used 312 tok on gpt-oss-120b, compliance used 89 tok on gpt-oss-20b etc.)
is sitting in `/api/cost/summary` but is never rendered.

---

## Plan: Add Per-Agent Cost Panel to the React UI

### What to Build
A collapsible `CostPanel.tsx` component — similar to `ExecutionPanel.tsx` — that renders
the per-agent breakdown from `result.token_usage.agents`. No new API call needed since
`token_usage` is already in the `/api/query` response.

### Files to Change

| File | Change |
|---|---|
| `frontend/src/components/chat/CostPanel.tsx` | NEW — collapsible table: agent · model · ↑in · ↓out · cost |
| `frontend/src/components/chat/CostBadge.tsx` | Add an expand chevron that toggles CostPanel |
| `frontend/src/components/chat/MessageBubble.tsx` | Pass `token_usage` down to CostBadge (already done) |

### CostPanel Layout (inside collapsible)
```
Agent          Model                  ↑ Input   ↓ Output   Est. Cost
─────────────────────────────────────────────────────────────────────
price_assist   gpt-oss-120b          1,248      312       $0.001413
compliance     gpt-oss-20b             89        24       $0.000011
─────────────────────────────────────────────────────────────────────
TOTAL                                1,337      336       $0.001424
```

### No Backend Changes Needed
`token_usage` with per-agent breakdown is already in the `/api/query` response.
`CostPanel` just reads `result.token_usage.agents`.

---

## Verification
1. Send a query in the React UI
2. CostBadge shows total tokens + cost inline
3. Click the expand icon → CostPanel opens showing per-agent rows
4. In Grafana Explore: query `fab_llm_tokens_input_total` — data points should appear
