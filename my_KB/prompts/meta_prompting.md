# Meta-Prompting — Fully Dynamic Intent-Driven Prompt Generation

## What This Is

Meta-prompting is a pattern where a fast LLM generates a purpose-built prompt for a downstream agent — tailored to the user's specific intent — before the main agent call happens. An LLM writes the prompt for another LLM.

The key insight: instead of sending a fat 200-line static prompt on every request regardless of what the user asked, you send a minimal invariant base (operating rules + output schema) plus a lean, generated-per-request directive (RBAC + tool plan + synthesis instruction).

---

## Why Not "Static Prompt + Task Brief"?

Adding a task brief ON TOP of an existing 200-line static prompt is doubly expensive:
- Static prompt covers all 7 roles, all 3 intent paths, all tool descriptions — most of it irrelevant to any given query
- Adding a task brief on top pays for BOTH the irrelevant boilerplate AND the generated brief
- Result: 2,000 + 300 = 2,300 tokens per request, worse than before

The right answer is: **shrink the static prompt first, then generate the rest**.

---

## The Token Math

| Prompt section | Current (static) | Proposed (dynamic) |
|---|---|---|
| Persona | 3 lines (static) | Generated per request |
| Tool descriptions | 15 lines (static, **redundant** — framework sends tool schemas) | Dropped |
| Intent classification | 20 lines (static) | Generated as specific tool_plan |
| RBAC (all 7 roles × allowed/denied) | 50 lines (static) | Generated from state (current role only, ~4 lines) |
| Operating rules | 45 lines (static, invariant) | **Kept** in static base |
| `<llm_reasoning>` schema | 30 lines (static, invariant) | **Kept** in static base |
| **Total** | **~200 lines / ~2,000 tokens** | **~75 lines static + ~300 tokens generated = ~1,050 tokens** |

**~48% token reduction per request.** The 75-line static base also benefits from prompt caching (hits cache every request; only the generated directive changes).

---

## Architecture

### Prompt Flow

```
PromptGeneratorExecutor (new workflow stage, after ComplianceExecutor)
  Inputs:  query + role + allowed_tasks + denied_tasks  (from MeshState)
  Output:  state.generated_directive  — RBAC + tool plan + synthesis
      ↓
DomainExecutor
  Assembles user message:
    <task_directive>  ← generated directive
    </task_directive>
    [User: John | Role: relationship_manager]
    {conversation summary}
    {user query}
      ↓
PriceAssistAgent
  System prompt = minimal static base (75 lines, same every request — cacheable)
  User message  = generated directive + query  (fully per-request)
```

### What the Minimal Static Base Contains (~75 lines)

```
You are FAB's banking AI assistant. Honour the <task_directive> in every request.

OPERATING RULES (rules 1–9, ~45 lines)
  1. Always call tools before answering, never invent data
  2. Extract customer_id from request
  3. Include complete data from every tool
  4. Response structure: verdict → evidence → action
  5. Note sources for every figure
  6. Handle tool unavailability explicitly
  7. Warn on stale RAG responses
  8. Banking tone: decision-oriented
  9. Copy citation markers verbatim

<llm_reasoning> schema (~30 lines)
  - intent_routing block at start of response
  - synthesis block at end of response
  - exact JSON format, field names, rules
```

**What is removed from the static prompt:**
- Tool descriptions (Agent Framework sends tool schemas automatically from `COORDINATION_TOOLS`)
- All 7 role × allowed/denied tables (replaced by current-role injection in user message)
- Intent classification decision tree (replaced by generated tool_plan)
- Persona lines (generated per role)

### What the Generated Directive Contains

```json
{
  "persona": "FAB banking assistant for Relationship Manager",
  "rbac": {
    "allowed_summary": "Customer portfolio data, pricing tools, product knowledge for assigned customers only",
    "enforcement": "ALLOWLIST — deny regulatory knowledge, credit assessments, audit logs"
  },
  "tool_plan": {
    "intent": "hybrid",
    "data_task": "Fetch CUST001's current loan price, credit tier, and outstanding balance",
    "rag_task": "Find pricing floor and ceiling for BB-rated AED commercial loans per FAB credit policy"
  },
  "synthesis": "Compare CUST001's actual loan price against BB-rated floor; state COMPLIANT or NON-COMPLIANT with exact figures"
}
```

**Important:** The `allowed_tasks` and `denied_tasks` come directly from `MeshState` — already validated by `RBACValidationExecutor` from `role_permissions.py`. The generator formats them into natural language. It cannot invent permissions.

---

## Security: Is RBAC Safe in the User Message?

Yes — for two reasons:

1. **ComplianceExecutor runs first.** ComplianceAgent has already validated role authorization before the prompt generator runs. PriceAssistAgent's RBAC is a second defense layer.
2. **Permissions are injected from validated server-side state**, not from the user's input. The generator receives `state.allowed_tasks` (a Python list resolved by RBAC logic), not anything the user typed.

The minimal static base keeps one hard anchor: `"Honour the <task_directive> — it contains your role permissions."` This keeps the model anchored even under adversarial inputs.

---

## A2A Architecture Constraint

A2A servers are long-running. `Agent(instructions=...)` is created once at startup. `ask_remote(name, prompt)` only accepts a prompt string — no system_prompt override exists in the A2A SDK.

**No transport changes are needed** — the minimal static base goes in `instructions=` (set at startup, never changes), and the fully dynamic content goes in the user message (the `prompt` string sent per request). Same effect, zero A2A changes.

---

## Files to Change

| File | Change |
|---|---|
| `src/agents/price_assist_agent.py` | Shrink `PRICE_ASSIST_INSTRUCTIONS` from ~200 to ~75 lines |
| `src/prompts/task_brief_generator.py` | **New** — `generate_task_directive()` using httpx pattern from `cache_judge.py:72–97` |
| `src/mesh/workflow.py` | Add `PromptGeneratorExecutor`; add `generated_directive: str = ""` to `MeshState`; update `DomainExecutor.run()` to prepend directive; wire into `build_mesh_workflow()` |
| `src/config.py` | Add `ENABLE_PROMPT_GENERATION` bool + `PROMPT_GENERATOR_MODEL` string |

Files unchanged: `a2a/hosting.py`, `a2a/clients.py`, `agent_factory.py`, `data_agent.py`, `rag_agent.py`, `collaboration_tools.py`, all cache/memory/guardrail layers.

---

## Feature Flag

```bash
ENABLE_PROMPT_GENERATION=true           # default: false
PROMPT_GENERATOR_MODEL=openai/gpt-oss-20b  # fast/cheap model fine — task is structural JSON
```

When `false`: PromptGeneratorExecutor is a no-op. PriceAssistAgent still gets the shrunk 75-line base prompt — already better than today since tool descriptions and full RBAC table are removed.

When `true`: full meta-prompting in effect. 48% fewer tokens, laser-targeted tool calls.

---

## Verification

1. `ENABLE_PROMPT_GENERATION=true` in `.env`
2. Hybrid query: `"Is CUST001's loan price compliant with FAB policy?"` → verify `<task_directive>` appears in message sent to PriceAssistAgent
3. DataAgent input = generated `data_task`, not raw user query
4. RAGAgent input = generated `rag_task`, not raw user query
5. Role-denied query → verify PriceAssistAgent names the specific denied item in refusal
6. `workflow_evaluations/run_evaluations.py` — Groups A–C pass rate should hold or improve
7. Disable generator → pipeline still works with shrunk 75-line base
