# Gernas SDK — end-to-end runbook

This runbook takes you from a clean checkout to a verified trace in Opik. The path is **Agent Mesh → Gernas SDK → OTel Collector → Opik + data plane**.

**Verification status.** Every command in Parts A–F was run on **2026-10-18** on Windows 11 with:
- Python 3.14.6, OpenTelemetry 1.45.1, otelcol-contrib 0.162.0;
- live traces `LIVE18A000`–`LIVE18A003` and `MCPPROBE18A`, all `opik_verify` **PASS**.

Part G (AKS) has **not** been run: there is no cluster, ADX instance or Azure credentials. It repeats the documented procedure from [collector-aks-deployment.md](collector-aks-deployment.md) and is marked as unverified.

Related documents:
- [GERNAS_SDK_INSTRUMENTATION_GUIDE.md](GERNAS_SDK_INSTRUMENTATION_GUIDE.md): how to instrument a new use case.
- [GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html](GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html): diagrams, API catalogue, call-site map.
- [SOW_COMPLIANCE_REPORT.md](SOW_COMPLIANCE_REPORT.md) and `../../checklist.md`: compliance status.

---

## Part A — Prerequisites and layout

### A.1 Supported versions and tools

| Component | Version used for verification | Minimum | Notes |
|---|---|---|---|
| Python | 3.14.6 | SDK ≥ 3.11; Agent Mesh ≥ 3.13 (`CLAUDE.md`) | On Windows, keep the venv at a short path (MAX_PATH) |
| OpenTelemetry SDK | 1.45.1 | 1.24 | Installed with the SDK |
| otelcol-contrib | 0.162.0 | — | Binary in `gernas-sdk/collector/bin/` (not in git) |
| MySQL | 8.0 (Windows service `MySQL80`) | — | DataLayer semantic views |
| Qdrant | not running in the verification run | — | Needed only for RAG retrieval answers |
| Opik | Opik Cloud (`www.comet.com`) | — | API key, workspace, project |
| LLM | Groq, OpenAI-compatible | — | `GROQ_API_KEY` |

### A.2 Repository structure

There is one git repository, `agent-mesh/`. All paths below are relative to it.

| Path | Responsibility |
|---|---|
| `gernas-sdk/` | **Gernas SDK** (`gernas-telemetry`, `import gernas`): the central telemetry toolkit, plus the Collector config, scripts and docs |
| `gernas-sdk/collector/` | Collector config (`otel-collector-config.yaml`), local overlay, AKS manifests (`k8s/`, `kustomization.yaml`), ADX tables (`adx/`) |
| `gernas-sdk/scripts/` | `run_collector.py`, `opik_verify.py`, `inspect_trace.py`, `mcp_probe.py`, `load_env.ps1`, `build_architecture_doc.py` |
| `agent-mesh/` | Agent Mesh reference use case. Its single SDK integration module is `src/observability/gernas_telemetry.py` |
| `datalayer-as-service/` | DataLayer MCP server (fastmcp → MySQL), instrumented with `GernasFastMCPMiddleware` |
| `rag-as-a-service/` | RAG MCP server and REST API (`trace_retrieval`, `trace_document`) |
| `checklist.md` | Authoritative audit record |

---

## Part B — Installation (run once)

All commands run from the repository root `agent-mesh/`, in PowerShell.

```powershell
py -3.14 -m venv .venv                                   # or any Python >= 3.13
.venv\Scripts\pip install --pre -r requirements.txt      # Agent Mesh + services (MAF pre-releases need --pre)
.venv\Scripts\pip install -e ".\gernas-sdk[dev,agent-framework,a2a]"
```

- **What it does:** creates the virtual environment and installs the mesh, the services, and the SDK in editable mode with the MAF and A2A adapters.
- **Expected:** `pip` ends with `Successfully installed …`, and `.venv\Scripts\python -c "import gernas; print(gernas.__version__)"` prints `0.4.0`.
- **On failure:** if you hit a path-too-long error on Windows, use a shorter venv path such as `C:\fab-venv`. If `agent-framework` versions cannot be resolved, add `--pre`.

