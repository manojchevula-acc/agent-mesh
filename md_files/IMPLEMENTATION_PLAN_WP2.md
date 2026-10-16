# IMPLEMENTATION_PLAN_BE2.md
## GERNAS POD2 — WP2 · WP3·C · WP3·D (BE2 Track)

> Authoritative implementation roadmap for BE2.
> WP2: OTel Collector & Gateway Pipeline (S0–S7)
> WP3·C: Alerting, SLOs & Runbooks (S8–S9)
> WP3·D: FinOps & Cost Attribution (S9–S11)
>
> **⚠ Critical blocker before starting WP2·C:**
> Data-plane store is NOT yet selected (Risk R1 Critical). Every task in WP2·C and parts of
> WP2·A (routing) are blocked until FAB Architect decides. Do not assume Postgres, ClickHouse,
> Cosmos DB, or any other store. Design schema tasks as store-agnostic DDL where possible and
> flag the open decision explicitly in each affected step.
>
> Status values: `[ ] Planned` · `[→] In progress` · `[✓] Complete` · `[!] Blocked`

---

## Task Overview

| Task ID | Sprint | Block | Description | Days | Status |
|---|---|---|---|---|---|
| WP2-A-01 | S0–S1 | A | Topology design + AKS deployment | 10 | [ ] |
| WP2-A-02 | S0 | A | Receiver configuration | 2 | [ ] |
| WP2-A-03 | S1 | A | Context enrichment processor | 7 | [ ] |
| WP2-A-04 | S1–S2 | A | PII and secret redaction | 10 | [ ] |
| WP2-A-05 | S2 | A | Payload capture policy engine | 4 | [ ] |
| WP2-A-06 | S2 | A | Sampling, aggregation, routing | 6 | [ ] |
| WP2-A-07 | S3 | A | Collector self-observability | 3 | [ ] |
| WP2-A-08 | S3 | A | Config as code, CI, runbook | 4 | [ ] |
| WP2-B-01 | S3–S4 | B | Tier 3 — infrastructure correlation (6 components) | 12 | [ ] |
| WP2-B-02 | S4 | B | Tier 4 — security and delivery correlation (4 components) | 8 | [ ] |
| WP2-B-03 | S4–S5 | B | Coverage matrix + validation (24 components) | 5 | [ ] |
| WP2-C-01 | S4–S5 | C | Destination design per schema (6 signal types) | 12 | [!] |
| WP2-C-02 | S5–S6 | C | Schema, index, partitioning | 6 | [!] |
| WP2-C-03 | S6–S7 | C | Retention tiering | 4 | [!] |
| WP2-C-04 | S7 | C | Access model and RBAC per store | 4 | [!] |
| WP2-C-05 | S5 | C | Ingestion and storage forecast | 4 | [!] |
| WP3-C-01 | S8 | C | SLI and SLO definition | 6 | [ ] |
| WP3-C-02 | S8 | C | Alert rules as code (8 classes) | 8 | [ ] |
| WP3-C-03 | S8–S9 | C | Severity, routing, incidents | 4 | [ ] |
| WP3-C-04 | S9 | C | Alert quality controls | 3 | [ ] |
| WP3-C-05 | S9 | C | Runbooks (12 alert classes) | 9 | [ ] |
| WP3-C-06 | S9 | C | Agentic-specific runbooks (4 failure patterns) | 3 | [ ] |
| WP3-D-01 | S9–S10 | D | Cost model implementation (9 components) | 12 | [ ] |
| WP3-D-02 | S10 | D | Rate cards + PTU amortisation | 8 | [ ] |
| WP3-D-03 | S10 | D | Shared allocation methodology | 5 | [ ] |
| WP3-D-04 | S10 | D | Derived cost measures (12 KPIs) | 6 | [ ] |
| WP3-D-05 | S11 | D | Invoice reconciliation | 6 | [ ] |
| WP3-D-06 | S10–S11 | D | Capacity telemetry and forecast | 8 | [ ] |

---

## WP2·A — Collector and Gateway

---

### WP2-A-01 · Topology Design + AKS Deployment
**Sprint:** S0–S1 · **Days:** 10 · **Block:** A

**Description:** Deciding how many collectors sit where, so one failure does not lose the whole estate's telemetry. Sized from real volume, proven under a load test, spread across availability zones.

**Dependencies:** AKS dev namespace provisioned; FAB platform team available for DR acceptance; OTel Collector contrib image pinned and FAB-approved.

**Affected files:**
- `collector/topology/sidecar-config.yaml`
- `collector/topology/gateway-deployment.yaml`
- `collector/topology/gateway-hpa.yaml`
- `collector/topology/kustomization.yaml`
- `docs/collector-topology.md`

**Steps:**

**Step 1 — Volume sizing from real numbers**
Compute expected load: 50 agents × 40 spans/run × 800 runs/day = 1.6M spans/day = ~19 spans/sec average. Peak assumed 8× = ~150 spans/sec. Document this in `docs/collector-topology.md` so the sizing is traceable to inputs, not guesswork.

**Step 2 — Two-tier topology specification**
Define the two tiers: (a) agent-side sidecar — lightweight OTel Collector as a sidecar container per agent pod; buffers spans locally, never blocks the agent process. (b) Central HA gateway — does the expensive processing (enrichment, redaction, routing). Write the topology spec to `docs/collector-topology.md`.

**Step 3 — Sidecar config (`sidecar-config.yaml`)**
Minimal sidecar config: OTLP receiver (gRPC :4317) → memory_limiter → batch → OTLP exporter (to gateway). `max_queue_size: 2048`, retry on failure, no expensive processing here. Validate with `otelcol validate --config sidecar-config.yaml`.

**Step 4 — Gateway deployment (`gateway-deployment.yaml`)**
3 replicas (one per availability zone), resource requests and limits sized from Step 1: `cpu: 1/2, memory: 2Gi/4Gi`. Anti-affinity rules to spread replicas across zones. Validate config.

**Step 5 — HPA scaling rule (`gateway-hpa.yaml`)**
Scale trigger: `otelcol_exporter_queue_size > 2000`. **Not CPU** — CPU is a lagging indicator of data loss; queue depth is the leading one. Validate the HPA metric is exported by the gateway's self-observability.

**Step 6 — Buffered degradation test**
Simulate gateway unavailability: bring down gateway replicas. Assert: (a) agent spans continue to be emitted without error, (b) sidecar queue fills to `max_queue_size`, (c) after gateway recovers, queue drains and spans arrive in Opik. Document recovery SLA.

**Step 7 — Zone failure simulation**
Cordon one AKS availability zone. Assert: telemetry continues without interruption, dashboard shows no gap in data. Document failure mode and recovery.

**Step 8 — FAB platform acceptance**
Demo the topology to FAB platform team. Get written acceptance of the DR position. Update `docs/collector-topology.md` with accepted DR document reference.

**Verification:**
```bash
otelcol validate --config collector/topology/sidecar-config.yaml
otelcol validate --config collector/topology/gateway-deployment.yaml
kubectl apply --dry-run=client -f collector/topology/
```

**Acceptance criteria (from briefing notes "Done when"):**
> Both tiers are deployed as code in the agreed environment; a zone failure loses no telemetry; the scaling trigger is exercised under a load test; and the recovery position is documented and accepted by FAB platform.

---

### WP2-A-02 · Receiver Configuration
**Sprint:** S0 · **Days:** 2 · **Block:** A

**Description:** Opening the doors the SDK sends to, with TLS, workload identity, and message limits. This is the interface contract between WP1 (SDK) and WP2 (Collector). If these don't agree, nothing in either package can be tested.

**Dependencies:** OIDC issuer URL from FAB Azure AD tenant; TLS certificates for the Collector endpoint; WP1-CORE-01 Step 3 (SDK endpoint config must match).

**Affected files:**
- `collector/base/receivers.yaml`
- `docs/interface-contract.md`

**Steps:**

**Step 1 — gRPC and HTTP OTLP endpoints with TLS**
Configure both entry points: gRPC `:4317` and HTTP `:4318`. TLS termination at the Collector (cert from Azure Key Vault or Kubernetes Secret). `max_recv_msg_size_mib: 8` on gRPC to cap oversized payloads. Validate config. Confirm the matching SDK env var: `GERNAS_OTLP_ENDPOINT=collector.gernas.fab:4317`.

**Step 2 — Workload identity authentication**
Add OIDC extension: only registered GERNAS workloads can write. Audience: `gernas-telemetry`. Any unauthenticated write attempt returns HTTP 401 / gRPC UNAUTHENTICATED. Test: unauthenticated test client receives a rejection. Test: SDK with correct workload identity token succeeds.

