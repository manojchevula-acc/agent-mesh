# Plan: Solving Token Growth in Conversation Memory

## Context

The current conversation memory layer (Option B — MAF Thread Memory + JSONL) injects prior turns verbatim into every prompt sent to PriceAssistAgent. Measured at 8 turns, this costs **~1,750–2,200 tokens per request**. Token cost grows **linearly with turns** and, beyond `CONVERSATION_MAX_TURNS=8`, the system silently drops the oldest context — the very information that may be most useful for resolving entities like "that deal" or "the same customer."

Infrastructure confirmed by exploration:
- LLM: Groq via `OpenAIChatCompletionClient` (agent_framework.openai), models available: `gpt-oss-120b` (Price Assist), `gpt-oss-20b` (Compliance). Direct API call possible without going through A2A.
- No tokenizer utilities in codebase; `tokenizers>=0.22.2` is installed but unused.
- Qdrant is external (RAG-as-a-Service), not directly accessible from main mesh process.
- History is injected verbatim by `DomainExecutor` in `workflow.py` via `ConversationStore.format_history_block()`.
- Current cap: `messages[-(2*max_turns):]` in `conversation_store.py` line 49.

---

## Solution Options

### Option 1 — Token-budget sliding window (quick fix, no LLM cost)
Replace the turn-count cap with a character/token budget (`CONVERSATION_MAX_TOKENS`). Pack as many recent turns as possible within the budget; oldest turns are still dropped but the boundary is token-precise rather than turn-count arbitrary.

- **Pro:** Zero extra cost; ~30-line change to `load()`.
- **Con:** Same fundamental problem — old context is lost; linear growth is slowed but not solved.

### Option 2 — Rolling LLM summarization (recommended)
Keep a **rolling summary** of older turns alongside a **verbatim recent window**. When total history exceeds a threshold, the oldest K turns are summarized via a direct Groq call and stored as a special `role: "summary"` record in the JSONL file. On every subsequent load: inject `[summary block] + [last N verbatim turns]`.

- **Injected tokens:** bounded to ~200–300 (summary) + ~500 (last 3–4 turns verbatim) ≈ constant.
- **Pro:** Preserves semantic context across unlimited turns; no external dependencies; fits the existing JSONL architecture cleanly.
- **Con:** One extra Groq API call when the summary threshold is crossed (~once per 6 turns, not per request). Adds ~300–700 ms on that turn only.

### Option 3 — Entity/state extraction (domain-specific)
For each turn, extract structured entities (customer IDs, deal IDs, margin/RWA figures, decisions) into a compact state dictionary. Inject only the state dict + bare user question.

- **Pro:** Minimal tokens (~100–200); highly legible to LLM.
- **Con:** Requires domain-specific extraction logic or LLM call; fragile if entities don't map cleanly.

### Option 4 — Semantic retrieval via vector search
Embed every turn and store in Qdrant; at query time, retrieve top-K most semantically relevant turns.

- **Pro:** Scales to unlimited history with constant injection cost.
- **Con:** Qdrant is currently external (RAG-as-a-Service, not directly accessible); requires embedding pipeline; significant complexity.

### Option 5 — Hybrid: summary + token budget + recent window
Combine Options 1 + 2: rolling LLM summary for old turns, token-budget cap for recent verbatim window. This is the production-grade approach and the natural next step after Option 2 is in place.

---

## Recommended Approach: Option 2 — Rolling LLM Summarization

Fits the existing architecture cleanly, solves the token growth problem completely, and uses only available Groq infrastructure.

### How it works end-to-end

```
JSONL file (session):
  {"role": "summary", "content": "CUST003: margin 2.1%, RWA 12.4%...", "ts": "..."}  ← rolling summary
  {"role": "user",    "content": "What about fee waivers?",              "ts": "..."}  ← verbatim recent
  {"role": "assistant","content": "...",                                  "ts": "..."}
  {"role": "user",    "content": "Compare to CUST007",                   "ts": "..."}
  {"role": "assistant","content": "...",                                  "ts": "..."}

Injected prompt:
  [Prior context summary]
  CUST003: margin 2.1%, RWA 12.4%...

  [Recent conversation]
  User: What about fee waivers?
  Assistant: ...
  User: Compare to CUST007
  Assistant: ...

  [Current question]
  <new query>
```

