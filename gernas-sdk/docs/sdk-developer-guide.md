# Gernas SDK developer guide

This guide is for Python engineers instrumenting an agent. You do not need to know OpenTelemetry to use it. You add decorators; the SDK creates spans, metrics and logs, and sends them to the GERNAS OTel Collector. The Collector forwards them to Opik and to the data plane.

```text
your agent ──@trace_*──▶ gernas SDK ──OTLP──▶ OTel Collector ──▶ Opik (developer view)
                                                         └──▶ data plane (dashboards, audit)
```

## 1. Setup — one call

```python
import gernas
gernas.init_gernas()          # reads GERNAS_* environment variables; safe to call twice
```

`GERNAS_OTLP_ENDPOINT` is required. A missing value raises a `KeyError` that names the variable. Everything else has a default; see `docs/onboarding-runbook.md` for the full table. Set `GERNAS_EXPORTER=console` while you develop if you have no Collector.

## 2. The rules the SDK enforces for you

- **Telemetry never breaks your agent.** A failed attribute write, a dead Collector or a bug in the SDK costs you a counted drop (`gernas.sdk.dropped_*`), never an exception.
- **PDPL.** The following never leave the process:
  - raw prompts and completions;
  - framework message payloads;
  - raw user identity.

  Inputs and outputs are recorded as SHA-256 fingerprints. Free text in exceptions, logs and unregistered attributes is PII-scrubbed: Emirates ID, IBAN, cards, e-mail, phone numbers and API keys.
- **One envelope everywhere.** Every span carries `gernas.business_domain`, `gernas.use_case_id`, `gernas.application_id`, `gernas.environment` and `gernas.operation`.
- **Use `gernas.Ctx` for attribute names**, for example `Ctx.TOOL_ID`, never the raw `"gernas.tool_id"` string. CI fails the build on hand-typed `gernas.*` strings.

## 3. The sixteen decorators

Every decorator works on sync functions, `async` functions, generators and async generators.

```python
import gernas
from gernas import Ctx
```

| # | Decorator | Span | Example |
|---|---|---|---|
| 1 | `trace_use_case` | `use_case.request` (root) | `@gernas.trace_use_case("UC-PRICING-001", channel="api", outcome_from=lambda r: "success" if r.ok else "failure")` |
| 2 | `trace_agent` | `agent.run` | `@gernas.trace_agent("price_assist", version="1.4.0", framework="agent_framework")` |
| 3 | `trace_llm` | `llm.call` | `@gernas.trace_llm("gpt-4o", provider="azure_openai", deployment_id="paygo-uae", prompt_template_id="answer", prompt_version="3", prompt_arg="prompt")` |
| 4 | `trace_stream` | `llm.stream` | `@gernas.trace_stream("gpt-4o", provider="azure_openai")` on an `async def ... yield chunk` |
| 5 | `trace_tool` | `tool.{tool_id}` | `@gernas.trace_tool("customer_profile", tool_version="2")` |
| 6 | `trace_mcp` | `mcp.call` | `@gernas.trace_mcp("datalayer", operation="tool", transport="streamable_http")` |
| 7 | `trace_delegation` | `agent.delegate` | `@gernas.trace_delegation(child_arg="name", protocol="a2a")` on `ask_remote(name, prompt)` |
| 8 | `trace_hitl` | `hitl.approval` | `@gernas.trace_hitl("submit_pricing_change", timeout_s=3600)` on the function that creates the ticket; later call `gernas.complete_hitl(ticket, "approved", approver_role="credit_officer")` |
| 9 | `trace_rag` | `rag.retrieve` | `@gernas.trace_rag(index_id="policies", strategy="hybrid")` on `search(query, top_k)` |
| 10 | `trace_retrieval` | `retrieval.search` | `@gernas.trace_retrieval(index_id="semantic_cache")` |
| 11 | `trace_embedding` | `embedding.create` | `@gernas.trace_embedding("bge-m3")` on `embed(texts)` |
| 12 | `trace_memory` | `memory.{operation}` | `@gernas.trace_memory("read", store="conversation", hit_from=lambda r: bool(r))` |
| 13 | `trace_planning` | `agent.plan` | `@gernas.trace_planning()` on `plan(goal, attempt=1) -> {"steps": [...]}`; then `with gernas.planning_step(plan_id, "s1", "tool_call"): ...` |
| 14 | `trace_chain` | `chain.run` (+ nested `llm.call`/`tool.*`) | `@gernas.trace_chain("explainer")` on a function that **builds** a LangChain runnable |
| 15 | `trace_guardrail` | `guardrail.check` | `@gernas.trace_guardrail("input_screen", decision_from=lambda r: ("allow" if r.ok else "deny", r.category))` |
| 16 | `trace_content_safety` | `content_safety.check` | `@gernas.trace_content_safety("pii", policy="output_redaction")` on a function that returns `{"blocked": bool, ...}` |