**Step 3 — Message size cap and concurrency limits**
Test: single oversized message (> 8 MiB) is rejected with a clear error. Assert: all other senders continue unaffected during and after the rejection. Test: 100 concurrent senders with valid payloads all succeed.

**Step 4 — Interface contract document**
Write `docs/interface-contract.md`: protocol (gRPC/HTTP), ports, TLS requirements, auth mechanism, size limits, expected SDK env vars. This is the handshake document between WP1 and WP2. Share with BE1 before S1 SDK work begins.

**Verification:**
```bash
otelcol validate --config collector/base/receivers.yaml
# Manual: curl unauthenticated → expect 401
# Manual: send oversized payload → expect rejection
```

**Acceptance criteria:**
> Both entry points accept traffic from the SDK over an encrypted connection with workload identity; an unauthenticated write is refused; and one oversized message is rejected without affecting other senders.

---

### WP2-A-03 · Context Enrichment Processor
**Sprint:** S1 · **Days:** 7 · **Block:** A

**Description:** Filling in the context an agent should not have to know — its owner, business domain, risk tier, cost centre, and certification status. Added centrally from the GERNAS OS registry so 50 agents do not each have to maintain this themselves.

**Dependencies:** GERNAS OS registry API accessible from the Collector's AKS namespace; WP1-CTX-01 attribute names finalised (the enriched field names must match the registry).

**Affected files:**
- `collector/processors/gernas_enrichment.py` (custom processor, if using OTel Collector contrib transform processor this may be YAML config instead)
- `collector/base/processors.yaml`
- `tests/test_enrichment_processor.py`

**Steps:**

**Step 1 — Registry lookup processor**
Implement or configure a processor that, for each span, reads `gernas.agent_id` and looks up the agent in the GERNAS OS registry. Returns: `business_domain`, `application_id`, `agent_owner`, `risk_tier`, `cost_centre`, `certification` status.

**Step 2 — In-memory cache**
Cache lookup results with `cache_ttl: 5m`. A cache miss triggers a live registry lookup; a cache hit adds zero latency. Cache is per-Collector-instance (not shared), which is correct — each gateway replica can have its own cache.

**Step 3 — Tag-and-pass on miss**
If registry is unreachable or the agent_id is not registered: set `gernas.registry_miss = true` and let the span through. **Never drop on miss.** An unregistered agent running in production is itself a finding — its telemetry must stay visible.

**Step 4 — Governance alert on miss**
Configure an alert: if `gernas.registry_miss = true` spans appear in production, raise a governance notification. An unregistered production agent is a policy violation, not a missing config item.

**Step 5 — Registry outage test**
Take the GERNAS OS registry offline. Send spans through the Collector. Assert: (a) spans arrive at Opik with `gernas.registry_miss = true`, (b) no spans are dropped, (c) enriched fields are absent (not set to null/empty — omitted entirely).

**Step 6 — Cache staleness test**
Register an agent; allow cache to populate; change the agent's `risk_tier` in the registry; wait for TTL expiry; send a span. Assert: enriched span reflects the updated risk_tier after TTL expiry.

**Step 7 — Performance: enrichment adds < 2ms p95**
Run 10,000 spans through the enrichment processor with a warm cache. Assert p95 added latency < 2ms. With a cold cache (first lookup), accept up to 50ms but assert it does not affect p99 of subsequent spans.

**Verification:**
```bash
otelcol validate --config collector/base/processors.yaml
pytest tests/test_enrichment_processor.py -v
```

**Acceptance criteria:**
> All six derived fields are attached centrally with no agent-side lookup; a registry outage degrades to tagged pass-through rather than dropped data; and an unregistered agent raises a governance alert.

---

### WP2-A-04 · PII and Secret Redaction
**Sprint:** S1–S2 · **Days:** 10 · **Block:** A

**Description:** Enforcing the WP1-CTX-05 sensitivity matrix at the one point every signal passes through. Half the effort is proving the rules work — a redaction rule nobody has measured is a claim, not a control.

**Dependencies:** WP1-CTX-05 redaction matrix signed (at minimum a draft from CISO for S1 work; formal sign-off needed before Collector goes to staging at S3). This is Risk R3.

**Affected files:**
- `collector/processors/redaction.yaml`
- `collector/policy/redaction-rules.yaml` (CODEOWNERS: CISO)
- `tests/redaction/labelled-corpus/`
- `tests/test_redaction.py`
- `reports/redaction-evidence.md`

**Steps:**

**Step 1 — Per-field redaction rules from the matrix**
For each field in the WP1-CTX-05 matrix, implement the assigned action using the OTel Collector `transform` processor or `redaction` processor:
- `keep` → no action
- `fingerprint` → SHA-256 hash of value, replace original
- `mask` → replace with `[MASKED]`
- `tokenise` → replace with a reversible token (stored separately)
- `drop` → `delete_key(attributes, "field_name")`

**Step 2 — Deny by default**
`allow_all_keys: false`. Any attribute not in the registered allow-list is dropped, not published. A team adding a new field next quarter is blocked until they register it — it does not leak by accident. Test: send a span with an unregistered attribute; assert it is absent from the output.

**Step 3 — Pattern detection for free-text PII**
Configure `blocked_values` patterns:
- Emirates ID: `784-[0-9]{4}-[0-9]{7}-[0-9]`
- IBAN (UAE): `AE[0-9]{2}[0-9]{19}`
- Card numbers (Luhn-passing, 13–19 digits): `[0-9]{13,19}`
- API keys and bearer tokens: common patterns

**Step 4 — Labelled test corpus**
Build `tests/redaction/labelled-corpus/`: a set of span payloads with known PII values, labelled with what should and should not be redacted. Minimum 100 examples per pattern class. This is the evidence CISO reviews — not a code review.

**Step 5 — Recall and false-positive measurement**
Run the corpus through the redaction pipeline. Record:
- Emirates ID: recall ≥ 99.7%, false positive < 1%
- IBAN: recall ≥ 100%, false positive < 0.5%
- Card numbers: recall ≥ 99.5%, false positive < 1%
- Free-text PII (names, emails in tool outputs): recall ≥ 94%, false positive < 3%
- Secrets/keys: recall ≥ 100%, false positive < 0.5%

Publish results to `reports/redaction-evidence.md`.

**Step 6 — CISO evidence pack**
Format `reports/redaction-evidence.md` in the form CISO and Data Governance actually review: per-pattern table of recall/FP rates, methodology, corpus description, and sign-off block. This is the document that unblocks Collector production promotion (S7 gate).

**Step 7 — Deny-by-default end-to-end test**
Deploy a test agent that emits a new, unregistered attribute. Assert: attribute is absent from Opik; absent from any data plane store; `redaction.applied = true` is set on the span.

**Verification:**
```bash
otelcol validate --config collector/processors/redaction.yaml
pytest tests/test_redaction.py -v --tb=short
# Check recall/FP rates in output
```

**Acceptance criteria:**
> Every row of the WP1-CTX-05 matrix has an implemented rule; catch rates and false-positive rates are published per pattern; CISO and Data Governance have accepted the evidence pack; and an unregistered field reaches no store.

---

### WP2-A-05 · Payload Capture Policy Engine
**Sprint:** S2 · **Days:** 4 · **Block:** A

**Description:** The controlled exception — how questions and answers get captured when evaluation genuinely needs them, without becoming a permanent gap in the redaction control.

**Affected files:**
- `collector/policy/payload-capture-grants.yaml`
- `collector/processors/payload-capture.yaml`
- `tests/test_payload_capture.py`

**Steps:**

**Step 1 — Grant schema**
Define a grant as a YAML record with required fields: `use_case_id`, `fields[]` (only `prompt` and `completion` permitted), `purpose`, `approver` (named individual), `approved_on`, `expires_on`, `destination` (restricted_store_only), `retention`. No optional fields — every field is mandatory. A grant missing any field is invalid.

**Step 2 — Enforced expiry without redeployment**
The Collector reads `expires_on` at processing time, not at startup. An expired grant stops capture immediately — no redeploy required. Test: create a grant with `expires_on = now() + 10s`; wait 15s; send a span; assert raw payload is NOT captured.

**Step 3 — Restricted routing**
Captured content routes to `restricted_store_only` pipeline, never to the standard pipeline or logs. Test: captured span payload appears in restricted store; does NOT appear in Opik or general trace store.

**Step 4 — Capture as telemetry**
When capture is active, set on the span: `gernas.payload_captured = true`, `gernas.capture_grant_id = PCG-XXXX`. When a grant lapses, set: `gernas.capture_grant_lapsed = true`. Both are evidence records, not just flags.

**Step 5 — Global default test**
Without any active grant: send a span with raw prompt and completion. Assert both are absent from all stores. Capture is off by default; grants are exceptions, not the default path.

