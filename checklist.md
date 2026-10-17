# Gernas SDK — blueprint compliance, Collector (AKS) → ADX + Opik readiness, Git hygiene

> Living record of the audit run on 2026-10-17. It sits at the root of the single git repository (`agent-mesh/`); the VS Code workspace root (`telemetry/`) is not a repository.
>
> - Requirement detail and the full evidence matrix: `gernas-sdk/docs/BLUEPRINT_TRACEABILITY.md` §3.
> - Architecture and deployment: `gernas-sdk/docs/collector-aks-deployment.md`.
>
> **Scope change during the audit (user instruction, 2026-10-17).** The Collector must send traces to **both** the data plane (Azure Data Explorer) **and** Opik. This replaces the brief's "Opik only, ADX deferred". Metrics and logs go to ADX, because Opik ingests traces only.

## A. Executive status

**Architecture**

```text
Agent Mesh / any app → Gernas SDK (OTLP) → OTel Collector gateway (AKS pod)
                                             ├─ Azure Data Explorer: traces, metrics, logs
                                             └─ Opik Cloud: traces
```

**Phase:** complete. The remaining items are blocked on infrastructure (section K).

**Counting rules**
- One row per requirement ID: GERNAS-REQ-001…040 in the traceability matrix.
- *Verified* means code exists **and** the behaviour passed a test or a live check.
- *Partial* means some acceptance criteria are unmet.
- *Blocked* means verification needs infrastructure or credentials that are not available.
- Totals reconcile with the matrix.

| Identified | Verified complete | Partial | Missing | Failed | Blocked |
|---|---|---|---|---|---|
| 40 | 37 | 1 (REQ-035) | 0 | 0 | 2 (REQ-039 AKS deployment, REQ-040 ADX leg) |

## B. Repository inventory

| Item | Finding |
|---|---|
| Git roots | One repository: `agent-mesh/` (`git rev-parse --show-toplevel`). There are no submodules, no nested `.git` and no `.gitmodules`. Branch `beacon_kit`. |
| Blueprint | `docs/GERNAS Enterprise Telemetry Observability Blueprint.docx`, re-extracted in full: all paragraphs, 8 tables, and Figure 1 inspected as an image. |
| Secondary requirement docs | `docs/BRIEFING_NOTES.md`, `DEVELOPER_BRIEF.md`, `SPRINT_PLAN.md`, `collector-topology.md`, `md_files/IMPLEMENTATION_PLAN_WP1/WP2.md`. The blueprint is authoritative. |
| SDK public API | `gernas/__init__.py` `__all__`, pinned by `tests/test_naming.py::test_public_api_is_exact`. |
| Agent Mesh integration points | `agent-mesh/src/observability/gernas_telemetry.py` (single module), `src/mesh/{orchestrator,workflow}.py`, `src/agents/agent_factory.py`, `src/a2a/{clients,hosting}.py`, `src/tools/collaboration_tools.py`, `src/memory/*`, `src/cache/*`, `src/guardrails/*`, `src/hitl/*`. Entrypoints: `run.py`, `api_server.py`, `a2a_server.py`, `devui_app.py`, `demos/gernas_e2e_queries.py`. |
| Services using the SDK | DataLayer MCP (`GernasFastMCPMiddleware`), RAG MCP (`trace_retrieval` + `mcp_meta_context`), RAG REST (`init_gernas` + ASGI middleware). |
| Collector and deployment | `gernas-sdk/collector/otel-collector-config.yaml`, `kustomization.yaml`, `k8s/`, `adx/create-tables.kql`, `local-dataplane-file.yaml` (local only). Binary otelcol-contrib 0.162.0, not committed. |
| Tests | `gernas-sdk/tests` (192), `agent-mesh/tests` (offline mesh suite), `rag-as-a-service/tests` (27). |
| CI/CD | None in the repository. |
| Tooling | Python 3.14.6, OTel 1.45.1, otelcol-contrib 0.162.0, ruff 0.17.1, mypy 2.4.0. **Unavailable:** kubectl, helm, docker, az, Azure / ADX credentials. |

