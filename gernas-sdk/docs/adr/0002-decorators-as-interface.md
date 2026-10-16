# ADR 0002 — Decorators are the interface

**Status:** accepted (2026-10-16)

**Decision.** Agent teams add `@trace_*` decorators and nothing else. One engine (`_core.wrap`) implements sync, async, generator and async-generator handling, so all sixteen decorators share the same guarantees:
- the envelope is set first;
- every attribute goes through `safe_set`;
- spans are always ended;
- exception messages are PII-scrubbed.

Each decorator is a small `Spec` with hooks: `start`, `success`, `failure`, `finish`, `on_item`, `activate` and `deactivate`. Framework adapters reuse the same Specs, so a span from an Agent Framework middleware is identical to one from a decorator.

**Consequences.**
- Adding a decorator is a matter of writing a Spec.
- Behaviour such as loop detection and termination reasons cannot drift between decorators.
- The overhead target holds: the worst decorator's p95 is 0.20 ms against a 1 ms budget (`reports/overhead_report.md`).
