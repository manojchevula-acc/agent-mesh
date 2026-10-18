# Gernas SDK — SOW / Enterprise Blueprint compliance checklist

> Authoritative audit record. Root of the single git repository `agent-mesh/` (the VS Code workspace root `telemetry/` is not a repository). Last full re-verification: **2026-10-18** (independent code audit, live re-run `LIVE18A`; earlier rounds 2026-10-16/17).
>
> Detailed requirement → code → test traceability: `gernas-sdk/docs/BLUEPRINT_TRACEABILITY.md` (GERNAS-REQ-001…042).
>
> **Legend:** `[x]` passed and verified · `[~]` partial · `[ ]` not started / not verified · `[!]` failed / noncompliant · `[B]` blocked (external dependency or decision) · `N/A` not applicable (with justification)

## 1. Executive status

**Overall: NOT fully verified against the SOW.**

- The SDK, the Agent Mesh integration, the Collector → Opik path and the instrumentation guide are implemented and verified, including live runs.
- Large parts of the SOW are not delivered by this repository: dashboards, alerting, KPI ownership and thresholds, FinOps allocation, platform / infrastructure telemetry, and the GERNAS OS.
- ADX and AKS are blocked on infrastructure.
- **Five SOW aspects** (blueprint §1, see §3A): A1 OpenTelemetry, A2 semantic conventions, A3 trace/span context, A4 exporter abstraction and A5 gateway / collectors are each **Partial**. In every aspect the SDK-side behaviour is verified; the open rows depend on other owners, ADX / AKS or WP3.
- **Single source of truth:** yes for every runtime path (§4). The one remaining competing implementation is the offline evaluation harness (F-16), which exports nothing today.
- **2026-10-18 live evidence:** `LIVE18A000`–`003` and `MCPPROBE18A`, `opik_verify` PASS ×5; Collector 89 accepted = 89 Opik = 89 data plane, 0 refused; `gernas doctor --opik` PASS.
- **Deliverables (§9A):**
  - `gernas-sdk/docs/GERNAS_SDK_END_TO_END_RUNBOOK.md`;
  - `gernas-sdk/docs/GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html` (generated);
  - `gernas-sdk/docs/SOW_COMPLIANCE_REPORT.md`;
  - `gernas-sdk/docs/INSTRUMENTATION_MAP.md` (generated).

| Requirements (§3 matrix) | `[x]` Passed | `[~]` Partial | `[ ]` Not started | `[!]` Failed | `[B]` Blocked | N/A |
|---|---|---|---|---|---|---|
| **65** | **25** | **15** | **18** | **0** | **5** | **2** |

Counting rules:
- One row per SOW requirement (SOW-001…065).
- `[x]` only if behaviour was tested or observed live, never merely because code or a document exists.
- Rows that need FAB decisions are `[~]`, or `[B]` if nothing else can progress.
- The counts above are recomputed from §3.

**Scope conflict, recorded for confirmation.** The 2026-10-17 re-verification brief and the 2026-10-18 audit brief say *"Opik as the only trace backend… ADX deferred"*. The user's direct instruction on 2026-10-17 was *"collector [must] send traces to dataplane ie Azure Data Explorer… also to opik"*. The direct instruction is followed: traces go to ADX + Opik, metrics and logs go to ADX. To revert to Opik-only, delete the `azuredataexplorer` exporter and the three `*/dataplane` pipelines in `gernas-sdk/collector/otel-collector-config.yaml` and adjust `tests/test_collector_deployment.py`.

## 2. Repository inventory (inspected)

| Item | Path / finding |
|---|---|
| Repository | One git repository `agent-mesh/` (branch `fab-toolkit-oct`). No submodules, no nested `.git`. |
| SOW / blueprint | `docs/GERNAS Enterprise Telemetry Observability Blueprint.docx`, subtitled *"Proposed Accenture Scope of Work for FAB"*: it **is** the SOW. Re-extracted in full: 11 headings, 8 tables, Figure 1 inspected as an image (four numbered layers). Five aspects = §1 *"Instrumentation should primarily use"* (§3A). |
| Derived delivery plans (not authoritative) | `md_files/IMPLEMENTATION_PLAN_WP1.md`, `IMPLEMENTATION_PLAN_WP2.md` (WP3 dashboards, alerting and FinOps as later work packages); `docs/BRIEFING_NOTES.md`, `DEVELOPER_BRIEF.md`, `SPRINT_PLAN.md`, `collector-topology.md` |
| SDK | `gernas-sdk/gernas/`: `__init__` (public API), `config`, `tracer`, `exporters`, `context`, `run_state`, `redaction`, `cost`, `hitl`, `evaluation`, `logs`, `conformance`, `decorators/*`, `adapters/{agent_framework,langchain,langgraph,a2a,mcp,asgi,httpx}`, `cli/{doctor,conformance}`, generated `attributes.py` |
| Registry | `gernas-sdk/registry/` (13 blueprint domains + `20_component_signals` + `30_sdk`, `spans.yaml`, `metrics.yaml`, `denylist.yaml`, `generate.py`) |
| Agent Mesh integration | `agent-mesh/src/observability/gernas_telemetry.py` (single integration module); call sites in §5 |
| Other services | `datalayer-as-service/mcp_server/server.py`, `rag-as-a-service/mcp_integration/server.py`, `rag-as-a-service/src/gernas_rag/main.py` |
| Collector and deployment | `gernas-sdk/collector/{otel-collector-config.yaml, kustomization.yaml, k8s/*.yaml, adx/create-tables.kql, local-dataplane-file.yaml}` |
| Dashboards in repo | **None of the 8 blueprint dashboards.** Present but different in purpose: the mesh React app pages (`agent-mesh/frontend/src/pages/{Trace,Logs,Audit,Approvals,Conversations,Feedback}DashboardPage.tsx`, the mesh's own operational UI) and `agent-mesh/workflow_evaluations/dashboards/*.json` (Grafana JSON for the mesh's evaluation harness) |
| Tests | `gernas-sdk/tests/` (25 files; see §10 for the count; incl. `test_lifecycle`, `test_generated_docs`, `test_examples`, `test_collector_deployment`), `agent-mesh/tests/`, `rag-as-a-service/tests/` |
| Docs | `gernas-sdk/docs/{GERNAS_SDK_END_TO_END_RUNBOOK.md, SOW_COMPLIANCE_REPORT.md, GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html, INSTRUMENTATION_MAP.md, GERNAS_SDK_INSTRUMENTATION_GUIDE.md, collector-aks-deployment.md, kpi-catalogue.md, BLUEPRINT_TRACEABILITY.md, onboarding-runbook.md, attribute-reference.md, adr/*}` |
| CI/CD | None in the repository |
| Tools available | Python 3.14.6, OTel 1.45.1, otelcol-contrib 0.162.0, ruff, mypy. **Not available:** kubectl, helm, docker, az, ADX / Azure credentials, AKS cluster |

