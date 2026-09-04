"""FastAPI wrapper (out-of-process mode). Design Document §8.2.

The same logic that runs in-process as bound tools runs here behind a single endpoint.
The DAL calls POST /v1/sql-agent/invoke; the envelope and the success/error output
contract are identical to the in-process case, so promotion requires no caller change.
"""

from __future__ import annotations

import ast
import asyncio
import json
from functools import lru_cache

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sql_agent.agent.messages import (
    is_assistant, is_tool_result, is_user, text_of, tool_call_id_of,
    tool_calls_of, tool_name_of, tool_result_text, user_message,
)
from sql_agent.config import settings
from sql_agent.feedback import capture_implicit
from sql_agent.feedback import record as record_feedback
from sql_agent.formatting import format_error
from sql_agent.logging_config import get_logger
from sql_agent.memory import (
    delete_session,
    get_conversation_store,
    list_sessions,
    list_turns,
    new_session_id,
    record_turn,
    rename_session,
    touch_session,
)
from sql_agent.routing.tier_router import (
    GATED_SCOPE,
    GATED_TOOLS,
    TOOL_TIER_REGISTRY,
    fixed_tiers_disabled,
)
from sql_agent.tools.registry import ALL_TOOLS, set_caller_scopes
from sql_agent.validation.exceptions import AuthError, SQLAgentError

log = get_logger("service")

app = FastAPI(title="SQL Agent — DAL micro-service", version="1.0.0")

# Allow the browser frontend to call this service directly (no dev proxy required).
# CORS_ALLOW_ORIGINS is a comma-separated list; "*" (default) permits any origin.
# We do not use cookies, so credentials stay off (required when origins is "*").
_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Envelope(BaseModel):
    caller_agent: str
    session_id: str | None = None
    correlation_id: str | None = None
    timestamp: str | None = None
    auth_scope: list[str] = Field(default_factory=list)


class InvokeRequest(BaseModel):
    envelope: Envelope
    tool: str
    args: dict = Field(default_factory=dict)


class AskRequest(BaseModel):
    envelope: Envelope
    question: str
    user_id: str = "anonymous"
    session_id: str | None = None    # omit to start a new chat


class FeedbackRequest(BaseModel):
    user_id: str
    session_id: str
    turn_ref: str | None = None
    rating: str | None = None          # "positive" | "negative"
    correction_text: str | None = None
    stage: str | None = None


class RenameSessionRequest(BaseModel):
    user_id: str
    title: str = Field(min_length=1, max_length=256)


@app.post("/v1/sql-agent/invoke")
def invoke(req: InvokeRequest) -> dict:
    """Resolve the named tool, enforce gating, run validate->query->calculate->format."""
    if req.tool not in TOOL_TIER_REGISTRY:
        return format_error("ValidationError", f"Unknown tool '{req.tool}'", retryable=False)

    scopes = set(req.envelope.auth_scope)
    set_caller_scopes(scopes)

    # Per-agent auth scope: gated tools require the dynamic_sql scope (Design §11.2).
    # Bypassed when the fixed tiers are all off and the dynamic tool is the only path.
    if req.tool in GATED_TOOLS and GATED_SCOPE not in scopes and not fixed_tiers_disabled():
        return format_error("AuthError", f"'{req.tool}' requires the {GATED_SCOPE} scope",
                            retryable=False)

    tool = ALL_TOOLS[req.tool]
    try:
        # Validate against the tool's schema BEFORE calling, exactly as
        # StructuredTool.invoke(dict) did. Skipping this would change the endpoint's
        # error contract: a bad argument would surface as a TypeError from Python or,
        # worse, reach the SQL layer, instead of the ValidationError envelope callers
        # already handle. .func is the undecorated callable, so the endpoint stays
        # SYNC and its blocking DB work keeps running in FastAPI's threadpool.
        return tool.func(**tool.input_model(**req.args).model_dump())
    except AuthError as exc:
        return format_error("AuthError", str(exc), retryable=False)
    except SQLAgentError as exc:
        return format_error(type(exc).__name__, str(exc), retryable=False)
    except Exception as exc:  # config/DB/LLM failures -> clean envelope, not a 500
        return format_error(type(exc).__name__, str(exc), retryable=False)


@lru_cache(maxsize=1)
def _agent_workflow():
    """Build the ReAct workflow once. Conversation memory is no longer a compile-time
    argument — the store is consulted per turn by run_turn() (see agent/workflow.py),
    keyed by thread_id = session_id."""
    from sql_agent.agent.workflow import build_sql_agent_workflow
    return build_sql_agent_workflow()


