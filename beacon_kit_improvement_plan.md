> **Historical document (2026-10-16).** Beacon Kit has since been evolved into the **Gernas SDK** (`gernas-sdk/`, package `gernas-telemetry`). Paths and APIs below refer to the former `beacon_kit/` package; see `gernas-sdk/docs/MIGRATION_FROM_BEACON_KIT.md` for the mapping and `gernas-sdk/docs/REQUIREMENTS_TRACEABILITY.md` for current coverage.

# Beacon Kit ↔ POD 1 / POD 3 Observability Coverage Analysis & Development Plan

## Context
POD 2 owns the "GERNAS Telemetry" observability platform, structured in three layers plus a data pipeline and visualization:
- **Layer 1 – Infrastructure**: AKS compute/host health, deployed endpoints (network latency/error), environment-aware monitoring, capacity/autoscaling, infra cost.
- **Layer 2 – Agent Runtime**: agent/reasoning tracing, tool/MCP tracing, A2A tracing, LLM token/latency/cost, RAG telemetry, session telemetry, error/retry, override/escalation, drift signal, data-product access.
- **Layer 3 – Compliance**: immutable audit trail, lineage, PII monitoring, guardrail-violation logging, approval-workflow audit, security events, retention/archival, ARB reporting.
- **Telemetry Data Pipeline**: collect → enrich → ingest.
- **Telemetry Visualization**: dashboards, alerts, drill-down.

**POD 1** (FAB Pricing Agent, `agent-mesh/`) is the primary telemetry SOURCE and hands off to POD 2.
**POD 3** (data products on Databricks/Delta/Unity Catalog) is a secondary source; its own observability is Databricks/Azure-native.

The **Beacon Kit** (`beacon_kit/`, v0.2.0) is the concrete OTel implementation meant to satisfy POD 2. This plan validates POD 2's activities against the *actual* Beacon Kit and agent-mesh code, classifies each item, reports coverage/gaps, and defines the work to reach full coverage.

**Confirmed scope decisions:**
- Beacon Kit's remit = **Layers 1, 2, and 3** (infrastructure instrumentation is added to Beacon Kit). POD 3's Databricks data-layer observability stays **native** (Unity Catalog lineage, Delta audit tables, Databricks job monitoring, Azure Monitor); Beacon's link to POD 3 is a **correlation/access contract**, not notebook instrumentation.
- Visualization/alerting = **GERNAS-native**. Beacon Kit only exports signals (OTLP → Tempo/Mimir/Loki / Azure Monitor) and defines KPIs, thresholds, and export contracts. The HTML reporter remains a dev-time tool only.

## Ground-truth findings (validated against code)

**Beacon Kit already implements (Layer 2/3 strong):**
- Tracing: `setup_telemetry` (`core/provider.py`) — TracerProvider, W3C TraceContext+Baggage propagation, ParentBased+TraceIdRatio sampling, Batch/Simple processors; exporters console/pretty/OTLP-gRPC/Grafana-OTLP-HTTP/file/Azure/off (`core/exporters.py`).
- Layer-2 decorators (`decorators/`): `traced`, `traced_node`, `traced_pipeline`, `traced_agent` (SERVER), `traced_llm_call` (CLIENT), `traced_tool`, `traced_mcp_tool`, `traced_a2a_hop`; metrics `AgenticMetrics` (`metrics/agentic.py`) — agent/tool/llm/a2a/mcp call counters + agent/llm/a2a duration histograms; `track_latency`/`count_calls`.
- Middleware/propagation: `middleware/fastapi.py`, `middleware/a2a.py`, `propagation/context.py` (HITL checkpoint continuity), `propagation/baggage.py` (`BaggageSpanProcessor`).
- Logging: `LoggerProvider` bridge, `core/structured_logger.py`, `decorators/log.py`, `logconfig/request_logging.py` (trace/span/request-id stamping).
- Layer-3 compliance (`compliance/`): `ComplianceEvent` with SHA-256 chain-hash + HMAC signature + `verify_chain` (`chain.py`), `GuardrailEvent`/`RBACEvent`, decorators `compliance_gate`/`guardrail_check`/`rbac_check`, JSONL append-only store.
- Reporting: `core/html_reporter.py` (`beacon-report` CLI) — static per-session dark HTML with Requests/Gantt/service-map/Logs/Spans/Compliance/Metrics/Errors tabs.
- Config: `core/config.py` `TelemetryConfig.from_env` — exporter/profile selection, endpoints, Grafana/Azure auth, sampling, resource attrs, compliance flags.