## 3. SOW and Enterprise Blueprint compliance matrix

Component key — **SDK**: Gernas SDK · **MESH**: Agent Mesh integration · **COL**: Collector · **OPIK** · **ADX** · **PLAT**: platform / other owner · **DASH**: dashboards (not in repo).

| ID | Source | Requirement | Component / implementation | Gap | Verification and evidence | Status | Corrective action |
|---|---|---|---|---|---|---|---|
| SOW-001 | Purpose | Every AI interaction traceable along Use Case → User → GERNAS OS → Agent → Runtime → Tool → LLM → Knowledge → Infra → Outcome | SDK spans and context, MESH | GERNAS OS and infrastructure links not produced | Live trace `LIVE17E000`: use case → agent → steps → delegate → remote agents → LLM / tool / retrieval | `[~]` | GERNAS OS and infrastructure telemetry are other owners (SOW-011, 017, 032) |
| SOW-002 | §1 | OpenTelemetry instrumentation | SDK (`tracer.py`) | — | `test_packaging`; live runs | `[x]` | — |
| SOW-003 | §1 | Standardized GERNAS semantic conventions | SDK registry → `Ctx` | — | `test_naming`, `test_registry`, `test_no_handtyped_attrs` | `[x]` | — |
| SOW-004 | §1 | Standard trace / span context | SDK `context.py`, adapters | — | `opik_verify` LIVE17E ×4, LIVE18A ×4 (2026-10-18): one trace across up to 5 processes; MCPPROBE17D / MCPPROBE18A | `[x]` | — |
| SOW-005 | §1 | Exporter abstraction, no vendor coupling | SDK exports OTLP only; vendor shape in COL | — | `test_edge_cases::test_exporter_selection`; no vendor SDK in the package (`test_packaging`) | `[x]` | — |
| SOW-006 | §1, Fig. 1 | Centralized telemetry gateway / collectors (AKS) | COL + `k8s/` | Not deployed | `otelcol validate` VALID; k8s v1.30 strict schema 7/7; local Collector live | `[B]` | Deploy per `collector-aks-deployment.md` §2–3 |
| SOW-007 | Fig. 1 | Context enrichment (use case / user / agent / cost / policy) | Done at the source by the SDK (envelope, run context, `request_context`, cost); COL keeps it | No Collector-side registry enrichment (not needed: values present at source) | Conformance PASS on Opik and data-plane copies | `[x]` | — |
| SOW-008 | Fig. 1, §2 | PII / secret detection, filtering, redaction | SDK `redaction.py` + COL denylist / allow-list / masks | — | `test_security` (24); `e2e_pipeline_checks.py` 9/9 | `[x]` | — |
| SOW-009 | Fig. 1 | Sampling / aggregation / routing / transformation | COL tail + probabilistic sampling, fan-out, Opik transform | — | Local Collector: 96 accepted = 96 Opik = 96 data plane | `[x]` | — |
| SOW-010 | Fig. 1 §3 | Telemetry data plane: trace, log and metrics stores, agent-execution, audit and cost datasets | ADX exporter + `adx/create-tables.kql` | No ADX environment | `otelcol validate`; local file substitute | `[B]` | Provision ADX, run the KQL, set `ADX_CLUSTER_URI` |
| SOW-011 | Fig. 1 | Azure / infrastructure telemetry | PLAT (Azure Monitor, Container Insights) | Not in repo | Inspection | `[ ]` | Platform team |
| SOW-012 | Fig. 1 | API / tool telemetry | SDK `trace_tool`, MCP adapters (tools); API gateway is PLAT | API gateway not instrumented | `test_decorators`; MCP probe | `[~]` | APIM diagnostics → Collector (platform) |
| SOW-013 | §2 table | 13 canonical context domains with exact fields | SDK registry | — | `test_naming::test_every_blueprint_context_field…` | `[x]` | — |
| SOW-014 | §2 critical principle | No raw prompts, responses, customer data, account numbers, secrets or tokens | SDK + COL | — | `test_security`; Opik copy has no payload keys | `[x]` | — |
| SOW-015 | §2 | Sensitive payload capture only under policy, redacted, encrypted | No capture path in SDK; MAF sensitive capture disabled | — | `test_security::test_prompts_completions…`; `.env` `ENABLE_SENSITIVE_DATA=false` | `[x]` | — |
| SOW-016 | §3 Use Case Layer | Use case, application, channel, domain, user / session, request type, outcome | SDK `trace_use_case`, `request_context` | — | `test_decorators`, live | `[x]` | — |
| SOW-017 | §3 GERNAS OS | Agent registration, deployment, version, config change, ownership, certification, lifecycle | PLAT (GERNAS OS / Agent Studio) | Not in repo | Inspection | `[ ]` | GERNAS OS team; the SDK can carry `agent_version` |
| SOW-018 | §3 Agent Runtime | Run start / end, planning, steps, retries, loops, decisions, delegation, termination | SDK `trace_agent`, `RunState`, `trace_planning` | — | `test_runtime`, `test_kpi_metrics`, live | `[x]` | — |
| SOW-019 | §3 Multi-Agent | Parent / child, delegation path, inter-agent calls, latency, failures, hand-offs | SDK `trace_delegation`, A2A interceptor, ASGI | — | Live: 3–4 delegations per request, remote `parent_run_id` | `[x]` | — |
| SOW-020 | §3 Skills / Tools | Count, latency, status, authorization decision, payload size, dependency | SDK `trace_tool` + metrics | — | `test_decorators`, `test_metrics`, MCP probe | `[x]` | — |
| SOW-021 | §3 API Gateway | Requests, consumers, latency, 2xx / 4xx / 5xx, throttling, quota | PLAT (APIM) | Not in repo | Inspection | `[ ]` | APIM telemetry (platform) |
| SOW-022 | §3 LLM Gateway | Routing, provider / model, tokens, latency, retries, fallback, throttling | SDK client-side `llm.call` (provider, model, tokens, latency, retry, fallback, `error_category=rate_limit`) | Gateway-side routing not observable from the agent | `test_decorators`, `test_kpi_metrics` | `[~]` | Gateway telemetry (platform) |
| SOW-023 | §3 LLMs | TTFT, generation latency, tokens, tokens / s, stop reason, model errors | SDK `trace_llm` (TTFT and tokens / s on streams) | TTFT only for streaming calls | `test_runtime`, `test_kpi_metrics` | `[x]` | — |
| SOW-024 | §3 Embedding | Calls, latency, token volume, batch size, errors | SDK `trace_embedding` | Not exercised live (RAG store down) | `test_decorators::test_embedding` | `[x]` | — |
| SOW-025 | §3 Content Safety | Blocked request, category, risk level, policy triggered | SDK `trace_content_safety` | — | `test_decorators`, live (`output-redaction`) | `[x]` | — |
| SOW-026 | §3 RAG / Search | Duration, query rewriting, top-K, result count, empty result, ranking score | SDK `trace_retrieval` | — | `test_decorators::test_retrieval`, live (`cache-check`) | `[x]` | — |
| SOW-027 | §3 Vector Store | Query latency, index size, ingestion rate, failed indexing, freshness | PLAT (Qdrant / Azure AI Search metrics) | Not in repo | Inspection | `[ ]` | Platform |
| SOW-028 | §3 Document Processing | Documents received / parsed / rejected, OCR latency, chunks, failures | SDK `trace_document` → `document.process` (`document_type`, `document_status` parsed / rejected / failed, `chunk_count`), metrics `gernas.documents` + `gernas.document.chunks`; extraction (OCR) latency = `agent.step` `step_type=document_extraction`. RAG `IngestionPipeline.ingest_file` instrumented (SDK optional there) | No live ingestion run exported to the data plane; the RAG pipeline has no reject path, so `rejected` is never emitted there | `test_document_processing` (4 outcomes); RAG `test_ingestion_pipeline` (error path through the decorators); scratch span check 2026-10-18 | `[~]` | Live ingestion run (needs Docling + Qdrant) and a dashboard tile |
| SOW-029 | §3 Knowledge Layer | Source accessed, retrieval domain, authorization decision, document version | SDK `source_id`, `knowledge_domain`, `document_version` (read from the top result), `authorization_decision` parameter on `trace_retrieval` | RAG chunk metadata has no version field and the store enforces no document-level access, so neither value is emitted by the RAG MCP server yet | `test_decorators::test_retrieval`, `test_document_processing::test_retrieval_*` | `[~]` | RAG team: add a version to chunk metadata; FAB: knowledge access policy |
| SOW-030 | §3 Memory | Read / write, hit, stale, size | SDK `trace_memory` | — | `test_decorators`, live | `[x]` | — |
| SOW-031 | §3 Database | Saturation, query duration, failures, throughput | PLAT (Azure DB metrics) | Not in repo | Inspection | `[ ]` | Platform |
| SOW-032 | §3 AKS / Containers | CPU, memory, pod lifecycle, OOM, restarts, HPA, nodes | PLAT (Container Insights) | Not in repo | Inspection | `[ ]` | Platform |
| SOW-033 | §3 GPU | GPU utilization, memory, throughput, queue, idle | — | — | No GPU in the reference use case | N/A | Platform if GPUs are adopted |
| SOW-034 | §3 Messaging | Queue depth, age, throughput, dead-letter, lag | — (SDK supports `inject_context` / `attach_headers` for queues) | — | No messaging in the reference use case | N/A | Platform if adopted |
| SOW-035 | §3 Network | Connectivity, latency, DNS, ingress / egress, failures | PLAT | Not in repo | Inspection | `[ ]` | Platform |
| SOW-036 | §3 Identity | Authentication, token issuance / rejection, principal and workload identity | SDK `principal_type`, `tokenized_user_id`; IdP events PLAT | IdP events missing | `test_context` | `[~]` | Entra ID logs → data plane (platform) |
| SOW-037 | §3 Authorization | RBAC / ABAC decisions, denied actions, escalation attempts | SDK `authorization_decision`, `policy_decision`; mesh `RBACValidationExecutor` role check is a `guardrail.check` (`policy_id=rbac_role_check`, allow / deny, deny → `policy_block`) | No escalation-attempt signal; RBAC *deny* not observed live (`login()` maps unknown users to `customer`, so it is reachable only programmatically) | `test_decorators`; scratch span check 2026-10-18 (allow + deny); **live** `rbac_role_check` allow ×3 in LIVE18A (data plane) | `[~]` | Define an escalation signal (FAB) |
| SOW-038 | §3 CI/CD | Deployment version, release, rollback, drift | PLAT | Not in repo | Inspection | `[ ]` | Pipeline events (platform) |
| SOW-039 | §3 Telemetry Platform | Collector health, dropped spans / logs, ingestion rate, storage utilization | COL `health_check` + `:8888` metrics; SDK `gernas.sdk.dropped_*` | Storage utilization needs ADX | Local Collector metrics; `test_collector_down` | `[~]` | ADX storage metrics |
| SOW-040 | §4 | Each KPI: definition → formula → source → owner → threshold → SLO → dashboard → alert → retention → interpretation | `gernas-sdk/docs/kpi-catalogue.md` (definition, formula, source) | Owner, threshold, SLO, alert and retention not set; KQL unexecuted | Document review | `[~]` | FAB to set; validate KQL on ADX |
| SOW-041 | §4A | Adoption KPIs (active users, use cases, agents, executions, outcomes, trend, sessions / user) | Signals: SDK spans + `gernas.use_case.requests` | Computation needs the data plane | `test_kpi_metrics`; live data-plane metrics | `[B]` | ADX |
| SOW-042 | §5 | 16 agentic KPIs: signals | SDK metrics and attributes (`kpi-catalogue.md` 5-1…5-16) | — | `test_runtime`, `test_metrics`, `test_kpi_metrics`, live | `[x]` | — |
| SOW-043 | §5 | Cost per successful agent task | SDK `estimated_cost` + `business_outcome` | Only LLM + embedding cost | `test_decorators::test_llm_call…` | `[~]` | §11 components from platform metering |
| SOW-044 | §6 Performance | Volume, TTFT, latency, tokens / s, tokens, context size, cache, timeout, rate limit, retry, fallback, PTU | SDK `llm.call` + metrics | Context size not captured; PTU is a gateway / Azure metric | `test_kpi_metrics` | `[~]` | Context-size field (decision); PTU from Azure |
| SOW-045 | §6 Quality | Groundedness … refusal rate, where evaluation is enabled | SDK `record_evaluation` | Not used by the mesh runtime (evaluation harness is offline mesh tooling, F-16) | `test_kpi_metrics::test_record_evaluation_*`; `examples/custom_agent.py` | `[x]` | — |
| SOW-046 | §6 Consumption | Every model call attributable to use case → agent → run → user → model → cost | SDK envelope + run context + tokenized user + model + cost | — | Conformance on Opik copy; `test_adapter_*` | `[x]` | — |
| SOW-047 | §10 D1 | Executive AI platform dashboard | DASH | Not built | — | `[ ]` | WP3 dashboards on ADX |
| SOW-048 | §10 D2 | Platform operations (RED / USE) dashboard | DASH | Not built | — | `[ ]` | WP3 |
| SOW-049 | §10 D3 | Agent performance dashboard | DASH | Not built | — | `[ ]` | WP3 |
| SOW-050 | §10 D4 | LLM / AI consumption dashboard | DASH | Not built | — | `[ ]` | WP3 |
| SOW-051 | §10 D5 | RAG / knowledge dashboard | DASH | Not built | — | `[ ]` | WP3 |
| SOW-052 | §10 D6 | Security and AI governance dashboard | DASH | Not built | — | `[ ]` | WP3 |
| SOW-053 | §10 D7 | FinOps dashboard (domain → use case → app → agent → run → model / tool / infra) | DASH | Not built | — | `[ ]` | WP3 |
| SOW-054 | §10 D8 | Capacity dashboard | DASH | Not built | — | `[ ]` | WP3 |
| SOW-055 | §10 | Drill-down Use Case → Agent → Version → Run → Step → Span | Data model: SDK spans and attributes | Dashboard not built | Live hierarchy in Opik (`LIVE17E000` tree) | `[~]` | WP3 |
| SOW-056 | §10 D2 | RED and USE methodology | DASH + metrics | Not built | — | `[ ]` | WP3 |
| SOW-057 | §11 | Cost model: LLM + embedding + search + doc processing + tool / API + compute + storage + network + shared allocation | SDK: LLM + embedding cost | 7 of 9 components missing | `test_edge_cases::test_cost_lookup_edges` | `[~]` | FinOps model in the data plane (WP3-D) |
| SOW-058 | §11 | Cost KPIs (per use case / BU / user / agent / run / success / model / 1K interactions, idle cost, reserved utilization, peak / avg, headroom) | — | Not built | — | `[ ]` | WP3-D |
| SOW-059 | §21 | GERNAS telemetry graph: business → use case → app → agent → version → run → tool / API / knowledge / model / infra → cost → compliance → outcome | SDK trace graph | Infra, compliance and full cost nodes missing | Live traces | `[~]` | Platform + FinOps |
| SOW-060 | §21 contract | FAB-owned model, correlation standard, KPI catalogue, dashboards-as-code, alert rules, instrumentation, runbooks; vendor change without re-instrumenting | SDK + registry + docs + COL vendor mapping | Dashboards-as-code, alert rules and runbooks not produced | Exporter abstraction verified; registry and docs in repo | `[~]` | WP3 |
| SOW-061 | Programme brief | One model for MAF, LangChain, LangGraph and custom | SDK adapters | — | `test_adapter_*`, `test_examples` ×4 | `[x]` | — |
| SOW-062 | Programme brief | Collector runs in an AKS pod | COL `k8s/` | Not deployed | Schema validation | `[B]` | AKS access |
| SOW-063 | Programme brief | Collector → Opik | COL `otlphttp/opik` (Opik docs: OTLP/HTTP, traces only) | — | `opik_verify` PASS ×5 (LIVE17E000-003, MCPPROBE17D) and ×5 on 2026-10-18 (LIVE18A000-003, MCPPROBE18A) | `[x]` | — |
| SOW-064 | User instruction 2026-10-17 | Collector → ADX (data plane) | COL `azuredataexplorer` | No ADX | `otelcol validate` | `[B]` | ADX environment |
| SOW-065 | Programme brief | Instrumentation guide for new use cases | `gernas-sdk/docs/GERNAS_SDK_INSTRUMENTATION_GUIDE.md` | — | APIs checked against `inspect.signature`; examples executed by `tests/test_examples.py`; end-to-end runbook commands executed 2026-10-18 | `[x]` | — |

