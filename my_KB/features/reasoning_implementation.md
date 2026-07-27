# LLM Reasoning Explainability — Implementation Guide

> **Feature:** AI decision transparency — capture and display the LLM's actual reasoning for every decision point in the mesh
> **Approach:** MAF-first prompt-engineering with `<llm_reasoning>` markers, parsed at the orchestrator layer
> **Status:** Implemented · July 2026
> **Companion docs:** `SYSTEM_FLOW.md` (architecture), `EXECUTION.md` (running the mesh), `conversationstate_implementation.md` (memory layer)

This document explains *what* was built, *why* it was built this way (including the architectural constraints that shaped the design), *how* it flows end-to-end, and *how to extend, operate, and test* the reasoning layer. Use it as a learning and study reference.

---

## 1. The Problem It Solves

The client asked: **"How can I explain why the LLM made this decision, gave this output, or selected this path?"**

Before this feature, the **Execution Trace** panel in the UI showed:

- ✅ *What happened* — pipeline steps (guardrail passed, rbac passed, compliance passed, routed to data layer)
- ✅ *Post-hoc rationale bullets* — e.g. "Keywords detected: price, margin, customer."
- ❌ *Why the LLM actually decided this* — the model's internal reasoning was never captured

The existing rationale bullets in `infer_route_and_scores()` (`src/tracing/execution_trace.py`) were **keyword heuristics computed after the fact** — not the LLM's own logic. If the model chose the hybrid path because "the query asks for both CUST001's current margin AND the policy floor for BB-rated loans", that reasoning was invisible.

The client specifically wanted:
- **Why did the compliance LLM pass/fail this request?** (not just "COMPLIANCE_PASSED")
- **Why did Price Assist route to data vs RAG vs hybrid?** (what signals triggered that decision?)
- **How was the final answer assembled?** (which sources were used, what was found)
- **Which MCP tool did the Data Agent select and why?** (customer_360 vs pricing_recommendation vs margin_analysis)
- **What search query did the RAG Agent use?** (what knowledge domain was being accessed)

---

## 2. Design Constraints

Before choosing an approach, there are important architectural constraints to understand.

### 2.1 The A2A Process Boundary

The mesh runs as **five separate OS processes** communicating over HTTP (A2A protocol):

```
api_server.py        (port 8000)  — orchestrator, holds the ExecutionTracer
compliance_agent     (port 8015)  — ComplianceAgent LLM
data_agent           (port 8016)  — DataAgent LLM + DataLayer MCP
rag_agent            (port 8017)  — RAGAgent LLM + RAG MCP
price_assist_agent   (port 8018)  — PriceAssistAgent LLM (primary coordinator)
```

The `ExecutionTracer` (which accumulates trace data for the UI) lives in the **api_server process** as a Python `contextvars.ContextVar`. It is **not accessible** from the other four processes. Any approach that requires calling `get_active_tracer()` from inside PriceAssist or DataAgent will fail silently — the ContextVar has no value in those processes.

### 2.2 The A2A Text Transport Constraint

All A2A communication is text-in, text-out. The function `ask_remote(node, prompt)` sends a string and gets a string back. There is no structured metadata envelope, no side-channel for reasoning data. **Whatever the remote agent wants to communicate back, it must embed in the response text itself.**

This is the same constraint that shaped the conversation memory design (see `conversationstate_implementation.md` §2).

### 2.3 The Tool Result Flow for Data/RAG

PriceAssist calls Data/RAG agents **not directly**, but via MAF tool functions:

```
PriceAssist LLM → calls tool query_structured_data(question)
                        ↓
               collaboration_tools.py (runs inside price_assist process)
                        ↓ ask_remote("data_agent", question)
               DataAgent (port 8016) → returns response text
                        ↓
               tool result text fed back to PriceAssist LLM
                        ↓
               PriceAssist LLM incorporates into synthesis → final answer
                        ↓ ask_remote("price_assist", prompt) returns this text
               Orchestrator (api_server process) — this is where we can parse
```

DataAgent's reasoning can only reach the orchestrator if it **travels through PriceAssist's final answer** as text. PriceAssist is instructed to copy tool results verbatim, so DataAgent's text (including reasoning markers) flows through.

---

## 3. The Chosen Approach — Prompt-Engineered Reasoning Markers

### 3.1 Why not MAF EventLogger / InferenceResultEvent?