@app.on_event("startup")
async def _prewarm() -> None:
    """Warm the heavy singletons at boot so the FIRST user request is fast.

    Without this, the first request pays the one-time cost of (a) building the
    workflow + opening the conversation store, and (b) loading the embedding model +
    opening the vector index — together ~30-60s that otherwise land on a real user's
    request and can trip a frontend timeout. Each step is best-effort: a failure here
    must not stop the server from starting (the work would just fall back to lazy init
    on first use)."""
    try:
        _agent_workflow()
        get_conversation_store()      # open the sqlite file / pg pool at boot
        log.info("prewarm: agent workflow ready")
    except Exception as exc:  # noqa: BLE001 — never block startup on prewarm
        log.warning("prewarm: agent workflow failed | %s", exc)

    if settings.schema_retrieval_enabled:
        try:
            # Loads the embedding model into RAM and builds/opens the vector index
            # (the lru_cached singletons the request path reuses).
            from sql_agent.semantic_layer.selector import select_tables
            await asyncio.to_thread(select_tables, "warmup")
            log.info("prewarm: schema retrieval ready (embeddings + vector index)")
        except Exception as exc:  # noqa: BLE001
            log.warning("prewarm: schema retrieval failed | %s", exc)

    if settings.kg_enabled:
        try:
            # get_kg_client() reads the built artifact and loads it into the configured
            # backend (memory | neo4j) — for neo4j that load is a real Cypher upsert, so
            # this doubles as a startup connectivity check instead of only failing on
            # the first user question that happens to need the KG. It logs its own
            # "KG ready | backend=... fingerprint=..." line on success (kg/client.py),
            # or a clear warning naming the cause (missing artifact / backend
            # unreachable) on failure — either way it degrades to "no KG grounding",
            # never to a broken turn. @lru_cache'd, so this also warms the client the
            # request path reuses, same as the embeddings/vector-index step above.
            from sql_agent.kg.client import get_kg_client
            client = await asyncio.to_thread(get_kg_client)
            if client is None:
                log.warning("prewarm: KG enabled but unavailable — see the warning "
                           "above for why (missing artifact or backend failure)")
            else:
                log.info("prewarm: KG ready (backend=%s)", settings.kg_backend)
        except Exception as exc:  # noqa: BLE001
            log.warning("prewarm: KG failed | %s", exc)


def _parse_tool_content(content):
    """Tool results come back as a tool-message payload (JSON string). Parse to dict."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except Exception:
            try:
                return ast.literal_eval(content)
            except Exception:
                return None
    return None


def _pending_request_prefix(messages: list) -> str:
    """If the thread's last agent action was ask_clarification, return the previous
    user message so we can fold it into this reply.

    ask_clarification records no id, so after it fires the customer/product the user
    named survives only as a NAME in an earlier user message. Models reliably act on
    the CURRENT message but not on an unresolved name buried in older history, so a
    bare "provide for 12 months" loses the customer. Prepending the prior request
    puts the whole ask back into the live turn. The prior request is cumulative: each
    clarification reply already folded in the one before it, so a single level of
    look-back reconstructs the entire chain (original request + every value supplied).
    Returns "" when we're not answering a clarification (normal turns are untouched)."""
    last_tool = next((m for m in reversed(messages) if is_tool_result(m)), None)
    if last_tool is None:
        return ""
    parsed = _parse_tool_content(tool_result_text(last_tool))
    if not (isinstance(parsed, dict) and parsed.get("status") == "clarification"):
        return ""
    last_human = next((m for m in reversed(messages) if is_user(m)), None)
    return text_of(last_human).strip() if last_human is not None else ""


def _merge_pending_clarification(store, session_id: str, question: str) -> str:
    """Fold any unanswered clarification request into `question` (see
    _pending_request_prefix). Reads the thread's messages from the conversation store;
    on any failure it degrades to the raw question so a turn never breaks over this."""
    try:
        prior_state = store.load(session_id) or {}
        prior = prior_state.get("messages", [])
    except Exception:
        return question
    prefix = _pending_request_prefix(prior)
    return f"{prefix}\n{question}" if prefix else question


