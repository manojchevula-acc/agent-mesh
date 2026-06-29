# Conversational Memory — Guide Plan for AgentMesh (FAB Pricing Assistant)

## Context

You asked for a guide to understand all kinds of conversational state memory, evaluate what fits
your codebase, and decide which approach to implement. This is a **decision guide, not an
implementation plan** — you select the approach, then we implement.

Your codebase is a production-grade multi-agent mesh (Microsoft Agent Framework, A2A protocol,
MCP tools) for FAB banking. The current state: **completely stateless** — every query is
independent, no conversation history exists anywhere.

The one placeholder that was already stubbed out:
```python
# agent-mesh/src/config.py
CONVERSATION_STORE_DIR: str = os.getenv("CONVERSATION_STORE_DIR", "data/conversations")
```
This config var exists but is never used anywhere. It's your hook.

---

## Part 1 — Taxonomy: All Types of Conversational Memory

There are 5 distinct memory types used in production AI systems. They are not mutually exclusive —
most production systems layer 2–3 of them together.

---

### Type 1: In-Window Buffer Memory (Short-Term)
**"Remember the last N messages in this session"**

The simplest form. You keep a rolling list of prior Q&A turns and inject them into every
new request as plain context. The LLM sees everything at once.

```
System: You are FAB Pricing Assistant...
History:
  User: What is the pricing for deal CUST_001?
  Assistant: The recommended rate for CUST_001 is 3.25%...
  User: What about their RWA impact?
  Assistant: CUST_001 has RWA-weighted capital of...
Current: What fee waivers apply to this customer?
```

**Storage**: In-memory dict or flat JSONL files per session.  
**Where history lives**: Appended verbatim to each prompt.  
**Context window hit**: Linear growth — every new turn adds tokens.  
**Best for**: Sessions of 5–20 turns, same-session continuity.  
**Your existing hook**: `CONVERSATION_STORE_DIR = data/conversations/`.

---

### Type 2: Summary Memory (Compressed Context)
**"Summarize what we talked about earlier, keep recent turns verbatim"**

Addresses the context window growth problem of Type 1. You divide history into two zones:
- **Hot zone** (last 5 turns): Kept verbatim — agent sees exact words
- **Cold zone** (turns 6+): Compressed by an LLM into a running summary paragraph

Each time the hot zone overflows, the oldest turn is summarized and merged into the cold zone.

```
Summary so far: "User is analyzing CUST_001 pricing. Deal rated 3.25%. RWA impact confirmed
at 12.4%. User asked about fee waivers applicable to RM tier."

Recent turns:
  User: What about the compliance status on this deal?
  Assistant: Compliance verdict is PASSED...
Current: Can we extend the pricing lock?
```

**Storage**: Same JSONL per session + a `summary` field updated periodically.  
**Context window hit**: Constant (summary stays ~200 tokens regardless of history length).  
**Best for**: Long conversations (20+ turns), multi-step analysis workflows.  
**Extra cost**: One summarization LLM call when hot zone overflows.

---

### Type 3: Entity / Slot Memory (Structured Extraction)
**"Track what we know about the current customer/deal/product"**

Instead of storing raw messages, you extract structured facts from each turn and maintain a
"working context" object. Think of it as a scratchpad that gets filled in as the conversation
progresses.

```json
{
  "active_customer": "CUST_001",
  "active_deal_id": "DEAL_2024_089",
  "products_discussed": ["TERM_LOAN", "REVOLVING_CREDIT"],
  "last_pricing_check": "3.25% (as of turn 3)",
  "compliance_status": "PASSED",
  "open_questions": ["fee waivers", "pricing lock extension"]
}
```

This entity object is injected as a compact prefix instead of raw message history.

**Storage**: JSON file or dict per session, updated after each turn.  
**Context window hit**: Very small — only the entity JSON, not full messages.  
**Best for**: Domain-specific assistants where you can enumerate what entities matter
(banking: customer ID, deal ID, product type, pricing figures, compliance flags).  
**Extra cost**: One extraction LLM call (or regex) per turn to update the entity state.  
**Special banking value**: Lets agents say "the customer we're analyzing" without the user
re-stating CUST_001 every time.

---

