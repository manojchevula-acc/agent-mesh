# ADR 0003 — Emit once, fan out in the Collector

**Status:** accepted (2026-10-16). Revised in 0.4.0: SDK-side kill switches removed.

**Decision.**
- The SDK exports once, over OTLP, to the Collector.
- The Collector runs two trace pipelines:
  - The data-plane pipeline does tail sampling, always keeping errors, policy denials, high-risk content and HITL decisions.
  - The Opik pipeline does probabilistic sampling, maps the GERNAS keys to the OTel GenAI keys that Opik displays (`gernas.model_id` → `gen_ai.request.model`, tokens, provider, `session_id` → `thread_id`), and sends OTLP/HTTP protobuf.
- The data plane is **Azure Data Explorer** (`azuredataexplorer` exporter: traces, metrics, logs; AKS Workload Identity). Opik receives traces only (it ingests OTLP/HTTP traces only).
- Destination selection is **Collector configuration**. 0.3.0 had SDK resource flags (`gernas.route.*` from `GERNAS_EMIT_*`); they were removed because the blueprint puts routing in the gateway and forbids coupling producers to a vendor.
- PDPL is enforced again at the Collector:
  - `attributes/pdpl` deletes the registry-generated denylist;
  - the redaction processor is deny-by-default with a registry-generated allow-list;
  - PII value patterns are masked.

**Consequences.**
- No signal is recorded twice.
- Destinations change without touching agents.
- Vendor field shapes live in one place (the Collector).
- Local runs without `ADX_CLUSTER_URI` use the clearly labelled overlay `collector/local-dataplane-file.yaml` (file exporters) instead of ADX; it is never deployed. Deployment: `docs/collector-aks-deployment.md`.
