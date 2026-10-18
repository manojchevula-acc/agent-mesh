# ruff: noqa: E501 - embedded CSS / JS / prose strings
"""Build the architecture / instrumentation documentation from the source and checklist.md.

    python scripts/build_architecture_doc.py           # write the two generated files
    python scripts/build_architecture_doc.py --check   # exit 1 if they are stale

Generated (never edit by hand):

* ``docs/GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html`` — self-contained, offline
  HTML (inline CSS / JS / SVG): overview, flowchart, code explorer, API catalogue,
  sequence diagrams, Agent Mesh instrumentation map, SOW dashboard, gaps;
* ``docs/INSTRUMENTATION_MAP.md`` — the same API catalogue and call-site map as Markdown.

Sources, so the documents cannot drift from the implementation:

* API definitions, signatures and line numbers: ``ast`` over ``gernas/``;
* span name / kind / required attributes: ``registry/spans.yaml``;
* Agent Mesh and service call sites: ``ast`` over the application code, resolved through
  the mesh integration module (``src/observability/gernas_telemetry.py``) to SDK APIs;
* tests per API: ``ast`` over ``tests/``;
* SOW statuses, the five-aspect map, findings, gaps and the test log: ``../checklist.md``.

Behaviour notes per API (``FACTS``) and the sequence diagrams are written from a reading of
the source; each diagram states whether it was observed in a live trace or is
source-derived. ``--check`` also verifies the checklist's derived aspect table.
"""

from __future__ import annotations

import argparse
import ast
import html
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SDK = Path(__file__).resolve().parent.parent
REPO = SDK.parent
CHECKLIST = REPO / "checklist.md"
OUT_HTML = SDK / "docs" / "GERNAS_SDK_ARCHITECTURE_AND_INSTRUMENTATION.html"
OUT_MD = SDK / "docs" / "INSTRUMENTATION_MAP.md"
MESH_INTEGRATION = REPO / "agent-mesh" / "src" / "observability" / "gernas_telemetry.py"

APP_SOURCES = [
    ("Agent Mesh", REPO / "agent-mesh", ["src/**/*.py", "*.py", "demos/*.py"]),
    ("DataLayer MCP", REPO / "datalayer-as-service", ["mcp_server/*.py"]),
    ("RAG service", REPO / "rag-as-a-service", ["mcp_integration/*.py", "src/gernas_rag/**/*.py"]),
]

# ── behaviour facts per public API (from reading the source; see module docstring) ──

FACTS: dict[str, dict[str, str]] = {
    "init_gernas": {
        "group": "Setup",
        "span": "No span. Builds TracerProvider (ParentBased(TraceIdRatioBased) sampler, 1024-char attribute limit), "
        "_BaggageSpanProcessor + _GernasBatchSpanProcessor(CountingSpanExporter(OTLP)), MeterProvider "
        "(PeriodicExportingMetricReader), LoggerProvider + GernasLogHandler on GERNAS_LOG_NAMESPACES.",
        "context": "Installs the global propagator: W3C TraceContext + GernasBaggagePropagator.",
        "errors": "Missing mandatory identity → GernasConfigError (from GernasConfig.from_env). Metric / log "
        "exporter failures are logged and that signal is disabled. Idempotent; reuses an existing SDK TracerProvider.",
        "async": "n/a (call once per process at startup)",
    },
    "GernasConfig": {
        "group": "Setup",
        "span": "No span. Configuration only (GernasConfig.from_env reads GERNAS_* variables).",
        "context": "Supplies the §2 envelope (business_domain, use_case_id, application_id, environment).",
        "errors": "GernasConfigError on missing / invalid mandatory values — identity is never invented.",
        "async": "n/a",
    },
    "GernasHandle": {
        "group": "Setup",
        "span": "No span. flush(timeout) force-flushes traces, metrics, logs; shutdown() is idempotent.",
        "context": "—",
        "errors": "flush returns False on failure (logged); shutdown never raises.",
        "async": "n/a (call at process exit)",
    },
    "trace_use_case": {
        "group": "Decorator",
        "span": "Opens use_case.request (SERVER): root of the request trace, or child of an inbound server span; "
        "new_trace=True always starts a fresh trace.",
        "context": "Becomes the current span for the call; child spans nest under it.",
        "errors": "Exception → business_outcome=failure, ERROR + exception event, re-raised. Cancellation → "
        "abandoned, status left UNSET. Metric gernas.use_case.requests.",
        "async": "sync, async, generator, async generator",
    },
    "trace_agent": {
        "group": "Decorator",
        "span": "Opens agent.run (INTERNAL) and binds a new RunState (fresh agent_run_id; parent_run_id from the "
        "enclosing run or from baggage) for the duration of the call.",
        "context": "RunState is a ContextVar — isolated per asyncio task; every span inside gets agent_id / agent_run_id.",
        "errors": "ERROR only for termination_reason error / timeout; policy_block, human_rejected, max_steps … "
        "are outcomes, not errors. Loop / max-step / retry detection; gernas.agent.* metrics.",
        "async": "sync, async, generator, async generator",
    },
    "trace_step": {
        "group": "Decorator",
        "span": "Opens agent.step (INTERNAL) with step_id / step_type / attempt_number; counts a step on the run.",
        "context": "Child of the current span (normally agent.run).",
        "errors": "Exception → ERROR + exception event, re-raised.",
        "async": "sync, async, generator, async generator",
    },
    "trace_planning": {
        "group": "Decorator",
        "span": "Opens agent.plan (INTERNAL) with plan_id; records planning latency.",
        "context": "Child of the current run.",
        "errors": "Exception → ERROR, re-raised.",
        "async": "sync, async, generator, async generator",
    },
    "trace_delegation": {
        "group": "Decorator",
        "span": "Opens agent.delegate (CLIENT) with parent_agent_id, child_agent_id, delegation_path.",
        "context": "Injects traceparent + GERNAS baggage into a dict argument named headers / carrier (or headers_arg).",
        "errors": "Exception → ERROR, re-raised. Metrics gernas.agent.delegations + delegation duration.",
        "async": "sync, async, generator, async generator",
    },
    "trace_llm": {
        "group": "Decorator",
        "span": "Opens llm.call (CLIENT): provider, model_id, tokens (usage_from or response.usage), stop_reason, "
        "estimated_cost (rate card); TTFT and tokens/s on streams.",
        "context": "Child of the current span; run context attached.",
        "errors": "Exception → ERROR + error_category (rate_limit, timeout, auth …), re-raised. "
        "No prompt / completion text is ever recorded.",
        "async": "sync, async, generator (stream), async generator (stream)",
    },
    "trace_embedding": {
        "group": "Decorator",
        "span": "Opens embedding.create (CLIENT): provider, model_id, batch_size, tokens.",
        "context": "Child of the current span.",
        "errors": "Exception → ERROR, re-raised.",
        "async": "sync, async, generator, async generator",
    },
    "trace_tool": {
        "group": "Decorator",
        "span": "Opens tool.call (CLIENT): tool_id, tool_type, authorization_decision, payload size, dependency.",
        "context": "Child of the current span; run context attached.",
        "errors": "Auth error → authorization_decision=deny, status OK (expected outcome), re-raised. Other "
        "exceptions → ERROR, re-raised.",
        "async": "sync, async, generator, async generator",
    },
    "trace_retrieval": {
        "group": "Decorator",
        "span": "Opens retrieval.search (CLIENT): result_count, empty_result, top_k, ranking score, source_id, "
        "knowledge_domain, document_version, authorization_decision.",
        "context": "Child of the current span.",
        "errors": "Exception → ERROR, re-raised.",
        "async": "sync, async, generator, async generator",
    },
    "trace_document": {
        "group": "Decorator",
        "span": "Opens document.process (INTERNAL): document_type, document_status (parsed / rejected / failed), "
        "chunk_count; metrics gernas.documents + gernas.document.chunks.",
        "context": "Child of the current span.",
        "errors": "Exception → document_status=failed, ERROR. A failure reported via outcome_from is also ERROR.",
        "async": "sync, async, generator, async generator",
    },
    "trace_memory": {
        "group": "Decorator",
        "span": "Opens memory.access (INTERNAL): memory_operation (read / write), hit, size.",
        "context": "Child of the current span.",
        "errors": "Exception → ERROR, re-raised.",
        "async": "sync, async, generator, async generator",
    },
    "trace_guardrail": {
        "group": "Decorator",
        "span": "Opens guardrail.check (INTERNAL): policy_id, policy_decision (allow / deny), risk_classification.",
        "context": "Child of the current span.",
        "errors": "A deny (returned or a block exception) is an outcome: status OK, and with blocks_run=True the "
        "run's termination_reason becomes policy_block. Other exceptions → ERROR.",
        "async": "sync, async, generator, async generator",
    },
    "trace_content_safety": {
        "group": "Decorator",
        "span": "Opens content_safety.check (INTERNAL): safety_category, content_blocked, policy_triggered.",
        "context": "Child of the current span.",
        "errors": "Block exception → content_blocked=true, status OK, re-raised. Other exceptions → ERROR.",
        "async": "sync, async, generator, async generator",
    },
    "trace_hitl": {
        "group": "Decorator",
        "span": "hitl.approval around a call that waits for a human decision (see start_hitl).",
        "context": "Child of the current span.",
        "errors": "Outcome approved / rejected / timed_out.",
        "async": "sync, async",
    },
    "start_hitl": {
        "group": "Run helper",
        "span": "Opens a long-lived hitl.approval span (not made current) keyed by ticket id.",
        "context": "Child of the current span (or parent=); the caller's context is untouched while pending.",
        "errors": "Never raises; returns INVALID_SPAN on failure. Open tickets are bounded (oldest force-closed timed_out).",
        "async": "sync (safe from async code)",
    },
    "complete_hitl": {
        "group": "Run helper",
        "span": "Ends the hitl.approval span of a ticket with hitl_outcome.",
        "context": "—",
        "errors": "Returns False for an unknown ticket; never raises.",
        "async": "sync",
    },
    "record_retry": {
        "group": "Run helper",
        "span": "No span: increments retry_count on the current run (gernas.agent.retries).",
        "context": "Needs a bound run (inside @trace_agent); otherwise a no-op.",
        "errors": "Never raises.",
        "async": "sync",
    },
    "set_termination_reason": {
        "group": "Run helper",
        "span": "No span: sets termination_reason of the current run (e.g. policy_block).",
        "context": "Needs a bound run.",
        "errors": "Never raises.",
        "async": "sync",
    },
    "mark_fallback": {
        "group": "Run helper",
        "span": "No span: marks the current llm.call as a model fallback (fallback rate KPI).",
        "context": "Current span must be llm.call.",
        "errors": "Never raises.",
        "async": "sync",
    },
    "record_evaluation": {
        "group": "Run helper",
        "span": "Creates an evaluation.score span (INTERNAL) + gernas.evaluation.score histogram; no evaluated text.",
        "context": "Child of the current context; offline evaluators pass agent_id / agent_run_id.",
        "errors": "ValueError for an unknown metric or a score outside [0, 1] (caller bug); telemetry failures swallowed.",
        "async": "sync",
    },
    "annotate": {
        "group": "Run helper",
        "span": "No span: sets registry attributes on the current span through safe_set (redaction applies).",
        "context": "Current span.",
        "errors": "Never raises.",
        "async": "sync",
    },
    "request_context": {
        "group": "Context",
        "span": "No span: context manager that sets GERNAS baggage (session_id, task_id, channel, "
        "tokenized_user_id via HMAC, principal_type, tenant, department).",
        "context": "Baggage is copied onto every span started inside (_BaggageSpanProcessor) and travels on every "
        "remote hop (GernasBaggagePropagator). contextvars-based: isolated per task.",
        "errors": "Raw user ids are never recorded; without GERNAS_USER_TOKEN_KEY the user is omitted (warning).",
        "async": "context manager; works in sync and async code",
    },
    "correlation": {
        "group": "Context",
        "span": "No span: read-only trace_id, span_id, agent_id, agent_run_id, session_id, task_id for app logs.",
        "context": "Reads the current context.",
        "errors": "Never raises (None values when unavailable).",
        "async": "sync",
    },
    "inject_context": {
        "group": "Context",
        "span": "No span: writes traceparent / tracestate / baggage (incl. parent run ids) into a carrier dict.",
        "context": "Outbound propagation for any transport (HTTP, queues).",
        "errors": "Never raises.",
        "async": "sync",
    },
    "extract_context": {
        "group": "Context",
        "span": "No span: returns an OTel Context from inbound headers.",
        "context": "Inbound propagation.",
        "errors": "Never raises.",
        "async": "sync",
    },
    "attach_headers": {
        "group": "Context",
        "span": "No span: context manager that attaches the context extracted from headers.",
        "context": "Inbound propagation for servers without a framework adapter.",
        "errors": "Never raises; detaches on exit.",
        "async": "context manager",
    },
    "gernas_task": {
        "group": "Context",
        "span": "No span: asyncio.create_task that copies the caller's context (trace, baggage, run).",
        "context": "Background work stays in the request trace.",
        "errors": "As asyncio.create_task.",
        "async": "async (returns Task)",
    },
    "wrap_in_context": {
        "group": "Context",
        "span": "No span: binds the current context to a callable (threads, executors).",
        "context": "Cross-thread propagation.",
        "errors": "As the wrapped callable.",
        "async": "sync",
    },
    "set_redaction_hook": {
        "group": "Privacy",
        "span": "No span: installs a custom redaction hook run by safe_set before every attribute write.",
        "context": "—",
        "errors": "Hook errors drop the attribute and are counted (gernas.sdk.dropped_attributes).",
        "async": "sync",
    },
    "gernas_middleware": {
        "group": "Adapter (MAF)",
        "span": "Returns [GernasAgentMiddleware (agent.run), GernasChatMiddleware (llm.call), "
        "GernasFunctionMiddleware (tool.call, tool_type=mcp with mcp_server)] for Agent(middleware=…).",
        "context": "Same engine as the decorators; RunState bound per MAF agent invocation.",
        "errors": "As trace_agent / trace_llm / trace_tool.",
        "async": "async (MAF middleware protocol)",
    },
    "disable_native_telemetry": {
        "group": "Adapter (MAF)",
        "span": "Switches off MAF's own invoke_agent / chat / execute_tool spans (single instrumentation path).",
        "context": "—",
        "errors": "Raises ImportError if agent_framework is not installed (callers guard it).",
        "async": "sync",
    },
    "GernasA2AInterceptor": {
        "group": "Adapter (A2A)",
        "span": "No span: A2A client interceptor; before() injects traceparent + baggage into service parameters.",
        "context": "Outbound propagation on every A2A call; chains an existing interceptor.",
        "errors": "Propagation failure never fails the call.",
        "async": "async",
    },
    "GernasASGIMiddleware": {
        "group": "Adapter (ASGI)",
        "span": "Opens '{method} {route}' (SERVER) per HTTP request; http.route / status code; 5xx → ERROR.",
        "context": "Extracts the inbound trace + baggage and makes the server span current.",
        "errors": "Exception → ERROR + exception event, re-raised; never breaks request handling.",
        "async": "async (ASGI)",
    },
    "GernasFastMCPMiddleware": {
        "group": "Adapter (MCP server)",
        "span": "Opens tool.call (SERVER, tool_type=mcp) per tools/call request on a fastmcp server.",
        "context": "Reads traceparent / baggage from the MCP request _meta (attach_headers) so the server span "
        "joins the caller's trace.",
        "errors": "As trace_tool.",
        "async": "async",
    },
    "mcp_meta_context": {
        "group": "Adapter (MCP server)",
        "span": "No span: context manager attaching the trace context from a low-level mcp Context's _meta.",
        "context": "Inbound propagation for mcp.server.fastmcp tools.",
        "errors": "Never raises outside a request.",
        "async": "context manager",
    },
    "GernasCallbackHandler": {
        "group": "Adapter (LangChain)",
        "span": "LangChain callback handler mapping chains / LLMs / tools / retrievers onto the same GERNAS spans.",
        "context": "Parent / child from LangChain run ids.",
        "errors": "Errors recorded on the matching span.",
        "async": "sync + async callbacks",
    },
}

