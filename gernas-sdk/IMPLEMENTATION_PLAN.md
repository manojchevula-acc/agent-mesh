# IMPLEMENTATION_PLAN.md (status-tracked copy)

> Source: `../md_files/IMPLEMENTATION_PLAN_WP1.md` (unchanged task IDs, acceptance criteria and sprints).
> Status column updated 2026-10-16 after the Beacon Kit → Gernas SDK pass; evidence per row in
> `docs/REQUIREMENTS_TRACEABILITY.md` and `docs/E2E_VALIDATION_REPORT.md`. Tier 1–4 (WP1·C) rows remain planned:
> Agent Mesh is the instrumented reference application, not the 24 platform components.

# IMPLEMENTATION_PLAN.md
## GERNAS POD2 — WP1 Agent Telemetry SDK

> Authoritative implementation roadmap for BE1.
> Do not modify task IDs, acceptance criteria, or sprint assignments without programme lead approval.
> WP2 (BE2) and WP3 (UI) entries to be added when WP1·A is in flight.
> Status values: `[ ] Planned` · `[→] In progress` · `[✓] Complete` · `[!] Blocked`

---

## Task Overview

| Task ID | Sprint | Block | Description | Days | Status |
|---|---|---|---|---|---|
| WP1-CORE-01 | S0–S1 | A | Core SDK foundation | 7 | [✓] |
| WP1-CORE-02a | S1 | A | Decorators — Simple batch 1 (@trace_agent, @trace_llm) | 3 | [✓] |
| WP1-CORE-02b | S2 | A | Decorators — Simple/Moderate/Complex batch 2 (@trace_tool, @trace_mcp, @trace_stream, @trace_hitl) | 7 | [✓] |
| WP1-CORE-02c | S3 | A | Decorators — Moderate batch 3 (@trace_rag, @trace_retrieval, @trace_embedding, @trace_memory, @trace_planning) | 7 | [→] publish pending |
| WP1-CORE-02d | S4 | A | Decorators — Final batch (@trace_chain, @trace_use_case, @trace_delegation, @trace_guardrail, @trace_content_safety) | 7 | [✓] |
| WP1-CORE-03 | S2–S3 | A | Behavioural detection (loop, runaway, retry, timeout) | 6 | [✓] |
| WP1-CORE-04 | S2 | A | HITL + prompt registry | 4 | [✓] |
| WP1-CORE-05 | S2 | A | Streaming, async, generators | 5 | [✓] |
| WP1-CORE-06 | S1–S3 | A | Dual-emit adapter (Opik + data plane fan-out) | 6 | [✓] |
| WP1-CORE-07 | S3 | A | Tests + overhead benchmark | 8 | [✓] |
| WP1-CORE-08 | S11 | A | Packaging, docs, reference agent | 6 | [→] publish pending |
| WP1-CTX-01 | S4–S5 | B | Attribute registry (16 domains) | 24 | [→] sign-off R5 |
| WP1-CTX-02 | S5 | B | Convention files + code constants (generated) | 6 | [✓] |
| WP1-CTX-03 | S4 | B | Identifier lifecycle rules | 6 | [✓] |
| WP1-CTX-04 | S4 | B | Cross-system correlation keys | 3 | [→] R1 |
| WP1-CTX-05 | S5 | B | Classification + redaction matrix | 3 | [→] sign-off R3 |
| WP1-CTX-06 | S5 | B | Cardinality governance | 3 | [→] R1 |
| WP1-CTX-07 | S5 | B | Version policy + conformance suite | 3 | [→] CI template |
| WP1-T1-01 | S5 | C | Tier 1 — Use Case Layer | 5 | [ ] |
| WP1-T1-02 | S5 | C | Tier 1 — GERNAS OS | 5 | [ ] |
| WP1-T1-03 | S5 | C | Tier 1 — Agent Runtime | 5 | [ ] |
| WP1-T1-04 | S6 | C | Tier 1 — Multi-Agent Orchestration | 5 | [ ] |
| WP1-T1-05 | S6 | C | Tier 1 — Skills/Tools | 5 | [ ] |
| WP1-T1-06 | S6 | C | Tier 1 — API Gateway | 5 | [ ] |
| WP1-T2-01 | S6 | C | Tier 2 — LLM Gateway | 4 | [ ] |
| WP1-T2-02 | S6 | C | Tier 2 — LLMs | 4 | [ ] |
| WP1-T2-03 | S6 | C | Tier 2 — Embedding Services | 4 | [ ] |
| WP1-T2-04 | S7 | C | Tier 2 — Content Safety | 4 | [ ] |
| WP1-T2-05 | S7 | C | Tier 2 — RAG/Search | 4 | [ ] |
| WP1-T2-06 | S7 | C | Tier 2 — Vector Store | 4 | [ ] |
| WP1-T2-07 | S7 | C | Tier 2 — Document Processing | 4 | [ ] |
| WP1-T2-08 | S7 | C | Tier 2 — Memory | 4 | [ ] |
| WP1-T2-09 | S7 | C | Tier 2 — Knowledge Layer ⚠ NOT COSTED | 0 | [!] |
| WP1-T3-01 | S8 | C | Tier 3 — Database (SQLAlchemy) | 2 | [ ] |
| WP1-T3-02 | S8 | C | Tier 3 — AKS/Containers | 2 | [ ] |
| WP1-T3-03 | S8 | C | Tier 3 — GPU (DCGM) | 2 | [ ] |
| WP1-T3-04 | S8 | C | Tier 3 — Messaging (Service Bus) | 2 | [ ] |
| WP1-T3-05 | S8 | C | Tier 3 — Network (Network Watcher) | 2 | [ ] |
| WP1-T4-01 | S9 | C | Tier 4 — Identity/AAD | 2 | [ ] |
| WP1-T4-02 | S9 | C | Tier 4 — Authorization/OPA | 2 | [ ] |
| WP1-T4-03 | S9 | C | Tier 4 — CI/CD/GitLab | 2 | [ ] |
| WP1-T4-04 | S9 | C | Tier 4 — Telemetry Platform self-monitoring | 2 | [ ] |
| WP1-PTU-01 | S9 | D | PTU telemetry (Azure Monitor Metrics API) | TBC | [!] R4 |
| WP1-E2E-01 | S8 | E | E2E integration test plan design | 2 | [ ] |
| WP1-E2E-02 | S10 | E | E2E integration test execution (all 24 components) | 5 | [ ] local E2E done |
| WP1-E2E-03 | S10 | E | Performance/load test at 580M span/year | 3 | [ ] local bench done |
| WP1-DOCS-01 | S10–S11 | E | SDK developer guide, attribute reference, onboarding runbook, ADRs | 6 | [✓] |