**Collector binary.** Download `otelcol-contrib` 0.162.0 for your OS from the opentelemetry-collector-releases GitHub release into `gernas-sdk/collector/bin/`. Verify the SHA-256 against the release checksum file, as described in [onboarding-runbook.md](onboarding-runbook.md) §3.

```powershell
.venv\Scripts\python gernas-sdk\scripts\run_collector.py --validate
```

**Expected:**
- exit code 0;
- the line `collector: …otelcol-contrib.exe  configs: ['otel-collector-config.yaml', 'local-dataplane-file.yaml']  env keys loaded: [...]`, which lists names only;
- without `ADX_CLUSTER_URI`, the warning `data plane goes to LOCAL FILES (collector/out/)`.

If `OPIK_*` is missing, the script warns `… not set — the Opik exporter will fail` and continues. Variable values are never printed.

**One-time data setup** (only needed for data answers):
- DataLayer: run `01_…` → `04_…` in `datalayer-as-service/`, as described in the repository `CLAUDE.md`.
- RAG: run `python scripts/ingest_docs.py` in `rag-as-a-service/`. This requires Qdrant.

---

## Part C — Configuration and secrets

All settings live in **`agent-mesh/.env`**; copy `agent-mesh/.env.example` to create it. **Never commit `.env` and never print its values.** The scripts below read it and print only variable *names*.

| Variable | Read by | Required | Example (non-secret) |
|---|---|---|---|
| `GERNAS_OTLP_ENDPOINT` | every instrumented process | yes | `http://127.0.0.1:4317` |
| `GERNAS_USE_CASE_ID`, `GERNAS_APPLICATION_ID`, `GERNAS_BUSINESS_DOMAIN`, `GERNAS_ENVIRONMENT` | every instrumented process | **yes**: if missing, telemetry is off with a warning, and identity is never invented | your use-case registry values |
| `GERNAS_SERVICE_NAME` | SDK | yes. Agent Mesh entrypoints and the MCP servers pass their own name; your own scripts must set it | `my_use_case_api` |
| `GERNAS_USER_TOKEN_KEY` | SDK (`request_context`) | optional, **secret** | HMAC key. If unset, no user identity is recorded |
| `GERNAS_RATE_CARD_PATH` | SDK | optional | JSON rate card that produces `estimated_cost` |
| `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME` | **Collector only**, and `opik_verify.py` | for Opik | `OPIK_API_KEY` is **secret** |
| `ADX_CLUSTER_URI`, `ADX_DATABASE` (+ Azure credentials) | Collector only | for ADX | none locally: the data plane goes to local files instead |
| `GROQ_API_KEY` (+ `*_MODEL`) | Agent Mesh agents | for live answers | **secret** |
| `ENABLE_INSTRUMENTATION=false`, `ENABLE_SENSITIVE_DATA=false`, `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false` | Agent Mesh | keep `false` | Framework-native tracing off: Gernas is the only instrumentation path |

**Processes that do not read `agent-mesh/.env` themselves** need these values in their own environment:
- the DataLayer MCP server;
- the RAG MCP server (it reads `rag-as-a-service/.env`, which has no `GERNAS_*`);
- `opik_verify.py`;
- `gernas doctor`;
- `mcp_probe.py`.

Load them into the current shell with the helper, which prints only names:

```powershell
. .\gernas-sdk\scripts\load_env.ps1 -Path .\agent-mesh\.env -Prefix GERNAS_,OPIK_
# loaded: OPIK_API_KEY, OPIK_PROJECT_NAME, OPIK_WORKSPACE, GERNAS_OTLP_ENDPOINT, ...
```

> Pitfall found during verification: an MCP server started without these variables logs `Gernas SDK telemetry disabled: Missing required Gernas configuration: GERNAS_USE_CASE_ID, …`. It keeps serving requests, but its spans are missing from the trace.

---

## Part D — Start the stack (local development)

Start each item in its own terminal, in this order, from `agent-mesh/`.