**Acceptance criteria:**
> Capture is off globally and on only by a grant carrying approver, purpose, destination and expiry; an expired grant stops capture without a deployment; and captured content appears only in the restricted store.

---

### WP2-A-06 · Sampling, Aggregation, Routing
**Sprint:** S2 · **Days:** 6 · **Block:** A

**Description:** Deciding what is kept in full, what is summarised, and where each kind of signal goes. Tail-based sampling — waiting for the run outcome before deciding — is the decision that costs the days and produces data worth keeping.

**Dependencies:** WP2-A-02 (routing destinations must be known); data-plane store selection (R1) affects routing targets — use placeholder names if store is not yet selected.

**Affected files:**
- `collector/processors/tail-sampling.yaml`
- `collector/processors/routing.yaml`
- `tests/test_sampling.py`

**Steps:**

**Step 1 — Tail-based sampling policy**
`decision_wait: 30s` — the Collector holds span data for 30 seconds to let the run complete before making the keep/discard decision. This requires the `tail_sampling` processor from OTel Collector contrib. Verify the contrib image includes it.

**Step 2 — Always-keep rules (non-negotiable, never sampled away)**
Four always-keep policies:
```yaml
- name: always_errors
  type: status_code
  status_code: { status_codes: [ERROR] }
- name: always_high_risk
  type: string_attribute
  key: gernas.risk_tier
  values: ['3', '4']
- name: always_policy_block
  type: string_attribute
  key: gernas.policy_decision
  values: ['deny']
- name: always_hitl
  type: string_attribute
  key: gernas.hitl.outcome
  values: ['approved', 'rejected', 'timed_out', 'changed']
```

**Step 3 — Baseline success sampling**
Successful runs that cleared all always-keep rules: probabilistic sampling at `baseline_success_rate` (from env var `GERNAS_BASELINE_SAMPLE_RATE`, default `0.15`). The rate is configurable without redeploying agents — only the Collector config changes.

**Step 4 — Test: no error span sampled away**
Run 10,000 error spans through the sampling pipeline. Assert: 10,000/10,000 in the output. Run 10,000 success spans. Assert: approximately 1,500 in the output (within ±5% of the 15% rate).

**Step 5 — Test: baseline rate change without agent redeploy**
Change `GERNAS_BASELINE_SAMPLE_RATE` from 0.15 to 0.25 via Collector config update. Redeploy only the Collector. Assert: agents emit no errors, no restarts, and the new rate applies immediately.

**Step 6 — Signal routing**
Each signal class to its designated destination only:
- Traces → Opik + data plane traces schema
- Metrics → data plane metrics schema
- Logs → data plane logs schema
- Cost facts → data plane cost schema
- Audit events → data plane audit schema (durable writes, different pipeline)

Test: a metric counter appears in the metrics schema and does NOT appear in the traces schema. A log record appears in logs only.

**Step 7 — Routing placeholder for undecided store (R1)**
If the data-plane store is not yet selected: configure routing to a logging exporter as a placeholder. Log a warning: `⚠ DATA PLANE STORE NOT SELECTED — routing to placeholder exporter`. Do not block WP2·A completion on R1; isolate the dependency in the routing config.

**Acceptance criteria:**
> No error, policy block, human approval or high-risk run is ever sampled away; baseline sampling rate can be changed without redeploying agents; and each signal class reaches its designated store and nowhere else.

---

### WP2-A-07 · Collector Self-Observability
**Sprint:** S3 · **Days:** 3 · **Block:** A

**Description:** Making the pipeline report its own health so silent telemetry loss becomes impossible. A Collector cannot be trusted to report that it has stopped working using itself — these metrics go to a separate destination.

**Affected files:**
- `collector/base/self-observability.yaml`
- `collector/base/alerts/telemetry-loss.yaml`
- `tests/test_self_observability.py`

**Steps:**

**Step 1 — Pipeline health metrics**
Enable Collector's built-in metrics on `:8888`. Confirm these metrics are exported:
- `otelcol_receiver_refused_spans`
- `otelcol_processor_dropped_spans`
- `otelcol_exporter_send_failed_spans`
- `otelcol_exporter_queue_size`

Add GERNAS-specific labelled drop counters:
- `gernas.dropped{reason=redaction_deny}`
- `gernas.dropped{reason=cardinality_limit}`
- `gernas.dropped{reason=registry_miss}`
- `gernas.dropped{reason=queue_full}`

**Step 2 — Separate export destination**
Configure self-observability metrics to export to a **different** Prometheus instance or monitoring backend, not the same destination the Collector is serving. If the Collector fails, its self-metrics must still be reachable.

**Step 3 — Loss alert**
```yaml
alert: TelemetryLoss
expr: rate(gernas_dropped_total[5m]) > 0
for: 10m
severity: P2
annotations:
  summary: "Telemetry pipeline dropping spans — check reason label"
```
This alert fires within 10 minutes of any drop. A drop rate above zero is always worth investigating. Test: manually cause a redaction_deny drop; assert alert fires within 10 minutes.

**Step 4 — Store freshness alerts (one per destination)**
```yaml
alert: StoreStale
expr: time() - max(last_write_timestamp) by (store) > 300
severity: P2
```
Fires when writes to any destination stop for > 5 minutes. Test: stop the exporter for one destination; assert the freshness alert fires for that destination and not the others.

**Acceptance criteria:**
> Dropped records are counted and labelled by reason; a loss rate above zero raises an alert within ten minutes; and each destination has a freshness alert that fires when writes stop.

---

### WP2-A-08 · Config as Code, CI, Runbook
**Sprint:** S3 · **Days:** 4 · **Block:** A

**Description:** The full Collector configuration in GitLab so a change to a governed control (redaction rule, sampling policy) goes through review rather than being made by hand on a cluster.

**Affected files:**
- `collector/` (full repo structure with overlays)
- `.gitlab-ci.yml` (Collector pipeline steps)
- `docs/collector-runbook.md`

**Steps:**

**Step 1 — GitLab repo structure with kustomize overlays**
```
collector/
  base/           ← receivers, processors, exporters
  overlays/dev/   ← sampling 100%, capture allowed for dev, logging exporter
  overlays/staging/ ← sampling per policy, capture denied, staging destinations
  overlays/prod/  ← sampling per policy, capture denied, production destinations
  policy/         ← redaction rules (CODEOWNERS: CISO)
```
CODEOWNERS: `collector/policy/ @ciso-team` — redaction rules cannot merge without CISO review.

**Step 2 — CI pipeline for Collector config**
Three mandatory CI steps before any config reaches a cluster:
1. `otelcol validate --config rendered.yaml` — schema validation
2. `gernas redaction-test --corpus tests/redaction/labelled-corpus/` — recall/FP gates
3. `gernas policy-diff --against prod` — show what will change, block if requires platform approval

**Step 3 — Promotion path (dev → staging → prod by pipeline only)**
No manual `kubectl apply` in staging or production. Overlay rendering + `kubectl apply` happen in the pipeline only. Staging promotion requires the CI pipeline to pass. Prod promotion requires an additional manual approval step in GitLab.

**Step 4 — Operator runbook**
`docs/collector-runbook.md` — written for failures an on-call engineer will actually encounter:
- Queue filling up → steps to diagnose (check receiver rate vs exporter rate) → scale gateway, then throttle sidecar
- Export failing to Opik → check egress network policy + OTLP API key
- Drop count rising → check `gernas.dropped{reason=...}` label — each reason has a distinct fix
- Registry miss spike → GERNAS OS registry unreachable — check registry health endpoint

**Step 5 — Blueprint IP commitment test**
Confirm: all configuration is in FAB-owned GitLab repo, not in a fork owned by Accenture. Run a diff between the FAB repo and any Accenture working copy — no unsynced changes.

**Acceptance criteria:**
> The full configuration is in GitLab with automated validation and named reviewers on policy files; a change reaches production by pipeline only; and the runbook covers queue, export and drop failures.

---

## WP2·B — Tier 3–4 Component Correlation

---

### WP2-B-01 · Tier 3 — Infrastructure Correlation
**Sprint:** S3–S4 · **Days:** 12 · **Block:** B

**Description:** Joining the Azure signals that already exist to the agent run that caused them. This is label plumbing in the Collector — no infrastructure code changes required. The BE1 track (WP1-T3-01 through WP1-T3-05) handles SDK-level instrumentation; this task handles the Collector-side identity injection.

**Rate:** 6 infrastructure components × 2 days each = 12 days.

**Pattern for each component (2 steps):**

**Step 1 — Resource attribute injection via k8sattributes processor**
Configure `k8sattributes` processor to read pod labels (`gernas.agent_id`, `gernas.use_case_id`, `gernas.environment`) and inject them as resource attributes on all signals from that pod. No code changes to the infrastructure component.

