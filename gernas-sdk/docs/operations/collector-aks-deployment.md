# GERNAS telemetry pipeline: Gernas SDK → OTel Collector (AKS) → Azure Data Explorer + Opik

## 1. Architecture

```text
 Agent / application pod (any framework)                 gernas-telemetry namespace (AKS)
 ┌───────────────────────────────────┐   OTLP gRPC :4317  ┌──────────────────────────────────────────┐
 │ app code                           │ ─────────────────▶ │ gernas-otel-collector (Deployment ×2)    │
 │  └─ Gernas SDK (decorators,        │   (Service DNS)    │  memory_limiter                          │
 │     MAF / LangChain / LangGraph    │                    │  attributes/pdpl   (registry denylist)   │
 │     adapters, A2A / ASGI / MCP     │                    │  redaction (deny-by-default allow-list,  │
 │     propagation, PDPL redaction)   │                    │             PII / secret value masks)    │
 │  └─ OTel SDK + OTLP exporter       │                    │  ├─ traces/dataplane: tail_sampling ─────┼──▶ Azure Data Explorer
 └───────────────────────────────────┘                    │  │                                       │     OTELTraces / OTELMetrics /
                                                           │  ├─ metrics/dataplane, logs/dataplane ───┼──▶ OTELLogs (Workload Identity)
                                                           │  └─ traces/opik: sampler → Opik shape ───┼──▶ Opik Cloud (OTLP/HTTP, traces)
                                                           └──────────────────────────────────────────┘
```

| Concern | Decision | Where |
|---|---|---|
| Producer contract | The SDK exports OTLP only, to the Collector (blueprint §1 "exporter abstraction", "centralized collectors"). The SDK has no vendor exporter. | `src/gernas/exporters.py` |
| Data plane | `azuredataexplorer` exporter (otelcol-contrib, Beta for traces, metrics and logs), queued ingestion, retry, sending queue | `collector/otel-collector-config.yaml` |
| Developer trace view | Opik's documented OTLP endpoint `…/opik/api/v1/private/otel`, over HTTP (Opik supports HTTP only, traces only), headers `Authorization`, `Comet-Workspace`, `projectName` | same |
| Vendor field shape | GERNAS keys → GenAI keys for Opik in `transform/opik_shape`. The SDK stays vendor-neutral. | same |
| PDPL (blueprint §2) | The SDK redacts at source; the Collector deletes the generated denylist, drops every non-registry key and masks PII and secret values, on **both** trace pipelines | same + `registry/generate.py` |
| Sampling | Data plane: tail sampling that always keeps errors, policy denials, high-risk content and HITL decisions. Opik: probabilistic. Both default to 100 %. | same |
| Single source of config | `kustomization.yaml` builds the ConfigMap from the exact file that `otelcol-contrib validate` checked | `collector/kustomization.yaml` |
| Auth to ADX | AKS Workload Identity (`use_azure_auth: true` → Azure default credential chain). No ADX secret in the cluster. | `k8s/serviceaccount.yaml`, `k8s/deployment.yaml` |
| Auth to Opik | Kubernetes Secret `opik-credentials`, created out-of-band and never committed | `k8s/deployment.yaml` (`envFrom.secretRef`) |
| Health and diagnostics | `health_check` :13133 (readiness and liveness), self-metrics :8888 (`otelcol_exporter_send_failed_*`, queue size, refused spans), JSON logs | config + Service |
| Resilience | 2 replicas, anti-affinity, PDB `minAvailable: 1`, memory limiter at 80 % of the container limit, `GOMEMLIMIT`, 30 s graceful termination to drain the queues | `k8s/` |
| Network | NetworkPolicy: OTLP only from namespaces labelled `gernas.fab/telemetry-producer=true`; :8888 only from `monitoring`; egress DNS + 443 (ADX, Entra ID, Opik) | `k8s/networkpolicy.yaml` |
| Hardening | non-root (10001), read-only root FS, all capabilities dropped, `seccompProfile: RuntimeDefault`, no SA token automount, no RBAC | `k8s/deployment.yaml` |

Kubernetes metadata: the SDK sets `gernas.pod` from `HOSTNAME` inside Kubernetes, and `gernas.namespace` / `gernas.cluster` / `gernas.region` from `GERNAS_*` env (downward API). The Collector therefore needs no `k8sattributes` processor and no RBAC.

## 2. Deploy to AKS

Prerequisites, done once per environment:

