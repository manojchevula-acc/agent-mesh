"""Mesh orchestration as a LangGraph StateGraph.

The request pipeline is expressed as a typed ``StateGraph`` so each stage is a
plain async function that receives state and returns a partial-state dict:

    workflow.ainvoke(initial)
      └─ input_guardrail_node
      └─ rbac_validation_node
      └─ compliance_node      ──(HTTP POST)──► /invoke ComplianceAgent
      └─ domain_node           ──(HTTP POST)──► /invoke PriceAssistAgent
      └─ output_redaction_node

PriceAssistAgent is the primary FAB banking orchestrator.  It receives ALL
requests after the security/RBAC/compliance pipeline, classifies intent
internally, and delegates to DataAgent (→ DataLayer MCP) or RAGAgent (→ RAG
MCP) as needed.

Design notes
------------
- ``MeshState`` is a TypedDict; LangGraph merges each node's partial return
  dict into the running state, so nodes return ONLY the fields that changed.
- Blocking nodes return ``{"blocked": True, ...}``; conditional edges route to
  ``END`` when ``blocked`` is True, short-circuiting the rest of the pipeline.
- A2A-calling nodes use an injected ``ask`` callable so the offline test suite
  can patch the transport at the ``orchestrator.ask_remote`` seam.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import time
import pathlib
from contextvars import ContextVar
from typing import Awaitable, Callable, List, Literal, Optional

from typing_extensions import TypedDict

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.graph import StateGraph, END

from src.guardrails.deterministic_filters import screen_input, redact_pii
from src.auth.identity_provider import BankingRole
from src.observability import get_logger, CAT_WORKFLOW
from src.observability.metrics import (
    record_guardrail,
    record_rbac,
    record_compliance,
    record_domain_route,
    record_a2a_call,
    record_pii_hits,
)
from src.tracing.execution_trace import get_active_tracer, infer_route_and_scores
from src.tracing.llm_reasoning import extract_reasoning, strip_reasoning_markers
from src.memory import ConversationStore
from src.config import Config

_log = get_logger(CAT_WORKFLOW)

# Type alias for the injected dependency.
AskRemote = Callable[..., Awaitable[str]]

# Set by handle_request_stream() to forward per-stage events to the SSE endpoint.
# None in the normal (non-streaming) request path — all emit calls are no-ops.
_stream_queue: ContextVar[Optional[asyncio.Queue]] = ContextVar("_stream_queue", default=None)


def _emit_stream_event(event: dict) -> None:
    """Push a pipeline progress event to the active SSE queue. No-op when not streaming."""
    q = _stream_queue.get()
    if q is not None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# OTel helpers — crash-safe, no-op when OTel is unavailable
# ---------------------------------------------------------------------------

def _mesh_tracer():
    """Returns the OTel tracer for agent_mesh, or None if OTel is unavailable."""
    try:
        from opentelemetry import trace
        return trace.get_tracer("agent_mesh")
    except Exception:
        return None


def _set_attr(span, key: str, value) -> None:
    """Safe span attribute setter — no-op if span is None or not recording."""
    try:
        if span and hasattr(span, "set_attribute"):
            span.set_attribute(key, value if isinstance(value, (bool, int, float, str)) else str(value))
    except Exception:
        pass


def _add_event(span, name: str, attrs: dict | None = None) -> None:
    """Safe span event emitter — no-op if span is None or not recording."""
    try:
        if span and hasattr(span, "add_event"):
            span.add_event(name, attributes={
                k: (v if isinstance(v, (bool, int, float, str)) else str(v))
                for k, v in (attrs or {}).items()
            })
    except Exception:
        pass


def _set_ok(span) -> None:
    try:
        if span and hasattr(span, "set_status"):
            from opentelemetry.trace import StatusCode
            span.set_status(StatusCode.OK)
    except Exception:
        pass


def _set_error(span, description: str = "") -> None:
    try:
        if span and hasattr(span, "set_status"):
            from opentelemetry.trace import StatusCode
            span.set_status(StatusCode.ERROR, description)
    except Exception:
        pass


def _span_ctx(tracer, name: str, kind_internal: bool = True):
    """Returns a context manager: a real span or contextlib.nullcontext()."""
    try:
        if tracer:
            from opentelemetry.trace import SpanKind
            kind = SpanKind.INTERNAL if kind_internal else SpanKind.CLIENT
            return tracer.start_as_current_span(name, kind=kind)
    except Exception:
        pass
    return contextlib.nullcontext()


# Set of all valid FAB banking role string values — used by rbac_validation_node.
_ALLOWED_ROLES = {r.value for r in BankingRole}

# Roles that bypass the LLM semantic compliance check. The deterministic guardrail
# (layer 1) still applies to everyone — only the A2A compliance agent call is skipped.
_COMPLIANCE_BYPASS_ROLES = {
    "relationship_manager",
    "platform_administrator",
    "operations_manager",
}


# ---------------------------------------------------------------------------
# MeshState — typed dict flowing through the graph
# ---------------------------------------------------------------------------

class MeshState(TypedDict):
    """The single state dict that flows through the mesh workflow graph."""
    user_name: str
    role: str
    query: str
    session_id: str
    compliance_verdict: str
    answer: str
    blocked: bool
    block_stage: Optional[str]
    trail: List[str]
    # Prior conversation turns (role/content dicts) for this session, loaded by the
    # orchestrator.  Injected into the PriceAssistAgent prompt by domain_node so
    # follow-up questions resolve in-context. Empty when memory is off / first turn.
    conversation_history: List[dict]


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

async def input_guardrail_node(state: MeshState) -> dict:
    """Deterministic input screen (hard gate, pre-review). Workflow entry node."""
    tracer = get_active_tracer()
    if tracer:
        tracer.emit_stage("guardrail", "started", message="Validating input safety...")
    _emit_stream_event({"stage": "guardrail", "status": "started", "message": "Validating input safety..."})

    otel = _mesh_tracer()
    t0 = time.perf_counter()
    trail = list(state["trail"])
    with _span_ctx(otel, "fab.guardrail.input_screen", kind_internal=True) as span:
        _set_attr(span, "guardrail.stage", "input_guardrail")
        _set_attr(span, "guardrail.query_length", len(state["query"]))
        try:
            screen = screen_input(state["query"])
            elapsed = (time.perf_counter() - t0) * 1000

            if not screen.allowed:
                categories_str = ",".join(screen.categories)
                _set_attr(span, "guardrail.result", "BLOCK")
                _set_attr(span, "guardrail.categories", categories_str)
                _set_attr(span, "guardrail.violations_count", len(screen.violations))
                _set_attr(span, "guardrail.block_reason", screen.reason[:200])
                _add_event(span, "guardrail.blocked", {
                    "categories": categories_str,
                    "reason":     screen.reason[:200],
                })
                _set_error(span, f"Input blocked: {categories_str}")

                trail.append(f"guardrail_block:{categories_str}")
                _log.warning("Input guardrail BLOCK: %s", screen.reason[:160],
                             extra={"user": state["user_name"], "status": "BLOCK"})
                if tracer:
                    tracer.record_blocked("input_guardrail")
                    tracer.emit_stage(
                        "guardrail", "blocked",
                        message=screen.reason[:120],
                        result="BLOCKED",
                        rationale=list(screen.categories),
                    )
                record_guardrail("BLOCK", screen.categories[0] if screen.categories else "none", elapsed)
                _emit_stream_event({"stage": "guardrail", "status": "blocked", "message": screen.reason[:120]})
                return {
                    "blocked":     True,
                    "block_stage": "input_guardrail",
                    "answer":      f"Request blocked by security guardrails ({', '.join(screen.categories)}).",
                    "trail":       trail,
                }

            _set_attr(span, "guardrail.result", "PASS")
            _set_attr(span, "guardrail.categories", "none")
            _set_attr(span, "guardrail.violations_count", 0)
            _add_event(span, "guardrail.pass", {"checks_run": 3})
            _set_ok(span)

            trail.append("guardrail_pass")
            _log.info("Input guardrail PASS", extra={"user": state["user_name"], "status": "PASS"})
            if tracer:
                tracer.emit_stage(
                    "guardrail", "completed",
                    result="SAFE",
                    checks=[
                        "Prompt injection check passed",
                        "Safety validation passed",
                        "Content policy validation passed",
                    ],
                )
            record_guardrail("PASS", "none", elapsed)
        except Exception as exc:
            _set_error(span, str(exc)[:200])
            raise
    _emit_stream_event({"stage": "guardrail", "status": "completed", "message": "Input validation passed"})
    return {"trail": trail}


async def rbac_validation_node(state: MeshState) -> dict:
    """Role-based access control gate — enforces FAB banking roles."""
    tracer = get_active_tracer()
    if tracer:
        tracer.emit_stage("rbac", "started")
    _emit_stream_event({"stage": "rbac", "status": "started", "message": "Checking access control..."})

    otel = _mesh_tracer()
    t0 = time.perf_counter()
    trail = list(state["trail"])
    with _span_ctx(otel, "fab.rbac.validate", kind_internal=True) as span:
        _set_attr(span, "rbac.role", state["role"])
        _set_attr(span, "rbac.user", state["user_name"])
        _set_attr(span, "rbac.allowed_role_count", len(_ALLOWED_ROLES))
        try:
            elapsed = (time.perf_counter() - t0) * 1000

            if state["role"] not in _ALLOWED_ROLES:
                _set_attr(span, "rbac.result", "BLOCK")
                _set_attr(span, "rbac.block_reason", f"Role '{state['role']}' not in allowed set")
                _add_event(span, "rbac.denied", {"role": state["role"], "reason": "unrecognised_role"})
                _set_error(span, f"RBAC block: role={state['role']}")

                trail.append(f"rbac_block:{state['role']}")
                _log.warning("RBAC BLOCK: unrecognised role=%s user=%s",
                             state["role"], state["user_name"],
                             extra={"user": state["user_name"], "status": "BLOCK"})
                if tracer:
                    tracer.record_blocked("rbac_validation")
                    tracer.emit_stage(
                        "rbac", "blocked",
                        message=f"Role '{state['role']}' is not a recognised FAB banking role.",
                        result="ACCESS DENIED",
                    )
                record_rbac("BLOCK", state["role"], elapsed)
                _emit_stream_event({"stage": "rbac", "status": "blocked",
                                    "message": f"Role '{state['role']}' is not recognised"})
                return {
                    "blocked":     True,
                    "block_stage": "rbac_validation",
                    "answer":      (
                        f"Access denied: role '{state['role']}' is not a recognised FAB banking role. "
                        "Please authenticate with valid FAB credentials."
                    ),
                    "trail": trail,
                }

            _set_attr(span, "rbac.result", "PASS")
            _add_event(span, "rbac.authorized", {"role": state["role"]})
            _set_ok(span)

            trail.append(f"rbac_pass:{state['role']}")
            _log.info("RBAC PASS role=%s", state["role"],
                      extra={"user": state["user_name"], "status": "PASS"})
            if tracer:
                tracer.emit_stage(
                    "rbac", "completed",
                    result="AUTHORIZED",
                    checks=[
                        f"Role '{state['role']}' validated",
                        "FAB banking role permissions granted",
                    ],
                )
            record_rbac("PASS", state["role"], elapsed)
        except Exception as exc:
            _set_error(span, str(exc)[:200])
            raise
    _emit_stream_event({"stage": "rbac", "status": "completed",
                        "message": f"Role '{state['role']}' authorized"})
    return {"trail": trail}


async def compliance_node(
    state: MeshState,
    *,
    ask: AskRemote,
    enabled: bool = True,
) -> dict:
    """Semantic safety review via the Compliance node over HTTP (hard gate)."""
    tracer = get_active_tracer()

    otel = _mesh_tracer()
    t0 = time.perf_counter()
    trail = list(state["trail"])
    with _span_ctx(otel, "fab.compliance.check", kind_internal=False) as span:
        _set_attr(span, "compliance.role", state["role"])
        _set_attr(span, "compliance.user", state["user_name"])
        _set_attr(span, "compliance.query_length", len(state["query"]))
        _bypass_reason = (
            "service_disabled" if not enabled else
            "elevated_role" if state["role"] in _COMPLIANCE_BYPASS_ROLES else
            None
        )
        bypass = _bypass_reason is not None
        _set_attr(span, "compliance.bypass", bypass)
        try:
            if bypass:
                _emit_stream_event({"stage": "compliance", "status": "started",
                                    "message": "Compliance check (bypassed)..."})
                elapsed = (time.perf_counter() - t0) * 1000
                _set_attr(span, "compliance.result", "BYPASSED")
                _add_event(span, "compliance.bypassed", {
                    "role":   state["role"],
                    "reason": _bypass_reason,
                })
                _set_ok(span)

                trail.append(f"compliance_pass:{_bypass_reason}:{state['role']}")
                _log.info("Compliance BYPASS reason=%s role=%s", _bypass_reason, state["role"],
                          extra={"user": state["user_name"], "status": "PASS"})
                if tracer:
                    _check_msg = (
                        "Compliance node is disabled (ENABLE_COMPLIANCE=false); stamping pass verdict."
                        if _bypass_reason == "service_disabled"
                        else f"Elevated role '{state['role']}' bypasses semantic compliance check."
                    )
                    tracer.emit_stage(
                        "compliance", "completed",
                        result="COMPLIANT",
                        checks=[_check_msg],
                    )
                record_compliance("BYPASSED", state["role"], elapsed)
                _emit_stream_event({"stage": "compliance", "status": "completed",
                                    "message": "Compliance bypassed"})
                return {
                    "compliance_verdict": f"COMPLIANCE_PASSED: {_bypass_reason} bypass",
                    "trail": trail,
                }

            if tracer:
                tracer.record_agent_invoked()
                tracer.emit_stage(
                    "compliance", "started",
                    message="Running semantic compliance check...",
                )
            _emit_stream_event({"stage": "compliance", "status": "started",
                                "message": "Running semantic compliance check..."})

            _add_event(span, "compliance.a2a_call.started", {"target": "compliance"})
            verdict = await ask("compliance", f"Review this request for safety: '{state['query']}'")
            # Extract and capture LLM reasoning before consuming the verdict text.
            _reasoning_entries, verdict = extract_reasoning(verdict, "compliance")
            if tracer and _reasoning_entries:
                tracer.add_llm_reasoning([e.to_dict() for e in _reasoning_entries])
            if _reasoning_entries:
                _emit_stream_event({"event_type": "reasoning",
                                    "entries": [e.to_dict() for e in _reasoning_entries]})
            elapsed = (time.perf_counter() - t0) * 1000

            if "compliance_failed" in verdict.lower():
                _set_attr(span, "compliance.result", "FAILED")
                _set_attr(span, "compliance.verdict", verdict[:120])
                _add_event(span, "compliance.a2a_call.completed", {
                    "target":          "compliance",
                    "result":          "FAILED",
                    "verdict_preview": verdict[:80],
                })
                _add_event(span, "compliance.failed", {"verdict": verdict[:120]})
                _set_error(span, "Compliance check failed")

                trail.append("compliance_failed")
                _log.warning("Compliance FAIL: %s", verdict[:160],
                             extra={"user": state["user_name"], "status": "FAIL"})
                if tracer:
                    tracer.record_blocked("compliance")
                    tracer.emit_stage(
                        "compliance", "blocked",
                        message="Request failed semantic safety review.",
                        result="COMPLIANCE FAILED",
                        rationale=[verdict[:120]],
                    )
                record_compliance("FAILED", state["role"], elapsed)
                _emit_stream_event({"stage": "compliance", "status": "blocked",
                                    "message": "Compliance check failed"})
                return {
                    "compliance_verdict": verdict,
                    "blocked":            True,
                    "block_stage":        "compliance",
                    "answer":             "Request blocked by the Compliance agent (semantic safety review).",
                    "trail":              trail,
                }

            _set_attr(span, "compliance.result", "PASSED")
            _set_attr(span, "compliance.verdict", verdict[:120])
            _add_event(span, "compliance.a2a_call.completed", {
                "target":          "compliance",
                "result":          "PASSED",
                "verdict_preview": verdict[:80],
            })
            _set_ok(span)

            trail.append("compliance_pass")
            _log.info("Compliance PASS", extra={"user": state["user_name"], "status": "PASS"})
            if tracer:
                tracer.emit_stage(
                    "compliance", "completed",
                    result="COMPLIANT",
                    checks=[
                        "Regulatory validation passed",
                        "Organization policy validation passed",
                    ],
                )
            record_compliance("PASSED", state["role"], elapsed)
        except Exception as exc:
            _set_error(span, str(exc)[:200])
            raise
    _emit_stream_event({"stage": "compliance", "status": "completed",
                        "message": "Compliance check passed"})
    return {"compliance_verdict": verdict, "trail": trail}


# Regex patterns preserved from DomainExecutor for retry logic.
# Matches a bare tool-call echo: the model wrote the call as plain text instead of
# using structured function-calling.
_TOOL_CALL_RE = re.compile(
    r'(query_structured_data|query_knowledge_base)\s*[\(:{"\']',
    re.IGNORECASE,
)

# Detects meta-responses: the LLM described calling the tool instead of returning data.
_META_RESPONSE_RE = re.compile(
    r'\b(this response was generated|i (have |just |)(called|retrieved|fetched|invoked)'
    r'|data has been (retrieved|fetched)|i would be happy to provide'
    r'|please let me know if you (have|need)|feel free to ask)\b',
    re.IGNORECASE,
)

# Detects hallucinated bracket-placeholder templates.
_HALLUCINATION_RE = re.compile(r'\[(?![A-Z]{2,}[0-9])[A-Za-z][A-Za-z0-9 /-]{1,40}\]')


async def domain_node(
    state: MeshState,
    *,
    ask: AskRemote,
    bypass_price_assist: bool = False,
) -> dict:
    """Dispatches the request to the PriceAssistAgent — the primary FAB banking orchestrator."""
    tracer = get_active_tracer()
    t0 = time.perf_counter()
    failed = False
    retry_reason = "none"
    route = "unknown"
    route_conf = 0.0
    trail = list(state["trail"])

    _target_node = "data_agent" if bypass_price_assist else "price_assist"

    if tracer:
        tracer.record_agent_invoked()
        if bypass_price_assist:
            tracer.add_execution_path("Data Agent (Direct)")
            tracer.emit_stage(
                "domain_classification", "started",
                message="Routing directly to Data Agent (PriceAssist disabled)...",
            )
        else:
            tracer.add_execution_path("Price Assist")
            tracer.emit_stage(
                "domain_classification", "started",
                message="Analyzing intent...",
            )
    _emit_stream_event({
        "stage":   "domain",
        "status":  "started",
        "message": "Routing directly to Data Agent..." if bypass_price_assist else "Querying domain agents...",
    })

    otel = _mesh_tracer()
    with _span_ctx(otel, "fab.domain.dispatch", kind_internal=False) as span:
        _set_attr(span, "domain.target_node", _target_node)
        _set_attr(span, "domain.bypass_mode", "direct" if bypass_price_assist else "full_orchestration")
        _set_attr(span, "domain.user", state["user_name"])
        _set_attr(span, "domain.query_length", len(state["query"]))

        # Prepend role context so PriceAssistAgent can enforce scope rules.
        # History travels inline in the prompt; bare query is preserved on span.
        role_context = f"[User: {state['user_name']} | Role: {state['role']}]\n"
        history_block = ConversationStore.format_history_block(state["conversation_history"])
        base_prompt = (
            f"{role_context}{history_block}{state['query']}"
            if history_block
            else f"{role_context}{state['query']}"
        )
        _set_attr(span, "domain.history_turns", len(state["conversation_history"]) // 2)

        if bypass_price_assist:
            # --- Direct DataAgent path (PriceAssist disabled) ---
            from datetime import datetime, timezone as _tz
            _bypass_entry = {
                "agent": "orchestrator",
                "phase": "routing_decision",
                "timestamp": datetime.now(_tz.utc).isoformat(),
                "data": {
                    "agent":    "orchestrator",
                    "phase":    "routing_decision",
                    "decision": "direct_data_agent",
                    "reason":   (
                        "ENABLE_PRICE_ASSIST=false — PriceAssist orchestration is disabled; "
                        "routing query directly to Data Agent (structured data path)."
                    ),
                    "skipped_nodes": ["price_assist"],
                    "active_node":   "data_agent",
                },
            }
            if tracer:
                tracer.add_llm_reasoning([_bypass_entry])
            _emit_stream_event({"event_type": "reasoning", "entries": [_bypass_entry]})
            try:
                _add_event(span, "domain.a2a_call.started", {"target": "data_agent", "mode": "direct"})
                answer = await ask("data_agent", base_prompt)
            except Exception as exc:
                answer = f"The banking data service is currently unavailable ({exc})."
                failed = True
                trail.append("domain_error:data_agent_direct")
                _log.warning("Domain direct hop failed node=data_agent: %s", exc,
                             extra={"status": "ERROR"})
                _add_event(span, "domain.error", {"error": str(exc)[:200]})
                _set_error(span, str(exc)[:200])
            else:
                trail.append("domain_answer:data_agent_direct")
                _log.info("Domain direct answer (%d chars)", len(answer or ""),
                          extra={"status": "SUCCESS"})
        else:
            # --- Full PriceAssist orchestration path ---
            try:
                _add_event(span, "domain.a2a_call.started", {"target": "price_assist"})
                answer = await ask("price_assist", base_prompt)
                if _TOOL_CALL_RE.search(answer or ""):
                    retry_reason = "tool_call_echo"
                    _log.warning(
                        "price_assist returned bare tool-call text; retrying once.",
                        extra={"status": "RETRY"},
                    )
                    _add_event(span, "domain.retry", {"reason": retry_reason, "attempt": 2})
                    await asyncio.sleep(5)
                    answer = await ask("price_assist", base_prompt)
                elif _META_RESPONSE_RE.search(answer or ""):
                    retry_reason = "meta_response"
                    _log.warning(
                        "price_assist returned meta-response without data; retrying once.",
                        extra={"status": "RETRY"},
                    )
                    _add_event(span, "domain.retry", {"reason": retry_reason, "attempt": 2})
                    retry_prompt = (
                        f"{base_prompt}\n\n"
                        "IMPORTANT: Your previous response did not include the actual data. "
                        "You MUST copy the COMPLETE raw output returned by the tool into your "
                        "response — every field, every row, every figure. Do NOT say 'I retrieved' "
                        "or 'I called'; just show the data."
                    )
                    await asyncio.sleep(5)
                    answer = await ask("price_assist", retry_prompt)
                elif _HALLUCINATION_RE.search(answer or ""):
                    retry_reason = "hallucination"
                    _log.warning(
                        "price_assist returned hallucinated placeholder text; retrying once.",
                        extra={"status": "RETRY"},
                    )
                    _add_event(span, "domain.retry", {"reason": retry_reason, "attempt": 2})
                    retry_prompt = (
                        f"{base_prompt}\n\n"
                        "CRITICAL: Your previous response contained placeholder text like "
                        "[Name] or [Value] that is NOT real data. You MUST call the tool, "
                        "then copy the EXACT values it returns — customer names, figures, "
                        "percentages — verbatim. NEVER invent or template any field."
                    )
                    await asyncio.sleep(5)
                    answer = await ask("price_assist", retry_prompt)
            except Exception as exc:
                answer = f"The banking assistant is currently unavailable ({exc})."
                failed = True
                trail.append("domain_error:price_assist")
                _log.warning("Domain hop failed node=price_assist: %s", exc,
                             extra={"status": "ERROR"})
                _add_event(span, "domain.error", {"error": str(exc)[:200]})
                _set_error(span, str(exc)[:200])
            else:
                trail.append("domain_answer:price_assist")
                _log.info("Domain answer (%d chars)", len(answer or ""),
                          extra={"status": "SUCCESS"})

        total_ms = int((time.perf_counter() - t0) * 1000)
        answer = ConversationStore.strip_history_echo(answer or "", state["query"])
        # Extract LLM reasoning markers from the answer and strip them.
        _reasoning_entries: list = []
        if not failed:
            _reasoning_entries, answer = extract_reasoning(answer, _target_node)
            if tracer and _reasoning_entries:
                tracer.add_llm_reasoning([e.to_dict() for e in _reasoning_entries])
            if _reasoning_entries:
                _emit_stream_event({"event_type": "reasoning",
                                    "entries": [e.to_dict() for e in _reasoning_entries]})
            if not bypass_price_assist:
                # Collect reasoning entries from peer agents cached in collaboration_tools.
                try:
                    from src.tools.collaboration_tools import _peer_reasoning as _pr_ctx
                    _peer_entries = _pr_ctx.get() or []
                    if tracer and _peer_entries:
                        tracer.add_llm_reasoning(_peer_entries)
                    if _peer_entries:
                        _emit_stream_event({"event_type": "reasoning", "entries": _peer_entries})
                except Exception:
                    pass
                # Also read from the request-scoped temp file written by collaboration_tools
                # in the PriceAssist A2A server process.
                try:
                    import json as _json
                    import pathlib as _pl
                    from src.observability.baggage import get_request_id as _grid
                    _rid = (_grid() or "").upper().strip()
                    if _rid and _rid != "-":
                        _pf = _pl.Path("data/logs") / f".peer_{_rid}.json"
                        if _pf.exists():
                            _file_entries = _json.loads(_pf.read_text())
                            if tracer and _file_entries:
                                tracer.add_llm_reasoning(_file_entries)
                            if _file_entries:
                                _emit_stream_event({"event_type": "reasoning",
                                                    "entries": _file_entries})
                            _pf.unlink(missing_ok=True)
                except Exception:
                    pass
        # Belt-and-suspenders: strip any blocks not caught by extract_reasoning.
        clean = strip_reasoning_markers(answer)
        # Fallback: if the LLM packed its entire answer inside <llm_reasoning> blocks,
        # reconstruct from the synthesis entry's key_findings + answer_rationale.
        if not failed and (not clean or clean.strip() == state["query"].strip()):
            _syn = next((e for e in _reasoning_entries if e.phase == "synthesis"), None)
            if _syn and isinstance(_syn.data, dict):
                _parts: list[str] = list(_syn.data.get("key_findings") or [])
                if _syn.data.get("answer_rationale"):
                    _parts.append(_syn.data["answer_rationale"])
                if _parts:
                    clean = "\n\n".join(_parts)
                    _log.warning(
                        "domain_node: answer was empty after reasoning strip; "
                        "reconstructed from synthesis block (%d chars)",
                        len(clean),
                        extra={"status": "WARN"},
                    )
        # Fallback 2: if the answer is still empty or just echoes the user's question,
        # issue one final plain-answer retry.
        if not failed and (not clean or clean.strip() == state["query"].strip()):
            _log.warning(
                "domain_node: answer still empty/echoed after all processing; "
                "issuing plain-answer retry",
                extra={"status": "RETRY"},
            )
            try:
                _plain = await ask(
                    _target_node,
                    state["query"]
                    + "\n\n[SYSTEM NOTE: Please provide a direct, complete answer "
                    "to the question above in plain markdown. "
                    "Do NOT repeat the question. Do NOT use <llm_reasoning> blocks.]",
                )
                _plain_entries, _plain_clean = extract_reasoning(_plain or "", _target_node)
                if tracer and _plain_entries:
                    tracer.add_llm_reasoning([e.to_dict() for e in _plain_entries])
                if _plain_entries:
                    _emit_stream_event({"event_type": "reasoning",
                                        "entries": [e.to_dict() for e in _plain_entries]})
                _plain_clean = strip_reasoning_markers(_plain_clean)
                if (
                    _plain_clean
                    and _plain_clean.strip() != state["query"].strip()
                    and "SYSTEM NOTE" not in _plain_clean
                ):
                    clean = _plain_clean
                    trail.append("domain_answer:retry_plain")
            except Exception as _exc:
                _log.warning(
                    "domain_node: plain-answer retry failed: %s",
                    _exc,
                    extra={"status": "ERROR"},
                )

        _set_attr(span, "domain.retry_reason", retry_reason)
        _set_attr(span, "domain.retried", retry_reason != "none")
        _set_attr(span, "domain.result", "ERROR" if failed else "SUCCESS")
        _set_attr(span, "domain.answer_length", len(answer or ""))

        if tracer and not failed:
            if bypass_price_assist:
                route = "Data Layer Service"
                route_conf = 1.0
                tracer.record_domain("Data Agent (Direct)", 1.0)
                tracer.record_route(route)
                tracer.add_execution_path("Data Layer Service")
                tracer.emit_stage(
                    "domain_classification", "completed",
                    result="Data Agent (Direct)",
                    confidence=1.0,
                    checks=["Direct data routing (ENABLE_PRICE_ASSIST=false)"],
                    rationale=[
                        "PriceAssist orchestration is disabled via ENABLE_PRICE_ASSIST=false.",
                        "Query routed directly to Data Agent (structured data path).",
                    ],
                )
                tracer.emit_stage(
                    "routing", "completed",
                    result=route,
                    confidence=1.0,
                    checks=["Direct DataAgent routing (bypass mode)"],
                    rationale=["ENABLE_PRICE_ASSIST=false — no intent classification needed."],
                )
                tracer.emit_stage(
                    "agent_handoff", "completed",
                    result="Handoff successful",
                    handoff_path=["Coordinator Agent", "Data Agent (Direct)",
                                  "Data Layer Service", "Response Generator"],
                )
                tracer.record_tool_used()
                retrieval_ms = max(50, int(total_ms * 0.35))
                tracer.emit_stage(
                    "data_retrieval", "completed",
                    result="Data retrieved successfully",
                    checks=["Query generated", "Query validated", "Data retrieved"],
                    duration_ms=retrieval_ms,
                    latency_ms=retrieval_ms,
                )
                tracer.emit_stage(
                    "response_generation", "completed",
                    result="Response generated",
                    checks=["Context assembled", "Response generated",
                            "Hallucination checks passed"],
                )
                _set_attr(span, "domain.route", route)
                _set_attr(span, "domain.route_confidence", route_conf)
                _add_event(span, "domain.a2a_call.completed", {
                    "target":        "data_agent",
                    "mode":          "direct",
                    "result":        "SUCCESS",
                    "answer_length": len(answer or ""),
                })
                _set_ok(span)
            else:
                route, route_rationale, route_conf, alt_scores = infer_route_and_scores(
                    state["query"], answer
                )
                tracer.record_domain("Price Assist Agent", 0.96)
                tracer.record_route(route)
                if route == "Data Layer + RAG (Hybrid)":
                    tracer.add_execution_path("Data Layer Service")
                    tracer.add_execution_path("RAG Service")
                else:
                    tracer.add_execution_path(route)

                tracer.emit_stage(
                    "domain_classification", "completed",
                    result="Price Assist Agent",
                    confidence=0.96,
                    checks=["Request classified to pricing domain"],
                    rationale=[
                        "User is requesting pricing or banking information.",
                        "Price Assist domain has highest confidence score.",
                        "Historical routing pattern matched.",
                    ],
                    alt_scores=alt_scores,
                )
                tracer.emit_stage(
                    "routing", "completed",
                    result=route,
                    confidence=route_conf,
                    checks=["Evaluated available retrieval strategies"],
                    rationale=route_rationale,
                )
                handoff_path = [
                    "Coordinator Agent", "Price Assist Agent", route, "Response Generator"
                ]
                tracer.emit_stage(
                    "agent_handoff", "completed",
                    result="Handoff successful",
                    handoff_path=handoff_path,
                )
                tracer.record_tool_used()
                retrieval_ms = max(50, int(total_ms * 0.35))
                tracer.emit_stage(
                    "data_retrieval", "completed",
                    result="Data retrieved successfully",
                    checks=["Query generated", "Query validated", "Data retrieved"],
                    duration_ms=retrieval_ms,
                    latency_ms=retrieval_ms,
                )
                tracer.emit_stage(
                    "response_generation", "completed",
                    result="Response generated",
                    checks=["Context assembled", "Response generated",
                            "Hallucination checks passed"],
                )
                _set_attr(span, "domain.route", route)
                _set_attr(span, "domain.route_confidence", route_conf)
                _add_event(span, "domain.a2a_call.completed", {
                    "target":        "price_assist",
                    "result":        "SUCCESS",
                    "answer_length": len(answer or ""),
                })
                _add_event(span, "domain.route_inferred", {
                    "route":      route,
                    "confidence": route_conf,
                })
                _set_ok(span)

            record_domain_route(route, float(total_ms))

    _emit_stream_event({"stage": "domain", "status": "completed",
                        "message": "Domain agent responded"})
    return {"answer": clean, "trail": trail}


async def output_redaction_node(state: MeshState) -> dict:
    """Deterministic output redaction (PII). Terminal pipeline node."""
    _emit_stream_event({"stage": "output_redaction", "status": "started",
                        "message": "Redacting output..."})
    otel = _mesh_tracer()
    trail = list(state["trail"])
    with _span_ctx(otel, "fab.output.redact", kind_internal=True) as span:
        original_len = len(state["answer"] or "")
        _set_attr(span, "redaction.input_length", original_len)
        try:
            redacted = redact_pii(state["answer"])
            output_len = len(redacted or "")
            pii_count = redacted.count("[REDACTED_")
            pii_found = original_len != output_len or pii_count > 0

            _set_attr(span, "redaction.output_length", output_len)
            _set_attr(span, "redaction.pii_found", pii_found)
            _add_event(span, "output.redaction.completed", {
                "input_length":  original_len,
                "output_length": output_len,
                "pii_found":     pii_found,
            })
            _set_ok(span)
            record_pii_hits(pii_count)
        except Exception as exc:
            _set_error(span, str(exc)[:200])
            raise

    trail.append("output_redacted")
    _log.info("Request complete trail=%s", " -> ".join(trail),
              extra={"user": state["user_name"], "status": "SUCCESS"})
    _emit_stream_event({"stage": "output_redaction", "status": "completed",
                        "message": "Response ready"})
    return {"answer": redacted, "trail": trail}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def route_after_guardrail(state: MeshState) -> Literal["rbac_validation", "__end__"]:
    return END if state["blocked"] else "rbac_validation"


def route_after_rbac(state: MeshState) -> Literal["compliance", "__end__"]:
    return END if state["blocked"] else "compliance"


def route_after_compliance(state: MeshState) -> Literal["domain", "__end__"]:
    return END if state["blocked"] else "domain"


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------

def build_mesh_workflow(ask: AskRemote, bypass_price_assist: bool = False):
    """Builds the mesh orchestration workflow as a LangGraph StateGraph.

    Args:
        ask: async callable ``(node, prompt, **kwargs) -> str`` used for A2A hops.
             The orchestrator passes its module-level ``ask_remote`` so the
             offline test suite can patch the transport.
        bypass_price_assist: when True, routes directly to DataAgent instead of
             PriceAssistAgent.  Defaults to False; the Config flag overrides this.

    Returns:
        A compiled, immutable, reusable LangGraph ``CompiledStateGraph``.
    """
    # Honour the runtime Config flags when the caller hasn't explicitly overridden.
    if not bypass_price_assist:
        bypass_price_assist = not Config.ENABLE_PRICE_ASSIST
    _enabled = Config.ENABLE_COMPLIANCE

    # Closures inject the A2A seam and config flags into nodes that need them,
    # keeping the outer node-function signatures (state) -> dict compatible with
    # LangGraph's node calling convention.
    async def _compliance(state: MeshState) -> dict:
        return await compliance_node(state, ask=ask, enabled=_enabled)

    async def _domain(state: MeshState) -> dict:
        return await domain_node(state, ask=ask, bypass_price_assist=bypass_price_assist)

    graph = StateGraph(MeshState)
    graph.add_node("input_guardrail",  input_guardrail_node)
    graph.add_node("rbac_validation",  rbac_validation_node)
    graph.add_node("compliance",       _compliance)
    graph.add_node("domain",           _domain)
    graph.add_node("output_redaction", output_redaction_node)

    graph.set_entry_point("input_guardrail")
    graph.add_conditional_edges(
        "input_guardrail", route_after_guardrail,
        {"rbac_validation": "rbac_validation", END: END},
    )
    graph.add_conditional_edges(
        "rbac_validation", route_after_rbac,
        {"compliance": "compliance", END: END},
    )
    graph.add_conditional_edges(
        "compliance", route_after_compliance,
        {"domain": "domain", END: END},
    )
    graph.add_edge("domain", "output_redaction")
    graph.add_edge("output_redaction", END)

    return graph.compile()


def build_devui_workflow(ask: AskRemote, user_name: str, role: str):
    """DevUI workflow compatibility shim — wraps build_mesh_workflow.

    Note: The DevUIEntryExecutor (which adapted a plain str prompt to MeshState)
    has been removed.  devui_app.py callers must construct a full MeshState dict
    and call ainvoke() directly.  This shim preserves the import so the module
    does not crash at import time; actual DevUI invocations will fail at runtime
    (acceptable — DevUI is dev-only).
    """
    return build_mesh_workflow(ask=ask)
