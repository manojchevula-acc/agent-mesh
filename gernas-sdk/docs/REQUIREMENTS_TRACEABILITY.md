# Requirement → implementation traceability

This document maps every requirement in the WP1 specification to the code that implements it and the evidence that it works.

- **Primary specification:** `md_files/IMPLEMENTATION_PLAN_WP1.md`, together with the engineering principles and prohibitions in `md_files/CLAUDE.md`.
- **Collector requirements:** taken from `md_files/IMPLEMENTATION_PLAN_WP2.md`. Only the subset this SDK depends on is in scope.
- **Scope source:** `docs/GERNAS Enterprise Telemetry Observability Blueprint.docx`. It supplied the 13 context domains and the PDPL constraints.

**Status values:**
- ✅ done and verified
- ◐ partially done; the gap is stated in the row
- ⛔ blocked by an external dependency
- — out of scope for this pass

**How each row was verified:**
- `T:` a pytest test in `gernas-sdk/tests/`
- `E2E:` checked against telemetry exported by the live Collector (see `docs/E2E_VALIDATION_REPORT.md`)
- `B:` the benchmark in `benchmarks/bench_overhead.py`

## Engineering principles (md_files/CLAUDE.md)

| # | Principle | Implementation | Verified by | Status |
|---|---|---|---|---|
| 1 | OTel SDK primitives only; no `opentelemetry-instrumentation-*` | Every span is created in `gernas/decorators/_core.py`. The deprecated SDK `LoggingHandler` is replaced by `logconfig/otel_handler.py`. httpx, A2A, ASGI and MCP propagation are built from `opentelemetry.propagate`. | T: `test_packaging.py::test_no_auto_instrumentation_dependencies`, `::test_core_modules_do_not_import_vendor_sdks`. Clean-venv install pulled no instrumentation package. | ✅ |
| 2 | Sidecar package; application code never touches the OTel SDK | Agent Mesh imports only `src/observability/gernas_telemetry.py`, which uses the public `gernas` API. | Code review of the Agent Mesh diff. | ✅ |
| 3 | Decorators are the only interface | The 16 `@trace_*` decorators plus framework adapters. Adapters are built on the same engine. | T: `test_decorators.py`, `test_adapters.py` | ✅ |
| 4 | Telemetry never breaks the application | `safe_set` handles attribute failures. `_guard` wraps every hook. If a span cannot start, the function runs untraced. Export failures are counted, never raised. | T: `test_core.py::test_safe_set_*`, `test_decorators.py::test_telemetry_failure_never_reaches_the_agent`, `test_collector_down.py`. B: 0 agent errors over 36,000 spans with the Collector down. | ✅ |
| 5 | The Collector is the backbone | The only network exporter is OTLP to `GERNAS_OTLP_ENDPOINT`. Beacon's direct Grafana and Azure exporters were removed (see MIGRATION). | T: `test_core.py::test_grpc_is_the_default_transport`. E2E: Collector → Opik and data plane. | ✅ |
| 6 | Span hierarchy `trace → agent_run → chain → llm/tool` via OTel context | `_begin`/`_end` attach and detach context. A2A uses an interceptor plus ASGI extraction. MCP uses `_meta` extraction. | T: `test_contract.py::test_all_spans_share_one_trace`, `test_parent_mismatch.py`. E2E: one live trace with 256–338 spans across 5–6 services. | ✅ |
| 7 | Environment variables only | `gernas/config.py` reads `GERNAS_*` only. There are no hard-coded endpoints, keys, models or deployments in `gernas/`. | T: `test_core.py::test_from_env_reads_every_documented_variable` | ✅ |
| 8 | PDPL by default | `gernas/redaction.py` runs on every write. The registry action `drop` removes the prompt, completion, framework message payloads and raw user id. PII patterns are scrubbed from unregistered strings, exception messages and log bodies. Baggage-to-span copying is allow-listed, so `fab.user` is never copied. | T: `test_redaction.py`, `test_core.py::test_safe_set_skips_none_and_applies_pdpl`. E2E: `e2e_pipeline_checks` (raw prompt removed, Emirates ID masked), and the live mesh trace has no payload or identity keys. | ✅ |

## WP1·A — SDK core

