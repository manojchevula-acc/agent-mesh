# Gernas SDK — GERNAS Agent Telemetry SDK (`gernas-telemetry`)

The Gernas SDK is a production telemetry library for agentic AI on the FAB GERNAS platform. It evolved from Beacon Kit. It is built only on OpenTelemetry SDK primitives, and all artefacts are FAB-owned intellectual property.

```python
import gernas
gernas.init_gernas()                                   # GERNAS_* environment only

@gernas.trace_agent("price_assist", version="1.4.0", framework="agent_framework")
async def answer(question: str) -> str: ...
```

- **16 decorators:** `trace_agent`, `trace_llm`, `trace_tool`, `trace_mcp`, `trace_stream`, `trace_hitl`, `trace_rag`, `trace_retrieval`, `trace_embedding`, `trace_memory`, `trace_planning`, `trace_chain`, `trace_use_case`, `trace_delegation`, `trace_guardrail`, `trace_content_safety`. They support sync, async, generator and async-generator functions.
- **Adapters:** Microsoft Agent Framework middlewares, A2A client interceptor, pure-ASGI server middleware, httpx hooks, fastmcp / mcp server context, LangChain callbacks.
- **PDPL by default:** raw prompts, completions and identities never leave the process; free text is PII-scrubbed; the Collector enforces the same rules again with a deny-by-default allow-list.
- **Behavioural signals:** run ids and parent links, termination reason, loop detection, runaway-step detection, retry counts, timeouts, orphan detection, HITL outcomes, prompt provenance (SHA-256), and token and AED cost from a rate card.
- **Never breaks the agent:** every failure is counted (`gernas.sdk.dropped_*`), never raised. p95 overhead is 0.20 ms per span.
- **Collector-first:** SDK → OTel Collector → Opik and the data plane, with per-destination kill switches (`GERNAS_EMIT_OPIK`, `GERNAS_EMIT_DATAPLANE`).

## Layout

| Path | What |
|---|---|
| `gernas/` | The package. `attributes.py` is generated from `registry/`. |
| `registry/` | 16-domain attribute registry (YAML), span and metric catalogues, generator, redaction matrix, semantic conventions |
| `collector/` | `otel-collector-config.yaml` (local gateway → file data plane plus Opik), `sidecar-config.yaml`. `bin/` and `out/` are git-ignored. |
| `tests/` | 201 tests: unit, contract, attribute lint, registry, collector-down, cross-process, adapters, CLI |
| `benchmarks/`, `reports/` | Overhead benchmark and the published report, plus E2E evidence |
| `examples/reference-agent/` | All 16 decorators in one connected trace, with no external dependencies |
| `scripts/` | `run_collector.py`, `e2e_pipeline_checks.py`, `inspect_trace.py` |
| `docs/` | Developer guide, onboarding runbook, attribute reference (generated), identifier lifecycle, migration from Beacon Kit, requirements traceability, ADRs, E2E validation report |

## Quick start

```powershell
pip install -e .[dev]
python -m pytest tests --cov=gernas --cov-fail-under=90
python scripts\run_collector.py                 # needs collector\bin\otelcol-contrib.exe (see docs\onboarding-runbook.md)
$env:GERNAS_OTLP_ENDPOINT="http://127.0.0.1:4317"; gernas doctor
python examples\reference-agent\main.py
```

Next steps: `docs/onboarding-runbook.md` (onboard an agent), `docs/sdk-developer-guide.md` (API), `docs/E2E_VALIDATION_REPORT.md` (what has been verified).
