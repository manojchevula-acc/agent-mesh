Plan — Response Cache ("same question → cached answer") for stable knowledge turns
Context
Today every query — even an identical repeat — runs the full pipeline including the expensive DomainExecutor hop (PriceAssist → Data/RAG agents → multiple LLM calls + synthesis). For stable knowledge/policy questions (e.g. "What is the pricing floor for BB-rated AED corporate loans?") re-generating the same answer wastes latency and tokens.

Goal: a short-TTL response cache that serves a recent answer for a repeated knowledge/policy question, without ever caching live customer-data or compliance-sensitive answers, and without weakening safety — the guardrail, RBAC, compliance, and output-redaction stages still run on every request. Only the domain answer-generation hop is skipped on a cache hit.

Safety design (why this is safe here)
Cache only pure-knowledge turns. Eligibility is gated twice:
Lookup gate (query-only): the question has a policy/RAG keyword and no data keyword and no entity id (CUST001/deal) — reuses the keyword vocab already in execution_trace.py.
Store gate (post-answer): only store when the inferred route is not Data Layer / Hybrid / Conversation Context (i.e. a pure "RAG Service" knowledge answer).
Never cache customer data / compliance — those carry entity ids or data keywords, so they fail the lookup gate and are never even looked up.
Short TTL (default 60s) bounds staleness.
Safety gates always run — the cache check lives inside DomainExecutor, so guardrail → RBAC → compliance precede it and output-redaction follows it. Redaction re-applies every time because we cache the pre-redaction answer.
Per-session key (session_id + normalized_query) → a user only ever gets their own cached answers; role is implicitly fixed within a session.
Implementation
1. NEW — src/cache/ package
Mirror the small, self-contained style of src/memory/.

src/cache/__init__.py — export get_response_cache, ResponseCache.
src/cache/response_cache.py:
@dataclass CacheEntry: answer: str; route: str; ts: float + computed age_seconds.
class ResponseCache (in-process TTL dict; asyncio is single-threaded so a plain dict is fine):
Reads config in __init__: enabled, ttl (RESPONSE_CACHE_TTL_SECONDS), max_entries.
@staticmethod _normalize(query) — lowercase, strip, collapse whitespace, strip trailing punctuation (so "…loans?" == "…loans").
_key(session_id, query) — f"{session_id}\x1f{normalized}".
get(session_id, query) -> Optional[CacheEntry] — returns None if disabled / missing / older than TTL (and deletes the expired entry).
set(session_id, query, answer, route) — store with ts=now; evict oldest if over max_entries (simple FIFO/dict insertion-order pop).
Module-level singleton + get_response_cache() factory (so all requests share one cache in the api_server process).
Future Redis: note in the docstring that this can later swap to a shared backend (same pattern as src/memory/redis_backend.py); not built now.
2. src/config.py — cache knobs (after the conversation-memory block)
ENABLE_RESPONSE_CACHE: bool = os.getenv("ENABLE_RESPONSE_CACHE", "true").lower() in ("1","true","yes")
RESPONSE_CACHE_TTL_SECONDS: int = int(os.getenv("RESPONSE_CACHE_TTL_SECONDS", "60"))
RESPONSE_CACHE_MAX_ENTRIES: int = int(os.getenv("RESPONSE_CACHE_MAX_ENTRIES", "512"))
3. src/tracing/execution_trace.py — query-only eligibility helper
Add next to query_has_retrieval_signal (reuse _DATA_KW, _RAG_KW, _ENTITY_RE):

def is_pure_knowledge_query(query: str) -> bool:
    """True for stable policy/knowledge questions safe to cache: has a RAG/policy
    keyword, but NO data keyword and NO entity id (so never customer data/compliance)."""
    q = (query or "").lower()
    if _ENTITY_RE.search(q):
        return False
    if any(k in q for k in _DATA_KW):
        return False
    return any(k in q for k in _RAG_KW)
4. src/mesh/workflow.py DomainExecutor.run — lookup (hit) + store (miss)
Import is_pure_knowledge_query (already importing from execution_trace) and from src.cache import get_response_cache. Inside the with _span_ctx(...) block, after answered_from_context is computed:

Lookup / hit branch:

