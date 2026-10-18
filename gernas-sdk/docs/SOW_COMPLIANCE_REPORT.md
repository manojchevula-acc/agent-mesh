# Gernas SDK — SOW compliance report

**Independent code audit, five-aspect SOW verification, Agent Mesh instrumentation audit**
**Date:** 2026-10-18 · **SDK:** `gernas-telemetry` 0.4.0 · **Repository:** `agent-mesh/` (branch `fab-toolkit-oct`)
**Authoritative status record:** `../../checklist.md` (this report explains it; where they differ, the checklist wins and this report is stale).

## 1. Summary

| Question | Answer | Evidence level |
|---|---|---|
| Is the Gernas SDK the single central toolkit for the required observability behaviour? | **Yes, for every SOW capability this repository delivers.** All spans, metrics, logs, context propagation, attribute names and export in Agent Mesh, the DataLayer MCP server and the RAG service go through the SDK. One competing implementation remains in **offline tooling only**: the mesh evaluation harness (`workflow_evaluations/`, F-16). Its spans currently go to the no-op provider, so nothing is exported. | Source search (§5), live traces (§8: "only Gernas SDK spans" ×5) |
| Are the five SOW aspects fully covered? | **No.** All five are **Partial**. The SDK-side behaviour of each aspect is implemented and verified. The remaining rows depend on other owners (API / LLM gateway, GERNAS OS, IdP), on infrastructure (AKS, ADX), or on WP3 deliverables (dashboards-as-code, FinOps). | Derived from `checklist.md` §3A |
| Are agent execution, LLM calls, delegation, tool calls and MCP traced end to end? | **Yes**, observed in Opik on 2026-10-18 (`LIVE18A000`–`003`, `MCPPROBE18A`, all PASS). | Real Opik traces |
| Were real traces verified in Opik? | **Yes**: 5 traces with `opik_verify.py` PASS. Collector counters: 89 accepted = 89 sent to Opik = 89 sent to the data plane, 0 refused. | Opik REST API, Collector `:8888` |
| ADX and AKS? | **Blocked**: no cluster, ADX instance or credentials. Config validated; manifests valid against the Kubernetes 1.30 schema. | Static validation only |
| Defects found and fixed in this audit | **F-33** (span lifecycle for coroutine-returning callables), F-34 (MCP servers started without `GERNAS_*` in the runbook path), and a stale orchestrator docstring. | Tests (§7) |

### Evidence levels used in this report

| Level | Meaning |
|---|---|
| **S** | Source inspection |
| **U** | Automated unit / contract test in `gernas-sdk/tests` |
| **L** | Local integration: real processes and an in-memory or file exporter |
| **C** | Collector integration: real `otelcol-contrib` with its counters |
| **O** | Real trace read back from Opik (`scripts/opik_verify.py`) |
| **K** | AKS deployment. **Not achieved**: blocked |

## 2. Source of truth and the five aspects

- **The SOW** is `docs/GERNAS Enterprise Telemetry Observability Blueprint.docx`, subtitled *"Proposed Accenture Scope of Work for FAB"*. It was re-extracted in full, and Figure 1 was inspected as an image.
- **The five aspects** are the five instrumentation principles of blueprint **§1 Target Telemetry Reference Architecture**: *"Instrumentation should primarily use:"*
  1. OpenTelemetry
  2. Standardized GERNAS semantic conventions
  3. Standard trace/span context
  4. Exporter abstraction
  5. Centralized telemetry gateway/collectors
- **Why this list.** It is the only five-item capability list in the SOW. Figure 1 has **four** numbered layers (instrumentation; collection and processing; data plane; observability), so it is not the five-aspect grouping.
- **Row mapping.** Each of the 65 detailed requirements (SOW-001…065) is assigned to exactly one aspect, or to **X** (scope outside the five aspects). The mapping and the derived counts are in `checklist.md` §3A, and `scripts/build_architecture_doc.py --check` recomputes them from the row statuses.

## 3. Per-aspect compliance

### A1 — OpenTelemetry (blueprint §1 bullet 1; §3 component telemetry)