**Step 2 — Attribution test**
Run a query on the target signal grouped by `gernas.use_case_id`. Assert: the grouping returns distinct values (not all-null). This proves the identity is attached and queryable.

**Components (apply standard 2-step pattern):**

| Task | Component | Sprint | Azure signal source | k8s label source |
|---|---|---|---|---|
| WP2-B-01a | AKS/Containers | S3 | `container_cpu_usage`, `container_memory_usage` | pod labels |
| WP2-B-01b | GPU Infrastructure | S3 | DCGM exporter metrics | pod/node labels |
| WP2-B-01c | Messaging (Service Bus) | S4 | Azure Monitor → Collector receiver | namespace + pod labels |
| WP2-B-01d | Network | S4 | Azure Network Watcher → Collector | pod labels |
| WP2-B-01e | Database | S4 | SQLAlchemy OTel spans already carry context via WP1-T3-01 | n/a — context from SDK |
| WP2-B-01f | API Gateway | S4 | Azure API Management logs → Collector | subscription/backend labels |

**Acceptance criteria:**
> Each of the six infrastructure signal sets carries agent and use-case identity; container, GPU and queue measurements can be grouped by use case in a query; and no infrastructure component needed a code change.

---

### WP2-B-02 · Tier 4 — Security and Delivery Correlation
**Sprint:** S4 · **Days:** 8 · **Block:** B

**Description:** Joining identity, authorisation, release and pipeline-health events to the agent that triggered them. The four components each need access agreement with their system owner before instrumentation begins — agree access first, implement second.

**Rate:** 4 components × 2 days each = 8 days.

**Components:**

**WP2-B-02a: Identity/AAD (2d)**
- Access needed: Azure Monitor Logs Receiver permission to read AAD audit logs
- Signals to correlate: authentication events, token issuance, token rejection — linked to `gernas.agent_id` via the workload identity principal
- Step 1: Configure Azure Monitor Logs Receiver with AAD log table access
- Step 2: Map `principalId` → `gernas.agent_id` via the registry enrichment processor

**WP2-B-02b: Authorization/OPA (2d)**
- Access needed: OPA audit log OTLP export (check OPA version in FAB; not all versions support it)
- Signals to correlate: policy decisions (`allow`/`deny`), policy ID, resource, principal
- Step 1: Configure OPA to emit OTLP audit logs to Collector
- Step 2: Test: a deny decision appears with `gernas.agent_id`, `gernas.policy_id`, `gernas.policy_decision=deny`

**WP2-B-02c: CI/CD/GitLab (2d)**
- Access needed: GitLab webhook → OTLP receiver (or GitLab CI pipeline → OTLP exporter)
- Signals to correlate: `deployment_version`, `release_id`, `commit_sha`, `rollback` flag — linked to `gernas.agent_version`
- Step 1: Configure GitLab webhook to emit deployment events as OTLP spans
- Step 2: Test: `gernas.agent_version = 2.3` in a span resolves to `gernas.commit_sha = 4a9de17` via the CI/CD event

**WP2-B-02d: Telemetry Platform (2d)**
- Self-referential: the Collector's own health surfaced as a tracked component
- Signals: `otelcol_receiver_refused_spans`, `otelcol_processor_dropped_spans`, per-reason drop counters
- Step 1: Expose Collector self-metrics as a named component in the coverage register
- Step 2: Verify Collector health appears on Dashboard 2 (SRE) — coordinate with UI track

**Acceptance criteria:**
> Authentication, authorisation, deployment and pipeline-health events all carry agent identity; Policy Block Rate can be calculated; and a running agent version resolves to the release and commit that produced it.

---

### WP2-B-03 · Coverage Matrix + Validation
**Sprint:** S4–S5 · **Days:** 5 · **Block:** B

**Description:** The single document that says, per component, whether telemetry actually works — with a query that proves it rather than a human assertion.

**Affected files:**
- `coverage-register.yaml`
- `tests/test_coverage_validation.py`

**Steps:**

**Step 1 — Coverage register YAML (24 rows)**
```yaml
components:
  - name: Use Case Layer
    tier: 1
    owner: "DAI / KYC squad"
    required_signals: 5
    actual_signals: 0
    status: not_started
    validation_query: "count(spans) where name='use_case.request' and gernas.use_case_id != '' last 24h"
    expected: "> 0"
  # ... repeat for all 24 components
  - name: Knowledge Layer
    tier: 2
    owner: unassigned
    status: NOT_COSTED
    notes: "No line in estimate. ~4 days if FAB confirms scope. Raise before starting."
```

**Step 2 — Validation query per component (24 queries)**
Each component's `validation_query` runs against the data plane. Returns a count > 0 if the component is genuinely instrumented and flowing. Status is set by the query result, not by a person claiming it's done.

**Step 3 — CI integration**
Add a daily CI job that runs all 24 validation queries and updates `coverage-register.yaml` with actual status. Fails the CI pipeline if any component that was `done` drops below the expected threshold (regression detection).

**Step 4 — Honest unknowns**
Knowledge Layer must appear with `status: NOT_COSTED` and an explicit note. 24 components exist in the Blueprint; the register shows all 24 regardless of costing status.

**Step 5 — Handover as live record**
The final `coverage-register.yaml` is the artefact handed to FAB at project close — not a snapshot PDF. It must be in FAB-owned GitLab and CI-maintained.

**Acceptance criteria:**
> All twenty-four components have a row with owner, required signal count and status, including Knowledge Layer at not-costed; each row's validation query sets its status; and the matrix is handed over as FAB's live coverage record.

---

## WP2·C — Data Plane, Retention and Access

> **⚠ Risk R1 applies to ALL tasks in this block.**
> Data-plane store is not selected. Do not implement any task that requires a specific store's DDL, query syntax, or client library until the selection is confirmed.
> Where possible, produce store-agnostic design artefacts (schema design docs, access model docs) and flag the implementation steps that are store-specific.

---

### WP2-C-01 · Destination Design Per Schema
**Sprint:** S4–S5 · **Days:** 12 · **Block:** C

**Description:** Designing the six data sets the Blueprint's data plane needs. Each needs its own shape, retention position, query pattern, and ingestion path — and the ingestion path (how signals get from the Collector into the store) is the most complex piece.

**Rate:** 6 signal types × 2 days each = 12 days.

**⚠ Open item flagged in briefing notes:** Opik accepts OTLP natively. Most stores do not. The write path into the data plane (transform → batch → backpressure → dead-letter) has no line in the original estimate and must be built. This is the item most likely to be discovered late.

**Steps per signal type (apply to each of 6):**

**Step 1 — Schema design document**
For each signal type, define: field names (from attribute registry), data types, which fields are indexed, time partitioning column, primary access pattern (the query the dashboard will actually run), rollup requirement (yes/no), retention period, and write guarantee required (eventual = traces/metrics; durable/ordered = audit).

**Step 2 — Write path design**
For the chosen store (TBD): how do signals get from the OTel Collector to this schema? For Postgres: no native OTLP receiver → custom exporter component needed (transform, batch, backpressure, dead-letter queue). For ClickHouse: OTLP receiver plugin exists. For Azure Monitor: Log Ingestion API. Document the write path, including failure handling.

**Signal types and characteristics:**

| Schema | Retention | Rollups | Write guarantee | Primary query pattern |
|---|---|---|---|---|
| 1: traces | 90d (standard) / 13m (high-risk) | Yes | Eventual | `runs by use_case, agent, version` |
| 2: agent_execution | 13–25m | Yes | Eventual | `runs by use_case, agent, version, outcome` |
| 3: metrics | 13m | Yes | Eventual | `sum(tokens) by model, use_case, day` |
| 4: operational_logs | 30–90d | No | Eventual | `logs by agent, level, time_range` |
| 5: cost_capacity | 25m | Yes | Eventual | `cost per successful task by use_case` |
| 6: audit_security | Statutory | No | **Durable + ordered** | `every action on case_id in order` |

**Step 3 — Trace duplication design**
Document what differs between the Opik copy (developer inspection, shorter retention) and the data-plane copy (dashboards, longer retention, full schema). They are not interchangeable and must not be assumed to be.

**Acceptance criteria:**
> Each of the six has an agreed shape, retention position and primary query pattern; the write path into the store is specified; and the difference between the Opik copy and the data-plane record is documented.

---

### WP2-C-02 · Schema, Index, Partitioning
**Sprint:** S5–S6 · **Days:** 6 · **Block:** C

**⚠ Blocked on R1.** Implement against the selected store only. If store is not selected by S5, escalate — this is a hard gate for dashboard build in S5.

**Description:** Laying the data out so dashboards are fast. At ~580M spans/year, executive views over 30 days will not meet a 2-second SLO against raw data. Pre-aggregated rollup tables are a hard requirement, not an optimisation.

**Steps:**

