# Onboarding runbook — instrument an agent with the Gernas SDK

Follow these steps in order. The checks at the end match `gernas doctor`.

## 1. Install

```powershell
pip install gernas-telemetry==0.4.0             # from the FAB package registry once published
# local (this repo):
pip install -e .\gernas-sdk                     # or: pip install gernas-sdk\dist\gernas_telemetry-0.4.0-py3-none-any.whl
```

Optional extras, one per framework:
- `[agent-framework]`
- `[a2a]`
- `[langchain]`
- `[langgraph]`

The core depends only on OpenTelemetry.

## 2. Configure environment variables

| Variable | Required | Default | Meaning (Blueprint field) |
|---|---|---|---|
| `GERNAS_SERVICE_NAME` | **yes** | — | OTel `service.name` |
| `GERNAS_USE_CASE_ID` | **yes** | — | `use_case_id` |
| `GERNAS_APPLICATION_ID` | **yes** | — | `application_id` |
| `GERNAS_BUSINESS_DOMAIN` | **yes** | — | `business_domain` |
| `GERNAS_ENVIRONMENT` | **yes** | — | `environment` |
| `GERNAS_OTLP_ENDPOINT` | **yes** when the exporter is `otlp` | — | Collector endpoint, e.g. `http://127.0.0.1:4317` |
| `GERNAS_EXPORTER` | | `otlp` | `otlp`, `console` or `off` |
| `GERNAS_OTLP_TRANSPORT` | | `grpc` | `grpc` or `http` (HTTP uses `:4318/v1/<signal>`) |
| `GERNAS_OTLP_API_KEY` | | — | Sent as a bearer header to the Collector; never recorded |
| `GERNAS_OTLP_TIMEOUT_S` | | `10` | Export timeout |
| `GERNAS_SERVICE_VERSION` | | `0.0.0` | OTel `service.version` |
| `GERNAS_AGENT_VERSION` | | service version | `agent_version` |
| `GERNAS_CLUSTER` / `GERNAS_NAMESPACE` / `GERNAS_REGION` | | — | `cluster`, `namespace`, `region` |
| `GERNAS_POD` | | `HOSTNAME` inside Kubernetes | `pod` |
| `GERNAS_USER_TOKEN_KEY` | | — | HMAC key for `tokenized_user_id`. Unset means user identity is not recorded at all. Treat it as a secret. |
| `GERNAS_SAMPLING_RATE` | | `1.0` | Head sampling. Tail sampling is done in the Collector. |
| `GERNAS_METRIC_INTERVAL_MS` | | `30000` | Metric export interval |
| `GERNAS_LOG_NAMESPACES` | | `gernas` | Python loggers bridged to OTel logs |
| `GERNAS_LOOP_THRESHOLD` / `GERNAS_MAX_STEPS` | | `3` / `50` | Loop rate and maximum step violation |
| `GERNAS_RATE_CARD_PATH` | | — | JSON rate card that produces `estimated_cost` |

An invalid or missing mandatory value raises `GernasConfigError`, which lists every problem.

## 3. Start the Collector (local / dev)

```powershell
cd gernas-sdk
# one-time: download otelcol-contrib into collector\bin\ and verify its SHA-256
python scripts\run_collector.py --validate      # config check
python scripts\run_collector.py                 # loads OPIK_* from ..\agent-mesh\.env; serves :4317/:4318
```

Outputs:
- `collector\out\dataplane-traces.json`, `dataplane-metrics.json` and `dataplane-logs.json`.
- Traces also go to Opik when `OPIK_API_KEY`, `OPIK_WORKSPACE` and `OPIK_PROJECT_NAME` are set. The Collector maps the GERNAS keys to the GenAI keys Opik displays.
- Health check: `http://127.0.0.1:13133/`.
- Self-metrics: `http://127.0.0.1:8888/metrics`.

## 4. Call `init_gernas()` once at startup

```python
import gernas
gernas.init_gernas()
```

## 5. Instrument

- Framework agents: add the adapter (`gernas_middleware`, `instrument`, `instrument_graph`); see the developer guide, §4.
- Custom Python: add `@gernas.trace_use_case` to the request handler, `@gernas.trace_agent(agent_id, agent_type=...)` to the agent, `@gernas.trace_step` to workflow steps, and the tool, LLM and retrieval decorators where they are not already covered by an adapter.
- Wrap each request in `gernas.request_context(session_id=..., task_id=..., user_id=..., principal_type=...)`.
- MAF only: MAF's built-in instrumentation emits its own `invoke_agent` / `execute_tool` spans next to the GERNAS spans. Set `ENABLE_INSTRUMENTATION=false` if you do not want them.

## 6. Verify

```powershell
gernas doctor --opik
```

A healthy result looks like this:

```text
[PASS] configuration  GERNAS_* valid (service 'my-agent', exporter otlp)
[PASS] collector      127.0.0.1:4317 reachable in 8.4 ms
[PASS] export         test span accepted ...
[PASS] context        12/12 context fields present
[PASS] opik           found in project '...'
RESULT: PASS
```

Then run your agent and check what it exported:

```powershell
gernas conformance --file collector\out\dataplane-traces.json --service <your-service> --require-op agent.run
python scripts\inspect_trace.py <trace_id>
python scripts\e2e_pipeline_checks.py           # PDPL, routing, Opik
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `GernasConfigError: Missing required Gernas configuration: ...` | A mandatory identity variable is unset | Set the listed variables |
| `GERNAS_OTLP_ENDPOINT is required` | No endpoint and exporter `otlp` | Set it, or use `GERNAS_EXPORTER=console` |
| `GERNAS_USER_TOKEN_KEY is not set` warning | `request_context(user_id=...)` was used without a key | Set the key, or stop passing `user_id` |
| `doctor` collector FAIL | The Collector is not running or the port is blocked | `python scripts\run_collector.py`; check `:13133` |
| Spans missing from the data plane for a few seconds | Tail sampling holds each trace for `GERNAS_TAIL_DECISION_WAIT` (5 s locally) | Wait, or lower the wait in dev |
| An attribute is missing in the data plane | Deny-by-default: the key is not in the registry | Register the field in `registry/domains/*.yaml` and run `python registry\generate.py` |
| `gernas.sdk.dropped_spans` rising | The Collector is unreachable or its queue is full | Check the Collector and its `otelcol_exporter_queue_size` |
