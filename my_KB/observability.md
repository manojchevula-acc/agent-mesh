# Observability — Agent Mesh (Grafana Cloud Profile)

> **Scope:** This document covers the `OBS_PROFILE=grafana` configuration only.
> Source files: `src/observability/` · Entry point: `setup_observability()` in `run.py` / `api_server.py`

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        Agent Mesh Process                    │
│                                                              │
│  run.py / api_server.py                                      │
│       └─ setup_observability("agent_mesh_cli")               │
│             │                                                │
│             ├─ configure_logging()   ──► rotating file log   │
│             │                             data/logs/*.log    │
│             │                                                │
│             └─ _setup_grafana()                              │
│                   │                                          │
│          ┌────────┼────────┐                                 │
│          ▼        ▼        ▼                                 │
│    TracerProvider  MeterProvider  LoggerProvider             │
│    (OTel SDK)      (OTel SDK)     (OTel SDK)                 │
│          │               │              │                    │
│   BatchSpanProcessor  PeriodicExporting  BatchLogRecord      │
│          │            MetricReader       Processor           │
│          │               │              │                    │
│    OTLPSpanExporter  OTLPMetricExporter  OTLPLogExporter     │
│    (OTLP/HTTP)       (OTLP/HTTP)        (OTLP/HTTP)          │
└──────────┼───────────────┼──────────────┼───────────────────┘
           │               │              │
           │   HTTPS + Basic Auth (instance_id:api_token)
           │               │              │
           ▼               ▼              ▼
    Grafana Tempo    Grafana Mimir   Grafana Loki
    (Distributed     (Metrics &      (Log
     Tracing)         Dashboards)     Aggregation)
```

---



## 1. Packages Used

All packages installed via `requirements.txt`:

| Package | Version | Purpose |
|---|---|---|
| `opentelemetry-api` | >=1.42.1 | Core OTel API — `trace`, `metrics`, `baggage` interfaces |
| `opentelemetry-sdk` | >=1.42.1 | SDK implementations — `TracerProvider`, `MeterProvider` |
| `opentelemetry-proto` | >=1.42.1 | Protobuf serialization for OTLP wire format |
| `opentelemetry-exporter-otlp-proto-http` | >=1.42.1 | OTLP/HTTP exporters for traces, metrics, and logs |
| `opentelemetry-exporter-otlp-proto-grpc` | >=1.42.1 | OTLP/gRPC exporter (used in dev profile) |
| `opentelemetry-exporter-otlp-proto-common` | >=1.42.1 | Shared OTLP serialization utilities |
| `opentelemetry-semantic-conventions` | >=0.63b1 | Standard attribute key constants (e.g. `service.name`) |
| `opentelemetry-instrumentation` | >=0.63b1 | Base auto-instrumentation framework |
| `opentelemetry-instrumentation-fastapi` | >=0.63b1 | Auto-instruments FastAPI routes → HTTP spans |
| `opentelemetry-instrumentation-httpx` | >=0.63b1 | Auto-injects `traceparent` on all outbound HTTP calls |
| `opentelemetry-instrumentation-asgi` | >=0.63b1 | ASGI middleware spans (used by FastAPI instrumentation) |
| `opentelemetry-instrumentation-starlette` | >=0.63b1 | Starlette-level HTTP span instrumentation |
| `opentelemetry-instrumentation-logging` | >=0.61b0 | Bridges Python log records → OTel `LoggerProvider` |

---

## 2. Authentication with Grafana Cloud

Authentication uses **HTTP Basic Auth** encoded as a Base64 credential pair.

**Environment variables required:**

| Variable | Description | Example |
|---|---|---|
| `OBS_PROFILE` | Activates Grafana profile | `grafana` |
| `GRAFANA_OTLP_ENDPOINT` | Grafana Cloud OTLP base URL | `https://otlp-gateway-prod-us-east-0.grafana.net/otlp` |
| `GRAFANA_INSTANCE_ID` | Grafana Cloud instance/stack ID | `123456` |
| `GRAFANA_API_TOKEN` | Grafana Cloud API token with metrics+traces+logs push scope | `glc_eyJ...` |

**How the auth header is built** (in `setup.py` → `_setup_grafana()`):

```python
import base64

raw = f"{instance_id}:{api_token}".encode("utf-8")
auth_value = "Basic " + base64.b64encode(raw).decode("ascii")
headers = {"Authorization": auth_value}
```

This single `headers` dict is passed to all three OTLP exporters — traces, metrics, and logs.

---

## 3. Traces → Grafana Tempo

**Package:** `opentelemetry-exporter-otlp-proto-http`
**Import:** `from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter`

**How it's wired:**

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry import trace as _trace

tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint=base + "/v1/traces", headers=headers)
    )
)
_trace.set_tracer_provider(tracer_provider)
```

**Flow:**
1. Code creates spans via `get_tracer("agent_mesh").start_as_current_span("...")`
2. Completed spans queue in `BatchSpanProcessor` (batches by size/time)
3. Processor serializes spans to OTLP Protobuf and POSTs to `{GRAFANA_OTLP_ENDPOINT}/v1/traces`
4. Grafana Cloud routes to **Tempo** for distributed trace storage and querying

**What spans are created:**

| Span Name | Source | Description |
|---|---|---|
| `mesh.request` | `orchestrator.py` (custom) | Root span for the full mesh request |
| `invoke_agent <name>` | `agent_framework` (auto) | Each agent invocation, with token counts |
| `chat <model>` | `agent_framework` (auto) | Each LLM call |
| `execute_tool <fn>` | `agent_framework` (auto) | Each MCP or function tool call |
| `workflow.run` | `agent_framework` (auto) | Workflow execution |
| `executor.process` | `agent_framework` (auto) | Executor step processing |
| `edge_group.process` | `agent_framework` (auto) | Edge routing in workflow |

---

## 4. Metrics → Grafana Mimir

**Package:** `opentelemetry-exporter-otlp-proto-http`
**Import:** `from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter`

**How it's wired:**

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry import metrics as _metrics

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=base + "/v1/metrics", headers=headers)
        )
    ],
)
_metrics.set_meter_provider(meter_provider)
```

**Flow:**
1. Code records metric values via `record_*()` helpers in `metrics.py`
2. `PeriodicExportingMetricReader` aggregates and exports on a timer (default 60s)
3. Serialized to OTLP Protobuf and POSTed to `{GRAFANA_OTLP_ENDPOINT}/v1/metrics`
4. Grafana Cloud routes to **Mimir** (Prometheus-compatible, long-term metric storage)

**Meter access in code:**

```python
from opentelemetry import metrics as _metrics
meter = _metrics.get_meter_provider().get_meter("agent_mesh", "1.0.0")
```

**Business Metrics defined in `src/observability/metrics.py`:**

| Instrument Name | Type | Unit | Dimensions |
|---|---|---|---|
| `fab.guardrail.requests.total` | Counter | `{request}` | `result`, `category` |
| `fab.rbac.requests.total` | Counter | `{request}` | `result`, `role` |
| `fab.compliance.requests.total` | Counter | `{request}` | `result`, `role` |
| `fab.mesh.requests.total` | Counter | `{request}` | `result`, `block_stage` |
| `fab.domain.route.total` | Counter | `{request}` | `route` |
| `fab.a2a.calls.total` | Counter | `{request}` | `target_node`, `result` |
| `fab.mcp.calls.total` | Counter | `{request}` | `service`, `tool_name`, `result` |
| `fab.guardrail.duration` | Histogram | `ms` | `result`, `category` |
| `fab.rbac.duration` | Histogram | `ms` | `result`, `role` |
| `fab.compliance.duration` | Histogram | `ms` | `result`, `role` |
| `fab.domain.duration` | Histogram | `ms` | `route` |
| `fab.a2a.duration` | Histogram | `ms` | `target_node`, `result` |
| `fab.mesh.request.duration` | Histogram | `ms` | `result`, `block_stage` |
| `fab.output.redaction.pii_hits` | Histogram | `{match}` | — |

**Also emitted by `agent_framework` auto-instrumentation:**
- `gen_ai.client.operation.duration` — LLM call latency
- `gen_ai.client.token.usage` — prompt + completion token counts
- `agent_framework.function.invocation.duration` — tool call latency

**Public recording API:**

```python
from src.observability import metrics

metrics.record_guardrail(result="pass", category="toxicity", duration_ms=12.3)
metrics.record_a2a_call(target_node="finance_agent", result="SUCCESS", duration_ms=340.0)
metrics.record_mcp_call(service="rag", tool_name="search", result="ok")
metrics.record_pii_hits(count=3)
```

---

## 5. Logs → Grafana Loki

**Package:** `opentelemetry-exporter-otlp-proto-http` + `opentelemetry-instrumentation-logging`
**Import:** `from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter`

**How it's wired:**

```python
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider
import logging

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(
        OTLPLogExporter(endpoint=base + "/v1/logs", headers=headers)
    )
)
set_logger_provider(logger_provider)

# Bridge: attaches OTel handler to Python's root logger
logging.getLogger().addHandler(
    LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
)
```

**Flow:**
1. Any `logging.getLogger(...)` call in the mesh emits a log record
2. `LoggingHandler` (from `opentelemetry-instrumentation-logging`) captures it
3. Record is converted to an OTel `LogRecord` and queued in `BatchLogRecordProcessor`
4. Exported to `{GRAFANA_OTLP_ENDPOINT}/v1/logs` → **Loki** in Grafana Cloud

**Trace correlation:** `TraceContextFilter` in `logging_config.py` injects `trace_id` and `span_id` into every log record, so in Loki you can click a log line and jump directly to the correlated trace in Tempo.

**Log format (text mode):**
```
2026-06-26 10:23:01 | INFO     | mesh.agent       | trace=3a7f...b2 span=9c1d...f4 req=req_abc123 | Routing to finance_agent
```

**Log format (JSON mode, `LOG_JSON=true`):**
```json
{"time": "2026-06-26T10:23:01Z", "level": "INFO", "logger": "mesh.agent", "trace_id": "3a7f...b2", "span_id": "9c1d...f4", "request_id": "req_abc123", "message": "Routing to finance_agent"}
```

**Category loggers** (all defined in `logging_config.py`):

| Logger name | Category constant | Used by |
|---|---|---|
| `mesh.agent` | `CAT_AGENT` | Orchestrator, agent invocations |
| `mesh.workflow` | `CAT_WORKFLOW` | Workflow executor |
| `mesh.tools` | `CAT_TOOLS` | Tool/MCP execution |
| `mesh.a2a` | `CAT_A2A` | A2A client/server calls |
| `mesh.mcp` | `CAT_MCP` | MCP service interactions |
| `mesh.transport` | `CAT_TRANSPORT` | HTTP transport layer |
| `mesh.approvals` | `CAT_APPROVALS` | Human-in-the-loop approvals |
| `mesh.system` | `CAT_SYSTEM` | Bootstrap, config, observability setup |

**Local file sink** (always on, regardless of profile):
- Path: `data/logs/agent_mesh.log`
- Rotation: 10 MB max size, 5 backup files kept

---

## 6. Resource — Service Identity Stamping

Every telemetry item (span, metric data point, log record) is tagged with a **Resource** that identifies the service:

```python
from agent_framework.observability import create_resource

resource = create_resource()   # reads OTEL_SERVICE_NAME, service version, deployment.environment
```

This stamps `service.name`, `service.version`, and `deployment.environment` onto all three signals — traces, metrics, and logs — so Grafana knows which service each item came from.

---

## 7. W3C Baggage — Cross-Process Identity Propagation

Beyond tracing, the mesh propagates user identity across agent hops using **W3C Baggage**.

**Baggage keys** (defined in `baggage.py`):

| Key | Meaning |
|---|---|
| `fab.request_id` | Unique ID for the top-level mesh request |
| `fab.user` | Username of the caller |
| `fab.role` | RBAC role of the caller |
| `fab.session_id` | Session ID |

**How it works:**

1. At request entry, `orchestrator.py` calls `set_request_baggage(request_id, user, role, session_id)`
2. The baggage values are attached to the active OTel context
3. The composite propagator (`W3CBaggagePropagator`) serializes them into the `baggage` HTTP header on every outbound call
4. On the receiving agent, `TraceContextMiddleware` in `a2a/hosting.py` calls `extract(headers)` — restoring both the trace context AND the baggage
5. `TraceContextFilter` reads the baggage and stamps `request_id` onto every log line

**Propagator setup** (`_ensure_composite_propagator()` in `setup.py`):

```python
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator

set_global_textmap(CompositePropagator([
    TraceContextTextMapPropagator(),   # injects/extracts traceparent, tracestate
    W3CBaggagePropagator(),            # injects/extracts baggage
]))
```

---

## 8. Distributed Trace Propagation (A2A Hops)

When one agent calls another agent over HTTP (Agent-to-Agent / A2A protocol), the trace must continue across that boundary.

**Outbound (injecting context):**
- `opentelemetry-instrumentation-httpx` is enabled via `HTTPXClientInstrumentor().instrument()`
- This monkey-patches every httpx request to auto-inject `traceparent` and `baggage` headers
- No code changes needed at call sites

**Inbound (extracting context):**
- `TraceContextMiddleware` (in `src/a2a/hosting.py`) runs on every inbound A2A HTTP request
- Calls `opentelemetry.propagate.extract(request.headers)`
- Attaches the extracted context so the child agent's spans are parented under the caller's span
- Also reads baggage to enrich the active span with caller identity attributes

**Result in Tempo:** A single root trace that spans multiple services — you can see the full call chain from the mesh orchestrator down through every agent hop.

---

## 9. Legacy JSONL Trace Sink

Off by default. Can be enabled for local debugging:

```bash
ENABLE_TRACE_JSONL=true
TRACE_LOG_FILE=data/trace_log.jsonl   # default
```

When enabled, `tracer.py` writes structured JSON events for: guardrail checks, A2A calls, RBAC decisions, compliance verdicts, payment gate approvals, and flow steps. These duplicate what OTel spans already capture — use only for local development when you don't have a Grafana instance.

---

## 10. Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `OBS_PROFILE` | `dev` | Set to `grafana` to activate Grafana Cloud export |
| `OTEL_SERVICE_NAME` | `agent_mesh` | Service name tag on all telemetry |
| `GRAFANA_OTLP_ENDPOINT` | _(none)_ | Grafana Cloud OTLP gateway base URL |
| `GRAFANA_INSTANCE_ID` | _(none)_ | Grafana Cloud stack/instance ID (Basic auth username) |
| `GRAFANA_API_TOKEN` | _(none)_ | Grafana Cloud API token (Basic auth password) |
| `ENABLE_INSTRUMENTATION` | `true` | Enable auto-instrumentation (FastAPI, httpx, etc.) |
| `ENABLE_BUSINESS_METRICS` | `true` | Enable `fab.*` custom metric recording |
| `ENABLE_SENSITIVE_DATA` | `false` | Include LLM prompts/responses in span attributes |
| `LOG_LEVEL` | `INFO` | Minimum log level for console + file |
| `LOG_FILE` | `data/logs/agent_mesh.log` | Rotating log file path |
| `LOG_JSON` | `false` | Emit JSON-formatted log lines |
| `ENABLE_TRACE_JSONL` | `false` | Enable legacy JSONL trace sink |

---

## 11. Data Flow: Agent Mesh → OpenTelemetry → Grafana Cloud

```
┌─────────────────────────────────────────────────────────────────┐
│  AGENT MESH  (your application code)                            │
│                                                                 │
│  • Opens/closes spans   →  get_tracer().start_as_current_span() │
│  • Records metrics      →  counter.add() / histogram.record()   │
│  • Writes log lines     →  log.info() / log.warning() / etc.    │
└───────────────────────┬─────────────────────────────────────────┘
                        │ calls OTel SDK APIs (in-process library)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  OPENTELEMETRY SDK  (runs inside the same Python process)       │
│                                                                 │
│  Traces  →  BatchSpanProcessor        buffers finished spans    │
│  Metrics →  PeriodicExportingMetricReader  aggregates every 60s │
│  Logs    →  BatchLogRecordProcessor   buffers log records       │
│                                                                 │
│  All three are serialized to Protobuf (compact binary format)   │
└───────────────────────┬─────────────────────────────────────────┘
                        │ HTTPS POST  (Basic Auth: instance_id:api_token)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  GRAFANA CLOUD  —  OTLP Gateway                                 │
│                                                                 │
│  POST /v1/traces   ──►  Tempo   →  trace timelines & spans      │
│  POST /v1/metrics  ──►  Mimir   →  dashboards & alerting        │
│  POST /v1/logs     ──►  Loki    →  searchable log lines         │
│                                                                 │
│  All three share the same trace_id so you can jump between them │
└─────────────────────────────────────────────────────────────────┘
```

**In one sentence:** The agent mesh calls the OTel SDK like any other library → the SDK buffers and serializes the data → three OTLP exporters push it over HTTPS to Grafana Cloud → Grafana splits it by path into Tempo (traces), Mimir (metrics), and Loki (logs), all linked by a shared `trace_id`.