## 3A. The five SOW aspects

**Source:** blueprint §1 *Target Telemetry Reference Architecture*: *"Instrumentation should primarily use: OpenTelemetry · Standardized GERNAS semantic conventions · Standard trace/span context · Exporter abstraction · Centralized telemetry gateway/collectors"*.

This is the only five-item capability list in the SOW. Figure 1 has **four** numbered layers (instrumentation; collection and processing; data plane; observability, analytics and operations), so it is not the five-aspect grouping.

Every SOW row of §3 is assigned to exactly one aspect, or to **X**: SOW scope outside the five instrumentation aspects. X covers Figure 1 layers 3–4, other telemetry producers, KPI governance and FinOps.

Aspect status is derived from the member rows and is never set by hand:
- **Verified:** all rows `[x]`.
- **Failed:** any `[!]`.
- **Partial:** anything else.

`scripts/build_architecture_doc.py` recomputes it.

| Aspect | SOW §1 principle | What it means for this repository | SOW rows |
|---|---|---|---|
| A1 | OpenTelemetry | Every GERNAS component operation is instrumented with OTel spans / metrics / logs through the Gernas SDK | SOW-002, SOW-012, SOW-016, SOW-018, SOW-020, SOW-022, SOW-023, SOW-024, SOW-025, SOW-026, SOW-028, SOW-029, SOW-030, SOW-036, SOW-037, SOW-061, SOW-065 |
| A2 | Standardized GERNAS semantic conventions | One registry of blueprint field names, enums, span and metric names; PDPL rules on what may be recorded | SOW-003, SOW-013, SOW-014, SOW-015, SOW-042, SOW-043, SOW-044, SOW-045, SOW-046 |
| A3 | Standard trace/span context | One correlated trace per request across processes, agents, tools, MCP; run / session / task context on every span | SOW-001, SOW-004, SOW-007, SOW-019, SOW-055, SOW-059 |
| A4 | Exporter abstraction | Producers export OTLP only; backends are Collector configuration, replaceable without re-instrumenting | SOW-005, SOW-060, SOW-063, SOW-064 |
| A5 | Centralized telemetry gateway/collectors | The GERNAS Collector (AKS) receives, redacts, samples, routes; data plane behind it | SOW-006, SOW-008, SOW-009, SOW-010, SOW-039, SOW-062 |
| X | Outside the five aspects | Platform telemetry sources, KPI governance, dashboards, alerting, FinOps (Figure 1 layers 3–4 and other producers) | SOW-011, SOW-017, SOW-021, SOW-027, SOW-031, SOW-032, SOW-033, SOW-034, SOW-035, SOW-038, SOW-040, SOW-041, SOW-047, SOW-048, SOW-049, SOW-050, SOW-051, SOW-052, SOW-053, SOW-054, SOW-056, SOW-057, SOW-058 |