# Diagrams that were observed in live traces (checked with scripts/opik_verify.py).
LIVE = "Observed in the live trace LIVE18A000–003 (Opik, 2026-10-18, opik_verify PASS)"
SOURCE = "Source-derived expected sequence (not observed as a runtime trace)"


@dataclass
class Seq:
    key: str
    title: str
    evidence: str
    participants: list[tuple[str, str]]
    steps: list[tuple]
    notes: str = ""


SEQUENCES: list[Seq] = [
    Seq(
        "init",
        "Application initialisation and SDK setup",
        SOURCE + "; covered by tests/test_packaging.py, tests/test_config.py and every live start-up",
        [
            ("app", "run.py / api_server.py"),
            ("gt", "gernas_telemetry"),
            ("cfg", "GernasConfig"),
            ("sdk", "gernas.tracer"),
            ("otel", "OpenTelemetry SDK"),
            ("maf", "agent_framework"),
        ],
        [
            ("call", "app", "gt", "init_telemetry(service_name, channel)"),
            ("call", "gt", "cfg", "GernasConfig.from_env(service_name)"),
            ("ret", "cfg", "gt", "cfg  (or GernasConfigError → telemetry off + warning)"),
            ("call", "gt", "sdk", "init_gernas(cfg)"),
            ("call", "sdk", "otel", "TracerProvider(resource, ParentBased sampler)"),
            ("call", "sdk", "otel", "add _BaggageSpanProcessor, _GernasBatchSpanProcessor"),
            ("self", "sdk", "CountingSpanExporter(OTLP exporter)"),
            ("call", "sdk", "otel", "set_global_textmap(TraceContext + GernasBaggage)"),
            ("call", "sdk", "otel", "MeterProvider, LoggerProvider"),
            ("ret", "sdk", "gt", "GernasHandle"),
            ("call", "gt", "maf", "disable_native_telemetry()"),
            ("ret", "gt", "app", "ready (one init per process)"),
        ],
    ),
    Seq(
        "root",
        "Root request, workflow run and workflow steps",
        LIVE,
        [
            ("cli", "demos / run.py"),
            ("orc", "orchestrator"),
            ("ctx", "gernas.context"),
            ("wf", "workflow executors"),
            ("eng", "_core engine"),
            ("exp", "span processor"),
        ],
        [
            ("call", "cli", "orc", "handle_request(user, query)"),
            ("note", "orc", "@trace_use_case → use_case.request (SERVER)"),
            ("call", "orc", "ctx", "request_context(session_id, task_id, user_id)"),
            ("ret", "ctx", "orc", "baggage attached (tokenized user)"),
            ("call", "orc", "wf", "_run_workflow(workflow, state)"),
            ("note", "wf", "@trace_mesh_run → trace_agent: agent.run + RunState"),
            ("call", "wf", "eng", "InputGuardrailExecutor  @trace_step agent.step"),
            ("self", "eng", "screen_input → guardrail.check"),
            ("call", "wf", "eng", "RBACValidationExecutor  agent.step"),
            ("self", "eng", "_role_recognised → guardrail.check"),
            ("call", "wf", "eng", "CacheCheck / Compliance / Domain / OutputRedaction"),
            ("ret", "eng", "wf", "spans end in finally (status, execution_status)"),
            ("ret", "wf", "orc", "MeshState → agent.run ends (termination_reason)"),
            ("call", "orc", "exp", "use_case.request ends (business_outcome)"),
            ("ret", "orc", "cli", "MeshResult"),
        ],
    ),
    Seq(
        "llm",
        "LLM invocation (raw OpenAI-compatible call)",
        LIVE + " — conversation_summary → llm.call",
        [
            ("sum", "memory.summarizer"),
            ("gt", "gernas_telemetry"),
            ("eng", "_core / LLMSpec"),
            ("http", "httpx client"),
            ("groq", "Groq API"),
        ],
        [
            ("call", "sum", "gt", "chat_post(client, url, json, model, template_id)"),
            ("note", "gt", "trace_llm(provider, usage_from=_usage)"),
            ("call", "gt", "eng", "_begin: llm.call (CLIENT) + envelope + run context"),
            ("call", "gt", "http", "client.post(url, json)"),
            ("call", "http", "groq", "POST /chat/completions"),
            ("ret", "groq", "http", "200 + usage"),
            ("ret", "http", "gt", "response"),
            ("self", "gt", "_annotate: prompt_template_id, stop_reason"),
            ("call", "gt", "eng", "success: tokens, estimated_cost (rate card)"),
            ("ret", "eng", "sum", "response (span ended, OK)"),
        ],
    ),
    Seq(
        "delegation",
        "Delegation and agent hand-off over A2A (cross-process)",
        LIVE + " — compliance-gate → agent.delegate → POST / → agent.run(compliance) → llm.call",
        [
            ("dom", "Compliance / Domain executor"),
            ("cl", "a2a.clients"),
            ("icp", "GernasA2AInterceptor"),
            ("srv", "a2a_server (ASGI)"),
            ("mw", "GernasAgentMiddleware"),
            ("chat", "GernasChatMiddleware"),
        ],
        [
            ("call", "dom", "cl", "ask_remote(name, prompt)"),
            ("note", "cl", "@trace_delegation(child_arg='name') → agent.delegate (CLIENT)"),
            ("call", "cl", "icp", "before(args)"),
            ("self", "icp", "inject_context({}) → traceparent + baggage"),
            ("call", "icp", "srv", "HTTP POST / (A2A JSON-RPC)"),
            ("note", "srv", "GernasASGIMiddleware: extract_context → 'POST /' (SERVER)"),
            ("call", "srv", "mw", "MAF agent.run(...)"),
            ("self", "mw", "agent.run: parent_run_id from baggage"),
            ("call", "mw", "chat", "chat client request"),
            ("self", "chat", "llm.call (CLIENT): model, tokens"),
            ("ret", "chat", "mw", "response"),
            ("ret", "mw", "srv", "agent result"),
            ("ret", "srv", "cl", "A2A response (server span ends)"),
            ("ret", "cl", "dom", "answer (agent.delegate ends)"),
        ],
    ),
    Seq(
        "tool",
        "Tool execution (agent-as-tool peer delegation)",
        LIVE + " — LIVE18A002: tool.call(query_knowledge_base) → agent.delegate → rag_agent",
        [
            ("pa", "price_assist (MAF)"),
            ("tool", "collaboration_tools"),
            ("cl", "a2a.clients"),
            ("rag", "rag_agent node"),
        ],
        [
            ("call", "pa", "tool", "query_knowledge_base(question)"),
            ("note", "tool", "@trace_tool('query_knowledge_base', tool_type='a2a') → tool.call (CLIENT)"),
            ("call", "tool", "cl", "_consult_peer → ask_remote('rag_agent', …)"),
            ("self", "cl", "agent.delegate + A2A propagation (previous diagram)"),
            ("call", "cl", "rag", "A2A call"),
            ("ret", "rag", "cl", "answer / 'unavailable' soft-fail"),
            ("ret", "cl", "tool", "text"),
            ("ret", "tool", "pa", "tool result (authorization_decision=allow)"),
        ],
    ),
    Seq(
        "mcp",
        "MCP interaction (client → DataLayer fastmcp server)",
        "Observed in the live MCP probe MCPPROBE18A (scripts/mcp_probe.py, Opik, 2026-10-18, opik_verify PASS). In the mesh runs the data_agent model "
        "returns HTTP 404 before any MCP call, so the mesh path itself is source-derived",
        [
            ("da", "data_agent (MAF)"),
            ("fm", "GernasFunctionMiddleware"),
            ("mcpc", "MCPStreamableHTTPTool"),
            ("fmcp", "GernasFastMCPMiddleware"),
            ("db", "DataLayer view (MySQL)"),
        ],
        [
            ("call", "da", "fm", "function invocation (MCP tool)"),
            ("self", "fm", "tool.call (CLIENT, tool_type=mcp, dependency)"),
            ("call", "fm", "mcpc", "call_next()"),
            ("call", "mcpc", "fmcp", "tools/call  (_meta: traceparent, baggage)"),
            ("self", "fmcp", "attach_headers(_request_meta) → tool.call (SERVER)"),
            ("call", "fmcp", "db", "SQL view query"),
            ("ret", "db", "fmcp", "rows"),
            ("ret", "fmcp", "mcpc", "result (server span ends; payload size only)"),
            ("ret", "mcpc", "fm", "result"),
            ("ret", "fm", "da", "result (client span ends)"),
        ],
    ),
    Seq(
        "context",
        "Context and baggage propagation, background task",
        LIVE + " — conversation_summary step inside the request trace",
        [
            ("orc", "orchestrator"),
            ("ctx", "request_context"),
            ("bsp", "_BaggageSpanProcessor"),
            ("task", "gernas_task"),
            ("summ", "summarizer"),
        ],
        [
            ("call", "orc", "ctx", "enter: baggage session_id, task_id, tokenized_user_id"),
            ("note", "bsp", "on_start: copy allow-listed baggage onto every span"),
            ("call", "orc", "task", "spawn(summarize…) → gernas_task(coro)"),
            ("self", "task", "create_task with a copy of the current context"),
            ("call", "task", "summ", "run in background"),
            ("self", "summ", "agent.step conversation_summary → llm.call"),
            ("ret", "summ", "task", "done (same trace_id, same session_id)"),
            ("ret", "ctx", "orc", "exit: context detached (per-task, no leakage)"),
        ],
    ),
    Seq(
        "error",
        "Exception handling and expected policy outcomes",
        LIVE + " — rag_agent llm.call execution_status=error (LIVE18A002); guardrail deny with status OK and "
        "termination_reason=policy_block (LIVE18A003)",
        [("caller", "caller"), ("wr", "_wrap_async"), ("fn", "decorated function"), ("end", "_end"), ("span", "span")],
        [
            ("call", "caller", "wr", "await wrapper(*args)"),
            ("call", "wr", "fn", "await fn(*args)"),
            ("ret", "fn", "wr", "raises exc"),
            ("call", "wr", "end", "finally: _end(spec, span, call, exc, token)"),
            ("call", "end", "span", "spec.finish → stamp_outcome: execution_status, error_category"),
            ("call", "end", "span", "spec.failure(exc) → is_error?"),
            ("call", "end", "span", "error: record_exception (PII-scrubbed), status ERROR"),
            ("call", "end", "span", "expected outcome (deny / auth): status OK"),
            ("call", "end", "span", "span.end(); detach context"),
            ("ret", "wr", "caller", "re-raise exc (business behaviour unchanged)"),
        ],
    ),
    Seq(
        "export",
        "Span closure and telemetry export",
        "Live: Collector accepted 89 spans, 0 refused (LIVE18A); failure path: tests/test_collector_down.py, "
        "tests/test_lifecycle.py",
        [
            ("span", "span.end()"),
            ("bsp", "_GernasBatchSpanProcessor"),
            ("cnt", "CountingSpanExporter"),
            ("otlp", "OTLPSpanExporter"),
            ("col", "Collector :4317"),
        ],
        [
            ("call", "span", "bsp", "on_end(span) → queue (full → dropped_spans+1)"),
            ("self", "bsp", "worker thread: batch"),
            ("call", "bsp", "cnt", "export(batch)"),
            ("self", "cnt", "drop GERNAS_SUPPRESSED_SCOPES spans"),
            ("call", "cnt", "otlp", "export(batch)"),
            ("call", "otlp", "col", "OTLP/gRPC ExportTraceServiceRequest"),
            ("ret", "col", "otlp", "OK"),
            ("ret", "otlp", "cnt", "SUCCESS (failure / raise → dropped_spans += n)"),
            ("note", "span", "handle.flush() / shutdown() force-flush at exit"),
        ],
    ),
    Seq(
        "collector",
        "Collector processing to Opik and the data plane",
        "Opik branch observed live (otelcol_exporter_sent_spans{otlphttp/opik}=89, opik_verify PASS ×4). Data-plane "
        "branch observed with the local file exporter; the azuredataexplorer exporter is configured and validated "
        "but not exercised (no ADX)",
        [
            ("rcv", "otlp receiver"),
            ("proc", "memory_limiter, attributes/pdpl, redaction"),
            ("tail", "tail_sampling / probabilistic"),
            ("opik", "otlphttp/opik → Opik"),
            ("adx", "azuredataexplorer (file locally)"),
        ],
        [
            ("call", "rcv", "proc", "traces/opik + traces/dataplane pipelines"),
            ("self", "proc", "PDPL denylist, deny-by-default allow-list"),
            ("call", "proc", "tail", "traces/dataplane: tail_sampling/dataplane"),
            ("call", "proc", "tail", "traces/opik: probabilistic_sampler/opik"),
            ("call", "tail", "opik", "transform/opik_shape → batch → OTLP/HTTP"),
            ("call", "tail", "adx", "batch → OTelTraces (ADX) / dataplane-traces.json"),
            ("note", "adx", "metrics + logs pipelines → data plane only"),
        ],
    ),
]