**Already wired into POD 1** (guarded try/except, no-op fallback):
- `setup_telemetry` at `agent-mesh/run.py`, `api_server.py`, `a2a_server.py`, `devui_app.py`, `datalayer-as-service/app.py`, `rag-as-a-service/.../main.py`.
- `traced_agent`+`AgenticMetrics` on every executor (`src/mesh/workflow.py:72`), `traced_a2a_hop` (`src/a2a/clients.py:36`), `traced_tool`+`compliance_gate` (`src/tools/collaboration_tools.py:30`), `guardrail_check` (`src/guardrails/deterministic_filters.py:20`).
- A parallel native OTel layer (`agent-mesh/src/observability/`) still runs for business metrics/logging/baggage.

**Not present in Beacon Kit (key gaps):**
- No MAF auto-instrumentation (no `agent_framework` hooks); all instrumentation is manual decorators.
- No Layer-1 infrastructure telemetry (AKS health, network, capacity/autoscaling, infra cost) — only resource tags.
- No automatic LLM token/cost capture (only if caller passes a `usage` dict; no price table → no cost).
- No RAG retrieval-quality metrics (relevance/hit-rate); no drift collection; no retention/archival; no ARB-format reporting.
- No live dashboards, no alerting (HTML reporter is static; no shipped Grafana dashboard JSON).
- Minor: version mismatch (`__init__.py` 0.2.0 vs `pyproject.toml` 0.1.0); FastAPI/httpx auto-instrumentors listed as deps but never invoked.

## POD 2 activity → POD 1 / POD 3 mapping, classification & Beacon Kit coverage

Legend — **Applicability** (to POD 1/POD 3): Directly / Partially / Not Applicable. **Beacon coverage**: Full / Partial / None.

### Layer 0 — Cross-Layer Telemetry Foundation
| POD 2 activity | Applies to | Maps to | Applicability | Beacon coverage | Gap |
|---|---|---|---|---|---|
| Decorator templates for Infra / Agent Runtime / Compliance | POD 1 (also POD 3 infra) | POD1 "Instrumenting agents/tools/LLM/data" | Directly | **Partial** | L2+L3 decorator families exist; **no L1 infra decorator/collector template** |

### Layer 1 — Infrastructure
| POD 2 activity | Applies to | Maps to | Applicability | Beacon coverage | Gap |
|---|---|---|---|---|---|
| Compute & hosting health (AKS) | POD1 + POD3 hosting | POD1 Monitoring "perf monitoring"; POD3 Ops | Directly | **None** | No CPU/mem/pod-health collection; needs OTel host/k8s receiver or infra decorator |
| Deployed endpoints (network latency/error, MCP, data sources) | POD1 (A2A/MCP), POD3 (data) | POD1 A2A/MCP integration | Partially | **Partial** | App-side latency/errors captured on A2A/MCP client spans; network-level not |
| Environment-aware monitoring (shared Dev/SIT/UAT vs Prod) | POD1 + POD3 | Shared env dependency (both pods) | Directly | **Partial** | `deployment.environment` resource tag present; per-env dashboards are GERNAS-native |
| Capacity planning & autoscaling telemetry | POD1 + POD3 | POD1 Monitoring (throughput) | Partially | **None** | No utilisation-trend/scaling-event signals |
| Infrastructure cost monitoring | POD1 + POD3 | POD1 "cost/token monitoring" (infra portion) | Partially | **None** | No cloud billing/usage export integration |

### Layer 2 — Agent Runtime (Beacon Kit core strength)
| POD 2 activity | Applies to | Maps to | Applicability | Beacon coverage | Gap |
|---|---|---|---|---|---|
| Agent invocation & reasoning-step tracing | POD1 | Orchestrator + sub-agents; `workflow.py` | Directly | **Full (agent) / Partial (reasoning-step)** | `traced_agent` covers invocation; reasoning content is hashed only (per-step spans live in app `src/tracing`, not Beacon) |
| Tool / MCP call tracing | POD1 | Tool registry, MCP integration | Directly | **Full** | Wired via `traced_tool`/`traced_mcp_tool` |
| A2A message tracing across sub-agents | POD1 | A2A server/client build | Directly | **Full** | `traced_a2a_hop` + `A2ATraceContextMiddleware` |
| LLM call metrics: tokens, latency, cost | POD1 | LLM access; cost/token monitoring | Directly | **Partial** | Latency yes; tokens only if caller passes `usage`; **no cost calc / price table / auto-capture from OpenAI/Groq response** |
| RAG retrieval telemetry (latency, relevance, hit rate) | POD1 | RAG pipeline/retrieval API | Directly | **Partial** | Call latency traceable; **no relevance/hit-rate quality metrics** (rag-as-a-service telemetry commented out) |
| Conversation / session-level telemetry | POD1 | Multi-turn, memory/context mgmt | Directly | **Partial** | Session id can ride baggage; **no session-scoped span/metric aggregation** |
| Error, exception & retry tracking | POD1 | Evaluator/monitoring | Directly | **Partial** | Error status/counter + circuit breaker present; **retry/backoff not instrumented as metric** |
| Human override & escalation event tracking | POD1 | HITL, feedback capture | Directly | **Partial** | `guardrail_check(escalate)`+compliance events partial; **no first-class override/escalation event schema** |
| Model / output drift signal collection | POD1 | Drift monitoring vs golden baseline | Directly | **None** | No drift-metric collection |
| Data products access tracing | POD1 ↔ POD3 | DataAgent→DataLayer MCP; POD3 access events | Directly | **Partial** | Access spans on MCP calls; **POD3 stable-identifier/audit contract missing** |

