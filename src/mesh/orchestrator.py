"""Mesh client orchestrator.

Drives a single user request across the distributed agent mesh using a
Microsoft Agent Framework **Workflow** (see ``src/mesh/workflow.py``). The
workflow graph enforces defense-in-depth guardrails and role-based access:

  1. Deterministic input screen  (hard gate: injection / PII / destructive)
  2. Router node (A2A)           -> domain classification
  3. Role-based access control   (e.g. finance = leadership only)
  4. Compliance node (A2A)       -> semantic safety review (hard gate)
  5. Payment approval gate       -> human-in-the-loop for outbound payments
  6. Domain node (A2A)           -> the actual answer
  7. Deterministic output redaction (PII)

Each stage is a workflow executor, so the framework emits native ``workflow.run``
/ ``executor.process`` spans and auto-propagates trace context between hops. A
root ``mesh.request`` span ties the whole request together; the A2A client
carries the context across process boundaries so every node joins one trace.

The public surface (``handle_request`` + ``MeshResult``) and the ``ask_remote``
seam are preserved for the offline test suite.
"""
import sys
import time
import pathlib
from dataclasses import dataclass, field
from typing import Callable, List, Optional

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.auth.identity_provider import User
from src.guardrails.deterministic_filters import screen_input, redact_pii  # re-exported for tests/back-compat
from src.a2a.clients import ask_remote
from src.utils.console_logger import AgentLogger
from src.observability import get_logger, CAT_SYSTEM
from src.mesh.workflow import MeshState, build_mesh_workflow

_log = get_logger(CAT_SYSTEM)


def _cli_approver(prompt: str) -> bool:
    """Default human approver: a CLI yes/no. Works across the A2A boundary because
    it runs in the orchestrator (client) process, not inside the agent."""
    try:
        return input(f"\n>>> {prompt} (yes/no): ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


@dataclass
class MeshResult:
    answer: str
    domain: Optional[str] = None
    domains: List[str] = field(default_factory=list)
    blocked: bool = False
    block_stage: Optional[str] = None
    trail: List[str] = field(default_factory=list)


async def handle_request(user: User, query: str, approver: Callable[[str], bool] = _cli_approver) -> MeshResult:
    """Runs one request through the full mesh workflow.

    Opens a root ``mesh.request`` span so every downstream executor / agent / A2A
    span nests under one coherent distributed trace, then maps the workflow's
    terminal :class:`MeshState` to a :class:`MeshResult`.
    """
    session_id = f"sess_{user.username}"
    AgentLogger.print_agent_header("Mesh", "Dispatching request through the workflow graph")

    initial = MeshState(
        user_name=user.username,
        role=user.role.value,
        query=query,
        session_id=session_id,
    )

    # Build the workflow fresh per request, passing the (possibly patched at test
    # time) module-level ``ask_remote`` so the A2A seam is honoured.
    workflow = build_mesh_workflow(ask=ask_remote, approver=approver)

    from src.observability import record_request

    t0 = time.perf_counter()
    final = None
    try:
        # Root the whole request in a single span (framework-native tracer). All
        # workflow/executor/agent/A2A spans become children of this one.
        # _final_state is extracted INSIDE the span so we can annotate it with
        # domain/blocked/block_stage before it closes and set the correct status.
        with _root_span(user, query, session_id) as _span:
            _log.info("Request start user=%s role=%s query_len=%d",
                      user.username, user.role.value, len(query),
                      extra={"user": user.username, "session_id": session_id})
            events = await workflow.run(initial)
            final = _final_state(events)
            if final is not None:
                _annotate_span(_span, final)
    finally:
        duration_ms = (time.perf_counter() - t0) * 1000
        _domain = (final.domain or "unknown") if final else "unknown"
        _status = "error" if final is None else ("blocked" if final.blocked else "success")
        record_request(
            domain=_domain,
            role=user.role.value,
            status=_status,
            duration_ms=duration_ms,
            block_stage=(final.block_stage if final else None),
        )

    if final is None:
        _log.error("Workflow produced no output", extra={"user": user.username})
        return MeshResult(answer="Internal error: no workflow output.", blocked=True,
                          block_stage="internal_error", trail=["no_output"])

    if final.blocked:
        AgentLogger.print_agent_response("Mesh", f"[{final.block_stage}] {final.answer}")
    else:
        domain_label = ", ".join(final.domains) if final.domains else (final.domain or "Mesh")
        AgentLogger.print_agent_response(domain_label, final.answer)

    return MeshResult(
        answer=final.answer,
        domain=final.domain,
        domains=final.domains,
        blocked=final.blocked,
        block_stage=final.block_stage,
        trail=final.trail,
    )


def _root_span(user: User, query: str, session_id: str):
    """Returns a context manager for the root ``mesh.request`` span.

    Falls back to a no-op context manager if OpenTelemetry is unavailable.
    """
    try:
        from agent_framework.observability import get_tracer
        from opentelemetry.trace import SpanKind

        cm = get_tracer().start_as_current_span("mesh.request", kind=SpanKind.CLIENT)

        class _Wrapped:
            def __enter__(self):
                self._span = cm.__enter__()
                try:
                    self._span.set_attribute("mesh.user", user.username)
                    self._span.set_attribute("mesh.role", user.role.value)
                    self._span.set_attribute("session.id", session_id)
                    self._span.set_attribute("mesh.query_length", len(query))
                except Exception:
                    pass
                return self._span

            def __exit__(self, *exc):
                return cm.__exit__(*exc)

        return _Wrapped()
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _annotate_span(span, final: "MeshState") -> None:
    """Sets final-state attributes on the root span and ERROR status if blocked.

    Called inside the ``with _root_span(...) as span:`` block so attributes
    land on the span before it is finalized and exported.
    """
    try:
        from opentelemetry.trace import StatusCode
        span.set_attribute("mesh.blocked", final.blocked)
        if final.domain:
            span.set_attribute("mesh.domain", final.domain)
        if final.block_stage:
            span.set_attribute("mesh.block_stage", final.block_stage)
        if final.blocked:
            span.set_status(StatusCode.ERROR, f"blocked:{final.block_stage}")
        else:
            span.set_status(StatusCode.OK)
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
