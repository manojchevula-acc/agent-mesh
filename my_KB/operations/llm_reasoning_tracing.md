# LLM Reasoning Transparency & Execution Tracing

Two complementary observability features that make AgentMesh's decision-making inspectable.

---

## 1. LLM Reasoning Capture

**File:** `src/tracing/llm_reasoning.py`

### What it is

Agents embed structured `<llm_reasoning>` JSON blocks at key decision points in their responses. These blocks expose *why* an agent made a particular decision — which safety checks passed, how intent was classified, which tools were selected.

### Where each agent embeds reasoning

| Agent | Phase | When |
|---|---|---|
| ComplianceAgent | `safety_review` | After running all 7 safety checks |
| PriceAssistAgent | `intent_routing` | At intent classification (data/knowledge/hybrid) |
| PriceAssistAgent | `synthesis` | At final answer composition |
| DataAgent | `tool_selection` | Per MCP tool call |
| RAGAgent | `tool_selection` | Per `search_documents` call |

### Block format

```xml
<llm_reasoning>
{
  "phase": "safety_review",
  "agent": "compliance",
  "checks": [
    {"category": "prompt_injection", "result": "pass"},
    {"category": "pii_exfiltration", "result": "pass"},
    ...
  ],
  "risk_signals": [],
  "authorization": "approved"
}
</llm_reasoning>
```

### Parsing

`extract_reasoning(text, agent_name)` → returns `(reasoning_entries: list[ReasoningEntry], clean_text: str)`

- Parses all `<llm_reasoning>` blocks from agent response text
- Returns the cleaned text (blocks stripped) + the parsed entries
- `strip_reasoning_markers(text)` is a final safety net applied before any user-visible output to ensure no raw markers leak through

### Flow to Frontend

```
Agent response text
    → extract_reasoning()
    → ReasoningEntry[] accumulated in ExecutionTracer
    → included in MeshResult.llm_reasoning
    → returned in API response
    → rendered in LLMReasoningPanel.tsx ("AI Reasoning" tab)
```

---

## 2. Execution Tracer

**File:** `src/tracing/execution_trace.py`

### What it is

Per-request event collector that records every significant pipeline event as an `ExecutionEvent`. Produces an `ExecutionSummary` at the end.

### ExecutionEvent fields

```python
@dataclass
class ExecutionEvent:
    stage: str          # "guardrail" | "rbac" | "compliance" | "domain" | "redaction"
    status: str         # "started" | "completed" | "blocked" | "failed"
    message: str        # human-readable description
    timestamp: float
    metadata: dict      # stage-specific details (latency, route, verdict, etc.)
```

### Listener pattern

`ExecutionTracer` maintains a list of registered listeners. When `record(event)` is called, all listeners are notified synchronously:

- **CLI renderer** (`src/tracing/cli_renderer.py`) — Rich-formatted terminal output
- **SSE queue** (registered by API server) — pushes `stage` events to the browser stream

This means the same event system drives both the terminal display and the browser's real-time pipeline panel.

### Route Inference

`infer_route_and_scores(query, answer)` — keyword matching on the query + answer text to infer the routing label displayed in the transparency panel:
- `"Data Layer Service"` — structured data keywords
- `"RAG Service"` — policy/regulatory keywords
- `"Hybrid"` — both

### ExecutionSummary

At the end of a request, `ExecutionTracer.finalize()` returns:
```python
@dataclass
class ExecutionSummary:
    events: list[ExecutionEvent]
    llm_reasoning: list[ReasoningEntry]
    route: str
    total_latency_ms: float
    stages_completed: list[str]
    blocked: bool
    block_reason: str | None
```

---

## 3. State Transition Tracing

**File:** `src/tracing/state_trace.py`

`log_state_handoff(before_state, after_state, request_id)` writes human-readable diffs of `MeshState` transitions to per-request state trace files:

```
data/logs/state/{request_id}.log
```

Records:
- Field-level before/after deltas (which fields changed, old vs. new value)
- A2A payload previews (first 200 chars of what was sent/received)

Useful for debugging unexpected state mutations or tracing exactly what each executor changed.

---

## 4. CLI Renderer

**File:** `src/tracing/cli_renderer.py`

A registered `ExecutionTracer` listener that renders pipeline stages to the terminal using **Rich**:

- Colored stage boxes (green=passed, red=blocked, yellow=in-progress)
- Route label with confidence indicators
- Timing per stage
- Expandable reasoning entries (when `--explain` flag is active)

Activated when running `python run.py` (always) or `python run.py --verbose` / `--explain` for more detail.

| Flag | Extra output |
|---|---|
| `--verbose` / `-v` | Confidence scores, routing detail, timing breakdown |
| `--explain` / `-e` | Alternative domain scores, rejection rationale |

---

## 5. Audit Trail (Related)

**File:** `src/middleware/audit_middleware.py`

`AuditMiddleware` intercepts every MAF agent invocation and appends a JSONL record to `data/audit_trail.jsonl`.

**Record fields:**
| Field | Description |
|---|---|
| `timestamp` | ISO timestamp |
| `request_id` | UUID per request |
| `trace_id` / `span_id` | OTel trace context |
| `session_id` | Conversation session |
| `user` / `role` | Identity |
| `agent_name` | Which agent was invoked |
| `inputs` | PII-scrubbed input |
| `output` | PII-scrubbed output |
| `status` | `success` / `error` |
| `latency_ms` | Agent call duration |
| `input_tokens` / `output_tokens` / `total_tokens` | Token counts (from MAF usage_details or estimated at ~4 chars/token) |

Queryable via `GET /api/audit` and `GET /api/audit/{request_id}`.