**Derived status (2026-10-18):**

| Aspect | `[x]` | `[~]` | `[ ]` | `[!]` | `[B]` | N/A | Status | Why not Verified |
|---|---|---|---|---|---|---|---|---|
| A1 | 11 | 6 | 0 | 0 | 0 | 0 | Partial | Rows that depend on other owners: API gateway (012), LLM gateway side (022), RAG data (028, 029), IdP events (036), escalation signal (037) |
| A2 | 7 | 2 | 0 | 0 | 0 | 0 | Partial | Cost components beyond LLM / embedding (043); context size and PTU (044) |
| A3 | 3 | 3 | 0 | 0 | 0 | 0 | Partial | GERNAS OS and infrastructure links (001, 059); drill-down dashboard (055) |
| A4 | 2 | 1 | 0 | 0 | 1 | 0 | Partial | Dashboards-as-code / alert rules (060); ADX unavailable (064) |
| A5 | 2 | 1 | 0 | 0 | 3 | 0 | Partial | AKS and ADX unavailable (006, 010, 062); storage utilization (039) |
| X | 0 | 2 | 18 | 0 | 1 | 2 | Not delivered | Other owners / WP3 |

Per-aspect evidence (implementation, exact source lines, tests, runtime results, gaps) is in `gernas-sdk/docs/SOW_COMPLIANCE_REPORT.md` §3.

