# GERNAS Agent Telemetry SDK

Production-grade Python instrumentation library for FAB's GERNAS agentic AI platform.
Built on OpenTelemetry SDK primitives. All artefacts are FAB-owned intellectual property.

## Reference Files — Read Before Every Task

| File | Purpose |
|---|---|
| `IMPLEMENTATION_PLAN.md` | Authoritative task list — work from this, never invent tasks |
| `IMPLEMENTATION_LOG.md` | Record every completed step here before stopping |
| `docs/REQUIREMENTS_TRACEABILITY.md` | Requirement → implementation → evidence map (keep current) |
| `docs/sdk-developer-guide.md` · `docs/onboarding-runbook.md` | Public API and onboarding |
| `registry/domains/*.yaml` | The attribute contract — edit here, then `python registry/generate.py` |
| `../md_files/` · `../docs/` | Original WP1/WP2 plans, Blueprint (SOW) |

## Non-Negotiable Engineering Principles

1. **OTel SDK primitives only.** Never install or import `opentelemetry-instrumentation-*` packages. Every span is owned by this SDK.
2. **Sidecar package.** Application code never touches the OTel SDK directly.
3. **Decorators are the only interface.** Agent teams add one decorator. Zero boilerplate.
4. **Telemetry never breaks the application.** Wrap every `span.set_attribute()` in try/except. A Collector outage produces a dropped counter, never an agent exception.
5. **Collector is the backbone.** Never export directly from application to a backend: SDK → Collector → exporters always.
6. **Span hierarchy must be preserved.** `trace → agent_run → chain → llm_call / tool_call` via OTel context propagation, not custom IDs alone.
7. **Environment variables only.** No hardcoded endpoints, API keys, model names, or deployment IDs anywhere in source code.
8. **PDPL by default.** Never capture raw prompt text, completion text, user identity, or credentials. The redaction hook must run before any span attribute is emitted.

## Gate Protocol — Highest Priority Rule

Implement **one step at a time** exactly as defined in `IMPLEMENTATION_PLAN.md`.

After each step:
1. Run the tests listed in that step
2. Inspect the diff — confirm scope matches this step only
3. Update `IMPLEMENTATION_LOG.md` with the entry for this step
4. Print the completion report (format below)
5. **STOP.**

Do not proceed to the next step. Do not implement "while I'm here" improvements. Wait for explicit human approval.

This rule applies even when the next step is obvious, trivial, or directly dependent on what you just finished. Violating it creates unreviewed changes and breaks the audit trail.

## Completion Report (print after every step)

```
Step completed:         [Task ID · Step N — short title]
Files changed:          [list of files]
Symbols added/changed:  [class and function names]
Tests run:              [pytest command used]
Test result:            [PASS N/N  or  FAIL — reason]
Acceptance criteria:    [quote "Done when" from IMPLEMENTATION_PLAN.md]
Criteria met?:          [Yes / Partial / No — explain if not Yes]
Known issues:           [none, or list]
Next planned step:      [one line — do not implement it]

Please review this step. I will not proceed until you approve.
```

## Hard Prohibitions

- Do not install `opentelemetry-instrumentation-openai`, `opentelemetry-instrumentation-anthropic`, or any `opentelemetry-instrumentation-*` package
- Do not import `openai`, `anthropic`, `azure.ai`, or `boto3` into core SDK modules (only in `tests/` and `adapters/`)
- Do not use hand-typed string attribute names — always use the `Ctx` class from `gernas/attributes.py`
- Do not capture `gen_ai.prompt` or `gen_ai.completion` by default
- Do not commit unless explicitly told to
- Do not implement more than the current step

## Python Environment

- Python 3.11+ — use `match`, `asyncio.TaskGroup`, `tomllib` freely; no 3.9/3.10 compatibility shims
- Test runner: `pytest` with `pytest-asyncio` for all async tests
- Full dependency list in `pyproject.toml`
- Package name: `gernas-telemetry` (import as `gernas`); evolved from Beacon Kit (`docs/MIGRATION_FROM_BEACON_KIT.md`)
- Before finishing any change: `pytest tests --cov=gernas --cov-fail-under=90` and `python registry/generate.py --check`
- Never edit `gernas/attributes.py` or the Collector allow-list block by hand — they are generated
