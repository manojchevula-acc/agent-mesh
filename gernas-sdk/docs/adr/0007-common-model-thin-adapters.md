# ADR 0007 — One telemetry model, thin framework adapters

**Status:** accepted (2026-10-16)

**Context.** The SDK must support Microsoft Agent Framework, LangChain, LangGraph and custom Python without separate schemas per framework.

**Decision.**
- The core (`src/gernas/decorators`, `run_state`, `context`) is framework-neutral and imports no framework.
- Each adapter maps framework-native execution onto the same spans:
  - MAF agent invocation, LangChain root runnable and LangGraph graph invocation → `agent.run`.
  - MAF workflow executor (`@trace_step`), LangGraph node and custom plan step → `agent.step`.
  - Chat-model request → `llm.call`.
  - Function / MCP tool → `tool.call`.
  - Retriever → `retrieval.search`.
- `framework` (TN-02) records the origin.
- Adapters reuse the core span builders (`start_run`, `open_step`, `record_usage`, `ToolSpec`, `LLMSpec`) rather than re-implementing attribute logic.
- An adapter invoked inside an existing Gernas run reuses that run; it never opens a second `agent.run`.
- Tools that are already decorated can opt out of adapter tool spans (`trace_functions=False`, `trace_tools=False`), which avoids duplicate emission. `gernas conformance` flags any remaining duplicates.
- Framework dependencies are optional extras. Importing an adapter without its framework raises an `ImportError` naming the extra.

**Consequences.** Dashboards and conformance rules are written once. Adding a framework means one adapter module and its tests, with no registry change.
