# Gernas SDK developer guide

This guide is for Python engineers instrumenting an agent. You do not need to know OpenTelemetry. You add decorators (or one framework adapter); the SDK creates spans, metrics and logs that follow the GERNAS Enterprise Telemetry Blueprint, and sends them to the GERNAS OTel Collector. The Collector forwards them to Opik and to the data plane.

```text
your agent ──@trace_* / adapter──▶ gernas SDK ──OTLP──▶ OTel Collector (AKS) ──▶ Opik (developer trace view)
                                                                          └──▶ Azure Data Explorer (data plane)
```

The same span and attribute model is produced for Microsoft Agent Framework, LangChain, LangGraph and plain Python.

## 1. Setup — one call

```python
import gernas
gernas.init_gernas()          # reads GERNAS_* environment variables; safe to call twice
```

These variables are mandatory. A missing one raises `gernas.GernasConfigError`, which names every missing variable; the SDK never invents identity:
- `GERNAS_SERVICE_NAME`
- `GERNAS_USE_CASE_ID`
- `GERNAS_APPLICATION_ID`
- `GERNAS_BUSINESS_DOMAIN`
- `GERNAS_ENVIRONMENT`
- `GERNAS_OTLP_ENDPOINT` (only when `GERNAS_EXPORTER=otlp`, the default)

Use `GERNAS_EXPORTER=console` during development without a Collector, or `off` to disable export. The full variable table is in `docs/onboarding-runbook.md`.

## 2. What the SDK guarantees

- **Telemetry never breaks your agent.** A failed attribute write, a dead Collector or a bug in the SDK costs a counted drop (`gernas.sdk.dropped_*`), never an exception.
- **No payloads, no secrets.**
  - Prompts, completions, tool arguments and results, and retrieved content are never recorded; only their sizes (`request_size_bytes`, `response_size_bytes`, `memory_size_bytes`).
  - The raw user id is never recorded. `gernas.request_context(user_id=...)` emits `tokenized_user_id` (HMAC-SHA256 with `GERNAS_USER_TOKEN_KEY`), or nothing if the key is not set.
  - Free text in exceptions, logs and unregistered attributes is PII-scrubbed; registered values are scrubbed for credentials.
  - String values are capped at 1024 characters.
- **One envelope everywhere.** Every span carries `gernas.business_domain`, `gernas.use_case_id`, `gernas.application_id`, `gernas.environment`, `gernas.operation` and `gernas.execution_status`. Every span inside an agent run also carries `gernas.agent_id` and `gernas.agent_run_id`. Failures add `gernas.error_category` (timeout, rate_limit, auth, policy, validation, dependency, cancelled, internal).
- **Attribute names come from `gernas.Ctx`.** They are generated from `registry/`, and the blueprint field names are used verbatim, for example `Ctx.TOKEN_INPUT` → `gernas.token_input`. CI fails on hand-typed `gernas.*` strings.

## 3. Decorators

Every decorator works on sync functions, `async` functions, generators and async generators.

| Decorator | Span | Records (beyond the envelope) |
|---|---|---|
| `trace_use_case(use_case_id=None, *, channel, request_type, outcome_from)` | `use_case.request` (root, SERVER) | channel, request_type, business_outcome |
| `trace_agent(agent_id, *, agent_type, name, version, framework="custom")` | `agent.run` | agent context, termination_reason, step/retry counts, loop and max-step flags, parent_run_id, delegation_path |
| `trace_step(step_id, *, step_type, attempt_number=1, plan_id)` | `agent.step` | the "Step" level of the drill-down: workflow executor, graph node, plan step |
| `trace_planning(*, plan_id, attempt_arg="attempt", steps_from)` | `agent.plan` | plan_id, attempt_number, plan_step_count |
| `trace_delegation(child_agent_id, *, child_arg, headers_arg)` | `agent.delegate` | parent/child agent, delegation path; injects trace headers into a `headers`/`carrier` dict |
| `trace_llm(model_id, *, provider, deployment_id, prompt_template_id, prompt_version, usage_from, stream)` | `llm.call` | tokens (input/output/cached), estimated_cost, stop_reason; streams add ttft_ms and tokens_per_second |
| `trace_embedding(model_id, *, provider, deployment_id, input_arg="texts")` | `embedding.create` | batch_size, token_input, estimated_cost |
| `trace_tool(tool_id, *, tool_type="function", tool_version, api_operation, dependency, authorization_decision)` | `tool.call` | tool context, payload sizes, authorization_decision. MCP: `tool_type="mcp", dependency="<server>"` |
| `trace_retrieval(*, index_id, top_k, strategy, knowledge_domain, query_rewritten, results_from)` | `retrieval.search` | result_count, empty_result, ranking_score, source_id |
| `trace_memory("read" \| "write", *, hit_from, stale_from)` | `memory.access` | memory_hit, memory_stale, memory_size_bytes |
| `trace_guardrail(policy_id, *, risk_classification, decision_from, blocks_run=True)` | `guardrail.check` | policy_decision (allow/deny/warn/escalate), risk_classification |
| `trace_content_safety(category, *, policy, risk_level, result_from)` | `content_safety.check` | content_blocked, safety_category, risk_level, policy_triggered |
| `trace_hitl(action, *, timeout_s, ticket_from, outcome_from)` | `hitl.approval` | ticket id, action, outcome, approver role |

Helpers:

