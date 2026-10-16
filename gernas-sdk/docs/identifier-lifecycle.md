# Identifier lifecycle (WP1-CTX-03)

| Identifier | Created by | Lifetime / reset rule | Propagated how | Notes |
|---|---|---|---|---|
| `trace_id` (W3C) | the first span of a request, which is `use_case.request` or the inbound HTTP span | one business request | `traceparent` header, A2A interceptor, MCP `_meta` | Mirrored as `gernas.trace_id` on `agent.run` for cross-system joins |
| `gernas.session_id` | the **channel** (API server or CLI), never an agent | a whole conversation, across many requests | W3C baggage | Copied onto every span by the baggage allow-list |
| `gernas.request_id` | the channel, per request | one request | baggage, plus the echoed `x-run-id` response header | |
| `gernas.agent_run_id` | `@trace_agent`, on every invocation | **new for every run**; never inherited | baggage `gernas.parent_run_id` (set by `inject_context` / delegation) | Format `run_<uuid4 hex>` |
| `gernas.parent_run_id` | the receiving `@trace_agent` | set only on delegated or nested child runs | read from the enclosing local run, otherwise from baggage | The parent's `agent_run_id` |
| `gernas.task_id` | the application | one work item; may be shared by several agents | baggage (allow-listed) | |
| `gernas.plan_id` / `gernas.step_id` | `@trace_planning` / `planning_step` | one plan | passed explicitly | |
| `gernas.hitl.ticket_id` | the approval store (`start_hitl`) | until `complete_hitl` or the timeout sweep | correlation table inside the process | Approver **role** only, never the person |
| `gernas.provenance_id`, `gernas.work_unit_id`, `gernas.commit_sha` | the deployment (`GERNAS_*` env) | one deployment | env, onto `agent.run` and the resource | Join keys (WP1-CTX-04) |

## Rules

- A child run gets its own `agent_run_id` and records the delegating run's id in `parent_run_id`. It shares the parent's `trace_id`.
- In-process nesting takes the parent from the active `RunState`, a ContextVar. Process and queue hops take it from baggage written by `inject_context`, `@trace_delegation` or `GernasA2AInterceptor`.
- **Orphan detection.** When a run receives `parent_run_id` but no valid parent span context arrives with it, the run is flagged:
  - `gernas.orphan_detected=true` is set and an `gernas.orphan_detected` event is added;
  - the counter `gernas.orphan_detected.total` is incremented;
  - a warning is logged.

  The typical cause is baggage that survived a hop while `traceparent` was stripped. A run given an explicit `parent_run_id` that is unknown in this process, and that has no remote parent, is also flagged.

Verified by:
- `tests/test_async_context.py`: queue hop, cross-process subprocess hop, both orphan cases.
- `tests/test_decorators.py::test_nested_agents_link_parent_run`.
- The live Agent Mesh trace in `docs/E2E_VALIDATION_REPORT.md`.