MAF's EventLogger can subscribe to `InferenceResultEvent` (fires after each LLM call with full prompt + response). This is the "purest" MAF-native approach — but it only works **within the same process**. Since each agent is a separate OS process, a single EventLogger cannot span all five of them. You would need to install a per-process logger in each agent server and then transport reasoning data back through A2A anyway — achieving the same result with more complexity.

### 3.2 Why not Qwen3 Thinking Mode?

Qwen3 models (used by Data/RAG agents) support `enable_thinking=True` in the API call, which produces `<think>...</think>` blocks. This is a good future enhancement, but has two complications:
1. The MAF framework may process or strip thinking blocks before returning the final response text
2. Thinking mode only works for Qwen3 models — Compliance uses `gpt-oss-20b` and PriceAssist uses `gpt-oss-120b`, which have no native thinking mode
3. Thinking blocks reflect the model's internal monologue (verbose, stream-of-consciousness) — structured JSON reasoning blocks are cleaner for a business UI

### 3.3 The Chosen Design: `<llm_reasoning>` Prompt Markers

Each agent is prompted to **embed a structured JSON block** using an XML-style tag at specific decision points. The tag is:

```
<llm_reasoning>{JSON object}</llm_reasoning>
```

Key properties of this design:
- **Works across all models** — pure prompt engineering, no API parameter changes
- **Works across process boundaries** — text travels through A2A naturally
- **Framework-agnostic** — doesn't touch MAF internals; the framework can change without breaking this
- **Fault-tolerant** — if the LLM skips or malforms the block, the parser handles it gracefully (empty list returned, no crash)
- **Self-cleaning** — markers are stripped from the displayed answer so users never see raw JSON

---

## 4. Files Changed / Created

| File | Change | Purpose |
|------|--------|---------|
| `src/tracing/llm_reasoning.py` | **NEW** | Parser + data model for reasoning blocks |
| `src/tracing/execution_trace.py` | Modified | Added `llm_reasoning` to `ExecutionSummary` + `add_llm_reasoning()` to tracer |
| `src/agents/price_assist_agent.py` | Modified | Added REASONING TRANSPARENCY prompt section |
| `src/agents/compliance_agent.py` | Modified | Added REASONING TRANSPARENCY prompt section |
| `src/agents/data_agent.py` | Modified | Added REASONING TRANSPARENCY prompt section |
| `src/agents/rag_agent.py` | Modified | Added REASONING TRANSPARENCY prompt section |
| `src/mesh/workflow.py` | Modified | `ComplianceExecutor` + `DomainExecutor` call `extract_reasoning()` |
| `api_server.py` | Modified | Added `llm_reasoning` field to `/api/query` response JSON |
| `frontend/src/types/mesh.ts` | Modified | Added `LLMReasoningEntry` + `LLMReasoningData` interfaces |
| `frontend/src/components/chat/LLMReasoningPanel.tsx` | **NEW** | Per-agent reasoning cards UI component |
| `frontend/src/components/chat/ExecutionPanel.tsx` | Modified | Added "Execution Steps \| AI Reasoning" tab switcher |

---

## 5. Backend Architecture

### 5.1 The Data Model (`src/tracing/llm_reasoning.py`)

```python
@dataclass
class LLMReasoningEntry:
    agent: str            # "compliance" | "price_assist" | "data" | "rag"
    phase: str            # "safety_review" | "intent_routing" | "synthesis" | "tool_selection"
    data: Dict[str, Any]  # parsed JSON from the <llm_reasoning> block
    timestamp: str        # ISO 8601 UTC
```

The `extract_reasoning()` function:

```python
def extract_reasoning(text: str, agent: str) -> Tuple[List[LLMReasoningEntry], str]:
    """
    Parses all <llm_reasoning>...</llm_reasoning> blocks from text.
    Returns (entries, clean_text_with_markers_stripped).
    """
```

**Agent self-identification**: Data/RAG agents embed `"agent":"data"` or `"agent":"rag"` inside their JSON. The extractor checks `data.get("agent", agent)` — so when DataAgent's block appears in PriceAssist's answer text, it is attributed to "data", not "price_assist".

```python
effective_agent = data.get("agent", agent) if isinstance(data, dict) else agent
entries.append(LLMReasoningEntry(agent=effective_agent, phase=phase, data=data))
```

### 5.2 ExecutionSummary Extension (`src/tracing/execution_trace.py`)