| Field | Detail |
|---|---|
| SOW rows | SOW-002, 012, 016, 018, 020, 022, 023, 024, 025, 026, 028, 029, 030, 036, 037, 061, 065 |
| Expected behaviour | Every GERNAS component operation (use case, agent runtime, multi-agent, tools, LLM, embedding, safety, RAG, documents, knowledge, memory, identity, authorization) emits OTel telemetry with the blueprint fields |
| SDK implementation | OTel SDK primitives only (no `opentelemetry-instrumentation-*`). Providers: `gernas/tracer.py:254` `init_gernas`. One wrapper engine `gernas/decorators/_core.py:246` `wrap` runs the per-operation `Spec`s in `gernas/decorators/*.py` |
| Public APIs | `trace_use_case` (`decorators/use_case.py:73`), `trace_agent` (`agent.py:181`), `trace_step` / `trace_planning` (`planning.py:64` / `:146`), `trace_delegation` (`delegation.py:64`), `trace_llm` / `trace_embedding` (`llm.py:217` / `:301`), `trace_tool` (`tool.py:103`), `trace_retrieval` (`retrieval.py:107`), `trace_document` (`document.py:75`), `trace_memory` (`memory.py:49`), `trace_guardrail` / `trace_content_safety` (`safety.py:111` / `:186`), `start_hitl` / `complete_hitl` (`hitl.py:52` / `:79`), `record_evaluation` (`evaluation.py:30`); MAF / A2A / ASGI / MCP / LangChain adapters |
| Agent Mesh integration | `src/mesh/orchestrator.py:114` `handle_request` (`trace_use_case`); `:108` `_run_workflow` (`trace_agent`); `src/mesh/workflow.py:227/289/359/776/962/1369` (6 executors, `trace_step`); `workflow.py:105` `_role_recognised` and `src/guardrails/deterministic_filters.py:126` `screen_input` (`trace_guardrail`); `deterministic_filters.py:140` `redact_output` (`trace_content_safety`); `src/agents/agent_factory.py:65` (MAF middlewares: `agent.run`, `llm.call`, `tool.call`); `src/tools/collaboration_tools.py:137/150/166/185` (`trace_tool`); `src/a2a/clients.py:59` `ask_remote` (`trace_delegation`); `src/memory/conversation_store.py:45/56/81` (`trace_memory`); `src/cache/semantic_cache.py:143` (`trace_retrieval`); `src/hitl/approval_store.py:72/104/125/143` (HITL); `src/memory/summarizer.py:49`, `src/cache/cache_judge.py:87`, `src/cache/entity_extractor.py:266/309` (`trace_llm` via `chat_post`). Full list: [INSTRUMENTATION_MAP.md](INSTRUMENTATION_MAP.md) §2 |
| Telemetry output | 16 span types (`registry/spans.yaml`), 28 metric instruments (`registry/metrics.yaml`, incl. 4 SDK-health counters), OTel logs bridge |
| Tests | `test_decorators` (28), `test_runtime` (13), `test_document_processing` (5), `test_kpi_metrics` (8), `test_metrics` (4), `test_adapter_*` (22), `test_lifecycle` (5), `test_examples` |
| Verification result | **U, L, C, O.** In `LIVE18A000`, Opik holds `use_case.request`, `agent.run` ×4, `agent.step` ×7, `guardrail.check` ×2, `agent.delegate` ×3, `llm.call` ×5, `tool.call`, `retrieval.search`, `memory.access` ×3 and `content_safety.check` |
| Status | **Partial**: 11 Verified, 6 Partial |
| Gaps → corrective action | 012 API gateway (APIM → platform); 022 gateway-side routing (platform); 028 live document ingestion and a reject path (RAG team); 029 document version in RAG metadata and a knowledge access policy (RAG team + FAB); 036 IdP events (platform); 037 escalation-attempt signal (FAB decision) |

### A2 — Standardized GERNAS semantic conventions (blueprint §2, §4–6)

