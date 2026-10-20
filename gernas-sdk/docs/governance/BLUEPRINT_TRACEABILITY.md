# GERNAS Blueprint → Gernas SDK traceability

**Source of truth:** `docs/GERNAS Enterprise Telemetry Observability Blueprint.docx`. It was read in full: about 11k characters of text, 8 tables and Figure 1. The section numbering in the source is non-contiguous (1–6, 10, 11, 21); nothing is missing.

**Status of this document:**
- §1–§2 are the baseline and the **initial** matrix, written *before* the cleanup.
- §3–§7 hold the **final** matrix, the technical necessities, the removal report, the naming inventory and the gaps. They reflect release 0.4.0.

## 1. Requirements baseline

### 1.1 What the blueprint defines explicitly

| Area | Blueprint statement (paraphrased; quotes in "…") |
|---|---|
| Architecture (§1, Fig. 1) | "OpenTelemetry", "Standardized GERNAS semantic conventions", "Standard trace/span context", "Exporter abstraction", "Centralized telemetry gateway/collectors". Telemetry producers must "not be coupled directly to a particular observability vendor". In Figure 1 the instrumentation layer contains the "GERNAS Agent Telemetry SDK", and context enrichment, PII/secret detection, sampling and routing all sit in the **collectors**. |
| Canonical context (§2) | "Every telemetry signal should carry a common contextual envelope wherever applicable." There are 13 domains with exact field names (table below). |
| Privacy (§2 Critical principle) | Avoid unrestricted raw prompts, raw LLM responses, customer data, account numbers, credentials, secrets, authentication tokens and unrestricted RAG context in normal logs. Sensitive payload capture must be "explicitly policy controlled, redacted, encrypted…" |
| Component telemetry (§3) | Per-component signal lists. The rows relevant to an agent SDK are: Use Case Layer, Agent Runtime, Multi-Agent Orchestration, Skills/Tools, LLM Gateway, LLMs, Embedding, Content Safety, RAG/Search, Knowledge Layer, Memory and Telemetry Platform. Infrastructure, identity and CI/CD rows are platform, Azure or Collector telemetry. |
| Agentic KPIs (§5) | Task success rate, run latency, planning latency, steps per run, tool calls per run, failed tool calls, retry rate, loop rate, maximum step violation, delegation count, hand-off latency, human intervention rate, abandonment rate, timeout rate, fallback rate, policy block rate, and cost per successful task. |
| LLM metrics (§6) | Request volume, TTFT, total generation latency, tokens per second, input and output tokens, context size, cache usage, timeout, rate limit, retry, model fallback, PTU utilisation. Every model call must be attributable to "Use Case → Agent → Agent Run → User → Model → Cost". |
| Dashboards (§10) | Agent drill-down: Use Case → Agent → Agent Version → Run → Step → Span. FinOps drill-down: Business Domain → Use Case → Application → Agent → Agent Run → Model/Tool/Infra. |
| Cost (§11) | Agent run cost = LLM + embedding + search/RAG + document processing + tool/API + compute + storage + network + shared allocation. The SDK can attribute only the directly metered parts. |
| IP (§21) | FAB must be able to change vendors or partners "without re-instrumenting GERNAS". |

Canonical context, with the exact blueprint field names:

| Domain | Fields |
|---|---|
| Business | business_domain, use_case_id, application_id |
| Identity | user_id/tokenized_user_id, principal_type, tenant, department |
| Agent | agent_id, agent_name, agent_version, agent_type |
| Execution | agent_run_id, session_id, task_id, trace_id, span_id |
| Planning | plan_id, step_id, step_type, attempt_number |
| Tool | tool_id, tool_type, tool_version, api_operation |
| AI | provider, model_id, deployment_id, model_version |
| Prompt | prompt_template_id, prompt_version |
| Knowledge | knowledge_domain, source_id, index_id, retrieval_strategy |
| Security | policy_id, policy_decision, risk_classification |
| Infrastructure | cluster, namespace, pod, region, environment |
| Cost | token_input, token_output, compute_units, estimated_cost |
| Outcome | execution_status, business_outcome, error_category |

### 1.2 What the blueprint does **not** define

The blueprint does not define any of the following:
- span names, metric names, event names or environment-variable names;
- a namespace prefix for attribute keys;
- the SDK's public API;
- framework adapters (MAF, LangChain and LangGraph are required by the programme brief, not by the blueprint);
- sampling rates;
- HITL mechanics, beyond the "Human Intervention Rate" KPI.

These are SDK conventions or technical necessities, documented as such in §3.

### 1.3 Naming decision (needs architecture-owner confirmation — see Gaps)

The blueprint field names are used **verbatim** as the attribute-key suffix under the `gernas.` namespace, for example `gernas.use_case_id`, `gernas.token_input` and `gernas.estimated_cost`.