```python
@dataclass
class ExecutionSummary:
    # ... existing fields ...
    llm_reasoning: List[Dict[str, Any]] = field(default_factory=list)  # ← NEW
```

```python
class ExecutionTracer:
    def __init__(self, ...):
        # ...
        self._llm_reasoning: List[Dict[str, Any]] = []  # ← NEW

    def add_llm_reasoning(self, entries: List[dict]) -> None:
        """Store LLM reasoning entries captured from agent response text."""
        self._llm_reasoning.extend(entries)

    def summary(self) -> ExecutionSummary:
        return ExecutionSummary(
            # ... existing fields ...
            llm_reasoning=list(self._llm_reasoning),  # ← included in output
        )
```

### 5.3 Where Extraction Happens (`src/mesh/workflow.py`)

There are exactly **two extraction points** — both in the orchestrator process where the tracer is accessible:

#### Compliance (ComplianceExecutor)

```python
verdict = await self._ask("compliance", f"Review this request for safety: '{state.query}'")

# Extract reasoning BEFORE using the verdict text for the pass/fail check.
_reasoning_entries, verdict = extract_reasoning(verdict, "compliance")
state.compliance_verdict = verdict          # clean verdict (no markers)
if tracer and _reasoning_entries:
    tracer.add_llm_reasoning([e.to_dict() for e in _reasoning_entries])

# The pass/fail check still works — "COMPLIANCE_PASSED: reason" is preserved.
if "compliance_failed" in verdict.lower():
    ...
```

#### Domain / PriceAssist (DomainExecutor)

```python
answer = ConversationStore.strip_history_echo(answer or "", state.query)

# Extract after history stripping but before setting state.answer.
# Only extract on non-failed hops (failed hops return error strings, not agent output).
if not failed:
    _reasoning_entries, answer = extract_reasoning(answer, "price_assist")
    if tracer and _reasoning_entries:
        tracer.add_llm_reasoning([e.to_dict() for e in _reasoning_entries])

state.answer = answer  # clean answer with all markers stripped
```

**Why after `strip_history_echo`?** The history-echo stripper removes the conversation prefix that may have been echoed back by the LLM. Reasoning markers can appear anywhere in the response, so we extract after the text is cleaned of the history echo.

**Why only `if not failed`?** When a hop fails (A2A connection error, timeout), `answer` is set to an internal error string like `"The banking assistant is currently unavailable (...)."` — there is no LLM output to parse, so extraction would produce nothing anyway. The guard makes intent explicit.

### 5.4 API Response (`api_server.py`)

```python
return JSONResponse({
    "answer":           result.answer,
    # ... other fields ...
    "events":           [dataclasses.asdict(e) for e in summary.events],
    "llm_reasoning":    summary.llm_reasoning,   # ← NEW: list of dicts
})
```

---

## 6. Agent Prompt Engineering

Each agent has a **REASONING TRANSPARENCY** section appended to its system instructions. These are the exact formats agents are asked to produce.

### 6.1 Compliance Agent (`src/agents/compliance_agent.py`)

**When:** After the verdict line, on the next line.