---

## WP1·A — SDK Core

---

### WP1-CORE-01 · Core SDK Foundation
**Sprint:** S0–S1 · **Days:** 7 · **Block:** A

**Description:** The engine underneath everything else. TracerProvider setup, exporter abstraction, context propagation across async/thread/queue boundaries, and graceful degradation when the Collector is unreachable.

**Dependencies:** `pyproject.toml` with pinned OTel SDK version; AKS dev namespace reachable (for S1 E2E verification).

**Affected files:**
- `pyproject.toml`
- `gernas/__init__.py`
- `gernas/config.py`
- `gernas/tracer.py`
- `gernas/exporters.py`
- `tests/test_core.py`
- `tests/conftest.py`

**Steps:**

**Step 1 — Package scaffold and `pyproject.toml`**
Create the package structure. Pin `opentelemetry-sdk>=1.24.0`, `opentelemetry-exporter-otlp-proto-grpc>=1.24.0`, `opentelemetry-exporter-otlp-proto-http>=1.24.0`, `opentelemetry-semantic-conventions>=0.45b0`. Add dev extras: `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=5.0`, `mypy>=1.8`, `ruff>=0.4`. Verify `pip install -e .[dev]` succeeds.

**Step 2 — `GernasConfig` in `gernas/config.py`**
Env-var-driven dataclass. All settings from environment — no defaults for required fields. Required: `GERNAS_OTLP_ENDPOINT`. Optional with safe defaults: `GERNAS_SERVICE_NAME`, `GERNAS_SERVICE_VERSION`, `GERNAS_USE_CASE_ID`, `GERNAS_OTLP_API_KEY`. Property `resource` returns a `Resource` with service name, version, and use-case-id. Test: missing required env var raises `KeyError` with a meaningful message.

**Step 3 — `init_gernas()` in `gernas/tracer.py`**
Single-call idempotent initialisation. Wires `TracerProvider` + `BatchSpanProcessor` + `OTLPSpanExporter` (gRPC by default, HTTP optional via `GERNAS_OTLP_TRANSPORT=http`). Sets the global tracer provider. Second call returns existing provider without reinitialising. Test: calling `init_gernas()` twice does not create two providers.

**Step 4 — Exporter abstraction in `gernas/exporters.py`**
Factory function that returns the correct `SpanExporter` based on env config. Accepts gRPC and HTTP. Destination is the Collector, never directly to Opik or a backend. Test: correct exporter type instantiated per transport env var.

**Step 5 — Graceful degradation — `safe_set()` helper**
`safe_set(span, key, value)` wraps `span.set_attribute(key, value)` in try/except. On exception, increments `gernas.sdk.dropped_attributes` counter and re-raises nothing. Export every `safe_set` usage through this helper — never call `span.set_attribute()` directly in decorator code. Test: `safe_set()` on a finished span does not raise.

**Step 6 — Context propagation across async boundaries**
Implement `inject_context(headers: dict) -> dict` and `extract_context(headers: dict) -> Context` using OTel's W3C TraceContext propagator. These are the primitives for queue hand-offs. Test: context round-trips through a dict of headers with correct trace-id and span-id.

