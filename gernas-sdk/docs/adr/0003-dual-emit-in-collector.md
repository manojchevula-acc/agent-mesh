# ADR 0003 — Emit once, fan out in the Collector

**Status:** accepted (2026-10-16)

**Decision.**
- The SDK exports once, over OTLP, to the Collector.
- The Collector runs two trace pipelines. The data-plane pipeline does tail sampling with always-keep policies. The Opik pipeline does probabilistic sampling, applies the Opik span shape and sends OTLP/HTTP protobuf. Opik accepts traces only, over HTTP.
- Metrics and logs go only to their data-plane destinations.
- Kill switches: the SDK stamps `GERNAS_EMIT_OPIK` and `GERNAS_EMIT_DATAPLANE` as the resource attributes `gernas.route.opik` and `gernas.route.dataplane`. A `filter` processor in each pipeline drops the signal when its switch is false. Turning a destination off is an environment change and a restart, with no code change.
- PDPL is enforced again at the Collector: the attributes processor deletes payload keys, the redaction processor is deny-by-default with a registry-generated allow-list, and PII value patterns are masked.

**Consequences.**
- No signal is recorded twice: verified, one span per run id.
- Destinations can change without touching agents.
- The data-plane store is not yet selected (R1), so it is represented by per-signal `file` exporters, as WP2-A-06 step 7 allows.
- The redaction processor also filters **resource** attributes, so OTel resource semconv keys must be on the allow-list. This was found and fixed during end-to-end testing.
