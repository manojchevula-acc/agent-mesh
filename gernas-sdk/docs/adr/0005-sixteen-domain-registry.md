# ADR 0005 — Sixteen-domain attribute registry, generated into code

**Status:** accepted pending DAI confirmation (risk R5)

**Decision.**
- The registry has 16 domains: the 13 Blueprint context domains plus three from the gap analysis (`human_approval`, `evaluation`, `memory`). It lives in `registry/domains/*.yaml`.
- Every field records: name, type, enum, required level, sensitivity, cardinality class, rollup flag, PDPL action, owner and example.
- `registry/generate.py` validates the registry and generates everything that depends on it:
  - `gernas/attributes.py` (`Ctx`, `SpanName`, `Metric`, `REQUIRED_ATTRIBUTES`, `REDACTION_ACTIONS`);
  - the redaction matrix;
  - OTel-style semantic conventions for non-Python services;
  - the attribute reference document;
  - the Collector allow-list.
- CI runs `generate.py --check`, and an AST lint forbids hand-typed `gernas.*` attribute strings.

**13-field interpretation.** The plan's "13 required canonical context fields" for `gernas doctor` are not enumerated anywhere. They are defined in `registry/spans.yaml::canonical_context`, one per context domain that an agent run can always supply.

**Consequences.**
- A field exists in code only if it exists in the registry.
- The SDK redaction, the Collector rules and the conformance check all derive from one source.
- If DAI chooses 13 domains, the three extra domain files are removed, and the validator's domain count changes from 16 to 13.