## 4. Single-source-of-truth audit

| Capability | Central implementation (SDK) | Duplicated elsewhere? | Status |
|---|---|---|---|
| Init, config, lifecycle | `init_gernas`, `GernasConfig`, `GernasHandle` | No (mesh `setup.py` removed) | `[x]` |
| Providers, resource | `tracer.py` | No (`grep` of mesh runtime: no provider code) | `[x]` |
| Spans, hierarchy, correlation | decorators + adapters | No (mesh `fab.*` / `mesh.request` spans removed) | `[x]` |
| Context and baggage propagation | `context.py`, `GernasBaggagePropagator`, A2A / ASGI / httpx / MCP adapters | Mesh `fab.*` baggage kept as **application** business context (audit trail, peer-reasoning files), carried over the SDK propagator and never written to telemetry | `[x]` (justified) |
| Attributes and semantic conventions | `registry/` → `attributes.py` | No | `[x]` |
| Agent, workflow, tool, MCP, hand-off instrumentation | decorators + adapters | No | `[x]` |
| Errors and exceptions | `_core.stamp_outcome`, `record_exception` | No | `[x]` |
| Metrics | `_metrics.py` + `registry/metrics.yaml` | Mesh `fab.*` metrics removed. **Remaining:** `agent-mesh/workflow_evaluations/workflow/grafana_push.py` pushes evaluation metrics to Grafana directly (offline harness, F-16) | `[~]` |
| Logs and correlation | `logs.py` bridge, `correlation()` | No (mesh logging reads `gernas.correlation()`) | `[x]` |
| Export / Collector | OTLP only | No direct exporters in mesh or services (search) | `[x]` |
| Framework-native duplicates | `disable_native_telemetry()` (MAF), `GERNAS_SUPPRESSED_SCOPES` (fastmcp); a2a-sdk via env | Verified absent live ("only Gernas spans" ×5) | `[x]` |
| Evaluation telemetry | `record_evaluation` | Mesh harness `trace_linker.py` emits its own span events (F-16) | `[~]` |
| Reusability (no mesh coupling) | — | SDK package has 0 mesh names; no mesh imports | `[x]` |

## 5. Instrumentation coverage (Agent Mesh)

Exact file:line for all 67 call sites (Agent Mesh, DataLayer, RAG) is generated into `gernas-sdk/docs/INSTRUMENTATION_MAP.md` §2; the per-row line numbers are in `SOW_COMPLIANCE_REPORT.md` §6.

| Location | SDK API | Agent Mesh usage | Verified |
|---|---|---|---|
| Startup / shutdown | `init_gernas`, `handle.shutdown()` | `gernas_telemetry.init_telemetry` in `run.py`, `api_server.py`, `a2a_server.py`, `devui_app.py`, `demos/` | `[x]` live |
| Request entry | `trace_use_case` + `request_context` | `src/mesh/orchestrator.py` `handle_request` | `[x]` live |
| Workflow run / steps | `trace_agent` / `trace_step` | `orchestrator._run_workflow`; 6 executors in `src/mesh/workflow.py` | `[x]` live |
| Agent (MAF) + LLM + tools | `gernas_middleware` | `src/agents/agent_factory.py` | `[x]` live |
| Raw LLM calls | `trace_llm` | `gernas_telemetry.chat_post` (summarizer, cache judge, entity extractor) | `[x]` live (summary) |
| Tools (peer delegation) | `trace_tool` | `src/tools/collaboration_tools.py` | `[x]` live |
| A2A hand-off | `trace_delegation` + `GernasA2AInterceptor` + `GernasASGIMiddleware` | `src/a2a/clients.py`, `src/a2a/hosting.py`, `api_server.py` | `[x]` live (5 processes) |
| MCP client | MAF function middleware (`mcp_server=`) | data and rag agents | `[~]` mesh path not reached live (model 404); same mechanism verified by MCP probe |
| MCP servers | `GernasFastMCPMiddleware`, `mcp_meta_context` + `trace_retrieval` | `datalayer-as-service/mcp_server/server.py`, `rag-as-a-service/mcp_integration/server.py` | `[x]` DataLayer live (MCPPROBE17D, MCPPROBE18A); RAG `[ ]` live (store down). Servers need `GERNAS_*` in their environment (F-34) |
| Retrieval (cache) | `trace_retrieval` | `src/cache/semantic_cache.py` | `[x]` live |
| Memory | `trace_memory` | `src/memory/conversation_store.py` | `[x]` live |
| Guardrails / safety | `trace_guardrail`, `trace_content_safety` | `src/guardrails/deterministic_filters.py`; RBAC role check in `src/mesh/workflow.py` (`rbac_role_check`) | `[x]` live (input screen allow + deny); RBAC allow `[x]` live (LIVE18A), deny `[x]` scratch check only |
| HITL | `start_hitl` / `complete_hitl` | `src/hitl/approval_store.py` | `[x]` unit; `[ ]` live (requires an approver) |
| Background tasks | `gernas_task` | `orchestrator` summarizer `spawn` | `[x]` live (summary step in trace) |
| Log correlation | `correlation()` | `src/observability/logging_config.py`, `src/middleware/audit_middleware.py` | `[x]` unit |

## 6. Collector and Opik readiness