| Field | Detail |
|---|---|
| SOW rows | SOW-003, 013, 014, 015, 042, 043, 044, 045, 046 |
| Expected behaviour | The 13 canonical context domains use the exact blueprint field names on every signal. No raw prompts, responses, customer data, secrets or tokens. KPI signals and consumption are attributable along Use Case → Agent → Run → User → Model → Cost |
| SDK implementation | `registry/domains/*.yaml` → `registry/generate.py` → `gernas/attributes.py` (`Ctx`, `SpanName`, `Metric`, `REQUIRED_ATTRIBUTES`, `DENYLIST`). Collector allow-list and denylist are generated from the same registry. Every write goes through `gernas/tracer.py:74` `safe_set` → `gernas/redaction.py:41` `redact` |
| Public APIs | `gernas.Ctx`, `gernas.annotate`, `gernas.set_redaction_hook`, `gernas.request_context` (HMAC-tokenized user) |
| Agent Mesh integration | Uses `Ctx.*` only (`gernas_telemetry.py` `_annotate`). No hand-typed `gernas.*` keys (AST lint `test_no_handtyped_attrs`) |
| Telemetry output | `gernas.<blueprint_field>` attributes. The envelope (`business_domain`, `use_case_id`, `application_id`, `environment`, `operation`, `execution_status`) is on every span |
| Tests | `test_naming` (9), `test_registry` (8), `test_security` (11), `test_no_handtyped_attrs`, `test_conformance` (10) |
| Verification result | **U, O.** GERNAS conformance rules pass on Opik's own copy of all 5 traces. No payload keys in Opik |
| Status | **Partial**: 7 Verified, 2 Partial |
| Gaps → corrective action | 043 cost covers LLM + embedding only (the other 7 §11 components need FinOps / platform metering); 044 context size (field decision) and PTU (Azure metric) |

### A3 — Standard trace/span context (blueprint §1 bullet 3; Purpose; Fig. 1 enrichment; §10 drill-down; §21 graph)

| Field | Detail |
|---|---|
| SOW rows | SOW-001, 004, 007, 019, 055, 059 |
| Expected behaviour | One correlated trace per request across processes, agents, tools and MCP, with run, session and task context on every span, so the Use Case → Agent → Run → Step → Span drill-down is possible |
| SDK implementation | Global propagator: W3C TraceContext + `GernasBaggagePropagator` (`gernas/context.py:113`), installed by `init_gernas`. Run context: `RunState` ContextVar (`gernas/run_state.py:28`, `bind` at `:139`), so it is isolated per asyncio task. `_BaggageSpanProcessor` (`tracer.py:178`) copies allow-listed baggage onto every span. `stamp_context` (`_core.py:167`) adds run context, with a baggage fallback for remote servers |
| Public APIs | `request_context` (`context.py:50`), `inject_context` / `extract_context` / `attach_headers` (`:124` / `:136` / `:145`), `gernas_task` (`:190`), `wrap_in_context` (`:195`), `correlation` (`:163`); adapters `GernasA2AInterceptor`, `GernasASGIMiddleware`, `GernasFastMCPMiddleware`, `mcp_meta_context` |
| Agent Mesh integration | `orchestrator.py:140` `request_context`; `orchestrator.py:449` `spawn` → `gernas_task`; `src/a2a/clients.py:47` `GernasA2AInterceptor`; `src/a2a/hosting.py:83` and `api_server.py:1129` `GernasASGIMiddleware`; `datalayer-as-service/mcp_server/server.py:78` `GernasFastMCPMiddleware`; `rag-as-a-service/mcp_integration/server.py:173` `mcp_meta_context`; `logging_config.py:120` and `audit_middleware.py:30` `correlation()` |
| Telemetry output | One `trace_id` per request; `gernas.session_id`, `task_id`, `tokenized_user_id` and `channel` on every span; `parent_run_id` and `delegation_path` across agents |
| Tests | `test_context` (11), `test_adapter_transport` (8), `test_decorators::test_async_nested_and_concurrent_runs_stay_isolated`, `test_lifecycle::test_request_context_baggage_is_isolated_per_concurrent_task` |
| Verification result | **U, O.** "request is one Opik trace" and "every parent link resolves" PASS for all 5 traces. `LIVE18A002` spans 5 processes (CLI orchestrator + compliance, price_assist, data_agent, rag_agent nodes). `MCPPROBE18A`: the server span in the DataLayer process carries the client run's `agent_id` |
| Status | **Partial**: 3 Verified, 3 Partial |
| Gaps → corrective action | 001 / 059: GERNAS OS and infrastructure nodes are not produced (other owners); 055: drill-down dashboard not built (WP3) |