| # | Component | Command | Port(s) | Ready when |
|---|---|---|---|---|
| 1 | Collector | `.venv\Scripts\python gernas-sdk\scripts\run_collector.py --env agent-mesh\.env` | 4317 gRPC, 4318 HTTP, 13133 health, 8888 metrics | `curl http://127.0.0.1:13133/` returns "Server available" |
| 2 | DataLayer MCP | `. .\gernas-sdk\scripts\load_env.ps1 -Path .\agent-mesh\.env -Prefix GERNAS_; cd datalayer-as-service; $env:MCP_TRANSPORT="http"; $env:MCP_PORT="9100"; ..\.venv\Scripts\python -m mcp_server.server` | 9100 | port listening; **no** "telemetry disabled" warning |
| 3 | RAG MCP | `. .\gernas-sdk\scripts\load_env.ps1 -Path .\agent-mesh\.env -Prefix GERNAS_; cd rag-as-a-service; $env:MCP_TRANSPORT="http"; $env:MCP_PORT="9000"; ..\.venv\Scripts\python -m mcp_integration.server` | 9000 | port listening |
| 4 | Agent Mesh nodes | `cd agent-mesh; ..\.venv\Scripts\python launch_mesh.py` | 8015 compliance, 8016 data_agent, 8017 rag_agent, 8018 price_assist | all four ports listening |

Readiness check, which waits until every port is up:

```powershell
$ports=4317,13133,9000,9100,8015,8016,8017,8018
$ports | ForEach-Object { "$_ $(Test-NetConnection 127.0.0.1 -Port $_ -WarningAction SilentlyContinue -InformationLevel Quiet)" }
```

**SDK initialisation** happens inside each process: `gernas_telemetry.init_telemetry(...)` in `run.py`, `api_server.py`, `a2a_server.py`, `devui_app.py` and `demos/`, and `gernas.init_gernas(...)` in the services. To confirm a process can reach the Collector and Opik before running a workflow:

```powershell
. .\gernas-sdk\scripts\load_env.ps1 -Path .\agent-mesh\.env -Prefix GERNAS_,OPIK_
$env:GERNAS_SERVICE_NAME="gernas_doctor"; .venv\Scripts\python -m gernas doctor --opik
```

Expected output (2026-10-18):

```
[PASS] configuration  GERNAS_* valid (service 'gernas_doctor', exporter otlp)
[PASS] collector      127.0.0.1:4317 reachable in 29.5 ms
[PASS] export         test span accepted ...
[PASS] context        12/12 context fields present
[PASS] opik           found in project '<OPIK_PROJECT_NAME>'
RESULT: PASS
```

---

## Part E — Run a representative workflow and verify the trace

### E.1 Run

```powershell
cd agent-mesh
..\.venv\Scripts\python demos\gernas_e2e_queries.py --prefix LIVE18A     # use a new prefix per run
```

This sends four queries through the real orchestrator, the A2A nodes, the MCP servers and Groq:
- Q1: data;
- Q2: knowledge;
- Q4: hybrid;
- Q6: prompt injection.

It prints one JSON line per request. The `request_id` (for example `LIVE18A000`) is the GERNAS `task_id` you search for.

Observed on 2026-10-18:

```
{"label": "Q1-data", "request_id": "LIVE18A000", "blocked": false, "trail": ["compliance_pass", "domain_answer:price_assist", ...]}
{"label": "Q2-knowledge", "request_id": "LIVE18A001", "blocked": true, "block_stage": "compliance", ...}
{"label": "Q4-hybrid", "request_id": "LIVE18A002", "blocked": false, ...}
{"label": "Q6-injection", "request_id": "LIVE18A003", "blocked": true, "block_stage": "input_guardrail", ...}
```

### E.2 Verify in Opik (trace generation, hierarchy, attributes, no duplicates)

```powershell
cd ..\gernas-sdk
. .\scripts\load_env.ps1 -Path ..\agent-mesh\.env -Prefix OPIK_
..\.venv\Scripts\python scripts\opik_verify.py --task-id LIVE18A000 --timeout 120
```

`opik_verify.py` prints the span tree as Opik stored it, then five checks:

| Check | Proves |
|---|---|
| `request is one Opik trace` | One trace ID across all processes: A2A propagation works |
| `every parent link resolves` | Parent–child relationships are intact (no orphans) |
| `GERNAS conformance on Opik's copy` | Envelope, run context, enums and the denylist on every span |
| `llm.call typed 'llm' with model by Opik` | The Collector's Opik mapping works |
| `only Gernas SDK spans` | No competing or duplicate instrumentation |