| Check | Status | Evidence |
|---|---|---|
| Config syntax (otelcol-contrib 0.162.0) | `[x]` | `validate` VALID (AKS mode with `ADX_CLUSTER_URI`; local overlay) |
| Receiver / exporter compatibility | `[x]` | OTLP gRPC / HTTP receivers; Opik OTLP/HTTP per Opik docs; `azuredataexplorer` exporter present (Beta) |
| SDK → Collector | `[x]` | Live: 96 spans accepted, 0 refused (2026-10-17); 89 accepted, 0 refused (2026-10-18, LIVE18A) |
| Collector processing (PDPL, sampling, shape) | `[x]` | `e2e_pipeline_checks.py` 9/9 |
| Export to Opik | `[x]` | `otelcol_exporter_sent_spans{otlphttp/opik}` = 96, 0 failed; = 89 on 2026-10-18 (data-plane file 89) |
| Trace visibility, ids, parent-child, attributes in Opik | `[x]` | `opik_verify` ×5 (2026-10-17) and ×5 (2026-10-18) |
| No duplicate spans | `[x]` | "only Gernas SDK spans" ×5; conformance `duplicate` rule |
| Error behaviour | `[x]` | `data_agent` / `rag_agent` 404 → `execution_status=error`, `termination_reason=error` in Opik |
| Shutdown flush | `[x]` | `handle.shutdown()` in demo; spans of the last request present |
| Health / diagnostics | `[x]` local | `:13133` ready; `:8888` metrics; `gernas doctor --opik` PASS 5/5 (2026-10-18) |
| Retry / queue / backpressure | `[~]` | Configured; Collector-down tested SDK-side (`test_collector_down`); Collector → backend outage not simulated |
| Export to ADX | `[B]` | No ADX |
| AKS deployment | `[B]` | No cluster access; manifests schema-valid; steps and acceptance in `collector-aks-deployment.md` |

## 7. Dashboards and supporting capabilities

| Capability (SOW) | Status | Note |
|---|---|---|
| 8 dashboards (§10) | `[ ]` | Not in repo; planned WP3. Mesh React pages and evaluation Grafana JSON are mesh-specific, not the blueprint dashboards. |
| Drill-down / filters / time range / trace search | `[~]` | Trace search and inspection exist in Opik (verified). Dashboard drill-down not built. |
| KPI catalogue (§4) | `[~]` | `kpi-catalogue.md`: definitions, formulas, sources. Owner / threshold / SLO / retention TBD (FAB). |
| Metrics emitted for KPIs | `[x]` | 26 instruments (`registry/metrics.yaml`) |
| Logs with trace correlation | `[x]` | OTel logs bridge (`logs.py`) → Collector → logs pipeline; `correlation()` for app logs |
| Alerts / thresholds | `[ ]` | Not built (WP3-C) |
| FinOps allocation (§11) | `[~]` | LLM + embedding cost only |
| Dashboard data access | `[B]` | ADX tables defined; no ADX |
| Mesh Trace Dashboard page | `[!]` (mesh) | Fed by the removed raw-payload JSONL sink (F-20); now shows no new data. Mesh-team decision: point it at Opik / ADX, or remove it |

## 8. Implementation findings and fixes (this re-verification)

| ID | Sev | File | Observed | Expected | Action | Verification |
|---|---|---|---|---|---|---|
| F-20 | High (mesh UX) | `agent-mesh/api_server.py` `/api/traces`, `frontend/.../TraceDashboardPage.tsx` | Fed by `data/trace_log.jsonl` from the removed raw-payload sink | Trace view from the GERNAS trace stores | **Open**, mesh-team decision; not re-adding a PDPL-violating sink | Inspection |
| F-21 | High | `gernas/context.py`, `decorators/_core.py` | MCP server span (other process) had no `agent_id` / `agent_run_id`: run context only travelled through `inject_context` | Run context on every propagation path | `GernasBaggagePropagator` + baggage fallback in `stamp_context` | `test_context::test_plain_otel_injection…`; MCPPROBE17D PASS |
| F-22 | Medium | DataLayer (fastmcp) | fastmcp `tools/call` span duplicated the server `tool.call` | One span per operation | `GERNAS_SUPPRESSED_SCOPES` (SDK, opt-in) + set in `mcp_server/server.py` | `test_edge_cases::test_suppressed_scopes…`; MCPPROBE17D "only Gernas spans" |
| F-23 | Medium | `gernas/conformance.py` | Client→server pair flagged as duplicate | Pairs exempt | Span kind + MCP fallback | `test_conformance::test_client_server_pair…` |
| F-24 | Low | `gernas/_metrics.py` | Every histogram created with unit `ms` | Registry units | `METRIC_UNITS` generated | `test_kpi_metrics::test_histogram_units…` |
| F-25 | Medium | — | No §6 Quality evaluation API | Evaluators record through the SDK | `gernas/evaluation.py` `record_evaluation` + `evaluation.score` span + metric | `test_kpi_metrics` |
| F-26 | Medium | `registry/metrics.yaml` | §5 / §6 KPIs without instruments: TTFT, tokens / s, planning latency, steps / run, fallback rate, use-case outcome | Metric per KPI | 6 instruments added and wired | `test_kpi_metrics`; live data-plane metrics |
| F-27 | Medium | — | No KPI catalogue (§4) | Catalogue | `docs/kpi-catalogue.md` | Review; KQL unexecuted |
| F-28 | High (scope) | — | Dashboards, alerts and FinOps not implemented | SOW §10, §11 | **Open** (WP3) | — |
| F-30 | Medium | `rag-as-a-service/.../ingestion/pipeline.py`, `gernas/decorators/document.py` | No document-processing telemetry (SOW-028) | `document.process` per document | `trace_document` + registry fields / span / 2 metrics; RAG ingestion decorated | `test_document_processing`; RAG suite unchanged |
| F-31 | Medium | `gernas/decorators/retrieval.py` | No document version / knowledge authorization on retrieval (SOW-029) | Fields when available | `document_version`, `authorization_decision` | `test_document_processing` |
| F-32 | Medium | `agent-mesh/src/mesh/workflow.py` | RBAC decision not emitted as a policy decision (SOW-037) | `guardrail.check` | `_role_recognised` decorated with `trace_guardrail("rbac_role_check")`; logic unchanged | Scratch check; mesh suite |
| F-33 | High | `gernas-sdk/gernas/decorators/_core.py` | An object with `async def __call__` (or a sync callable returning a coroutine) got a span that closed before the work ran: 0.9 ms, OK, while the work took 50 ms and raised | Span covers the awaited work; ERROR on failure | `wrap` treats async-callable objects as coroutine functions; `_await_in_span` keeps the span of a coroutine-returning sync call open until completion (a Task / Future is returned unchanged) | `tests/test_lifecycle.py`: 2 tests fail before, pass after; full suite |
| F-34 | Medium (operational) | DataLayer / RAG MCP servers | Started without `GERNAS_*` → "Gernas SDK telemetry disabled" (correctly refused to invent identity); their spans missing from the trace | Same identity as the use case | `gernas-sdk/scripts/load_env.ps1` + runbook Part C / D; `scripts/mcp_probe.py` added | MCPPROBE18A PASS |
| F-16 | Medium | `agent-mesh/workflow_evaluations/evaluators/trace_linker.py`, `workflow/grafana_push.py` | Offline evaluation harness creates its own `fab.eval.*` spans / pushes to Grafana; never calls `init_telemetry`, so spans go to the no-op provider | Evaluators record through `gernas.record_evaluation` | **Open**, mesh-team decision: map its 20 evaluator names onto the 8 §6 metrics | Inspection 2026-10-18 |
| F-29 | Decision | Brief vs user instruction | Opik-only vs ADX + Opik | One decision | Followed the user's direct instruction; recorded in §1 | — |
| F-01…F-19 (except F-16) | — | (earlier audit, all fixed or justified) | see `BLUEPRINT_TRACEABILITY.md` §5–7 and the git history of this file | — | — | — |

