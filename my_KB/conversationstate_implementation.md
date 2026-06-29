# Conversation State Memory — Implementation Guide

> **Feature:** Multi-turn conversational memory for AgentMesh (FAB Pricing Assistant)
> **Approach implemented:** **Option B — MAF Thread Memory + JSONL Persistence** (from `my_KB/memory.md`)
> **Status:** Implemented & verified · June 2026
> **Companion docs:** `memory.md` (decision guide), `SYSTEM_FLOW.md` (architecture), `architecture.md`

This document explains *what* was built, *why* it was built this way, *how* it flows end-to-end,
and *how to operate, extend, and test* the conversation memory layer. Use it as a learning and
guidance reference.

---

## 1. The Problem It Solves

Before this change, AgentMesh was **completely stateless**. Every query was independent — there was
no memory of what was asked before. A user could not say:

```
Turn 1: "Margin analysis for CUST003"
Turn 2: "What about its RWA impact?"     ← "its" had no meaning; the mesh forgot CUST003
```

The mesh had one stubbed-but-unused hook: `Config.CONVERSATION_STORE_DIR = "data/conversations"`.
This feature activates that hook and gives the assistant **single-conversation multi-turn memory**:
follow-up questions, pronoun resolution ("that deal", "the same customer"), and UI history that
survives a page refresh.

---

## 2. Why Option B (and the key technical reality)

`memory.md` laid out five memory types and four implementation options (A–D). **Option B** was chosen
because it is the correct MAF-aligned baseline using only existing infrastructure (JSONL files), and
it can grow into Option C/D later without refactoring.

### The A2A flattening constraint (important to understand)

Option B as originally described assumed we could pass a MAF **`messages` list** through the A2A
protocol so the PriceAssistAgent's LLM would see proper chat history. **Investigation of the A2A SDK
showed this is not cleanly possible:**

| Layer | Behavior | Consequence |
|-------|----------|-------------|
| **Client** `A2AAgent.run(messages)` | Sends only the **last** message over the wire (`normalized_messages[-1]`) | A list of messages collapses to one |
| **Server** `A2AExecutor` | Calls `context.get_user_input()`, which **joins all message parts into a single string** | Role separation (user/assistant) is lost |

A true messages-list would require **forking/subclassing the A2A executor** — fragile and tightly
coupled to SDK internals.

### The chosen solution: structured prompt block

Instead, history is:
1. **Stored** in MAF role/content JSONL format (future-proof — keeps the door open for Option C/D).
2. **Delivered** by injecting prior turns as a clearly-delimited text block *into the single prompt
   string* sent via `ask_remote`.

This delivers **every user-facing benefit of Option B** (multi-turn continuity, pronoun resolution,
page-refresh restore) while leaving the entire A2A layer, the collaboration tools, and the agents
**completely untouched**.

```
[Conversation so far]
User: Margin analysis for CUST003
Assistant: CUST003 margin is 2.1%...

[Current question]
What about its RWA impact?
```

> **Takeaway:** When a framework boundary won't carry the structure you want, encode the structure
> inside the payload it *does* carry. Storage format and transport format can differ.

---

## 3. Architecture & Data Flow

```
Frontend (localStorage: "agent-mesh-session-id")
   │  POST /api/query { username, query, session_id? }
   ▼
api_server.py  post_query()
   │  handle_request(user, query, session_id)
   ▼
orchestrator.py  handle_request()
   ├─ session_id = session_id or f"{username}_{uuid8}"      # generate if absent
   ├─ ConversationStore.load(session_id, MAX_TURNS)  ──►  MeshState.conversation_history
   ▼
workflow.py  (guardrail → rbac → compliance → DOMAIN → redact)
   └─ DomainExecutor:
        history_block = ConversationStore.format_history_block(state.conversation_history)
        base_prompt   = history_block + state.query
        answer        = ask_remote("price_assist", base_prompt)   # A2A UNCHANGED
   ▼
orchestrator.py  (after workflow)
   ├─ if not blocked: ConversationStore.append_turn(session_id, query, answer)
   └─ return MeshResult(..., session_id)
   ▼
api_server.py  →  JSON { answer, ..., session_id }
   ▼
Frontend persists session_id; on reload GET /api/conversations/{session_id} restores turns
```