**Format:**
```
COMPLIANCE_PASSED: <short reason>
<llm_reasoning>{"phase":"safety_review","checks":["prompt_injection","pii_leakage","destructive_action"],"risk_signals":[],"decision":"PASSED","rationale":"Standard pricing analysis query with no harmful intent"}</llm_reasoning>
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `phase` | `"safety_review"` | Fixed — identifies this as a compliance reasoning block |
| `checks` | `string[]` | The three checks always performed |
| `risk_signals` | `string[]` | Any suspicious patterns found; `[]` if none |
| `decision` | `"PASSED"` \| `"FAILED"` | Must match the verdict token |
| `rationale` | `string` | Specific reason — names the concern or confirms it's routine |

**Key note:** The reasoning block is on a NEW LINE after the verdict. The existing compliance parsing in `workflow.py` checks `"compliance_failed" in verdict.lower()` on the clean verdict string (after markers are stripped), so nothing breaks.

### 6.2 Price Assist Agent (`src/agents/price_assist_agent.py`)

**Two blocks per response:**

**Block 1 — Intent Routing** (before first tool call):
```
<llm_reasoning>{"phase":"intent_routing","intent":"hybrid","data_signals":["CUST001","margin","pricing"],"rag_signals":["policy","compliance","floor"],"rationale":"Query requires both customer pricing figures AND the applicable policy floor to assess compliance","confidence":0.94}</llm_reasoning>
```

**Block 2 — Synthesis** (after all tool results, before final answer):
```
<llm_reasoning>{"phase":"synthesis","sources_used":["query_structured_data","query_knowledge_base"],"key_findings":["CUST001 current margin is 2.1%, below 2.5% minimum","BB-rated AED loan floor per credit policy §4.2 is 2.5%"],"answer_rationale":"Combining customer data and policy to render a non-compliant verdict with specific gap"}</llm_reasoning>
```

**Intent Routing fields:**
| Field | Type | Description |
|-------|------|-------------|
| `phase` | `"intent_routing"` | Fixed |
| `intent` | `"data"` \| `"knowledge"` \| `"hybrid"` | The routing decision |
| `data_signals` | `string[]` | Words/phrases from the query that indicate structured data need |
| `rag_signals` | `string[]` | Words/phrases that indicate policy/document need |
| `rationale` | `string` | One sentence explaining why this path |
| `confidence` | `float` | 0.0–1.0, routing certainty |

**Synthesis fields:**
| Field | Type | Description |
|-------|------|-------------|
| `phase` | `"synthesis"` | Fixed |
| `sources_used` | `string[]` | Tool names actually called |
| `key_findings` | `string[]` | 2–4 brief findings from the retrieved data |
| `answer_rationale` | `string` | How the final answer was constructed |

### 6.3 Data Agent (`src/agents/data_agent.py`)

**When:** Before calling any MCP tool.

**Format:**
```
<llm_reasoning>{"agent":"data","phase":"tool_selection","tool_selected":"pricing_recommendation","customer_id":"CUST001","query_intent":"current recommended price and compliance status","rationale":"pricing_recommendation provides per-deal recommended price, approved price, margins, and compliance flags"}</llm_reasoning>
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `agent` | `"data"` | **Fixed** — used for attribution across A2A boundary |
| `phase` | `"tool_selection"` | Fixed |
| `tool_selected` | `string` | Exact MCP tool name: `customer_360`, `pricing_recommendation`, `profitability_summary`, `margin_analysis`, or `rwa_impact` |
| `customer_id` | `string` | Extracted customer ID, e.g. `"CUST001"`, or `""` if none |
| `query_intent` | `string` | Brief phrase: what the question is asking for |
| `rationale` | `string` | Why this tool for this query |

### 6.4 RAG Agent (`src/agents/rag_agent.py`)

**When:** Before calling `search_documents`.

**Format:**
```
<llm_reasoning>{"agent":"rag","phase":"tool_selection","tool_selected":"search_documents","search_query":"pricing floor BB-rated AED corporate loan","knowledge_domain":"credit_policy","rationale":"User needs the minimum pricing floor for BB-rated AED loans from FAB credit policy"}</llm_reasoning>
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `agent` | `"rag"` | **Fixed** — used for attribution across A2A boundary |
| `phase` | `"tool_selection"` | Fixed |
| `tool_selected` | `"search_documents"` | Always this tool |
| `search_query` | `string` | The exact query string sent to `search_documents` |
| `knowledge_domain` | `string` | Short label: `credit_policy`, `fee_schedule`, `kyc_rules`, `product_guidelines`, etc. |
| `rationale` | `string` | Why this search query for this question |

---

## 7. The Cross-Process Attribution Trick

This is the most important implementation detail to understand. Without it, all reasoning blocks would be attributed to "price_assist" regardless of which agent produced them.

### The problem

`DomainExecutor` calls `extract_reasoning(answer, "price_assist")`. The second parameter is the fallback agent label. If DataAgent's block appears in PriceAssist's answer text, it would be labelled "price_assist" unless we do something special.

### The solution

Data/RAG agents include `"agent":"data"` or `"agent":"rag"` **inside their JSON block**. The extractor checks:

```python
effective_agent = data.get("agent", agent) if isinstance(data, dict) else agent
```

This means:
- PriceAssist's own blocks (no `"agent"` key) → attributed to "price_assist" (the parameter)
- DataAgent's blocks (have `"agent":"data"`) → attributed to "data" (from the JSON)
- RAGAgent's blocks (have `"agent":"rag"`) → attributed to "rag" (from the JSON)

**This works because** PriceAssist is instructed to "Copy every field, figure, row, and passage the tool returned verbatim into your reply." DataAgent's and RAGAgent's complete response texts (including their `<llm_reasoning>` blocks) are treated as tool result data and included in PriceAssist's final answer. The orchestrator then extracts all blocks in one pass.

### Visual flow for a hybrid query

```
1. Orchestrator sends query to PriceAssistAgent (port 8018)