### Layer 3 — Compliance (Beacon Kit second strength)
| POD 2 activity | Applies to | Maps to | Applicability | Beacon coverage | Gap |
|---|---|---|---|---|---|
| Immutable audit trail (agent/data/decision chain) | POD1 (+POD3 data) | POD1 "immutable audit logging" | Directly | **Full** (app-level) | Chain-hash+HMAC JSONL; **external tamper-evident store still roadmap** |
| Data lineage & access logging | POD1 ↔ POD3 | POD3 Unity Catalog lineage | Partially (POD3-native) | **Partial** | Access events yes; **true source→output lineage needs POD3 UC contract**, no lineage model in Beacon |
| PII / data-privacy monitoring | POD1 | Guardrails input/output redaction | Directly | **Partial** | Detection + `GuardrailEvent` present; **alerting is GERNAS-native** |
| Guardrail-violation & policy-adherence logging | POD1 | Guardrails | Directly | **Full** | `guardrail_check` → `GuardrailEvent`, wired |
| Approval-workflow audit logging | POD1 | HITL approval routing | Directly | **Partial** | `compliance_gate`+checkpoint continuity; **no "who/when/what-evidence" approval event** |
| Security event monitoring (auth failures, anomalous access) | POD1 | Auth/authz for agent calls | Directly | **Partial** | `rbac_check`→`RBACEvent`; **no anomaly detection / SIEM feed** |
| Retention & archival policy enforcement | POD1 + POD3 | Records-retention (FAB) | Directly | **None** | JSONL grows unbounded; no retention/archival jobs |
| Regulatory / ARB reporting hooks | POD1 | Audit → ARB reporting | Partially | **Partial** | HTML compliance report exists; **no ARB-format report generation** |

### Telemetry Data Pipeline
| POD 2 activity | Applies to | Applicability | Beacon coverage | Gap |
|---|---|---|---|---|
| Collect via layer-specific decorators | POD1 (+POD3 infra) | Directly | **Partial** | L2/L3 collection Full; **no L1 infra collectors/decorators** |
| Post-process & enrich (validate/normalize/correlate/mask/aggregate) | POD1 | Partially | **Partial** | Correlation (trace/baggage) + masking (hash/sanitize) present; normalize/aggregate = Collector-side, not built |
| Ingest into operational/analytical/audit stores | POD1 | Directly | **Partial** | OTLP/Grafana/Azure/file + JSONL audit store exist; **no broker/DLQ/retry pipeline** |

### Telemetry Visualization
| POD 2 activity | Applies to | Applicability | Beacon coverage | Gap |
|---|---|---|---|---|
| Dashboards, alerts, drill-down (Infra/Runtime/Compliance) | POD1 + POD3 | Directly | **Partial → GERNAS-native** | Static HTML drill-down only; **live dashboards + alerting delegated to GERNAS**; Beacon must publish KPIs/thresholds + export contracts |

### POD 3 specific note
POD 3's only observability line item ("Operations & Handover: configure monitoring, alerting and run logging… assess agent observability only where applicable") is satisfied by **Databricks job monitoring + Azure Monitor + Unity Catalog lineage**, not Beacon Kit. Beacon's only POD 3 touchpoints are the **data-product access + lineage contract** (POD 3 exposes stable identifiers/access-audit events that POD 1 Beacon spans/compliance events reference). No Beacon instrumentation is added to POD 3 notebooks.

## Consolidated Observability Development Plan (POD 1 + POD 3)

### A. Already supported by Beacon Kit (no build — verify only)
Agent invocation tracing, tool/MCP tracing, A2A tracing, guardrail-violation logging, immutable audit trail (chain-hash+HMAC), structured logging with trace correlation, exporter fan-out (OTLP/Grafana/Azure/file), env resource tagging, HTML dev report. Action: keep wired; confirm the parallel native `src/observability` layer is deduplicated (avoid double metrics).