**Crucial scoping decision:** Only the **domain** stage (PriceAssistAgent) receives history.
Guardrail / RBAC / Compliance evaluate the **current query in isolation** — this is correct, because
a safety gate must judge each query on its own merits, not be softened by prior context.

---

## 4. Components Built

### 4.1 The `src/memory/` package (pluggable storage)

The store is deliberately built behind a backend abstraction so the storage mechanism can change
(e.g. file → Redis) by flipping **one config var**, with **zero changes** to the orchestrator or
workflow.

```
src/memory/
├── __init__.py            # public exports: ConversationStore, get_conversation_store, ConversationBackend
├── base.py                # ConversationBackend (ABC) — the interface every backend implements
├── jsonl_backend.py       # JsonlBackend — ACTIVE default (file-based)
├── redis_backend.py       # RedisBackend — PLACEHOLDER STUB for future use
└── conversation_store.py  # ConversationStore facade + get_conversation_store() factory
```

#### `base.py` — the interface
```python
class ConversationBackend(abc.ABC):
    @abc.abstractmethod
    def load_messages(self, session_id) -> list[dict]: ...   # full history, chronological
    @abc.abstractmethod
    def append(self, session_id, role, content) -> None: ...  # add one message
    def clear(self, session_id) -> None: ...                  # optional (default no-op)
```

#### `jsonl_backend.py` — active default
- Writes `data/conversations/{session_id}.jsonl`, **one JSON object per line, one line per message**:
  ```json
  {"role": "user", "content": "...", "ts": "2026-06-29T..."}
  {"role": "assistant", "content": "...", "ts": "2026-06-29T..."}
  ```
- **Path safety:** `session_id` is sanitized to `[A-Za-z0-9_-]` before use as a filename (prevents
  path traversal / invalid names).
- **CWD-independent:** relative `CONVERSATION_STORE_DIR` is resolved against the agent-mesh root, so
  it writes to the same place no matter which process (api_server, launch_mesh) calls it.
- **Resilient:** missing file → `[]`; malformed/corrupt lines are skipped, not raised.
- Stdlib only (`json`, `pathlib`, `datetime`) — no new dependencies.

#### `redis_backend.py` — placeholder stub (future use)
- Implements the same interface but every method raises a **clear `NotImplementedError`** until wired,
  so selecting it never silently loses history.
- Documents the intended design: store each session as a Redis list (`conv:{session_id}` via
  `RPUSH`/`LRANGE`), with a guarded lazy `import redis` and `# TODO` markers.
- **To adopt Redis later:** implement the TODOs, `pip install redis`, set `CONVERSATION_BACKEND=redis`.
  No orchestrator/workflow changes needed — that's the whole point of the abstraction.

#### `conversation_store.py` — the facade the app uses
```python
class ConversationStore:
    def load(self, session_id, max_turns) -> list[dict]      # tail-capped to 2*max_turns messages
    def load_messages(self, session_id) -> list[dict]         # full history (API restore endpoint)
    def append_turn(self, session_id, user_query, assistant_answer)   # append user + assistant
    def clear(self, session_id)
    @staticmethod
    def format_history_block(messages) -> str                 # render the [Conversation so far] block
```
The backend is selected by `get_conversation_store()` / the constructor from
`Config.CONVERSATION_BACKEND` (`"jsonl"` default, `"redis"` future).

### 4.2 Configuration (`src/config.py`)
```python
# Existing hook, now used:
CONVERSATION_STORE_DIR = os.getenv("CONVERSATION_STORE_DIR", "data/conversations")

# New:
ENABLE_CONVERSATION_MEMORY = os.getenv("ENABLE_CONVERSATION_MEMORY", "true")  # master on/off
CONVERSATION_MAX_TURNS     = int(os.getenv("CONVERSATION_MAX_TURNS", "8"))    # turns replayed into prompt
CONVERSATION_BACKEND       = os.getenv("CONVERSATION_BACKEND", "jsonl")       # "jsonl" | "redis"
CONVERSATION_REDIS_URL     = os.getenv("CONVERSATION_REDIS_URL", "redis://127.0.0.1:6379/0")  # future
```