### Type 4: Long-Term / Cross-Session Memory (Vector Retrieval)
**"Remember what we discussed last week about customer X"**

Conversation turns are embedded and stored in a vector database. When a new query arrives,
relevant past turns are retrieved semantically, not just chronologically.

```
New query: "What was the pricing floor we agreed on for CUST_001 last quarter?"

Retrieves from vector store:
  [Session 2024-03-12, turn 4]: "Pricing floor for CUST_001 set at 2.90% per credit policy..."
  [Session 2024-03-15, turn 2]: "Credit officer confirmed floor remains 2.90%..."
```

**Storage**: Dedicated Qdrant collection (your RAG infra already has Qdrant running!).  
**Where history lives**: Embedded conversation turns as vector documents.  
**Retrieval**: Semantic search over past turns relevant to current query.  
**Best for**: Returning users, account managers tracking deals over weeks/months.  
**Integration point**: Your existing RAG-as-a-Service already handles Qdrant — this would be a
new collection (`conversations`) alongside your document collection.

---

### Type 5: MAF-Native Thread Memory (Framework-Level)
**"Use MAF's own message thread model — the idiomatic approach"**

Microsoft Agent Framework has a built-in concept of **agent message threads** (also called
topics). Every message sent to/from an agent is part of a thread identified by a `topic_id`.
The `AgentRuntime` tracks these threads.

MAF's `OpenAIChatCompletionClient` accepts a `messages` list — the full conversation history
in proper OpenAI message format:
```python
[
  {"role": "user", "content": "What is pricing for CUST_001?"},
  {"role": "assistant", "content": "The recommended rate is 3.25%..."},
  {"role": "user", "content": "What about RWA impact?"}
]
```

If you pass this list consistently, MAF + the underlying LLM handle multi-turn naturally —
no special summarization or extraction needed (until context window fills).

**How your code currently works**: `ask_remote()` sends a single-turn message. There's no
`messages` list passed — only the current query.

**MAF-native solution**: Maintain a `messages` list per `session_id` and pass it through the
A2A call → PriceAssistAgent receives it → the LLM sees full history.

**Storage**: In-memory (process restart loses it) or externalized to JSONL/Redis/SQLite.  
**Best for**: Tight MAF integration, proper multi-turn agent behavior.  
**This is the approach MAF was designed for — it's the right foundation.**

---

## Part 2 — What Your Codebase Needs Changed (Regardless of Approach)

No matter which memory type you pick, these 4 layers need changes:

### Layer 1 — Storage Backend
A place to persist conversation turns between HTTP requests.
Options for your setup (ordered simplest→complex):
- **File-based JSONL** — `data/conversations/{session_id}.jsonl` — zero new infra, uses existing `CONVERSATION_STORE_DIR`
- **SQLite** — single file, queryable, no server needed
- **Redis/Valkey** — already in your stack (rag-as-a-service uses it for caching)
- **Qdrant** — only for Type 4 vector memory

### Layer 2 — Session Management
`session_id` needs to be:
- Stable across turns (same user, same conversation)
- Passed from frontend → api_server → orchestrator → agents
- Currently it's generated as `sess_{username}` per request — needs to become per-conversation, not per-request

Frontend fix: store `session_id` in localStorage, send it on every query.

### Layer 3 — API Contract
`api_server.py` `/api/query` needs:
- Accept `session_id` in request body (optional — create new if absent)
- Return `session_id` in response (so frontend can track it)
- Optionally: new `GET /api/conversations/{session_id}` endpoint for loading history

### Layer 4 — Agent Context Injection
The most important layer. History must reach the PriceAssistAgent's LLM call.

Two ways to do this in your architecture:
- **Simple (context prefix)**: Prepend history as a text block to the query string before it reaches the agent. Zero changes to MAF agent code.
- **MAF-native (messages list)**: Pass the full `messages` list through `ask_remote()` → A2A → agent. The agent's `OpenAIChatCompletionClient` sees proper chat history. Requires changes to `clients.py`, `a2a/hosting.py`, and agent factory.

---

## Part 3 — Recommended Approaches (With Honest Trade-Offs)

### Option A — Minimal: Session Buffer + Context Prefix
**Complexity: Low | MAF change: None | New infra: None**