cache = get_response_cache()
cache_eligible = cache.enabled and is_pure_knowledge_query(state.query)
hit = cache.get(state.session_id, state.query) if cache_eligible else None
Wrap the existing answer-generation block (the try: ask_remote ... retries and the if tracer and not failed: route-inference/emit block) in if hit is None:. Add an else: (cache hit) branch that:

answer = hit.answer; state.answer = answer; failed = False
state.trail.append("domain_cache_hit:price_assist")
_set_attr(span, "domain.cache", "HIT"); _add_event(span, "domain.cache.hit", {"age_s": int(hit.age_seconds)}); _set_ok(span)
emits an honest, minimal trace via the tracer: add_execution_path("Response Cache"), record_route(hit.route), record_domain("Price Assist Agent", 0.99), and a response_cache stage (result=f"Served from cache ({int(hit.age_seconds)}s old)", checks like "Identical knowledge question answered recently", no retrieval steps, no record_tool_used()).
record_a2a_call/record_domain_route are not called (no hop happened); duration stays the real (tiny) wall-clock.
Store on miss (after a successful fresh answer): just before await ctx.send_message(state), guarded by success:

if hit is None and not failed and cache_eligible and answer \
   and route not in ("Data Layer Service", "Conversation Context") and "Hybrid" not in route:
    cache.set(state.session_id, state.query, answer, route)
(route defaults to "unknown" when no tracer is active — still allowed to store, since the query already passed the pure-knowledge lookup gate.)

Net effect: identical knowledge question within TTL → guardrail/RBAC/compliance run, domain LLM hop skipped, redaction runs, answer returned. Data/compliance questions are never cached.

5. Trace label parity (frontend + CLI)
ExecutionPanel.tsx STAGE_LABELS: add response_cache: "Response Cache".
cli_renderer.py _STAGE_LABELS: add "response_cache": "RESPONSE CACHE".
(Optional polish) MessageBubble.tsx RouteChip: the cached route is "RAG Service" so it already styles correctly — no change needed.
Files touched
File	Change
src/cache/__init__.py	NEW — exports
src/cache/response_cache.py	NEW — in-memory TTL ResponseCache + factory
src/config.py	ENABLE_RESPONSE_CACHE, RESPONSE_CACHE_TTL_SECONDS, RESPONSE_CACHE_MAX_ENTRIES
src/tracing/execution_trace.py	is_pure_knowledge_query()
src/mesh/workflow.py	DomainExecutor: cache lookup (hit) + store (miss); response_cache trace step
frontend/.../ExecutionPanel.tsx	response_cache stage label
src/tracing/cli_renderer.py	response_cache label parity
Unchanged: A2A layer, agents, ConversationStore, orchestrator (cache lives entirely in DomainExecutor; the conversation turn is still saved by the orchestrator as today, so a cache hit is still recorded in conversation history).

Verification
Unit (cache): ResponseCache.set/get round-trip; get returns None after TTL (monkeypatch ts or set RESPONSE_CACHE_TTL_SECONDS=0); eviction past max_entries.
Eligibility: is_pure_knowledge_query — True for "pricing floor for BB-rated AED loans", "KYC requirements"; False for "CUST001 margin", "Is CUST001 compliant with policy".
Mocked end-to-end (patch orchestrator.ask_remote, count calls):
Ask a knowledge question twice in one session → second call: ask_remote("price_assist", …) not invoked, trail contains domain_cache_hit:price_assist, a response_cache trace stage present, tools_used == 0, answer identical.
Ask a data question ("CUST003 margin") twice → ask_remote invoked both times (never cached).
After TTL expiry → knowledge question re-invokes ask_remote (cache miss).
Confirm guardrail/compliance/redaction events still present on the cache-hit turn.
Toggle: ENABLE_RESPONSE_CACHE=false → every turn calls ask_remote (cache fully bypassed).
Frontend: npm run typecheck; a repeated knowledge question shows the "Response Cache" step.
Regression: offline suite unchanged except the known pre-existing test_rbac_blocks_invalid_role failure.
Notes / limits
Per-process cache. Multiple uvicorn workers each hold their own cache; a shared Redis backend (same pattern as the memory layer's Redis stub) is the future multi-node upgrade.
Per-session key (as requested) means cross-conversation repeats don't hit; a global/role-keyed variant would raise hit-rate at the cost of isolation — easy to switch later in _key().
Cache hits are still appended to conversation history by the orchestrator, so memory/trace stay consistent.