2. PriceAssist LLM emits:
   <llm_reasoning>{"phase":"intent_routing","intent":"hybrid",...}</llm_reasoning>
   [calls query_structured_data("CUST001 pricing")]

3. DataAgent (port 8016) processes, emits:
   <llm_reasoning>{"agent":"data","phase":"tool_selection","tool_selected":"pricing_recommendation",...}</llm_reasoning>
   [actual data results...]
   → this entire text is the tool result returned to PriceAssist

4. PriceAssist LLM receives tool result (with DataAgent's reasoning block inside)
   [calls query_knowledge_base("pricing floor BB-rated AED loan")]

5. RAGAgent (port 8017) processes, emits:
   <llm_reasoning>{"agent":"rag","phase":"tool_selection","tool_selected":"search_documents",...}</llm_reasoning>
   [actual policy passages...]
   → this entire text is the tool result returned to PriceAssist

6. PriceAssist LLM synthesizes final answer:
   <llm_reasoning>{"phase":"synthesis","sources_used":[...],...}</llm_reasoning>
   [Final answer text including the verbatim tool result blocks from steps 3 and 5]

7. Orchestrator receives PriceAssist's full answer text, which now contains:
   - 1 intent_routing block (from PriceAssist)
   - 1 data tool_selection block (from DataAgent, passed through)
   - 1 rag tool_selection block (from RAGAgent, passed through)
   - 1 synthesis block (from PriceAssist)

8. extract_reasoning(answer, "price_assist") parses all 4 blocks:
   - intent_routing → attributed to "price_assist" (no "agent" key)
   - tool_selection with "agent":"data" → attributed to "data"
   - tool_selection with "agent":"rag" → attributed to "rag"
   - synthesis → attributed to "price_assist" (no "agent" key)

9. Clean answer (no markers) set as state.answer
   4 reasoning entries stored in tracer._llm_reasoning
```

---

## 8. Frontend Architecture

### 8.1 TypeScript Types (`frontend/src/types/mesh.ts`)

```typescript
export interface LLMReasoningData {
  // intent_routing phase
  intent?: "data" | "knowledge" | "hybrid";
  data_signals?: string[];
  rag_signals?: string[];
  rationale?: string;
  confidence?: number;
  // synthesis phase
  sources_used?: string[];
  key_findings?: string[];
  answer_rationale?: string;
  // safety_review phase
  checks?: string[];
  risk_signals?: string[];
  decision?: string;
  // tool_selection phase (data / rag agents)
  tool_selected?: string;
  customer_id?: string;
  query_intent?: string;
  search_query?: string;
  knowledge_domain?: string;
  // fallback
  raw?: string;
  [key: string]: unknown;
}

export interface LLMReasoningEntry {
  agent: string;   // "compliance" | "price_assist" | "data" | "rag"
  phase: string;   // "safety_review" | "intent_routing" | "synthesis" | "tool_selection"
  data: LLMReasoningData;
  timestamp?: string;
}
```

Added to `MeshResult`:
```typescript
export interface MeshResult {
  // ... existing fields ...
  llm_reasoning?: LLMReasoningEntry[];  // ← NEW
}
```

### 8.2 LLMReasoningPanel Component (`frontend/src/components/chat/LLMReasoningPanel.tsx`)

A component that renders a list of `LLMReasoningEntry` objects as collapsible cards. Each card:

```
┌─────────────────────────────────────────────────────┐
│ [1]  [Price Assist Agent]  ⑂ Intent Routing   0.94  ↓│
├─────────────────────────────────────────────────────┤
│ Routed to   [ HYBRID ]                              │
│ "Query requires both customer data AND policy floor"│
│                                                     │
│ Data signals detected                               │
│ [CUST001] [margin] [pricing]                        │
│                                                     │
│ Policy / RAG signals detected                       │
│ [policy] [compliance] [floor]                       │
│                                                     │
│ Routing confidence  ████████████░░  94%             │
└─────────────────────────────────────────────────────┘
```

**Colour coding by agent:**
| Agent | Border / Badge colour |
|-------|----------------------|
| Compliance | Amber / orange |
| Price Assist | Brand blue |
| Data Agent | Teal |
| RAG Agent | Violet |

**Phase-specific rendering:**

| Phase | Key fields displayed |
|-------|---------------------|
| `safety_review` | Checks performed list, risk signals, PASSED/FAILED badge, rationale quote |
| `intent_routing` | Routing badge (data/knowledge/hybrid), data signals pills, RAG signals pills, confidence bar, rationale quote |
| `synthesis` | Sources used badges, key findings bullets, answer rationale quote |
| `tool_selection` | Tool name badge, customer ID, search query (monospace), knowledge domain, query intent |

### 8.3 ExecutionPanel Tab Switcher (`frontend/src/components/chat/ExecutionPanel.tsx`)

The `ExecutionPanel` now renders a pill-style tab bar with two tabs:

```
┌──────────────────┐  ┌─────────────────┐
│ ⚡ Execution Steps  4│  │ 🧠 AI Reasoning  5│
└──────────────────┘  └─────────────────┘
```

The badge number shows step/entry count. Switching tabs is instant (React state). The `LLMReasoningPanel` is only mounted when the "AI Reasoning" tab is active.

```typescript
type PanelTab = "trace" | "reasoning";

// In the component:
const [activeTab, setActiveTab] = useState<PanelTab>("trace");
const reasoningCount = result.llm_reasoning?.length ?? 0;

// Tab render:
{activeTab === "trace"     && <ExecutionStepsList />}
{activeTab === "reasoning" && <LLMReasoningPanel entries={result.llm_reasoning ?? []} />}
```

---

## 9. End-to-End Data Flow

```
User sends query
        ↓
api_server.py:  ExecutionTracer created, set as active_tracer ContextVar
        ↓
orchestrator.handle_request() → workflow runs

  ── ComplianceExecutor ──────────────────────────────────────────
  ask_remote("compliance", "Review: '...'")
      → ComplianceAgent (port 8015) responds:
        "COMPLIANCE_PASSED: routine banking query\n
         <llm_reasoning>{"phase":"safety_review","decision":"PASSED",...}</llm_reasoning>"
  
  extract_reasoning(verdict, "compliance")
      → entries = [LLMReasoningEntry(agent="compliance", phase="safety_review", ...)]
      → clean_verdict = "COMPLIANCE_PASSED: routine banking query"
  tracer.add_llm_reasoning(...)
  state.compliance_verdict = clean_verdict
  ────────────────────────────────────────────────────────────────

  ── DomainExecutor ──────────────────────────────────────────────
  ask_remote("price_assist", history_block + query)
      → PriceAssistAgent (port 8018) runs; internally calls:
        → ask_remote("data_agent", "CUST001 pricing") via collaboration tool
            → DataAgent (port 8016) responds (with data + reasoning marker)
        → ask_remote("rag_agent", "pricing floor query") via collaboration tool
            → RAGAgent (port 8017) responds (with policy + reasoning marker)
      → PriceAssist synthesizes final answer (includes tool results verbatim):
        "<llm_reasoning>{"phase":"intent_routing",...}</llm_reasoning>
         <llm_reasoning>{"agent":"data","phase":"tool_selection",...}</llm_reasoning>
         [data results...]
         <llm_reasoning>{"agent":"rag","phase":"tool_selection",...}</llm_reasoning>
         [policy passages...]
         <llm_reasoning>{"phase":"synthesis",...}</llm_reasoning>
         [final answer text]"
  
  strip_history_echo(answer, query)
  extract_reasoning(answer, "price_assist")
      → 4 entries: intent_routing(price_assist), tool_selection(data),
                   tool_selection(rag), synthesis(price_assist)
      → clean_answer = answer text without any <llm_reasoning> tags
  tracer.add_llm_reasoning(...)
  state.answer = clean_answer
  ────────────────────────────────────────────────────────────────

summary = tracer.summary()
  → summary.llm_reasoning = [compliance, intent_routing, data, rag, synthesis]

JSONResponse({
  "answer": ...,
  "events": [...],          ← execution trace steps
  "llm_reasoning": [...]    ← NEW: 5 reasoning entries
})

Frontend:
  ExecutionPanel receives result.llm_reasoning
  "AI Reasoning" tab badge shows 5
  LLMReasoningPanel renders 5 cards
```

---

## 10. What Reasoning the Client Sees (Example)

For the query: **"Is CUST001's loan price compliant with the pricing policy?"**

The AI Reasoning tab shows 5 cards:

**Card 1 — Compliance Agent: Safety Review**
```
Checks performed: prompt_injection ✓  pii_leakage ✓  destructive_action ✓
Risk signals: (none)
Decision: PASSED
"Standard pricing compliance analysis — routine banking query, no harmful intent"
```

**Card 2 — Price Assist Agent: Intent Routing**
```
Routed to: HYBRID
Data signals: [CUST001] [loan] [price]
RAG signals: [policy] [compliant] [pricing]
"Query requires CUST001's actual pricing figures AND the applicable policy floor to assess compliance"
Routing confidence: ████████████░░ 92%
```

**Card 3 — Data Agent: Tool Selection**
```
Tool: pricing_recommendation
Customer: CUST001
Intent: current price and compliance flag
"pricing_recommendation provides per-deal recommended price, policy floor, and compliance status flags"
```

**Card 4 — RAG Agent: Tool Selection**
```
Tool: search_documents
Search query: "pricing floor BB-rated AED corporate loan minimum rate"
Domain: credit_policy
"User needs the regulatory minimum price for BB-rated AED loans to compare against CUST001's deal price"
```

**Card 5 — Price Assist Agent: Answer Synthesis**
```
Sources used: [query_structured_data] [query_knowledge_base]
Key findings:
  › CUST001 current deal price is 2.1%, policy floor for BB-rated AED loans is 2.5%
  › Pricing_recommendation table shows compliance_flag = NON_COMPLIANT for deal D004
  › Credit policy §4.2 confirms minimum floor of 2.5% for the applicable segment
"Combined customer pricing data with regulatory policy to produce a compliance verdict with specific gap identified"
```

---

## 11. Fault Tolerance

The feature is designed to be fully backward-compatible and fault-tolerant.

**If an LLM skips the reasoning block:**
- `_REASONING_RE.finditer(text)` finds zero matches
- `extract_reasoning()` returns `([], original_text)` — empty list, text unchanged
- `tracer.add_llm_reasoning([])` is a no-op
- The answer displays normally; the AI Reasoning tab shows "No LLM reasoning captured for this request."

**If an LLM produces malformed JSON:**
```python
try:
    data: Dict[str, Any] = json.loads(raw)
except (json.JSONDecodeError, ValueError):
    data = {"raw": raw}   # capture the raw text as-is
```
The card still renders in the "fallback" section showing the raw text.

**If the reasoning block spans an unusual format:**
The regex `r"<llm_reasoning>(.*?)</llm_reasoning>"` with `re.DOTALL` handles multi-line content. It will also find blocks that PriceAssist embeds mid-paragraph.

**Retry logic safety:** The `DomainExecutor` retry patterns (`_TOOL_CALL_RE`, `_META_RESPONSE_RE`, `_HALLUCINATION_RE`) do NOT match `<llm_reasoning>` tags, so reasoning blocks never accidentally trigger a retry.

---

## 12. How to Extend This Feature

### Adding reasoning to a new agent

1. Add a REASONING TRANSPARENCY section to the agent's instructions (in its `_INSTRUCTIONS` string).
2. Decide the `phase` name (e.g. `"policy_check"`, `"query_planning"`).
3. Include `"agent":"<name>"` in the JSON if the agent's response travels through another agent's text before reaching the orchestrator.
4. Call `extract_reasoning(response, "agent_name")` at the point where the orchestrator receives that agent's response.
5. Add a rendering section in `LLMReasoningPanel.tsx` for the new phase (or let it fall through to the "fallback" section which displays `data.rationale` and `data.raw`).

### Adding a new rendering section in the UI

In `LLMReasoningPanel.tsx`, add a new `{entry.phase === "your_phase" && (...)}` block inside the `ReasoningCard` body, following the same pattern as the existing `intent_routing`, `synthesis`, `safety_review`, and `tool_selection` sections.

### Changing the marker tag

If you want to use a different tag (e.g. `<reasoning>` instead of `<llm_reasoning>`), update the `_REASONING_RE` regex in `src/tracing/llm_reasoning.py` and the prompt text in all four agent instruction strings.

---

## 13. Testing the Feature

### Manual test — check backend extraction

Run the mesh and make a hybrid query. Check `data/trace_log.jsonl` (or add a debug print) to verify `llm_reasoning` entries are populated:

```python
# Quick test in Python REPL:
from src.tracing.llm_reasoning import extract_reasoning

test_text = """
COMPLIANCE_PASSED: routine banking query
<llm_reasoning>{"phase":"safety_review","checks":["prompt_injection","pii_leakage","destructive_action"],"risk_signals":[],"decision":"PASSED","rationale":"Standard pricing query"}</llm_reasoning>
"""
entries, clean = extract_reasoning(test_text, "compliance")
print(entries)   # [LLMReasoningEntry(agent='compliance', phase='safety_review', ...)]
print(clean)     # "COMPLIANCE_PASSED: routine banking query"
assert "llm_reasoning" not in clean
assert entries[0].agent == "compliance"
assert entries[0].data["decision"] == "PASSED"
```

### Manual test — check agent self-identification

```python
# DataAgent block should override the "price_assist" parameter
data_block = '<llm_reasoning>{"agent":"data","phase":"tool_selection","tool_selected":"pricing_recommendation","customer_id":"CUST001","rationale":"test"}</llm_reasoning>'
entries, _ = extract_reasoning(data_block, "price_assist")
assert entries[0].agent == "data"          # self-identified, not "price_assist"
assert entries[0].phase == "tool_selection"
assert entries[0].data["customer_id"] == "CUST001"
```

### End-to-end test

1. Start the mesh (`python launch_mesh.py`)
2. Start the API server (`python api_server.py`)
3. Open the frontend (`npm run dev` in `frontend/`)
4. Log in as `credit_officer`
5. Send: `"Is CUST001's loan price compliant with our pricing policy?"`
6. When the response arrives, open **Execution Trace** → click **AI Reasoning** tab
7. Expect 5 reasoning cards; confirm:
   - Card 1 shows "safety_review" with PASSED + rationale
   - Card 2 shows "intent_routing" with `intent: "hybrid"` and both signal types
   - Card 3 shows "tool_selection" for "data" agent with tool name and customer ID
   - Card 4 shows "tool_selection" for "rag" agent with search query and domain
   - Card 5 shows "synthesis" with sources and key findings
8. Verify the main answer text contains NO `<llm_reasoning>` tags

### Blocked request test

1. Send a request blocked by guardrails (e.g. `"DROP TABLE customers"`)
2. The AI Reasoning tab should show "No LLM reasoning captured" (no LLM calls run for blocked requests — guardrail is deterministic regex)

### Compliance bypass test

1. Log in as `relationship_manager` (elevated role — bypasses compliance LLM check)
2. Send any query
3. The AI Reasoning tab should show only 2–4 cards (no Compliance Safety Review — that step is skipped for elevated roles)

---

## 14. Known Limitations and Future Work

### Current limitations

1. **LLM compliance**: The feature depends on LLMs following the reasoning prompt instructions. Models may occasionally skip blocks (especially under high token load) or produce slightly malformed JSON. The fault tolerance handles this gracefully but the reasoning will be absent for those requests.

2. **Reasoning blocks in tool results**: DataAgent and RAGAgent blocks travel through PriceAssist as tool result text. If PriceAssist's LLM decides to summarize or paraphrase tool results (violating the "verbatim" instruction), the reasoning blocks may be lost. In practice this is rare because the verbatim instruction is strongly enforced.

3. **Order of reasoning cards**: The extraction order in PriceAssist's answer follows the order in which PriceAssist embedded blocks. For hybrid queries this is typically: intent_routing → data_tool_selection → data results → rag_tool_selection → rag results → synthesis. But the LLM is not guaranteed to follow this exact order.

### Recommended future enhancements

1. **Qwen3 Thinking Mode**: For Data/RAG agents (both use `qwen3.6-27b`), add `extra_create_args={"extra_body": {"enable_thinking": True}}` to `agent_factory.py` and parse `<think>...</think>` blocks alongside `<llm_reasoning>` blocks. This would give deeper reasoning from the model's native chain-of-thought capability.

2. **Streaming reasoning to UI**: The current implementation collects reasoning only at the end of the request. For long requests, reasoning could be streamed in real-time using WebSockets or SSE, showing cards appearing as the pipeline runs.

3. **Reasoning persistence**: Currently reasoning is ephemeral — it's returned in the API response but not stored. Adding reasoning to `data/audit_trail.jsonl` would allow post-hoc analysis of AI decisions over time.

4. **Confidence calibration**: The `confidence` field in intent_routing is whatever the LLM says. For production use, calibrate against actual routing outcomes to determine if LLM self-reported confidence correlates with accuracy.

5. **Multi-language reasoning**: For Arabic banking users, the reasoning blocks are currently English only. Adding a locale-aware rendering layer would improve usability.