## 9. Instrumentation guide

| Item | Status | Evidence |
|---|---|---|
| `gernas-sdk/docs/GERNAS_SDK_INSTRUMENTATION_GUIDE.md` (replaces `sdk-developer-guide.md`; references updated) | `[x]` | Sections A–H: architecture (Mermaid), install, init and shutdown, where-to-instrument table, propagation with correct / incorrect examples, examples, troubleshooting, adoption checklist |
| API accuracy | `[x]` | Every signature cross-checked with `inspect.signature` on 0.4.0 |
| Examples runnable | `[x]` | `tests/test_examples.py`: 4 examples run in fresh processes and emit the documented spans |
| Collector / Opik commands | `[x]` | Commands used in §10 of this file |

## 9A. Runbook, HTML and report deliverables (2026-10-18)

| Deliverable | Status | Evidence |
|---|---|---|
| `gernas-sdk/docs/GERNAS_SDK_END_TO_END_RUNBOOK.md` | `[x]` local / `[B]` AKS part | Parts A–F executed on 2026-10-18 (install, configure, Collector, services, workflow, Opik / Collector verification, tests); Part G (AKS) repeats `collector-aks-deployment.md`, not executed |
| `gernas-sdk/docs/GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html` | `[x]` | Generated by `gernas-sdk/scripts/build_architecture_doc.py` from the source (ast), `registry/spans.yaml` and this checklist; offline (inline CSS / JS / SVG); rendered and inspected in headless Edge; `tests/test_generated_docs.py` fails on drift |
| `gernas-sdk/docs/INSTRUMENTATION_MAP.md` | `[x]` | Same generator: API catalogue with definition lines, 67 call sites with exact lines |
| `gernas-sdk/docs/SOW_COMPLIANCE_REPORT.md` | `[x]` | Five-aspect sections with SOW rows, implementation, call sites, tests, results, gaps; consistent with §3 / §3A |
| Sequence diagrams | `[x]` | 10 diagrams with exact source names; 9 observed live (LIVE18A, MCPPROBE18A, Collector counters; in the Collector diagram only the Opik and local data-plane branches, not ADX); 1 source-derived (initialisation internals) |

## 10. Test execution log (2026-10-17, Windows 11, Python 3.14.6, OTel 1.45.1, otelcol-contrib 0.162.0)

| # | Command | Result |
|---|---|---|
| 1 | `cd gernas-sdk; python -m pytest tests --cov=gernas` | **215 passed**, 91.42 % coverage (gate 90 %), re-run 2026-10-18 |
| 2 | `ruff check gernas tests registry scripts benchmarks examples`; `ruff format --check …`; `mypy gernas` | clean; formatted; no issues (42 files) |
| 3 | `python registry/generate.py --check` | up to date |
| 4 | `otelcol-contrib validate` (AKS config with `ADX_CLUSTER_URI`; `run_collector.py --validate`) | VALID / VALID |
| 5 | k8s v1.30 strict JSON-schema validation of the rendered kustomization | 7/7 VALID |
| 6 | `python -m build` + clean venv install + `pip check` | OK (re-run after these changes: §11) |
| 7 | Live MCP probe (MAF `MCPStreamableHTTPTool` → DataLayer fastmcp, `MCPPROBE17C`) → `opik_verify.py` | FAIL: missing run context on the server span, false duplicate, fastmcp span → F-21 / F-22 / F-23 |
| 8 | Same after the fixes (`MCPPROBE17D`) | **PASS** (one trace, run context on the server span, only Gernas spans) |
| 9 | Live Agent Mesh (Collector + DataLayer + RAG MCP + 4 A2A nodes + Groq), `demos/gernas_e2e_queries.py --prefix LIVE17E` → `opik_verify.py` ×4 | **PASS ×4**. Collector: accepted 96 = Opik 96 = data plane 96, 0 refused. New KPI metrics present in the data plane. |
| 10 | Agent Mesh offline suite | Unchanged from the pre-change baseline (5 known failures; rbac_matrix hangs, as before) |
| 11 | `pytest rag-as-a-service/tests` (re-run 2026-10-18 after F-30) | 26 passed, 1 failed (missing input PDF, F-18; unrelated) |

**2026-10-18 independent audit** (same environment):

| # | Command | Result |
|---|---|---|
| 12 | Probe: async-callable object and coroutine-returning sync function under `trace_tool` | **FAIL**: span 0.9 ms, OK, while the work took 50 ms and raised → F-33 |
| 13 | `pytest tests/test_lifecycle.py` against the unfixed `_core.py` / fixed `_core.py` | 2 failed, 3 passed / 5 passed |
| 14 | `cd gernas-sdk; python -m pytest tests --cov=gernas` (after F-33, new tests, generated docs) | **221 passed**, 91.58 % coverage (gate 90 %); includes `test_lifecycle` (5) and `test_generated_docs` |
| 15 | `ruff check gernas tests registry scripts benchmarks examples`; `ruff format --check gernas tests scripts`; `mypy gernas`; `registry/generate.py --check`; `scripts/build_architecture_doc.py --check` | clean: ruff all checks passed (72 files formatted); mypy no issues (43 files); registry up to date; architecture docs up to date |
| 16 | `cd agent-mesh; python -m pytest tests -q --ignore=tests/test_rbac_matrix.py` | 87 passed, 5 failed: the known pre-existing baseline failures (`test_rbac_blocks_invalid_role`, cache `test_04/06/11`, `test_ingest_wrapper_delegates`). `--timeout` is unavailable (`pytest-timeout` not installed) |
| 17 | `cd rag-as-a-service; python -m pytest tests -q` | 26 passed, 1 failed (`test_ingest_file_end_to_end`, missing input PDF; pre-existing) |
| 18 | Live: Collector + DataLayer + RAG MCP + `launch_mesh.py`; `demos/gernas_e2e_queries.py --prefix LIVE18A` → `opik_verify.py` ×4 | **PASS ×4**. Collector 89 accepted = 89 Opik = 89 data plane, 0 refused. Data plane: `rbac_role_check` allow ×3, `input_screen` deny ×1 (status OK) |
| 19 | Same run: MCP servers' logs | Both logged "Gernas SDK telemetry disabled: Missing required … GERNAS_USE_CASE_ID…" (started without `GERNAS_*`) → F-34 |
| 20 | DataLayer MCP started with `load_env.ps1`; `scripts/mcp_probe.py MCPPROBE18A` → `opik_verify.py` | **PASS** (client + server `tool.call` in one trace, run context on the server span) |
| 21 | `python -m gernas doctor --opik` | PASS 5/5 (configuration, collector, export, 12/12 context, Opik) |
| 22 | `run_collector.py --validate`; `inspect_trace.py <trace>` | exit 0; tree + summary printed |
| 23 | Headless Edge screenshots of the generated HTML (overview, flowchart, sequence diagrams, SOW dashboard) | Rendered offline; label layout fixed after first inspection |