**Expected:** `RESULT: PASS`. Result on 2026-10-18: PASS for all four requests. For example, `LIVE18A000` had `use_case.request` → `agent.run(agent_mesh)` → 7 `agent.step` → `guardrail.check` ×2 (`input_screen`, `rbac_role_check`) → `agent.delegate` ×3 → `POST /` → `agent.run(price_assist / data_agent)` → `llm.call` ×5.

### E.3 Verify context, baggage and attributes

- **In Opik:** open the project `OPIK_PROJECT_NAME` and the trace that `opik_verify.py` reported. Opik shows OTel span attributes in each span's input map. Every span carries:
  - `gernas.business_domain`, `gernas.use_case_id`, `gernas.application_id`, `gernas.environment`;
  - `gernas.session_id` and `gernas.task_id`, from baggage;
  - `gernas.agent_id` and `gernas.agent_run_id` inside a run.
  Remote `agent.run` spans carry `gernas.parent_run_id`.
- **Locally:** the data-plane copy is in `gernas-sdk/collector/out/dataplane-traces.json`. It is rewritten on every Collector start.

  ```powershell
  ..\.venv\Scripts\python scripts\inspect_trace.py <32-hex trace id>     # tree + summary JSON
  ..\.venv\Scripts\python -m gernas conformance --file collector\out\dataplane-traces.json --service agent_mesh_e2e
  ```

### E.4 Verify error handling

These failure paths happen naturally in the reference run:

| Request | What to look for | Meaning |
|---|---|---|
| `LIVE18A002` | `agent.run(rag_agent)` and its `llm.call` with `execution_status=error`, `termination_reason=error`, ERROR status and an `exception` event | A real failure (the model returned 404) is recorded and re-raised; the workflow degrades gracefully |
| `LIVE18A003` | `guardrail.check` `policy_id=input_screen`, `policy_decision=deny`, status **OK**; `agent.run` `termination_reason=policy_block` | A policy block is an expected outcome, not an error |

### E.5 Verify the MCP leg (cross-process MCP context)

```powershell
cd ..   # repository root
. .\gernas-sdk\scripts\load_env.ps1 -Path .\agent-mesh\.env -Prefix GERNAS_,OPIK_
.venv\Scripts\python gernas-sdk\scripts\mcp_probe.py MCPPROBE18A
cd gernas-sdk; ..\.venv\Scripts\python scripts\opik_verify.py --task-id MCPPROBE18A
```

**Expected:** `use_case.request → agent.run → tool.call (client) → tool.call (server, DataLayer process)`, then `RESULT: PASS`. Result on 2026-10-18: PASS.

### E.6 Collector counters

```powershell
curl.exe -s http://127.0.0.1:8888/metrics | Select-String "otelcol_receiver_accepted_spans|otelcol_receiver_refused_spans|otelcol_exporter_sent_spans|otelcol_exporter_send_failed_spans"
```

**Expected:** accepted = sent to `otlphttp/opik` = sent to the data plane; refused = 0; no `send_failed` series. On 2026-10-18: 89 / 89 / 89, 0 refused.

---

## Part F — Tests

| Suite | Command (directory) | Result on 2026-10-18 |
|---|---|---|
| SDK unit, contract and subprocess | `..\.venv\Scripts\python -m pytest tests --cov=gernas` (`gernas-sdk/`) | All pass, coverage ≥ 90 % (gate); see `checklist.md` §10 for the count |
| SDK static | `ruff check gernas tests registry scripts benchmarks examples`; `ruff format --check gernas tests scripts`; `mypy gernas`; `python registry/generate.py --check`; `python scripts/build_architecture_doc.py --check` | clean |
| Agent Mesh offline | `..\.venv\Scripts\python -m pytest tests -q --ignore=tests/test_rbac_matrix.py` (`agent-mesh/`) | 87 passed, 5 failed. These are the pre-existing failures, unrelated to telemetry: `test_rbac_blocks_invalid_role`, 3 cache tests and `test_ingest_wrapper_delegates` |
| RAG | `..\.venv\Scripts\python -m pytest tests -q` (`rag-as-a-service/`) | 26 passed, 1 failed (missing input PDF fixture, pre-existing) |