**Step 1 — Access-pattern-driven schema**
The drill-down path on each dashboard is the access pattern: `use_case → agent → version → run → step → span`. Design the schema backwards from this path. Designing the schema first and fitting the queries to it produces correct-but-slow data.

**Step 2 — Time partitioning**
Partition all tables by time column (day partitions for traces and execution data). Retention expiry = drop the partition. Never `DELETE` rows at scale — partition drops are orders of magnitude cheaper and faster.

**Step 3 — Rollup tables (mandatory for 2-second SLO)**
Minimum rollup tables needed:
```sql
-- agent_runs_daily: the table dashboards actually query
CREATE MATERIALIZED VIEW agent_runs_daily AS
SELECT
  date_trunc('day', started_at) as day,
  use_case_id, agent_id, agent_version, model_id,
  count(*) as runs,
  count(*) FILTER (WHERE business_outcome = 'success') as success,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) as p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) as p99,
  sum(llm_cost_aed) as llm_cost_aed,
  sum(total_cost_aed) as total_cost_aed
GROUP BY 1, 2, 3, 4, 5;
-- Refresh every 15 minutes, incrementally
```

**Step 4 — Join key indexes**
Index only the attributes actually used in joins: `agent_run_id`, `use_case_id`, `agent_id`, `session_id`. Do not index unbounded cardinality fields (user_id, trace_id) in rollup tables — strip them before rollup.

**Step 5 — SLO verification at synthetic volume**
Load 580M rows of synthetic span data (or use statistical sampling to simulate it). Run each dashboard's primary query. Assert all return within their SLO:
- Executive rollup (30d): < 2s
- Agent drill-down (7d): < 3s
- Single run reconstruction: < 1s

**Acceptance criteria:**
> Each data set is partitioned by time with expiry expressed as partition drops; summary tables exist at the levels the dashboards query and refresh incrementally; and the stated response targets are met against a year of simulated volume.

---

### WP2-C-03 · Retention Tiering
**Sprint:** S6–S7 · **Days:** 4 · **Block:** C

**⚠ Blocked on R1 and on Data Governance availability** — the policy half requires Data Governance in the room and cannot be compressed by adding engineers.

**Steps:**

**Step 1 — Retention policy per signal class × risk tier**
Document the matrix (needs Data Governance sign-off before implementation):

| Signal class | Tier 0–2 | Tier 3–4 (regulated) |
|---|---|---|
| Operational logs | 30d | 90d |
| Traces (data plane) | 90d | 13 months |
| Traces (Opik copy) | 30d | 90d |
| Agent execution | 13 months | 25 months |
| Metrics (rolled up) | 13 months | 13 months |
| Cost & capacity | 25 months | 25 months |
| Audit & provenance | Statutory | Statutory |
| Captured payloads | — | Grant period only |

**Step 2 — Enforced expiry (partition drops, not DELETE)**
Schedule partition drops on a job (cron or stored procedure). Logs that the drop occurred (who, when, what partition). Test: partition older than the retention period is absent after the scheduled job runs.

**Step 3 — Legal hold mechanism**
A legal hold suspends expiry for a named case. Implementation: a `legal_holds` table; the partition drop job checks this table before dropping. Audit partitions have no drop rights on the job's role — enforced at DB level, not application level.

**Step 4 — Data Governance sign-off document**
Produce the retention policy as a formal document for Data Governance review. Not a code review — a policy document showing periods, risk-tier multipliers, and enforcement mechanism.

**Acceptance criteria:**
> Every signal class has a retention period by risk tier approved by Data Governance; expiry is enforced by the store itself; and a legal hold suspends expiry while deletion produces an evidence record.

---

### WP2-C-04 · Access Model and RBAC Per Store
**Sprint:** S7 · **Days:** 4 · **Block:** C

**⚠ Agreement half is with Data Governance and sets the pace — this is the part that cannot be compressed.**

**Steps:**

**Step 1 — Role model per store**
Define who can see what across all six stores. Minimum roles required:

| Role | Logs | Traces | Exec | Metrics | Cost | Audit |
|---|---|---|---|---|---|---|
| SRE | RW | R | R | R | — | — |
| Agent owner | — | R* | R* | R* | R* | — |
| MRM | — | R | R | R | — | R |
| Internal audit | — | R | R | — | — | R |
| Finance | — | — | — | R | R | — |
| Executive | — | — | — | R+ | R+ | — |
| Accenture pod | RW | RW | RW | RW | R | — |

`R*` = scoped to own use cases only. `R+` = aggregate views only, no record-level access.

**Step 2 — Database-level enforcement**
Implement as database roles + row-level security (RLS) where supported by the chosen store. A filter applied only in the dashboard API is not a control — it fails when someone connects a BI tool directly. RLS must hold for any client.

**Step 3 — Segregation of duties**
No single role holds both `audit:read` AND `retention:write`. Enforced at DB level, not application level. Test: attempt to grant both roles to the same principal → DB rejects it.

**Step 4 — Restricted payload store isolation**
Captured payload data lives in its own schema or database. Accessible only with an active capture grant. Every read of the restricted store is logged to the audit table (who, when, what query).

**Step 5 — Data Governance sign-off matrix**
Produce the role-permission matrix as a formal document. Data Governance must sign the matrix before any dashboard is built against these stores — RBAC enforcement in the dashboards (WP3-B, UI track) depends on this being settled.

**Acceptance criteria:**
> Each store has an explicit role grant matrix accepted by Data Governance; no role holds both audit read and retention write; restricted access requires a live grant; and reads of the restricted store are logged.

---

### WP2-C-05 · Ingestion and Storage Forecast
**Sprint:** S5 · **Days:** 4 · **Block:** C

**Description:** Forecasting what the data plane costs to run, and which dials change the answer. The output is a decision aid, not a number to put in a budget line.

**⚠ Cost shape changed when platform changed:** this is provisioned infrastructure (vCPU, storage IOPS, backup), not a per-GB SaaS charge. The sensitivity analysis must reflect the actual platform cost model.

**Steps:**

**Step 1 — Volume model from countable inputs**
```
50 agents × 800 runs/day × 40 spans/run × 2.1 KB/span = 3.4 GB/day traces
+ 0.9 GB/day metrics
+ 2.6 GB/day logs
= ~6.9 GB/day (~2.5 TB/year, unsampled)
+ Opik trace copy (30–90d retention)
```
Document sources for every input number.

**Step 2 — Platform cost model for chosen store**
Once R1 is resolved, model: storage GB, compute (vCPU/hour), IOPS, backup, network egress. For the Opik copy: Opik pricing (SaaS or self-hosted). Not a per-GB SaaS model.

**Step 3 — Rollup table sizing**
Rollup tables are sized by number of dimensions × number of days, not by span volume. This means:
- Sampling the raw data harder has almost no effect on rollup cost
- Growth from 27 to 50 agents increases raw volume +85%, but rollup volume is flat

Document this clearly — it is the main argument for aggressive raw-data sampling.

**Step 4 — Sensitivity analysis (the actual deliverable)**

| Sampling | Retention | Raw store | Rollup store |
|---|---|---|---|
| 100% | 13 months | 2.5 TB | 0.3 TB |
| 25% | 13 months | 0.9 TB | 0.3 TB |
| 15% | 13 months | 0.7 TB | 0.3 TB |
| 15% | 90d traces | 0.3 TB | 0.3 TB |

Always-keep rules excluded from every scenario (they are mandatory regardless of cost).

**Acceptance criteria:**
> A volume model derived from agent count and run rate; storage and compute cost for the chosen platform; a sensitivity table across sampling and retention options; and the always-keep rules excluded from every scenario.

---

## WP3·C — Alerting, SLOs and Runbooks (BE2-owned)

---

### WP3-C-01 · SLI and SLO Definition
**Sprint:** S8 · **Days:** 6

**Description:** Deciding what "working properly" means numerically so alerts fire on real degradation rather than noise. The targets take an afternoon; getting service owners to agree to them is the rest.

**Steps:**

**Step 1 — Service tier classification**
Classify all agents and platform services into tiers:
- Tier 1 (critical): customer-facing agents, GERNAS OS core, LLM Gateway
- Tier 2 (important): agent runtime, evaluation pipeline, RAG search
- Tier 3 (supporting): monitoring infrastructure, non-customer-facing agents

Tiers set the SLO targets — critical services get tighter targets and smaller error budgets.

**Step 2 — SLIs from actual telemetry**
Define each SLI as a computable expression from real telemetry that will exist after WP1·C instrumentation:
- Availability: `count(runs where status != error) / count(runs)`
- Latency: `percentile(run_duration_ms, 0.95) < threshold`
- Quality: `count(runs where business_outcome = 'success') / count(runs)`

Do not define SLIs against signals that don't exist yet.

