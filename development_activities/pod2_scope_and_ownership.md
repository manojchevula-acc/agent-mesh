# POD 2 — Agentic Observability: Scope, Deliverables & Ownership

Purpose: clarify **which POD 2 activities are actual development deliverables for the agentic-observability engineer** (the person building a "beacon_kit"-style SDK for the GERNAS platform), which belong to adjacent teams, and what the scope boundary is for each. Use this to size the engagement and set expectations with the client.

---

## 1. The mental model: "Instrument" vs "Operate"

Every observability activity is one of two kinds:

| | **Instrument** (emit signals) | **Operate** (collect / store / visualize / monitor) |
|---|---|---|
| What | Code inside the agent app that produces spans, metrics, logs, audit events | Pipelines, storage backends, dashboards, alerting, infra monitoring |
| Skill | Agent/LLM/OTel SDK engineering | Platform / SRE / DevOps / FinOps / Security |
| Tooling | Custom SDK (the "beacon_kit") + OTel SDK | Mostly off-the-shelf: OTel Collector, Grafana stack / Azure Monitor / Prometheus, SIEM |
| **Owner** | **The agentic-observability engineer (you)** | **Platform / SRE / GERNAS platform team** |

**The single most important framing:** your core deliverable is the *instrument* half **plus the contracts** (semantic conventions, correlation IDs, event/metric schemas, KPI definitions) that let the *operate* half consume what you emit. You are not building the infrastructure monitoring platform, the dashboards, the SIEM, or the storage backends.

---

## 2. Roles involved in POD 2

| Code | Role | Responsibility |
|---|---|---|
| **OE** | Agentic Observability Engineer (you) | The SDK: agent-runtime instrumentation, compliance/audit event capture, semantic conventions, exporter config, KPI/schema contracts |
| **PLAT** | Platform / SRE / DevOps | Infra monitoring (AKS/host/network/cost), OTel Collector deployment, telemetry storage backends (Tempo/Mimir/Loki or Azure Monitor), autoscaling |
| **SEC** | Security / GRC / Compliance | SIEM, data-classification policy, retention/archival policy, ARB report format, anomaly detection |
| **DATA** | Data Engineering (POD 3) | Data-product access/audit events, lineage at source (Unity Catalog / Delta), stable identifiers |
| **VIZ** | BI / GERNAS dashboard team | Live dashboards, alert rules, drill-down UIs, RBAC on dashboards |

---

## 3. Ownership & scope for every POD 2 activity

Legend — **Owner** = accountable role. **OE scope**: *Build* (core deliverable) / *Contract* (define schema, someone else operates) / *None*.

### Cross-Layer Foundation
| Activity | Owner | OE scope | Notes |
|---|---|---|---|
| Decorator/instrumentation templates for the 3 layers | OE | **Build** | The reusable SDK pattern. Core deliverable. |

### Layer 1 — Infrastructure  *(mostly NOT the OE's build)*
| Activity | Owner | OE scope | Notes |
|---|---|---|---|
| Compute & hosting health (AKS CPU/mem/pod) | **PLAT** | **Contract** | Off-the-shelf (OTel k8s/host receivers, Azure Monitor Container Insights, Prometheus). OE only guarantees resource attributes on app telemetry so it correlates. |
| Deployed endpoints (network latency/error) | **PLAT** + OE | Build (app-side) / Contract (network) | OE emits client-span latency/errors for A2A/MCP calls; network-level is service mesh / platform. |
| Environment-aware monitoring (Dev/SIT/UAT vs Prod) | **PLAT** | **Contract** | OE stamps `deployment.environment`; PLAT builds per-env dashboards & isolation. |
| Capacity planning & autoscaling telemetry | **PLAT** | None | Pure infra/SRE. |
| Infrastructure cost monitoring | **PLAT / FinOps** | None | Cloud billing export; not agent-specific. |

> **Answer to "should infra be the AI engineer's job?":** No. Layer 1 is ~90% platform/SRE using standard tooling. The OE's only Layer-1 responsibility is **resource attribution + correlation** — ensuring agent telemetry carries the identifiers (service, pod, node, env) needed to join it to the platform's infra telemetry.

### Layer 2 — Agent Runtime  *(this IS the OE's core build — the "beacon_kit")*
| Activity | Owner | OE scope | Notes |
|---|---|---|---|
| Agent invocation & reasoning-step tracing | OE | **Build** | Spans per agent/orchestrator step. |
| Tool / MCP call tracing | OE | **Build** | Client spans for tool + MCP calls. |
| A2A message tracing across sub-agents | OE | **Build** | Trace-context propagation across agent hops. |
| LLM call metrics: tokens, latency, cost | OE | **Build** | Auto-capture token usage from LLM SDK response + per-model cost table. |
| RAG retrieval telemetry (latency, relevance, hit rate) | OE | **Build** | Retrieval-quality metrics. |
| Conversation / session-level telemetry | OE | **Build** | Session-scoped correlation & metrics. |
| Error, exception & retry tracking | OE | **Build** | Error taxonomy + retry/backoff signals. |
| Human override & escalation event tracking | OE | **Build** | First-class override/escalation events. |
| Model / output drift signal collection | OE | **Build** (collect) | OE emits drift signals; baseline & detection logic may be shared with data-science/eval. |
| Data products access tracing | OE + DATA | Build (app-side) / Contract (source) | OE traces access from the agent; DATA exposes stable IDs/audit events. |