`tests/test_rbac_matrix.py` hangs: two tests wait for a HITL approval that never comes. Exclude it. `pytest-timeout` is not installed, so `--timeout` is not available.

---

## Part G — AKS deployment (not executed: blocked)

The Collector runs as a Deployment in the `gernas-telemetry` namespace. The full procedure and acceptance table are in [collector-aks-deployment.md](collector-aks-deployment.md) §2–3. In summary:

1. Provision ADX and run `collector/adx/create-tables.kql`.
2. Create a user-assigned managed identity with "Database Ingestor", federated to `system:serviceaccount:gernas-telemetry:gernas-otel-collector`.
3. `kubectl create namespace gernas-telemetry`, then create the `opik-credentials` secret from the secret store.
4. Set `ADX_CLUSTER_URI` in `kustomization.yaml` and the client-id annotation in `k8s/serviceaccount.yaml`.
5. `kubectl apply -k gernas-sdk/collector/` and `kubectl -n gernas-telemetry rollout status deploy/gernas-otel-collector`.
6. Point every producer pod's `GERNAS_OTLP_ENDPOINT` at `http://gernas-otel-collector.gernas-telemetry.svc.cluster.local:4317`.
7. Run the 7 acceptance checks, including `gernas doctor` from an app pod and `opik_verify.py`.

What *was* verified without a cluster:
- `otelcol-contrib validate` on the AKS configuration;
- strict schema validation of the rendered manifests against Kubernetes 1.30 (7/7 valid).

---

## Part H — Troubleshooting

All of these were met during verification.

| Symptom | Cause | Fix |
|---|---|---|
| `Gernas SDK telemetry disabled: Missing required Gernas configuration: GERNAS_USE_CASE_ID, …` in an MCP server | Process started without `GERNAS_*` | Load them with `load_env.ps1` before starting (Part D) |
| `GernasConfigError: … GERNAS_SERVICE_NAME` in your own script | No service name | Set `GERNAS_SERVICE_NAME`, or pass `service_name=` to `init_gernas` / `GernasConfig.from_env` |
| `opik_verify.py`: `KeyError: 'OPIK_API_KEY'` or `[FAIL] no Opik span with gernas.task_id=…` | `OPIK_*` not in the shell; wrong or reused `--prefix`; the export has not arrived yet | Load `OPIK_*`; use a new prefix; increase `--timeout` (Opik ingestion takes seconds) |
| `run_collector.py` exits 2: `otelcol-contrib not found` | Binary missing | Part B |
| Port 4317 already in use | A previous Collector is still running | `Get-NetTCPConnection -LocalPort 4317` → stop that process |
| `agent.run(data_agent / rag_agent)` ERROR with HTTP 404 | `DATA_AGENT_MODEL` / `RAG_AGENT_MODEL` names a model your Groq key cannot use (`qwen/qwen3.6-27b` returned 404) | Set a model available to your key. Telemetry is correct: this shows error recording |
| Q2 blocked at `compliance` | The compliance LLM classified the question as out of scope (fails closed) | Expected behaviour; still a valid trace |
| Duplicate-looking spans | Framework-native tracing switched on | Keep `ENABLE_INSTRUMENTATION=false` and `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`; the DataLayer MCP server sets `GERNAS_SUPPRESSED_SCOPES=fastmcp` itself |
| Spans missing at exit | Process killed before flush | Entrypoints call `gernas_telemetry.shutdown()`; own code should call `handle.shutdown()` |
| `gernas.sdk.dropped_spans` > 0 | Collector unreachable or queue full | `gernas doctor`; check Collector health on `:13133` |

---

## Part I — Cleanup and safe shutdown

1. Stop the client first (`demos`, `run.py` or `api_server.py`). They flush through `shutdown()`.
2. Stop the mesh nodes (Ctrl+C in the `launch_mesh.py` terminal), then the MCP servers.
3. Stop the Collector last, so it can drain its queues.
4. Confirm that nothing is still listening:

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 4317,9000,9100,8015,8016,8017,8018 -ErrorAction SilentlyContinue
   ```

5. Local data-plane files under `gernas-sdk/collector/out/` are git-ignored test output; delete them if needed. Do not delete `collector/bin/`.