**Step 3 — Targets and windows (agreed with service owners)**
Propose targets, then agree them with the named service owner. Not negotiable by POD 2 alone. Document the agreed targets in `docs/slo-definitions.yaml`.

Example for Tier 1:
```yaml
service: gernas-agent-platform
tier: 1
slis:
  availability:
    expr: "count(agent_runs{status='success'}) / count(agent_runs)"
    target: 0.995
    window: 28d
  p95_latency:
    expr: "p95(agent_run_duration_ms)"
    target_ms: 20000
    window: 28d
  quality:
    expr: "count(agent_runs{business_outcome='success'}) / count(agent_runs)"
    target: 0.97
    window: 28d
error_budget:
  availability: "3h 22m of failure allowed per 28d"
  policy:
    at_85_percent: "change freeze except fixes"
    at_100_percent: "incident review before any release"
```

**Step 4 — Error budget policy**
The error budget policy is what gives the SLO teeth — it turns a reported number into a release decision. Without it, the SLO is a dashboard metric nobody acts on. Get the policy accepted by service owners at the same time as the targets.

**Acceptance criteria:**
> Every service tier has SLIs computable from live telemetry; SLO targets accepted by the service owner; and a written error budget policy; budget consumption is visible on Dashboard 2 (SRE).

---

### WP3-C-02 · Alert Rules as Code
**Sprint:** S8 · **Days:** 8

**Description:** The 8 alert classes, defined in GitLab so a threshold change is reviewed. FAB owns these rules as IP.

**Affected files:** `alerts/rules/` (8 files, one per class)

**Steps (one per alert class):**

**Step 1 — GernasRunawayAgent**
```yaml
alert: GernasRunawayAgent
expr: max_step_violation_total > 0
for: 0m   # IMMEDIATE — no delay window
severity: P2
route_to: agent_squad
annotations:
  summary: "Agent exceeded max step limit — consuming budget continuously"
  runbook: "docs/runbooks/RB-01-runaway-agent.md"
```
`for: 0m` is intentional and non-negotiable. A runaway agent burns money for every minute it is left running. No delay window.

**Step 2 — GernasCostAnomaly**
```yaml
alert: GernasCostAnomaly
expr: cost_per_use_case_1h > 3 * avg_over_time(cost_per_use_case_1h[7d])
for: 30m
severity: P2
route_to: finops_plus_agent_owner
```

**Step 3 — GernasModelDegradation**
```yaml
alert: GernasModelDegradation
expr: task_success_rate < slo_target - 0.05
for: 1h
severity: P2
route_to: agent_squad
```
⚠ MRM sign-off required on the threshold (`slo_target - 0.05`) before this rule goes to production. Flag to Risk R7.

**Step 4 — GernasLatencyBreach**
SLO burn-rate alert (not a raw latency threshold):
```yaml
alert: GernasLatencyBreach
expr: slo_error_budget_burn_rate{tier='1', sli='latency'} > 6
for: 5m
severity: P1
```

**Step 5 — GernasErrorRateSpike**
```yaml
alert: GernasErrorRateSpike
expr: slo_error_budget_burn_rate{tier='1', sli='availability'} > 6
for: 5m
severity: P1
```

**Step 6 — GernasSecurityViolation**
```yaml
alert: GernasSecurityViolation
expr: rate(policy_block_total[5m]) > 0 OR rate(jailbreak_attempt_total[5m]) > 0
for: 0m   # IMMEDIATE
severity: P1
route_to: security_operations
```
⚠ CISO/SOC approval required before production activation. Flag as dependency.

**Step 7 — GernasCapacityThreshold**
```yaml
alert: GernasCapacityThreshold
expr: ptu_utilisation_percent > 80 OR vector_index_utilisation_percent > 80
for: 15m
severity: P2
route_to: platform_sre
```

**Step 8 — GernasDataQuality**
```yaml
alert: GernasDataQuality
expr: rate(gernas_dropped_total[5m]) > 0
for: 10m
severity: P2
route_to: platform_sre
annotations:
  summary: "Telemetry pipeline dropping data — investigate reason label"
```

**All rules:** committed to `alerts/rules/` as versioned YAML; deployed by pipeline only; each references its SLO target or a documented threshold owner.

**Acceptance criteria:**
> All eight alert classes are implemented as versioned rules with severities; each references an SLO or documented threshold owner; agentic classes tested against replayed failure traces.

---

### WP3-C-03 · Severity, Routing, Incidents
**Sprint:** S8–S9 · **Days:** 4

**Steps:**

**Step 1 — Severity model by business impact**
```
P1: customer-facing agent unavailable, or policy breach with customer impact
P2: degraded quality, runaway agent, cost anomaly, capacity threshold breach
P3: single agent failing (non-customer-facing), data quality drift
P4: hygiene — stale index, expiring grant, conformance drift
```
Defined by what it means for a customer, not by how large the technical signal is.

**Step 2 — Routing matrix**
```
Availability, latency breach    → Platform SRE on-call
Runaway, loop, quality          → Agent squad (routed by gernas.agent_owner from enrichment)
Policy, security, jailbreak     → Security operations
Cost anomaly                    → FinOps + agent owner
Telemetry loss                  → Platform SRE (P2)
```
Routing key: `gernas.agent_owner` (set by the enrichment processor in WP2-A-03). A new agent routes correctly on day one without anyone editing an alert rule.

**Step 3 — FAB on-call platform integration**
Connect to FAB's existing on-call tool (PagerDuty or Microsoft Teams — confirm which). Do not introduce a new on-call platform. Test: trigger each alert class in staging; confirm it arrives in the correct on-call channel within 2 minutes.

**Step 4 — Escalation timers**
P1: 15 minutes → owner's manager. P2: 60 minutes → owner's manager. Configured in the on-call tool, not in the alert rule.

**Acceptance criteria:**
> Every alert class has a severity tied to business impact and routes to the owning team in FAB's existing on-call platform; routing is driven by enrichment fields rather than hardcoded lists.

---

### WP3-C-04 · Alert Quality Controls
**Sprint:** S9 · **Days:** 3

**Description:** Preventing alert fatigue — the failure mode that makes the entire alerting investment worthless within a month.

**Steps:**

**Step 1 — Grouping (one cause, one notification)**
```yaml
group_by: [alertname, gernas.use_case_id]
group_wait: 30s
group_interval: 5m
repeat_interval: 4h
```

**Step 2 — Deduplication (gateway outage ≠ 50 incidents)**
```yaml
inhibit:
  - source_matchers: [alertname="LLMGatewayDown"]
    target_matchers: [alertname="GernasModelDegradation"]
    equal: [environment]
```
A shared dependency outage creates one incident, not one per affected agent.

**Step 3 — Maintenance and deployment suppression**
Planned maintenance window: silence active. Deployment in progress: silence `GernasLatencyBreach` for the deployment duration. Both configured via the on-call platform's maintenance window feature.

**Step 4 — Weekly volume and precision review**
Automated weekly report:
```
Fired this week: 34
Actioned: 29
Auto-resolved: 5
Noisiest rule: GernasDataQuality (11 fires)
P1 precision: 100%
P2 precision: 87%
Action: tune GernasDataQuality threshold
```
The precision metric (alerts that were true positives / total fires) is the health indicator for the alerting system. Below 90% = tuning needed.

**Acceptance criteria:**
> One underlying failure produces one notification; a shared dependency outage does not page every agent owner; maintenance windows suppress correctly; a weekly precision review is in place.

---

### WP3-C-05 · Runbooks (12 Alert Classes)
**Sprint:** S9 · **Days:** 9 · **Rate:** 12 × 0.75d

**Description:** Written procedures so whoever is woken knows what to do. Each runbook follows the same five-step format so a responder under pressure doesn't have to learn a new structure.

**Format for every runbook:**
```markdown
## RB-XX — [Alert name] ([severity])

### 1. CONFIRM
[How to verify this is a real incident and not a false alarm. Dashboard link + specific panel.]

### 2. DIAGNOSE
[Most common cause first. Then less common causes in order of probability.
Never alphabetical. Never top-down architecture.]

### 3. ACT
[Specific actions for each diagnostic path. Commands where applicable.]

### 4. ESCALATE
[When and to whom. Timing thresholds.]

### 5. VERIFY
[How to confirm the incident is resolved and the metric has returned to normal.]
```

**12 runbooks to produce:**