### New config vars (`src/config.py`)
```python
CONVERSATION_SUMMARY_TRIGGER = int(os.getenv("CONVERSATION_SUMMARY_TRIGGER", "6"))
# When total stored turns exceed this, oldest turns are collapsed into a rolling summary.

CONVERSATION_RECENT_WINDOW   = int(os.getenv("CONVERSATION_RECENT_WINDOW", "4"))
# Turns kept verbatim after summarization (the "hot" recent context).
```

### New file: `src/memory/summarizer.py`
Calls Groq directly via `OpenAIChatCompletionClient` (same pattern as `agent_factory.py`):

```python
async def summarize_turns(turns: list[dict]) -> str:
    """Summarize a list of {role, content} messages into a compact context block."""
    # Uses Config.PRICE_ASSIST_API_KEY + "https://api.groq.com/openai/v1"
    # Prompt instructs LLM to preserve: customer IDs, deal IDs, numeric figures, key decisions
    # Target: 150-250 tokens output
```

Reuse `OpenAIChatCompletionClient` from `agent_framework.openai` exactly as `agent_factory.py` does (lines 29–33).

### Modified files

| File | Change |
|------|--------|
| `src/config.py` | Add `CONVERSATION_SUMMARY_TRIGGER`, `CONVERSATION_RECENT_WINDOW` |
| `src/memory/summarizer.py` | **NEW** — `summarize_turns(turns)` → Groq call |
| `src/memory/jsonl_backend.py` | `load_messages()` returns `role: "summary"` records naturally (already works); `set_summary(session_id, text)` rewrites the leading summary record |
| `src/memory/conversation_store.py` | `load()` — if stored turns > SUMMARY_TRIGGER: summarize oldest (total - RECENT_WINDOW) turns, update leading summary in JSONL, return summary record + recent turns; `format_history_block()` — recognize `role: "summary"` and prefix with `[Prior context summary]` header |
| `src/mesh/orchestrator.py` | `handle_request()` — `load()` is already `async`-compatible; no signature change needed |

### Summary update strategy

Summarization happens **inside `load()`** (lazy, on-demand), not on every `append_turn()`. This means:
- Turn N+1 append: normal JSONL append (fast, no LLM call).
- Turn N+1 load: if stored turns > threshold, summarize, rewrite JSONL, return compressed result.
- The rewrite collapses old turns → one summary record in the file, reducing future I/O too.

### `format_history_block()` output (after change)

```python
if messages[0]["role"] == "summary":
    # Render summary separately from verbatim turns
    block = "[Prior context summary]\n{summary_content}\n\n[Recent conversation]\n{verbatim_turns}\n\n[Current question]\n"
else:
    # Existing format (no summary yet — early conversation)
    block = "[Conversation so far]\n{all_turns}\n\n[Current question]\n"
```

---

## Token budget comparison

| State | Tokens injected |
|-------|----------------|
| Current (8-turn verbatim cap) | ~1,750–2,200 tokens, oldest dropped |
| After Option 1 (token budget) | same ceiling, smarter boundary |
| After Option 2 (rolling summary) | ~250 (summary) + ~500 (4 recent turns) = **~750 tokens, bounded forever** |

---

## Verification

1. **Unit — summarizer**: Call `summarize_turns()` with 6 mock turns, verify output is < 300 tokens (char heuristic), contains entity names from input.
2. **Unit — load with summarization**: Create a JSONL with 7 turns; call `ConversationStore.load(session_id, max_turns=4, summary_trigger=6)`; assert result has 1 summary record + 4 verbatim turns; assert original JSONL is rewritten with summary record at top.
3. **End-to-end**: Run two conversations of 10 turns each; inspect injected `base_prompt` in `DomainExecutor` — confirm it stays bounded regardless of turn count; confirm PriceAssistAgent still resolves pronouns correctly on turn 10.
4. **Toggle**: `CONVERSATION_SUMMARY_TRIGGER=999` → behaves as current (no summarization); `CONVERSATION_RECENT_WINDOW=0` → full context replaced by summary only.
5. **Observability**: `domain.history_turns` span attr (already wired) will now plateau at `CONVERSATION_RECENT_WINDOW`; `conversation.memory.load` span duration will spike on the summarization turn — confirms the trigger fired.
