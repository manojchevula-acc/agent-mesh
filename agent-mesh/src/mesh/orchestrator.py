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
import sys
import time
import uuid
import pathlib
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
from src.observability.metrics import record_mesh_request
from src.mesh.workflow import MeshState, build_mesh_workflow
from src.tracing.execution_trace import get_active_tracer
from src.memory import ConversationStore

_log = get_logger(CAT_SYSTEM)


@dataclass
class MeshResult:
    answer: str
    blocked: bool = False
    block_stage: Optional[str] = None
    trail: List[str] = field(default_factory=list)
    session_id: str = ""


async def handle_request(user: User, query: str, session_id: str | None = None) -> MeshResult:
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
    request_id = uuid.uuid4().hex[:8].upper()
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
    )

    # Load prior conversation turns for this session so PriceAssistAgent can resolve
    # follow-ups in-context. No-op (empty history) when memory is disabled.
    store = ConversationStore()
    if Config.ENABLE_CONVERSATION_MEMORY:
        try:
            initial.conversation_history = store.load(session_id, Config.CONVERSATION_MAX_TURNS)
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

    # Persist this turn so the next request in the session sees it. Only non-blocked
    # turns with a real answer are stored (blocked queries carry no useful context).
    if Config.ENABLE_CONVERSATION_MEMORY and not final.blocked and final.answer:
        try:
            store.append_turn(session_id, query, final.answer)
        except Exception as exc:  # never let memory I/O break a request
            _log.warning("conversation history save failed session=%s: %s", session_id, exc)

    return MeshResult(
        answer=final.answer,
        blocked=final.blocked,
        block_stage=final.block_stage,
        trail=final.trail,
        session_id=session_id,
    )


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