```bash
# 1. ADX: database + tables + ingest permission for the Collector identity
#    run collector/adx/create-tables.kql against the database (default name gernas_telemetry)
# 2. Identity: user-assigned managed identity with ADX "Database Ingestor", federated with
#    system:serviceaccount:gernas-telemetry:gernas-otel-collector (AKS OIDC issuer enabled)
az identity federated-credential create --name gernas-otel-collector \
  --identity-name <mi-name> --resource-group <rg> \
  --issuer "$(az aks show -n <aks> -g <rg> --query oidcIssuerProfile.issuerUrl -o tsv)" \
  --subject system:serviceaccount:gernas-telemetry:gernas-otel-collector
# 3. Namespace + Opik secret (values from the secret store, never from git)
kubectl create namespace gernas-telemetry
kubectl -n gernas-telemetry create secret generic opik-credentials \
  --from-literal=OPIK_API_KEY=... --from-literal=OPIK_WORKSPACE=... --from-literal=OPIK_PROJECT_NAME=...
```

Set the per-environment values with a kustomize overlay or by editing before apply:
- `ADX_CLUSTER_URI` in `kustomization.yaml`;
- the client-id annotation in `k8s/serviceaccount.yaml`.

```bash
kubectl apply -k gernas-sdk/collector/
kubectl -n gernas-telemetry rollout status deploy/gernas-otel-collector
```

Producer configuration (every instrumented app pod):

```yaml
env:
  - {name: GERNAS_OTLP_ENDPOINT, value: "http://gernas-otel-collector.gernas-telemetry.svc.cluster.local:4317"}
  - {name: GERNAS_NAMESPACE, valueFrom: {fieldRef: {fieldPath: metadata.namespace}}}
  - {name: GERNAS_CLUSTER, value: "<aks-cluster-name>"}
  - {name: GERNAS_REGION, value: "<azure-region>"}
  # plus the mandatory identity: GERNAS_SERVICE_NAME, _USE_CASE_ID, _APPLICATION_ID, _BUSINESS_DOMAIN, _ENVIRONMENT
```

Label the app namespace: `kubectl label ns <app-ns> gernas.fab/telemetry-producer=true`.

## 3. Acceptance criteria for the AKS deployment

| # | Check | Command / evidence |
|---|---|---|
| 1 | Pods Ready, 0 restarts | `kubectl -n gernas-telemetry get pods` |
| 2 | Health endpoint | `kubectl -n gernas-telemetry port-forward deploy/gernas-otel-collector 13133` → `curl :13133/` returns "Server available" |
| 3 | SDK → Collector | `gernas doctor` from an app pod: configuration, collector and export all PASS |
| 4 | Collector → ADX | `OTELTraces \| where TraceAttributes["gernas.task_id"] == "<request id>" \| count` > 0; `otelcol_exporter_send_failed_spans{exporter="azuredataexplorer"}` stays at 0 |
| 5 | Collector → Opik | `python scripts/opik_verify.py --task-id <request id>` → RESULT PASS |
| 6 | PDPL | `OTELTraces \| where TraceAttributes has "gen_ai.prompt"` returns 0 rows; `scripts/e2e_pipeline_checks.py` adapted to ADX |
| 7 | Resilience | Delete one pod during load: no client errors; `gernas.sdk.dropped_spans` stays flat |

## 4. Local runs (developer workstation)

`python scripts/run_collector.py` starts the same config with `GERNAS_COLLECTOR_BIND=127.0.0.1`.

- **With `ADX_CLUSTER_URI`** set (and Azure credentials available to the default credential chain, for example `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`), the data plane goes to ADX exactly as in AKS.
- **Without it**, the overlay `collector/local-dataplane-file.yaml` replaces the ADX exporter with JSON files in `collector/out/`. The overlay is clearly labelled and never deployed. Opik export is identical in both modes.

## 5. Verification status (2026-10-17)

| Level | Status |
|---|---|
| Config inspected and validated with otelcol-contrib 0.162.0 | **Done**: `validate` exits 0 with ADX env set; fails fast with "clusterURI config is mandatory" without it |
| Manifests validated | **Done**: strict Kubernetes v1.30 JSON-schema validation of all 7 rendered objects; image tag `0.162.0` exists on Docker Hub |
| Running Collector, SDK → Collector → Opik | **Done (local)**: live Agent Mesh run with 4 requests. Every request verified in Opik by `opik_verify.py` (one trace across 5 processes, parent links resolve, conformance PASS, only Gernas spans). Collector metrics: 660 accepted / 660 sent to Opik / 0 failed. |
| Data-plane path (processors, routing, PDPL) | **Done (local substitute)**: same pipelines with the file exporter; `e2e_pipeline_checks.py` 9/9 PASS |
| Live MCP leg (MAF MCP client → DataLayer fastmcp server) | **Done (local)**: `opik_verify.py --task-id MCPPROBE17D` PASS. One trace; the server `tool.call` carries the caller's run; no fastmcp duplicate span. |
| Re-run after the propagator change (2026-10-17) | **Done (local)**: 4 live mesh requests `LIVE17E000-003` PASS. Collector: 96 accepted = 96 to Opik = 96 to the data plane, 0 refused. |
| Collector → ADX ingestion | **Blocked**: no ADX cluster or credentials available in this environment |
| AKS deployment | **Blocked**: no cluster access (`kubectl` / `az` not available). Use §2 and §3 to close. |
