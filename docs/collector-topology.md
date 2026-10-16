# GERNAS OTel Collector — Topology Design

> **Status:** In progress — WP2-A-01  
> **Owner:** BE2 track  
> **Last updated:** 2026-09-26  
>
> This document is built incrementally across WP2-A-01 Steps 1–8.  
> Steps completed so far: Step 1 (Volume Sizing), Step 2 (Two-Tier Topology).

---

## Section 1 — Volume Sizing

> Step 1 of WP2-A-01. All numbers must be traceable to the inputs below.

### Input Parameters

| Parameter | Value | Source |
|---|---|---|
| Agent count (full estate) | 50 | Blueprint scope — 50 production agents |
| Spans per agent run | 40 | Average run depth: use-case → agent → chains → LLM/tool calls |
| Agent runs per day | 800 | Business volume estimate from FAB programme lead |
| Peak-to-average multiplier | 8× | Industry rule of thumb for bursty AI workloads; to be validated in Step 6 load test |

### Derived Throughput

```
Daily span volume:
  50 agents × 40 spans/run × 800 runs/day = 1,600,000 spans/day

Average throughput:
  1,600,000 spans / 86,400 seconds = 18.5 spans/sec ≈ 19 spans/sec

Peak throughput (8× average):
  19 × 8 = 152 spans/sec ≈ 150 spans/sec
```

### Approximate Payload Volume

Assuming 2.1 KB per span (from WP2-C-05 volume model):

```
Average data rate:   19 spans/sec × 2.1 KB  ≈   40 KB/sec  (0.3 Gbps)
Peak data rate:     150 spans/sec × 2.1 KB  ≈  315 KB/sec  (2.5 Mbps)
Daily raw volume:   1,600,000 × 2.1 KB      ≈    3.4 GB/day
```

### Sizing Implications

- **Sidecar buffering:** `max_queue_size: 2048` absorbs short gateway outages at peak (2048 spans ÷ 150/sec = ~14 seconds of full-peak buffering before back-pressure).
- **Gateway replicas:** 3 replicas sized at `cpu: 1 core / 2 core limit, memory: 2Gi / 4Gi` per replica gives ~3× headroom over average throughput before HPA scaling kicks in.
- **HPA trigger:** queue depth (`otelcol_exporter_queue_size > 2000`) not CPU — CPU is a lagging indicator; queue depth signals data loss risk earlier.

> **Note:** These numbers represent the design point for Steps 2–8.  
> They will be validated against a load test in Step 6 and updated if the test reveals a different peak profile.

---

## Section 2 — Two-Tier Topology

> Step 2 of WP2-A-01.

The GERNAS telemetry pipeline uses two tiers of OTel Collector. Each tier has a single, non-overlapping responsibility. Mixing responsibilities between the tiers is the failure mode that causes agent processes to block on expensive Collector work.

### Tier 1 — Agent-Side Sidecar

**What it is:** A lightweight OTel Collector instance running as a sidecar container in the same pod as each agent.

**Responsibility:** Buffer-and-forward only. Accept spans from the agent process over loopback gRPC, queue them locally, and forward to the central gateway. No enrichment. No redaction. No routing decisions.

**Why a sidecar and not an in-process SDK export:**  
The agent process must never block on Collector work. If the central gateway is slow or unreachable, the sidecar queue absorbs the backlog. The agent sees a local loopback write (microsecond latency) and continues. Without the sidecar, a gateway outage would stall or crash agent processes under backpressure.

**Processing chain:**
```
Agent process
  │  OTLP gRPC (loopback :4317)
  ▼
[memory_limiter] ─── drops spans if sidecar process memory exceeds limit
  │                   (protects the agent pod, not the pipeline)
  ▼
[batch]          ─── groups spans into efficient batches before forwarding
  │
  ▼
[OTLP exporter]  ─── forwards to central gateway; retries on failure;
                      fills queue (max_queue_size: 2048) if gateway is unavailable
```

**What the sidecar does NOT do:**
- No registry lookups
- No PII redaction
- No sampling decisions
- No routing logic

All of the above are gateway responsibilities. Keeping them out of the sidecar is what keeps sidecar latency near zero.

**Failure mode — gateway unavailable:**  
Sidecar queue fills to `max_queue_size: 2048`. At peak throughput (150 spans/sec) this gives ~14 seconds of buffering before back-pressure signals to the agent's OTel SDK. The SDK drops spans rather than blocking the agent process. A drop counter increments; the loss alert fires within 10 minutes (WP2-A-07).