**Step 7 — Collector-down test**
Start `init_gernas()` with a non-existent Collector endpoint. Run 100 span operations. Assert: zero exceptions raised to caller, `BatchSpanProcessor` internal drop counter is > 0 (accessible via the processor's `_spans_dropped` or equivalent), agent code sees no errors.

**Step 8 — Thread/queue context propagation produces a single connected record**
Spawn a background thread; extract context in the parent, pass via a dict, re-attach in the child thread. Assert child span's `trace_id` matches parent's. This is the proof that queue hand-offs will work.

**Acceptance criteria (from briefing notes "Done when"):**
> One startup call sets everything up from environment settings alone; with the collector switched off the agent reports no errors and we get a count of what was dropped; and a job that moves between threads or through a queue still comes out as one connected record.

---

### WP1-CORE-02a · Decorators — Batch 1: @trace_agent, @trace_llm (Moderate, 1.5d each)
**Sprint:** S1 · **Days:** 3 · **Block:** A

**Dependencies:** WP1-CORE-01 complete (TracerProvider must be live).

**Affected files:** `gernas/decorators.py`, `gernas/attributes.py`, `tests/test_decorators.py`

**Decorator complexity — Moderate:** must read the response object after the call returns to extract token counts, finish reason, and cost.

**Steps:**

**Step 1 — `gernas/attributes.py` — Ctx class skeleton**
Define `Ctx` class with all canonical attribute name constants. This is the only permitted way to reference attribute names in decorator code. Includes `gen_ai.*` (OTel GenAI semconv) and `gernas.*` namespaces. Lint rule enforced later (WP1-CTX-02); for now the class establishes the pattern.

**Step 2 — `@trace_agent` — sync/async dual-mode**
Decorator factory. Detects sync vs async function at decoration time (`inspect.iscoroutinefunction`). Span name: `agent.run`. Required attributes (on entry): `Ctx.AGENT_ID`, `Ctx.AGENT_NAME`, `Ctx.AGENT_VERSION`, `Ctx.USE_CASE_ID`, `Ctx.FRAMEWORK`. On exit (success): `Ctx.TERMINATION_REASON = "completed"`. On exception: span status ERROR + `record_exception()` + `Ctx.TERMINATION_REASON = "error"`. Uses `safe_set()` for every attribute. Test: happy path attributes present; exception path status is ERROR.

**Step 3 — `@trace_llm` — response introspection**
Span name: `llm.call`. Attributes on entry: `Ctx.MODEL_ID`, `Ctx.PROVIDER`, `Ctx.DEPLOYMENT_ID`. After call returns, inspect response for token usage using duck-typed access (`getattr(resp.usage, "prompt_tokens", None)` and `getattr(resp.usage, "input_tokens", None)` to handle both Azure OpenAI and Anthropic shapes). Set `Ctx.INPUT_TOKENS`, `Ctx.OUTPUT_TOKENS`, `Ctx.FINISH_REASON`. Never set `Ctx.PROMPT` or `Ctx.COMPLETION` — PDPL default is drop. Test: Azure OpenAI token shape produces correct attributes; Anthropic token shape produces correct attributes; missing usage field does not raise.

**Step 4 — Cost enrichment on `@trace_llm`**
Read rate card from `GERNAS_RATE_CARD_PATH` env var (JSON file). Look up `(model_id, provider)` → `(input_rate, output_rate)` per 1K tokens. Compute `gernas.est_cost_aed`. If rate card absent or lookup fails: skip without error. Test: known model_id produces a positive cost value; unknown model_id produces no cost attribute and no exception.

**Step 5 — OTLP attribute envelope — canonical context injection**
Modify `@trace_agent` to inject the canonical context envelope on every span: `Ctx.BUSINESS_DOMAIN`, `Ctx.USE_CASE_ID`, `Ctx.APPLICATION_ID`, `Ctx.ENVIRONMENT` — all from env vars via `GernasConfig`. This is the pattern for all 16 decorators — envelope first, then operation-specific attributes.

**Step 6 — E2E verification**
With Collector running in dev, decorate a minimal reference agent, invoke it, confirm the span appears in Opik with all required attributes. This is the S1 milestone: `@trace_agent + @trace_llm emitting PDPL-redacted spans to Opik end-to-end`.

**Acceptance criteria (partial — full criteria after all 16 decorators):**
> Spans verified end-to-end: reference agent → Collector → Opik UI. Both decorators emit correct canonical context envelope. No raw prompt or completion captured by default.

---

### WP1-CORE-02b · Decorators — Batch 2: @trace_tool, @trace_mcp, @trace_stream, @trace_hitl
**Sprint:** S2 · **Days:** 7 · **Block:** A

**Dependencies:** WP1-CORE-02a, WP1-CORE-05 (streaming/async patterns informed by this).

**Affected files:** `gernas/decorators.py`, `tests/test_decorators.py`, `tests/test_streaming.py`

**Complexity mix:** @trace_tool (Simple 1d), @trace_mcp (Simple 1d), @trace_stream (Complex 2d), @trace_hitl (Complex 2d). Plus collector outage test (1d).

**Steps:**

**Step 1 — `@trace_tool` (Simple)**
Span name: `tool.{tool_id}`. Attributes: `Ctx.TOOL_ID`, `Ctx.TOOL_TYPE`, `Ctx.TOOL_VERSION`, `Ctx.AUTH_DECISION`. Input/output captured as content hash only — never raw values. Error: `record_exception()` + status ERROR. Test: happy path; auth denial recorded as attribute, not error; exception path.

**Step 2 — `@trace_mcp` (Simple)**
Span name: `mcp.call`. Attributes: `Ctx.MCP_SERVER_ID`, `Ctx.MCP_OPERATION` (tool/resource/prompt), `Ctx.MCP_TOOL_NAME`. Transport information as attribute, not in span name (avoid cardinality). Never capture MCP payloads by default. Test: tool call, resource access, and prompt retrieval each produce correct operation attribute.

**Step 3 — `@trace_stream` Step 1: generator span lifecycle**
Manual span start before first `yield`. Span held open for life of the generator. Close in `finally` block — this runs on exhaust, error, and client disconnect/GC. Span name: `llm.stream`. Test: complete stream → span ends with OK status. Abandoned mid-stream → span ends with CANCELLED status. Error mid-stream → span ends with ERROR status.

**Step 4 — `@trace_stream` Step 2: TTFT and throughput measurement**
On first chunk: record `Ctx.TTFT_MS = now() - span_start_ms`. On exhaust: record `Ctx.TOKENS_PER_SEC` from output token count and elapsed time. Test: TTFT attribute present and positive on complete stream; absent when stream abandoned before first chunk.

**Step 5 — `@trace_hitl` Step 1: long-lived span lifecycle**
Span opened at the start of the human approval wait. Must survive for hours without holding a connection or consuming memory proportional to wait time. Use `span.start_span()` not `start_as_current_span()` — do not hold the context manager open. Record the span object reference, close it explicitly when the approval resolves.

**Step 6 — `@trace_hitl` Step 2: outcome capture**
On approval resolution, emit: `Ctx.HITL_OUTCOME` (approved/rejected/changed/timed_out), `Ctx.HITL_WAITED_MS`, `Ctx.HITL_APPROVER_ROLE` (role, never individual identity). On timeout: outcome = `timed_out`, span closes with a distinctive status. Test: each outcome value is recorded correctly; timeout distinguished from rejection.

**Step 7 — Collector outage test**
Disable the Collector endpoint. Run reference agent through all four batch-2 decorators. Assert: agent produces zero exceptions, zero test failures. Confirm `dropped_spans` counter is nonzero.

**Acceptance criteria (partial):**
> @trace_stream: abandoning a stream halfway closes the recording as cancelled. @trace_hitl: an approval that timed out can be told apart from one that was refused. Collector outage: agent executes successfully, dropped_spans counter increments.

---

### WP1-CORE-02c · Decorators — Batch 3: @trace_rag, @trace_retrieval, @trace_embedding, @trace_memory, @trace_planning
**Sprint:** S3 · **Days:** 7 · **Block:** A

**Dependencies:** WP1-CORE-02a complete.

**Complexity:** All Moderate (1.5d each, round to 7d across 5).

**Steps (apply standard Moderate pattern to each):**

**Step 1 — `@trace_rag`** Span: `rag.retrieve`. Attributes: `Ctx.RETRIEVAL_DURATION_MS`, `Ctx.TOP_K`, `Ctx.RESULT_COUNT`, `Ctx.EMPTY_RESULT` (bool), `Ctx.QUERY_REWRITTEN` (bool). Query text: hash only, never raw. Inspect result to get count — if result is None or empty list, set `Ctx.EMPTY_RESULT = True`. Test: empty result vs non-empty produces distinct attribute value.

**Step 2 — `@trace_retrieval`** Span: `retrieval.search`. Attributes: `Ctx.QUERY_HASH`, `Ctx.RESULT_COUNT`, `Ctx.RANKING_SCORE` (first result, if available). Test: correct attributes on results; missing ranking score does not raise.

**Step 3 — `@trace_embedding`** Span: `embedding.create`. Attributes: `Ctx.EMBED_MODEL`, `Ctx.EMBED_TOKEN_COUNT`, `Ctx.EMBED_BATCH_SIZE`, `Ctx.EMBED_LATENCY_MS`. Test: batch of 10 texts produces batch_size=10.

**Step 4 — `@trace_memory`** Span: `memory.{operation}` where operation is read or write (from decorator param). Attributes: `Ctx.MEMORY_HIT` (bool, read ops only), `Ctx.MEMORY_STALE` (bool), `Ctx.MEMORY_SIZE_BYTES`. Test: read hit vs miss produces distinct memory_hit attribute.

**Step 5 — `@trace_planning`** Span: `agent.plan`. Attributes: `Ctx.PLAN_ID`, `Ctx.STEP_ID`, `Ctx.STEP_TYPE`, `Ctx.ATTEMPT_NUMBER`, `Ctx.PLANNING_LATENCY_MS`. Test: multi-step plan produces correct step count in parent span attribute.

**Step 6 — SDK v1.0 milestone**
Run full test suite across all decorators built so far (11/16). Publish to FAB GitLab package registry. Run p95 benchmark: must be < 1ms/span. Conformance fixture in GitLab CI blocks merge if mandatory attributes missing.

**Acceptance criteria (partial):**
> SDK v1.0 published to FAB GitLab package registry; p95 benchmark signed off (< 1ms/span); conformance test suite blocks merge if mandatory attributes missing.

---

### WP1-CORE-02d · Decorators — Batch 4: @trace_chain, @trace_use_case, @trace_delegation, @trace_guardrail, @trace_content_safety
**Sprint:** S4 · **Days:** 7 · **Block:** A

**Complexity:** @trace_chain (Moderate 1.5d), @trace_use_case (Simple 1d), @trace_delegation (Complex 2d), @trace_guardrail (Simple 1d), @trace_content_safety (Simple 1d). Total ≈ 6.5d rounded to 7d.

**Steps:**

**Step 1 — `@trace_chain` (LangChain callback integration)**
Implement `GernasLangChainCallback(BaseCallbackHandler)` hooking `on_chain_start`, `on_chain_end`, `on_llm_start`, `on_llm_end`. Parent-child span relationships via OTel context stored per `run_id`. `@trace_chain` decorator wraps the chain construction call. Test: nested chain produces correct parent-child hierarchy.

**Step 2 — `@trace_use_case`**
Span: `use_case.request`. Binds `Ctx.USE_CASE_ID`, `Ctx.APPLICATION_ID`, `Ctx.CHANNEL`, `Ctx.BUSINESS_DOMAIN` from env vars at span start. This is the outermost span in a trace — all agent spans are children. Test: use_case span is the root span; all child spans inherit trace_id.

**Step 3 — `@trace_delegation` Step 1: parent span and child linkage**
Span: `agent.delegate`. Attributes on parent: `Ctx.PARENT_AGENT_ID`, `Ctx.CHILD_AGENT_ID`, `Ctx.DELEGATION_LATENCY_MS`. Child agent receives context via `inject_context()` / `extract_context()`. Child span sets `Ctx.PARENT_RUN_ID = parent.agent_run_id` and generates its own `Ctx.AGENT_RUN_ID`. Test: child span trace_id matches parent; child has its own span_id.

**Step 4 — `@trace_delegation` Step 2: async cross-process delegation**
Context travels in message headers (see WP1-CORE-01 Step 6). After context extraction, child span created with correct remote parent. Test: context extracted from headers produces span with correct trace_id even with no active local span.

**Step 5 — `@trace_guardrail`**
Span: `guardrail.check`. Attributes: `Ctx.POLICY_ID`, `Ctx.POLICY_DECISION` (allow/deny), `Ctx.RISK_CLASSIFICATION`. On deny: span status OK (this is expected behaviour, not an error). Test: deny decision does not produce ERROR span status.

**Step 6 — `@trace_content_safety`**
Span: `content_safety.check`. Attributes: `Ctx.SAFETY_CATEGORY`, `Ctx.RISK_LEVEL`, `Ctx.POLICY_TRIGGERED`. On block: `Ctx.CONTENT_BLOCKED = True`. Test: blocked request produces content_blocked=True with correct category.

**Step 7 — All 16 decorators milestone**
Run the full decorator test suite (all 16). Zero failures. All mandatory canonical context attributes present on every span. Reference agent uses all 16 decorators in a single trace.

**Acceptance criteria (full "Done when"):**
> All sixteen are published with tests; a demonstration agent uses every one of them; the measured slowdown is inside the agreed limit; and no recording is left hanging open after a cancellation, timeout or error.

---

### WP1-CORE-03 · Behavioural Detection
**Sprint:** S2–S3 · **Days:** 6 · **Block:** A (folded into decorator builds)

**Description:** SDK-level per-run state tracking for loop detection, runaway step violation, retry counting, and timeout differentiation. Built as a `RunState` class used by `@trace_agent`.

**Affected files:** `gernas/run_state.py`, `gernas/decorators.py`, `tests/test_run_state.py`

**Steps:**

**Step 1 — `RunState` class**
`RunState` holds: `steps: int`, `tool_calls: Counter[tuple[str, str]]`, `retry_count: int`, `timed_out: bool`. Created per `@trace_agent` invocation, not shared. Not stored globally.

**Step 2 — Loop detection**
In `on_tool_start(run, tool_id, args_hash)`: increment `run.tool_calls[(tool_id, args_hash)]`. If count >= `cfg.loop_threshold` (from `GERNAS_LOOP_THRESHOLD` env var, default 3): set `Ctx.LOOP_DETECTED = True`, `Ctx.LOOP_REPEAT_COUNT = count` on the agent span. Test: tool called 3 times with same args triggers loop flag; different args do not.

**Step 3 — Max-step violation**
After each step: `run.steps += 1`. If `run.steps > cfg.max_steps` (from `GERNAS_MAX_STEPS` env var): set `Ctx.MAX_STEP_VIOLATION = True`, set span status ERROR with reason `max_steps`. Test: agent exceeding max_steps gets flagged and marked as failed.

**Step 4 — Retry counting**
Implement `run.increment_retry()`. Retries counted across the whole run, not per-tool. Set `Ctx.RETRY_COUNT = run.retry_count` on span close. Test: 5 retries across 3 different tools produces retry_count=5.

**Step 5 — Timeout vs slow differentiation**
On `asyncio.TimeoutError` or `concurrent.futures.TimeoutError` in the agent wrapper: set `Ctx.TIMED_OUT = True`, `Ctx.TERMINATION_REASON = "timeout"`. On other exceptions: `TERMINATION_REASON = "error"`. Slow-but-completing run: no timeout attribute. Test: each termination reason produces the correct attribute value.

**Acceptance criteria:**
> A test agent built to loop raises the flag within the set threshold; a runaway agent is flagged and its run marked as failed; and retry attempts are numbered correctly right across a run.

---

### WP1-CORE-04 · HITL + Prompt Registry
**Sprint:** S2 · **Days:** 4 · **Block:** A

**Description:** Two gaps identified in gap analysis: human approval spans that survive hours without holding a connection, and prompt provenance (template id + version + SHA-256 hash).

**Affected files:** `gernas/decorators.py`, `gernas/prompt_registry.py`, `tests/test_hitl.py`

**Steps:**

**Step 1 — Long-lived span without connection hold**
`@trace_hitl` opens a span using `tracer.start_span()` (not context manager). Stores span reference in a correlation dict keyed by approval ticket ID. Returns immediately — no blocking. When the approval resolves (via callback or polling), caller invokes `complete_hitl(ticket_id, outcome)` which closes the span.

**Step 2 — Approval outcomes**
`complete_hitl(ticket_id, outcome)` sets `Ctx.HITL_OUTCOME`, `Ctx.HITL_WAITED_MS`, `Ctx.HITL_APPROVER_ROLE`. Outcome enum: `approved`, `rejected`, `changed`, `timed_out`. Approver role from the calling context, never individual identity.

**Step 3 — Prompt provenance**
`record_prompt(span, template_id, version, text)`: sets `Ctx.PROMPT_TEMPLATE_ID`, `Ctx.PROMPT_VERSION`, `Ctx.PROMPT_SHA256` (SHA-256 of the actual text at runtime). The hash is the critical part — name and version stay stable even if someone edits the prompt in production; the hash changes. Test: same template_id + version, different text → different SHA-256.

**Step 4 — Integration into `@trace_llm`**
`@trace_llm` accepts optional `prompt_template_id` and `prompt_version` kwargs. If provided, calls `record_prompt()` internally. Test: model call with template params produces all three prompt provenance attributes.

**Acceptance criteria:**
> Human Intervention Rate can be calculated from the records alone; an approval that timed out can be told apart from one that was refused; and every model call carries the instruction name, version and fingerprint.

---

### WP1-CORE-05 · Streaming, Async, Generators
**Sprint:** S2 · **Days:** 5 · **Block:** A

**Description:** The three situations where standard instrumentation silently loses track. Must be solved before streaming agents are instrumented.

**Affected files:** `gernas/async_context.py`, `gernas/decorators.py`, `tests/test_async_context.py`

**Steps:**

**Step 1 — Generator span lifecycle (finally block pattern)**
Proof-of-concept generator wrapper showing span open before first `yield`, closed in `finally`. The `finally` block runs on exhaust, explicit `.close()`, and GC. Test: all three exit paths close the span.

**Step 2 — Async context propagation across `asyncio.create_task()`**
Context is not automatically inherited by `asyncio.create_task()`. Implement `gernas_task(coro)` — a wrapper that copies the current OTel context into the task. Test: task-created span has same trace_id as parent coroutine.

**Step 3 — Cross-process context propagation via message headers**
`inject_context(headers: dict) -> dict` and `extract_context(headers: dict) -> Context`. Uses W3C TraceContext propagator. Test: inject then extract in a separate process produces span with matching trace_id.

**Step 4 — Wrong-parent detection test**
Write `test_parent_mismatch.py`: a test that creates two independent spans and asserts they do NOT share a trace_id. This test must be in CI to catch context leakage. If this test fails in the future, it means a decorator is accidentally inheriting a wrong parent. Test: two independent `@trace_agent` calls produce different trace_ids.

**Acceptance criteria:**
> A streaming call, a background task and a queue hand-off each produce one properly connected record; abandoning a stream halfway closes the recording as cancelled; and the wrong-parent test runs automatically on every build.

---

### WP1-CORE-06 · Dual-Emit Adapter
**Sprint:** S1–S3 · **Days:** 6 · **Block:** A

**Description:** Single emission from the agent, fan-out in the Collector. Agent code never knows there are two destinations. This task is primarily Collector config (WP2), but the SDK side is the Opik span-shape mapping and per-destination kill switches.

**Note:** The Collector write path to the data plane (not Opik) is **WP2 work**. This task covers only the SDK-side concerns.

**Affected files:** `gernas/exporters.py`, `gernas/config.py`, `collector/otel-collector-config.yaml`

**Steps:**

**Step 1 — SDK kill switches**
Env vars: `GERNAS_EMIT_OPIK=true|false`, `GERNAS_EMIT_DATAPLANE=true|false`. Each independently controls whether that destination receives spans. No agent code change required to toggle. Test: with `GERNAS_EMIT_OPIK=false`, no OTLP traffic goes to Opik endpoint.

**Step 2 — Opik span-shape mapping**
Opik expects `input` and `output` fields on the span in a specific position. OTel does not put them there. Implement a `transform` processor config in the Collector that reshapes the span for Opik: maps `gen_ai.prompt.redacted` → `input`, `gen_ai.completion.redacted` → `output`, `gernas.session_id` → `thread_id`.

**Step 3 — Per-destination sampling config**
Collector config: Opik pipeline uses a probabilistic sampler at 100% for developers (dev) and 15% in staging/production. Data plane pipeline uses tail-based sampling (errors always kept). Test: two separate pipeline configs in `otel-collector-config.yaml` verified by `otelcol validate`.

**Step 4 — No double-recording test**
Assert that a single decorator call produces exactly one span in the data plane store and one span in Opik — not two in either. Test: span count per trace_id is exactly 1 in each destination.

**Acceptance criteria:**
> One labelled call produces both a scorable record in Opik and a queryable record in the main store; either destination can be switched off without redeploying an agent; and nothing is recorded twice.

---

### WP1-CORE-07 · Tests + Overhead Benchmark
**Sprint:** S3 · **Days:** 8 · **Block:** A

**Description:** Proving the library is correct and does not slow agents down — before fifty teams install it.

**Affected files:** `tests/`, `benchmarks/bench_overhead.py`, `reports/overhead_report.md`

**Steps:**

**Step 1 — Unit test coverage gate**
Run `pytest --cov=gernas --cov-fail-under=90`. All 16 decorators must have > 90% coverage. Identify and fill gaps.

**Step 2 — Contract tests (span shape vs registry)**
For each decorator: assert the emitted span contains every attribute in `REQUIRED_ATTRIBUTES[operation]`. This is a schema test, not a value test. Test: removing a required attribute from a decorator fails the contract test.

**Step 3 — Overhead benchmark**
`benchmarks/bench_overhead.py`: run each decorator 10,000 times at concurrency 32. Measure p50, p95, p99 added latency and memory per open span. **Target: p95 < 1ms/span.** Publish results to `reports/overhead_report.md`.

**Step 4 — Failure injection — collector down**
`GERNAS_OTLP_ENDPOINT=http://nowhere:4317`. Run 18,000 span operations. Assert: agent error count = 0, dropped span count = 18,000 (approximately), memory does not grow unboundedly.

**Step 5 — Failure injection — collector slow**
Add `tc netem delay 500ms` to dev environment (or mock with slow exporter). Run benchmark. Assert: agent latency increase < 5ms p95 (batching absorbs the delay).

**Acceptance criteria:**
> All tests pass automatically on every build; there is a published report showing typical, high and worst-case added delay and memory per record; and the agent error count stays at zero with the collector down, slow and failing.

---

### WP1-CORE-08 · Packaging, Docs, Reference Agent
**Sprint:** S11 · **Days:** 6 · **Block:** A

**Affected files:** `docs/sdk-developer-guide.md`, `docs/onboarding-runbook.md`, `examples/reference-agent/`, `gernas/cli/doctor.py`

**Steps:**

**Step 1 — Versioned publish to FAB GitLab package registry**
`pyproject.toml` with correct version. `python -m build` produces a clean wheel. Publish to FAB internal registry. `pip install gernas-telemetry==<version>` from the registry succeeds in a clean venv.

**Step 2 — Onboarding runbook**
Written in the order a team actually follows: install → configure env vars → call `init_gernas()` → decorate entrypoint → verify with `gernas doctor`. Not a feature catalogue.

**Step 3 — `gernas doctor` preflight diagnostic**
CLI command. Checks: env vars present; Collector reachable with latency; test span emitted and visible in Opik; all 13 required canonical context fields present. Prints pass/fail per check. Exit code 0 = all pass, 1 = any fail.

**Step 4 — Reference agent**
Complete worked example in `examples/reference-agent/` using all 16 decorators. Produces a single connected trace with all canonical context attributes. Runs in the test environment without modification.

**Acceptance criteria:**
> The package installs from FAB's own catalogue by version number; a team can onboard a new agent using the guide alone with no code change from Accenture; and the example agent runs in the test environment producing a complete record.

---

## WP1·B — Canonical Semantic Context Model

---

### WP1-CTX-01 · Attribute Registry (16 Domains)
**Sprint:** S4–S5 · **Days:** 24 · **Block:** B

**Description:** The dictionary that fixes what every field is called, what it can contain, and how sensitive it is. This is a contract, not documentation. Must be complete and signed off before the SDK is built against it.

**Note:** 16 domains are in scope (13 from Blueprint + 3 from gap analysis: human approval, evaluation linkage, memory). Confirm with FAB DAI before starting — this is Risk R5.

**Affected files:** `registry/attributes.yaml`, `registry/domains/`

**Rate:** 16 domains × 1.5d = 24d

**Steps (apply to each domain — do one domain per step):**

Each domain step: define ~7 fields. For each field record: name, type, enum (if applicable), required (always/conditional/never), sensitivity (public/internal/confidential/restricted/secret), cardinality class (bounded/growing/unbounded), rollup dimension (yes/no), owner.

**Domains and sprint assignment:**
- S4: Business, Identity, Agent, Execution, Planning, Tool, AI/LLM, Prompt (8 domains)
- S5: Knowledge, Security, Infrastructure, Cost, Outcome + 3 DAI-confirmed extras (8 domains)

**Step template (repeat for each domain):**

`Step N — [Domain name] domain YAML`
Define all fields for this domain. For each field, make 5 decisions: name, type+enum, required, sensitivity, cardinality. Commit the YAML to `registry/domains/[domain].yaml`. Run the registry validator to check for duplicate names and missing required properties.

**Acceptance criteria:**
> All sixteen areas have complete entries; DAI and Data Governance have signed off the sensitivity column; and no field exists anywhere in the code that is missing from the registry.

---

### WP1-CTX-02 · Convention Files + Code Constants
**Sprint:** S5 · **Days:** 6 · **Block:** B

**Dependencies:** WP1-CTX-01 complete (registry frozen).

**Affected files:** `registry/generate.py`, `gernas/attributes.py`, `registry/semantic-conventions.yaml`, `.ruff.toml`

**Steps:**

**Step 1 — Code generator**
`registry/generate.py`: reads all YAML files in `registry/domains/`, produces `gernas/attributes.py` (the `Ctx` class). Generated file has a header warning: `# AUTO-GENERATED by registry/generate.py — do not edit`. Run: `python registry/generate.py && git diff gernas/attributes.py` — should be clean after regeneration.

**Step 2 — Cross-language convention files**
Generate `registry/semantic-conventions.yaml` in OTel semconv format. This allows non-Python services to consume the same attribute names. Validated with the OTel semconv schema linter.

**Step 3 — Lint rule — no hand-typed attribute names**
Add a ruff custom rule (or a `conftest.py` AST check) that fails if any string literal matching `gernas\..*` appears outside `gernas/attributes.py`. Every decorator file must use `Ctx.ATTRIBUTE_NAME`. Test: introduce one hand-typed attribute name → CI fails.

**Step 4 — CI enforcement**
Add to GitLab CI: `python registry/generate.py && git diff --exit-code gernas/attributes.py`. Fails the build if a developer edits `attributes.py` by hand without regenerating from the registry.

**Acceptance criteria:**
> The code and validation files are generated automatically rather than maintained by hand; the build fails on any unrecognised field name; and the published files are usable by a non-Python service.

---

### WP1-CTX-03 · Identifier Lifecycle
**Sprint:** S4 · **Days:** 6 · **Block:** B

**Steps:**

**Step 1 — Lifecycle rules document**
For each identifier: who creates it, when it resets, how long it lives. Commit to `docs/identifier-lifecycle.md`. Key rules: `session_id` spans many runs (created by channel, not agent); `agent_run_id` is new per run; `task_id` may be shared across agents; `parent_run_id` set only on delegated children.

**Step 2 — Propagation across async boundaries**
Update `inject_context()` and `extract_context()` to carry `gernas.session_id` and `gernas.agent_run_id` in addition to OTel W3C TraceContext headers. Test: session_id survives a queue hop.

**Step 3 — Parent-child linkage for delegation**
Child agent: sets `Ctx.PARENT_RUN_ID = parent.agent_run_id`, generates its own `Ctx.AGENT_RUN_ID`. Never inherits the parent's run ID. Test: delegated child has unique agent_run_id but matching trace_id.

**Step 4 — Orphan detection**
Check: if `parent_run_id` is set but no span with that `agent_run_id` exists in the current trace → set `Ctx.ORPHAN_DETECTED = True` and emit a log event. Test: broken delegation chain triggers orphan flag.

**Acceptance criteria:**
> Each identifier has a documented owner, reset rule and lifetime; a job crossing background tasks, queues and agent boundaries comes out as one connected record; and broken chains are detected and counted rather than silently lost.

---

### WP1-CTX-04 · Cross-System Correlation Keys
**Sprint:** S4 · **Days:** 3 · **Block:** B

**Steps:**

**Step 1 — Join key design doc**
Document the four joins: Opik ↔ telemetry (`gernas.trace_id`, `gernas.agent_run_id`), provenance ledger ↔ telemetry (`gernas.agent_run_id`, `gernas.provenance_id`), workspace ↔ telemetry (`gernas.work_unit_id`), GitLab release ↔ telemetry (`gernas.agent_version`, `gernas.commit_sha`).

**Step 2 — Bidirectional embedding**
Update `@trace_agent` to emit `Ctx.PROVENANCE_ID`, `Ctx.WORK_UNIT_ID`, `Ctx.COMMIT_SHA` from env vars. Test: given one `agent_run_id`, all four join keys are present on the span.

**Step 3 — Join validation query**
For each join, write a query that returns a result only if the join resolves. Commit queries to `tests/test_joins.py`. These are integration tests run against a seeded test store.

**Acceptance criteria:**
> Each of the four joins works in both directions and is documented; a single run identifier resolves to its trace, audit record, workspace and release in one check; and the keys are stored on both sides, not just where they were created.

---

### WP1-CTX-05 · Classification + Redaction Matrix
**Sprint:** S5 · **Days:** 3 · **Block:** B

**Affected files:** `registry/redaction-matrix.yaml`, `tests/test_redaction_matrix.py`

**Steps:**

**Step 1 — Redaction matrix YAML**
One row per field from WP1-CTX-01 registry. Columns: field name, sensitivity class, default action (keep/fingerprint/mask/tokenise/drop), notes (PDPL/residency). Actions map directly to Collector processor rules in WP2.

**Step 2 — CISO review package**
Export matrix to PDF/table format for CISO and Data Governance review. Every field accounted for, no blanks in action column. This is the gate for Collector production promotion (S7).

**Step 3 — Matrix-to-Collector mapping test**
For each row with action ≠ keep: assert a corresponding rule exists (or is planned) in `collector/otel-collector-config.yaml`. No row left unmapped.

**Acceptance criteria:**
> Every field has a sensitivity level and a masking action; CISO and Data Governance have signed the matrix; and every row maps to a rule actually implemented in WP2 with nothing left unmapped.

---

### WP1-CTX-06 · Cardinality Governance
**Sprint:** S5 · **Days:** 3 · **Block:** B

**Steps:**

**Step 1 — Cardinality classification per field**
Add `cardinality_class` (bounded/growing/unbounded) and `rollup_dimension` (yes/no) to each registry entry. Bounded ≤ ~500 unique values; growing = expected to grow over time but managed; unbounded = session_id, user_id, trace_id — never a rollup dimension.

**Step 2 — Rollup row budget calculation**
Compute `use_case × agent × version × model × day` for the permitted dimensions. Target: < 100M rollup rows/year. Document the calculation in `docs/cardinality-budget.md`.

**Step 3 — Collector enforcement config**
Add processor rule in Collector config: strip unbounded fields (session_id, user_id, trace_id) before the rollup pipeline. These stay on raw spans, never enter rollup tables. Test: rollup table query does not have a column for user_id.

**Acceptance criteria:**
> Every field has a size class and a yes-or-no decision on whether it can slice a report; the pipeline strips unlimited fields before summarising; and the exception route states the added cost before anyone approves it.

---

### WP1-CTX-07 · Version Policy + Conformance Suite
**Sprint:** S5 · **Days:** 3 · **Block:** B

**Steps:**

**Step 1 — Version policy document**
Three rules: (1) fields may be added, never renamed or retyped; (2) enum values may be added, never removed; (3) removal requires 2-release notice + migration note. Commit to `docs/version-policy.md`. Reference in `CHANGELOG.md` template.

**Step 2 — `gernas conformance` CLI command**
Checks a running service's spans against the registry: required fields present, no unregistered fields, no restricted fields leaked, no cardinality breaches. Exit code 0 = pass. Produces a machine-readable JSON report.

**Step 3 — CI integration**
Add `gernas conformance --service <service-name>` to the GitLab CI template that agent teams use. Fails the pipeline on any breach. Test: intentionally omit a required field → conformance fails with an actionable error message.

**Acceptance criteria:**
> The change policy is published and referenced in the library's change history; the self-check runs in any team's build and fails on a breach; and upgrading the dictionary does not require agent teams to change their code.

---

## WP1·C — Platform Component Instrumentation

The following component tasks share a **standard 4-step pattern**. Apply this pattern to each, using the component-specific signals listed below.

**Standard instrumentation pattern for each component:**

**Step 1 — Read existing code and identify emission points**
Read the component's source. Identify where to call the decorator or add span attributes. Agree emission points with the component owner before writing any code.

**Step 2 — Implement instrumentation**
Add decorator calls or direct span creation at the agreed emission points. Inject canonical context envelope. Use `safe_set()` throughout.

**Step 3 — Test: required signals present**
For each required signal: write a test that asserts the attribute is present and non-null on the emitted span. Test with a realistic invocation of the component.

**Step 4 — Component owner acceptance + coverage register update**
Demo the instrumented component to the component owner. Record status in `coverage-register.yaml`: component name, tier, owner, required signal count, actual signal count, status (done/partial/blocked).

### Tier 1 Components (5d each — signals do not exist yet)

| Task ID | Component | Sprint | Required signals |
|---|---|---|---|
| WP1-T1-01 | Use Case Layer | S5 | use_case_id, application_id, channel, business_domain, business_outcome |
| WP1-T1-02 | GERNAS OS | S5 | agent_registration, deployment, version, config_change, certification_status |
| WP1-T1-03 | Agent Runtime | S5 | run_start, run_end, planning_latency, steps, retries, loops, termination_reason |
| WP1-T1-04 | Multi-Agent Orchestration | S6 | parent_agent_id, child_agent_id, delegation_path, inter_agent_latency, handoff_count |
| WP1-T1-05 | Skills/Tools | S6 | tool_id, tool_type, invocation_count, latency, failure_reason, auth_decision |
| WP1-T1-06 | API Gateway | S6 | request_volume, latency, status_2xx/4xx/5xx, throttled, quota_remaining |

**Critical signal for WP1-T1-03:** `termination_reason` is a single attribute that Task Success Rate, Abandonment Rate, Timeout Rate, and Policy Block Rate all depend on. It must emit one of: `completed`, `max_steps`, `timeout`, `policy_block`, `abandoned`, `error`, `human_rejected`. Do not leave this optional.

**Critical signal for WP1-T1-01:** `business_outcome` cannot be inherited from the platform — it must be emitted by the Use Case Layer with per-use-case business logic. Without it, Task Success Rate returns nothing.

### Tier 2 Components (4d each — signals partially exist; add identity tags)

| Task ID | Component | Sprint | Required signals |
|---|---|---|---|
| WP1-T2-01 | LLM Gateway | S6 | routing, provider, model, tokens, latency, retries, fallback_used, throttled |
| WP1-T2-02 | LLMs | S6 | ttft_ms, generation_latency, tokens_per_sec, stop_reason, input_tokens, output_tokens |
| WP1-T2-03 | Embedding Services | S6 | embedding_calls, latency, token_volume, batch_size, errors |
| WP1-T2-04 | Content Safety | S7 | blocked_request, safety_category, risk_level, policy_triggered |
| WP1-T2-05 | RAG/Search | S7 | retrieval_duration, query_rewriting, top_k, result_count, empty_result, ranking_score |
| WP1-T2-06 | Vector Store | S7 | query_latency, index_size, ingestion_rate, failed_indexing, index_freshness |
| WP1-T2-07 | Document Processing | S7 | docs_parsed, docs_rejected, ocr_latency, chunk_count, parsing_failures |
| WP1-T2-08 | Memory | S7 | read_write_ops, memory_hit, stale_memory, memory_size |
| WP1-T2-09 | Knowledge Layer | — | ⚠ **NOT COSTED** — flag to programme lead before starting |

### Tier 3 Components (2d each — signals exist in Azure; add agent identity correlation)

| Task ID | Component | Sprint | Correlation approach |
|---|---|---|---|
| WP1-T3-01 | Database | S8 | k8sattributes processor + SQLAlchemy OTel hook |
| WP1-T3-02 | AKS/Containers | S8 | k8sattributes from pod labels (agent_id, use_case_id) |
| WP1-T3-03 | GPU (DCGM) | S8 | DCGM exporter → Collector with resource attributes |
| WP1-T3-04 | Messaging (Service Bus) | S8 | SDK-level context injection in message headers |
| WP1-T3-05 | Network (Network Watcher) | S8 | Azure Network Watcher → Collector via Azure Monitor receiver |

### Tier 4 Components (2d each — security and delivery signals; agree access first)

| Task ID | Component | Sprint | Access requirement |
|---|---|---|---|
| WP1-T4-01 | Identity/AAD | S9 | Azure Monitor Logs Receiver — needs AAD read permission |
| WP1-T4-02 | Authorization/OPA | S9 | OPA audit log OTLP export — confirm OPA version supports it |
| WP1-T4-03 | CI/CD/GitLab | S9 | GitLab webhook → OTLP receiver |
| WP1-T4-04 | Telemetry Platform | S9 | Collector self-metrics → separate Prometheus instance |

**Tier 1–2 shared acceptance criteria:**
> Each component produces the signals listed above with the full canonical context envelope attached; the component owner has accepted the change; and the coverage register shows status = done.

**Tier 3–4 shared acceptance criteria:**
> Each infrastructure/security signal class carries agent and use-case identity; no infrastructure component required a code change; coverage register updated.

---

## WP1·D — PTU Telemetry

### WP1-PTU-01 · Azure OpenAI PTU Telemetry
**Sprint:** S9 · **Days:** TBC (new scope) · **Block:** D

**Dependency:** Azure Monitor Metrics API access for PTU deployment IDs — Risk R4. Do not start without confirmed access.

**Steps:**

**Step 1 — Azure Monitor Metrics API integration**
Poll `microsoft.cognitiveservices/accounts` metrics for each PTU deployment: `TokensPerMinuteUsed`, `TotalTokenCalls`, `ProvisionedManagedUtilization`. Polling interval: 1 minute.

**Step 2 — PTU cost attribution**
`gernas.ptu.cost_usd = tokens_consumed × (monthly_PTU_commit / capacity_tokens_per_month)`. Rate from `GERNAS_PTU_RATE_MONTHLY_AED` and `GERNAS_PTU_CAPACITY_TOKENS` env vars.

**Step 3 — Attach to agent spans**
Correlate PTU usage to the agent_run_id active during the polling window. Emit as span attribute: `Ctx.PTU_COST_AED`.

---

## WP1·E — E2E Tests + Documentation

### WP1-E2E-01 · E2E Test Plan Design
**Sprint:** S8 · **Days:** 2

Write `docs/e2e-test-plan.md`: all 24 components → Collector → data plane → all 8 dashboards. One test scenario per component. Success criteria per scenario. Requires sign-off from all three tracks (BE1, BE2, UI) before S10 execution.

### WP1-E2E-02 · Full E2E Integration Test Execution
**Sprint:** S10 · **Days:** 5

Execute the plan from WP1-E2E-01. All 24 component spans must be visible in the data plane. All 8 dashboards must return data for at least one scenario. Zero broken drill-down links.

### WP1-E2E-03 · Performance/Load Test at 580M Span/Year
**Sprint:** S10 · **Days:** 3

Synthetic load: 50 agents × 40 spans/run × 800 runs/day × 365 days. Run in AKS staging. Dashboard query latency < 2s at p95 for all 8 dashboards. SDK overhead < 1ms/span p95. Publish results to `reports/load-test-report.md`.

**Acceptance criteria:**
> E2E tests green; p99 dashboard query < 2s; WP1 docs draft reviewed by FAB engineering.

### WP1-DOCS-01 · SDK Developer Guide + ADRs
**Sprint:** S10–S11 · **Days:** 6

**Steps:**

**Step 1 — SDK developer guide** — 16-decorator reference with one code example per decorator. Written for a Python engineer who has never used OTel.

**Step 2 — 16-domain attribute reference** — generated from registry. Every field with its name, type, required status, sensitivity class, and example value.

**Step 3 — Onboarding runbook** — see WP1-CORE-08 Step 2. Matches the `gernas doctor` output.

**Step 4 — ADRs** — one ADR per significant architectural decision: OTel-primitives-only (vs auto-instrumentation), decorator-as-interface, dual-emit design, `try/finally` for streaming spans, 16-domain vs 13-domain context model.

---

## ⚠ Open Items That Block Implementation

| Item | Task blocked | Action required |
|---|---|---|
| Data-plane store not selected (R1) | WP1-CTX-06 Step 3, WP1-E2E-02 | FAB Architect decision by end of S0 |
| Domain count 13 vs 16 (R5) | WP1-CTX-01 | FAB DAI confirmation by end of S0 |
| CISO PII attribute review (R3) | WP1-CTX-05, Collector prod promotion | Formal review must be scheduled by S3 |
| Azure Monitor API access for PTU (WP1-PTU-01) | WP1-PTU-01 | Azure subscription owner grant needed |
| Knowledge Layer ownership | WP1-T2-09 | Raise to programme lead — currently NOT COSTED |

---

*WP2 (Collector & Gateway) and WP3 (Dashboards & KPIs) task entries to be added in a follow-on plan document covering BE2 and UI tracks.*
