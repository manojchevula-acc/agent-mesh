"""Mesh client orchestrator.

Drives a single user request across the distributed agent mesh using a
Microsoft Agent Framework **Workflow** (see ``src/mesh/workflow.py``). The
workflow graph enforces a defense-in-depth safety/governance pipeline:

  1. Deterministic input screen  (hard gate: injection / PII / destructive)
  2. Compliance node (A2A)       -> semantic safety review (hard gate)
  3. Policy node (A2A)           -> resolves the corporate rules that apply
  4. Deterministic output redaction (PII)

Each stage is a workflow executor, so the framework emits native ``workflow.run``
/ ``executor.process`` spans and auto-propagates trace context between hops. A
root ``mesh.request`` span ties the whole request together; the A2A client
carries the context across process boundaries so every node joins one trace.

The public surface (``handle_request`` + ``MeshResult``) and the ``ask_remote``
seam are preserved for the offline test suite.
"""
import asyncio
import sys
import time
import uuid
import pathlib
import dataclasses
from dataclasses import dataclass, field
from typing import List, Optional

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.auth.identity_provider import User
from src.guardrails.deterministic_filters import screen_input, redact_pii  # re-exported for tests/back-compat
from src.a2a.clients import ask_remote
from src.utils.console_logger import AgentLogger
from src.observability import get_logger, CAT_SYSTEM
from src.observability.baggage import set_request_baggage, detach_baggage
from src.observability.metrics import record_mesh_request, record_cache
from src.mesh.workflow import MeshState, build_mesh_workflow, build_hitl_resume_workflow, build_intent_resume_workflow, _stream_queue, _emit_stream_event
from src.tracing.execution_trace import get_active_tracer
from src.tracing.llm_reasoning import strip_reasoning_markers
from src.memory import ConversationStore

_log = get_logger(CAT_SYSTEM)