Behaviour worth knowing:

- **Status codes.**
  - A guardrail or content-safety *deny* keeps status OK; the decision is an attribute.
  - A tool's `PermissionError` is recorded as `auth_decision=deny`, not as an ERROR.
  - A cancelled stream records `stream_outcome=cancelled` with status UNSET. OTel has no CANCELLED status code.
- **Behaviour detection on `trace_agent`.** These are set automatically from the tool, MCP and LLM calls inside the run:
  - `termination_reason`: one of `completed`, `max_steps`, `timeout`, `policy_block`, `abandoned`, `error`, `human_rejected`;
  - `loop_detected`: the same tool with the same arguments was called `GERNAS_LOOP_THRESHOLD` times;
  - `max_step_violation`: steps exceeded `GERNAS_MAX_STEPS`;
  - `retry_count`: increment it with `gernas.record_retry()`.
- **Token usage and cost.** `trace_llm` reads token usage from OpenAI/Azure (`usage.prompt_tokens`), Anthropic (`usage.input_tokens`), Agent Framework (`usage_details`), LangChain (`usage_metadata`) and raw JSON. With `GERNAS_RATE_CARD_PATH` set it adds `gernas.est_cost_aed`.

## 4. Crossing boundaries

```python
headers = gernas.inject_context({}, session_id=session)       # producer: traceparent + baggage
with gernas.attach_headers(msg.headers):                       # consumer (queue, thread, process)
    handle(msg)

task = gernas.gernas_task(background_job())                    # asyncio task in the same trace
await gernas.to_thread(blocking_fn, arg)                       # thread pool, same trace
```

## 5. Framework adapters

| Framework | Use |
|---|---|
| Microsoft Agent Framework | `Agent(..., middleware=[*gernas.adapters.agent_framework.gernas_middleware("data_agent", provider="groq", mcp_server_id="datalayer")])` |
| A2A client | `A2AAgent(..., auth_interceptor=gernas.adapters.a2a.GernasA2AInterceptor())` |
| Starlette / FastAPI / any ASGI | `app.add_middleware(gernas.adapters.asgi.GernasASGIMiddleware, channel="api")` |
| httpx | `httpx.AsyncClient(event_hooks=gernas.adapters.httpx.async_event_hooks())` |
| fastmcp server | `mcp.add_middleware(gernas.adapters.mcp.GernasFastMCPMiddleware("datalayer"))` |
| mcp (low-level) server | `with gernas.adapters.mcp.mcp_meta_context(ctx): ...` |
| LangChain | `@gernas.trace_chain` or `callbacks=[gernas.adapters.langchain.GernasLangChainCallback()]` |

## 6. Checking your work

```powershell
gernas doctor --opik                                    # config, Collector, test span, 13 context fields, Opik
gernas conformance --file collector\out\dataplane-traces.json --service my-agent --require-op agent.run
```

The reference agent in `examples/reference-agent/main.py` uses all sixteen decorators in one connected trace.
