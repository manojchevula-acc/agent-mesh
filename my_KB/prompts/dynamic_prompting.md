# Dynamic Prompt Loading — Research & Options

## Current State: Static Prompts

All four core agents plus 5+ auxiliary LLM callers define their prompts as **hardcoded Python string constants** baked directly into source files. There are no external prompt files anywhere in the runtime path.

| Agent | File | Constant | Size |
|---|---|---|---|
| PriceAssistAgent | `src/agents/price_assist_agent.py` | `PRICE_ASSIST_INSTRUCTIONS` | ~200 lines |
| ComplianceAgent | `src/agents/compliance_agent.py` | `COMPLIANCE_INSTRUCTIONS` | ~170 lines |
| DataAgent | `src/agents/data_agent.py` | `DATA_INSTRUCTIONS` | ~135 lines |
| RAGAgent | `src/agents/rag_agent.py` | `RAG_INSTRUCTIONS` | ~134 lines |
| Summarizer | `src/memory/summarizer.py` | `_SYSTEM_PROMPT` | 4 lines |
| Cache Judge | `src/cache/cache_judge.py` | `_JUDGE_PROMPT` | ~20 lines |
| Entity Extractor | `src/cache/entity_extractor.py` | `_EXTRACT_PROMPT`, `_BATCH_PROMPT` | ~20 lines each |

### How Prompts Flow Today

```
Agent server startup
    ↓
*_agent.py: INSTRUCTIONS = """...hardcoded text..."""
    ↓
create_demo_agent(instructions=INSTRUCTIONS)  ← agent_factory.py
    ↓
Agent(client=..., instructions=instructions)  ← Microsoft Agent Framework
    ↓
LLM call: { role: "system", content: instructions }  (fixed for server lifetime)
```

**Per-request context** (user role, allowed/denied tasks, conversation history) IS already dynamic — assembled via inline f-strings in `workflow.py`. Only the *system* prompts are static.

**Key constraint:** Each agent is a long-running A2A HTTP server. `instructions=` is set **once at startup**. A prompt change today requires a server restart.

---

## Problems With the Current Approach

1. **Prompt editing = code change + server restart** — tweaking a compliance rule means touching Python source and redeploying all four A2A servers.
2. **No prompt versioning beyond git** — rollback = git revert; no artifact-level rollback.
3. **No A/B testing** — impossible to run two prompt variants in parallel without forking code.
4. **Role context only in user message, not system prompt** — `allowed_tasks`/`denied_tasks` are injected into the user message in `workflow.py`, not the system prompt. The LLM reconciles this mid-conversation instead of having it up front.
5. **Prompts scattered, no central registry** — 4 agent files + 5 utility files, no single place to audit all prompts.

---

## Options

### Option 1 — External File Loading *(Simplest, Recommended First Step)*

Move each prompt constant to a plain `.md` or `.txt` file in a top-level `prompts/` directory. Load at agent server startup.

```
agent-mesh/
  prompts/
    compliance_agent.md
    data_agent.md
    rag_agent.md
    price_assist_agent.md
    summarizer.md
    cache_judge.md
    entity_extractor.md
```

Each `*_agent.py` changes from:
```python
COMPLIANCE_INSTRUCTIONS = """You are the Compliance Agent..."""
```
to:
```python
COMPLIANCE_INSTRUCTIONS = Path("prompts/compliance_agent.md").read_text()
```

**Hot-reload variant:** A `PromptLoader` utility that caches file `mtime` and re-reads when the file changes. A2A servers can poll every ~10 seconds so prompt edits take effect without restart.

```python
# src/prompts/loader.py
class PromptLoader:
    def get(self, name: str) -> str:
        path = PROMPTS_DIR / f"{name}.md"
        mtime = path.stat().st_mtime
        if mtime != self._cache[name].mtime:
            self._cache[name] = CachedPrompt(path.read_text(), mtime)
        return self._cache[name].text
```

**Pros:** Near-zero code change. Prompts become git-diffable plain text. Easy to edit without touching Python. IDE syntax highlighting and Markdown preview.  
**Cons:** No variable substitution in system prompts. No versioning beyond git history.  
**Effort:** ~1–2 hours

---

### Option 2 — Jinja2 Templated Prompts *(Role-Aware System Prompts)*

Same file layout as Option 1, but prompts are Jinja2 templates. The system prompt itself can accept role-specific variables — moving `allowed_tasks`/`denied_tasks` from the user message into the system prompt where they're more effective.