### A4 — Exporter abstraction (blueprint §1 bullet 4; §21 contractual recommendation)

| Field | Detail |
|---|---|
| SOW rows | SOW-005, 060, 063, 064 |
| Expected behaviour | Producers are not coupled to a vendor; backends can change without re-instrumenting |
| SDK implementation | `gernas/exporters.py:68/84/100` builds **OTLP only** (gRPC or HTTP), plus `console` and `off` for development. No vendor SDK is imported (`test_packaging`). Vendor shape (Opik mapping, ADX tables) lives in Collector config |
| Agent Mesh integration | None needed: `GERNAS_OTLP_ENDPOINT` only |
| Tests | `test_edge_cases::test_exporter_selection`, `test_packaging::test_core_modules_do_not_import_vendor_sdks`, `test_collector_deployment::test_traces_go_to_adx_and_opik_metrics_and_logs_to_adx` |
| Verification result | **U, C, O**: Opik export live; data-plane export live to local files |
| Status | **Partial**: 2 Verified, 1 Partial, 1 Blocked |
| Gaps → corrective action | 060: dashboards-as-code and alert rules (WP3); 064: ADX environment (platform) |

### A5 — Centralized telemetry gateway/collectors (blueprint §1 bullet 5; Fig. 1 layer 2–3; §3 Telemetry Platform)

| Field | Detail |
|---|---|
| SOW rows | SOW-006, 008, 009, 010, 039, 062 |
| Expected behaviour | A Collector gateway in AKS that enriches, detects and redacts PII / secrets, samples, routes and transforms, and feeds the data plane. Collector health and dropped telemetry are observable |
| Implementation | `gernas-sdk/collector/otel-collector-config.yaml`:
- pipelines `traces/dataplane`, `traces/opik`, `metrics/dataplane`, `logs/dataplane`;
- processors `memory_limiter`, `attributes/pdpl` (generated denylist), `redaction/deny_by_default` (generated allow-list), `tail_sampling/dataplane`, `probabilistic_sampler/opik`, `transform/opik_shape`, `batch`;
- exporters `azuredataexplorer`, `otlphttp/opik`.

AKS: `k8s/*.yaml`, `kustomization.yaml`. ADX: `adx/create-tables.kql`. SDK health: `gernas.sdk.dropped_spans / dropped_logs / dropped_attributes / redactions` |
| Tests | `test_collector_deployment` (6), `test_collector_down`, `test_registry::test_collector_allowlist_matches_registry`, `scripts/e2e_pipeline_checks.py` (9/9 earlier) |
| Verification result | **C, O** locally: `otelcol validate` VALID; 89 accepted / 0 refused / 89 sent to Opik / 89 sent to the data plane (2026-10-18). **K not achieved** |
| Status | **Partial**: 2 Verified, 1 Partial, 3 Blocked |
| Gaps → corrective action | 006 / 062 deploy to AKS; 010 provision ADX; 039 storage utilization (needs ADX) — see `collector-aks-deployment.md` §2–3 |

### X — Scope outside the five aspects

These are 23 rows: platform telemetry sources (Azure, APIM, GERNAS OS, vector store, DB, AKS, network, CI/CD), KPI governance, 8 dashboards, RED / USE and FinOps.

**Status: not delivered by this repository.** 0 Verified, 2 Partial, 18 Not started, 1 Blocked, 2 N/A. Owners: platform, WP3, FinOps, FAB.

### Consolidated matrix

