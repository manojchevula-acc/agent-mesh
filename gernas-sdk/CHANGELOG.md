# Changelog

All notable changes to `gernas-telemetry`. The version lives in `src/gernas/_version.py`.

## Unreleased

### Changed
- Repository layout. There is no API or telemetry change; `import gernas` is unchanged.
  - The package moved to `src/gernas/` (src layout). Tests now run against the installed package.
  - Tests are split into `tests/unit/`, `tests/integration/`, `tests/packaging/` and `tests/conformance/`.
  - Docs are grouped into `docs/user-guide/`, `reference/`, `operations/`, `governance/` and `adr/`, with `docs/index.md` as the map. `onboarding-runbook.md` became `docs/getting-started.md`.

### Added
- `py.typed` marker (PEP 561), so type checkers use the SDK's annotations.
- `LICENSE`, `CHANGELOG.md` and an SDK-local `.gitignore`.

## 0.4.0

Blueprint alignment. This is a breaking release; see `docs/user-guide/MIGRATION_FROM_BEACON_KIT.md` §0.
- GERNAS Blueprint §2 field names are used verbatim (`gernas.<field>`) and generated from `registry/` into `gernas.Ctx`.
- Mandatory identity (`GERNAS_SERVICE_NAME`, `GERNAS_USE_CASE_ID`, `GERNAS_APPLICATION_ID`, `GERNAS_BUSINESS_DOMAIN`, `GERNAS_ENVIRONMENT`) is validated, never defaulted (`GernasConfigError`).
- Decorators were consolidated (`trace_mcp` → `trace_tool(tool_type="mcp")`, `trace_stream` → `trace_llm`, `trace_rag` → `trace_retrieval`).
- Export is OTLP only, to the GERNAS OTel Collector.

## 0.3.0

Gernas SDK evolved in place from Beacon Kit (ADR 0006). See the history tables in `docs/user-guide/MIGRATION_FROM_BEACON_KIT.md`.
