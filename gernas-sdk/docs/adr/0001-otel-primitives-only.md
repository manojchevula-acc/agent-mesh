# ADR 0001 — OTel SDK primitives only

**Status:** accepted (2026-10-16)

**Context.** Auto-instrumentation packages (`opentelemetry-instrumentation-openai`, `-httpx`, `-fastapi`, and so on) create spans the SDK does not own. Those spans use their own attribute names and may capture payloads, which conflicts with PDPL. They also differ between versions. Engineering principle 1 forbids them.

**Decision.** Every GERNAS span is created by `gernas/decorators/_core.py` through `tracer.start_span`. Each piece of cross-cutting work is built directly on the OTel API:
- context propagation uses `opentelemetry.propagate` (see `async_context.py`, `adapters/a2a.py`, `adapters/httpx.py`, `adapters/asgi.py` and `adapters/mcp.py`);
- the logs bridge (`logconfig/otel_handler.py`) is built on the Logs API, because the SDK's `LoggingHandler` is deprecated and its successor is an instrumentation package.

`tests/test_packaging.py` fails the build if an instrumentation dependency or a vendor SDK import appears in core.

**Consequences.**
- The SDK owns span names, attributes and redaction.
- New frameworks need a small adapter written against their public middleware or callback API, as was done for MAF, LangChain, A2A, fastmcp and ASGI.
- Framework-native spans (MAF `invoke_agent`, fastmcp `tools/call`) may still exist alongside SDK spans. The Collector's deny-by-default allow-list governs what they can carry.