@app.post("/v1/sql-agent/ask")
async def ask(req: AskRequest) -> dict:
    """Run a natural-language question through the ReAct agent.

    Unlike /invoke (which targets one named tool), this lets the agent's LLM choose
    the most specific tool and TIER per the design's decision tree — parameterised
    for a single named entity, semi-dynamic for a filtered list, gated dynamic only
    as a last resort. Returns the primary tool result enriched with the composed
    natural-language answer and a per-step trace.
    """
    scopes = set(req.envelope.auth_scope)
    set_caller_scopes(scopes)

    cid = req.envelope.correlation_id or "-"
    # Identity: each login person has a user_id; each chat is a session_id. The
    # session_id is the conversation-memory thread. Omitting it starts a fresh chat.
    # Permissions still come from auth_scope on THIS request.
    user_id = req.user_id or "anonymous"
    session_id = req.session_id or new_session_id()
    touch_session(session_id, user_id)
    log.info("[%s] ASK received | caller=%s | user=%s | session=%s | question=%r",
             cid, req.envelope.caller_agent, user_id, session_id, req.question)

    workflow = _agent_workflow()
    store = get_conversation_store()

    # If this turn answers an ask_clarification, fold the pending request back in so the
    # named customer/product isn't lost (ask_clarification records no id — see helper).
    question_text = _merge_pending_clarification(store, session_id, req.question)
    if question_text != req.question:
        log.info("[%s] ASK folding clarification context into reply", cid)

    # Pass ONLY the new turn; run_turn merges it with this session's history via
    # state.merge_state + add_messages (do not re-seed prior messages here).
    state = {
        "messages": [user_message(question_text)],
        "caller_agent": req.envelope.caller_agent,
        "auth_scopes": scopes,
        "tool_call_count": 0,
        "dynamic_call_count": 0,
        "correlation_id": cid,
        "user_id": user_id,
        "session_id": session_id,
    }
    try:
        from sql_agent.agent.workflow import run_turn
        out = await run_turn(workflow, state, store=store, thread_id=session_id)
    except SQLAgentError as exc:
        log.warning("[%s] ASK failed | %s: %s", cid, type(exc).__name__, exc)
        return format_error(type(exc).__name__, str(exc), retryable=False)
    except Exception as exc:  # config/DB/LLM failures -> clean envelope, not a 500
        log.error("[%s] ASK error | %s: %s", cid, type(exc).__name__, exc)
        return format_error(type(exc).__name__, str(exc), retryable=False)

    messages = out.get("messages", [])

    # With conversation memory the store returns the FULL thread history.
    # Scope the execution flow to THIS turn only (messages after the last human
    # turn) so each answer reports just the tools it ran — not prior turns' calls.
    last_human_idx = max(
        (i for i, m in enumerate(messages) if is_user(m)),
        default=-1,
    )
    turn_messages = messages[last_human_idx + 1:]

    # The arguments the model passed live on the assistant message's tool calls, not
    # on the tool RESULT message. Map tool_call_id -> args so each step can show
    # exactly which parameters were used (e.g. {"customer_id": "CUST002"}).
    call_args: dict[str, dict] = {}
    for m in turn_messages:
        if is_assistant(m):
            for tc in tool_calls_of(m):
                if tc.get("id"):
                    call_args[tc["id"]] = tc.get("args") or {}

    # Collect each tool result (in order) and pick the primary one to display.
    steps: list[dict] = []
    last_result: dict | None = None
    for m in turn_messages:
        if is_tool_result(m):
            parsed = _parse_tool_content(tool_result_text(m))
            if isinstance(parsed, dict):
                steps.append({
                    "tool": parsed.get("tool", tool_name_of(m)),
                    "query_tier": parsed.get("query_tier"),
                    "status": parsed.get("status"),
                    "rows_returned": parsed.get("rows_returned"),
                    "sql": parsed.get("sql"),
                    # Full execution flow for the UI: the call arguments and the
                    # bound SQL params behind this step's query.
                    "args": call_args.get(tool_call_id_of(m), {}),
                    "sql_params": parsed.get("sql_params"),
                    # Formula + term values behind a computed figure (compute_* tools).
                    "calc_explain": parsed.get("calc_explain"),
                })
                if parsed.get("status") == "success" or last_result is None:
                    last_result = parsed

    # Composed natural-language answer = the final assistant message with no tool calls.
    answer = ""
    for m in reversed(messages):
        if is_assistant(m) and not tool_calls_of(m):
            answer = text_of(m)
            break

    if steps:
        trace = " -> ".join(
            f"{s['tool']}({s['query_tier']},{s['status']},{s['rows_returned']}r)"
            for s in steps
        )
        log.info("[%s] ASK trace | %s", cid, trace)

    # Clarification: the agent's final action was to ask the user for a missing/
    # ambiguous required input (via ask_clarification). Return it as a clarification
    # PROMPT — not a data result and not an error — so the UI shows the question and
    # the user can reply in the same session to continue.
    if steps and steps[-1].get("tool") == "ask_clarification":
        question = (
            answer.strip()
            or (steps[-1].get("args") or {}).get("question")
            or "Could you share a bit more detail so I can run the right query?"
        )
        log.info("[%s] ASK clarification | %r", cid, question[:120])
        record_turn(
            session_id=session_id, user_id=user_id, turn_ref=cid,
            question=req.question, answer=question,
            tier=None, tool="ask_clarification", validated_sql=None, rows_returned=0,
        )
        return {
            "status": "clarification",
            "message": question,
            "session_id": session_id,
            "turn_ref": cid,
        }

    if last_result is None:
        # The agent produced a final answer WITHOUT calling any tool this turn. For a
        # data agent that means nothing was validated against the governed tables, so
        # the text is ungrounded — model memory / hallucination, an attempt to answer
        # from the conversation history (which is reference-only), or an out-of-scope
        # refusal. We must NOT present it as a successful, audited data result; every
        # answered turn has to run a tool. Surface it as an error the UI flags instead.
        message = answer.strip() if answer.strip() else (
            "The agent did not call any data tool, so no validated result was produced."
        )
        log.warning("[%s] ASK NoToolInvoked | no validated result; answer=%r",
                    cid, message[:120])
        # No tool ran => a routing/grounding miss; record it as an implicit signal.
        capture_implicit(session_id=session_id, user_id=user_id, turn_ref=cid,
                         tier=None, status="error", rows_returned=0)
        err = format_error("NoToolInvoked", message, retryable=False)
        err["session_id"] = session_id
        err["turn_ref"] = cid
        return err

    last_result["answer"] = answer
    last_result["steps"] = steps

    # Persist a readable history row + capture implicit feedback (best-effort, never
    # fails the request). turn_ref = correlation_id links any later explicit feedback.
    record_turn(
        session_id=session_id, user_id=user_id, turn_ref=cid,
        question=req.question, answer=answer,
        tier=last_result.get("query_tier"), tool=last_result.get("tool"),
        validated_sql=last_result.get("sql"),
        rows_returned=last_result.get("rows_returned"),
    )
    capture_implicit(
        session_id=session_id, user_id=user_id, turn_ref=cid,
        tier=last_result.get("query_tier"), status=last_result.get("status"),
        rows_returned=last_result.get("rows_returned") or 0,
    )
    last_result["session_id"] = session_id
    last_result["turn_ref"] = cid

    log.info("[%s] ASK done | tool=%s | tier=%s | rows=%s | answer=%s chars",
             cid, last_result.get("tool"), last_result.get("query_tier"),
             last_result.get("rows_returned"), len(answer))
    return last_result


