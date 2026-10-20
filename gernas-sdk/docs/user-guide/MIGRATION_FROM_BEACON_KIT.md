# Migrating from Beacon Kit to the Gernas SDK

> **0.4.0 supersedes the 0.3.0 API described further down.** Read §0 first. The Beacon Kit → 0.3.0 tables below are kept as history.

## 0. Migrating from 0.3.0 to 0.4.0 (blueprint alignment)

0.4.0 aligns names and scope with the GERNAS Enterprise Telemetry Blueprint (see `docs/governance/BLUEPRINT_TRACEABILITY.md`). It is a breaking release. No aliases are shipped, because the blueprint requires one set of names.

| 0.3.0 | 0.4.0 |
|---|---|
| Optional identity with invented defaults (`unassigned`, `dev`) | `GERNAS_SERVICE_NAME`, `GERNAS_USE_CASE_ID`, `GERNAS_APPLICATION_ID`, `GERNAS_BUSINESS_DOMAIN`, `GERNAS_ENVIRONMENT` are **mandatory** (`GernasConfigError`) |
| `KeyError` for config | `gernas.GernasConfigError` (a `ValueError`) |
| `gen_ai.request.model`, `gen_ai.provider.name`, `gen_ai.usage.input_tokens` / `output_tokens` | `gernas.model_id`, `gernas.provider`, `gernas.token_input` / `gernas.token_output` (the Collector maps them back for Opik) |
| `gernas.est_cost_aed` | `gernas.estimated_cost` (rate-card currency) |
| `gernas.tokenised_user_id` | `gernas.tokenized_user_id`, via `gernas.request_context(user_id=...)` + `GERNAS_USER_TOKEN_KEY` |
| dotted keys (`gernas.prompt.template_id`, `gernas.retrieval.index_id`, `gernas.hitl.outcome`, …) | flat blueprint keys (`gernas.prompt_template_id`, `gernas.index_id`, `gernas.hitl_outcome`, …) |
| `trace_agent(agent_id)` | `trace_agent(agent_id, *, agent_type=...)`; `agent_type` is a mandatory blueprint field |
| `trace_mcp(server_id, ...)` → `mcp.call` | `trace_tool(tool_id, tool_type="mcp", dependency=server_id)` → `tool.call` |
| `trace_stream(model)` → `llm.stream` | `trace_llm(model)` on a generator (or `stream=True`) → `llm.call` |
| `trace_rag(...)` → `rag.retrieve` | `trace_retrieval(...)` → `retrieval.search` |
| `tool.{tool_id}`, `memory.{operation}` span names | fixed `tool.call`, `memory.access` (identity is an attribute) |
| `trace_chain`, `chain.run`, `GernasLangChainCallback` | `gernas.adapters.langchain.instrument(runnable, agent_id=..., agent_type=...)` / `GernasCallbackHandler` |
| — | `trace_step` (`agent.step`) for workflow executors and graph nodes; `gernas.adapters.langgraph.instrument_graph` |
| `planning_step(...)` | `trace_step(..., step_type="plan_step", plan_id=...)` |
| `trace_memory(op, store=...)` | `trace_memory("read" \| "write")`; `store` removed |
| `trace_delegation(..., protocol=...)` | `protocol` removed |
| `gernas_middleware(agent_id, provider=, mcp_server_id=)` | `gernas_middleware(agent_id, *, agent_type, provider, mcp_server=, trace_functions=True)` |
| `GernasASGIMiddleware(run_id_header=...)`, `gernas.request_id` | removed (task correlation uses `request_context(task_id=...)`) |
| `inject_context(headers, session_id=...)` | `with gernas.request_context(session_id=...): gernas.inject_context(headers)` |
| `record_prompt`, `gernas.prompt.sha256` | removed: not a blueprint field |
| `gernas.input_hash` / `output_hash` | removed: payload **sizes** only (`request_size_bytes` / `response_size_bytes`) |
| `GERNAS_EXPORTER=pretty\|file\|memory` | removed: `otlp`, `console`, `off` only |
| `GERNAS_EMIT_OPIK` / `GERNAS_EMIT_DATAPLANE`, `gernas.route.*` | removed: routing is Collector configuration |
| `GERNAS_CAPTURE_PAYLOADS`, `GERNAS_BAGGAGE_ATTRIBUTES`, `GERNAS_ROLE_BAGGAGE_KEYS`, `GERNAS_LLM_PROVIDER`, `GERNAS_FILE_OUTPUT_DIR`, `GERNAS_COMMIT_SHA`, `GERNAS_PROVENANCE_ID`, `GERNAS_WORK_UNIT_ID`, `GERNAS_COMPLIANCE_*` | removed |
| `compliance_gate`, `guardrail_check`, `rbac_check`, HMAC audit chain, `gernas report` / `audit-report`, circuit breaker, `traced*`, `log_calls`, `track_latency`, `count_calls`, `AgenticMetrics`, `StructuredLogger` | removed: not blueprint telemetry. Use `trace_guardrail` for policy decisions. |
| extras `pretty`, `audit`, `breaker` | removed. Extras are now `agent-framework`, `a2a`, `langchain`, `langgraph`, `dev`. |

Beacon Kit (`beacon_kit/`, version 0.2.0) has been evolved into the **Gernas SDK**. The new package is `gernas-sdk/`, the distribution is `gernas-telemetry` 0.3.0, and the import name is `gernas`. The git history is preserved: the files were moved with `git mv`. No `beacon_kit` compatibility package is shipped. In-repo callers (Agent Mesh, DataLayer, RAG) have been migrated.

## Install

```powershell
pip install -e ./gernas-sdk            # from the agent-mesh repo root
# or: pip install gernas_telemetry-0.3.0-py3-none-any.whl
```

