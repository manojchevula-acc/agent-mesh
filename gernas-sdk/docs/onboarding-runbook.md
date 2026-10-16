# Onboarding runbook — instrument an agent with the Gernas SDK

Follow these steps in order. The checks at the end match `gernas doctor`.

## 1. Install

```powershell
pip install gernas-telemetry==0.3.0            # from the FAB package registry once published
# local (this repo):
pip install -e .\gernas-sdk                     # or: pip install gernas-sdk\dist\gernas_telemetry-0.3.0-py3-none-any.whl
```

Optional extras: `[langchain]`, `[agent-framework]`, `[pretty]`, `[audit]`, `[breaker]`.

## 2. Configure environment variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `GERNAS_OTLP_ENDPOINT` | **yes** (for `otlp`) | — | Collector endpoint, e.g. `http://127.0.0.1:4317` |
| `GERNAS_OTLP_TRANSPORT` | | `grpc` | `grpc` or `http`. HTTP uses `:4318/v1/<signal>`. |
| `GERNAS_OTLP_API_KEY` | | — | Sent as a bearer header to the Collector |
| `GERNAS_OTLP_TIMEOUT_S` | | `10` | Export timeout |
| `GERNAS_EXPORTER` | | `otlp` | `otlp`, `console`, `pretty`, `file`, `memory`, `off` |
| `GERNAS_SERVICE_NAME` / `GERNAS_SERVICE_VERSION` | | `gernas-service` / `0.0.0` | Service identity |
| `GERNAS_AGENT_VERSION` | | service version | `gernas.agent_version` |
| `GERNAS_USE_CASE_ID` / `GERNAS_APPLICATION_ID` / `GERNAS_BUSINESS_DOMAIN` | | `unassigned` / service / `unassigned` | Envelope on every span |
| `GERNAS_ENVIRONMENT` | | `dev` | `dev`, `sit`, `uat`, `staging`, `prod` |
| `GERNAS_EMIT_OPIK` / `GERNAS_EMIT_DATAPLANE` | | `true` | Destination kill switches, applied by the Collector |
| `GERNAS_SAMPLING_RATE` | | `1.0` | Head sampling. Tail sampling is done in the Collector. |
| `GERNAS_LOOP_THRESHOLD` / `GERNAS_MAX_STEPS` | | `3` / `50` | Behavioural detection |
| `GERNAS_RATE_CARD_PATH` | | — | JSON rate card → `gernas.est_cost_aed` |
| `GERNAS_LLM_PROVIDER` | | — | Default `gen_ai.provider.name` |
| `GERNAS_CAPTURE_PAYLOADS` | | `false` | Lets redacted payload fields through. Only under a WP2-A-05 grant. |
| `GERNAS_BAGGAGE_ATTRIBUTES` | | session, request, role, parent run, channel, task | Baggage keys copied onto spans |
| `GERNAS_ROLE_BAGGAGE_KEYS` | | `gernas.user_role` | Read by `rbac_check` |
| `GERNAS_LOG_NAMESPACES` | | `gernas` | Python loggers bridged to OTel logs |
| `GERNAS_FILE_OUTPUT_DIR` | | — | Extra local JSONL sinks and dev HTML/audit reports |
| `GERNAS_COMMIT_SHA` / `GERNAS_PROVENANCE_ID` / `GERNAS_WORK_UNIT_ID` | | — | Join keys |
| `GERNAS_ENABLE_COMPLIANCE` / `GERNAS_COMPLIANCE_STORE` / `GERNAS_COMPLIANCE_HMAC_KEY` | | off / `compliance_events.jsonl` / dev key | Layer-3 audit chain. A secure key is mandatory outside `dev`. |

## 3. Start the Collector (local / dev)

```powershell
cd gernas-sdk
# one-time: download otelcol-contrib (v0.162.0 used here) into collector\bin\ and verify its SHA-256
python scripts\run_collector.py --validate      # config check
python scripts\run_collector.py                 # loads OPIK_* from ..\agent-mesh\.env; serves :4317/:4318
```

Output files are `collector\out\dataplane-traces.json`, `dataplane-metrics.json` and `dataplane-logs.json`. Traces also go to Opik when `OPIK_API_KEY`, `OPIK_WORKSPACE` and `OPIK_PROJECT_NAME` are set. Health check: `http://127.0.0.1:13133/`. Self-metrics: `http://127.0.0.1:8888/metrics`.

## 4. Call `init_gernas()` once at startup

```python
import gernas
gernas.init_gernas()
```

## 5. Decorate the entry point

Add `@gernas.trace_use_case(...)` to the request handler and `@gernas.trace_agent(...)` to the agent. Use the framework adapters from the developer guide, or decorate tools, LLM calls and hand-offs individually.

## 6. Verify

```powershell
gernas doctor --opik
```

A healthy result looks like this:

```text
[PASS] configuration  GERNAS_* parsed
[PASS] collector      127.0.0.1:4317 reachable in 8.4 ms
[PASS] export         test span accepted ...
[PASS] context        13/13 canonical fields present
[PASS] opik           found in project '...'
RESULT: PASS
```

Then run your agent and check what it exported:

```powershell
gernas conformance --file collector\out\dataplane-traces.json --service <your-service> --require-op agent.run
python scripts\inspect_trace.py <trace_id>
python scripts\e2e_pipeline_checks.py           # PDPL, kill switches, routing, Opik
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `KeyError: GERNAS_OTLP_ENDPOINT is required` | No endpoint set | Set it, or choose a local sink with `GERNAS_EXPORTER=console` |
| `doctor` collector FAIL | The Collector is not running or the port is blocked | `python scripts\run_collector.py`; check `:13133` |
| Spans missing from the data plane for a few seconds | Tail sampling holds each trace for `GERNAS_TAIL_DECISION_WAIT` (5 s locally) | Wait, or lower the wait in dev |
| An attribute is missing in the data plane | Deny-by-default: the key is not in the registry | Register the field in `registry/domains/*.yaml` and run `python registry\generate.py` |
| `gernas.sdk.dropped_spans` rising | The Collector is unreachable or its queue is full | Check the Collector and its `otelcol_exporter_queue_size` |