## C. Blueprint traceability matrix

The stable IDs are `GERNAS-REQ-NNN`. The full table, with blueprint reference, implementation, file, status, action and verification for each row, is in `gernas-sdk/docs/BLUEPRINT_TRACEABILITY.md` §3. Rows added or changed by this audit:

| ID | Blueprint ref | Requirement | Existing state → action | Status | Verification and result |
|---|---|---|---|---|---|
| REQ-005 | §1 bullets; Fig. 1 §2 | Gateway: enrichment, PII/secret detection, sampling, routing | The file placeholder was replaced by the ADX exporter; PDPL processors run on both trace pipelines | Passed (local); ADX leg → REQ-040 | `e2e_pipeline_checks.py` 9/9 PASS; Collector metrics 660 accepted / 660 sent / 0 failed |
| REQ-019 | §2 Critical principle | No raw prompts, responses, credentials or account numbers | Fixed: the PII scrubber corrupted hex ids (phone pattern) and IBAN matched lower-case hex; MAF `ENABLE_SENSITIVE_DATA` and a raw-payload JSONL sink were active in the mesh | Passed | `tests/test_security.py` 23 passed; the live Opik copy contains no payload keys (conformance `restricted` rule) |
| REQ-003, REQ-009, REQ-023 | §1 "standard trace/span context"; §2 Execution; §3 Multi-agent | One correlated trace across agents | The competing mesh middleware was removed; propagation goes only through the SDK | Passed (live) | `opik_verify.py`: each of 4 requests is **one** Opik trace across 5 processes, every parent link resolves, child runs carry `parent_run_id` |
| REQ-036 | §2 envelope "every signal" | Logs correlated with traces | Mesh log and audit correlation now read `gernas.correlation()` | Passed | `test_context::test_correlation_exposes_trace_run_and_request_ids` |
| REQ-038 | Programme brief | One model for MAF, LangChain, LangGraph and custom; no competing instrumentation | `disable_native_telemetry()` for MAF; a2a-sdk tracing off | Passed (live) | `opik_verify.py` "only Gernas SDK spans" PASS ×4. Before the change: 402 a2a-sdk and 74 MAF / `fab.*` foreign spans per run |
| REQ-039 (new) | Fig. 1 "OpenTelemetry Collectors / Telemetry Gateway", Infrastructure "AKS" | Collector runs in an AKS pod | No manifests existed → kustomize + Deployment / Service / PDB / NetworkPolicy / ServiceAccount (Workload Identity) added | **Blocked** (validated, not deployed) | Strict k8s v1.30 schema 7/7 VALID; `test_collector_deployment` 6 passed; image tag exists |
| REQ-040 (new) | Fig. 1 §3 data plane (trace, log, metrics stores); §21 vendor change | Data plane + trace experience | Traces → ADX **and** Opik; metrics and logs → ADX | Opik **passed live**; ADX **blocked** | Opik: `opik_verify.py` PASS ×4. ADX: `otelcol validate` VALID; no cluster available |

## D. Architecture compliance

