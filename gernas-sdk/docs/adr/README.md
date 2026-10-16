# Architecture decision records (WP1-DOCS-01 step 4)

| ADR | Decision |
|---|---|
| [0001](0001-otel-primitives-only.md) | Build on OTel SDK primitives only; no auto-instrumentation packages |
| [0002](0002-decorators-as-interface.md) | Decorators (plus thin framework adapters) are the only application interface |
| [0003](0003-dual-emit-in-collector.md) | Emit once; fan out to Opik and the data plane in the Collector; kill switches as resource attributes |
| [0004](0004-finally-span-lifecycle.md) | `finally`-based span lifecycle for streams and generators; cancelled = attribute, not status |
| [0005](0005-sixteen-domain-registry.md) | Implement the 16-domain registry (13 Blueprint domains plus 3 gap-analysis domains), generated into code |
| [0006](0006-evolve-beacon-kit.md) | Evolve Beacon Kit in place (git mv) rather than rewriting; no `beacon_kit` shim |