- The namespace is a technical necessity. Bare keys such as `provider`, `pod` or `environment` would collide with OTel semantic conventions and with other instrumentation.
- No alternative names are kept. The previous OTel GenAI keys (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, …) are therefore replaced by the blueprint names, and any vendor-specific mapping (for example Opik's GenAI view) moves to the Collector, in line with the "exporter abstraction" principle.

## 2. Initial traceability matrix (before cleanup)

Status values: **complete**, **partial**, **missing**, **conflicting** (exists under a non-blueprint name or semantics), **duplicate**, **not required**.

| ID | Blueprint requirement | M/O | Blueprint name(s) | Current SDK | Status | Required action |
|---|---|---|---|---|---|---|
| GERNAS-REQ-001 | OpenTelemetry-based instrumentation | M | — | OTel SDK primitives | complete | keep |
| GERNAS-REQ-002 | Standardised GERNAS semantic conventions, centralised | M | — | `registry/` → `attributes.py` (`Ctx`) | partial | rename to blueprint names; remove non-blueprint fields |
| GERNAS-REQ-003 | Standard trace/span context and propagation | M | trace_id, span_id | W3C propagation; `gernas.trace_id` mirror attribute | duplicate | keep native context; remove the mirror |
| GERNAS-REQ-004 | Exporter abstraction; not vendor-coupled | M | — | OTLP plus console/pretty/file/memory/off sinks; `gernas.route.opik` kill switch in the SDK | conflicting | keep OTLP plus console/off; remove the vendor-named routing switch and the file/pretty sinks |
| GERNAS-REQ-005 | Centralised gateway/collectors do enrichment, PII detection, sampling and routing | M | — | `collector/` config | complete | update the mapping for renamed keys |
| GERNAS-REQ-006 | Business context on every signal | M | business_domain, use_case_id, application_id | envelope, but with **invented defaults** (`unassigned`, service name) | partial | make mandatory; never invent |
| GERNAS-REQ-007 | Identity context | M | user_id/tokenized_user_id, principal_type, tenant, department | registry has `tokenised_user_id` (spelling) and no API to set identity | conflicting / missing | rename to `tokenized_user_id`; add a `set_identity()` API with keyed tokenization |
| GERNAS-REQ-008 | Agent context | M | agent_id, agent_name, agent_version, agent_type | `trace_agent` | complete | keep |
| GERNAS-REQ-009 | Execution context | M | agent_run_id, session_id, task_id, trace_id, span_id | run ids and baggage session/task; child spans lack agent_run_id | partial | put agent_id and agent_run_id on every span inside a run |
| GERNAS-REQ-010 | Planning context and steps | M | plan_id, step_id, step_type, attempt_number | `trace_planning`, `planning_step` | partial | add `trace_step` as the common step model for workflows and graphs |
| GERNAS-REQ-011 | Tool context | M | tool_id, tool_type, tool_version, api_operation | `trace_tool`, `trace_mcp` (separate MCP schema) | duplicate | merge MCP into `trace_tool(tool_type="mcp")` |
| GERNAS-REQ-012 | AI context | M | provider, model_id, deployment_id, model_version | `gen_ai.provider.name`, `gen_ai.request.model`, `gernas.deployment_id`, `gernas.model_version` | conflicting | rename to `gernas.provider`, `gernas.model_id` |
| GERNAS-REQ-013 | Prompt context | M | prompt_template_id, prompt_version | `gernas.prompt.template_id`, `gernas.prompt.version`, `gernas.prompt.sha256` | conflicting | rename; drop sha256 (not required) |
| GERNAS-REQ-014 | Knowledge context | M | knowledge_domain, source_id, index_id, retrieval_strategy | `gernas.retrieval.*` names | conflicting | rename |
| GERNAS-REQ-015 | Security context | M | policy_id, policy_decision, risk_classification | `trace_guardrail` | complete | keep |
| GERNAS-REQ-016 | Infrastructure context | M | cluster, namespace, pod, region, environment | resource attributes from env; environment defaults to `dev` | partial | make environment mandatory; derive pod when running in Kubernetes |
| GERNAS-REQ-017 | Cost context | M | token_input, token_output, compute_units, estimated_cost | `gen_ai.usage.*`, `gernas.est_cost_aed` | conflicting | rename; estimated_cost is in the rate-card currency |
| GERNAS-REQ-018 | Outcome context | M | execution_status, business_outcome, error_category | execution_status only on use_case; error_category almost never set | partial | set execution_status on every span and classify error_category on every error |
| GERNAS-REQ-019 | No raw prompts, responses, customer data, secrets or tokens in telemetry | M | — | drop list, PII scrubbing, hashed I/O, collector redaction | complete | keep; drop the unused payload-capture fields |
| GERNAS-REQ-020 | Sensitive capture only when explicitly policy-controlled | M | — | `GERNAS_CAPTURE_PAYLOADS` plus `*.redacted` fields that no API writes | not required (speculative) | remove; capture stays a Collector policy (WP2-A-05) |
| GERNAS-REQ-021 | Use-case layer: channel, request type, user/session, business outcome | M | — | `trace_use_case` (channel, outcome) | partial | add request_type |
| GERNAS-REQ-022 | Agent runtime: run start/end, planning duration, steps, retries, loops, decisions, delegation, termination reason | M | — | `trace_agent`, `RunState` | complete | keep |
| GERNAS-REQ-023 | Multi-agent: parent/child agent, delegation path, inter-agent calls, latency, failures, hand-offs | M | — | `trace_delegation`; **no delegation path** | partial | add delegation path |
| GERNAS-REQ-024 | Tools: invocation count, latency, success/failure, authorisation decision, payload size, dependency | M | — | tool spans and metrics; **no payload size or dependency** | partial | add payload size and dependency |
| GERNAS-REQ-025 | LLM gateway/LLM: tokens, latency, retries, fallback, throttling, TTFT, tokens/sec, stop reason, model errors, cache usage | M | — | `trace_llm`/`trace_stream`; fallback never set; no cache tokens; stop reason as `gen_ai.response.finish_reasons` | partial | add fallback marking and cache tokens; rename stop reason; merge streaming into `trace_llm` |
| GERNAS-REQ-026 | Embedding: calls, latency, token volume, batch size, errors | M | — | `trace_embedding` (separate `embedding.model`/`token_count` keys) | duplicate | reuse model_id and token_input |
| GERNAS-REQ-027 | Content safety: blocked request, safety category, risk level, policy triggered | M | — | `trace_content_safety` | complete | keep |
| GERNAS-REQ-028 | RAG/search: duration, query rewriting, top-K, result count, empty result, ranking score | M | — | `trace_rag` **and** `trace_retrieval` (same operation) | duplicate | keep one: `trace_retrieval` |
| GERNAS-REQ-029 | Memory: read/write, hit, stale, size | M | — | `trace_memory` | complete | keep (fixed span name) |
| GERNAS-REQ-030 | Telemetry platform: dropped spans/logs | M | — | dropped spans and attributes counted | partial | add dropped log records |
| GERNAS-REQ-031 | KPI: Human Intervention Rate | M | — | `trace_hitl`, `start_hitl`/`complete_hitl` | complete | keep |
| GERNAS-REQ-032 | KPI: Maximum step violation, loop rate, retry rate, timeout, abandonment, policy block | M | — | `RunState` | complete | keep |
| GERNAS-REQ-033 | Attribution of every model call to use case → agent → run → user → model → cost | M | — | LLM spans lack agent and run ids | partial | covered by REQ-009 and REQ-007 |
| GERNAS-REQ-034 | Drill-down Use Case → Agent → Version → Run → Step → Span | M | — | no common step span | missing | covered by REQ-010 |
| GERNAS-REQ-035 | Cost per successful task, cost attribution | M | — | rate-card LLM cost | partial | also attribute embedding cost |
| GERNAS-REQ-036 | Logs correlated with traces | M (operational log store) | — | OTel logs bridge; **also** `StructuredLogger`, `TraceContextFilter`, `log_calls` (duplicates) | duplicate | keep only the bridge |
| GERNAS-REQ-037 | Change vendors without re-instrumenting | M | — | decorators plus OTLP | complete | keep |
| — | MAF, LangChain, LangGraph and custom support (programme brief) | M | — | MAF and LangChain adapters; **no LangGraph** | missing | add LangGraph on the common model |

Not required by the blueprint, so they will be removed (removal reasons in §4 of the final report):
- Beacon compatibility aliases and the generic `traced*` decorators;
- `track_latency`/`count_calls`/`log_calls`, `StructuredLogger`, `TraceContextFilter`;
- the HMAC compliance audit chain and its decorators;
- the HTML and audit reporters, the pretty and JSONL file sinks, the circuit breaker;
- orphan detection, prompt SHA-256, the `gernas.trace_id` mirror;
- WP1 join keys (provenance_id, work_unit_id, commit_sha);
- Opik and data-plane routing switches in the SDK;
- payload-capture config and fields, and the evaluation domain;
- the `AgenticMetrics` manual API, checkpoint context serialisation, duplicate baggage helpers;
- Beacon's study guide and demo.

## 3. Final traceability matrix (0.4.0)

Every row was verified by the named tests (`pytest tests`, 180 passed). Rows marked ★ were also verified by live export through the Collector: `scripts/e2e_pipeline_checks.py` PASS, and `gernas conformance` PASS on the four examples and on an in-process Agent Mesh run.

| ID | Requirement | M/O | Blueprint name(s) | SDK implementation | File(s) | Status | Action taken | Verification |
|---|---|---|---|---|---|---|---|---|
| GERNAS-REQ-001 | OTel-based instrumentation | M | — | OTel SDK primitives only | `tracer.py` | complete | kept | `test_packaging::test_no_auto_instrumentation_dependencies` |
| GERNAS-REQ-002 | Standardised, centralised semantic conventions | M | — | `registry/` → generated `Ctx`, `SpanName`, `Metric` | `registry/*`, `attributes.py` | complete | rebuilt on blueprint names | `test_naming`, `test_registry`, `test_no_handtyped_attrs` |
| GERNAS-REQ-003 | Standard trace/span context and propagation | M | trace_id, span_id | native span context; W3C traceparent and baggage; A2A, httpx, ASGI and MCP adapters | `context.py`, `adapters/*` | complete | mirror attribute removed | `test_context::test_cross_process_hop…`, `test_adapter_transport` ★ |
| GERNAS-REQ-004 | Exporter abstraction; not vendor-coupled | M | — | `otlp` / `console` / `off` | `exporters.py` | complete | file/pretty/memory sinks and SDK routing flags removed | `test_edge_cases::test_exporter_selection` |
| GERNAS-REQ-005 | The gateway does enrichment, PII detection, sampling and routing | M | — | Collector: generated denylist + deny-by-default allow-list + PII masks on both trace pipelines; tail sampling (data plane) and probabilistic (Opik); routing to Azure Data Explorer and Opik | `collector/otel-collector-config.yaml` | complete (local); ADX ingestion **blocked** | ADX exporter replaced the file placeholder; Opik GenAI mapping | `otelcol validate` VALID; `test_collector_deployment`; `e2e_pipeline_checks.py` 9/9 ★; Collector metrics 660 accepted / 660 sent / 0 failed |
| GERNAS-REQ-006 | Business context on every signal | M | business_domain, use_case_id, application_id | mandatory config; envelope plus resource | `config.py`, `_core.stamp_context` | complete | invented defaults removed | `test_config`, `test_decorators::test_agent_run_required…`, conformance ★ |
| GERNAS-REQ-007 | Identity context | M | user_id/tokenized_user_id, principal_type, tenant, department | `request_context()` with HMAC tokenization; `user_id` never emitted; principal_type enum-checked | `context.py`, `02_identity.yaml` | complete | renamed; API added | `test_context`, `test_security::test_denylisted_keys…` |
| GERNAS-REQ-008 | Agent context | M | agent_id, agent_name, agent_version, agent_type | `trace_agent` (agent_type mandatory); adapters | `decorators/agent.py` | complete | agent_type made mandatory | `test_decorators::test_agent_run_required…` |
| GERNAS-REQ-009 | Execution context | M | agent_run_id, session_id, task_id, trace_id, span_id | run context on every child span; session and task from `request_context` baggage | `_core.py`, `tracer._BaggageSpanProcessor` | complete | run context stamped | `test_decorators::test_children_carry_run_context…`, conformance `run_context` rule ★ |
| GERNAS-REQ-010 | Planning context and steps | M | plan_id, step_id, step_type, attempt_number | `trace_step`, `trace_planning`, `open_step` | `decorators/planning.py` | complete | `agent.step` added | `test_decorators::test_planning`, LangGraph and MAF step tests |
| GERNAS-REQ-011 | Tool context | M | tool_id, tool_type, tool_version, api_operation | `trace_tool` (MCP is a tool_type) | `decorators/tool.py`, `adapters/mcp.py` | complete | MCP schema merged | `test_decorators::test_tool_payload_sizes…`, `test_mcp_is_a_tool_type` |
| GERNAS-REQ-012 | AI context | M | provider, model_id, deployment_id, model_version | `trace_llm` / `trace_embedding` / adapters | `decorators/llm.py` | complete | renamed from `gen_ai.*` | `test_decorators::test_llm_call…` |
| GERNAS-REQ-013 | Prompt context | M | prompt_template_id, prompt_version | `trace_llm(prompt_template_id=, prompt_version=)` | `decorators/llm.py` | complete | renamed; sha256 removed | `test_decorators::test_llm_call…` |
| GERNAS-REQ-014 | Knowledge context | M | knowledge_domain, source_id, index_id, retrieval_strategy | `trace_retrieval`, LangChain retriever | `decorators/retrieval.py` | complete | renamed | `test_decorators::test_retrieval` |
| GERNAS-REQ-015 | Security context | M | policy_id, policy_decision, risk_classification | `trace_guardrail` | `decorators/safety.py` | complete | kept | `test_decorators::test_guardrail_*`, `test_edge_cases` |
| GERNAS-REQ-016 | Infrastructure context | M | cluster, namespace, pod, region, environment | resource attributes; environment mandatory; pod from the Kubernetes HOSTNAME | `config.py` | complete | environment made mandatory | `test_config::test_pod…`, `test_resource…` |
| GERNAS-REQ-017 | Cost context | M | token_input, token_output, compute_units, estimated_cost | `record_usage` and rate card; compute_units via `annotate` | `decorators/llm.py`, `cost.py` | complete | renamed; never invented | `test_decorators::test_cost_is_never_invented…`, `test_edge_cases::test_cost_lookup_edges` |
| GERNAS-REQ-018 | Outcome context | M | execution_status, business_outcome, error_category | on every span; error classification | `_core.stamp_outcome`, `run_state.error_category` | complete | added everywhere | `test_decorators::test_error_categories`, `test_cancelled_child_span…` |
| GERNAS-REQ-019 | No raw prompts, responses, customer data or secrets | M | — | denylist; payload sizes only; PII and secret scrub; 1024-character cap; Collector masks | `redaction.py`, `utils/sanitizer.py` | complete | secret scrub on registered keys added | `test_security` (all), e2e ★ |
| GERNAS-REQ-020 | Sensitive capture only under policy | M | — | the SDK never captures payloads | — | complete | capture config removed (speculative) | `test_security::test_prompts_completions…` |
| GERNAS-REQ-021 | Use-case layer signals | M | — | `trace_use_case(channel, request_type, outcome_from)` | `decorators/use_case.py` | complete | request_type added | `test_decorators::test_use_case_is_the_root…` |
| GERNAS-REQ-022 | Agent runtime signals | M | — | `RunState` (steps, retries, loops, termination) | `run_state.py` | complete | kept | `test_runtime` |
| GERNAS-REQ-023 | Multi-agent: parent/child, delegation path, hand-offs | M | — | `trace_delegation`; baggage parent_run_id and delegation_path | `decorators/delegation.py`, `context.py` | complete | delegation path added | `test_decorators::test_nested_agent…`, `test_context::test_cross_process…` |
| GERNAS-REQ-024 | Tools: count, latency, status, authorisation, payload size, dependency | M | — | `trace_tool` plus metrics | `decorators/tool.py` | complete | sizes and dependency added | `test_metrics`, `test_decorators::test_auth_failure…` |
| GERNAS-REQ-025 | LLM: tokens, latency, TTFT, tokens/s, stop reason, cache, fallback, errors | M | — | `trace_llm` (streaming merged), `mark_fallback` | `decorators/llm.py` | complete | merged and extended | `test_runtime::test_sync_stream…`, `test_decorators::test_fallback_marker` |
| GERNAS-REQ-026 | Embedding signals | M | — | `trace_embedding` (shared model_id / token_input) | `decorators/llm.py` | complete | de-duplicated | `test_decorators::test_embedding` |
| GERNAS-REQ-027 | Content safety signals | M | — | `trace_content_safety` | `decorators/safety.py` | complete | kept | `test_decorators::test_content_safety`, `test_edge_cases` |
| GERNAS-REQ-028 | RAG/search signals | M | — | `trace_retrieval` | `decorators/retrieval.py` | complete | `trace_rag` merged | `test_decorators::test_retrieval` |
| GERNAS-REQ-029 | Memory signals | M | — | `trace_memory("read"\|"write")` → `memory.access` | `decorators/memory.py` | complete | fixed span name | `test_decorators::test_memory…`, `test_edge_cases::test_memory_custom…` |
| GERNAS-REQ-030 | Telemetry platform: dropped spans/logs | M | — | `gernas.sdk.dropped_spans/logs/attributes`, `redactions` | `tracer.py` | complete | dropped logs added | `test_metrics::test_sdk_health…`, `test_collector_down` |
| GERNAS-REQ-031 | Human Intervention Rate | M | — | `trace_hitl`, `start_hitl` / `complete_hitl`, metric | `hitl.py` | complete | kept | `test_runtime::test_hitl_*`, `test_metrics` |
| GERNAS-REQ-032 | Max-step, loop, retry, timeout, abandonment, policy block | M | — | `RunState` termination reasons | `run_state.py` | complete | kept | `test_runtime`, `test_decorators::test_error_categories` |
| GERNAS-REQ-033 | Every model call attributable: use case → agent → run → user → model → cost | M | — | envelope, run context, tokenized user, model and cost on `llm.call` | — | complete | via REQ-007/009/017 | `test_adapter_agent_framework`, `test_adapter_langchain`, conformance ★ |
| GERNAS-REQ-034 | Drill-down Use Case → Agent → Version → Run → Step → Span | M | — | `use_case.request` → `agent.run` → `agent.step` → leaf spans | — | complete | via REQ-010 | `test_decorators::test_children…`, LangGraph test, Agent Mesh trace ★ |
| GERNAS-REQ-035 | Cost per successful task / cost attribution | M | — | estimated_cost on LLM and embedding, plus business_outcome | `cost.py` | **partial** | LLM and embedding only; the other §11 components are platform-metered | see Gaps |
| GERNAS-REQ-036 | Logs correlated with traces | M | — | `GernasLogHandler` (scrubbed, carries span context) | `logs.py` | complete | duplicates removed | `test_edge_cases::test_log_records…`, e2e ★ |
| GERNAS-REQ-037 | Change vendors without re-instrumenting | M | — | decorators plus OTLP; vendor mapping in the Collector | — | complete | Opik mapping moved | e2e ★ (Opik delivery through the mapping) |
| GERNAS-REQ-038 | MAF, LangChain, LangGraph and custom on one model (programme brief) | M | — | adapters on the core builders | `adapters/agent_framework.py`, `langchain.py`, `langgraph.py` | complete | LangGraph added; LangChain rewritten | `test_adapter_*`, `test_optional_dependencies`, examples ★ |
| GERNAS-REQ-039 | Centralized telemetry gateway runs in AKS (Fig. 1 "OpenTelemetry Collectors / Telemetry Gateway", Infrastructure: AKS) | M | — | Deployment ×2, Service, PDB, NetworkPolicy, ServiceAccount (Workload Identity), ConfigMap generated from the validated config | `collector/kustomization.yaml`, `collector/k8s/` | partial: validated, **not deployed** (no cluster access) | added | strict k8s v1.30 schema validation 7/7; `test_collector_deployment`; procedure + acceptance in `docs/operations/collector-aks-deployment.md` |
| GERNAS-REQ-040 | Telemetry data plane (Fig. 1 §3: trace / log / metrics stores) and a trace experience; vendor-swappable (§21) | M | — | traces → Azure Data Explorer **and** Opik; metrics and logs → ADX; exporters only in the Collector | `collector/otel-collector-config.yaml`, `collector/adx/create-tables.kql` | Opik: complete (live); ADX: **blocked** (no cluster / credentials) | added | `scripts/opik_verify.py` PASS on 4 live Agent Mesh requests (one trace across 5 processes, conformance on Opik's copy, only Gernas spans) |
| GERNAS-REQ-041 | §6 Quality: groundedness, relevance, faithfulness, answer completeness, citation quality, hallucination indicators, toxicity / safety, refusal rate ("where evaluation is enabled") | O (conditional) | — | `gernas.record_evaluation(metric, score, …)` → `evaluation.score` span + `gernas.evaluation.score` histogram | `src/gernas/evaluation.py` | complete | added | `test_kpi_metrics::test_record_evaluation_*`; `examples/custom_agent.py` |
| GERNAS-REQ-042 | §4A / §5 / §6 KPI signals (task success, steps / run, planning latency, fallback rate, TTFT, tokens / s) as metrics; KPI catalogue (§4: definition → formula → source) | M | — | `gernas.use_case.requests`, `gernas.agent.steps`, `gernas.agent.plan.duration`, `gernas.fallbacks`, `gernas.llm.ttft`, `gernas.llm.tokens_per_second`; `docs/reference/kpi-catalogue.md` | `registry/metrics.yaml` | partial: signals complete; owner / threshold / SLO / alert / retention are FAB decisions; KQL unverified (no ADX) | added | `test_kpi_metrics`; live: `gernas.use_case.requests`, `gernas.agent.steps` in the data plane |
| GERNAS-REQ-043 | §3 Document Processing: documents received / parsed / rejected, OCR latency, chunk count, parsing failures | M | — | `@gernas.trace_document(outcome_from=…, chunks_from=…)` → `document.process` (`document_type`, `document_status`, `chunk_count`) + `gernas.documents` / `gernas.document.chunks`; OCR latency = `agent.step` `step_type=document_extraction` | `decorators/document.py`; RAG `ingestion/pipeline.py` | partial: SDK + RAG ingestion instrumented and tested; no live ingestion exported yet | added | `test_document_processing`; RAG `test_ingestion_pipeline` |
| GERNAS-REQ-044 | §3 Knowledge Layer: source accessed, retrieval domain, authorization decision, document version | M | — | `trace_retrieval`: `source_id`, `knowledge_domain`, `document_version` (top result), `authorization_decision=` | `decorators/retrieval.py` | partial: SDK complete; RAG metadata has no version and no document-level access decision | added | `test_document_processing::test_retrieval_*` |

## 4. Retained technical necessities (not named by the blueprint)

| ID | What | Why it is needed |
|---|---|---|
| TN-01 | `gernas.operation` | Identifies the span operation for conformance and dashboards, independently of the span name |
| TN-02 | `gernas.framework` | Distinguishes the adapter origin on the shared model (programme requirement) |
| TN-03 | `http.request.method`, `http.route`, `http.response.status_code` | OTel HTTP semconv on the ASGI server span that carries inbound context extraction |
| TN-04 | `gernas.` key namespace | Avoids collisions (`provider`, `environment`, `pod`) with OTel semconv and third-party spans |
| TN-05 | Component-signal keys (`20_component_signals.yaml`) | Blueprint §3/§5/§6 name the signals but not the keys; they are named flat, in the blueprint style |
| TN-06 | `sdk_version`, `redaction_applied`, `token_type` | Operability: version triage, redaction audit, token-metric dimension |
| TN-07 | Span and metric names (`registry/spans.yaml`, `metrics.yaml`) | The blueprint defines none. One fixed name per operation, with identity in attributes (cardinality). |
| TN-08 | `GernasConfigError`, `GERNAS_*` env vars | Config-driven onboarding with validation |
| TN-09 | `gernas doctor` / `gernas conformance` CLI | Onboarding verification and CI enforcement. `doctor --opik` defaults to the Opik cloud URL, overridable with `OPIK_URL_OVERRIDE`; diagnostic only. |
| TN-10 | ASGI / httpx / A2A / MCP adapters | Cross-process propagation (REQ-003) for the transports the reference agent uses |
| TN-11 | Benchmark, e2e scripts, `inspect_trace` | Evidence that telemetry never slows or breaks the agent; pipeline verification |

## 5. Removal report

| Removed | Reason |
|---|---|
| `compliance/` (HMAC audit chain, `compliance_gate`, `rbac_check`, events) | Not blueprint telemetry; an audit store is an application concern. Policy decisions are covered by `trace_guardrail`. |
| `core/` (HTML and audit reports, pretty and file exporters, structured logger) | Vendor-specific local sinks and report generation; a duplicate logging path (REQ-036) |
| `circuit_breaker/` | Not required; exporter retries and queues belong to OTLP and the Collector |
| `propagation/` (baggage and context helpers) | Duplicate of `context.py` |
| `decorators/legacy.py`, `log.py`, `metrics.py`, `chain.py` | Beacon aliases, generic decorators, manual metrics API and chain span: duplicate or not in the blueprint |
| `logconfig/request_logging.py` | Duplicates the logs bridge plus the ASGI span |
| `prompt_registry.py` (`record_prompt`, sha256) | Not a blueprint field |
| `trace_mcp`, `trace_stream`, `trace_rag` | Merged into `trace_tool`, `trace_llm` and `trace_retrieval` (one schema) |
| Orphan detection, `gernas.trace_id` mirror, input/output hashes | Speculative, or a duplicate of native context; payload hashes replaced by sizes |
| `GERNAS_EMIT_*` / `gernas.route.*`, `GERNAS_CAPTURE_PAYLOADS`, join keys, evaluation domain, request_id / user_role attributes | Vendor routing belongs in the Collector; the rest is speculative or not in the blueprint |
| `report` / `audit-report` CLI, `pretty` / `audit` / `breaker` extras, `rich` / `reportlab` / `pybreaker` deps | Follow from the removals above |
| `examples/reference-agent`, `demo_all_layers.py`, `STUDY_GUIDE.md`, old traceability doc | Demo or obsolete; replaced by four minimal examples and this document |
| Agent Mesh: `compliance_gate` on write tools, `_emit_screen_guardrail`, `request_baggage`, report writing in `finalize` | Removed together with the SDK features. Mesh business logic is unchanged. |
| Agent Mesh competing instrumentation: `src/observability/setup.py` (Grafana / App Insights / console providers), `tracer.py` (raw prompt/response JSONL sink), `metrics.py` (`fab.*` metrics) + every `record_*` call, `TraceContextMiddleware` (wrote the raw user onto spans), manual `fab.*` / `mesh.request` spans and attributes in the workflow and orchestrator, `peer.*` attributes, MAF and a2a-sdk native instrumentation | A second telemetry path next to the SDK (duplicate spans, raw payloads, non-blueprint names). Verified gone: live traces in Opik contain only Gernas spans. |
| `collector/sidecar-config.yaml`; `rag-as-a-service/.../utils/telemetry.py` (commented-out stubs) | Not the target topology (gateway in AKS); dead code with no callers |

## 6. Naming inventory

- **Spans:** `use_case.request`, `agent.run`, `agent.step`, `agent.plan`, `agent.delegate`, `llm.call`, `embedding.create`, `tool.call`, `retrieval.search`, `memory.access`, `guardrail.check`, `content_safety.check`, `hitl.approval`, and the HTTP span `{method} {route}`.
- **Events:** the OTel `exception` event only.
- **Metrics (19):**
  - `gernas.agent.runs`, `gernas.agent.run.duration`, `gernas.agent.retries`, `gernas.agent.loops`
  - `gernas.agent.delegations`, `gernas.agent.delegation.duration`
  - `gernas.llm.calls`, `gernas.llm.duration`, `gernas.llm.tokens`, `gernas.llm.estimated_cost`
  - `gernas.tool.calls`, `gernas.tool.duration`
  - `gernas.retrieval.calls`, `gernas.policy.decisions`, `gernas.hitl.interventions`
  - `gernas.sdk.dropped_spans`, `gernas.sdk.dropped_logs`, `gernas.sdk.dropped_attributes`, `gernas.sdk.redactions`
- **Attributes:** `docs/reference/attribute-reference.md` (generated). The 13 blueprint domains appear verbatim as `gernas.<field>`; `trace_id` and `span_id` are native.
- **Resource attributes:** `service.name`, `service.version`, `gernas.sdk_version`, the business envelope and `environment`, plus optional `cluster`, `namespace`, `pod` and `region`.
- **Environment variables (23 `GERNAS_*`):** pinned in `tests/conformance/test_naming.py::test_environment_variables_are_exact`.
- **Decorators and public API:** pinned in `test_naming.py::test_public_api_is_exact`.
- **Entry point:** `gernas = gernas.cli:main` (`doctor`, `conformance`).
- **Adapters:**
  - `gernas.adapters.agent_framework.gernas_middleware`
  - `langchain.instrument` / `GernasCallbackHandler`
  - `langgraph.instrument_graph`
  - `a2a.GernasA2AInterceptor`
  - `mcp.GernasFastMCPMiddleware` / `mcp_meta_context`
  - `asgi.GernasASGIMiddleware`
  - `httpx.sync_event_hooks` / `async_event_hooks`

## 7. Gaps and assumptions

1. **Key namespace (TN-04).** The blueprint names none. `gernas.<field>` is assumed and needs architecture-owner sign-off.
2. **Span names, metric names and component-signal keys** are SDK conventions (TN-05, TN-07). They are pinned by tests, but need DAI confirmation.
3. **REQ-035 is partial.** The SDK attributes only LLM and embedding cost. Search, compute, storage, network and shared allocation (§11) must be joined in the data plane from platform metering.
4. **Not produced by an agent SDK.** Blueprint §6 PTU utilisation, context size and gateway throttling are LLM-gateway or Azure metrics.
5. **Enums.** `tool_type`, `agent_type`, `step_type` and `channel` have no blueprint enum, so they are free bounded strings. `principal_type` uses an assumed enum (`human` / `agent` / `service`).
6. **Framework-native instrumentation is switched off** (MAF `disable_native_telemetry()`, a2a-sdk `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`). Other applications must do the same; the SDK cannot switch off a library's own tracing for them without importing it.
7. **HITL wait mode.** An unrecognised outcome is recorded as `changed`, and a non-timeout exception as `rejected`.
8. **ADX ingestion and AKS deployment are not verified.** No ADX cluster, Azure credentials, `kubectl` or `az` are available here. Config and manifests are validated; the steps and acceptance criteria are in `docs/operations/collector-aks-deployment.md` §2–3.
9. **Agent Mesh baggage.** The mesh carries its own `fab.user` baggage (the raw username) in A2A headers. This is application baggage, not telemetry, and the SDK never copies it onto spans. The mesh team should still review it.
10. **Live MCP leg: closed 2026-10-17.** Verified with a MAF MCP client probe against the live DataLayer server (`MCPPROBE17D`). The mesh's own data and RAG agents still cannot reach MCP live, because their model (`qwen/qwen3.6-27b`) returns 404 from Groq. That is a mesh `.env` setting.
11. **Agent Mesh evaluation harness** (`workflow_evaluations/`) pushes evaluation scores to Grafana and adds OTel span events outside the SDK. It is offline mesh tooling, not the runtime path, and was not modified. The SDK now offers `record_evaluation` for blueprint §6 metrics; the harness's own safety evaluations (PII, injection, compliance) do not map onto the §6 enum and need a mesh-team decision.
12. **Agent Mesh Trace Dashboard page** (`frontend/.../TraceDashboardPage.tsx` → `/api/traces` → `data/trace_log.jsonl`) was fed by the removed raw-payload JSONL sink, so it now shows no new data. Options for the mesh team: read from Opik or ADX (the GERNAS trace stores), or remove the page. Re-adding a raw-payload sink would violate blueprint §2.
13. **Not produced by this repository** (platform or other owners): GERNAS OS lifecycle telemetry; API gateway; vector store; document processing; database; AKS / GPU / messaging / network infrastructure metrics; identity and authorization platform events; CI/CD; storage utilization; the 8 dashboards; alerts; FinOps allocation. See `checklist.md` §3 and §7.
