# Gernas SDK — GERNAS Agent Telemetry SDK (`gernas-telemetry`)

The Gernas SDK implements the **GERNAS Enterprise Telemetry & Observability Blueprint** on OpenTelemetry primitives. It produces one span, metric and attribute model for Microsoft Agent Framework, LangChain, LangGraph and plain Python agents, and exports it only to the GERNAS OTel Collector.

```python
import gernas
gernas.init_gernas()                                   # GERNAS_* environment only; mandatory identity is validated

@gernas.trace_agent("price_assist", agent_type="orchestrator")
async def answer(question: str) -> str: ...
```

- **Blueprint naming.** The 13 canonical context domains use the exact blueprint field names (`gernas.use_case_id`, `gernas.token_input`, `gernas.estimated_cost`, …), generated into `gernas.Ctx` from `registry/`.
- **Decorators (13):**
  - `trace_use_case`, `trace_agent`, `trace_step`, `trace_planning`, `trace_delegation`
  - `trace_llm` (including streaming), `trace_embedding`, `trace_tool` (including MCP)
  - `trace_retrieval`, `trace_memory`, `trace_guardrail`, `trace_content_safety`, `trace_hitl`
- **Thin framework adapters.**
  - MAF middleware.
  - A LangChain callback handler; LangGraph uses the same handler, with each node producing an `agent.step`.
  - The A2A interceptor, ASGI middleware, httpx hooks and MCP server context.
- **Blueprint §5 / §6 KPIs:**
  - termination reason, steps, retries, loops, maximum step violation;
  - delegation path, HITL outcome;
  - TTFT, tokens per second, cached tokens, fallback;
  - estimated cost from a rate card.
- **No payloads, secrets or raw identity.**
  - Payload sizes only.
  - Tokenized user id.
  - PII and secret scrubbing.
  - 1024-character value cap.
  - The Collector enforces a registry-generated denylist and allow-list again.
- **Never breaks the agent.** Failures are counted (`gernas.sdk.dropped_*`), never raised; p95 overhead is below 0.5 ms per span.

## Layout

| Path | What |
|---|---|
| `gernas/` | The package. `attributes.py` is generated from `registry/`. |
| `gernas/adapters/` | `agent_framework`, `langchain`, `langgraph`, `a2a`, `mcp`, `asgi`, `httpx` (optional dependencies) |
| `registry/` | Blueprint domains plus documented extensions (YAML), span and metric catalogues, denylist, generator, redaction matrix, semantic conventions |
| `collector/` | `otel-collector-config.yaml` (gateway: traces → Azure Data Explorer + Opik, metrics/logs → ADX), `kustomization.yaml` + `k8s/` (AKS), `adx/create-tables.kql`, `local-dataplane-file.yaml` (local-only overlay). `bin/` and `out/` are git-ignored. |
| `tests/` | Naming contract, config, decorators, runtime, context, security, metrics, adapters (MAF / LangChain / LangGraph / transport), optional dependencies, conformance, registry, packaging |
| `examples/` | `custom_agent.py`, `maf_agent.py`, `langchain_agent.py`, `langgraph_agent.py` (offline), rate card example |
| `benchmarks/`, `reports/` | Overhead benchmark and report, plus E2E evidence |
| `scripts/` | `run_collector.py`, `e2e_pipeline_checks.py`, `inspect_trace.py`, `opik_verify.py` |
| `docs/` | Blueprint traceability, developer guide, onboarding runbook, attribute reference (generated), migration, ADRs |

## Quick start

```powershell
pip install -e .[dev]
python -m pytest tests --cov=gernas
python registry\generate.py --check
$env:GERNAS_SERVICE_NAME="demo"; $env:GERNAS_USE_CASE_ID="UC-DEMO"; $env:GERNAS_APPLICATION_ID="demo-app"
$env:GERNAS_BUSINESS_DOMAIN="demo"; $env:GERNAS_ENVIRONMENT="dev"; $env:GERNAS_EXPORTER="console"
python examples\custom_agent.py
```

Next steps:
- `docs/onboarding-runbook.md` to onboard an agent;
- `docs/sdk-developer-guide.md` for the API;
- `docs/BLUEPRINT_TRACEABILITY.md` for the requirement → code → test mapping.
