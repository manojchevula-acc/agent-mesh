# ADR 0005 — Blueprint-domain attribute registry, generated into code

**Status:** accepted (2026-10-16). Supersedes the 16-domain registry of 0.3.0.

**Decision.**
- `registry/domains/01…13_*.yaml` hold the 13 Blueprint §2 context domains. They are marked `blueprint: true` and their field names are exactly the blueprint's.
  - `registry/generate.py` holds the blueprint field list verbatim (`BLUEPRINT_CONTEXT`). Validation fails if a blueprint domain has a missing, extra or renamed field.
  - `trace_id` / `span_id` are native OTel span context (`native: true`), not attributes.
  - `user_id` is declared with action `drop`. Only `tokenized_user_id` is emitted.
- `20_component_signals.yaml` holds the Blueprint §3/§5/§6 component signals: delegation path, payload sizes, TTFT, HITL, retrieval and memory signals, and so on. They are SDK conventions, not blueprint field names, and are named in the same flat style.
- `30_sdk.yaml` holds the technical necessities: `operation` (TN-01), `framework` (TN-02), `sdk_version`, `redaction_applied`, `token_type`, and OTel HTTP semconv keys for the ASGI span (TN-03).
- Each field records its key, type, required level, sensitivity, cardinality, rollup flag, PDPL action, source requirement (`GERNAS-REQ-NNN` / `TN-NN`), brief and example.
- The generator validates the registry and produces:
  - `src/gernas/attributes.py` (`Ctx`, `SpanName`, `Metric`, `REGISTRY`, `ENVELOPE`, `RUN_CONTEXT`, `REQUIRED_ATTRIBUTES`, `DENYLIST`);
  - the redaction matrix;
  - OTel-style semantic conventions;
  - the attribute reference;
  - the Collector allow-list and denylist blocks.

  CI runs `generate.py --check`, and an AST lint forbids hand-typed `gernas.*` strings.

**Consequences.**
- One source drives SDK redaction, Collector rules, conformance checks and docs.
- Keys are `gernas.<blueprint_field>`. The namespace is a technical necessity, because bare names such as `provider` and `environment` would collide with other instrumentation. The architecture owner should confirm it (see Gaps in `docs/governance/BLUEPRINT_TRACEABILITY.md`).