# ── source analysis ─────────────────────────────────────────────────────────


def rel(path: Path) -> str:
    return path.resolve().relative_to(REPO).as_posix()


@dataclass
class Definition:
    name: str
    file: str
    line: int
    signature: str
    doc: str


def first_paragraph(doc: str | None) -> str:
    """First paragraph of a docstring, reST ``literals`` and :role:`refs` reduced to `code`."""
    if not doc:
        return ""
    text = " ".join(doc.strip().split("\n\n")[0].split())
    text = re.sub(r":[a-z]+:`~?([^`]+)`", r"`\1`", text)
    return text.replace("``", "`")


def index_sdk() -> tuple[dict[str, Definition], dict[str, dict]]:
    """Top-level functions / classes of the gernas package, and per-module summaries."""
    defs: dict[str, Definition] = {}
    modules: dict[str, dict] = {}
    for path in sorted((SDK / "gernas").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_"):
                    continue
                names.append(node.name)
                sig = f"({ast.unparse(node.args)})" if not isinstance(node, ast.ClassDef) else ""
                defs.setdefault(
                    node.name,
                    Definition(node.name, rel(path), node.lineno, sig, first_paragraph(ast.get_docstring(node))),
                )
        modules[rel(path)] = {
            "doc": first_paragraph(ast.get_docstring(tree)),
            "names": names,
            "lines": len(path.read_text(encoding="utf-8").splitlines()),
        }
    return defs, modules


def public_api() -> list[str]:
    tree = ast.parse((SDK / "gernas" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__all__" for t in node.targets):
            return [e.value for e in node.value.elts]  # type: ignore[attr-defined]
    return []


def span_registry() -> dict[str, dict]:
    data = yaml.safe_load((SDK / "registry" / "spans.yaml").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for s in data["spans"]:
        for api in str(s["api"]).split("/"):
            out[api.strip()] = s
    return out


def tests_by_api(names: list[str]) -> dict[str, list[str]]:
    wanted = set(names)
    found: dict[str, list[str]] = defaultdict(list)
    for path in sorted((SDK / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            used = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr in wanted:
                    used.add(sub.attr)
                elif isinstance(sub, ast.Name) and sub.id in wanted:
                    used.add(sub.id)
            for name in used:
                found[name].append(f"{path.name}::{node.name}")
    return found


def _is_sdk_module(module: str | None) -> bool:
    return module == "gernas" or (module or "").startswith("gernas.")


def mesh_helper_map() -> dict[str, list[str]]:
    """gernas_telemetry helper name → SDK APIs it delegates to."""
    tree = ast.parse(MESH_INTEGRATION.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}

    def sdk_refs(node: ast.AST) -> list[str]:
        refs: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "gernas":
                if sub.attr not in ("GernasConfigError", "__version__") and sub.attr not in refs:
                    refs.append(sub.attr)
            elif isinstance(sub, ast.ImportFrom) and _is_sdk_module(sub.module):
                refs += [a.name for a in sub.names if a.name not in refs]
            elif (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "_handle"
                and sub.attr in ("flush", "shutdown")
                and "GernasHandle" not in refs
            ):
                refs.append("GernasHandle")
            elif (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "_get"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
            ):
                refs.append(str(sub.args[0].value))
        return refs

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = sdk_refs(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = sdk_refs(node.value)
    return out


@dataclass
class Site:
    service: str
    file: str
    line: int
    where: str
    how: str  # decorator | call
    helper: str
    apis: list[str] = field(default_factory=list)


class _SiteVisitor(ast.NodeVisitor):
    def __init__(self, service: str, file: str, tree: ast.Module) -> None:
        self.service, self.file = service, file
        self.mesh_aliases: set[str] = set()
        self.sdk_aliases: set[str] = set()
        self.imported: dict[str, str] = {}  # local name → gernas symbol
        self.local: dict[str, tuple[str, str]] = {}  # local decorator name → (alias kind, helper)
        self.stack: list[str] = []
        self.sites: list[Site] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for a in node.names:
                    if a.name == "gernas_telemetry" and mod.endswith("observability"):
                        self.mesh_aliases.add(a.asname or a.name)
                    elif _is_sdk_module(mod):
                        self.imported[a.asname or a.name] = a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "gernas":
                        self.sdk_aliases.add(a.asname or a.name)

    def _ref(self, node: ast.AST) -> tuple[str, str] | None:
        """(kind, helper) if *node* refers to an SDK / mesh-integration symbol."""
        if isinstance(node, ast.Call):
            return self._ref(node.func)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in self.mesh_aliases:
                return ("mesh", node.attr)
            if node.value.id in self.sdk_aliases:
                return ("sdk", node.attr)
        if isinstance(node, ast.Name):
            if node.id in self.imported:
                return ("sdk", self.imported[node.id])
            if node.id in self.local:
                return self.local[node.id]
        return None

    def _where(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _add(self, line: int, how: str, ref: tuple[str, str], where: str | None = None) -> None:
        kind, helper = ref
        if helper in ("AVAILABLE", "Ctx", "GernasConfig", "GernasConfigError", "__version__"):
            return
        self.sites.append(
            Site(
                self.service,
                self.file,
                line,
                where or self._where(),
                how,
                f"{'_gt' if kind == 'mesh' else 'gernas'}.{helper}",
            )
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Name):
            return  # plain aliasing, e.g. the no-op fallback ``_b = _a`` in an ``except ImportError`` branch
        ref = self._ref(node.value)
        if ref is None and isinstance(node.value, ast.Call):
            ref = self._ref(node.value.func)
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if ref is not None and targets:
            self.local[targets[0]] = ref
            self._add(node.lineno, "call", ref, where=f"{self._where()} → {targets[0]}")
            for arg in getattr(node.value, "args", []):
                self.visit(arg)
            return
        self.generic_visit(node)

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        name = ".".join([*self.stack, node.name])
        for dec in node.decorator_list:
            ref = self._ref(dec)
            if ref is not None:
                self._add(dec.lineno, "decorator", ref, where=name)
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_def  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        ref = self._ref(node.func)
        if ref is not None:
            self._add(node.lineno, "call", ref)
        self.generic_visit(node)


def call_sites(helpers: dict[str, list[str]]) -> list[Site]:
    sites: list[Site] = []
    for service, base, patterns in APP_SOURCES:
        files = sorted({p for pat in patterns for p in base.glob(pat)})
        for path in files:
            if path.resolve() == MESH_INTEGRATION.resolve() or "tests" in path.parts or ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "gernas" not in text:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            visitor = _SiteVisitor(service, rel(path), tree)
            visitor.visit(tree)
            for site in visitor.sites:
                name = site.helper.split(".", 1)[1]
                site.apis = helpers.get(name, [name]) if site.helper.startswith("_gt.") else [name]
            sites.extend(visitor.sites)
    return sorted(sites, key=lambda s: (s.service, s.file, s.line))


# ── checklist parsing ───────────────────────────────────────────────────────

STATUS_RE = re.compile(r"`(\[[ x~!B]\])`|\| (N/A) \|")


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def parse_checklist() -> dict:
    text = CHECKLIST.read_text(encoding="utf-8")
    rows = []
    for line in section(text, "## 3. SOW and Enterprise Blueprint compliance matrix").splitlines():
        if line.startswith("| SOW-"):
            c = cells(line)
            status = c[6].strip("`")
            rows.append(
                {
                    "id": c[0],
                    "source": c[1],
                    "req": c[2],
                    "impl": c[3],
                    "gap": c[4],
                    "evidence": c[5],
                    "status": status,
                    "action": c[7],
                }
            )
    aspects, derived = [], {}
    sec = section(text, "## 3A. The five SOW aspects")
    for line in sec.splitlines():
        c = cells(line) if line.startswith("|") else []
        if len(c) == 4 and re.fullmatch(r"A\d|X", c[0]):
            aspects.append({"id": c[0], "name": c[1], "meaning": c[2], "rows": re.findall(r"SOW-\d{3}", c[3])})
        elif len(c) == 9 and re.fullmatch(r"A\d|X", c[0]):
            derived[c[0]] = {"counts": [int(x) for x in c[1:7]], "status": c[7], "why": c[8]}
    date = re.search(r"Last full re-verification: \*\*(\d{4}-\d{2}-\d{2})\*\*", text)
    findings = [cells(line) for line in section(text, "## 8.").splitlines() if re.match(r"\| F-\d", line)]
    gap_rows = [
        cells(line) for line in section(text, "## 12.").splitlines() if line.startswith("| ") and "---" not in line
    ]
    gap_header, gaps = (gap_rows[0], gap_rows[1:]) if gap_rows else ([], [])
    tests = [cells(line) for line in section(text, "## 10.").splitlines() if re.match(r"\| \d+ \|", line)]
    executive = section(text, "## 1. Executive status")
    return {
        "rows": rows,
        "aspects": aspects,
        "derived": derived,
        "date": date.group(1) if date else "",
        "findings": findings,
        "gaps": gaps,
        "gap_header": gap_header,
        "tests": tests,
        "executive": executive,
    }


ORDER = ["[x]", "[~]", "[ ]", "[!]", "[B]", "N/A"]


def aspect_status(counts: Counter, is_x: bool) -> str:
    if is_x:
        return "Not delivered" if counts["[x]"] == 0 else "Partial"
    if counts["[!]"]:
        return "Failed"
    if counts["[x]"] == sum(counts.values()):
        return "Verified"
    return "Partial"


def compute_aspects(data: dict) -> list[dict]:
    by_id = {r["id"]: r for r in data["rows"]}
    out = []
    assigned: list[str] = []
    for a in data["aspects"]:
        members = [by_id[i] for i in a["rows"] if i in by_id]
        assigned += a["rows"]
        counts = Counter(r["status"] for r in members)
        out.append({**a, "members": members, "counts": counts, "status": aspect_status(counts, a["id"] == "X")})
    missing = sorted(set(by_id) - set(assigned))
    dupes = [k for k, v in Counter(assigned).items() if v > 1]
    if missing or dupes:
        raise SystemExit(f"checklist §3A: unassigned rows {missing}, duplicated rows {dupes}")
    return out


def verify_derived(data: dict, aspects: list[dict]) -> list[str]:
    problems = []
    for a in aspects:
        want = [a["counts"].get(k, 0) for k in ORDER]
        got = data["derived"].get(a["id"])
        if got is None:
            problems.append(f"{a['id']}: missing from the derived-status table")
        elif got["counts"] != want or got["status"] != a["status"]:
            problems.append(
                f"{a['id']}: checklist says {got['counts']} {got['status']}, rows give {want} {a['status']}"
            )
    return problems


# ── SVG rendering ───────────────────────────────────────────────────────────


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def text_fit(label: str, width: float, size: float = 11.5) -> float:
    est = len(label) * size * 0.56
    return size if est <= width else max(8.5, size * width / est)


def seq_svg(seq: Seq) -> str:
    col = 190
    n = len(seq.participants)
    width = 30 + col * n + 230  # right margin for self-call labels on the last lane
    xs = {pid: 30 + col * i + col / 2 - 15 for i, (pid, _) in enumerate(seq.participants)}
    row = 30
    top = 64
    height = top + row * (len(seq.steps) + 1) + 20
    out = [
        f'<svg class="seq" width="{width}" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(seq.title)}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#1f3a5f"/></marker>'
        '<marker id="r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,0 L10,5 L0,10" fill="none" stroke="#5b6b7f"/></marker></defs>',
    ]
    for pid, label in seq.participants:
        x = xs[pid]
        size = text_fit(label, col - 24)
        out.append(f'<line x1="{x}" y1="{top - 14}" x2="{x}" y2="{height - 10}" class="life"/>')
        out.append(f'<rect x="{x - col / 2 + 8}" y="8" width="{col - 16}" height="40" rx="7" class="part"/>')
        out.append(
            f'<text x="{x}" y="33" text-anchor="middle" font-size="{size:.1f}" class="ptext">{esc(label)}</text>'
        )
    y = top
    for step in seq.steps:
        y += row
        kind = step[0]
        if kind in ("call", "ret"):
            _, a, b, label = step
            x1, x2 = xs[a], xs[b]
            cls, marker = ("msg", "a") if kind == "call" else ("ret", "r")
            pad = 6 if x2 > x1 else -6
            out.append(f'<line x1="{x1}" y1="{y}" x2="{x2 - pad}" y2="{y}" class="{cls}" marker-end="url(#{marker})"/>')
            span = abs(x2 - x1)
            if len(label) * 11.5 * 0.56 <= span - 12:  # fits above the arrow: centre it
                out.append(f'<text x="{(x1 + x2) / 2}" y="{y - 6}" text-anchor="middle">{esc(label)}</text>')
            else:  # readable size, anchored at the left end of the arrow
                out.append(f'<text x="{min(x1, x2) + 8}" y="{y - 6}">{esc(label)}</text>')
        elif kind == "self":
            _, a, label = step
            x = xs[a]
            out.append(f'<path d="M{x},{y - 10} h26 v14 h-22" class="msg" fill="none" marker-end="url(#a)"/>')
            out.append(f'<text x="{x + 32}" y="{y}">{esc(label)}</text>')
        elif kind == "note":
            _, a, label = step
            x = xs[a]
            w = min(max(len(label) * 6.4 + 16, 120), width - 20)
            left = min(max(10, x - w / 2), width - w - 10)
            out.append(f'<rect x="{left}" y="{y - 18}" width="{w}" height="22" rx="4" class="note"/>')
            out.append(f'<text x="{left + w / 2}" y="{y - 3}" text-anchor="middle" font-size="11">{esc(label)}</text>')
    out.append("</svg>")
    return "".join(out)


FLOW_NODES = [
    # id, x, y, w, h, title, subtitle, state
    (
        "mesh",
        20,
        20,
        300,
        64,
        "Agent Mesh (reference use case)",
        "run.py · api_server.py · a2a_server.py ×4 nodes",
        "ok",
    ),
    ("dl", 340, 20, 200, 64, "DataLayer MCP", "datalayer-as-service (fastmcp)", "ok"),
    ("rag", 560, 20, 220, 64, "RAG service", "MCP server · REST API · ingestion", "partial"),
    (
        "api",
        20,
        120,
        760,
        64,
        "Gernas SDK public APIs",
        "init_gernas · @trace_* decorators · request_context · adapters (MAF, A2A, ASGI, MCP, LangChain)",
        "ok",
    ),
    ("core", 20, 220, 370, 64, "SDK internals", "_core engine · RunState · safe_set + redaction · registry Ctx", "ok"),
    (
        "prop",
        410,
        220,
        370,
        64,
        "Context propagation",
        "TraceContext + GernasBaggagePropagator · baggage processor",
        "ok",
    ),
    (
        "otel",
        20,
        320,
        760,
        64,
        "OpenTelemetry SDK + OTLP exporter",
        "TracerProvider · MeterProvider · LoggerProvider → CountingSpanExporter → OTLP/gRPC :4317",
        "ok",
    ),
    (
        "col",
        20,
        420,
        760,
        64,
        "OpenTelemetry Collector (otelcol-contrib 0.162.0)",
        "Verified locally · AKS manifests schema-valid, not deployed (blocked)",
        "partial",
    ),
    ("opik", 20, 530, 240, 64, "Opik", "traces (otlphttp/opik) — verified live", "ok"),
    (
        "adx",
        280,
        530,
        250,
        64,
        "ADX data plane",
        "traces · metrics · logs — blocked (local file substitute verified)",
        "blocked",
    ),
    ("dash", 550, 530, 230, 64, "Dashboards · alerts · FinOps", "SOW §10–11 — not built (WP3)", "missing"),
]
FLOW_EDGES = [
    ("mesh", "api", "ok"),
    ("dl", "api", "ok"),
    ("rag", "api", "partial"),
    ("api", "core", "ok"),
    ("api", "prop", "ok"),
    ("core", "otel", "ok"),
    ("prop", "otel", "ok"),
    ("otel", "col", "ok"),
    ("col", "opik", "ok"),
    ("col", "adx", "blocked"),
    ("adx", "dash", "missing"),
]


def flow_svg() -> str:
    nodes = {n[0]: n for n in FLOW_NODES}
    out = [
        '<svg class="flow" viewBox="0 0 800 620" role="img" aria-label="Architecture flowchart" '
        'xmlns="http://www.w3.org/2000/svg">',
        '<defs><marker id="fa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#44546a"/></marker></defs>',
    ]
    for a, b, state in FLOW_EDGES:
        _, ax, ay, aw, ah, *_ = nodes[a]
        _, bx, by, bw, bh, *_ = nodes[b]
        x1 = ax + aw / 2
        x2 = bx + bw / 2
        if b == "dash":
            out.append(
                f'<line x1="{ax + aw}" y1="{ay + ah / 2}" x2="{bx - 4}" y2="{by + bh / 2}" '
                f'class="edge {state}" marker-end="url(#fa)"/>'
            )
            continue
        if a in ("mesh", "dl", "rag") or b in ("opik", "adx"):
            x2 = x1 if bx <= x1 <= bx + bw else bx + bw / 2
        out.append(
            f'<line x1="{x1}" y1="{ay + ah}" x2="{x2}" y2="{by - 4}" class="edge {state}" marker-end="url(#fa)"/>'
        )
    for _nid, x, y, w, h, title, sub, state in FLOW_NODES:
        out.append(
            f'<g class="node {state}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9"/>'
            f'<text x="{x + w / 2}" y="{y + 26}" text-anchor="middle" class="nt">{esc(title)}</text>'
            f'<text x="{x + w / 2}" y="{y + 46}" text-anchor="middle" font-size="{text_fit(sub, w - 16, 11):.1f}" '
            f'class="ns">{esc(sub)}</text></g>'
        )
    out.append("</svg>")
    return "".join(out)


# ── document assembly ───────────────────────────────────────────────────────

STATUS_CLASS = {"[x]": "s-ok", "[~]": "s-part", "[ ]": "s-none", "[!]": "s-fail", "[B]": "s-block", "N/A": "s-na"}
STATUS_LABEL = {
    "[x]": "Verified",
    "[~]": "Partial",
    "[ ]": "Not started",
    "[!]": "Failed",
    "[B]": "Blocked",
    "N/A": "N/A",
}
COMPONENT_TAGS = [
    ("SDK", r"\bSDK\b"),
    ("MESH", r"\bMESH\b|\bmesh\b"),
    ("COL", r"\bCOL\b|Collector"),
    ("OPIK", r"Opik"),
    ("ADX", r"\bADX\b"),
    ("PLAT", r"\bPLAT\b|[Pp]latform"),
    ("DASH", r"\bDASH\b"),
]

MD_LINK = re.compile(r"`([^`]+)`")


def md_inline(s: str) -> str:
    return MD_LINK.sub(lambda m: f"<code>{m.group(1)}</code>", esc(s).replace("**", ""))


def components(row: dict) -> list[str]:
    text = f"{row['impl']} {row['gap']}"
    return [tag for tag, pat in COMPONENT_TAGS if re.search(pat, text)] or ["other"]


def anchor(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build(data: dict) -> tuple[str, str]:
    defs, modules = index_sdk()
    api_names = [
        n for n in public_api() if n not in ("__version__", "Ctx", "SpanName", "Metric", "DROP", "GernasConfigError")
    ]
    adapter_names = [
        "gernas_middleware",
        "disable_native_telemetry",
        "GernasA2AInterceptor",
        "GernasASGIMiddleware",
        "GernasFastMCPMiddleware",
        "mcp_meta_context",
        "GernasCallbackHandler",
    ]
    catalogue_names = api_names + [n for n in adapter_names if n not in api_names]
    spans = span_registry()
    tests = tests_by_api(catalogue_names)
    helpers = mesh_helper_map()
    sites = call_sites(helpers)
    aspects = compute_aspects(data)
    usage: dict[str, list[Site]] = defaultdict(list)
    for s in sites:
        for api in s.apis:
            usage[api].append(s)
    unknown = [n for n in catalogue_names if n not in FACTS]
    if unknown:
        raise SystemExit(f"FACTS missing for public APIs: {unknown}")

    total = Counter(r["status"] for r in data["rows"])
    date = data["date"]

    # ---------- Markdown ----------
    md = [
        "# Gernas SDK — API catalogue and Agent Mesh instrumentation map",
        "",
        f"> **Generated** by `gernas-sdk/scripts/build_architecture_doc.py` from the source and `checklist.md` "
        f"(re-verification {date}). Do not edit by hand; re-run the script after code changes. "
        "Line numbers are exact for the committed source.",
        "",
        "## 1. Public APIs",
        "",
        "| API | Group | Defined at | Span (registry) | Agent Mesh / service call sites | Tests |",
        "|---|---|---|---|---|---|",
    ]
    for name in catalogue_names:
        d = defs.get(name)
        s = spans.get(name)
        span = f"`{s['name']}` ({s['kind']})" if s else "—"
        md.append(
            f"| `{name}` | {FACTS[name]['group']} | `{d.file}:{d.line}` | {span} | {len(usage.get(name, []))} | "
            f"{len(tests.get(name, []))} |"
            if d
            else f"| `{name}` | {FACTS[name]['group']} | (not found) | {span} | {len(usage.get(name, []))} | "
            f"{len(tests.get(name, []))} |"
        )
    md += [
        "",
        "## 2. Call sites (Agent Mesh, DataLayer MCP, RAG service)",
        "",
        "`_gt` is the mesh's single integration module `agent-mesh/src/observability/gernas_telemetry.py`; "
        "the SDK API column is what that helper delegates to.",
        "",
        "| Service | Location | Where | How | Helper | SDK API |",
        "|---|---|---|---|---|---|",
    ]
    for s in sites:
        md.append(
            f"| {s.service} | `{s.file}:{s.line}` | `{s.where}` | {s.how} | `{s.helper}` | "
            f"{', '.join(f'`{a}`' for a in s.apis) or '—'} |"
        )
    md += [
        "",
        "## 3. The five SOW aspects (derived from `checklist.md` §3 / §3A)",
        "",
        "| Aspect | Principle | Rows | Verified | Partial | Not started | Failed | Blocked | N/A | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in aspects:
        c = a["counts"]
        md.append(
            f"| {a['id']} | {a['name']} | {len(a['members'])} | "
            + " | ".join(str(c.get(k, 0)) for k in ORDER)
            + f" | {a['status']} |"
        )
    md.append("")
    markdown = "\n".join(md)

    # ---------- HTML ----------
    h: list[str] = []
    add = h.append
    add(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Gernas SDK — Architecture and Instrumentation</title>"
    )
    add(f"<style>{CSS}</style></head><body>")
    add(
        "<button id='navtoggle' aria-label='Toggle navigation'>☰ Sections</button><nav id='nav'><div class='brand'>Gernas SDK</div>"
    )
    for key, title in NAV:
        add(f"<a href='#{key}'>{title}</a>")
    add("</nav><main>")
    add(
        f"<header><h1>Gernas SDK — Architecture and Instrumentation Guide</h1>"
        f"<p class='meta'>Generated from the source and <code>checklist.md</code> · re-verification {esc(date)} · "
        f"generator <code>gernas-sdk/scripts/build_architecture_doc.py</code> · works offline (no external scripts)</p>"
        f"</header>"
    )

    # A. overview
    add("<section id='overview'><h2>A. Executive overview</h2>")
    add(
        "<div class='banner warn'><strong>Overall: not fully verified against the SOW.</strong> "
        "The SDK, the Agent Mesh integration and the Collector → Opik path are implemented and verified with live "
        "traces; dashboards, alerting, KPI governance, FinOps allocation and platform telemetry are not delivered by "
        "this repository, and ADX / AKS are blocked on infrastructure.</div>"
    )
    add("<div class='cards'>")
    for k in ORDER:
        add(
            f"<div class='card {STATUS_CLASS[k]}'><div class='num'>{total.get(k, 0)}</div>"
            f"<div>{STATUS_LABEL[k]}</div></div>"
        )
    add(f"<div class='card'><div class='num'>{len(data['rows'])}</div><div>SOW requirements</div></div></div>")
    add(
        "<div class='grid2'><div><h3>What each part does</h3><dl>"
        "<dt>Gernas SDK</dt><dd>The central toolkit (<code>pip install gernas-telemetry</code>, <code>import gernas</code>): "
        "one init call, decorators and adapters that emit blueprint-named spans, metrics and logs, context propagation, "
        "PDPL redaction. No vendor SDK; OTLP only.</dd>"
        "<dt>Agent Mesh</dt><dd>The reference use case. It consumes the SDK through one integration module "
        "(<code>src/observability/gernas_telemetry.py</code>) and decorators at its business seams.</dd>"
        "<dt>OpenTelemetry Collector</dt><dd>The gateway: receives OTLP, applies the PDPL denylist and allow-list, "
        "samples, reshapes for Opik and fans out to Opik and the ADX data plane.</dd>"
        "<dt>Opik</dt><dd>Developer trace view (traces only).</dd>"
        "<dt>ADX</dt><dd>The data plane for traces, metrics and logs (dashboards and KPIs are computed there).</dd>"
        "</dl></div><div><h3>Five SOW aspects (blueprint §1)</h3><table class='mini'><tr><th>Aspect</th><th>Status</th></tr>"
    )
    for a in aspects:
        add(
            f"<tr><td>{a['id']} · {esc(a['name'])}</td><td><span class='pill {pill(a['status'])}'>{esc(a['status'])}</span></td></tr>"
        )
    add(
        "</table><p class='small'>Aspect status is computed from the member SOW rows (all verified → Verified). "
        "See section H.</p></div></div></section>"
    )

    # B. flowchart
    add(
        "<section id='flow'><h2>B. Architecture flowchart</h2>"
        "<p>Control and data flow as implemented. Solid green edges are verified with live traces; dashed amber "
        "edges are configured but blocked; dotted grey are not built.</p>"
    )
    add(flow_svg())
    add(
        "<div class='legend'><span class='lg ok'>verified</span><span class='lg partial'>partially verified</span>"
        "<span class='lg blocked'>blocked</span><span class='lg missing'>not built</span></div>"
        "<details><summary>Supporting signals</summary><ul>"
        "<li><b>Metrics</b>: SDK instruments from <code>registry/metrics.yaml</code> → MeterProvider → Collector "
        "<code>metrics/dataplane</code> → ADX (local file verified).</li>"
        "<li><b>Logs</b>: <code>GernasLogHandler</code> (OTel logs bridge) → Collector <code>logs/dataplane</code> → ADX; "
        "application logs carry ids from <code>gernas.correlation()</code>.</li>"
        "<li><b>SDK health</b>: <code>gernas.sdk.dropped_spans / dropped_logs / dropped_attributes / redactions</code>; "
        "Collector health on <code>:13133</code>, internal metrics on <code>:8888</code>.</li></ul></details></section>"
    )

    # C. code structure
    add(
        "<section id='code'><h2>C. Code structure explorer</h2>"
        "<input class='search' data-target='codetable' placeholder='Filter modules…'>"
        "<table id='codetable' class='data'><thead><tr><th>Module</th><th>Purpose</th><th>Public symbols</th>"
        "<th>Lines</th><th>Used by app call sites</th></tr></thead><tbody>"
    )
    module_usage: Counter = Counter()
    for s in sites:
        for api in s.apis:
            if api in defs:
                module_usage[defs[api].file] += 1
    for path, info in modules.items():
        if path.endswith("__init__.py") and not info["names"] and not info["doc"]:
            continue
        add(
            f"<tr id='m-{anchor(path)}'><td><code>{esc(path)}</code></td><td>{md_inline(info['doc'])}</td>"
            f"<td>{', '.join(f'<code>{esc(n)}</code>' for n in info['names'][:12])}</td><td>{info['lines']}</td>"
            f"<td>{module_usage.get(path, 0)}</td></tr>"
        )
    add("</tbody></table>")
    add("<details><summary>Agent Mesh and service files that use the SDK</summary><ul>")
    for f, n in Counter(s.file for s in sites).most_common():
        add(f"<li><code>{esc(f)}</code> — {n} call site(s)</li>")
    add("</ul></details></section>")

    # D. catalogue
    add(
        "<section id='api'><h2>D. Decorator and method catalogue</h2>"
        "<div class='filters'><input class='search' data-target='apitable' placeholder='Search APIs…'>"
        "<select class='colfilter' data-target='apitable' data-col='1'><option value=''>All groups</option>"
    )
    for g in sorted({FACTS[n]["group"] for n in catalogue_names}):
        add(f"<option>{esc(g)}</option>")
    add(
        "</select></div><table id='apitable' class='data'><thead><tr><th>API</th><th>Group</th><th>Defined at</th>"
        "<th>Span</th><th>App call sites</th><th>Tests</th></tr></thead><tbody>"
    )
    for name in catalogue_names:
        d = defs.get(name)
        s = spans.get(name)
        f = FACTS[name]
        span = f"<code>{esc(s['name'])}</code> {esc(s['kind'])}" if s else "—"
        uses = usage.get(name, [])
        add(
            f"<tr><td><a href='#api-{anchor(name)}'><code>{esc(name)}</code></a></td><td>{esc(f['group'])}</td>"
            f"<td><code>{esc(f'{d.file}:{d.line}' if d else '?')}</code></td><td>{span}</td><td>{len(uses)}</td>"
            f"<td>{len(tests.get(name, []))}</td></tr>"
        )
    add("</tbody></table>")
    for name in catalogue_names:
        d = defs.get(name)
        s = spans.get(name)
        f = FACTS[name]
        uses = usage.get(name, [])
        t = tests.get(name, [])
        req = ", ".join(f"<code>gernas.{esc(x.lower())}</code>" for x in (s or {}).get("required", [])) if s else "—"
        add(
            f"<details class='api' id='api-{anchor(name)}'><summary><code>{esc(name)}</code> "
            f"<span class='muted'>{esc(f['group'])}</span></summary><table class='kv'>"
            f"<tr><th>Defined at</th><td><code>{esc(f'{d.file}:{d.line}' if d else '?')}</code></td></tr>"
            f"<tr><th>Signature</th><td><code>{esc(name + (d.signature if d else ''))}</code></td></tr>"
            f"<tr><th>Purpose (docstring)</th><td>{md_inline(d.doc if d else '')}</td></tr>"
            f"<tr><th>Span behaviour</th><td>{esc(f['span'])}</td></tr>"
            f"<tr><th>Required attributes (registry)</th><td>{req}</td></tr>"
            f"<tr><th>Context / baggage</th><td>{esc(f['context'])}</td></tr>"
            f"<tr><th>Errors</th><td>{esc(f['errors'])}</td></tr>"
            f"<tr><th>Sync / async</th><td>{esc(f['async'])}</td></tr>"
            f"<tr><th>App call sites</th><td>{site_links(uses)}</td></tr>"
            f"<tr><th>Tests</th><td>{', '.join(f'<code>{esc(x)}</code>' for x in t[:8])}"
            f"{f' … +{len(t) - 8} more' if len(t) > 8 else ''}{'none found by name' if not t else ''}</td></tr>"
            "</table></details>"
        )
    add(
        "<p class='small'>Every decorator runs on one engine (<code>gernas/decorators/_core.py</code>): the envelope "
        "first, spans closed in <code>finally</code> on every exit path (return, exception, cancellation, generator "
        "close), exceptions re-raised unchanged, telemetry failures never reach the application, objects with "
        "<code>async __call__</code> and sync callables returning a coroutine timed until the coroutine completes "
        "(F-33).</p></section>"
    )

    # E. sequences
    add(
        "<section id='seq'><h2>E. Sequence diagrams</h2><p>Participants and calls use the exact class, decorator and "
        "method names in the source. Each diagram states whether it was observed in a runtime trace.</p>"
    )
    for seq in SEQUENCES:
        live = seq.evidence.startswith("Observed") or seq.evidence.startswith("Live") or "observed live" in seq.evidence
        add(
            f"<details class='seqbox' id='seq-{seq.key}' open><summary>{esc(seq.title)}</summary>"
            f"<p class='evidence {'live' if live else 'derived'}'>{esc(seq.evidence)}</p>"
            f"<div class='scroll'>{seq_svg(seq)}</div></details>"
        )
    add("</section>")

    # F. instrumentation map
    add(
        "<section id='map'><h2>F. Agent Mesh instrumentation map</h2>"
        "<p><code>_gt</code> = <code>agent-mesh/src/observability/gernas_telemetry.py</code>, the mesh's single "
        "integration module; it degrades to pass-through decorators when the SDK is absent.</p>"
        "<div class='filters'><input class='search' data-target='maptable' placeholder='Search call sites…'>"
        "<select class='colfilter' data-target='maptable' data-col='0'><option value=''>All services</option>"
    )
    for svc in sorted({s.service for s in sites}):
        add(f"<option>{esc(svc)}</option>")
    add("</select><select class='colfilter' data-target='maptable' data-col='5'><option value=''>All SDK APIs</option>")
    for api in sorted({a for s in sites for a in s.apis}):
        add(f"<option>{esc(api)}</option>")
    add(
        "</select></div><table id='maptable' class='data'><thead><tr><th>Service</th><th>Location</th>"
        "<th>Function / class</th><th>How</th><th>Helper</th><th>SDK API</th></tr></thead><tbody>"
    )
    for s in sites:
        apis = ", ".join(
            f"<a href='#api-{anchor(a)}'><code>{esc(a)}</code></a>" if a in FACTS else f"<code>{esc(a)}</code>"
            for a in s.apis
        )
        add(
            f"<tr id='site-{anchor(s.file)}-{s.line}'><td>{esc(s.service)}</td><td><code>{esc(s.file)}:{s.line}</code></td>"
            f"<td><code>{esc(s.where)}</code></td><td>{s.how}</td><td><code>{esc(s.helper)}</code></td><td>{apis}</td></tr>"
        )
    add("</tbody></table>")
    add(
        "<details><summary>Integration helpers in <code>gernas_telemetry.py</code> → SDK APIs</summary><table class='data'>"
        "<tr><th>Helper</th><th>Delegates to</th></tr>"
    )
    for k, v in helpers.items():
        if v:
            add(f"<tr><td><code>{esc(k)}</code></td><td>{', '.join(f'<code>{esc(x)}</code>' for x in v)}</td></tr>")
    add("</table></details></section>")

    # G. quick start
    add(f"<section id='run'><h2>G. End-to-end execution (quick start)</h2>{QUICKSTART}</section>")

    # H. SOW dashboard
    add(
        "<section id='sow'><h2>H. SOW compliance dashboard</h2>"
        "<p>Counts are computed from <code>checklist.md</code> §3 at generation time.</p><div class='aspects'>"
    )
    for a in aspects:
        c = a["counts"]
        tot = max(sum(c.values()), 1)
        bar = "".join(
            f"<span class='{STATUS_CLASS[k]}' style='width:{100 * c.get(k, 0) / tot:.1f}%' "
            f"title='{STATUS_LABEL[k]}: {c.get(k, 0)}'></span>"
            for k in ORDER
            if c.get(k)
        )
        why = data["derived"].get(a["id"], {}).get("why", "")
        add(
            f"<div class='aspect'><h3>{a['id']} · {esc(a['name'])} <span class='pill {pill(a['status'])}'>"
            f"{esc(a['status'])}</span></h3><p class='small'>{esc(a['meaning'])}</p><div class='bar'>{bar}</div>"
            f"<p class='small'>{' · '.join(f'{STATUS_LABEL[k]} {c.get(k, 0)}' for k in ORDER if c.get(k))}</p>"
            f"<p class='small'><b>Why not verified:</b> {esc(why) or '—'}</p></div>"
        )
    add(
        "</div><div class='filters'><input class='search' data-target='sowtable' placeholder='Search requirements…'>"
        "<select class='colfilter' data-target='sowtable' data-col='1'><option value=''>All aspects</option>"
    )
    for a in aspects:
        add(f"<option>{a['id']}</option>")
    add(
        "</select><select class='colfilter' data-target='sowtable' data-col='4'><option value=''>All components</option>"
    )
    for tag, _ in COMPONENT_TAGS:
        add(f"<option>{tag}</option>")
    add("</select><select class='colfilter' data-target='sowtable' data-col='5'><option value=''>All statuses</option>")
    for k in ORDER:
        add(f"<option>{STATUS_LABEL[k]}</option>")
    add(
        "</select><select class='colfilter' data-target='sowtable' data-col='2'><option value=''>All SOW sections</option>"
    )
    for src in sorted({r["source"] for r in data["rows"]}):
        add(f"<option>{esc(src)}</option>")
    add(
        "</select></div><table id='sowtable' class='data'><thead><tr><th>ID</th><th>Aspect</th><th>SOW section</th>"
        "<th>Requirement</th><th>Components</th><th>Status</th><th>Evidence</th><th>Gap / action</th></tr></thead><tbody>"
    )
    aspect_of = {rid: a["id"] for a in aspects for rid in a["rows"]}
    for r in data["rows"]:
        add(
            f"<tr><td>{r['id']}</td><td>{aspect_of.get(r['id'], '')}</td><td>{esc(r['source'])}</td>"
            f"<td>{md_inline(r['req'])}</td><td>{' '.join(components(r))}</td>"
            f"<td><span class='pill {STATUS_CLASS[r['status']]}'>{STATUS_LABEL[r['status']]}</span></td>"
            f"<td>{md_inline(r['evidence'])}</td><td>{gap_action(r)}</td></tr>"
        )
    add("</tbody></table></section>")

    # I. gaps
    add(
        "<section id='gaps'><h2>I. Known gaps and limitations</h2><h3>Outstanding (checklist §12)</h3>"
        "<table class='data'><tr>" + "".join(f"<th>{esc(h)}</th>" for h in data["gap_header"]) + "</tr>"
    )
    for g in data["gaps"]:
        add("<tr>" + "".join(f"<td>{md_inline(x)}</td>" for x in g) + "</tr>")
    add(
        "</table><h3>Open findings (checklist §8)</h3><table class='data'><tr><th>ID</th><th>Severity</th><th>File</th>"
        "<th>Observed</th><th>Action</th></tr>"
    )
    for f in data["findings"]:
        if len(f) >= 6 and ("Open" in f[5] or f[1] == "Decision"):
            add(
                f"<tr><td>{esc(f[0])}</td><td>{esc(f[1])}</td><td>{md_inline(f[2])}</td><td>{md_inline(f[3])}</td>"
                f"<td>{md_inline(f[5])}</td></tr>"
            )
    add(
        "</table><h3>Test runs that did not fully pass (checklist §10)</h3><table class='data'>"
        "<tr><th>#</th><th>Command</th><th>Result</th></tr>"
    )
    for t in data["tests"]:
        if re.search(r"(?i)\b(fail|failed|hangs?|blocked)\b", t[2]):
            add(f"<tr><td>{esc(t[0])}</td><td>{md_inline(t[1])}</td><td>{md_inline(t[2])}</td></tr>")
    add("</table></section>")

    add(
        f"<footer>Generated {esc(date)} from commit-tracked sources. Runbook: "
        "<code>gernas-sdk/docs/GERNAS_SDK_END_TO_END_RUNBOOK.md</code> · Compliance report: "
        "<code>gernas-sdk/docs/SOW_COMPLIANCE_REPORT.md</code> · Checklist: <code>checklist.md</code></footer>"
    )
    add(f"</main><script>{JS}</script></body></html>")
    return "\n".join(h) + "\n", markdown


def gap_action(row: dict) -> str:
    parts = [md_inline(x) for x in (row["gap"], row["action"]) if x and x != "—"]
    return " → ".join(parts) or "—"


def pill(status: str) -> str:
    return {"Verified": "s-ok", "Partial": "s-part", "Failed": "s-fail", "Not delivered": "s-none"}.get(
        status, "s-none"
    )


def site_links(uses: list[Site]) -> str:
    if not uses:
        return "none in Agent Mesh / services (exercised by tests and examples)"
    out = [
        f"<a href='#site-{anchor(s.file)}-{s.line}'><code>{esc(s.file)}:{s.line}</code></a> ({esc(s.where)})"
        for s in uses[:10]
    ]
    more = f" … +{len(uses) - 10} more" if len(uses) > 10 else ""
    return "<br>".join(out) + more


NAV = [
    ("overview", "A. Overview"),
    ("flow", "B. Architecture flowchart"),
    ("code", "C. Code structure"),
    ("api", "D. API catalogue"),
    ("seq", "E. Sequence diagrams"),
    ("map", "F. Agent Mesh map"),
    ("run", "G. Run end to end"),
    ("sow", "H. SOW dashboard"),
    ("gaps", "I. Gaps and limitations"),
]

QUICKSTART = """
<p>Full procedure, expected output and troubleshooting: <code>gernas-sdk/docs/GERNAS_SDK_END_TO_END_RUNBOOK.md</code>.
Commands below are the ones used for the 2026-10-18 live verification (Windows PowerShell, repository root
<code>agent-mesh/</code>, virtual environment <code>.venv</code>).</p>
<ol class='steps'>
<li><b>Install</b> (once): <code>.venv\\Scripts\\pip install -e "./gernas-sdk[dev]"</code> and the mesh requirements.</li>
<li><b>Configure</b> <code>agent-mesh/.env</code>: <code>GERNAS_OTLP_ENDPOINT</code>, <code>GERNAS_USE_CASE_ID</code>,
<code>GERNAS_APPLICATION_ID</code>, <code>GERNAS_BUSINESS_DOMAIN</code>, <code>GERNAS_ENVIRONMENT</code>,
<code>OPIK_API_KEY</code> / <code>OPIK_WORKSPACE</code> / <code>OPIK_PROJECT_NAME</code>, <code>GROQ_API_KEY</code>.</li>
<li><b>Collector</b>: <code>.venv\\Scripts\\python gernas-sdk\\scripts\\run_collector.py --env agent-mesh\\.env</code>
(ready on <code>:13133</code>, OTLP on <code>:4317</code>).</li>
<li><b>Backing services</b>: DataLayer MCP <code>:9100</code> and RAG MCP <code>:9000</code>
(<code>MCP_TRANSPORT=http</code>, <code>python -m mcp_server.server</code> / <code>python -m mcp_integration.server</code>).</li>
<li><b>Mesh nodes</b>: <code>cd agent-mesh; python launch_mesh.py</code> (ports 8015–8018).</li>
<li><b>Workflow</b>: <code>python demos\\gernas_e2e_queries.py --prefix LIVE18A</code> — prints one JSON line per request with its
<code>request_id</code> (= GERNAS <code>task_id</code>).</li>
<li><b>Verify in Opik</b>: <code>cd gernas-sdk; python scripts\\opik_verify.py --task-id LIVE18A000</code> → <code>RESULT: PASS</code>
(one trace, all parents resolve, conformance, only Gernas spans).</li>
<li><b>Collector counters</b>: <code>curl localhost:8888/metrics</code> → <code>otelcol_receiver_accepted_spans</code> =
<code>otelcol_exporter_sent_spans{exporter="otlphttp/opik"}</code>, no <code>send_failed</code>.</li>
</ol>"""

CSS = """
:root{--bg:#f6f7f9;--fg:#1d2733;--muted:#5b6b7f;--line:#d8dee6;--ok:#1f8a4c;--part:#c98a00;--none:#8a96a6;
--fail:#c0392b;--block:#d35400;--na:#b0b8c4;--accent:#1f3a5f}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
color:var(--fg);background:var(--bg)}
nav{position:fixed;top:0;left:0;bottom:0;width:230px;background:#16263a;padding:18px 12px;overflow:auto}
nav .brand{color:#fff;font-weight:700;font-size:18px;margin:0 8px 14px}nav a{display:block;color:#c9d6e6;
text-decoration:none;padding:6px 8px;border-radius:6px;font-size:14px}nav a:hover{background:#24405f;color:#fff}
#navtoggle{display:none}main{margin-left:230px;padding:24px 36px 60px;max-width:1400px}
header h1{margin:0 0 4px;font-size:26px}.meta,.small{color:var(--muted);font-size:13px}
section{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 22px;margin:18px 0}
h2{margin-top:0;color:var(--accent);font-size:21px}h3{font-size:16px;margin:14px 0 6px}
code{font:12.5px/1.4 ui-monospace,Consolas,monospace;background:#eef2f6;padding:1px 4px;border-radius:4px}
.banner{padding:12px 14px;border-radius:8px;margin:8px 0 14px}.warn{background:#fff6e0;border:1px solid #f0d58c}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:12px}.card{flex:1 1 110px;border:1px solid var(--line);
border-radius:10px;padding:10px 12px;border-top:4px solid var(--accent)}.card .num{font-size:26px;font-weight:700}
.card.s-ok{border-top-color:var(--ok)}.card.s-part{border-top-color:var(--part)}.card.s-none{border-top-color:var(--none)}
.card.s-fail{border-top-color:var(--fail)}.card.s-block{border-top-color:var(--block)}.card.s-na{border-top-color:var(--na)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px}dt{font-weight:600;margin-top:8px}dd{margin:2px 0 0 0;color:#2c3a4a}
table{border-collapse:collapse;width:100%}table.data th,table.data td,table.mini td,table.mini th{border-bottom:1px solid
var(--line);padding:6px 8px;text-align:left;vertical-align:top;font-size:13.5px}table.data th{background:#eef2f6;
position:sticky;top:0}table.kv th{width:200px;text-align:left;vertical-align:top;color:var(--muted);font-weight:600;
padding:4px 8px}table.kv td{padding:4px 8px;font-size:14px}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;color:#fff;white-space:nowrap}
.pill.s-ok{background:var(--ok)}.pill.s-part{background:var(--part)}.pill.s-none{background:var(--none)}
.pill.s-fail{background:var(--fail)}.pill.s-block{background:var(--block)}.pill.s-na{background:var(--na)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.search,select{padding:7px 10px;border:1px solid var(--line);
border-radius:7px;font-size:14px}.search{min-width:260px}
details{margin:8px 0}summary{cursor:pointer;font-weight:600}details.api{border:1px solid var(--line);border-radius:8px;
padding:6px 10px}.muted{color:var(--muted);font-weight:400;font-size:13px}
.scroll{overflow-x:auto}svg.seq{max-width:none;height:auto;font:11.5px system-ui,sans-serif}
svg.seq text{fill:#1d2733}svg.seq .part{fill:#e8eef6;stroke:#1f3a5f}svg.seq .ptext{font-weight:600}
svg.seq .life{stroke:#9aa8b8;stroke-dasharray:4 4}svg.seq .msg{stroke:#1f3a5f;stroke-width:1.4}
svg.seq .ret{stroke:#5b6b7f;stroke-dasharray:5 4}svg.seq .note{fill:#fff6d6;stroke:#e0c060}
.evidence{font-size:13px;padding:6px 10px;border-radius:6px}.evidence.live{background:#e6f4ec;color:#145c33}
.evidence.derived{background:#eef2f6;color:#33475b}
svg.flow{width:100%;max-width:900px;height:auto;font-family:system-ui,sans-serif}svg.flow .nt{font-weight:700;font-size:14px}
svg.flow .ns{fill:#33475b}svg.flow .node rect{stroke-width:2}svg.flow .ok rect{fill:#e6f4ec;stroke:var(--ok)}
svg.flow .partial rect{fill:#fff6e0;stroke:var(--part)}svg.flow .blocked rect{fill:#fdebe0;stroke:var(--block);
stroke-dasharray:6 4}svg.flow .missing rect{fill:#f1f3f6;stroke:var(--none);stroke-dasharray:2 4}
svg.flow .edge{stroke-width:2}svg.flow .edge.ok{stroke:var(--ok)}svg.flow .edge.partial{stroke:var(--part)}
svg.flow .edge.blocked{stroke:var(--block);stroke-dasharray:7 5}svg.flow .edge.missing{stroke:var(--none);
stroke-dasharray:2 5}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;margin:6px 0}.lg:before{content:"";display:inline-block;
width:22px;height:0;border-top:3px solid;margin-right:6px;vertical-align:middle}.lg.ok:before{border-color:var(--ok)}
.lg.partial:before{border-color:var(--part)}.lg.blocked:before{border-color:var(--block);border-top-style:dashed}
.lg.missing:before{border-color:var(--none);border-top-style:dotted}
.aspects{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}.aspect{border:1px solid
var(--line);border-radius:10px;padding:10px 14px}.aspect h3{margin:2px 0}.bar{display:flex;height:12px;border-radius:6px;
overflow:hidden;background:#eef2f6}.bar span{display:block;height:100%}.bar .s-ok{background:var(--ok)}
.bar .s-part{background:var(--part)}.bar .s-none{background:var(--none)}.bar .s-fail{background:var(--fail)}
.bar .s-block{background:var(--block)}.bar .s-na{background:var(--na)}
ol.steps li{margin:6px 0}table.data td:first-child{white-space:nowrap}footer{color:var(--muted);font-size:13px;margin-top:20px}
@media (max-width:900px){nav{display:none;width:100%;bottom:auto;position:fixed;z-index:10}nav.open{display:block}
#navtoggle{display:block;position:fixed;top:8px;right:8px;z-index:11;padding:8px 12px;border-radius:8px;border:0;
background:#16263a;color:#fff}main{margin-left:0;padding:16px}.grid2{grid-template-columns:1fr}.search{min-width:0;
width:100%}}
"""

JS = """
function applyFilters(id){var t=document.getElementById(id);if(!t)return;
var q=(document.querySelector(".search[data-target='"+id+"']")||{value:""}).value.toLowerCase();
var sels=document.querySelectorAll("select.colfilter[data-target='"+id+"']");
t.querySelectorAll("tbody tr").forEach(function(r){var ok=!q||r.textContent.toLowerCase().indexOf(q)>=0;
sels.forEach(function(s){if(s.value){var c=r.children[+s.dataset.col];if(!c||c.textContent.indexOf(s.value)<0)ok=false;}});
r.style.display=ok?"":"none";});}
document.querySelectorAll(".search,select.colfilter").forEach(function(el){
el.addEventListener("input",function(){applyFilters(el.dataset.target);});});
document.getElementById("navtoggle").addEventListener("click",function(){
document.getElementById("nav").classList.toggle("open");});
document.querySelectorAll("a[href^='#api-'],a[href^='#site-']").forEach(function(a){a.addEventListener("click",
function(){var el=document.querySelector(a.getAttribute("href"));if(el&&el.tagName==="DETAILS")el.open=true;
if(el&&el.tagName==="TR"){var t=el.closest("table");t.querySelectorAll("tbody tr").forEach(function(r){r.style.display="";});
el.style.background="#fff6d6";setTimeout(function(){el.style.background="";},2500);}});});
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the generated files are stale")
    args = ap.parse_args()
    data = parse_checklist()
    aspects = compute_aspects(data)
    problems = verify_derived(data, aspects)
    html_text, md_text = build(data)
    if args.check:
        for path, text in ((OUT_HTML, html_text), (OUT_MD, md_text)):
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                problems.append(f"{rel(path)} is stale: run scripts/build_architecture_doc.py")
        for p in problems:
            print(p, file=sys.stderr)
        if not problems:
            print("architecture documentation up to date")
        return 1 if problems else 0
    for p in problems:
        print("WARNING:", p, file=sys.stderr)
    OUT_HTML.write_text(html_text, encoding="utf-8", newline="\n")
    OUT_MD.write_text(md_text, encoding="utf-8", newline="\n")
    print(f"wrote {rel(OUT_HTML)} and {rel(OUT_MD)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