@app.post("/v1/sql-agent/feedback")
def feedback(req: FeedbackRequest) -> dict:
    """Record explicit feedback (thumbs up/down + optional correction) on a turn."""
    polarity = {"positive": "positive", "negative": "negative"}.get(req.rating, "neutral")
    record_feedback(
        session_id=req.session_id, user_id=req.user_id, turn_ref=req.turn_ref,
        signal_type="explicit_correction" if req.correction_text else "explicit_rating",
        polarity=polarity, stage=req.stage, correction_text=req.correction_text,
    )
    return {"status": "ok"}


@app.get("/v1/sql-agent/sessions")
def sessions(user_id: str) -> dict:
    """List a user's chats (most recently active first)."""
    return {"sessions": list_sessions(user_id)}


@app.get("/v1/sql-agent/sessions/{session_id}/history")
def history(session_id: str) -> dict:
    """Readable chat history for the UI: each turn's question, answer, tier, and SQL."""
    return {"session_id": session_id, "turns": list_turns(session_id)}


@app.patch("/v1/sql-agent/sessions/{session_id}")
def rename(session_id: str, req: RenameSessionRequest) -> dict:
    """Rename a chat. Scoped to the owning user."""
    ok = rename_session(session_id, req.user_id, req.title.strip())
    return {"status": "ok" if ok else "noop"}


@app.delete("/v1/sql-agent/sessions/{session_id}")
def delete(session_id: str, user_id: str) -> dict:
    """Delete a chat and its turns. Scoped to the owning user."""
    ok = delete_session(session_id, user_id)
    return {"status": "ok" if ok else "noop"}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