| ✓ | Item | Evidence |
|---|---|---|
| ☑ | Gernas SDK is the authoritative implementation of required observability behaviour | Architectural search 1 (section G #12): no `opentelemetry` / tracer / meter / provider use in the mesh runtime except the application-baggage module |
| ☑ | Context and baggage handling centralized | The SDK's `request_context` / `inject_context` / A2A interceptor / ASGI middleware. The mesh's `fab.*` baggage is application business context (audit trail, peer-reasoning files) carried over the SDK-configured propagator; it is never emitted as telemetry (Finding F-17) |
| ☑ | Attributes and semantic conventions centralized | `registry/` → `gernas/attributes.py`; `tests/test_no_handtyped_attrs.py`; all hand-typed `fab.*` / `mesh.*` / `peer.*` span attributes removed |
| ☑ | Decorators and instrumentation APIs centralized | The mesh uses only `gernas_telemetry` wrappers around SDK APIs |
| ☑ | Agent Mesh uses the SDK for the required observability | Live Opik traces contain only Gernas spans (`opik_verify` ×4) |
| ☑ | Agent Mesh business logic intact | Mesh offline suite identical to the pre-change baseline (5 known failures before and after); the 4 live requests gave the expected outcomes (answer / compliance block / guardrail block) |
| ☑ | SDK reusable by future applications | No mesh names in the package (search #13 returns 0); 4 framework examples in `gernas-sdk/examples/`; optional-dependency test |
| ☑ | No competing observability implementation without justification | Removed (section I). Justified exceptions: mesh application baggage (F-17); offline `workflow_evaluations/` harness, out of runtime scope (F-16) |
| ☑ | Collector configuration suitable for an AKS pod | Pod-IP bind, health probes, container-aware memory limiter, Workload Identity, NetworkPolicy, PDB, non-root read-only FS; validated (section H) |
| ☑ | Trace backends are exactly ADX (data plane) and Opik, per user instruction | `test_collector_deployment::test_traces_go_to_adx_and_opik_metrics_and_logs_to_adx` |
| ☑ | Out-of-scope functionality removed or justified | Section I |
| ◐ | Required telemetry reaches the stores with correct correlation and attributes | Opik: verified live. ADX: **blocked** |
| ☑ | Tests and documentation match the implementation | Docs updated (section F); search #15 finds no stale references outside historical documents |

## E. Findings register

| ID | Sev | Location | Observed | Expected | Root cause | Action | Verification |
|---|---|---|---|---|---|---|---|
| F-01 | Critical | `agent-mesh/.env` (local, untracked) | `ENABLE_SENSITIVE_DATA=true`: MAF spans captured full prompts, responses and tool arguments | No raw payloads | MAF default setup | Set to false; MAF instrumentation disabled by `disable_native_telemetry()`; template set to false | Live traces show no MAF spans |
| F-02 | Critical | `.env` `ENABLE_TRACE_JSONL=true`; `src/observability/tracer.py` | Raw prompt / response JSONL trace sink active | No competing sink | Legacy module | Module deleted; flag false | Search #14 returns 0 references |
| F-03 | High | `src/observability/metrics.py` + 30 `record_*` calls | Parallel `fab.*` metrics | Metrics only through the SDK | Legacy | Module and calls removed (AST transform, logic preserved) | Mesh suite baseline unchanged |
| F-04 | High | `src/a2a/hosting.py` `TraceContextMiddleware`; `api_server.py` fallback | Duplicate extraction middleware; wrote the raw user (`fab.inbound.user`) onto spans | SDK ASGI middleware only | Legacy fallback | Removed | Live A2A hops show `POST /` server spans from the SDK only |
| F-05 | High | `src/mesh/workflow.py` (7 spans, 60 attributes, 21 events), `orchestrator.py` (`mesh.request`) | Manual `fab.*` spans parallel to `agent.step` and `guardrail.check` | Spans only through the SDK | Pre-SDK instrumentation | Removed with an AST transform (`with` blocks unwrapped, empty blocks handled); diff reviewed with `git diff -w` | Mesh suite baseline unchanged; live traces clean |
| F-06 | High | MAF native instrumentation | Duplicate `invoke_agent` / `chat` / `execute_tool` / `workflow.*` spans | One instrumentation path | Framework default | SDK `disable_native_telemetry()`, called by the mesh integration | Test plus live traces |
| F-07 | High | a2a-sdk built-in tracing | 402 `a2a.server.*` and 14 `a2a.client.*` spans per two requests | One path | Library default (decided at import) | `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`, set by the integration before `a2a` is imported | Re-run: 0 foreign spans ×4 |
| F-08 | **Critical (git)** | Root `.gitignore:16` `observability/` | **`agent-mesh/src/observability/gernas_telemetry.py` was never committed**; a fresh clone would fail with ImportError | Integration module tracked | Unanchored ignore pattern | Anchored to `/observability/`; file staged | `git check-ignore` returns nothing; the file is staged as A |
| F-09 | High | `gernas/utils/sanitizer.py` | Phone pattern rewrote digit runs inside hex ids (`run_1fe[PHONE]b…`) in logs and exception messages; case-insensitive IBAN pattern could match hex | Identifiers intact; PII still caught | Unanchored regex; global IGNORECASE | Phone anchored to non-identifier boundaries; patterns case-sensitive with inline `(?i)` where needed | 3 regression tests; probe 9/9 |
| F-10 | Medium | Collector config | Data-plane `file` placeholder; receivers bound to 127.0.0.1; fixed MiB memory limiter | ADX exporter; pod-bindable; container-aware | Local-only design | Rewritten; local overlay keeps file output for dev | `validate` VALID (both modes) |
| F-11 | Medium | — | No AKS deployment artefacts | Validated manifests | Not built yet | kustomize + `k8s/` + ADX KQL + deployment doc | Strict schema 7/7 |
| F-12 | Medium | `collector/sidecar-config.yaml` | Sidecar topology, not the target | Gateway | — | Removed | — |
| F-13 | Medium | `src/observability/setup.py`, `src/config.py`, `.env.example` | Dead Grafana / App Insights / console backend setup | Only the SDK → Collector path | Legacy | Removed; template rewritten | Search #14 |
| F-14 | Low | `rag-as-a-service/src/gernas_rag/utils/telemetry.py` | Commented-out code | Removed | Left by session 1 | Deleted | RAG suite (F-18) |
| F-15 | Medium | Tracked artefacts | 4.7 MB generated HTML report, PDF, HTML report, 6 trace-dump JSONs, stray log file, IDE settings tracked | Not versioned | No ignore rules | `git rm --cached` (local copies kept) + ignore rules | `git ls-files -ci --exclude-standard` returns 0 |
| F-16 | Info | `agent-mesh/workflow_evaluations/` | Offline evaluation harness pushes scores to Grafana and adds span events outside the SDK | Eventually through the SDK | Mesh-team tooling | **Not modified** (not the runtime path; unrelated subsystem) | Documented as gap 11 |
| F-17 | Info | `agent-mesh/src/observability/baggage.py` | Application `fab.*` baggage (user, role, request) for the audit trail and peer files | Justified application context | Business requirement | Kept; reads routed through it; docstring corrected; never copied to spans | `_BaggageSpanProcessor` allow-list; gap 9 (PDPL review of the raw username in headers) |
| F-18 | Info | `rag-as-a-service/tests/integration/test_ingestion_pipeline.py::test_ingest_file_end_to_end` | Fails: input document `FAB_Credit_Pricing_Policy_v2.4.pdf` missing | — | Test data absent | None (unrelated to telemetry) | 26 passed / 1 failed |
| F-19 | Low | `datalayer-as-service/.gitignore` `.env.*` | Would ignore an `.env.example` template | Templates trackable | — | `!.env.example` added | — |

## F. Implementation change log

| Files | Reason | REQ IDs | Tests |
|---|---|---|---|
| `gernas-sdk/collector/otel-collector-config.yaml` (rewritten), `local-dataplane-file.yaml` (new), `scripts/run_collector.py` | AKS gateway; traces → ADX + Opik, metrics / logs → ADX; local overlay | 005, 039, 040 | `test_collector_deployment`; `otelcol validate` |
| `gernas-sdk/collector/kustomization.yaml`, `k8s/*.yaml`, `adx/create-tables.kql` (new) | AKS deployment and ADX schema | 039, 040 | Strict schema; `test_collector_deployment` |
| `gernas-sdk/collector/sidecar-config.yaml` (deleted) | Not the target topology | — | — |
| `gernas-sdk/gernas/context.py` (`correlation()`), `__init__.py` | Central correlation ids for application logs and audit | 036 | `test_context`, `test_naming` |
| `gernas-sdk/gernas/adapters/agent_framework.py` (`disable_native_telemetry()`) | Single instrumentation path for MAF apps | 038 | `test_adapter_agent_framework` |
| `gernas-sdk/gernas/utils/sanitizer.py` | F-09 identifier corruption | 019 | `test_security` (+3) |
| `gernas-sdk/scripts/opik_verify.py` (new), `cli/conformance.py`, docstrings | Opik-side verification; vendor-neutral examples | 040, 038 | Live run |
| `gernas-sdk/docs/collector-aks-deployment.md` (new); `BLUEPRINT_TRACEABILITY.md`, ADR 0003, runbook, developer guide, README, `IMPLEMENTATION_LOG.md`, root `CLAUDE.md` | Documentation matches the implementation | all | — |
| `agent-mesh/src/observability/{setup,tracer,metrics}.py` (deleted); `__init__.py`, `baggage.py`, `logging_config.py`, `gernas_telemetry.py` | F-02, F-03, F-06, F-07, F-13; correlation via the SDK | 019, 036, 038 | Mesh suite |
| `agent-mesh/src/mesh/{workflow,orchestrator}.py`, `src/a2a/{clients,hosting}.py`, `src/middleware/{audit_middleware,tool_call_logger}.py`, `src/tools/collaboration_tools.py`, `src/config.py`, `api_server.py`, `devui_app.py`, `demos/gernas_e2e_queries.py`, `.env.example` | F-04, F-05, F-13; harness uses `task_id` instead of a provider span processor | 003, 009, 038 | Mesh suite + live run |
| `rag-as-a-service/src/gernas_rag/utils/{telemetry.py (deleted), __init__.py}` | F-14 | — | RAG suite |
| `.gitignore`, `agent-mesh/.gitignore`, `datalayer-as-service/.gitignore` | F-08, F-15, F-19 | — | Section J |
| Local `agent-mesh/.env` (untracked, not in git) | F-01, F-02: `ENABLE_INSTRUMENTATION`, `ENABLE_SENSITIVE_DATA`, `ENABLE_TRACE_JSONL`, `ENABLE_BUSINESS_METRICS` set to false; `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false` added. No other line touched. | 019 | Live run |

## G. Test execution log (2026-10-17, Windows 11, Python 3.14.6, OTel 1.45.1)

| # | Command / procedure | Result |
|---|---|---|
| 1 | `cd gernas-sdk; python -m pytest tests --cov=gernas` | **192 passed**, coverage 91.13 % (gate 90 %). Re-run after every change. |
| 2 | `ruff check gernas tests registry scripts benchmarks examples`; `ruff format --check …` | All checks passed; formatted |
| 3 | `mypy gernas` | Success: no issues in 41 files |
| 4 | `python registry/generate.py --check` | Up to date |
| 5 | `otelcol-contrib validate --config collector/otel-collector-config.yaml` (with `ADX_CLUSTER_URI` set) | VALID. Without it: "clusterURI config is mandatory" (fail-fast, as intended). |
| 6 | `python scripts/run_collector.py --validate` (local overlay) | VALID |
| 7 | Rendered kustomization → strict JSON schema, Kubernetes v1.30.0 (yannh standalone-strict) | 7/7 VALID; cross-references OK. Docker Hub: `otel/opentelemetry-collector-contrib:0.162.0` exists |
| 8 | `python -m build` + clean-venv install + `pip check` + smoke | Built; installs; "No broken requirements"; `init_gernas` + `trace_agent` + `correlation()` OK; the wheel contains no tests, examples or Collector files |
| 9 | Agent Mesh offline suite, per file | test_agent_mesh 19/1, test_cache 44/3, test_cache_phases 11/1, test_config 7/0, test_memory 6/0. **Identical to the pre-change baseline** (5 known failures); test_rbac_matrix still hangs (known) |
| 10 | `pytest rag-as-a-service/tests` | 26 passed, 1 failed (F-18, missing input PDF) |
| 11 | **Live E2E:** Collector + DataLayer MCP + RAG MCP + 4 A2A nodes, `demos/gernas_e2e_queries.py --prefix LIVE17A`, then `opik_verify.py` | Business outcomes as expected; Opik: one trace per request, links OK, conformance PASS, **FAIL: a2a-sdk foreign spans** → F-07 fixed |
| 12 | Same after the fix (`--prefix LIVE17B`), `opik_verify.py --task-id LIVE17B000…003` | **PASS ×4**: 1 trace / request across 5 processes; parent links resolve; GERNAS conformance on Opik's copy; `llm.call` typed `llm` with model; **only Gernas spans**. Errors visible (`data_agent` / `rag_agent` 404 → `execution_status=error`, `termination_reason=error`); blocks → `termination_reason=policy_block`. Evidence: `gernas-sdk/reports/opik_verify_LIVE17B00*.json` (local, ignored) |
| 13 | `gernas conformance --file collector/out/dataplane-traces.json --service <each mesh service> --gernas-only` | PASS ×5 (data-plane copy) |
| 14 | `python scripts/e2e_pipeline_checks.py` | First run 8/9 (log body corrupted → F-09 fixed); re-run **9/9 PASS** (PDPL deletion, deny-by-default, Emirates ID mask, envelope, run context, metric and log routing, Opik delivery) |
| 15 | Collector self-metrics `:8888` | `receiver_accepted_spans` 660, `refused` 0; `exporter_sent_spans` Opik 660, data plane 660; no send failures; Opik queue 0 |
| 16 | Architectural searches (final) | (1) direct OTel in mesh runtime: none besides `baggage.py`; (2) exporters / backends outside the Collector: none (2 prose mentions); (3) mesh names inside the SDK package: 0; (4) removed modules referenced: none; (5) secrets in tracked files / staged diff / history: test fixtures only |

## H. Collector and data-plane readiness

| Check | Status | Evidence |
|---|---|---|
| Collector config validation | ✅ Validated locally | G #5, #6 |
| SDK → Collector connectivity | ✅ Tested (running Collector) | G #12, #15 |
| Collector → Opik compatibility | ✅ Verified against Opik docs (OTLP/HTTP, traces only, 3 headers) and live | G #12 |
| Collector startup and health | ✅ Local ("Everything is ready", health_check ready) | Collector log |
| Export success, Opik | ✅ 660 / 660, 0 failed | G #15 |
| Opik trace visibility and correlation | ✅ Verified by REST API (`opik_verify.py`) | G #12 |
| Collector → ADX | ⛔ **Blocked**: no ADX cluster or credentials. Exporter present in 0.162.0 (Beta); schema from upstream README | `collector/adx/create-tables.kql` |
| Data-plane processors (local substitute) | ✅ | G #13, #14 |
| Retry and error handling | ✅ Configured (retry + sending queues on both exporters); SDK-side collector-down verified (`test_collector_down`) | config; G #1 |
| AKS deployment | ⛔ **Blocked**: no cluster access. Manifests schema-valid. Procedure and acceptance criteria in `docs/collector-aks-deployment.md` §2–3 | G #7 |

## I. Out-of-scope removals

| Removed | Original location | Why | Evidence required functionality is intact |
|---|---|---|---|
| Backend setup (Grafana, App Insights, console) | `agent-mesh/src/observability/setup.py`, `src/config.py`, `.env.example` | Competing exporter path; not the Collector | Live run exports through the SDK |
| Raw-payload JSONL trace sink | `agent-mesh/src/observability/tracer.py`, `clients.py` call | Competing sink; PDPL | — |
| `fab.*` business metrics | `agent-mesh/src/observability/metrics.py` + 30 calls | Duplicates `gernas.*` metrics | Mesh suite baseline |
| `TraceContextMiddleware` | `agent-mesh/src/a2a/hosting.py`, `api_server.py` | Duplicate of the SDK ASGI middleware; raw user on spans | Live cross-process traces |
| Manual `fab.*` / `mesh.request` spans, `peer.*` attributes | `src/mesh/workflow.py`, `orchestrator.py`, `src/tools/collaboration_tools.py` | Parallel instrumentation | Mesh suite + live traces |
| MAF and a2a-sdk native tracing (switched off) | Runtime | Second instrumentation path | `opik_verify` "only Gernas spans" |
| Sidecar Collector config | `gernas-sdk/collector/sidecar-config.yaml` | Target is an AKS gateway pod | — |
| Commented-out telemetry stubs | `rag-as-a-service/src/gernas_rag/utils/telemetry.py` | Dead code | RAG suite |

## J. Git hygiene and repository readiness

| Step | Command | Outcome |
|---|---|---|
| Repository root | `git rev-parse --show-toplevel` | `…/telemetry/agent-mesh`; single repository, no submodules |
| Ignore files inspected | root, `agent-mesh/`, `agent-mesh/frontend/`, `datalayer-as-service/`, `rag-as-a-service/`; `.git/info/exclude` (default only) | — |
| **Critical fix** | `git check-ignore -v agent-mesh/src/observability/gernas_telemetry.py` | Was `.gitignore:16:observability/`; now not ignored (anchored `/observability/`); staged as **A** |
| New ignore rules | root `.gitignore` | `*.log`, `*.bak`, `.coverage`, `.coverage.*`, `htmlcov/`, `coverage.xml`, `agent-mesh/observability_report.html`, `agent-mesh/reports/`, `agent-mesh/audit_regulatory/`, `gernas-sdk/reports/*.json`; datalayer `!.env.example` |
| Tracked artefacts removed from the index (local files kept) | `git rm --cached …` | 4.7 MB generated HTML report, HTML report, PDF, 6 trace-dump JSONs, stray `..agent-meshdatalogsdatalayer_mcp.log`, `datalayer-as-service/.vscode/settings.json` |
| Tracked-but-ignored | `git ls-files -ci --exclude-standard` | 0 |
| Untracked, not ignored | `git ls-files --others --exclude-standard` | 0 (everything intended is staged) |
| Whitespace | `git diff --cached --check` | clean |
| Secrets | regex scan of tracked files, staged diff and full history (provider keys, AWS, private keys, App Insights keys) | Only synthetic test fixtures; **no exposure** |
| Required files trackable | sources, `pyproject.toml`, `requirements*.txt`, `.env.example` templates, Collector config, k8s manifests, KQL, tests, docs, `checklist.md` | Confirmed in `git diff --cached --name-status` (66 entries: 13 A, 16 D, 37 M) |
| Commit / push | — | **Not done**; staged for human review only |
| Remaining risks | Root `test.py`, `test.md` (empty), `test.ipynb` and `beacon_kit_improvement_plan.md` are tracked scratch / history files of the repo owner | Left untouched; owner decision |

## K. Final gaps and sign-off

| Gap | Impact | Blocker | Next action | Evidence to close |
|---|---|---|---|---|
| ADX ingestion unverified (REQ-040) | The data-plane store is unproven end to end | No ADX cluster or credentials | Provision the DB, run `adx/create-tables.kql`, grant the Collector identity Database Ingestor, set `ADX_CLUSTER_URI`, re-run the live E2E | `OTELTraces` rows for `gernas.task_id`; `otelcol_exporter_send_failed_spans{exporter="azuredataexplorer"}` = 0 |
| AKS deployment unverified (REQ-039) | Gateway not proven in-cluster | No kubectl or az, no cluster | `docs/collector-aks-deployment.md` §2 | §3 acceptance table, all rows |
| REQ-035 partial | Cost per run covers LLM and embedding only | Platform metering needed for search, compute, storage, network | Join platform cost in the data plane | KQL cost view |
| Live MCP leg not exercised this run | MCP server spans not re-proven live | Groq 404 for `qwen/qwen3.6-27b` (data / RAG agents) | Fix the model id in `.env` and re-run `gernas_e2e_queries.py` | `opik_verify` showing a `tool.call` (`tool_type=mcp`) child of `data_agent` |
| Namespace and naming sign-off | Conventions are SDK-defined | Architecture-owner decision | Review `docs/BLUEPRINT_TRACEABILITY.md` §1.3 and TN-04 / 05 / 07 | Sign-off record |
| F-16 evaluation harness → Grafana | Second metrics destination for evaluation scores (offline) | Mesh-team scope | Migrate onto the SDK when §6 Quality is planned | — |

**Sign-off:** **not fully complete.** REQ-039 (AKS deployment) and the ADX leg of REQ-040 remain blocked on infrastructure, and REQ-035 is partial. Everything that the environment allows has been implemented and verified as recorded above.
