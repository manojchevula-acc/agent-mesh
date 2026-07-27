# Rolling LLM Summarization — Multi-Turn Conversation Memory

## Problem

Current short-term memory is pure truncation:
- `CONVERSATION_MAX_TURNS=3` → last 6 messages (3 user + 3 assistant) sent to LLM
- Older turns are **silently dropped** — no context preservation
- Hard turn limit means long sessions lose critical early context

**Goal:** No turn limit. Summarize all prior turns into a rolling summary. Always send summary + current message to LLM every turn.

---

## Stack Context

| Component | Detail |
|-----------|--------|
| Agent Framework | Microsoft Agent Framework (A2A protocol) |
| LLM Client | `OpenAIChatCompletionClient` → Groq (OpenAI-compat endpoint) |
| Session Storage | JSONL per session in `data/conversations/{session_id}.jsonl` |
| History Injection | `[Conversation so far]` text block prepended to prompt in `workflow.py` |
| Turn Cap Code | `src/config.py:48` → `CONVERSATION_MAX_TURNS=3` |
| Load Site | `src/mesh/orchestrator.py:114` → `store.load(session_id, max_turns)` |
| Format Site | `src/mesh/workflow.py:655–661` → `format_history_block()` |

---

## Options Evaluated

### ❌ Option A — Semantic Kernel `ConversationSummaryMemory`
Semantic Kernel has a built-in plugin for rolling summarization. **Not viable** — project uses Agent Framework (A2A), not Semantic Kernel. Migration is out of scope.

### ✅ Option B — Rolling LLM Summarization (Recommended)
After each turn, call the LLM with a compact summarization prompt. Store the running summary as a special `type=summary` record in the existing JSONL file. Inject `[Conversation Summary]` + `[Current question]` instead of raw message list. Non-blocking (async task).

### ❌ Option C — Heuristic / Extractive Summarization
Trim history by keyword extraction without an LLM call. Simpler but lower quality. Misses implicit context and intent. Not worth it when LLM is already available and cheap for short summaries.

---

## Recommended Design: Option B

### Per-Turn Flow

```
Turn N:
  1. load_with_summary(session_id)  →  (summary_str, messages[])
  2. Build prompt:
       [Conversation Summary]
       <summary_str>

       [Current question]
       <user_msg>
  3. Call LLM (PriceAssistAgent via A2A)  →  answer
  4. Return answer to user
  5. asyncio.create_task(summarize_and_persist(session_id, user_msg, answer))
       ↳ calls Groq with summarization prompt
       ↳ saves new summary to JSONL (non-blocking, no latency impact)
```

### Summarization Prompt (compact)
> "You are a conversation summarizer. Given the running summary and the latest exchange, produce a concise updated summary (≤200 words) capturing all key facts, decisions, and user intent. Be factual, not conversational."

### Prompt Format Sent to LLM (every turn)
```
[Conversation Summary]
<running summary of all prior turns — ≤200 words>

[Current question]
<user message>
```

No raw message list for old turns. No turn cap.

---

## Files to Change

| File | Change Type | Detail |
|------|-------------|--------|
| `src/memory/summarizer.py` | **New** | LLM summarization client using same Groq endpoint |
| `src/memory/base.py` | Extend | Add abstract `load_summary` / `save_summary` methods |
| `src/memory/jsonl_backend.py` | Extend | Implement summary read/write on JSONL |
| `src/memory/conversation_store.py` | Extend | Add `load_with_summary()`, `save_summary()` facade methods |
| `src/mesh/workflow.py` | Modify | Swap `format_history_block` → `format_summary_block`; fire async summarization after response |
| `src/mesh/orchestrator.py` | Modify | Line 114: use `load_with_summary` instead of `load` |
| `src/config.py` | Modify | Deprecate `CONVERSATION_MAX_TURNS`; optionally add `SUMMARY_MODEL` |

---

## Verification Checklist

- [ ] Ask 5+ questions in one session — confirm no 3-turn cap, all context preserved
- [ ] Inspect `data/conversations/{session_id}.jsonl` — confirm `type=summary` records appear
- [ ] Kill + restart server mid-session — confirm summary persists and is used on next turn
- [ ] Confirm `strip_history_echo()` still works (new `[Conversation Summary]` header is distinct from old `[Conversation so far]`)
- [ ] Measure response latency — summarization must not block the response path