| Helper | Use |
|---|---|
| `start_hitl` / `complete_hitl` | Long-lived approvals that are not held in context |
| `record_retry()` | Counts a retry against the active run |
| `set_termination_reason(...)` | Sets the run's termination reason |
| `mark_fallback()` | Marks the current model call as served by a fallback |
| `annotate({Ctx.X: value})` | Sets registry attributes on the current span |
| `correlation()` | `trace_id`, `span_id`, `agent_id`, `agent_run_id`, `session_id`, `task_id` for application log and audit records (read-only) |

Status rules:
- A guardrail or content-safety *deny* keeps status OK, and the decision is an attribute.
- A tool's auth failure is `authorization_decision=deny`, not ERROR.
- Cancellation is `execution_status=cancelled` with status UNSET.
- Max-step violations end the run with status ERROR.

## 4. Consumer examples

These are runnable versions of the examples in `examples/`. They work offline.

**Agent run, LLM call, tool call, exception, async:**

```python
import asyncio, gernas

@gernas.trace_llm("gpt-4o", provider="azure_openai", prompt_template_id="answer", prompt_version="3")
async def call_model(messages): ...                         # returns the provider response

@gernas.trace_tool("fx_rates", tool_type="api", dependency="treasury")
async def get_rate(currency): ...

@gernas.trace_agent("pricing_assistant", agent_type="orchestrator")
async def run(question):
    rate = await get_rate("USD")                            # tool.call, child of agent.run
    try:
        return await call_model([{"role": "user", "content": question}])
    except TimeoutError:                                    # llm.call: status ERROR, error_category=timeout
        gernas.record_retry()
        return await call_model([{"role": "user", "content": question}])

gernas.init_gernas()
with gernas.request_context(session_id="s-1", user_id="u-42", principal_type="human"):
    asyncio.run(run("What is the FX margin?"))
```

**Nested agent, through delegation:**

```python
@gernas.trace_agent("rag_agent", agent_type="worker")
async def rag_agent(q): ...

@gernas.trace_delegation("rag_agent")
async def ask_rag(q):
    return await rag_agent(q)           # child run: parent_run_id + delegation_path "pricing_assistant>rag_agent"
```

Across processes, the A2A interceptor (or `gernas.inject_context(headers)` on the caller and `gernas.attach_headers(headers)` on the callee) carries the trace, the parent run id and the delegation path.

**Workflow / graph steps:**

```python
@gernas.trace_step("input_guardrail", step_type="workflow_executor")
async def input_guardrail(state): ...
```

**Microsoft Agent Framework:**

```python
from gernas.adapters.agent_framework import gernas_middleware
agent = Agent(client, name="DataAgent", tools=[...],
              middleware=gernas_middleware("data_agent", agent_type="worker", provider="azure_openai",
                                           mcp_server="datalayer"))   # agent.run + llm.call + tool.call(mcp)
```

Pass `trace_functions=False` when the tools already carry `@gernas.trace_tool`, to avoid two tool spans per call. Call `gernas.adapters.agent_framework.disable_native_telemetry()` once at startup so MAF's own `invoke_agent` / `chat` / `execute_tool` / `workflow.*` spans (a second instrumentation path that can capture prompts) are not emitted. For A2A, set `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false` before `a2a` is imported (the a2a-sdk decides at import time).

**LangChain:**

```python
from gernas.adapters.langchain import instrument
chain = instrument(prompt | llm, agent_id="faq_chain", agent_type="worker", provider="azure_openai")
chain.invoke({"question": "..."})       # agent.run → llm.call / tool.call / retrieval.search
```

**LangGraph:**

```python
from gernas.adapters.langgraph import instrument_graph
app = instrument_graph(builder.compile(), agent_id="pricing_graph", agent_type="orchestrator")
app.invoke(state)                       # agent.run → agent.step per node → llm.call ...
```

If a chain or graph is invoked inside an existing `@gernas.trace_agent` run, the adapter reuses that run and does not open a second `agent.run`.

## 5. Crossing boundaries

```python
headers = gernas.inject_context({})                  # producer: traceparent + baggage (+ parent run, path)
with gernas.attach_headers(msg.headers):             # consumer (queue, process, MCP server)
    handle(msg)

task = gernas.gernas_task(background_job())          # asyncio task in the same trace and run
threading.Thread(target=gernas.wrap_in_context(fn))  # thread in the same trace and run
```

| Transport | Adapter |
|---|---|
| A2A client | `A2AAgent(..., auth_interceptor=gernas.adapters.a2a.GernasA2AInterceptor())` |
| Starlette / FastAPI / any ASGI | `app.add_middleware(gernas.adapters.asgi.GernasASGIMiddleware, channel="api")` |
| httpx | `httpx.AsyncClient(event_hooks=gernas.adapters.httpx.async_event_hooks())` |
| fastmcp server | `mcp.add_middleware(gernas.adapters.mcp.GernasFastMCPMiddleware("datalayer"))` |
| mcp (low-level) server | `with gernas.adapters.mcp.mcp_meta_context(ctx): ...` |

## 6. Checking your work

```powershell
gernas doctor                     # config, Collector reachability, test span + context fields (--opik: Opik delivery)
gernas conformance --file collector\out\dataplane-traces.json --require-op agent.run
```

`conformance` checks the following:
- required attributes per span;
- run context inside runs;
- duplicate emission;
- unregistered `gernas.*` keys;
- denylisted keys;
- enum values;
- cardinality.