Implementation:
1. `ConversationStore` class writes/reads `data/conversations/{session_id}.jsonl`
2. Before calling orchestrator, load last 8 turns from store
3. Format as: `[Context from this session]\nQ: ...\nA: ...\n\n[Current question]\n{query}`
4. After orchestrator returns, append new turn to store
5. Frontend: store `session_id` in localStorage

What you get:
- Single-session memory (user can say "that customer" referring to prior turn)
- Zero changes to agents, workflow, or MAF code
- Works immediately with the existing CONVERSATION_STORE_DIR

What you don't get:
- Proper MAF message threading
- Cross-session memory
- Entity extraction (still need to repeat CUST_001 after a new session)

**Best if**: You want something working in 1-2 days as a foundation.

---

### Option B — Standard: MAF Thread Memory + JSONL Persistence
**Complexity: Medium | MAF change: Yes (messages list through A2A) | New infra: None**

Implementation:
1. `ConversationStore` persists turns as MAF-format messages (role/content dicts) to JSONL
2. `orchestrator.handle_request()` accepts a `session_id`, loads history from store
3. History passed as `messages` list through `ask_remote()` → PriceAssistAgent
4. PriceAssistAgent's `OpenAIChatCompletionClient` call includes the messages list
5. After completion, new turn appended to store
6. Frontend: localStorage session_id, load history on page load via new API endpoint

What you get:
- Proper MAF-native multi-turn (LLM sees full chat history in correct format)
- Frontend can restore conversation after page refresh
- Natural pronoun resolution ("that deal", "the same customer") works in LLM
- Follows the MAF pattern you should be building for

What you don't get:
- Cross-session retrieval
- Context window safety for very long conversations (50+ turns)

**Best if**: You want the right MAF-aligned foundation that can grow.  
**This is the recommended baseline.**

---

### Option C — Production: MAF Thread + Summary + Entity Tracking
**Complexity: High | MAF change: Yes | New infra: None**

Adds on top of Option B:
1. **Summary compression**: When turn count > 15, an LLM call summarizes the cold zone
2. **Entity extraction**: After each turn, extract `{customer_id, deal_id, products}` and
   maintain a `session_entities.json` per session — injected as compact prefix
3. **History API**: `GET /api/conversations/{session_id}` returns full history for frontend