| SOW aspect | SDK implementation | Agent Mesh integration | Verification evidence | Status |
|---|---|---|---|---|
| A1 OpenTelemetry | `init_gernas` + 14 decorators on `_core.wrap`, adapters | 67 call sites across Agent Mesh, DataLayer and RAG (INSTRUMENTATION_MAP §2) | U + O: `LIVE18A000`–`003`, `MCPPROBE18A` | Partial (11 / 17 verified) |
| A2 Semantic conventions | registry → `Ctx`; `safe_set` + redaction | `Ctx.*` only, AST-linted | U + O: conformance on Opik's copy | Partial (7 / 9) |
| A3 Trace/span context | TraceContext + GernasBaggage, RunState, adapters | `request_context`, A2A interceptor, ASGI, MCP middleware | U + O: one trace across 5 processes; MCP server run context | Partial (3 / 6) |
| A4 Exporter abstraction | OTLP-only exporters | env-only | U + C + O | Partial (2 / 4; ADX blocked) |
| A5 Gateway / collectors | Collector config, k8s, ADX KQL | n/a | C + O local; K blocked | Partial (2 / 6; 3 blocked) |

## 4. Decorator and API inventory

The complete inventory is **generated from the source**:
- [INSTRUMENTATION_MAP.md](INSTRUMENTATION_MAP.md) §1 gives the API, group, definition `file:line`, registry span, number of call sites and number of tests.
- The HTML guide, section D, adds per API: signature, docstring, span behaviour, required attributes, context and baggage behaviour, error behaviour, sync/async support, call-site links and test names.

Behaviour that differs between decorators (verified in source and tests):

| API | Span / kind | Distinctive behaviour |
|---|---|---|
| `trace_use_case` | `use_case.request` SERVER | Root of the request; `business_outcome`; cancellation leaves the status UNSET (`abandoned`) |
| `trace_agent` | `agent.run` INTERNAL | Binds `RunState`; ERROR only for termination `error` / `timeout`. `policy_block`, `max_steps` and `human_rejected` are outcomes |
| `trace_delegation` | `agent.delegate` CLIENT | `parent_agent_id`, `child_agent_id`, `delegation_path`; injects context into a `headers` / `carrier` dict argument |
| `trace_tool` | `tool.call` CLIENT | Auth error → `authorization_decision=deny`, status OK; records payload *size* only |
| `trace_guardrail` / `trace_content_safety` | `guardrail.check` / `content_safety.check` | A deny is status OK and sets `termination_reason=policy_block` when `blocks_run` |
| `trace_document` | `document.process` | A caught failure reported via `outcome_from` is still ERROR |
| `start_hitl` | `hitl.approval` | Long-lived and not made current, so the caller's context is untouched |
| MAF `gernas_middleware` | `agent.run` / `llm.call` / `tool.call` | Same engine; `disable_native_telemetry()` removes MAF's own spans |
| `GernasASGIMiddleware` | `{method} {route}` SERVER | Extracts the inbound trace and baggage; 5xx → ERROR |
| `GernasFastMCPMiddleware` | `tool.call` SERVER | Context from the MCP `_meta`; with `GERNAS_SUPPRESSED_SCOPES=fastmcp` there is no duplicate fastmcp span |

## 5. Single-source-of-truth audit

**Search performed (2026-10-18)** over `agent-mesh/`, `datalayer-as-service/` and `rag-as-a-service/`, excluding `.venv`, `node_modules` and tests, for:
- `TracerProvider|MeterProvider|LoggerProvider|set_tracer_provider`;
- `start_as_current_span|start_span(|get_tracer(`;
- `OTLPSpanExporter|SpanExporter|opentelemetry.(instrumentation|exporter)`;
- `propagate.inject|baggage.set_baggage|set_attribute(`.

