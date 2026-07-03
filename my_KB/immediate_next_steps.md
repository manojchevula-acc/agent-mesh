# Advisory: Top 10 Next Tasks to Implement

## User Question
"As per next_steps_plan.md, which next tasks can I consider for implementation? Up to 10 tasks with priority."

---

## Prioritized Task List

| # | Capability | GERNAS Anchor | Current Status | Why This Priority | Key Files / Effort |
|---|---|---|---|---|---|
| 1 | **Redis Memory Backend** | Agent Runtime – Memory | Partially Implemented (stub exists) | Self-contained: interface + skeleton already exist in `redis_backend.py`. Only 4 methods (~40 lines). Unlocks multi-node session sharing. Zero disruption — single config flip to activate. Unblocks tasks 5 & 6. | `src/memory/redis_backend.py` — Low effort |
| 2 | **Human-in-the-Loop** | Agent Runtime | Not Implemented | Enterprise trust requirement. High-risk decisions (pricing exceptions, compliance overrides) need a pause/approve step before the pipeline proceeds. Adds a new executor in `workflow.py` + a React approval UI component. Critical for prod readiness. | `src/mesh/workflow.py`, new `src/mesh/hitl_executor.py`, new React `ApprovalModal.tsx` — Medium effort |
| 3 | **AI Cost Monitoring** | Unified Model Management – LLM Traffic Observability | Not Implemented | Before scaling, you need cost visibility per user/session/role. Groq API returns token counts in responses. Instrument `ask_remote()` + `agent_factory.py` to capture input/output tokens; extend `metrics.py` with token counters. | `src/a2a/clients.py`, `src/agents/agent_factory.py`, `src/observability/metrics.py` — Low effort |
| 4 | **Alerting & Notifications** | Platform Services – Telemetry | Not Implemented | OTel exporters are already wired to Grafana/Azure Monitor — alerting rules just need to be defined. Add threshold config (node-down, guardrail spike, latency breach) and a notification webhook. Can be done without UI initially (config-driven). | `src/observability/setup.py`, new `src/observability/alert_rules.py`, `.env` webhook config — Low effort |
| 5 | **Agent Lifecycle Management UI** | Discovery Layer – Agent Registry | Partially Implemented | `launch_mesh.py` already spawns processes. Add start/stop/restart controls to `MeshStatusPage.tsx` backed by new API endpoints in `api_server.py`. Surfaces operational control without needing a terminal. Requires Redis (task 1) for clean session hand-off on restart. | `agent-mesh/api_server.py`, `frontend/src/pages/MeshStatusPage.tsx` — Medium effort |
| 6 | **Agent Catalog UI** | Discovery Layer – Agent Discovery | Partially Implemented | `AGENT_REGISTRY` already has names, descriptions, and tool metadata. Just needs a React page to render it — agent cards showing name, model, port, tools, status. Very low backend work; mostly frontend. | `frontend/src/pages/` (new `AgentCatalogPage.tsx`), `frontend/src/components/` — Low effort |
| 7 | **Workflow Version Management** | Agent Runtime | Not Implemented | Introduce a version field to `MeshState` and `workflow.py`. Store workflow schema (stages + config) in a versioned dataclass or YAML. Enables audit trail of which workflow version processed each request. | `src/mesh/workflow.py`, `src/mesh/orchestrator.py`, `data/workflows/` — Medium effort |
| 8 | **Audit & Compliance Reporting** | Platform Services – Telemetry | Partially Implemented | `audit_trail.jsonl` and `trace_log.jsonl` already exist with rich data. Add a report generator (date-range filter, CSV/JSON export) + a React `AuditPage.tsx` to browse and export. Directly addresses regulatory reporting needs for FAB. | `agent-mesh/api_server.py` (new `/api/audit` endpoints), new `frontend/src/pages/AuditPage.tsx` — Medium effort |
| 9 | **AI Governance Management** | Unified Model Management – Guardrails | Partially Implemented | Guardrail rules are hardcoded in `deterministic_filters.py`. Move them to a configurable policy store (JSON/YAML file or DB table). Add a React policy editor UI so compliance officers can update rules without code changes. | `src/guardrails/deterministic_filters.py`, new `src/guardrails/policy_store.py`, new React `GuardrailPolicyPage.tsx` — Medium effort |
| 10 | **Azure AD / OIDC Integration** | User Interface – Identity & Access Management | Partially Implemented | Replace mock `identity_provider.py` with real SSO. `login()` resolves against Azure AD via MSAL or OIDC. Required for enterprise deployment — mock users cannot go to production. Also enables real role assignment tied to corporate directory. | `src/auth/identity_provider.py`, `api_server.py` login route, `.env` OIDC config — High effort |

---

## Recommended Sequencing

**Sprint 1 (Quick wins, build confidence):**
- Task 1 — Redis Backend (self-contained, low risk)
- Task 3 — AI Cost Monitoring (low effort, high value insight)
- Task 4 — Alerting & Notifications (config-driven, no UI needed yet)
- Task 6 — Agent Catalog UI (mostly frontend, no backend changes)

**Sprint 2 (Operational maturity):**
- Task 5 — Agent Lifecycle Management UI (depends on Task 1)
- Task 7 — Workflow Version Management
- Task 8 — Audit & Compliance Reporting

**Sprint 3 (Governance & Enterprise readiness):**
- Task 2 — Human-in-the-Loop (architecture change, needs design)
- Task 9 — AI Governance Management (policy editor)
- Task 10 — Azure AD / OIDC Integration (high effort, enterprise gate)