| File | Alert | Notes |
|---|---|---|
| `RB-01-runaway-agent.md` | GernasRunawayAgent | Capture trace FIRST — see WP3-C-06 |
| `RB-02-cost-anomaly.md` | GernasCostAnomaly | Most common causes: retry storm, loop, prompt growth, model change |
| `RB-03-model-degradation.md` | GernasModelDegradation | MRM contact required for threshold changes |
| `RB-04-latency-breach.md` | GernasLatencyBreach | Diagnose gateway vs agent runtime — different fixes |
| `RB-05-error-rate-spike.md` | GernasErrorRateSpike | Check infra first, then agent code |
| `RB-06-security-violation.md` | GernasSecurityViolation | SOC owns this — do not attempt remediation without SOC |
| `RB-07-capacity-threshold.md` | GernasCapacityThreshold | PTU/GPU have lead times — escalate early |
| `RB-08-data-quality.md` | GernasDataQuality | Check `gernas.dropped{reason=...}` label first |
| `RB-09-availability-burn.md` | Agent availability SLO burn | |
| `RB-10-slo-breach.md` | General SLO breach | |
| `RB-11-telemetry-loss.md` | Collector self-observability | Separate from data quality |
| `RB-12-conformance-drift.md` | Attribute conformance | |

**Acceptance criteria:**
> Each of the twelve alert classes has a runbook covering confirmation, diagnosis, action, escalation and verification; each is linked from the alert itself; a FAB engineer can follow one without help from the build team.

---

### WP3-C-06 · Agentic-Specific Runbooks
**Sprint:** S9 · **Days:** 3

**Description:** Four failure patterns where the standard engineering instinct is actively wrong. The correct first action is counter-intuitive in each case, and the standard reflex — restart the failing thing, then scale out — makes three of the four worse.

**Steps (one runbook per failure pattern):**

**Step 1 — Agent Loop runbook**
```
WRONG: restart the agent immediately
RIGHT: capture the trace first, then break the loop, then fix the instructions
WHY: restarting destroys the evidence that explains why the loop started
CONTAIN: set gernas.max_steps to 1 for this agent version; let current run complete or time out; retrieve trace from Opik; THEN restart
```

**Step 2 — Delegation Storm runbook**
```
WRONG: restart the parent agent OR scale out the runtime
RIGHT: drain the queue before returning compute
WHY: restarting the parent recreates the storm; scaling out multiplies it
CONTAIN:
  1. Stop admission of new parent runs (feature flag or rate limit)
  2. Let in-flight children complete or time out (do NOT kill them — orphans work units)
  3. Only then restart the orchestrator
DO NOT: kill children mid-flight; scale out the runtime
ROOT CAUSE: usually a plan with no termination condition, or two agents delegating to each other
```

**Step 3 — Fallback Cascade runbook**
```
WRONG: scale out to handle the load spike
RIGHT: reduce load — the fallback models are already saturated
WHY: a model outage pushes load to fallbacks until they fail too; scaling brings more agents to the same saturated pool
CONTAIN:
  1. Check GernasCapacityThreshold — is PTU at 100%?
  2. Enable request queuing (accept but delay, don't fail)
  3. Reduce agent concurrency limit until primary model recovers
  4. Do NOT add more model capacity mid-incident without Finance approval
```

**Step 4 — Judge Disagreement runbook**
```
SITUATION: evaluation scores have diverged from human reviewer scores
TWO POSSIBLE CAUSES: (a) agent quality actually dropped, (b) the judge model changed
DISTINGUISH:
  1. Run frozen control set (known-good traces) through the judge — if scores changed, the judge changed
  2. If control set scores are stable, the agents changed
WRONG: assume one cause without running the control set test
ESCALATE: to MRM if cause is ambiguous or if human reviewers disagree with the judge by > 10 points
```

**Acceptance criteria:**
> Loop, delegation storm, fallback cascade and judge disagreement each have a procedure stating the correct containment order and the actions to avoid; each has been walked through with FAB SRE.

---

## WP3·D — FinOps and Cost Attribution (BE2-owned)

---

### WP3-D-01 · Cost Model Implementation
**Sprint:** S9–S10 · **Days:** 12

**Description:** Computing what a single agent run actually consumed across nine components. Attribution, not invoice display — the Blueprint is explicit about this distinction.

**Affected files:**
- `cost-model/formula.py`
- `cost-model/components/`
- `tests/test_cost_model.py`

**⚠ Directly metered vs indirectly metered distinction:**
- Directly metered (countable unit per run): LLM tokens, embedding calls, search units, doc pages
- Indirectly metered (no per-run billing unit): compute, storage, network → each needs an allocation basis

**Steps:**

**Step 1 — Run cost formula skeleton**
```python
@dataclass
class RunCost:
    agent_run_id: str
    use_case_id: str
    business_domain: str
    agent_id: str
    business_outcome: str  # success/partial/failure/abandoned
    llm_cost_aed: float = 0.0
    embedding_cost_aed: float = 0.0
    search_cost_aed: float = 0.0
    doc_processing_cost_aed: float = 0.0
    tool_cost_aed: float = 0.0        # if metered
    compute_cost_aed: float = 0.0
    storage_cost_aed: float = 0.0
    network_cost_aed: float = 0.0
    shared_alloc_aed: float = 0.0     # see WP3-D-03
    total_cost_aed: float = field(init=False)

    def __post_init__(self):
        self.total_cost_aed = sum([
            self.llm_cost_aed, self.embedding_cost_aed, self.search_cost_aed,
            self.doc_processing_cost_aed, self.tool_cost_aed, self.compute_cost_aed,
            self.storage_cost_aed, self.network_cost_aed, self.shared_alloc_aed
        ])
```

**Step 2 — LLM component (directly metered)**
`llm_cost_aed = input_tokens × input_rate_per_1k + output_tokens × output_rate_per_1k`. Rates from versioned rate card. Test: 1,840 input tokens + 260 output tokens at GPT-4o paygo rates = expected AED value.

**Step 3 — Embedding component (directly metered)**
`embedding_cost_aed = embedding_calls × tokens_per_call × rate_per_1k`. Test: batch of 10 embeddings produces correct cost.

**Step 4 — RAG/Search component (directly metered)**
`search_cost_aed = search_units × rate_per_unit`. Units depend on search service (queries, documents retrieved, etc.). Document the basis.

**Step 5 — Document processing component (directly metered)**
`doc_cost_aed = doc_pages × rate_per_page`. For OCR: different rate than non-OCR.

**Step 6 — Compute component (indirectly metered)**
`compute_cost_aed = run_duration_seconds × (node_cost_per_hour / 3600) × cpu_fraction`. CPU fraction from AKS container metrics. Test: a 10-second run on a node costing X per hour produces the expected compute cost.

**Step 7 — Storage and network (indirectly metered)**
Storage: `storage_cost_aed = avg_storage_gb × rate_per_gb_month / (days_in_month × runs_per_day)`. Network: `network_cost_aed = egress_gb × rate_per_gb`. Both are estimates with documented bases.

**Step 8 — Fact row emission and reconciliation stub**
One cost fact row per run, written to the cost schema in the data plane. Includes all nine components and the `business_outcome`. Total must reconcile to invoice within agreed tolerance (see WP3-D-05).

**Acceptance criteria:**
> Every agent run produces a cost fact row covering all nine components with its use case, owner and outcome attached; totals reconcile to the invoice within the agreed tolerance.

---

### WP3-D-02 · Rate Cards + PTU Amortisation
**Sprint:** S10 · **Days:** 8

**Description:** Turning consumption into money, including the capacity FAB buys by the month rather than by the call.

**Steps:**

**Step 1 — Versioned rate card schema**
```yaml
rate_cards:
  - model: gpt-4o
    deployment: paygo-uae
    input_per_1k_tokens_aed: 0.0092
    output_per_1k_tokens_aed: 0.0368
    currency: AED
    valid_from: "2026-04-01"
    valid_to: null   # null = current
    source: "Finance confirmed YYYY-MM-DD"
```
Historical rate: when a rate changes, close the old record (`valid_to = date`), add a new one. Past months never restate.

**Step 2 — PTU amortisation (no per-call price)**
```
PTU monthly commit: AED 184,000 (from Finance)
Tokens served this month: 412,000,000
Derived rate per 1k tokens: 184,000 / 412,000 = AED 0.447/1k
```
Derived rate computed monthly, frozen for that month, applied to all runs in that month.

**Step 3 — Idle PTU treatment**
```
Capacity tokens (if 100% utilised): 680,000,000
Actual tokens served: 412,000,000
Utilisation: 60.6%
Idle cost: AED 184,000 × (1 - 0.606) = AED 72,500/month
```
Idle capacity charged to the platform overhead budget, not to individual agents. An efficient agent must not look expensive because the fleet was underutilised.

**Step 4 — Closed month stability**
A derived rate computed for April 2026 must not change in May when the rate card updates. Store the derived rate in the monthly fact table with `effective_month`. Test: change a rate card; confirm April cost facts are unchanged.

**Acceptance criteria:**
> Rate cards are versioned by effective date for every provider, model and deployment; PTU costs are amortised over actual tokens served with idle capacity held at platform level; closed months do not restate when rates change.

