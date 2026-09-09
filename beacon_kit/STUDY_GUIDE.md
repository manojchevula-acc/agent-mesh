# beacon_kit — Study Guide

> **Who this is for:** Python engineers working on this project who want to understand what beacon_kit is, why it was built, how each piece works, and how to add it to a new service. Written so a lead engineer can pick up any section independently.

---

## Table of Contents

1. [The Problem — Why Observability Was Hard Before](#1-the-problem--why-observability-was-hard-before)
2. [What is beacon_kit?](#2-what-is-beacon_kit)
3. [How beacon_kit Solves Each Problem (How / What / Where)](#3-how-beacon_kit-solves-each-problem)
   - [3.11 Local JSONL File Export (New)](#311-local-jsonl-file-export)
4. [How to Add beacon_kit to a New Project (5-Step Recipe)](#4-how-to-add-beacon_kit-to-a-new-project)
5. [File Structure — What Every File Does](#5-file-structure--what-every-file-does)
6. [Service Integration Map — Before vs After](#6-service-integration-map--before-vs-after)
7. [Environment Variables Reference](#7-environment-variables-reference)
8. [Viewing Results in Grafana](#8-viewing-results-in-grafana)
9. [Running the Local Demo](#9-running-the-local-demo)
10. [Reverting to Old Observability](#10-reverting-to-old-observability)

---

## 1. The Problem — Why Observability Was Hard Before

Before beacon_kit, each service in this project handled observability differently — or not at all:

| Service | Situation |
|---|---|
| **agent-mesh** | Custom `setup_observability()` in `src/observability/`. Worked, but was tightly coupled to agent-mesh. Could not be reused by other services. |
| **rag-as-a-service** | Had `configure_telemetry()` that created a `TracerProvider` with **no exporter attached**. Spans were created but exported nowhere — completely silent. |
| **datalayer-as-service** | Had nothing. Zero telemetry. No spans, no metrics, no logs. |

Beyond the per-service gaps, five structural problems made the whole system hard to observe:

**Problem 1 — No shared decorator API.**
Wrapping a function in a trace span required 15+ lines of OpenTelemetry SDK boilerplate per function: get tracer, start span, set attributes, record exceptions, close span. Engineers skipped it.

**Problem 2 — Cross-service traces broke.**
When agent-mesh called rag-as-a-service over HTTP, the two services started independent traces. In Grafana Tempo you saw two disconnected trace trees — impossible to follow a request end-to-end.

**Problem 3 — Compliance events were ad-hoc.**
Guardrail triggers, RBAC denials, and pricing approvals were logged as plain text with no tamper evidence, no chain-of-custody, and no link to the OpenTelemetry trace they came from.

**Problem 4 — Switching backends required code changes.**
Moving from a local OTLP collector to Grafana Cloud to Azure Monitor meant editing Python files in every service. No single configuration point.

**Problem 5 — Starting a new project meant starting from zero.**
Every new service had to re-invent the observability wheel: pick an exporter, wire up providers, add logging filters, inject W3C headers for distributed tracing — weeks of setup that had nothing to do with the service's actual purpose.

beacon_kit was built to solve all five problems with a single, reusable package.

---

## 2. What is beacon_kit?

beacon_kit is a **drop-in OpenTelemetry sidecar** — a Python package that any service activates in 2 lines and then instruments any function with a decorator.

```python
# 2 lines to activate (reads all config from env vars)
from beacon_kit import setup_telemetry, TelemetryConfig
handle = setup_telemetry(TelemetryConfig.from_env(service_name="my-service"))

# 1 decorator to trace any function
from beacon_kit import traced_agent

@traced_agent("triage-agent")
def run_triage(query: str) -> dict:
    ...  # beacon_kit creates a span, records timing, hashes inputs/outputs
```

### The 3-Layer Mental Model

Every capability in beacon_kit belongs to one of three layers:

```
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3 — Compliance                                        │
│  @compliance_gate  @guardrail_check  @rbac_check            │
│  Tamper-evident JSONL audit trail, HMAC-signed, chain-hashed │
├──────────────────────────────────────────────────────────────┤
│  LAYER 2 — Agentic Interactions                              │
│  @traced_agent  @traced_llm_call  @traced_tool              │
│  @traced_a2a_hop  @traced_mcp_tool  AgenticMetrics          │
│  Privacy-safe: SHA-256 hashes only, no raw data in spans    │
├──────────────────────────────────────────────────────────────┤
│  LAYER 1 — Infrastructure                                    │
│  setup_telemetry()  TelemetryConfig  ExporterFactory        │
│  Middleware  Circuit breaker  Logging  W3C Propagation      │
└──────────────────────────────────────────────────────────────┘
```

- **Layer 1** boots the entire OTel stack once at startup. You do not touch it again.
- **Layer 2** decorators go on your functions. Each decorator = one span in Grafana Tempo.
- **Layer 3** decorators go on compliance-sensitive functions. Each call = one signed record in the audit JSONL.

**Key principle: plug in → decorate → done.** No boilerplate, no service-specific code, no new infrastructure.

---

## 3. How beacon_kit Solves Each Problem

Each sub-section below answers: **What problem?** → **How it works** → **Where it lives in the code** → **Code example**.

---

### 3.1 Unified Provider Bootstrap

**What problem?** Every service had a different provider setup. RAG had no exporter. Datalayer had nothing. Each required custom code to change backends.

**How it works:**
`setup_telemetry(config)` wires the entire OpenTelemetry stack in one call:
- Creates a `TracerProvider` (traces → spans → Grafana Tempo)
- Creates a `MeterProvider` (metrics → counters/histograms → Grafana Mimir)
- Creates a `LoggerProvider` (structured logs → Grafana Loki)
- Sets W3C TraceContext + Baggage as the global propagator (enables cross-service trace stitching)
- Wires all exporters based on the `EXPORTER_TYPE` / `OBS_PROFILE` env var

**Where it lives:** `beacon_kit/core/provider.py` — `setup_telemetry()` and `TelemetryHandle`

**Code example:**
```python
# startup.py (any service)
from beacon_kit import setup_telemetry, TelemetryConfig

handle = setup_telemetry(TelemetryConfig.from_env(service_name="my-service"))
# That's it. Traces, metrics, and logs are all wired and exporting.

# At shutdown (optional but recommended):
from beacon_kit import flush_telemetry
flush_telemetry(handle)
handle.shutdown()
```

---

### 3.2 Multi-Backend Exporter Switching

**What problem?** Switching from a local OTLP collector to Grafana Cloud to Azure Monitor required code changes in every service.

**How it works:**
`TelemetryConfig` reads `OBS_PROFILE` (agent-mesh convention) or `EXPORTER_TYPE` (beacon_kit native) and maps it to the correct exporter backend:

| `OBS_PROFILE` / `EXPORTER_TYPE` | Traces go to | Metrics go to | When to use |
|---|---|---|---|
| `grafana` | Grafana Tempo (OTLP/HTTP + Basic auth) | Grafana Mimir | Production / shared infra |
| `grafana_and_file` | Grafana Tempo **+ local JSONL** | Grafana Mimir **+ local JSONL** | Production with offline backup copy |
| `file` | Local JSONL files only | Local JSONL files only | Offline dev / no network required |
| `dev` / `otel_collector` | Local OTLP collector (gRPC :4317) | Same | Local dev with Aspire/Jaeger |
| `azure_monitor` / `prod` | Azure Application Insights | Azure Monitor | Azure deployments |
| `pretty` | Rich terminal waterfall | Rich terminal table | Local dev, no collector |
| `console` | Stdout JSON | Stdout | CI / debugging |
| `off` | Nothing (no-op) | Nothing | DevUI / test mode |

> For `grafana_and_file` and `file` modes, set `OTEL_FILE_OUTPUT_DIR` (see [Section 3.11](#311-local-jsonl-file-export) and [Section 7](#7-environment-variables-reference)).

**Where it lives:** `beacon_kit/core/exporters.py` — `build_span_exporter()`, `build_metric_exporter()`, `build_log_exporter()`

**TelemetryConfig also reads Grafana credentials automatically:**
```
GRAFANA_OTLP_ENDPOINT=https://otlp-gateway-prod-ap-south-1.grafana.net/otlp
GRAFANA_INSTANCE_ID=1694452
GRAFANA_API_TOKEN=glc_eyJ...
```
No code change needed — just set these env vars and set `OBS_PROFILE=grafana`.

---

### 3.3 Agent Tracing — `@traced_agent`

**What problem?** No way to know how long an agent invocation took, what the input/output was, or whether it failed — critical for debugging a multi-agent system.

**How it works:**
Wraps the function in an OpenTelemetry **SERVER span** (server = entry point). Records:
- `agent.name` — the name you give it
- `agent.input_hash` — SHA-256 of all arguments (not the arguments themselves)
- `agent.output_hash` — SHA-256 of the return value
- `agent.latency_ms` — wall-clock duration
- `agent.token_usage.*` — if the result dict contains `token_usage` or `usage` keys, token counts are extracted and recorded

**Where it lives:** `beacon_kit/decorators/agent.py`

**Code example:**
```python
from beacon_kit import traced_agent

@traced_agent("triage-agent", attributes={"agent.version": "2"})
def run_triage(query: str) -> dict:
    # ... your agent logic ...
    return {
        "response": "...",
        "token_usage": {"input_tokens": 120, "output_tokens": 45},
    }
# In Grafana Tempo you see: agent.triage-agent [SERVER] 250ms OK
# Attributes: agent.name=triage-agent, agent.latency_ms=250,
#             agent.token_usage.input_tokens=120, agent.token_usage.output_tokens=45
```

Works on both sync and async functions. On exception: records the scrubbed error message (PII removed) and sets span status to ERROR.

---

### 3.4 LLM Call Tracing — `@traced_llm_call`

**What problem?** LLM API calls (the most expensive and latency-sensitive operation in any AI system) were invisible. No model name, no token count, no latency in traces.

**How it works:**
Wraps the function in a **CLIENT span** (client = outbound call). Records:
- `llm.model` — model name (static string or a callable that extracts it from the result)
- `llm.prompt_hash` — SHA-256 of the prompt/arguments
- `llm.completion_hash` — SHA-256 of the response
- `llm.latency_ms` — wall-clock duration
- `llm.input_tokens`, `llm.output_tokens` — extracted from `result["usage"]` or `result["token_usage"]`

**Where it lives:** `beacon_kit/decorators/agent.py`

**Code example:**
```python
from beacon_kit import traced_llm_call

@traced_llm_call(model="claude-sonnet-4-6")
def call_llm(prompt: str) -> dict:
    # ... actual LLM API call ...
    return {
        "content": "The answer is ...",
        "usage": {"input_tokens": 200, "output_tokens": 30},
    }

# Dynamic model extraction:
@traced_llm_call(model=lambda result: result.get("model", "unknown"))
async def call_llm_async(prompt: str) -> dict:
    ...
```

---

### 3.5 Tool Call Tracing — `@traced_tool` and `@traced_mcp_tool`

**What problem?** Tool invocations (database lookups, search calls, calculations) had no observability — failures were silent and slow tools were invisible.

**How it works:**
Both decorators create **CLIENT spans**. The key difference:
- `@traced_tool` — for internal Python functions called as tools
- `@traced_mcp_tool` — for MCP (Model Context Protocol) tool calls through an external MCP server

**Privacy-by-design:** Raw inputs and outputs are **never** stored in spans. Only SHA-256 hashes and structural summaries (field count, size in bytes, has_error flag) appear in telemetry. This means tool tracing is safe for banking/PII data.

**Where it lives:** `beacon_kit/decorators/tool.py` (traced_tool), `beacon_kit/decorators/agent.py` (traced_mcp_tool)

**Code example:**
```python
from beacon_kit import traced_tool, traced_mcp_tool

@traced_tool("account_balance_lookup")
def lookup_balance(account_id: str) -> dict:
    # account_id is hashed in the span — never stored raw
    return {"balance": 9999.99, "currency": "GBP"}

@traced_mcp_tool(service="datalayer-mcp", tool_name="pricing_recommendation")
def get_pricing(customer_id: str, product: str) -> dict:
    # Calls the external MCP server — both input and result are hashed
    ...
```

In Grafana Tempo:
```
tool.account_balance_lookup [CLIENT] 20ms OK
  tool.name = account_balance_lookup
  tool.input_hash = abc123...
  tool.output.field_count = 2
  tool.output.has_error = False
  tool.latency_ms = 20.3
```

---

### 3.6 Cross-Service Trace Continuity — `@traced_a2a_hop` + `A2ATraceContextMiddleware`

**What problem?** When agent-mesh called rag-as-a-service over HTTP (an "A2A hop"), the trace broke. In Grafana Tempo you saw two separate, unconnected traces — impossible to follow a user request across service boundaries.

**How it works (two parts working together):**

**Part A — Outbound (caller side): `@traced_a2a_hop`**
Before calling the remote service, the decorator:
1. Creates a CLIENT span for the hop
2. Calls `opentelemetry.propagate.inject(carrier)` which adds a `traceparent` HTTP header to the outbound request carrier dict
3. The `traceparent` encodes: `version-traceID-spanID-flags` — the receiving service uses this to become a child of the current trace

**Part B — Inbound (receiver side): `A2ATraceContextMiddleware`**
On the receiving Starlette/FastAPI server:
1. Extracts `traceparent` + `baggage` headers from the incoming request
2. Calls `opentelemetry.propagate.extract()` to reconstruct the caller's span context
3. Attaches it so all downstream spans (agent invocation, tool calls, LLM calls) become children of the caller's original trace
4. Copies identity baggage values (user, role, session_id) into the active span as attributes

**Where it lives:** `beacon_kit/decorators/agent.py` (traced_a2a_hop), `beacon_kit/middleware/a2a.py` (A2ATraceContextMiddleware)

**Code example:**
```python
# On the calling service (e.g. agent-mesh orchestrator)
from beacon_kit import traced_a2a_hop

@traced_a2a_hop(target_node="rag-service")
def call_rag_service(query: str, carrier: dict | None = None) -> dict:
    # beacon_kit injects traceparent into 'carrier' before this runs
    import httpx
    response = httpx.post("http://rag-service:9000/retrieve",
                          json={"query": query}, headers=carrier)
    return response.json()

# Invoke:
headers = {}
result = call_rag_service("What are Q3 margins?", carrier=headers)
# headers now contains: {"traceparent": "00-<traceID>-<spanID>-01"}
```

```python
# On the receiving service (e.g. rag-as-a-service main.py)
from beacon_kit.middleware.a2a import A2ATraceContextMiddleware
app.add_middleware(A2ATraceContextMiddleware)
# All request handlers are now children of the caller's trace
```

In Grafana Tempo, one trace tree spans both services:
```
mesh.request [SERVER] 350ms
  └─ a2a.rag-service [CLIENT] 180ms
       └─ rag.retrieve [SERVER] 175ms   ← this is in a different process!
            └─ llm.call [CLIENT] 120ms
```

---

### 3.7 Metrics Without Boilerplate — `@track_latency`, `@count_calls`, `AgenticMetrics`

**What problem?** Recording a histogram or counter with the OTel SDK required creating a meter, creating an instrument, calling `.record()` or `.add()` with attribute dicts — 10+ lines per metric. Engineers skipped it.

**How it works:**

`@track_latency` — wraps the function, records wall-clock duration as a histogram automatically:
```python
from beacon_kit import track_latency

@track_latency("rag.retrieval.duration", unit="ms")
def retrieve_documents(query: str) -> list:
    ...
# In Grafana Mimir: histogram for rag.retrieval.duration with min/avg/max/p99
```

`@count_calls` — increments a counter every time the function is called:
```python
from beacon_kit import count_calls

@count_calls("agent.invocations.total", attributes={"agent": "triage"})
def run_triage(query: str) -> dict:
    ...
# In Grafana Mimir: counter for agent.invocations.total{agent="triage"}
```

`AgenticMetrics` — for cases where you want to record metrics manually (e.g. after getting results back):
```python
from beacon_kit import AgenticMetrics
m = AgenticMetrics()  # instantiate once after setup_telemetry()

m.record_agent_call("success", "triage-agent", latency_ms=250)
m.record_llm_call("success", "claude-sonnet-4-6", latency_ms=180)
m.record_a2a_call("success", "rag-service", latency_ms=120)
m.record_tool_call("success", "account_balance_lookup")
m.record_mcp_call("success", "datalayer-mcp", "pricing_recommendation")
```

Pre-built metrics in `AgenticMetrics`:

| Metric name | Type | Dimensions |
|---|---|---|
| `beacon.agent.calls.total` | Counter | result, agent_name |
| `beacon.llm.calls.total` | Counter | result, model |
| `beacon.tool.calls.total` | Counter | result, tool_name |
| `beacon.a2a.calls.total` | Counter | result, target_node |
| `beacon.mcp.calls.total` | Counter | result, service, tool_name |
| `beacon.agent.duration` | Histogram (ms) | result, agent_name |
| `beacon.llm.duration` | Histogram (ms) | result, model |
| `beacon.a2a.duration` | Histogram (ms) | result, target_node |

**Where it lives:** `beacon_kit/decorators/metrics.py`, `beacon_kit/metrics/agentic.py`

---

### 3.8 Trace-Correlated Logging — `@log_calls` and `TraceContextFilter`

**What problem?** Log lines had no trace context. When debugging a failed request in Grafana Loki, you could not link a log line to its span in Grafana Tempo — you had to guess by timestamp.

**How it works (two complementary tools):**

**`@log_calls`** — emits structured OTEL-linked log records via Python's `logging` module (which beacon_kit bridges to the OTEL LoggerProvider via `LoggingHandler`). The log records automatically carry `trace_id` and `span_id`:
```python
from beacon_kit import log_calls

@log_calls(logger_name="my-service.agent")
def run_agent(query: str) -> dict:
    ...
# Emits:
#   INFO  Entering run_agent
#   INFO  Completed run_agent in 245ms
# Each record carries trace_id and span_id in the OTEL log body.
```

**`TraceContextFilter`** — a Python `logging.Filter` that stamps `trace_id`, `span_id`, and `request_id` (from W3C Baggage) onto every log record, even ones from `logging.info()` calls that have no decorator:
```python
from beacon_kit import setup_request_logging

# Call once at startup instead of logging.basicConfig():
logger = setup_request_logging(log_dir="./logs", service_name="my-service")
logger.info("Server started")
# Log line: 2026-09-08 09:10:45 | INFO | my-service | trace=eb5fd9... span=3e134... req=run-001 | Server started
```

**Where it lives:** `beacon_kit/decorators/log.py`, `beacon_kit/logconfig/request_logging.py`

In Grafana Loki you can query: `{service_name="my-service"} |= "trace_id=eb5fd9"` to see all logs for a specific trace.

---

### 3.9 Compliance Audit Trail — `@compliance_gate`, `@guardrail_check`, `@rbac_check`

**What problem?** Compliance events (pricing approvals, guardrail checks, RBAC denials) were logged as plain text with no audit trail guarantees — no tamper evidence, no chain of custody, no link to the OpenTelemetry trace.

**How it works:**

All three decorators write events to a JSONL file. Each event is:
1. **HMAC-signed** — proves it was written by a process that holds the HMAC key
2. **SHA-256 chain-hashed** — each event's hash includes the previous event's hash, creating a tamper-evident chain (like a blockchain)
3. **Trace-linked** — `trace_id` and `span_id` are captured from the active OTel span, so every audit event is linked to its trace in Grafana Tempo

**`@compliance_gate`** — wraps any function; on success writes `outcome=PASS`, on exception writes `outcome=FAIL` and re-raises:
```python
from beacon_kit import compliance_gate, set_default_compliance_config

set_default_compliance_config(config)  # once at startup

@compliance_gate("pricing_query_completed", actor_key="actor")
def compute_pricing(product_id: str, actor: str = "system") -> dict:
    return {"price": 29.99}
# Audit JSONL record:
# {"event_type": "pricing_query_completed", "actor": "system", "outcome": "PASS",
#  "payload_hash": "7114...", "chain_hash": "eb63...", "signature": "049c...",
#  "trace_id": "afd374...", "timestamp": "2026-09-08T09:05:03Z"}
```

**`@guardrail_check`** — wraps a function that returns `(triggered: bool, reason: str)`. Emits a `GuardrailEvent`. If `triggered=True` and `action_on_block="block"`, raises `GuardrailBlockedError`:
```python
from beacon_kit import guardrail_check, GuardrailBlockedError

@guardrail_check(rule_id="pii_filter", action_on_block="warn")
def check_pii(output: str) -> tuple[bool, str]:
    return ("iban" in output.lower()), "PII detected"

# action_on_block options:
#   "block"    → raises GuardrailBlockedError (stops execution)
#   "warn"     → records event but does NOT raise (execution continues)
#   "escalate" → records event but does NOT raise
```

**`@rbac_check`** — reads the user's role from W3C Baggage (`fab.role` or `beacon.role`), compares to `required_role`, emits `RBACEvent`. Raises `RBACDeniedError` if access is denied:
```python
from beacon_kit import rbac_check

@rbac_check(required_role="compliance_officer", resource="pricing_override")
def override_pricing(product_id: str, price: float) -> dict:
    # Only users with role="compliance_officer" in their Baggage can call this
    ...
```

**Verifying the audit chain:**
```python
import json
from beacon_kit.compliance.chain import verify_chain

events = [json.loads(line) for line in open("compliance_events.jsonl")]
is_valid = verify_chain(events)
print("Chain intact:", is_valid)  # False if any record was tampered with
```

**Where it lives:** `beacon_kit/compliance/decorators.py`, `beacon_kit/compliance/events.py`, `beacon_kit/compliance/chain.py`

---

### 3.10 Context Continuity Across Checkpoints

**What problem?** LangGraph HITL (human-in-the-loop) workflows save state to a checkpoint and resume later. When they resume, the original `trace_id` is lost — all post-HITL spans start a fresh trace, disconnected from the pre-HITL work.

**How it works:**
Before saving the checkpoint, serialize the current OTel context to a plain dict. When the workflow resumes, restore it — all subsequent spans become children of the original trace.

**Where it lives:** `beacon_kit/propagation/context.py`

```python
from beacon_kit import serialize_context, restore_context
import opentelemetry.context as otel_context

# Before saving checkpoint:
checkpoint_state["otel_ctx"] = serialize_context()
# {"trace_id": "eb5fd96609b2d94d...", "span_id": "3e1348905049ef73", "trace_flags": "01"}

# When workflow resumes from checkpoint:
ctx = restore_context(checkpoint_state["otel_ctx"])
token = otel_context.attach(ctx)
try:
    # All spans created here are children of the original trace
    result = resume_workflow(checkpoint_state)
finally:
    otel_context.detach(token)
```

---

### 3.11 Local JSONL File Export

**What problem?** Grafana Cloud requires network connectivity. During development or offline debugging, there is no local copy of spans, metrics, or logs — you must either run a local OTLP collector or inspect the Grafana UI. There is also no way to `grep` raw telemetry data.

**How it works:**
Three new exporters write every signal to a local JSONL file (one JSON record per line, append mode). Files are auto-created (including parent directories) and thread-safe.

```
observability/           ← OTEL_FILE_OUTPUT_DIR in .env
  traces/
    20260908_143022_agent_mesh_cli.jsonl       ← one file per process
    20260908_143022_agent_mesh_price_assist.jsonl
  metrics/
    20260908_143022_agent_mesh_cli.jsonl
  logs/
    20260908_143022_agent_mesh_cli.jsonl
  compliance/
    compliance_audit.jsonl                     ← Layer 3 HMAC-signed chain
```

**Filename convention:** `{YYYYMMDD}_{HHMMSS}_{service_name}[_{session_id}].jsonl`
The timestamp is fixed at process startup so all signals from one run share the same prefix.

**Dual-export mode (`grafana_and_file`):**
When `OBS_PROFILE=grafana_and_file`, beacon_kit wires two processors simultaneously:
- **Primary (Grafana):** `BatchSpanProcessor` → Grafana Tempo; `BatchLogRecordProcessor` → Grafana Loki; `PeriodicExportingMetricReader(30s)` → Grafana Mimir
- **Secondary (file):** `SimpleSpanProcessor` → local JSONL (written immediately when span ends); `SimpleLogRecordProcessor` → local JSONL (written immediately on emit); `PeriodicExportingMetricReader(15s)` → local JSONL

The file sink uses `Simple*Processor` (not `Batch*Processor`) so records are written immediately to disk — they are not lost if the process exits before a batch flush timer fires.

**Sample span record (traces JSONL):**
```json
{
  "ts": "2026-09-08T14:30:22.123456Z",
  "trace_id": "0xabc123...",
  "span_id": "0x456def...",
  "parent_span_id": "0x789...",
  "name": "a2a.price_assist",
  "kind": "INTERNAL",
  "start_ms": 1788870835530.0,
  "duration_ms": 427.3,
  "status": "OK",
  "attributes": {"agent.name": "PriceAssistAgent", "agent.latency_ms": 427.3},
  "events": [],
  "resource": {"service.name": "agent_mesh_price_assist"}
}
```

**Sample metric record (metrics JSONL):**
```json
{
  "ts": "2026-09-08T14:30:52.000Z",
  "name": "fab.a2a.calls.total",
  "kind": "counter",
  "unit": "{request}",
  "attributes": {"target_node": "data_agent", "result": "SUCCESS"},
  "value": 3
}
```

**Sample log record (logs JSONL):**
```json
{
  "ts": "2026-09-08T14:30:22.001Z",
  "level": "INFO",
  "logger": "mesh.a2a",
  "trace_id": "0xabc123...",
  "span_id": "0x456def...",
  "msg": "A2A call node=price_assist ok (427 ms, 68 chars)",
  "attributes": {"node": "price_assist", "status": "SUCCESS", "request_id": "A3F2B1C0"}
}
```

**Debugging with local files:**
```bash
# Find all log lines for a specific request
grep "req=A3F2B1C0" observability/logs/*.jsonl

# Find all ERROR spans
grep '"status":"ERROR"' observability/traces/*.jsonl | python -m json.tool

# Watch metrics as they arrive (flushes every 15 s)
tail -f observability/metrics/20260908_*_agent_mesh_cli.jsonl
```

**Logger namespace coverage:**
beacon_kit's OTEL `LoggingHandler` is attached to these Python logger namespaces so their records flow through the file exporter automatically:
`beacon_kit`, `simple_project`, `mesh`, `src`, `agent_mesh`

This means all `mesh.a2a`, `mesh.workflow`, `mesh.agent`, `src.agents.*` log calls are captured without any changes to call sites.

**Where it lives:**
- `beacon_kit/core/file_exporters.py` — `FileSpanExporter`, `FileMetricExporter`, `FileLogExporter`
- `beacon_kit/core/provider.py` — dual-export wiring (lines ~94–170)
- `beacon_kit/core/config.py` — `file_output_dir`, `file_session_id`, `file_path(signal)`

**Important implementation note — `MetricExporter` subclasses must call `super().__init__()`:**
`PeriodicExportingMetricReader` reads `exporter._preferred_temporality` directly (not via the property) in OTEL SDK 1.42+. If a custom `MetricExporter` does not call `super().__init__()`, this attribute is never set, the reader constructor crashes silently (inside a `try/except`), and the metric file stays at 0 bytes with no error message. `FileMetricExporter` calls `super().__init__()` as its first line for exactly this reason.

---

## 4. How to Add beacon_kit to a New Project

Follow these 5 steps in order. Each step takes less than 5 minutes.

### Step 1 — Install

From the project root:
```bash
pip install -e ./beacon_kit
```
This installs beacon_kit in editable mode so changes to the package are reflected immediately.

### Step 2 — Set Environment Variables

Copy these to your service's `.env` file. For local dev, use `pretty`; for production, use `grafana`:

**Local development (pretty terminal output, no infrastructure needed):**
```env
OBS_PROFILE=pretty
```

**Grafana Cloud (same credentials already in agent-mesh .env):**
```env
OBS_PROFILE=grafana
GRAFANA_OTLP_ENDPOINT=https://otlp-gateway-prod-ap-south-1.grafana.net/otlp
GRAFANA_INSTANCE_ID=1694452
GRAFANA_API_TOKEN=<paste from agent-mesh .env>
```

**Disable telemetry (e.g. DevUI mode):**
```env
OBS_PROFILE=off
```

### Step 3 — Bootstrap at Startup

Add this block at the very top of your startup file (before any other imports that create spans):

```python
from beacon_kit import setup_telemetry, TelemetryConfig

_bk_handle = setup_telemetry(TelemetryConfig.from_env(service_name="my-new-service"))
```

For FastAPI/Starlette apps, put this inside the lifespan function:
```python
from contextlib import asynccontextmanager
from beacon_kit import setup_telemetry, TelemetryConfig, flush_telemetry

@asynccontextmanager
async def lifespan(app):
    _bk_handle = setup_telemetry(TelemetryConfig.from_env(service_name="my-service"))
    yield
    flush_telemetry(_bk_handle)  # flush before shutdown
```

### Step 4 — Decorate Your Functions

Pick the right decorator for each function type:

```python
from beacon_kit import (
    traced_agent,       # agent entry points
    traced_llm_call,    # LLM API calls
    traced_tool,        # internal tool functions
    traced_mcp_tool,    # MCP server tool calls
    traced_a2a_hop,     # outbound HTTP calls to another agent/service
    traced_pipeline,    # top-level orchestration functions
    traced_node,        # LangGraph graph node functions
    track_latency,      # record duration as a histogram metric
    count_calls,        # increment a counter metric
    log_calls,          # emit trace-linked log records
)

@traced_agent("customer-onboarding-agent")
def run_onboarding(customer_id: str) -> dict: ...

@traced_llm_call(model="claude-sonnet-4-6")
def generate_summary(prompt: str) -> dict: ...

@traced_tool("crm_lookup")
def lookup_customer(customer_id: str) -> dict: ...

@traced_mcp_tool(service="crm-mcp", tool_name="get_account")
def get_account_from_mcp(account_id: str) -> dict: ...

@traced_a2a_hop(target_node="compliance-service")
def call_compliance_check(payload: dict, carrier: dict | None = None) -> dict: ...
```

### Step 5 — Add Middleware (FastAPI / Starlette only)

```python
from beacon_kit.middleware.fastapi import TelemetryMiddleware

app.add_middleware(TelemetryMiddleware)
# This adds: service.run_id, http.route, service.name to every request span
```

If your service receives A2A calls from other services:
```python
from beacon_kit.middleware.a2a import A2ATraceContextMiddleware

app.add_middleware(A2ATraceContextMiddleware)
# This extracts incoming traceparent headers and stitches traces cross-service
```

### Optional — Enable Compliance Events

If your service has pricing, guardrails, or access control:
```python
from beacon_kit import set_default_compliance_config, compliance_gate, guardrail_check

# Once at startup — after setup_telemetry()
set_default_compliance_config(TelemetryConfig.from_env())

# Then decorate:
@compliance_gate("my_event_type")
def my_sensitive_function(...): ...
```

---

## 5. File Structure — What Every File Does

```
beacon_kit/
│
├── __init__.py                  Public API — imports everything you need from one place
├── pyproject.toml               Package definition and dependencies
├── STUDY_GUIDE.md               This file
│
├── core/
│   ├── config.py                TelemetryConfig — all settings, reads from env vars
│   ├── provider.py              setup_telemetry(), flush_telemetry(), TelemetryHandle
│   ├── exporters.py             ExporterFactory — builds span/metric/log exporters per profile
│   ├── file_exporters.py        FileSpanExporter, FileMetricExporter, FileLogExporter (local JSONL)
│   └── pretty_exporter.py       Rich terminal waterfall exporter for local dev
│
├── decorators/
│   ├── trace.py                 @traced, @traced_node, @traced_pipeline (general spans)
│   ├── tool.py                  @traced_tool (privacy-safe CLIENT span for tools)
│   ├── agent.py                 @traced_agent, @traced_llm_call, @traced_a2a_hop, @traced_mcp_tool
│   ├── metrics.py               @track_latency, @count_calls (metric decorators)
│   └── log.py                   @log_calls (OTEL-linked log records)
│
├── metrics/
│   └── agentic.py               AgenticMetrics — manual record_*() methods for agentic counters
│
├── middleware/
│   ├── fastapi.py               TelemetryMiddleware — enriches spans on FastAPI/Starlette
│   └── a2a.py                   A2ATraceContextMiddleware — extracts W3C context on inbound A2A
│
├── propagation/
│   ├── context.py               serialize_context(), restore_context() for HITL checkpoints
│   └── baggage.py               W3C Baggage helpers — inject, extract, BaggageSpanProcessor
│
├── compliance/
│   ├── events.py                ComplianceEvent, GuardrailEvent, RBACEvent, emit_* functions
│   ├── chain.py                 compute_chain_hash(), sign_event(), verify_chain()
│   └── decorators.py            @compliance_gate, @guardrail_check, @rbac_check
│
├── circuit_breaker/
│   └── breaker.py               TelemetryCircuitBreaker — emits OTel events on state transitions
│
├── logconfig/
│   └── request_logging.py       TraceContextFilter, setup_request_logging()
│
├── utils/
│   ├── hashing.py               hash_inputs() (SHA-256), summarize_output() (structural summary)
│   └── sanitizer.py             scrub_pii() — removes tokens, IBANs, emails before logging
│
└── examples/
    └── demo_all_layers.py       Runnable demo — shows all 3 layers in 30 seconds
```

---

## 6. Service Integration Map — Before vs After

### agent-mesh

| File | Before beacon_kit | After beacon_kit |
|---|---|---|
| `run.py` lines 25–26 | `from src.observability import setup_observability` + `setup_observability("agent_mesh_cli")` | Commented out → `setup_telemetry(TelemetryConfig.from_env(service_name="agent_mesh_cli"))` |
| `api_server.py` lines 44–45 | `setup_observability("agent_mesh_api")` | Commented out → `setup_telemetry(...)` + `TelemetryMiddleware` in middleware stack |
| `api_server.py` line 832 | `flush_observability()` | Commented out → `flush_telemetry(_bk_handle)` |
| `api_server.py` lines 1157–1161 | `StarletteInstrumentor().instrument_app(app)` | Commented out → `TelemetryMiddleware` handles this |
| `a2a_server.py` line 112 | `setup_observability(f"agent_mesh_{args.agent}")` | Commented out → per-node `setup_telemetry(...)` |
| `devui_app.py` lines 49–52 | `setup_observability("agent_mesh_devui")` | Commented out → `setup_telemetry(...)` (`OBS_PROFILE=off` → no-op exporters) |

The entire `agent-mesh/src/observability/` directory is **untouched** — all original code is preserved.

### rag-as-a-service

| File | Before beacon_kit | After beacon_kit |
|---|---|---|
| `main.py` line 18 | `from .utils.telemetry import configure_telemetry, instrument_fastapi` | Commented out → `from beacon_kit import ...` |
| `main.py` line 29 | `configure_telemetry(settings.service_name)` | Commented out → `setup_telemetry(...)` |
| `main.py` line 84 | `instrument_fastapi(app)` | Commented out → `app.add_middleware(TelemetryMiddleware)` |
| `utils/telemetry.py` | Function bodies doing TracerProvider setup + FastAPIInstrumentor | Bodies commented out, signatures preserved |
| `.env` | No OTLP exporter — traces went nowhere | Added `EXPORTER_TYPE=grafana` + Grafana endpoint |

### datalayer-as-service

| File | Before beacon_kit | After beacon_kit |
|---|---|---|
| `mcp_server/server.py` | Only `logging.basicConfig()` — no OTel at all | beacon_kit init added (wrapped in try/except) |
| `app.py` | Only `logging.basicConfig()` — no OTel at all | beacon_kit init added (wrapped in try/except) |
| `.env` | No telemetry vars | Grafana vars to be added |

---

## 7. Environment Variables Reference

| Environment Variable | Maps to | Default | Notes |
|---|---|---|---|
| `OBS_PROFILE` | `exporter_type` | `console` | Agent-mesh convention. `grafana`, `grafana_and_file`, `file`, `dev`, `prod`, `off` |
| `EXPORTER_TYPE` | `exporter_type` | `console` | beacon_kit native. Takes priority over `OBS_PROFILE` |
| `GRAFANA_OTLP_ENDPOINT` | `otel_endpoint` | — | Grafana Cloud OTLP/HTTP gateway URL |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `otel_endpoint` (fallback) | `http://localhost:4317` | Local OTLP collector (used with `OBS_PROFILE=dev`) |
| `GRAFANA_INSTANCE_ID` | `grafana_instance_id` | — | Grafana Cloud instance ID for Basic auth |
| `GRAFANA_API_TOKEN` | `grafana_api_key` | — | Grafana Cloud API token for Basic auth |
| `GRAFANA_API_KEY` | `grafana_api_key` (fallback) | — | Alternative name for API token |
| `OTEL_SERVICE_NAME` | `service_name` | `unnamed-service` | Fallback if `service_name` not passed to `from_env()` |
| `SERVICE_NAME` | `service_name` (fallback) | `unnamed-service` | Alternative name |
| `SERVICE_VERSION` | `service_version` | `0.0.0` | Shown in Grafana as resource attribute |
| `ENVIRONMENT` | `environment` | `development` | Shown in Grafana; affects HMAC key validation |
| `POD_NAME` | `pod` | `""` | Kubernetes pod name if running in K8s |
| `OTEL_SAMPLING_RATE` | `sampling_rate` | `1.0` | Head-based sampling: 1.0 = 100%, 0.1 = 10% |
| `ENABLE_BAGGAGE_PROPAGATION` | `enable_baggage_propagation` | `true` | Copy W3C Baggage values to span attributes |
| `ENABLE_COMPLIANCE` | `enable_compliance` | `false` | Enable compliance event emission |
| `COMPLIANCE_STORE` | `compliance_store` | `compliance_events.jsonl` | Path to compliance JSONL file |
| `COMPLIANCE_HMAC_KEY` | `compliance_hmac_key` | `dev-insecure-key-...` | HMAC key for audit signing. Must be changed in prod |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | `azure_connection_string` | `""` | Azure Monitor connection string (prod only) |
| `OTEL_FILE_OUTPUT_DIR` | `file_output_dir` | `""` | Root directory for local JSONL files. Set to `../observability` from agent-mesh/. Subdirs `traces/`, `metrics/`, `logs/` are auto-created. When non-empty, a secondary file processor is wired alongside the primary exporter. |
| `OTEL_FILE_SESSION_ID` | `file_session_id` | `""` | Optional label appended to filenames: `YYYYMMDD_HHMMSS_{service}_{session_id}.jsonl`. Useful for tagging individual test runs. |

---

## 8. Viewing Results in Grafana

### Grafana Tempo (Traces)

1. Open Grafana → **Explore** → select the **Tempo** data source
2. Search by service name: `service.name = agent_mesh_api`
3. Click a trace to open the span waterfall tree
4. Look for span names like `agent.triage-agent`, `llm.call`, `tool.account_balance_lookup`, `a2a.rag-service`

**Key span attributes to filter on:**
- `agent.name` — which agent handled the request
- `llm.model` — which model was called
- `a2a.target_node` — which service was called via A2A

### Grafana Mimir (Metrics)

Query these in **Explore** with the Prometheus data source:
```promql
# Agent call rate
rate(beacon_agent_calls_total[5m])

# LLM call latency (p99)
histogram_quantile(0.99, rate(beacon_llm_duration_bucket[5m]))

# A2A hop error rate
rate(beacon_a2a_calls_total{result="error"}[5m])
  / rate(beacon_a2a_calls_total[5m])
```

### Grafana Loki (Logs)

```logql
# Find all logs for a specific trace
{service_name="agent_mesh_api"} |= "trace_id=eb5fd96609b2d94d"

# Find all errors across services
{service_name=~"agent_mesh.*"} | json | level="ERROR"
```

### Local Pretty Mode (no Grafana needed)

Set `OBS_PROFILE=pretty` in your `.env` and start your service. You will see a rich terminal table for every request:

```
Layer 02 — Span Trace: my-service  trace: eb5fd966...
  Span                              Kind       Duration   Status
 ─────────────────────────────────────────────────────────────
  my-pipeline                       INTERNAL    241 ms   OK
    ├ agent.triage-agent            SERVER       51 ms   OK
    ├ llm.call                      CLIENT       81 ms   OK
    └ tool.account_balance_lookup   CLIENT       20 ms   OK
```

---

## 9. Running the Local Demo

The demo script exercises all 3 layers in a single pipeline run — no Grafana, no collectors needed:

```bash
cd beacon_kit
python examples/demo_all_layers.py
```

**What you will see:**

1. **Span waterfall** (Layer 1 + 2) — rich terminal table showing all spans nested under `retail-banking-pipeline`
2. **Metrics snapshot** (Layer 2) — counters and histograms for each call type
3. **Compliance JSONL** (Layer 3) — 2 signed events written to `compliance_audit.jsonl`

The demo is self-contained. You can modify it to test your own functions with any decorator before wiring them into a live service.

---

## 10. Reverting to Old Observability

All changes follow a **comment-don't-delete** strategy. The original observability code is commented out with a `# BEACON_KIT:` prefix so reverting is mechanical.

**To revert a service:**

1. Find the `# BEACON_KIT:` block in the entry point file
2. Comment out the beacon_kit lines
3. Uncomment the original lines

**Example — reverting `agent-mesh/run.py`:**
```python
# BEFORE (beacon_kit):
# from src.observability import setup_observability
# setup_observability(service_name="agent_mesh_cli")
from beacon_kit import setup_telemetry, TelemetryConfig
_bk_handle = setup_telemetry(TelemetryConfig.from_env(service_name="agent_mesh_cli"))

# AFTER (reverted to original):
from src.observability import setup_observability
setup_observability(service_name="agent_mesh_cli")
# from beacon_kit import setup_telemetry, TelemetryConfig
# _bk_handle = setup_telemetry(TelemetryConfig.from_env(service_name="agent_mesh_cli"))
```

The original `src/observability/` module in agent-mesh was never modified — it is fully intact and ready to use.

**Files to revert per service:**

| Service | Files with BEACON_KIT comments |
|---|---|
| agent-mesh | `run.py`, `api_server.py`, `a2a_server.py`, `devui_app.py` |
| rag-as-a-service | `src/gernas_rag/main.py`, `src/gernas_rag/utils/telemetry.py` |
| datalayer | `mcp_server/server.py`, `app.py` (remove the try/except beacon_kit block) |

---

*beacon_kit version 0.2.0 — built for this project, designed for any project.*