def _record_cache_rejection(role: str, query: str, chosen_entry_id: str,
                            similarity: float, confidence: str) -> None:
    """Capture an explicit cache-HIT rejection as a false-positive signal.

    Bumps the in-memory reject counter, emits a metric, and appends a durable
    line to CACHE_REJECTIONS_LOG for later threshold tuning / a demote list.
    Best-effort — never raises into the request path.
    """
    try:
        is_hit = similarity >= 0.92
        from src.cache import get_cache_store
        _store = get_cache_store()
        if is_hit:
            _store._hit_rejected += 1
        else:
            _store._intent_rejected += 1
        record_cache("HIT_REJECTED" if is_hit else "INTENT_REJECTED",
                     role, 0.0, confidence=confidence)
    except Exception as exc:
        _log.warning("cache reject metric failed: %s", exc)
    try:
        import json as _json
        from datetime import datetime, timezone
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "query": query,
            "chosen_entry_id": chosen_entry_id,
            "similarity": round(float(similarity), 4),
            "confidence": confidence,
        }
        path = pathlib.Path(Config.CACHE_REJECTIONS_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.warning("cache reject log write failed: %s", exc)


@dataclass
class MeshResult:
    answer: str
    blocked: bool = False
    block_stage: Optional[str] = None
    trail: List[str] = field(default_factory=list)
    session_id: str = ""
    hitl_pending: bool = False
    hitl_approval_id: str = ""
    cache_hit: bool = False
    cache_age_hours: float = 0.0
    cache_similarity: float = 0.0
    cache_reasoning: List = field(default_factory=list)
    cache_judge_invoked: bool = False
    cache_judge_decision: str = ""   # "HIT" | "MISS" | ""
    cache_judge_reason: str = ""     # one-line reason from LLM judge
    # Intent-match suggestion provenance — set when the user accepted an intent suggestion
    intent_match_accepted: bool = False
    intent_match_root_query: str = ""
    intent_match_similarity: float = 0.0


async def handle_request(user: User, query: str, session_id: str | None = None, request_id: str | None = None, bypass_cache: bool = False) -> MeshResult:
    """Runs one request through the full mesh workflow.

    Opens a root ``mesh.request`` span so every downstream executor / agent / A2A
    span nests under one coherent distributed trace, then maps the workflow's
    terminal :class:`MeshState` to a :class:`MeshResult`.

    ``session_id`` ties consecutive turns into one conversation. When omitted, a
    fresh per-conversation id is generated; callers (api_server) should echo the
    returned ``MeshResult.session_id`` back on the next turn to continue the thread.
    """
    if not session_id:
        session_id = f"{user.username}_{uuid.uuid4().hex[:8]}"
    request_id = request_id or uuid.uuid4().hex[:8].upper()
    AgentLogger.print_agent_header("Mesh", "Dispatching request through the workflow graph")

    # Set W3C baggage BEFORE opening the root span so the baggage is inherited by
    # every child span and propagated via traceparent+baggage headers in A2A hops.
    _baggage_ctx, _baggage_token = set_request_baggage(
        request_id=request_id,
        user=user.username,
        role=user.role.value,
        session_id=session_id,
    )

    # Emit input_processing events to the active tracer (set by the CLI/API caller).
    tracer = get_active_tracer()
    if tracer:
        tracer.add_execution_path("Coordinator")
        tracer.emit_stage(
            "input_processing", "started",
            message="Processing request...",
        )
        tracer.emit_stage(
            "input_processing", "completed",
            checks=[
                "Request received",
                "Session identified",
                "User context loaded",
            ],
        )

    initial = MeshState(
        user_name=user.username,
        role=user.role.value,
        query=query,
        session_id=session_id,
        bypass_cache=bypass_cache,
    )

    # Load prior conversation turns for this session so PriceAssistAgent can resolve
    # follow-ups in-context. No-op (empty history) when memory is disabled.
    store = ConversationStore()
    _prior_summary: str = ""
    if Config.ENABLE_CONVERSATION_MEMORY:
        try:
            _prior_summary, _ = store.load_with_summary(session_id)
            initial.conversation_summary = _prior_summary
        except Exception as exc:  # never let memory I/O break a request
            _log.warning("conversation history load failed session=%s: %s", session_id, exc)

    # Build the workflow fresh per request, passing the (possibly patched at test
    # time) module-level ``ask_remote`` so the A2A seam is honoured.
    workflow = build_mesh_workflow(ask=ask_remote)

    final = None
    t0 = time.perf_counter()
    try:
        # Root the whole request in a single span (framework-native tracer). All
        # workflow/executor/agent/A2A spans become children of this one.
        span_cm = _root_span(user, query, session_id, request_id)
        with span_cm as root_span:
            _log.info("Request start user=%s role=%s query_len=%d req=%s",
                      user.username, user.role.value, len(query), request_id,
                      extra={"user": user.username, "session_id": session_id})
            events = await workflow.run(initial)

        final = _final_state(events)
        _enrich_root_span(root_span, final, request_id)
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000
        if final is None:
            record_mesh_request("ERROR", "internal_error", duration_ms)
        elif final.blocked:
            record_mesh_request("BLOCKED", final.block_stage or "none", duration_ms)
        else:
            record_mesh_request("SUCCESS", "none", duration_ms)
        detach_baggage(_baggage_token)

    if final is None:
        _log.error("Workflow produced no output", extra={"user": user.username})
        return MeshResult(answer="Internal error: no workflow output.", blocked=True,
                          block_stage="internal_error", trail=["no_output"],
                          session_id=session_id)

    # ── Human-in-the-Loop interception ──────────────────────────────────────────
    if getattr(final, "hitl_pending", False):
        from src.hitl.approval_store import approval_store
        aid = final.hitl_approval_id
        hitl_type = getattr(final, "hitl_type", "role_approval") or "role_approval"
        _log.info("HITL: awaiting approval id=%s type=%s user=%s", aid, hitl_type, user.username,
                  extra={"user": user.username, "status": "HITL_WAIT"})
        _emit_stream_event({
            "event_type": "hitl",
            "approval_id": aid,
            "hitl_type": hitl_type,
            "details": final.hitl_details,
        })
        # UC-5: no timeout — approval can take hours/days; checkpoint survives restart
        approved = await approval_store.wait_for_approval(aid)
        if approved:
            _log.info("HITL: approved id=%s type=%s — resuming", aid, hitl_type,
                      extra={"user": user.username, "status": "HITL_APPROVED"})
            final.hitl_pending = False
            if hitl_type == "tool_approval":
                # UC-3: tool was approved — generate confirmation directly
                # (do not re-run DomainExecutor to avoid firing the interceptor again)
                tool_details = final.hitl_details
                tool_name = tool_details.get("tool_name", "unknown")
                tool_args = tool_details.get("tool_args", {})
                args_display = " | ".join(f"{k}: {v}" for k, v in tool_args.items())
                final.answer = (
                    f"Action approved and executed.\n\n"
                    f"**{tool_name.replace('_', ' ').title()}**\n{args_display}\n\n"
                    f"The change has been applied and recorded in the audit trail."
                )
                final.trail.append(f"hitl_approved:tool:{tool_name}")
            else:
                # Standard role-level approval resume — run Domain → Redact
                resume_wf = build_hitl_resume_workflow(ask=ask_remote)
                resume_events = await resume_wf.run(final)
                resumed = _final_state(resume_events)
                if resumed is not None:
                    final = resumed
            approval_store.delete_checkpoint(aid)
        else:
            _log.info("HITL: rejected id=%s", aid,
                      extra={"user": user.username, "status": "HITL_REJECTED"})
            final.answer = (
                "This request was reviewed and declined by a human approver. "
                "Please contact your compliance team if you believe this is an error."
            )
            final.blocked = True
            final.block_stage = "hitl_rejected"
            final.hitl_pending = False
            final.trail.append("hitl_rejected")
            approval_store.delete_checkpoint(aid)
    # ── end HITL interception ────────────────────────────────────────────────────

    # ── Intent-match suggestion interception ────────────────────────────────────
    if getattr(final, "intent_match_pending", False):
        from src.cache.intent_decision_store import intent_decision_store
        entry_id = final.intent_match_entry_id
        _log.info(
            "Intent suggestion: awaiting user decision entry_id=%s user=%s",
            entry_id, user.username,
            extra={"user": user.username, "status": "INTENT_WAIT"},
        )
        final.intent_match_pending = False
        # Register before waiting so resolve() from the API endpoint always finds it
        intent_decision_store.create_pending(entry_id)
        outcome, chosen_id = await intent_decision_store.wait_for_decision_ex(entry_id, timeout=60.0)
        accepted = outcome == "accepted"
        primary_sim = getattr(final, "intent_match_similarity", 0.0)
        primary_conf = getattr(final, "intent_match_confidence", "")

        if accepted:
            # Find the specific candidate the user chose (may differ from top-1)
            chosen_id = chosen_id or entry_id
            candidates = getattr(final, "intent_match_candidates", [])
            chosen = next((c for c in candidates if c.get("entry_id") == chosen_id), None)
            chosen_answer = chosen["answer"] if chosen else final.intent_match_answer
            chosen_similarity = chosen["similarity"] if chosen else final.intent_match_similarity
            chosen_age = chosen["age_hours"] if chosen else final.intent_match_age_hours
            _log.info(
                "Intent suggestion: accepted chosen_id=%s sim=%.3f user=%s",
                chosen_id, chosen_similarity, user.username,
                extra={"user": user.username, "status": "INTENT_ACCEPTED"},
            )
            final.answer = chosen_answer
            final.cache_hit = True
            final.cache_similarity = chosen_similarity
            final.cache_age_hours = chosen_age
            final.skip_cache_store = True
            is_hit = chosen_similarity >= 0.92
            trail_tag = "cache_hit_selected" if is_hit else "intent_match_accepted"
            final.trail.append(f"{trail_tag}:chosen={chosen_id}:sim={chosen_similarity:.3f}")
            # Increment variant_count on the chosen entry (fire-and-forget) + record accept metrics
            try:
                from src.cache import get_cache_store
                _store = get_cache_store()
                asyncio.create_task(
                    asyncio.to_thread(_store.increment_variant_count, chosen_id)
                )
                if is_hit:
                    _store._hit_accepted += 1
                    record_cache("HIT_ACCEPTED", user.role.value, 0.0, confidence="high")
                else:
                    _store._intent_accepted += 1
                    record_cache("INTENT_ACCEPTED", user.role.value, 0.0, confidence=primary_conf)
            except Exception as _exc:
                _log.warning("intent_match: accept bookkeeping failed: %s", _exc)
        else:
            _log.info(
                "Intent suggestion: %s entry_id=%s — running full pipeline",
                outcome, entry_id, extra={"user": user.username, "status": "INTENT_REJECTED"},
            )
            final.skip_cache_store = True
            final.trail.append(f"intent_match_rejected:{outcome}")
            # Explicit reject (not a silent timeout) is a false-positive signal: the
            # candidate looked similar but the user said it's wrong. Record it for tuning.
            if outcome == "rejected":
                _record_cache_rejection(
                    role=user.role.value, query=query, chosen_entry_id=chosen_id or entry_id,
                    similarity=primary_sim, confidence=primary_conf,
                )
            resume_wf = build_intent_resume_workflow(ask=ask_remote)
            resume_events = await resume_wf.run(final)
            resumed = _final_state(resume_events)
            if resumed is not None:
                final = resumed
                final.skip_cache_store = True   # preserve the flag through resume
    # ── end intent-match interception ───────────────────────────────────────────

    # Safety-net: strip any <llm_reasoning> blocks that slipped through the
    # DomainExecutor extraction pass (e.g. on a retry path or when the LLM
    # placed the synthesis block after the answer text rather than before it).
    if final.answer:
        final.answer = strip_reasoning_markers(final.answer)

    # ── Populate semantic cache (post-redaction, final answer only) ─────────────
    # Only store when: cache is enabled, inline store is enabled, answer is non-empty,
    # request was not blocked, the answer was NOT itself a cache hit (avoid re-caching
    # stale data), and skip_cache_store is not set (intent-match variants never stored).
    # Negative-answer guard (Phase 7a): don't cache "no data found"/"unable to
    # retrieve" — a future identical query that now succeeds would get the stale miss.
    _is_negative = False
    if Config.CACHE_SKIP_NEGATIVE and final.answer:
        from src.cache.negative_filter import is_negative_answer
        _is_negative = is_negative_answer(final.answer)
        if _is_negative:
            final.trail.append("cache_store_skipped:negative_answer")

    if (
        Config.ENABLE_RESPONSE_CACHE
        and Config.CACHE_INLINE_STORE_ENABLED
        and final.answer
        and not final.blocked
        and not getattr(final, "cache_hit", False)
        and not getattr(final, "skip_cache_store", False)
        and not _is_negative
    ):
        try:
            from src.cache import get_cache_store
            _route = "unknown"
            _reasoning = []
            _active_tracer = get_active_tracer()
            if _active_tracer is not None:
                _summ = _active_tracer.summary()
                _route = _summ.route or "unknown"
                _reasoning = _summ.llm_reasoning or []
            # Entity signature for the gate. Reuse the value the CacheCheckExecutor
            # already extracted for this query; otherwise (definitive MISS — the gate
            # never ran) extract it now so future lookups can gate against this entry.
            # None → gating disabled → the "entities" metadata key is omitted.
            _entities = None
            if getattr(final, "query_entities_extracted", False):
                _entities = final.query_entities
            elif Config.CACHE_ENTITY_GATING_ENABLED:
                from src.cache import extract_entities, signature_to_str
                _entities = signature_to_str(await extract_entities(query, user.role.value))
            get_cache_store().store(
                query=query,
                answer=final.answer,
                role=user.role.value,
                route=_route,
                session_id=session_id,
                request_id=request_id or "",
                reasoning=_reasoning,
                entities=_entities,
            )
        except Exception as exc:
            _log.warning("cache store failed: %s", exc)
    # ── end cache population ─────────────────────────────────────────────────────

    # Persist this turn (including blocked ones) so the full conversation history
    # is visible when restoring the session.
    if Config.ENABLE_CONVERSATION_MEMORY and final.answer:
        try:
            snapshot: dict = {"trail": final.trail, "blocked": final.blocked, "role_at_time": user.role.value}
            if request_id:
                snapshot["request_id"] = request_id
            active_tracer = get_active_tracer()
            if active_tracer is not None:
                summ = active_tracer.summary()
                snapshot.update({
                    "route": summ.route,
                    "domain": summ.domain,
                    "duration_ms": summ.total_duration_ms,
                    "trace": [dataclasses.asdict(e) for e in summ.events],
                    "reasoning": summ.llm_reasoning,
                })
            # Cache provenance — preserve so the UI can replay the cache banner on history load
            if getattr(final, "cache_hit", False):
                snapshot.update({
                    "cache_hit": True,
                    "cache_age_hours": getattr(final, "cache_age_hours", 0.0),
                    "cache_similarity": getattr(final, "cache_similarity", 0.0),
                    "cache_judge_invoked": getattr(final, "cache_judge_invoked", False),
                    "cache_judge_decision": getattr(final, "cache_judge_decision", ""),
                    "cache_judge_reason": getattr(final, "cache_judge_reason", ""),
                })
            # Embed the prior summary inline so every assistant record carries the
            # context that was used to answer this turn (audit trail + fallback for load).
            snapshot["rolling_summary"] = _prior_summary
            store.append_turn_rich(session_id, query, final.answer, snapshot=snapshot)
            # Bind owner on the first turn; no-op on subsequent turns in the session.
            store.bind_session(session_id, user.username)
            # Fire rolling summarization as a non-blocking background task so it
            # never adds latency to the response path.
            if Config.ENABLE_ROLLING_SUMMARIZATION:
                try:
                    from src.memory.summarizer import summarize_and_persist
                    asyncio.create_task(
                        summarize_and_persist(session_id, _prior_summary, query, final.answer)
                    )
                except Exception as exc_sum:
                    _log.warning("summarization task creation failed session=%s: %s", session_id, exc_sum)
        except Exception as exc:  # never let memory I/O break a request
            _log.warning("conversation history save failed session=%s: %s", session_id, exc)

    _intent_accepted = (
        "intent_match_accepted" in " ".join(getattr(final, "trail", []))
    )
    return MeshResult(
        answer=final.answer,
        blocked=final.blocked,
        block_stage=final.block_stage,
        trail=final.trail,
        session_id=session_id,
        hitl_pending=getattr(final, "hitl_pending", False),
        hitl_approval_id=getattr(final, "hitl_approval_id", ""),
        cache_hit=getattr(final, "cache_hit", False),
        cache_age_hours=getattr(final, "cache_age_hours", 0.0),
        cache_similarity=getattr(final, "cache_similarity", 0.0),
        cache_reasoning=getattr(final, "cache_reasoning", []),
        cache_judge_invoked=getattr(final, "cache_judge_invoked", False),
        cache_judge_decision=getattr(final, "cache_judge_decision", ""),
        cache_judge_reason=getattr(final, "cache_judge_reason", ""),
        intent_match_accepted=_intent_accepted,
        intent_match_root_query=getattr(final, "intent_match_root_query", ""),
        intent_match_similarity=getattr(final, "intent_match_similarity", 0.0),
    )


async def handle_request_stream(
    user: User,
    query: str,
    session_id: str | None = None,
    request_id: str | None = None,
    event_queue: "asyncio.Queue | None" = None,
    bypass_cache: bool = False,
) -> MeshResult:
    """Same as handle_request() but pushes per-stage events into event_queue for SSE streaming.

    The caller is responsible for reading from event_queue and formatting SSE chunks.
    A sentinel ``None`` is pushed when the pipeline finishes (or errors), signalling EOF.
    """
    token = None
    if event_queue is not None:
        token = _stream_queue.set(event_queue)
    try:
        result = await handle_request(user, query, session_id, request_id, bypass_cache=bypass_cache)
    except Exception:
        if event_queue is not None:
            event_queue.put_nowait(None)
        raise
    finally:
        if token is not None:
            _stream_queue.reset(token)
    if event_queue is not None:
        event_queue.put_nowait(None)
    return result


def _root_span(user: User, query: str, session_id: str, request_id: str = ""):
    """Returns a context manager for the root ``mesh.request`` span.

    Falls back to a no-op context manager if OpenTelemetry is unavailable.
    The caller is responsible for calling ``_enrich_root_span`` while the span
    is still open (before ``__exit__``).
    """
    try:
        from agent_framework.observability import get_tracer
        from opentelemetry.trace import SpanKind

        cm = get_tracer().start_as_current_span("mesh.request", kind=SpanKind.SERVER)

        class _Wrapped:
            def __enter__(self):
                self._span = cm.__enter__()
                try:
                    self._span.set_attribute("mesh.user", user.username)
                    self._span.set_attribute("mesh.role", user.role.value)
                    self._span.set_attribute("session.id", session_id)
                    self._span.set_attribute("mesh.query_length", len(query))
                    if request_id:
                        self._span.set_attribute("fab.request_id", request_id)
                except Exception:
                    pass
                return self._span

            def __exit__(self, *exc):
                return cm.__exit__(*exc)

        return _Wrapped()
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _enrich_root_span(span, final: Optional[MeshState], request_id: str) -> None:
    """Enriches the root span with workflow outcome while it is still open."""
    try:
        if span is None or not hasattr(span, "set_attribute"):
            return
        if final:
            span.set_attribute("mesh.blocked",            final.blocked)
            span.set_attribute("mesh.block_stage",        final.block_stage or "none")
            span.set_attribute("mesh.trail",              " -> ".join(final.trail))
            span.set_attribute("mesh.compliance_verdict", (final.compliance_verdict or "")[:120])
            span.set_attribute("fab.request_id",          request_id)
            span.add_event("mesh.request.completed", attributes={
                "blocked":     final.blocked,
                "block_stage": final.block_stage or "none",
                "trail":       " -> ".join(final.trail),
            })
        else:
            span.set_attribute("mesh.blocked",    True)
            span.set_attribute("mesh.block_stage", "internal_error")
            span.add_event("mesh.request.completed", attributes={
                "blocked":     True,
                "block_stage": "internal_error",
                "trail":       "",
            })
    except Exception:
        pass


def _final_state(events) -> Optional[MeshState]:
    """Extracts the terminal MeshState from workflow run events."""
    try:
        outputs = events.get_outputs()
        for out in reversed(outputs):
            if isinstance(out, MeshState):
                return out
    except Exception:
        pass
    return None