---

### WP3-D-03 · Shared Allocation Methodology
**Sprint:** S10 · **Days:** 5

**Description:** Dividing the four shared platform costs between agents in a way Finance will stand behind. The allocation basis is a choice — every choice makes some agent look better and another worse. The choice must be ratified, not assumed.

**Steps:**

**Step 1 — Identify the four shared cost pools**
1. Collector + pipeline (sidecar + gateway)
2. AKS baseline (node pool minimum, not per-agent compute)
3. Vector store (index size + query volume)
4. LLM gateway (infrastructure, not model calls)

**Step 2 — Present two allocation bases side by side**
For each pool, compute the per-agent cost under two different bases:
- By-run: each agent pays `(pool_cost / total_runs) × own_runs`
- By-compute-seconds: each agent pays `(pool_cost / total_compute) × own_compute`

Show the resulting per-agent numbers side by side. A light agent is penalised by the per-run split; this is the argument for compute-based allocation.

**Step 3 — Finance ratification**
Present the two bases to Finance. Get a signed-off choice. Document: basis chosen per pool, rationale, approver, date, review cadence (annually or on material platform change).

**Step 4 — Unallocated residual**
Costs that cannot be fairly attributed to any agent (e.g., inter-zone network egress, platform overhead): reported as a separate `unallocated_platform_aed` line. Never spread evenly to make numbers tidy — an unexplained residual is better than a misleading attribution.

**Acceptance criteria:**
> Each shared cost pool has a documented allocation basis ratified by Finance; the effect of the chosen basis is shown against at least one alternative; unallocated platform cost is reported rather than distributed.

---

### WP3-D-04 · Derived Cost Measures
**Sprint:** S10 · **Days:** 6

**Description:** The ratios that make cost meaningful. Cost per run ranks agents in the wrong order — an agent that costs less per run but fails half the time is more expensive per completed task.

**12 derived measures to implement:**

| Measure | Formula | Why it matters |
|---|---|---|
| Cost / use case | `sum(run_cost) group by use_case_id` | Accountability |
| Cost / business unit | `sum(run_cost) group by business_domain` | Budget allocation |
| Cost / agent | `sum(run_cost) group by agent_id` | Efficiency comparison |
| Cost / agent run | `avg(run_cost) group by agent_id` | Baseline |
| **Cost / successful task** | `sum(run_cost) / count(runs where outcome='success')` | **The Blueprint-singled-out measure** |
| Cost / 1K interactions | `(sum(run_cost) / count(runs)) × 1000` | Comparison to manual process |
| Cost / model | `sum(llm_cost_aed) group by model_id` | Model selection signal |
| Cost / user | `sum(run_cost) group by tokenised_user_id` | Usage intensity |
| Idle infrastructure cost | `unallocated_platform_aed + idle_ptu_aed` | Efficiency opportunity |
| Reserved capacity utilisation | `tokens_served / capacity_tokens` | PTU/GPU headroom |
| Peak-to-average ratio | `max(hourly_cost) / avg(hourly_cost)` | Spikiness signal |
| Capacity headroom | `(capacity_tokens - tokens_served) / capacity_tokens` | Procurement signal |

**Key implementation note for "cost per successful task":** uses the `business_outcome = 'success'` field from WP1-T1-01 (Use Case Layer instrumentation). This field cannot be computed without the per-use-case success definitions agreed in WP3-A (KPI catalogue workshops). Do not implement without those definitions in place.

**Acceptance criteria:**
> All twelve derived measures implemented and visible on Dashboard 7; cost per successful task uses signed business success definitions; each measure carries the same ten-field KPI specification.

---

### WP3-D-05 · Invoice Reconciliation
**Sprint:** S11 · **Days:** 6

**Description:** Proving the cost model agrees with the actual Azure bill within a tolerance Finance has signed. An unexplained gap — even a small one — destroys trust in the model faster than a large explained gap.

**Steps:**

**Step 1 — Line-by-line comparison**
For each component in the cost model, compare modelled cost to the corresponding Azure invoice line. Components: LLM (paygo), LLM (PTU), embeddings, search, compute (AKS), storage, network.

**Step 2 — Classify every variance**
```
Component    Modelled   Invoiced   Variance
LLM (paygo)  41,220     41,180     +0.1%   → rounding
LLM (PTU)    184,000    184,000    0.0%
Search        11,800    12,340     -4.4%   → tier-boundary pricing not modelled → fix
Network        1,980     2,640     -25.0%  → inter-zone egress unmetered per run → allocate to shared pool
```
No unclassified variances. Every gap has a named explanation.

**Step 3 — Agreed tolerance with Finance**
Propose ±2% total. Get Finance sign-off. The tolerance is not a free pass — every variance within tolerance still needs a classification.

**Step 4 — Automated monthly reconciliation**
Schedule a monthly job: pull Azure invoice via billing API, run against modelled costs, produce diff report, flag any new variance > 0.5% for investigation.

**Step 5 — Three-run acceptance criterion**
Finance sign-off requires three consecutive monthly reconciliations within tolerance. The first dry run is in S10; second and third in S11. This means Finance must be engaged from S9 (see Risk R6).

**Acceptance criteria:**
> Modelled cost reconciles to the invoice within the agreed tolerance; every material variance is classified and documented; Finance has signed the tolerance; reconciliation runs monthly and automatically.

---

### WP3-D-06 · Capacity Telemetry and Forecast
**Sprint:** S10–S11 · **Days:** 8

**Description:** Answering whether the platform is correctly sized and how long the current capacity lasts. Headroom expressed in months, not percentage — because months is the lead time to procure more PTU or GPU capacity.

**Steps:**

**Step 1 — Utilisation against limits (not raw utilisation)**
```
Resource         Used    Limit   Utilisation   Headroom
gpt-4o PTU       60.6%   100%    60.6%         5.2 months
GPU pool          71.0%   8×      71.0%         3.8 months
AKS nodes         48.0%   24      48.0%         9+ months
Vector index GB   82.0%   500     82.0%         2.1 months
```
Utilisation without a limit is meaningless. 70% of unknown = unknown.

**Step 2 — Saturation and peak concurrency**
Averages hide failure moments. Measure peak concurrent runs and queue saturation separately from averages. A platform that handles 50 concurrent runs on average but fails at 80 needs to be sized for 80.

**Step 3 — Headroom in months calculation**
```
Current usage: 60.6% of PTU capacity
Current agents: 27
Growth rate: 50/27 = 1.85× when full estate
PTU headroom at 50 agents: (100% - 60.6%) / (60.6% × (1.85 - 1)) ≈ 0.9 months
```
PTU headroom drops to under 1 month when the estate doubles. This is the binding constraint.

**Step 4 — Growth forecast to 50+ agents**
Project each capacity dimension from 27 to 50 production agents. Identify the binding constraint (the dimension that will be exhausted first). Document the breach date.

**Step 5 — Dashboard 8 panels**
Coordinate with UI track: capacity headroom table, utilisation trend chart, breach forecast line, procurement lead time annotation. The forecast, not the utilisation, is what goes on the dashboard.

**Acceptance criteria:**
> Utilisation against limits published for every capacity dimension on Dashboard 8; headroom expressed as time remaining; a forecast to 50+ agents identifies the binding constraint and its date.

---

## ⚠ Open Items That Block WP2/WP3 Implementation

| Item | Tasks blocked | Action required |
|---|---|---|
| Data-plane store not selected (R1) | WP2-C-01 through WP2-C-05, WP2-A-06 (routing) | FAB Architect decision — escalate if not resolved by S0 end |
| CISO PDPL sign-off on redaction matrix (R3) | WP2-A-04 evidence pack, Collector staging → prod (S7 gate) | Formal review scheduled before S3 |
| Finance stakeholder availability (R6) | WP3-D-03, WP3-D-05 (3-run criterion) | Finance engaged by S9 — if not available, WP3·D is the Phase-2 release valve |
| MRM sign-off on GernasModelDegradation threshold | WP3-C-02 Step 3, WP3-C-05 RB-03 | Named MRM contact required before alert goes to production |
| CISO/SOC approval for GernasSecurityViolation (for:0m) | WP3-C-02 Step 6 | Required before security alert activates in production |
| OPA OTLP export support | WP2-B-02b | Check OPA version in FAB before starting |
| PTU deployment IDs via Azure Monitor API | WP3-D-01 Step 2, WP1-PTU-01 | Azure subscription owner grant required |
| Knowledge Layer ownership | WP2-B-03 Step 4 | NOT COSTED — raise to programme lead before doing any work on it |

---

*WP3·A (KPI Catalogue) and WP3·B (Query API & Dashboards) are UI track tasks — separate plan document to follow.*