**Resource footprint (per agent pod):**
| Resource | Request | Limit |
|---|---|---|
| CPU | 100m | 200m |
| Memory | 128Mi | 256Mi |

Kept deliberately small — the sidecar must not compete with the agent for pod resources.

---

### Tier 2 — Central HA Gateway

**What it is:** A 3-replica OTel Collector Deployment running in the GERNAS AKS namespace, spread across availability zones.

**Responsibility:** All expensive processing — registry enrichment, PII redaction, tail-based sampling, signal routing. Every span that leaves the estate passes through the gateway exactly once.

**Why centralised:**  
Running enrichment and redaction in 50 sidecars would mean 50 registry cache instances, 50 redaction rule evaluations per span, and 50 places to update when a rule changes. A single gateway fleet means one cache, one rule set, and one update path. The tradeoff is that the gateway is a shared component — it must be HA.

**Processing chain:**
```
Sidecar (any agent pod)
  │  OTLP gRPC :4317  (TLS + workload identity — WP2-A-02)
  ▼
[memory_limiter]         ─── gateway-level memory guard
  │
  ▼
[k8sattributes]          ─── injects pod/node labels as resource attributes (WP2-B-01)
  │
  ▼
[gernas_enrichment]      ─── registry lookup: business_domain, risk_tier, cost_centre, etc.
  │                           tag-and-pass on registry miss (never drop)
  ▼
[redaction]              ─── per-field rules from WP1-CTX-05 matrix; deny-by-default
  │
  ▼
[tail_sampling]          ─── decision_wait: 30s; always-keep rules for errors / high-risk / HITL
  │
  ▼
[routing]                ─── traces → Opik + data plane
                              metrics → metrics schema
                              logs → logs schema
                              cost facts → cost schema
                              audit → audit schema (durable pipeline)
```

**High availability:**
- 3 replicas, one per AKS availability zone
- Anti-affinity rules prevent two replicas landing on the same zone
- A zone failure leaves 2 of 3 replicas running — telemetry continues without gap

**Scaling:**
- HPA trigger: `otelcol_exporter_queue_size > 2000` (queue depth, not CPU)
- Scale-up adds replicas in unaffected zones; scale-down is gated on queue draining

**Resource footprint (per gateway replica):**
| Resource | Request | Limit |
|---|---|---|
| CPU | 1 core | 2 cores |
| Memory | 2Gi | 4Gi |

Sized from Step 1: 3 replicas × 1 core request = 3 cores handles ~57 spans/sec each at average load, with the limit providing headroom to absorb burst without HPA firing immediately.

---

### Tier Interaction Summary

```
┌─────────────────────────────────────────────┐
│  Agent Pod (×50)                            │
│  ┌──────────────┐   ┌────────────────────┐  │
│  │ Agent process│──▶│ Sidecar Collector  │  │
│  │              │   │ (buffer + forward) │  │
│  └──────────────┘   └────────┬───────────┘  │
└───────────────────────────────┼─────────────┘
                                │ OTLP gRPC (TLS + OIDC)
                                ▼
              ┌─────────────────────────────────────┐
              │  Gateway Collector (3 replicas, HA)  │
              │  enrichment → redaction →            │
              │  sampling → routing                  │
              └──────────────┬──────────────────────┘
                             │
              ┌──────────────┼──────────────────────┐
              ▼              ▼                       ▼
           Opik         Data plane             Audit store
         (traces)    (traces/metrics/        (durable, ordered)
                      logs/cost)
```

**Decision boundary:** The sidecar never makes a keep/drop decision. Every keep/drop decision is made in the gateway after the full run context is available (tail sampling). This is the property that ensures no error span, high-risk run, or human-approval event is sampled away.

---

## Section 3 — Sidecar Configuration

> To be completed in WP2-A-01 Step 3.

---

## Section 4 — Gateway Deployment

> To be completed in WP2-A-01 Step 4.

---

## Section 5 — HPA Scaling Rule

> To be completed in WP2-A-01 Step 5.

---

## Section 6 — Buffered Degradation Test Results

> To be completed in WP2-A-01 Step 6.

---

## Section 7 — Zone Failure Simulation Results

> To be completed in WP2-A-01 Step 7.

---

## Section 8 — FAB Platform Acceptance

> To be completed in WP2-A-01 Step 8.  
> DR acceptance document reference will be added here after sign-off.