### B. Partially supported — required enhancements to Beacon Kit
1. **LLM token/cost** (`decorators/agent.py`/`trace.py`, `metrics/agentic.py`): auto-extract usage from OpenAI/Groq response objects (not just dict); add a per-model **price table + cost histogram** (`beacon.llm.cost`).
2. **RAG retrieval-quality metrics** (new `decorators/rag.py` + `AgenticMetrics`): retrieval latency, hit-rate, top-k relevance/rerank scores; re-enable `rag-as-a-service` telemetry to feed it.
3. **Session-level telemetry**: session-scoped span attributes + per-session metrics keyed on baggage `session_id`; align with `agent-mesh/src/memory/`.
4. **Retry/error enrichment**: instrument `collaboration_tools` retry/backoff as a `beacon.retry` counter; standardize error taxonomy on spans.
5. **Override/escalation + approval events** (`compliance/events.py`): first-class `OverrideEvent`/`ApprovalEvent` (who/when/evidence), wired to `hitl/approval_store.py`.
6. **Security events**: extend `RBACEvent` with auth-failure capture; emit a defined event stream for GERNAS/SIEM (anomaly detection stays GERNAS-side).
7. **Retention/archival**: retention policy + rotation/archival job for JSONL compliance + file signal stores.
8. **Pipeline hardening**: document/enable Collector-side normalization/aggregation + DLQ; tail-sampling policy at Collector.
9. **Housekeeping**: fix version mismatch; either invoke or drop FastAPI/httpx auto-instrumentors.

### C. Not supported — new capabilities to add to Beacon Kit
1. **Layer 1 Infrastructure module** (new `beacon_kit/infra/`): infra decorator/collector template (Layer-0 foundation gap) + integration of OTel **host + k8s + kubelet receivers** for AKS compute/pod health; endpoint/network latency+error spans; capacity/utilisation + scaling-event signals; **infra cost** via cloud billing/usage export into the pipeline. Ships as Collector config + a thin decorator template so all three layers share one pattern.
2. **Drift signal collection** (new `beacon_kit/drift/`): collect output/model-drift signals vs POD 1 golden baseline; export metrics for GERNAS drift dashboards.
3. **Data-product access/lineage contract** (POD 1 ↔ POD 3): a documented event/attribute schema (stable data-product IDs, access-audit fields) that POD 3 emits (Unity Catalog/Delta audit) and Beacon spans/compliance events reference for end-to-end lineage.
4. **KPI/threshold + export contract spec** for GERNAS: define the metric/trace/log/audit schemas, KPIs (latency p95, error rate, override/escalation rate, token/cost, guardrail triggers, drift), thresholds, and RBAC — consumed by GERNAS-native dashboards/alerts (no dashboards built in Beacon).
5. **ARB regulatory report generator**: produce ARB-format compliance reports from the audit chain (extends `html_reporter` logic into a formatted export).

### D. Out of scope for Beacon Kit (native / other pods)
Live dashboards & alerting engine (GERNAS platform), POD 3 Databricks notebook instrumentation (Unity Catalog lineage + Databricks job monitoring + Azure Monitor), anomaly-detection models (GERNAS/SIEM).

### E. Final assessment
Beacon Kit **already satisfies the majority of POD 2 Layer 2 (Agent Runtime) and Layer 3 (Compliance) requirements for POD 1**, and is genuinely integrated into the running agent mesh. It **cannot yet satisfy full coverage** because of: (1) an absent Layer 1 Infrastructure capability, (2) partial LLM token/cost, RAG-quality, session, retry, override/approval, and security-event telemetry, and (3) missing drift, retention, ARB reporting, and the POD 3 lineage contract. With the enhancements in §B and new capabilities in §C — and delegating dashboards/alerts to GERNAS and the data layer to Databricks-native tooling — Beacon Kit **can** meet the POD 1 (and POD 1↔POD 3 contract) observability requirements. POD 3's internal data-layer observability remains native and is not a Beacon Kit deliverable.

## Verification (how to validate once implemented)
1. Run `agent-mesh/launch_mesh.py` (or `run.py --verbose`) end-to-end; confirm a single trace spans orchestrator → A2A → PriceAssist → Data/RAG MCP with token/cost, RAG-quality, and session attributes present.
2. Trigger a PII/guardrail query and a HITL-approval path; verify `GuardrailEvent`, `OverrideEvent`/`ApprovalEvent`, and chain-verified audit records (`verify_chain`) in `observability/compliance/`.
3. Deploy the Layer-1 Collector config on AKS; confirm host/k8s/cost metrics arrive with `deployment.environment` tags per env.
4. Point exporters at the GERNAS/Grafana stack; confirm signals land and the published KPI/threshold contract renders in GERNAS dashboards/alerts (built platform-side, not in Beacon).
5. Run `beacon-report` for the dev HTML sanity check; run the ARB report generator against the audit JSONL.
6. Confirm POD 3 emits the agreed data-product access/lineage identifiers and that POD 1 traces reference them (cross-pod lineage query).