```jinja2
{# prompts/compliance_agent.md.j2 #}
You are the Compliance Agent for FAB's AI banking assistant.

{% if role %}
Current user role: {{ role }}
Allowed task categories: {{ allowed_tasks | join(", ") }}
Denied task categories: {{ denied_tasks | join(", ") }}
{% endif %}

## Rules
...
```

`workflow.py` renders the template before the A2A call instead of injecting into the user message. This means `create_demo_agent()` must be called per-request (or the A2A handler must update `instructions` per call).

**Pros:** Role-specific context in the system prompt where it's most effective. Templates are reusable and composable (Jinja2 inheritance). Separates logic from content.  
**Cons:** Adds `jinja2` dependency. Requires Agent to be constructed per-request (small architectural change — move `create_demo_agent()` call inside the A2A request handler rather than at startup).  
**Effort:** ~4–6 hours

---

### Option 3 — Prompt Registry with Versioning + Hot-Reload *(Development Velocity)*

A `PromptRegistry` class (`src/prompts/registry.py`) that:
- Loads all prompts from `prompts/` directory at startup
- Watches files with `watchfiles` and reloads in-place
- Supports named versions: `prompts/compliance_agent_v2.md` → activate via `COMPLIANCE_PROMPT_VERSION=v2`
- Exposes `registry.get("compliance_agent")` throughout the codebase

```python
class PromptRegistry:
    def get(self, name: str) -> str: ...
    def reload(self, name: str) -> None: ...
    async def watch(self) -> None: ...  # background watchfiles task
```

**Pros:** Central source of truth. Version switching without code change. A/B testing via env var. Live prompt iteration during development.  
**Cons:** More complex. Hot-reload must coordinate across 4 separate A2A server processes — each watches independently, or a shared store (Redis, SQLite) is used.  
**Effort:** ~1 day

---

### Option 4 — Database-Backed Prompts *(Full Runtime Control)*

Store prompts in SQLite or the existing Redis stub (`src/memory/`). An admin API endpoint or CLI command updates prompts at runtime. A2A servers pull their prompt on startup (or on TTL-based refresh).

**Pros:** Zero-restart prompt updates. Enables a prompt management UI. Full audit trail.  
**Cons:** Operational complexity. The Redis backend in `src/memory/` is a stub — would need completing, or SQLite added. Network I/O per server startup.  
**Effort:** ~2 days

---

## Recommendation

**Start with Option 1 + hot-reload (thin subset of Option 3).**

This gives the biggest improvement for the least disruption:
- Prompts become editable plain-text files (biggest day-to-day win)
- No structural changes to agent code or `agent_factory.py`
- Hot-reload without server restart via a simple `PromptLoader` file-watcher
- Foundation to layer Jinja2 (Option 2) on top when role-specific system prompt injection becomes valuable

**Then add Option 2 (Jinja2)** when you want to move `allowed_tasks`/`denied_tasks` into the system prompt level, giving the LLM a sharper role-context signal from the start of each conversation.

---

## What Would Change (Option 1 + Hot-Reload)

### New files
- `src/prompts/loader.py` — `PromptLoader` class with `get(name)` and file-watch
- `prompts/compliance_agent.md` — content moved verbatim from `COMPLIANCE_INSTRUCTIONS`
- `prompts/data_agent.md` — content moved verbatim from `DATA_INSTRUCTIONS`
- `prompts/rag_agent.md` — content moved verbatim from `RAG_INSTRUCTIONS`
- `prompts/price_assist_agent.md` — content moved verbatim from `PRICE_ASSIST_INSTRUCTIONS`
- `prompts/summarizer.md` — content moved verbatim from `_SYSTEM_PROMPT`
- `prompts/cache_judge.md` — content moved verbatim from `_JUDGE_PROMPT`
- `prompts/entity_extractor.md` — content moved verbatim from `_EXTRACT_PROMPT`

### Files to modify
- `src/agents/compliance_agent.py`, `data_agent.py`, `rag_agent.py`, `price_assist_agent.py` — replace string constant with `prompt_loader.get("...")`
- `src/memory/summarizer.py`, `src/cache/cache_judge.py`, `src/cache/entity_extractor.py` — same

### Unchanged
- `workflow.py` — per-request f-string assembly stays as-is
- `agent_factory.py` — `create_demo_agent(instructions=...)` API unchanged
- All A2A transport, MCP wiring, cache, memory layers

---

## Verification Steps

1. Start all 4 A2A servers: `python launch_mesh.py`
2. Send a test query — confirm all agents respond correctly with prompts loaded from files
3. Edit `prompts/compliance_agent.md` in-place → within hot-reload TTL, send another query → confirm updated behavior without restart
4. Run workflow evaluations: `cd agent-mesh/workflow_evaluations && python run_evaluations.py` — confirm no regression