| Task | Requirement (Done when …) | Implementation | Verified by | Status |
|---|---|---|---|---|
| CORE-01 s1 | Package scaffold, pinned OTel deps, `pip install -e .[dev]` | `pyproject.toml` (`gernas-telemetry`, version taken from `gernas/_version.py`) | Editable install in `agent-mesh/.venv`. Wheel and sdist build with no warnings. Clean-venv install on Python 3.13. | ✅ |
| CORE-01 s2 | `GernasConfig` from env; a missing `GERNAS_OTLP_ENDPOINT` raises `KeyError` | `gernas/config.py` | T: `test_core.py::test_missing_endpoint_raises_keyerror_naming_the_variable` | ✅ |
| CORE-01 s3 | Idempotent `init_gernas()`; gRPC by default, HTTP optional | `gernas/tracer.py::init_gernas` | T: `test_init_twice_returns_the_same_provider`, `test_http_transport_targets_the_collector_http_port` | ✅ |
| CORE-01 s4 | Exporter factory; the destination is always the Collector | `gernas/exporters.py` (also fixes Beacon's broken `off` exporter) | T: `test_grpc_is_the_default_transport`, `test_off_exporter_regression` | ✅ |
| CORE-01 s5 | `safe_set` never raises and counts `gernas.sdk.dropped_attributes` | `tracer.py::safe_set`, observable counters | T: `test_safe_set_*` (4 tests) | ✅ |
| CORE-01 s6/8 | `inject_context` / `extract_context`; thread and queue hops form one record | `gernas/async_context.py` | T: `test_context_round_trips_through_headers`, `test_thread_hop_produces_one_connected_record`, `test_async_context.py::test_queue_hand_off_produces_one_connected_record` | ✅ |
| CORE-01 s7 | With the Collector down: zero errors, a nonzero drop count | `CountingSpanExporter`, `GernasBatchSpanProcessor` | T: `test_collector_down.py`. B: 36,000 dropped of 36,000 emitted, 0 errors. | ✅ |
| CORE-02a | `@trace_agent`, `@trace_llm` (Azure and Anthropic shapes, cost, envelope, no raw prompt) | `decorators/agent.py`, `decorators/llm.py`, `cost.py` | T: `test_decorators.py` (agent ×5, llm ×5). E2E: live `llm.call` spans carry tokens and `est_cost_aed`. | ✅ |
| CORE-02a s6 | Reference agent → Collector → Opik UI | `examples/reference-agent/main.py` | E2E: `gernas doctor --opik` PASS. The reference trace was exported through the Collector. Opik REST lookups for doctor, e2e and mesh runs all succeeded. | ✅ |
| CORE-02b | `@trace_tool`, `@trace_mcp`, `@trace_stream`, `@trace_hitl`; Collector-outage test | `decorators/tool.py`, `decorators/llm.py`, `hitl.py` | T: tool/mcp tests, `test_streaming.py` (12), `test_hitl.py` (12), `test_collector_down.py` | ✅ |
| CORE-02c | `@trace_rag`, `@trace_retrieval`, `@trace_embedding`, `@trace_memory`, `@trace_planning` | `decorators/retrieval.py`, `memory.py`, `planning.py` | T: `test_decorators.py` | ✅ |
| CORE-02c s6 | SDK v1.0 milestone; p95 under 1 ms; CI conformance gate | Benchmark and conformance CLI exist. **Publishing to the GitLab registry was not done:** this pass produces a local wheel only, version 0.3.0. | B: worst p95 0.20 ms. T: `test_contract.py` | ◐ (publish) |
| CORE-02d | `@trace_chain`, `@trace_use_case`, `@trace_delegation`, `@trace_guardrail`, `@trace_content_safety`; all 16 used by a demonstration agent; no span left open | `decorators/chain.py` and `adapters/langchain.py`, `use_case.py`, `delegation.py`, `safety.py` | T: `test_contract.py::test_every_decorator_emits_its_required_attributes`. E2E: reference agent conformance PASS on Collector output for all 17 operations. Streaming, cancellation and GC tests. | ✅ |
| CORE-03 | Loop detection, runaway steps, retry count, timeout vs slow | `gernas/run_state.py` | T: `test_run_state.py` (11) | ✅ |
| CORE-04 | HITL spans that survive hours; outcome capture; prompt provenance | `gernas/hitl.py`, `prompt_registry.py` | T: `test_hitl.py`. E2E: live credit_officer approval produced `hitl.approval` with outcome=approved and waited_ms=1280. | ✅ |
| CORE-05 | Generators, `asyncio.create_task`, queue hops; the wrong-parent test runs on every build | `decorators/_core.py::iterate_*`, `async_context.gernas_task` | T: `test_streaming.py`, `test_async_context.py`, `test_parent_mismatch.py` | ✅ |
| CORE-06 s1 | Kill switches `GERNAS_EMIT_OPIK` / `GERNAS_EMIT_DATAPLANE` | Resource attributes `gernas.route.*` plus Collector `filter/route_*` processors | E2E: `e2e_pipeline_checks.py`. With opik=false the span is absent from Opik and present in the data plane. With dataplane=false it is the reverse. | ✅ |
| CORE-06 s2 | Opik span shape (`input`, `output`, `thread_id`) | Collector `transform/opik_shape` | Config validated. `thread_id` mapping verified on Collector output. | ✅ |
| CORE-06 s3 | Per-destination sampling | `probabilistic_sampler/opik` and `tail_sampling/dataplane` with always-keep policies | `otelcol-contrib validate` | ✅ (rates set by env) |
| CORE-06 s4 | No double recording | Each span is emitted once and fanned out by the Collector | E2E: each run_id appears exactly once in the data plane | ✅ |
| CORE-07 | ≥ 90% coverage, contract tests, overhead benchmark, Collector down and slow | `tests/`, `benchmarks/bench_overhead.py`, `reports/overhead_report.md` | 201 tests pass, 90.3% coverage. B: all acceptance rows PASS. | ✅ |
| CORE-08 s1 | Versioned package installable by version | Wheel and sdist. **Publish to the FAB GitLab registry needs registry credentials.** | Clean-venv `pip install dist/gernas_telemetry-0.3.0-py3-none-any.whl` | ◐ |
| CORE-08 s2/3 | Onboarding runbook; `gernas doctor` | `docs/onboarding-runbook.md`, `gernas/cli/doctor.py` | E2E: `gernas doctor --opik` 5/5 PASS. T: `test_cli.py` | ✅ |
| CORE-08 s4 | Reference agent using all 16 decorators | `examples/reference-agent/main.py` | E2E: conformance `--require-op` for all 17 operations PASS | ✅ |

## WP1·B — Canonical semantic context

| Task | Implementation | Verified by | Status |
|---|---|---|---|
| CTX-01 registry (16 domains) | `registry/domains/*.yaml` with 146 fields. Each field has name, type, enum, required, sensitivity, cardinality, rollup, action, owner and example. **Sign-off by DAI and Data Governance is pending (R5).** | T: `test_registry.py` (validator: duplicate names, missing properties, PDPL rules, 16 domains) | ◐ (sign-off) |
| CTX-02 generated constants, cross-language conventions, lint, CI drift check | `registry/generate.py` generates `gernas/attributes.py`, `semantic-conventions.yaml` and `docs/attribute-reference.md`; `--check` fails if anything is stale. `tests/test_no_handtyped_attrs.py` is the AST lint. | T: `test_generated_artefacts_are_up_to_date`, `test_the_lint_catches_an_injected_violation` | ✅ |
| CTX-03 identifier lifecycle | `docs/identifier-lifecycle.md`. Session id and parent run id travel in baggage. Child runs set `parent_run_id` and get a new `agent_run_id`. Orphans are detected and counted. | T: queue hop, cross-process, orphan tests. E2E: mesh child runs link to their parent's run id. | ✅ |
| CTX-04 cross-system join keys | `@trace_agent` emits `gernas.trace_id`, `provenance_id`, `work_unit_id` and `commit_sha` from env. **Join validation queries against a seeded store are blocked: the store is not chosen (R1).** | T: resource and agent attribute tests | ◐ |
| CTX-05 redaction matrix | `registry/redaction-matrix.yaml` (generated, one row per field, each mapped to a Collector rule). **CISO sign-off is pending (R3).** | T: `test_redaction_matrix_covers_every_field_with_an_action`, `test_collector_allowlist_matches_registry` | ◐ (sign-off) |
| CTX-06 cardinality | `cardinality` and `rollup` on every field. The validator forbids unbounded fields as rollup dimensions. Conformance flags breaches. **The rollup-budget document and the rollup pipeline are blocked on R1.** | T: `test_unbounded_fields_are_never_rollup_dimensions` | ◐ |
| CTX-07 conformance | `gernas conformance` checks required fields, unregistered fields, restricted fields, enums and cardinality. Exit 0/1 with a JSON report. It reads Collector OTLP-JSON or SDK JSONL. **The version-policy document and the GitLab CI template are not written.** | T: `test_contract.py`, `test_cli.py`. E2E: run on Collector output | ◐ |

## Agent Mesh integration (reference application)

The integration module is `agent-mesh/src/observability/gernas_telemetry.py`. If the SDK is missing it falls back to pass-through decorators.

| Execution path | Location | Gernas API | Evidence |
|---|---|---|---|
| Startup and shutdown for every process | `run.py`, `api_server.py`, `a2a_server.py`, `devui_app.py`, DataLayer `app.py` / `mcp_server/server.py`, RAG `main.py` / `mcp_integration/server.py` | `init_telemetry` / `init_gernas`, `finalize` | E2E: spans from every service |
| Use-case root and business outcome | `src/mesh/orchestrator.py::handle_request` | `@trace_use_case(channel, outcome_from)` | E2E: one root per request, `business_outcome=success` |
| 6 workflow executors | `src/mesh/workflow.py` | `trace_stage` (that is, `@trace_agent`) plus `mark_policy_block` and `record_retry` | E2E: per-stage `agent.run` spans; the guardrail block ends with `policy_block` |
| Remote agents (4 nodes) | `src/agents/agent_factory.py` | MAF Agent and Chat middleware; Function middleware on MCP nodes | E2E: node `agent.run` linked to the caller's run; node `llm.call` carries tokens and cost |
| A2A hand-offs | `src/a2a/clients.py`, `src/a2a/hosting.py` | `@trace_delegation`, `GernasA2AInterceptor`, `GernasASGIMiddleware` | E2E: cross-process trace continuity (this replaces the httpx auto-instrumentation that had been commented out) |
| Peer tools | `src/tools/collaboration_tools.py` | `@trace_tool`, `compliance_gate`, `record_retry` | E2E: `tool.query_structured_data` → `agent.delegate` → data_agent |
| MCP client and server | data and RAG nodes; DataLayer FastMCP; RAG MCP | `mcp.call` (client), `GernasFastMCPMiddleware`, `mcp_meta_context` + `@trace_rag` | E2E: `datalayer_mcp` and `rag_mcp` server spans nested under the agent's `mcp.call` |
| Raw LLM calls (summarizer, cache judge, entity extractor) | `src/memory/summarizer.py`, `src/cache/*.py` | `chat_post` (`@trace_llm` plus prompt provenance) | E2E: `llm.call` with `prompt.template_id=conversation_summary`, tokens and cost |
| Memory | `src/memory/conversation_store.py`, summarizer | `@trace_memory` (read, write, summarize) | E2E: `memory.read` with hit=False, `memory.write`, `memory.summarize` |
| Semantic cache | `src/cache/semantic_cache.py::lookup_top_n` | `@trace_retrieval` | E2E: `retrieval.search` |
| Guardrails and output redaction | `src/guardrails/deterministic_filters.py` | `@trace_guardrail`, `@trace_content_safety`, `guardrail_check` | E2E: Q6 `guardrail.check` deny with status OK |
| HITL | `src/hitl/approval_store.py` | `start_hitl` / `complete_hitl` | E2E: live approval span |
| Background summarisation task | orchestrator | `spawn` (that is, `gernas_task`) | E2E: `memory.summarize` sits inside the request trace |
| Streaming (SSE) | `api_server.py` | Covered by the ASGI middleware, which keeps the request span open for the whole stream | Deviation D4 (Agent Mesh streams no LLM tokens) |
| LangChain chains and explicit planning | — | Not present in Agent Mesh | Covered by the reference agent and unit tests (D5) |

## Out of scope or blocked in this pass

- **Tier 1–4 components** (WP1-T1..T4, 24 components). These need component owners and Azure access. Agent Mesh covers the agent-runtime, orchestration, tools, LLM, RAG, memory and content-safety signal paths.
- **WP1-PTU-01.** Blocked on R4: Azure Monitor access.
- **WP1-E2E-02/03.** A 24-component end-to-end run and a load test at 580M spans per year in AKS. Local equivalents were run instead: the benchmark and the pipeline checks.
- **WP2.** HA gateway on AKS, TLS/OIDC receivers, registry enrichment, CISO evidence corpus, data-plane schema (R1), alerts, runbooks, FinOps. Only the dev Collector config and the sidecar config are delivered.
- **Sign-offs** from CISO, DAI, Data Governance, MRM and Finance.