## 11. Git hygiene

Repository-wide changes are tracked in git with an anchored `/observability/` rule. Earlier hygiene work is preserved:
- generated reports and trace dumps untracked;
- IDE file untracked;
- `gernas_telemetry.py` now tracked;
- secret scan clean.

This round:
- `docs/sdk-developer-guide.md` renamed to `GERNAS_SDK_INSTRUMENTATION_GUIDE.md` (`git mv`);
- new files: `gernas/evaluation.py`, `docs/kpi-catalogue.md`, `tests/test_kpi_metrics.py`, `tests/test_examples.py`.

Final staged inspection: see the report at the end of the session. Nothing is committed or pushed.

2026-10-18 audit round:
- new files: `gernas-sdk/tests/test_lifecycle.py`, `tests/test_generated_docs.py`, `scripts/build_architecture_doc.py`, `scripts/mcp_probe.py`, `scripts/load_env.ps1`, `docs/GERNAS_SDK_END_TO_END_RUNBOOK.md`, `docs/SOW_COMPLIANCE_REPORT.md`, `docs/GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html`, `docs/INSTRUMENTATION_MAP.md` (last two generated);
- changed: `gernas/decorators/_core.py` (F-33), `agent-mesh/src/mesh/orchestrator.py` (docstring), README / instrumentation guide links, `IMPLEMENTATION_LOG.md`, this file;
- `collector/out/` and `collector/bin/` remain git-ignored; no `.env` value was printed or written. Not committed (no authorization in this brief).

## 12. Outstanding gaps and sign-off

| # | Severity | Requirement | Component / file | Impact | Blocker | Corrective action |
|---|---|---|---|---|---|---|
| G-1 | High | SOW-010, 041, 064 (A4, A5) | Collector `azuredataexplorer` exporter; `collector/adx/create-tables.kql` | No data plane: KPIs, adoption metrics and dashboards cannot be computed; local file substitute only | No ADX instance or Azure credentials | Provision ADX, run the KQL, set `ADX_CLUSTER_URI`. Close on `OTELTraces` rows per `task_id` and 0 `send_failed` |
| G-2 | High | SOW-006, 062 (A5) | `gernas-sdk/collector/k8s/`, `kustomization.yaml` | Gateway not running in AKS; producers in AKS have no endpoint | No cluster access (kubectl, az) | Runbook Part G / `collector-aks-deployment.md` §2–3; 7 acceptance checks |
| G-3 | High (scope) | SOW-047…056, 060 (X, A4); F-28 | Dashboards, RED / USE, drill-down, alert rules (not in repo) | No observability experience beyond Opik trace view | WP3 not started; depends on G-1 | Dashboards-as-code over ADX, reviewed by FAB personas |
| G-4 | Medium | SOW-040 (X) | `gernas-sdk/docs/kpi-catalogue.md` | KPIs have no owner, threshold, SLO or retention | FAB decision | FAB completes the catalogue |
| G-5 | Medium | SOW-043, 057, 058 (A2, X) | SDK cost (LLM + embedding only) | Cost per run misses 7 of 9 §11 components | Platform metering, FinOps model | WP3-D cost model in the data plane |
| G-6 | Medium | SOW-011, 017, 021, 027, 031, 032, 035, 038; parts of 012, 022, 036, 039 (A1, X) | Platform telemetry sources (Azure Monitor, APIM, GERNAS OS, Qdrant / AI Search, DB, AKS, network, CI/CD, IdP) | No infrastructure or platform nodes in the telemetry graph | Other owners | Route those signals to the Collector / data plane |
| G-7 | Medium | SOW-028 (A1) | `rag-as-a-service/src/gernas_rag/ingestion/pipeline.py` | `document.process` not yet observed live; no `rejected` path | Docling + Qdrant not running | RAG team: live ingestion run; reject path |
| G-8 | Medium | SOW-029, 044 (A1, A2) | RAG chunk metadata; `llm.call` | `document_version`, knowledge `authorization_decision`, context size never populated | Data not available; field decision | RAG team: version in metadata; FAB: access policy, context-size field |
| G-9 | Low | SOW-037 (A1) | `agent-mesh/src/mesh/workflow.py:105` `_role_recognised` | No privilege-escalation signal; RBAC deny not observable through `login()` | FAB definition of "escalation attempt" | Define the signal, then emit it via `trace_guardrail` |
| G-10 | Medium | single source of truth (§4); F-16 | `agent-mesh/workflow_evaluations/evaluators/trace_linker.py`, `workflow/grafana_push.py` | Competing evaluation telemetry (inert today: no provider) | Mesh-team mapping of 20 evaluators onto 8 §6 metrics | Replace with `gernas.record_evaluation` |
| G-11 | High (mesh UX) | F-20 | `agent-mesh/api_server.py` `/api/traces`, `TraceDashboardPage.tsx` | Mesh Trace Dashboard shows no new data | Mesh-team decision | Point at Opik / ADX, or remove |
| G-12 | Low | SOW-030 HITL live, §5 HITL row | `agent-mesh/src/hitl/approval_store.py` | `hitl.approval` verified by unit tests only | Needs a human approver in a live run | Run a `credit_officer` request and approve it; `opik_verify` |
| G-13 | Decision | F-29 | Collector exporters | Brief says "ADX deferred"; user instruction says ADX + Opik | User confirmation | Kept ADX + Opik (instruction of 2026-10-17) |
| G-14 | Low (environment) | — | `agent-mesh/.env` `DATA_AGENT_MODEL` / `RAG_AGENT_MODEL` | Mesh MCP leg not reached live (model 404); proven separately by `MCPPROBE18A` | Model availability on the Groq key | Set an available model, re-run `LIVE` queries |

**Sign-off:** the system is **not yet fully verified against the SOW**.

- **Verified:** the SDK as the central instrumentation toolkit, the Agent Mesh integration, and the Collector → Opik path, with live evidence (re-verified 2026-10-18). All five SOW aspects are Partial for the reasons in §3A.
- **Not delivered:**
  - dashboards, alerts and KPI governance;
  - FinOps allocation;
  - platform telemetry;
  - ADX and AKS (blocked on infrastructure).