| Finding | Location | Assessment |
|---|---|---|
| `baggage.set_baggage` ×4 | `agent-mesh/src/observability/baggage.py:42-45` | **Justified**: mesh business context (`fab.*` request, user, role) used for the audit trail and peer-reasoning files. It is never copied onto spans (the SDK copies only allow-listed `gernas.*` baggage; `test_context::test_unknown_baggage_is_not_copied_to_spans`). It travels over the SDK propagator |
| `get_tracer`, `start_as_current_span`, `set_attribute("fab.eval.*")` | `agent-mesh/workflow_evaluations/evaluators/trace_linker.py:64-98` | **Competing implementation, offline tooling (F-16, open).** The harness never calls `init_telemetry`, so these spans go to the no-op provider and nothing is exported today. Its 20 evaluator names do not map onto the 8 §6 metrics accepted by `gernas.record_evaluation`; choosing the mapping is a mesh-team decision |
| `workflow_evaluations/workflow/grafana_push.py` | offline harness | Direct Grafana push of evaluation metrics (F-16); not part of the runtime |
| Anything else | — | **None.** No provider, exporter, span creation or hand-typed attribute in any runtime path |

Framework-native duplicates are switched off:
- **MAF:** `disable_native_telemetry()`.
- **a2a-sdk:** `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`.
- **fastmcp:** `GERNAS_SUPPRESSED_SCOPES`.

Every live trace passes "only Gernas SDK spans".

**Conclusion:** the SDK is the single source of truth for runtime observability. The only exception is the offline evaluation harness (F-16).

## 6. Agent Mesh instrumentation coverage

| SOW expectation | Where in Agent Mesh | SDK API | Telemetry | Verified |
|---|---|---|---|---|
| Initialisation / shutdown | `run.py:26/178`, `api_server.py:50/836`, `a2a_server.py:113`, `devui_app.py:49`, `demos/gernas_e2e_queries.py:32/60` via `gernas_telemetry.init_telemetry` / `shutdown` | `init_gernas`, `GernasHandle` | providers, resource | O (`gernas doctor --opik` PASS; every live run) |
| Root request | `src/mesh/orchestrator.py:114` `handle_request` | `trace_use_case` | `use_case.request` | O |
| Request context | `orchestrator.py:140` | `request_context` | session, task, tokenized user baggage | O |
| Orchestrator run | `orchestrator.py:108` `_run_workflow` | `trace_agent` | `agent.run(agent_mesh)` | O |
| Workflow executors | `src/mesh/workflow.py:227` input-guardrail, `:289` rbac-validator, `:359` cache-check, `:776` compliance-gate, `:962` domain-processor, `:1369` output-redaction | `trace_step` | `agent.step` | O |
| Guardrails / authorization | `deterministic_filters.py:126` `screen_input`; `workflow.py:105` `_role_recognised` | `trace_guardrail` | `guardrail.check` (`input_screen`, `rbac_role_check`) | O: allow ×3 and deny ×1 live (rbac allow live; rbac deny unit / scratch only) |
| Output safety | `deterministic_filters.py:140` `redact_output` | `trace_content_safety` | `content_safety.check` | O |
| Policy block | `workflow.py:241/301/838` `mark_policy_block` | `set_termination_reason` | `termination_reason=policy_block` | O (`LIVE18A003`) |
| Agents + LLM + MCP tools (MAF) | `src/agents/agent_factory.py:65` | `gernas_middleware` | `agent.run`, `llm.call`, `tool.call` (mcp) | O (`agent.run` / `llm.call`); mesh MCP leg not reached (model 404), same mechanism O via `MCPPROBE18A` |
| Raw LLM calls | `summarizer.py:49`, `cache_judge.py:87`, `entity_extractor.py:266/309` | `trace_llm` (`chat_post`) | `llm.call` with tokens, cost, stop reason | O (summary) |
| Delegation / hand-off | `src/a2a/clients.py:59` `ask_remote`, `:47` interceptor; `src/a2a/hosting.py:83` | `trace_delegation`, `GernasA2AInterceptor`, `GernasASGIMiddleware` | `agent.delegate` → `POST /` → remote `agent.run` | O (5 processes) |
| Tools | `src/tools/collaboration_tools.py:137/150/166/185` | `trace_tool` | `tool.call` | O |
| Retries | `workflow.py:1087/1096/1112`, `collaboration_tools.py:78`, `entity_extractor.py:312/321` | `record_retry` | `retry_count` | U |
| Retrieval (cache) | `src/cache/semantic_cache.py:143` | `trace_retrieval` | `retrieval.search` | O |
| Memory | `src/memory/conversation_store.py:45/56/81` | `trace_memory` | `memory.access` | O |
| Background task | `orchestrator.py:449` `spawn` | `gernas_task` | summary step in the request trace | O |
| HITL | `src/hitl/approval_store.py:72/104/125/143` | `start_hitl` / `complete_hitl` | `hitl.approval` | U only (needs a human approver) |
| Log correlation | `logging_config.py:120`, `audit_middleware.py:30` | `correlation` | ids on log records | U |
| MCP servers | `datalayer-as-service/mcp_server/server.py:76/78`; `rag-as-a-service/mcp_integration/server.py:53/54/173/177` | `init_gernas`, `GernasFastMCPMiddleware`, `mcp_meta_context`, `trace_retrieval` | server `tool.call`, `retrieval.search` | DataLayer O (`MCPPROBE18A`); RAG U (store down) |
| Document ingestion | `rag-as-a-service/src/gernas_rag/ingestion/pipeline.py:53/103` | `trace_document`, `trace_step` | `document.process`, extraction `agent.step` | U + L (scratch) |