### 4.3 Workflow (`src/mesh/workflow.py`)
- `MeshState` gains `conversation_history: List[dict] = field(default_factory=list)`.
- `DomainExecutor.run()` builds `base_prompt = format_history_block(history) + state.query` and uses
  it for the initial `ask_remote("price_assist", ...)` **and** for all three existing retry branches
  (`tool_call_echo`, `meta_response`, `hallucination`) so retries keep context.
- The `domain.query_length` span attribute still reflects the **bare user question** (not the
  augmented prompt); a new `domain.history_turns` attribute records how many prior turns were injected.

### 4.4 Orchestrator (`src/mesh/orchestrator.py`)
- Signature: `handle_request(user, query, session_id: str | None = None)`.
- Session id: `session_id or f"{user.username}_{uuid.uuid4().hex[:8]}"` (per-conversation, not
  per-user — replaces the old `sess_{username}`).
- Loads history into `MeshState` **before** the workflow; saves the turn **after** (only when
  `not blocked` and there's a real answer).
- All memory I/O is wrapped in `try/except` that logs a warning — **memory never breaks a request**.
- `MeshResult` gains `session_id: str = ""`, returned to the caller.

### 4.5 REST API (`api_server.py`)
- `POST /api/query` now accepts optional `session_id` in the body and returns it in the response.
- **New:** `GET /api/conversations/{session_id}` → `{ "session_id", "messages": [...] }` for UI restore.

### 4.6 Frontend (`frontend/src/`)
- `types/mesh.ts` — `QueryRequest.session_id?`, `MeshResult.session_id?`, new `ConversationMessage` /
  `ConversationHistory` types.
- `api/mesh.ts` — `queryMesh(username, query, sessionId?)` sends `session_id`; new
  `getConversation(sessionId)` calls the restore endpoint.
- `hooks/useChat.ts`:
  - Holds `session_id` in a ref, seeded from `localStorage["agent-mesh-session-id"]`.
  - On mount: if a stored id exists, fetches history and hydrates the transcript (refresh-safe).
  - On each response: pins/persists the returned `session_id`.
  - `clearChat()` ("New Chat"/Clear): wipes the transcript **and** the stored id → the next query
    starts a fresh conversation.

### 4.7 Files NOT changed (by design)
`src/a2a/clients.py`, `src/a2a/hosting.py`, `src/tools/collaboration_tools.py`,
`src/agents/price_assist_agent.py`, `src/agents/agent_factory.py` — the structured-prompt-block
approach needs no A2A or agent changes.

---

## 5. Session Lifecycle

| Event | What happens |
|-------|--------------|
| First query (no `session_id`) | Backend generates `{username}_{uuid8}`, returns it; frontend stores it in localStorage |
| Follow-up query | Frontend sends the stored `session_id`; orchestrator loads its history |
| Page refresh | Frontend reads `session_id` from localStorage, calls `GET /api/conversations/{id}`, restores transcript |
| "New Chat" / Clear | Frontend clears transcript + localStorage id → next query creates a new session |
| Blocked query (guardrail/RBAC/compliance) | **Not** persisted — carries no useful context |

**User isolation:** because `session_id` is prefixed with the username, each user's conversations are
naturally separated. (A stricter ownership check on the restore endpoint can be added later.)

---

## 6. Configuration Reference

| Env var | Default | Effect |
|---------|---------|--------|
| `ENABLE_CONVERSATION_MEMORY` | `true` | Master switch. `false` → fully stateless (no load/save). |
| `CONVERSATION_MAX_TURNS` | `8` | How many prior Q/A turns are replayed into the prompt (token-bounding). |
| `CONVERSATION_BACKEND` | `jsonl` | `jsonl` (active) or `redis` (placeholder — raises until implemented). |
| `CONVERSATION_STORE_DIR` | `data/conversations` | Directory for JSONL files. |
| `CONVERSATION_REDIS_URL` | `redis://127.0.0.1:6379/0` | Used only by the future Redis backend. |

---

## 7. How to Test / Verify

### 7.1 Unit — the store (no servers needed)
```python
from src.memory import ConversationStore
s = ConversationStore()
sid = "test_alice_demo"
s.append_turn(sid, "Margin analysis for CUST003", "CUST003 margin is 2.1%")
s.append_turn(sid, "What about its RWA?", "CUST003 RWA is 12.4%")
print(s.load(sid, 8))                          # last 2 turns as role/content dicts
print(ConversationStore.format_history_block(s.load(sid, 1)))
s.clear(sid)
```
Confirm `data/conversations/test_alice_demo.jsonl` is created with one line per message.

### 7.2 End-to-end multi-turn (mocked A2A — fast, no LLM)
Patch `orchestrator.ask_remote`, call `handle_request` twice with the same `session_id`, and assert
the second call's `price_assist` prompt contains `"[Conversation so far]"` and the prior user
message. (This is the exact check used during implementation — it passed.)

### 7.3 Backend abstraction
- `CONVERSATION_BACKEND=jsonl` (default) → works end-to-end.
- `CONVERSATION_BACKEND=redis` → selecting the store raises the placeholder `NotImplementedError`
  (proving the swap point exists; orchestrator catches it and degrades to stateless rather than 500ing).

### 7.4 Full stack (live)
```
# Terminals: DataLayer MCP (9100), RAG MCP (9000), launch_mesh.py, api_server.py, frontend
POST /api/query {username:"alice", query:"Margin analysis for CUST003"}        # note session_id
POST /api/query {username:"alice", query:"What about its RWA impact?", session_id:"<that id>"}
GET  /api/conversations/<that id>                                              # both turns returned
```
In the UI: ask two related questions → follow-up resolves context; refresh → transcript restored;
"New Chat" → fresh session.

### 7.5 Toggle / regression
- `ENABLE_CONVERSATION_MEMORY=false` → behavior reverts to stateless.
- Offline suite: `python -m unittest test_agent_mesh.py`. *Note:* `test_rbac_blocks_invalid_role`
  fails **pre-existingly** (the test passes a raw string role → `user.role.value` AttributeError) —
  unrelated to this feature.

---

## 8. Limitations & Future Growth (Option C / D)

| Limitation | Mitigation / next step |
|------------|------------------------|
| **Token growth** on very long conversations | `CONVERSATION_MAX_TURNS` caps replay; add **summary memory** (Option C) for unlimited length |
| **No entity tracking** | Add an entity extractor (`{customer_id, deal_id, products}`) injected as a compact prefix (Option C) |
| **No cross-session recall** | Embed turns into a Qdrant `conversations` collection for semantic recall (Option D) |
| **Single-node JSONL** | Implement the Redis backend stub for multi-node deployments (config flip) |

Because storage is MAF role/content JSONL behind a clean backend interface, and history delivery is
isolated to the DomainExecutor, all of the above are additive — they build on this foundation without
reworking it.

---

## 9. File Reference

| File | Change |
|------|--------|
| `src/memory/__init__.py` | **NEW** — package exports + factory |
| `src/memory/base.py` | **NEW** — `ConversationBackend` ABC |
| `src/memory/jsonl_backend.py` | **NEW** — active file-based backend |
| `src/memory/redis_backend.py` | **NEW** — placeholder stub (future) |
| `src/memory/conversation_store.py` | **NEW** — facade + history formatter |
| `src/config.py` | New memory config vars (uses existing `CONVERSATION_STORE_DIR`) |
| `src/mesh/workflow.py` | `MeshState.conversation_history`; DomainExecutor injects history block |
| `src/mesh/orchestrator.py` | `session_id` param + generation; load/save turns; `MeshResult.session_id` |
| `api_server.py` | accept/return `session_id`; new `GET /api/conversations/{session_id}` |
| `frontend/src/types/mesh.ts` | session_id + conversation types |
| `frontend/src/api/mesh.ts` | session_id in `queryMesh`; `getConversation()` |
| `frontend/src/hooks/useChat.ts` | session_id lifecycle, restore-on-mount, clear |

---

*Implementation of Option B from `my_KB/memory.md`. Last updated June 2026.*