### Layer 3 — Compliance  *(OE builds the capture; policy/operation is shared)*
| Activity | Owner | OE scope | Notes |
|---|---|---|---|
| Immutable audit trail (agent/data/decision chain) | OE + SEC | **Build** | OE builds tamper-evident capture (hash-chain + signing); SEC approves the store. |
| Data lineage & access logging | **DATA** | **Contract** | Lineage owned at source (Unity Catalog). OE references identifiers only. |
| PII / data-privacy monitoring | OE + SEC | Build (detect/emit) / Contract (alerting) | OE detects & emits events; SEC sets classification policy & alerting. |
| Guardrail-violation & policy-adherence logging | OE | **Build** | Guardrail event capture. |
| Approval-workflow audit logging | OE | **Build** | Who/when/what-evidence approval events. |
| Security event monitoring (auth failures, anomalous access) | **SEC** | **Contract** | OE emits auth/RBAC events in an agreed schema; SEC owns SIEM & anomaly detection. |
| Retention & archival policy enforcement | **SEC / PLAT** | Contract | SEC defines retention policy; PLAT executes; OE provides hooks/metadata. |
| Regulatory / ARB reporting hooks | **SEC / GRC** | Build (generator) / Contract (format) | GRC defines the ARB format; OE builds a generator over the audit data. |

### Telemetry Data Pipeline
| Activity | Owner | OE scope | Notes |
|---|---|---|---|
| Collect via layer-specific instrumentation | OE | **Build** | SDK exporters (OTLP/file/backend). |
| Post-process & enrich (validate/normalize/correlate/mask/aggregate) | **PLAT** | **Contract** | Done at the OTel Collector / stream layer; OE defines schemas & masking rules. |
| Ingest into operational/analytical/audit stores | **PLAT** | None | Storage backends & ingestion pipelines. |

### Telemetry Visualization
| Activity | Owner | OE scope | Notes |
|---|---|---|---|
| Dashboards, alerts, drill-down | **VIZ / PLAT** | **Contract** | Built in the GERNAS platform. OE delivers KPI/threshold definitions + semantic conventions; a static dev-only report is optional. |

---

## 4. The OE's actual deliverable (work breakdown)

What "developing something like beacon_kit" concretely means for the client:

1. **Instrumentation SDK — Agent Runtime** (Layer 2): decorators/middleware/auto-instrumentation for agents, tools, MCP, A2A, LLM (token+cost), RAG quality, sessions, errors/retries, overrides, drift signals.
2. **Compliance/Audit module** (Layer 3): tamper-evident audit chain, guardrail/RBAC/PII/approval event capture, ARB report generator.
3. **Semantic conventions & correlation contract** (the glue): standard attribute names, correlation/baggage IDs (request/session/user/role), resource attributes for infra join, and event schemas — the deliverable the PLAT/SEC/VIZ teams depend on.
4. **Exporter & config layer**: pluggable exporters to the client's chosen backend (Grafana stack / Azure Monitor), env-driven config, sampling.
5. **KPI / threshold / schema specifications**: handed to VIZ (dashboards/alerts), SEC (SIEM/retention), and DATA (lineage) so they can build their side.
6. **Integration & enablement**: wire the SDK into the agent app, docs, examples, and a dev-time report for local verification.

**Explicitly NOT the OE's deliverable:** infra monitoring, OTel Collector operation, storage backends, live dashboards, alerting engine, SIEM, retention execution, data-source lineage.

---

## 5. What to fix in the current Excel before sharing

Items to **reclassify or move out** of the OE's POD 2 build:
- **All of Layer 1 Infrastructure** except "resource attribution / env tagging" → mark as **PLAT-owned dependency**, not an OE build item.
- **Capacity/autoscaling, infra cost** → remove from OE scope entirely (FinOps/SRE).
- **Ingest into stores, storage backends, dashboards, alerting** → mark as **PLAT/VIZ**, OE provides contracts only.
- **Security event monitoring / SIEM, retention execution, ARB format** → **SEC-owned**, OE emits/generates against their spec.
- **Data lineage** → **DATA/POD 3**, OE references identifiers.

Items to **keep as firm OE deliverables:** the entire Agent Runtime layer, the Compliance *capture* logic, the cross-layer instrumentation templates, semantic conventions/correlation, exporter config, and the KPI/schema contracts.

Net effect: POD 2 shrinks from "build an observability platform" to **"build the agent-observability SDK + the contracts that integrate it into the client's existing GERNAS platform"** — which is a realistic, well-bounded engagement.

---

## 6. Open questions to confirm with the client (these change the scope)

1. **Is there a dedicated platform/SRE team, or is the OE expected to also stand up infra monitoring?** If solo, Layer 1 + pipeline + storage move *into* scope and the estimate grows substantially — flag this explicitly.
2. **Does GERNAS already have an observability backend** (Grafana/Tempo/Mimir/Loki, Azure Monitor, Datadog, Splunk)? Greenfield vs brownfield changes the exporter/integration work.
3. **Who owns the SIEM and the compliance/retention policy?** Confirms the Layer-3 boundary.
4. **Does the GERNAS platform already define semantic conventions / correlation-ID standards,** or is the OE defining them? (If defining, that's a foundational deliverable.)
5. **What agent framework / LLM stack** is the client on? Determines how much auto-instrumentation is feasible vs manual decorators.
6. **Is a live dashboard expected from the OE, or only the KPI contract** for the VIZ team? (This was a real gap in the reference project.)