## 7. Correctness audit of the decorator engine

| Check | Result | Evidence |
|---|---|---|
| Spans always closed (`finally`) on return, exception, cancellation and generator close | Pass | `_core.py:206` `_end` (`finally: span.end()`); `test_runtime::test_abandoned_stream_is_cancelled_not_error`, `test_decorators::test_cancelled_child_span_is_unset_not_ok` |
| Parent–child relationships | Pass | `test_decorators::test_children_carry_run_context_and_nest_under_the_run`; Opik "every parent link resolves" ×5 |
| Context leakage between concurrent tasks | Pass | ContextVar `RunState`; `test_decorators::test_async_nested_and_concurrent_runs_stay_isolated`, `test_lifecycle::test_request_context_baggage_is_isolated_per_concurrent_task` (**new**) |
| Async propagation (tasks, threads) | Pass | `test_context::test_gernas_task_keeps_the_trace_and_run`, `test_wrap_in_context_for_threads` |
| **Callable returning a coroutine** | **Failed → fixed (F-33)** | An object with `async def __call__` (or a sync function returning a coroutine) got a span that closed *before* the work ran: 0.9 ms and status OK while the work took 50 ms and raised. Fix in `_core.py:246` `wrap` and `:257` `_await_in_span`; a `Task` / `Future` is returned unchanged. Regression tests in `tests/test_lifecycle.py` fail on the old code and pass on the new |
| Duplicate spans from nested instrumentation | Pass | `test_adapter_agent_framework::test_no_duplicate_tool_span_when_tools_are_already_decorated`, conformance `duplicate` rule, Opik "only Gernas spans" |
| Exception recording and status | Pass | PII-scrubbed `exception` event, ERROR status; expected outcomes (deny, auth) stay OK (`test_exception_sets_error_status_category_and_scrubbed_event`, `test_guardrail_deny_keeps_status_ok_and_blocks_the_run`) |
| Attribute names consistent | Pass | Generated `Ctx`; `test_naming`, AST lint |
| Sensitive data | Pass | Denylist and redaction before every write; `test_security` (11); Collector PDPL processors |
| Decorators never swallow exceptions or change results | Pass | `raise` in every wrapper; `test_decorators::test_decorated_function_signature_and_return_are_preserved`, `test_security::test_telemetry_failure_never_breaks_the_function` |
| Exporter errors | Pass, by design hidden from callers | `CountingSpanExporter` (`tracer.py:98`) counts them in `gernas.sdk.dropped_spans`, surfaced by `gernas doctor`; `test_collector_down`, `test_lifecycle::test_an_exporter_that_raises_never_reaches_the_caller_and_is_counted` (**new**) |
| Provider lifecycle | Pass | `init_gernas` is idempotent (`test_config::test_init_is_idempotent`); `GernasHandle.shutdown` is idempotent. A limitation is documented: `shutdown` does not reset the global handle, so re-initialising in the same process returns the shut-down handle (OTel allows one global provider anyway) |