## API mapping

| Beacon Kit | Gernas SDK | Notes |
|---|---|---|
| `setup_telemetry(TelemetryConfig.from_env(...))` | `init_gernas()` or `init_gernas(GernasConfig.from_env(service_name=...))` | Idempotent. Returns a `GernasHandle` with `.flush()` and `.shutdown()`. |
| `flush_telemetry(handle)` | `handle.flush()` / `gernas.flush_gernas()` | |
| `TelemetryConfig` | `GernasConfig` | Reads `GERNAS_*` variables only (see the table below). |
| `@traced_agent(name)` | `@trace_agent(agent_id, name=, version=, framework=)` | The old name still works, with a `DeprecationWarning`. |
| `@traced_llm_call(model)` | `@trace_llm(model, provider=, deployment_id=)` | Token counts are read from OpenAI, Anthropic, MAF and dict responses. |
| `@traced_tool(name)` | `@trace_tool(tool_id)` | |
| `@traced_mcp_tool(service, tool)` | `@trace_mcp(server_id, tool_name=)` | The span name is now always `mcp.call`. |
| `@traced_a2a_hop(node)` | `@trace_delegation(child_agent_id)` | Injects context into a `headers`/`carrier` argument. |
| `TelemetryMiddleware`, `A2ATraceContextMiddleware` | `gernas.adapters.asgi.GernasASGIMiddleware` | A pure ASGI middleware that creates its own SERVER span. It does not depend on `FastAPIInstrumentor`. |
| httpx auto-instrumentation (A2A propagation) | `gernas.adapters.a2a.GernasA2AInterceptor`, `gernas.adapters.httpx` | Built from propagation primitives. |
| `traced`, `traced_node`, `traced_pipeline`, `track_latency`, `count_calls`, `log_calls` | unchanged | They now carry the envelope and use `safe_set`. |
| `compliance_gate`, `guardrail_check`, `rbac_check`, audit chain | unchanged | They read the role from `GERNAS_ROLE_BAGGAGE_KEYS` (default `gernas.user_role`). |
| `AgenticMetrics`, `beacon.*` metrics | `AgenticMetrics`, `gernas.*` metrics | Decorators record metrics automatically. Drop any manual calls to avoid double counting. |
| `beacon-report`, `beacon-audit-report` | `gernas report`, `gernas audit-report` | |
| Hand-typed attributes (`agent.name`, `llm.input_tokens`, `tool.latency_ms`, …) | `Ctx.*` constants (`gernas.*`, OTel `gen_ai.*`) | See `docs/reference/attribute-reference.md`. |

## Environment variables

| Beacon Kit | Gernas SDK |
|---|---|
| `EXPORTER_TYPE`, `OBS_PROFILE` | `GERNAS_EXPORTER` (`otlp` default, plus `console`, `pretty`, `file`, `memory`, `off`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `GRAFANA_OTLP_ENDPOINT` | `GERNAS_OTLP_ENDPOINT` (the Collector; required for `otlp`) |
| `OTEL_SERVICE_NAME`, `SERVICE_NAME`, `SERVICE_VERSION` | `GERNAS_SERVICE_NAME`, `GERNAS_SERVICE_VERSION` |
| `ENVIRONMENT`, `POD_NAME` | `GERNAS_ENVIRONMENT` (`dev`/`sit`/`uat`/`staging`/`prod`), `GERNAS_POD` |
| `OTEL_SAMPLING_RATE` | `GERNAS_SAMPLING_RATE` |
| `OTEL_FILE_OUTPUT_DIR`, `OTEL_FILE_SESSION_ID` | `GERNAS_FILE_OUTPUT_DIR`, `GERNAS_FILE_SESSION_ID` |
| `ENABLE_COMPLIANCE`, `COMPLIANCE_STORE`, `COMPLIANCE_HMAC_KEY` | `GERNAS_ENABLE_COMPLIANCE`, `GERNAS_COMPLIANCE_STORE`, `GERNAS_COMPLIANCE_HMAC_KEY` |
| `ENABLE_BAGGAGE_PROPAGATION` | always on. Baggage → span copying is limited to the `GERNAS_BAGGAGE_ATTRIBUTES` allow-list. |
| `GRAFANA_INSTANCE_ID`, `GRAFANA_API_TOKEN`, `APPLICATIONINSIGHTS_CONNECTION_STRING` | **Removed from the SDK.** Configure these backends as Collector exporters. |

## Behaviour changes, and why

1. **No direct export to a backend.** Engineering principle 5 requires SDK → Collector. The `grafana`, `grafana_and_file` and `azure_monitor` exporter types are gone. Add a `otlphttp` (Grafana) or `azuremonitor` exporter to the Collector config instead.
2. **PDPL by default.**
   - Raw prompts and completions, framework message payloads and raw user ids are dropped.
   - Unregistered string attributes, exception messages and log bodies are PII-scrubbed.
   - Beacon copied all baggage (including `fab.user`) onto spans. The SDK now copies only allow-listed keys.
3. **Span names follow the spec.** `agent.run`, `llm.call`, `tool.{id}`, `mcp.call`, `agent.delegate`, and so on. Beacon used `agent.<name>`, `a2a.<node>` and `mcp.<svc>.<tool>`. The agent and tool identity is now an attribute.
4. **Generators are supported.** Beacon closed a generator's span as soon as the generator object was created. The SDK keeps it open until the generator is exhausted, closed or garbage-collected.
5. **The `off` exporter works.** Beacon's version raised `NameError` on its first export.
6. **Logs bridge.** `GernasLogHandler` replaces the SDK `LoggingHandler`, which is deprecated in OTel 1.45 and whose successor is an `instrumentation-*` package.
