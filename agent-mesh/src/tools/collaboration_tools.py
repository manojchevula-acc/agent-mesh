"""Cross-agent collaboration tools (agent-to-agent peer delegation).

These let the Price Assist coordinator reach its peer domain agents over A2A
during its own reasoning. The coordinator's LLM decides which peer(s) to call:

  - query_structured_data  -> Data Agent  -> DataLayer service (structured SQL data)
  - query_knowledge_base   -> RAG Agent   -> RAG service (banking knowledge documents)

Two safeguards:
1. Depth guard — a ContextVar bounds nested delegation within a single process so
   a misbehaving chain cannot recurse without limit.
2. Soft-fail — a failed hop returns an error STRING rather than raising, so the
   coordinator can tell the user a source is unavailable instead of crashing.
"""
import asyncio
import sys
import time
import pathlib
import contextvars

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_core.tools import tool
from src.a2a.clients import ask_remote

# Bounds nested peer delegation within a single process.
_peer_depth: contextvars.ContextVar[int] = contextvars.ContextVar("peer_call_depth", default=0)
_MAX_PEER_DEPTH = 2

# Retry once on transient A2A transport failures (connection reset, brief unavailability).
# Does NOT retry on application-level errors (e.g. echo/rate-limit responses).
_A2A_RETRIES = 1
_A2A_RETRY_DELAY = 0.75  # seconds between attempts

# Per-request result cache keyed by (node, question). Scoped to the current async
# context so different concurrent requests never share entries. Initialised lazily
# on the first _consult_peer call in a given context.
_peer_cache: contextvars.ContextVar[dict] = contextvars.ContextVar("peer_cache", default=None)

# Accumulates LLM reasoning entries extracted from peer agent responses. Each
# _consult_peer call appends here so DomainExecutor can forward them to the tracer.
_peer_reasoning: contextvars.ContextVar[list] = contextvars.ContextVar("peer_reasoning", default=None)


async def _consult_peer(node: str, question: str, unavailable_label: str) -> str:
    depth = _peer_depth.get()
    if depth >= _MAX_PEER_DEPTH:
        return "PEER_LIMIT: delegation depth exceeded; aborting to prevent loops."

    # Deduplicate: if this (node, question) pair was already fetched in the current
    # request context, return the cached result immediately. This prevents the MAF
    # tool-loop from calling the same downstream agent multiple times and receiving
    # a Groq-rate-limited echo response on the second call.
    cache = _peer_cache.get()
    if cache is None:
        cache = {}
        _peer_cache.set(cache)
    cache_key = (node, question.strip())
    if cache_key in cache:
        return cache[cache_key]

    token = _peer_depth.set(depth + 1)
    t0 = time.perf_counter()
    error_occurred = False
    try:
        # Retry once on transient transport errors (connect reset, brief unavailability).
        _last_exc: Exception | None = None
        result = None
        for _attempt in range(_A2A_RETRIES + 1):
            try:
                result = await ask_remote(node, question)
                break
            except Exception as _e:
                _last_exc = _e
                if _attempt < _A2A_RETRIES:
                    await asyncio.sleep(_A2A_RETRY_DELAY)
        if result is None:
            raise _last_exc  # all retry attempts failed — fall into outer except
        # Extract and strip any <llm_reasoning> blocks emitted by the peer agent in
        # its final response. Entries are stored in _peer_reasoning so DomainExecutor
        # can forward them to the tracer. The result returned to PriceAssist is clean
        # (no raw JSON blocks), saving tokens in PriceAssist's tool-result context.
        if result:
            try:
                from src.tracing.llm_reasoning import extract_reasoning as _er
                _entries, _clean = _er(result, node)
                if _clean.strip():
                    result = _clean
                if _entries:
                    _pr = _peer_reasoning.get()
                    if _pr is None:
                        _pr = []
                        _peer_reasoning.set(_pr)
                    _pr.extend([e.to_dict() for e in _entries])
                    # Also persist to a temp file keyed by request_id so the API
                    # server process can read them (ContextVars are process-local).
                    try:
                        from src.observability.baggage import get_request_id as _grid
                        import json as _json
                        _rid = (_grid() or "").upper().strip()
                        if _rid and _rid != "-":
                            _pf = pathlib.Path("data/logs") / f".peer_{_rid}.json"
                            _pf.parent.mkdir(parents=True, exist_ok=True)
                            _existing = _json.loads(_pf.read_text()) if _pf.exists() else []
                            _pf.write_text(_json.dumps(_existing + [e.to_dict() for e in _entries]))
                    except Exception:
                        pass
            except Exception:
                pass
        # Echo detection: if the peer returned the input question verbatim it means
        # the downstream agent's LLM failed to call its tool (e.g. hit a Groq
        # rate-limit before making any MCP call and just echoed the prompt).
        if result and result.strip() == question.strip():
            result = (
                f"{unavailable_label}: agent returned no data "
                "(response matched input — downstream LLM may be rate-limited)."
            )
            error_occurred = True
        cache[cache_key] = result
        return result
    except Exception as e:
        error_occurred = True
        result = f"{unavailable_label}: could not reach the {node} agent ({e})."
        cache[cache_key] = result
        return result
    finally:
        _peer_depth.reset(token)
        # Enrich the enclosing execute_tool span (created by Agent Framework for
        # the @tool function) with peer delegation metadata so traces show which
        # sub-agent was consulted, at what depth, and how long it took.
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            if span and span.is_recording():
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                span.set_attribute("peer.target_node", node)
                span.set_attribute("peer.depth",       depth)
                span.set_attribute("peer.result",      "ERROR" if error_occurred else "SUCCESS")
                span.set_attribute("peer.duration_ms", elapsed_ms)
        except Exception:
            pass


@tool(description=(
    "Query FAB structured banking data via the Data Agent: customer profiles, deal "
    "pricing, margins, profitability, and RWA/capital (DataLayer). Use for any "
    "numeric or record lookup about a customer or deal (e.g. CUST001's recommended "
    "price, margin analysis, profitability tier, RWA impact)."
))
async def query_structured_data(question: str) -> str:
    """Agent-to-agent hop: Price Assist asks the Data Agent (over A2A)."""
    return await _consult_peer("data_agent", question, "DATA_UNAVAILABLE")


@tool(description=(
    "Query FAB banking knowledge via the RAG Agent: pricing floors and ceilings, "
    "fee schedules, credit/regulatory policy, product guidelines, FAQs, operational "
    "procedures, AML/KYC rules, concentration limits, model risk policy "
    "(RAG-as-a-Service knowledge base). Use to retrieve the rule or benchmark that "
    "a price or action must comply with."
))
async def query_knowledge_base(question: str) -> str:
    """Agent-to-agent hop: Price Assist asks the RAG Agent (over A2A)."""
    return await _consult_peer("rag_agent", question, "RAG_UNAVAILABLE")


COORDINATION_TOOLS = [query_structured_data, query_knowledge_base]