## 8. Runtime evidence (2026-10-18)

| Run | Command | Result |
|---|---|---|
| Stack | Collector + DataLayer MCP + RAG MCP + `launch_mesh.py` (4 nodes) | all ports up |
| Workflow | `demos/gernas_e2e_queries.py --prefix LIVE18A` | 4 requests (data, knowledge → compliance block, hybrid, injection → guardrail block) |
| Opik | `opik_verify.py --task-id LIVE18A000…003` | **PASS ×4** |
| Collector | `:8888/metrics` | accepted 89, refused 0, sent to Opik 89, sent to the data plane 89 |
| Data plane (local file) | `guardrail.check` spans | `input_screen` allow ×3 / deny ×1 (`prompt_injection`); `rbac_role_check` allow ×3. All status OK |
| MCP | `scripts/mcp_probe.py MCPPROBE18A` → `opik_verify.py` | **PASS**: client and server `tool.call` in one trace |
| Doctor | `python -m gernas doctor --opik` | PASS 5 / 5 |

**Observed limitations of the reference environment.** These are not telemetry defects; in each case telemetry recorded the behaviour correctly:
- The `data_agent` and `rag_agent` models return HTTP 404 on Groq (`qwen/qwen3.6-27b`), so the mesh's own MCP leg is not reached.
- Qdrant is not running, so RAG retrieval returns no results.
- In the first run, both MCP servers were started without `GERNAS_*` and correctly refused to invent identity (F-34). The runbook now loads the variables (`scripts/load_env.ps1`), and the MCP probe was re-run with them.

## 9. Corrections made in this audit

| ID | File | Change | Verification |
|---|---|---|---|
| F-33 | `gernas-sdk/gernas/decorators/_core.py` | Async-callable objects use the async wrapper. A sync callable returning a coroutine keeps its span open until the coroutine completes, with the span current inside it | `tests/test_lifecycle.py` (2 tests fail before, pass after); full SDK suite |
| F-34 | `gernas-sdk/scripts/load_env.ps1`, runbook Part C/D | MCP servers need `GERNAS_*` from `agent-mesh/.env`; a helper loads them without printing values | `MCPPROBE18A` PASS |
| — | `gernas-sdk/scripts/mcp_probe.py` | Repeatable MCP-leg check, promoted from a scratch script | `MCPPROBE18A` PASS |
| — | `gernas-sdk/scripts/build_architecture_doc.py`, `tests/test_generated_docs.py` | Generated HTML guide and instrumentation map. `--check` fails on drift from the source or the checklist | test passes |
| — | `agent-mesh/src/mesh/orchestrator.py` (docstring) | Removed the stale reference to the old `mesh.request` span | comment only |

## 10. Outstanding gaps

The full list, with severity, owner and closing evidence, is in `checklist.md` §12. In short:
- **AKS and ADX (blocked):** SOW-006, 010, 041, 062, 064.
- **WP3:** dashboards, alerts, KPI governance and FinOps — SOW-040, 047–058.
- **Platform telemetry sources** owned by other teams.
- **RAG:** RAG data for documents and knowledge (SOW-028, 029).
- **FAB decisions:**
  - escalation signal (SOW-037);
  - Opik-only vs ADX + Opik (F-29), currently ADX + Opik per the user's instruction of 2026-10-17. This audit brief again said "ADX deferred"; the direct instruction is still followed.
- **Mesh team:**
  - Trace Dashboard page (F-20);
  - evaluation harness → `record_evaluation` (F-16);
  - live HITL approval run.

**This system is not fully compliant with the SOW.** The instrumentation toolkit and its reference integration are verified end to end into Opik; the platform, data-plane and dashboard scope is not delivered.