What you get:
- Unlimited conversation length (summary prevents context overflow)
- Entity memory ("that deal" → CUST_001 always resolved)
- Full frontend history restore (refresh doesn't lose context)
- Audit-grade conversation records

What you don't get:
- Cross-session retrieval (that's Type 4, separate feature)

**Best if**: You want a complete, demo-ready implementation.

---

### Option D — Advanced: Cross-Session Vector Memory
**Complexity: Very High | New infra: New Qdrant collection**

Adds on top of Option C:
1. Each turn is embedded (BGE-M3, your existing model) and stored in Qdrant `conversations` collection
2. On each new query, retrieve top-3 semantically relevant past turns from ANY session
3. Inject retrieved past turns as "Long-term memory" block

What you get:
- "Remember what we discussed last month" capability
- Cross-user memory (admin can recall any past analysis)

When to add: After Option B/C is stable and proven. Not the first thing to build.

---

## Part 4 — Where Each Approach Touches Your Code

| File | Option A | Option B | Option C |
|------|----------|----------|----------|
| `src/config.py` | use existing var | use existing var | add SUMMARY_THRESHOLD |
| `api_server.py` | session_id in/out | session_id + history endpoint | session_id + history + entities |
| `src/mesh/orchestrator.py` | load/save history | load/pass messages list | load/pass/summarize |
| `src/mesh/workflow.py` | MeshState gets history | MeshState gets messages | MeshState gets messages + entities |
| `src/a2a/clients.py` | no change | add messages param | add messages param |
| `src/a2a/hosting.py` | no change | extract messages from A2A | extract messages |
| `src/agents/price_assist_agent.py` | no change | no change | no change |
| `src/agents/agent_factory.py` | no change | no change | no change |
| NEW: `src/memory/conversation_store.py` | create | create | create + summarizer |
| NEW: `src/memory/entity_extractor.py` | no | no | create |
| `frontend/src/api/mesh.ts` | add session_id | add session_id + history fetch | same |
| `frontend/src/hooks/useChat.ts` | persist session_id | persist + restore history | same |

---

## Part 5 — MAF-Specific Technical Details

### How MAF handles multi-turn natively

MAF's `OpenAIChatCompletionClient.create()` accepts:
```python
messages: List[LLMMessage]  # SystemMessage | UserMessage | AssistantMessage
```

If you maintain this list across turns:
```python
messages = []
messages.append(SystemMessage(content=agent_instructions))
messages.append(UserMessage(content="What is pricing for CUST_001?", source="user"))
messages.append(AssistantMessage(content="Rate is 3.25%...", source="price_assist"))
messages.append(UserMessage(content="What about RWA?", source="user"))
# Pass to next create() call — LLM sees full history
```

MAF's `AssistantAgent` also supports `save_state()` / `load_state()` for agent state
persistence — this is the deeper MAF pattern for serializing agent memory across process restarts.

### Where to inject in your A2A flow

Current flow (stateless):
```
api_server → orchestrator.handle_request(user, query) →
  workflow → DomainExecutor → ask_remote(agent_url, query) →
    PriceAssistAgent sees only: query
```

With memory (Option B):
```
api_server → orchestrator.handle_request(user, query, session_id) →
  ConversationStore.load(session_id) → messages list →
  workflow → DomainExecutor → ask_remote(agent_url, query, messages) →
    PriceAssistAgent.create(messages=[...history..., current_query])
```

The key change: `ask_remote()` in `src/a2a/clients.py` currently sends a plain string.
It needs to send a structured payload with `{"query": ..., "messages": [...]}`.
The A2A hosting layer in `src/a2a/hosting.py` needs to unpack this on receipt.

### Session ID strategy recommendation

Current (wrong for memory): `session_id = f"sess_{username}"` — same ID forever per user.

Correct:
```python
# On first query (no session_id from frontend):
session_id = f"{username}_{uuid4().hex[:8]}"   # e.g., "alice_3f9a12b4"

# On subsequent queries: use the session_id returned in the first response
```

Frontend stores `session_id` per conversation in `localStorage`. A "New Chat" button
clears it, generating a new session on the next query.

---

## Part 6 — Decision Summary

| Criterion | Option A | Option B | Option C |
|-----------|----------|----------|----------|
| Time to implement | ~1 day | ~2-3 days | ~4-5 days |
| MAF-aligned | No | Yes | Yes |
| Survives page refresh | No (unless add API) | Yes | Yes |
| Long conversation safe | No (token growth) | No (token growth) | Yes (summary) |
| Entity tracking | No | No | Yes |
| Cross-session memory | No | No | No (Option D) |
| New infrastructure needed | None | None | None |
| Recommended starting point | Quick prototype | **Baseline** | Full demo |

---

## Recommended Path

**Start with Option B** — MAF Thread Memory + JSONL Persistence.

Reasons:
1. It's the correct MAF pattern (messages list, not string concatenation)
2. Uses only existing infrastructure (JSONL files, `CONVERSATION_STORE_DIR` already in config)
3. Frontend can restore conversation after page refresh (proper UX)
4. A clean foundation — Option C adds on top without refactoring
5. No new dependencies, no new services, fits the demo scope

Once Option B is working and you've used it in demos, add Option C's summary + entity
extraction as a second pass if conversations feel long or context feels lost.

---

## Open Questions Before Implementation

1. **Single-node or distributed?** Currently agents run as separate processes. Should
   `ConversationStore` be a shared file/Redis store (works across nodes) or per-process
   in-memory dict (simpler, breaks if you scale nodes)? For the demo setup: file-based JSONL
   is fine. For multi-node: Redis (already in your stack).

2. **Session ownership**: Should `alice` be able to see `bob`'s conversation history?
   Current RBAC would say no — each user's sessions are isolated. Is that right?

3. **Conversation lifespan**: Should old sessions auto-expire? How long should history persist?
   (e.g., 24 hours for demo, indefinite for production)

4. **Frontend "New Chat" button**: Currently the frontend has a "Clear" button that only
   clears local UI state. Should this also clear/archive the backend session, or just start
   a new `session_id`?
