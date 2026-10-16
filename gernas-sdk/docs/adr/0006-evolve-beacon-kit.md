# ADR 0006 — Evolve Beacon Kit in place

**Status:** accepted (2026-10-16, by programme decision)

**Decision.**
- `beacon_kit/` was moved with `git mv` to `gernas-sdk/gernas/`, so history is preserved.
- These parts were kept and renamed: the audit chain (HMAC and chain hash), the compliance decorators, the JSONL file sinks, the pretty console exporter, the HTML and audit reports, the structured logger, the request logging, the circuit breaker, and the generic span and metric decorators.
- These parts were replaced because the spec requires it: config (`GernasConfig`), provider and exporters (`tracer.py`, `exporters.py`), the agent, LLM, tool and A2A decorators (the 16 spec decorators), and the middleware (pure-ASGI adapter).
- Direct Grafana and Azure exporters were removed (principle 5).
- No `beacon_kit` import shim is shipped. All in-repo callers were migrated. The deprecated `traced_*` aliases remain inside `gernas`.

**Consequences.** External code importing `beacon_kit` must migrate using `docs/MIGRATION_FROM_BEACON_KIT.md`.
