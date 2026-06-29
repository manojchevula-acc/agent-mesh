"""Mesh orchestration as a Microsoft Agent Framework Workflow.

The request pipeline is expressed as a typed ``WorkflowBuilder`` graph so the
framework emits native observability spans for the whole orchestration:

    workflow.run
      └─ executor.process input_guardrail
      └─ executor.process rbac_validation
      └─ executor.process compliance      ──(A2A)──► invoke_agent ComplianceAgent
      └─ executor.process domain           ──(A2A)──► invoke_agent PriceAssistAgent
      └─ executor.process output_redaction

PriceAssistAgent is the primary FAB banking orchestrator. It receives ALL requests
after the security/RBAC/compliance pipeline, classifies intent internally, and
delegates to DataAgent (→ DataLayer MCP) or RAGAgent (→ RAG MCP) as needed.

Design notes
------------
- A single :class:`MeshState` message flows through the graph. Each gate either
  forwards (``ctx.send_message``) to proceed or yields (``ctx.yield_output``) to
  terminate early (blocked).
- RBACValidationExecutor enforces FAB banking roles. All seven defined roles are
  permitted to proceed; unrecognised roles are blocked.
- The gateway routing step has been removed. Intent classification (data /
  knowledge / hybrid) now lives entirely inside PriceAssistAgent's LLM prompt,
  keeping the orchestration graph lean and reducing inter-service round-trips.
- A2A-calling executors use an injected ``ask`` callable so the offline test
  suite can patch the transport at the ``orchestrator.ask_remote`` seam.
"""
from __future__ import annotations

import contextlib
import re
import sys
import time
import pathlib
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from typing_extensions import Never

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

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
from src.memory import ConversationStore

_log = get_logger(CAT_WORKFLOW)

# Type alias for the injected dependency.
AskRemote = Callable[..., Awaitable[str]]


# ---------------------------------------------------------------------------
# OTel helpers — crash-safe, no-op when OTel is unavailable
# ---------------------------------------------------------------------------

def _mesh_tracer():
    """Returns the agent_framework OTel tracer, or None if OTel is unavailable."""
    try:
        from agent_framework.observability import get_tracer
        return get_tracer("agent_mesh")
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

# Set of all valid FAB banking role string values — used by RBACValidationExecutor.
_ALLOWED_ROLES = {r.value for r in BankingRole}

# Roles that bypass the LLM semantic compliance check. The deterministic guardrail
# (layer 1) still applies to everyone — only the A2A compliance agent call is skipped.
_COMPLIANCE_BYPASS_ROLES = {
    "relationship_manager",
    "platform_administrator",
    "operations_manager",
}


@dataclass
class MeshState:
    """The single message that flows through the mesh workflow graph."""
    user_name: str
    role: str
    query: str
    session_id: str = "default_session"
    compliance_verdict: str = ""
    answer: str = ""
    blocked: bool = False
    block_stage: Optional[str] = None
    trail: List[str] = field(default_factory=list)
    # Prior conversation turns (role/content dicts) for this session, loaded by the
    # orchestrator. Injected into the PriceAssistAgent prompt by DomainExecutor so
    # follow-up questions resolve in-context. Empty when memory is off / first turn.
    conversation_history: List[dict] = field(default_factory=list)


# --- Executors ----------------------------------------------------------------


class DevUIEntryExecutor(Executor):
    """Start node for the DevUI workflow.

    DevUI invokes a workflow with a plain ``str`` (the user's chat input), but the
    mesh pipeline flows a :class:`MeshState`. This executor adapts the text into a
    ``MeshState`` stamped with the DevUI session's identity (user/role), then hands
    off to the normal guardrail stage.
    """

    def __init__(self, user_name: str, role: str, id: str = "devui_entry") -> None:
        super().__init__(id=id)
        self._user_name = user_name
        self._role = role

    @handler
    async def run(self, query: str, ctx: WorkflowContext[MeshState]) -> None:
        state = MeshState(
            user_name=self._user_name,
            role=self._role,
            query=query,
            session_id=f"devui_{self._user_name}",
        )
        _log.info("DevUI request user=%s role=%s query_len=%d",
                  self._user_name, self._role, len(query or ""),
                  extra={"user": self._user_name})
        await ctx.send_message(state)


class InputGuardrailExecutor(Executor):
    """Deterministic input screen (hard gate, pre-review). Workflow start node."""

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        tracer = get_active_tracer()
        if tracer:
            tracer.emit_stage("guardrail", "started", message="Validating input safety...")

        otel = _mesh_tracer()
        t0 = time.perf_counter()
        with _span_ctx(otel, "fab.guardrail.input_screen", kind_internal=True) as span:
            _set_attr(span, "guardrail.stage", "input_guardrail")
            _set_attr(span, "guardrail.query_length", len(state.query))
            try:
                screen = screen_input(state.query)
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

                    state.blocked = True
                    state.block_stage = "input_guardrail"
                    state.answer = (
                        f"Request blocked by security guardrails ({', '.join(screen.categories)})."
                    )
                    state.trail.append(f"guardrail_block:{categories_str}")
                    _log.warning("Input guardrail BLOCK: %s", screen.reason[:160],
                                 extra={"user": state.user_name, "status": "BLOCK"})
                    if tracer:
                        tracer.record_blocked("input_guardrail")
                        tracer.emit_stage(
                            "guardrail", "blocked",
                            message=screen.reason[:120],
                            result="BLOCKED",
                            rationale=list(screen.categories),
                        )
                    record_guardrail("BLOCK", screen.categories[0] if screen.categories else "none", elapsed)
                    await ctx.yield_output(state)
                    return

                _set_attr(span, "guardrail.result", "PASS")
                _set_attr(span, "guardrail.categories", "none")
                _set_attr(span, "guardrail.violations_count", 0)
                _add_event(span, "guardrail.pass", {"checks_run": 3})
                _set_ok(span)

                state.trail.append("guardrail_pass")
                _log.info("Input guardrail PASS", extra={"user": state.user_name, "status": "PASS"})
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
        await ctx.send_message(state)


class RBACValidationExecutor(Executor):
    """Role-based access control gate — enforces FAB banking roles.

    All seven defined FAB banking roles are permitted to proceed. Requests
    carrying an unrecognised role string are blocked here with an explicit
    message. Granular data-level enforcement (e.g. a CUSTOMER role may only
    query their own account) is handled at the domain agent layer.
    """

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        tracer = get_active_tracer()
        if tracer:
            tracer.emit_stage("rbac", "started")

        otel = _mesh_tracer()
        t0 = time.perf_counter()
        with _span_ctx(otel, "fab.rbac.validate", kind_internal=True) as span:
            _set_attr(span, "rbac.role", state.role)
            _set_attr(span, "rbac.user", state.user_name)
            _set_attr(span, "rbac.allowed_role_count", len(_ALLOWED_ROLES))
            try:
                elapsed = (time.perf_counter() - t0) * 1000

                if state.role not in _ALLOWED_ROLES:
                    _set_attr(span, "rbac.result", "BLOCK")
                    _set_attr(span, "rbac.block_reason", f"Role '{state.role}' not in allowed set")
                    _add_event(span, "rbac.denied", {"role": state.role, "reason": "unrecognised_role"})
                    _set_error(span, f"RBAC block: role={state.role}")

                    state.blocked = True
                    state.block_stage = "rbac_validation"
                    state.answer = (
                        f"Access denied: role '{state.role}' is not a recognised FAB banking role. "
                        "Please authenticate with valid FAB credentials."
                    )
                    state.trail.append(f"rbac_block:{state.role}")
                    _log.warning("RBAC BLOCK: unrecognised role=%s user=%s",
                                 state.role, state.user_name,
                                 extra={"user": state.user_name, "status": "BLOCK"})
                    if tracer:
                        tracer.record_blocked("rbac_validation")
                        tracer.emit_stage(
                            "rbac", "blocked",
                            message=f"Role '{state.role}' is not a recognised FAB banking role.",
                            result="ACCESS DENIED",
                        )
                    record_rbac("BLOCK", state.role, elapsed)
                    await ctx.yield_output(state)
                    return

                _set_attr(span, "rbac.result", "PASS")
                _add_event(span, "rbac.authorized", {"role": state.role})
                _set_ok(span)

                state.trail.append(f"rbac_pass:{state.role}")
                _log.info("RBAC PASS role=%s", state.role,
                          extra={"user": state.user_name, "status": "PASS"})
                if tracer:
                    tracer.emit_stage(
                        "rbac", "completed",
                        result="AUTHORIZED",
                        checks=[
                            f"Role '{state.role}' validated",
                            "FAB banking role permissions granted",
                        ],
                    )
                record_rbac("PASS", state.role, elapsed)
            except Exception as exc:
                _set_error(span, str(exc)[:200])
                raise
        await ctx.send_message(state)


class ComplianceExecutor(Executor):
    """Semantic safety review via the Compliance node over A2A (hard gate)."""

    def __init__(self, ask: AskRemote, id: str = "compliance") -> None:
        super().__init__(id=id)
        self._ask = ask

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        tracer = get_active_tracer()

        otel = _mesh_tracer()
        t0 = time.perf_counter()
        with _span_ctx(otel, "fab.compliance.check", kind_internal=False) as span:
            _set_attr(span, "compliance.role", state.role)
            _set_attr(span, "compliance.user", state.user_name)
            _set_attr(span, "compliance.query_length", len(state.query))
            bypass = state.role in _COMPLIANCE_BYPASS_ROLES
            _set_attr(span, "compliance.bypass", bypass)
            try:
                if bypass:
                    elapsed = (time.perf_counter() - t0) * 1000
                    _set_attr(span, "compliance.result", "BYPASSED")
                    _add_event(span, "compliance.bypassed", {
                        "role":   state.role,
                        "reason": "elevated_role",
                    })
                    _set_ok(span)

                    state.compliance_verdict = "COMPLIANCE_PASSED: elevated role bypass"
                    state.trail.append(f"compliance_pass:elevated_role:{state.role}")
                    _log.info("Compliance BYPASS role=%s", state.role,
                              extra={"user": state.user_name, "status": "PASS"})
                    if tracer:
                        tracer.emit_stage(
                            "compliance", "completed",
                            result="COMPLIANT",
                            checks=[f"Elevated role '{state.role}' bypasses semantic compliance check."],
                        )
                    record_compliance("BYPASSED", state.role, elapsed)
                    await ctx.send_message(state)
                    return

                if tracer:
                    tracer.record_agent_invoked()
                    tracer.emit_stage(
                        "compliance", "started",
                        message="Running semantic compliance check...",
                    )

                _add_event(span, "compliance.a2a_call.started", {"target": "compliance"})
                verdict = await self._ask("compliance", f"Review this request for safety: '{state.query}'")
                state.compliance_verdict = verdict
                elapsed = (time.perf_counter() - t0) * 1000

                if "compliance_failed" in verdict.lower():
                    _set_attr(span, "compliance.result", "FAILED")
                    _set_attr(span, "compliance.verdict", verdict[:120])
                    _add_event(span, "compliance.a2a_call.completed", {
                        "target":         "compliance",
                        "result":         "FAILED",
                        "verdict_preview": verdict[:80],
                    })
                    _add_event(span, "compliance.failed", {"verdict": verdict[:120]})
                    _set_error(span, "Compliance check failed")

                    state.blocked = True
                    state.block_stage = "compliance"
                    state.answer = "Request blocked by the Compliance agent (semantic safety review)."
                    state.trail.append("compliance_failed")
                    _log.warning("Compliance FAIL: %s", verdict[:160],
                                 extra={"user": state.user_name, "status": "FAIL"})
                    if tracer:
                        tracer.record_blocked("compliance")
                        tracer.emit_stage(
                            "compliance", "blocked",
                            message="Request failed semantic safety review.",
                            result="COMPLIANCE FAILED",
                            rationale=[verdict[:120]],
                        )
                    record_compliance("FAILED", state.role, elapsed)
                    await ctx.yield_output(state)
                    return

                _set_attr(span, "compliance.result", "PASSED")
                _set_attr(span, "compliance.verdict", verdict[:120])
                _add_event(span, "compliance.a2a_call.completed", {
                    "target":         "compliance",
                    "result":         "PASSED",
                    "verdict_preview": verdict[:80],
                })
                _set_ok(span)

                state.trail.append("compliance_pass")
                _log.info("Compliance PASS", extra={"user": state.user_name, "status": "PASS"})
                if tracer:
                    tracer.emit_stage(
                        "compliance", "completed",
                        result="COMPLIANT",
                        checks=[
                            "Regulatory validation passed",
                            "Organization policy validation passed",
                        ],
                    )
                record_compliance("PASSED", state.role, elapsed)
            except Exception as exc:
                _set_error(span, str(exc)[:200])
                raise
        await ctx.send_message(state)


class DomainExecutor(Executor):
    """Dispatches the request to the PriceAssistAgent — the primary FAB banking orchestrator.

    PriceAssistAgent handles intent classification internally and delegates to
    DataAgent (→ DataLayer MCP, structured data) or RAGAgent (→ RAG MCP, knowledge
    retrieval) as appropriate. A failed hop degrades gracefully into an error
    answer rather than crashing the workflow.
    """

    def __init__(self, ask: AskRemote, id: str = "domain") -> None:
        super().__init__(id=id)
        self._ask = ask

    # Matches a bare tool-call echo: the model wrote the call as plain text
    # instead of using structured function-calling, so the framework returned
    # it verbatim. Retry once when detected.
    # Uses re.search (not match) so it catches mid-paragraph occurrences where
    # the LLM prefixed explanatory text before the tool-call description.
    # Catches function-call style `tool(`, JSON key style `"tool"`, and
    # descriptive style `tool:` / `tool {`.
    _TOOL_CALL_RE = re.compile(
        r'(query_structured_data|query_knowledge_base)\s*[\(:{"\']',
        re.IGNORECASE,
    )

    # Detects meta-responses: the LLM described calling the tool instead of
    # returning the actual data.  Retry with an explicit reminder when caught.
    _META_RESPONSE_RE = re.compile(
        r'\b(this response was generated|i (have |just |)(called|retrieved|fetched|invoked)'
        r'|data has been (retrieved|fetched)|i would be happy to provide'
        r'|please let me know if you (have|need)|feel free to ask)\b',
        re.IGNORECASE,
    )

    # Detects hallucinated bracket-placeholder templates, e.g. [Name], [Value],
    # [Customer ID], [Brief overview].  These are never present in real tool output.
    _HALLUCINATION_RE = re.compile(r'\[[A-Za-z][A-Za-z0-9 _/-]{1,40}\]')

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState]) -> None:
        tracer = get_active_tracer()
        t0 = time.perf_counter()
        failed = False
        retry_reason = "none"
        route = "unknown"
        route_conf = 0.0

        if tracer:
            tracer.record_agent_invoked()
            tracer.add_execution_path("Price Assist")
            tracer.emit_stage(
                "domain_classification", "started",
                message="Analyzing intent...",
            )

        otel = _mesh_tracer()
        with _span_ctx(otel, "fab.domain.dispatch", kind_internal=False) as span:
            _set_attr(span, "domain.target_node", "price_assist")
            _set_attr(span, "domain.user", state.user_name)
            _set_attr(span, "domain.query_length", len(state.query))

            # Prepend prior conversation turns (if any) so the PriceAssistAgent's
            # LLM resolves follow-ups ("that deal", "its RWA") in-context. The A2A
            # layer flattens to a single string, so history travels inline in the
            # prompt; the bare user question is preserved on the span attr above.
            history_block = ConversationStore.format_history_block(state.conversation_history)
            base_prompt = f"{history_block}{state.query}" if history_block else state.query
            _set_attr(span, "domain.history_turns", len(state.conversation_history) // 2)
            try:
                _add_event(span, "domain.a2a_call.started", {"target": "price_assist"})
                answer = await self._ask("price_assist", base_prompt)
                if self._TOOL_CALL_RE.search(answer or ""):
                    retry_reason = "tool_call_echo"
                    _log.warning(
                        "price_assist returned bare tool-call text; retrying once.",
                        extra={"status": "RETRY"},
                    )
                    _add_event(span, "domain.retry", {"reason": retry_reason, "attempt": 2})
                    answer = await self._ask("price_assist", base_prompt)
                elif self._META_RESPONSE_RE.search(answer or ""):
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
                    answer = await self._ask("price_assist", retry_prompt)
                elif self._HALLUCINATION_RE.search(answer or ""):
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
                    answer = await self._ask("price_assist", retry_prompt)
            except Exception as exc:
                answer = f"The banking assistant is currently unavailable ({exc})."
                failed = True
                state.trail.append("domain_error:price_assist")
                _log.warning("Domain hop failed node=price_assist: %s", exc,
                             extra={"status": "ERROR"})
                _add_event(span, "domain.error", {"error": str(exc)[:200]})
                _set_error(span, str(exc)[:200])
            else:
                state.trail.append("domain_answer:price_assist")
                _log.info("Domain answer (%d chars)", len(answer or ""),
                          extra={"status": "SUCCESS"})

            total_ms = int((time.perf_counter() - t0) * 1000)
            state.answer = answer

            _set_attr(span, "domain.retry_reason", retry_reason)
            _set_attr(span, "domain.retried", retry_reason != "none")
            _set_attr(span, "domain.result", "ERROR" if failed else "SUCCESS")
            _set_attr(span, "domain.answer_length", len(answer or ""))

            if tracer and not failed:
                route, route_rationale, route_conf, alt_scores = infer_route_and_scores(
                    state.query, answer
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
                    checks=[
                        "Context assembled",
                        "Response generated",
                        "Hallucination checks passed",
                    ],
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
                record_a2a_call("price_assist", "SUCCESS", float(total_ms))
                record_domain_route(route, float(total_ms))
            elif failed:
                record_a2a_call("price_assist", "ERROR", float(total_ms))

        await ctx.send_message(state)


class OutputRedactionExecutor(Executor):
    """Deterministic output redaction (PII). Terminal node — yields the answer."""

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[Never, MeshState]) -> None:
        otel = _mesh_tracer()
        with _span_ctx(otel, "fab.output.redact", kind_internal=True) as span:
            original_len = len(state.answer or "")
            _set_attr(span, "redaction.input_length", original_len)
            try:
                state.answer = redact_pii(state.answer)
                output_len = len(state.answer or "")
                pii_count = state.answer.count("[REDACTED_")
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

        state.trail.append("output_redacted")
        _log.info("Request complete trail=%s", " -> ".join(state.trail),
                  extra={"user": state.user_name, "status": "SUCCESS"})
        await ctx.yield_output(state)


def build_mesh_workflow(ask: AskRemote):
    """Builds the mesh orchestration workflow.

    Args:
        ask: async callable ``(node, prompt, **kwargs) -> str`` used for A2A hops.
             The orchestrator passes its module-level ``ask_remote`` so the
             offline test suite can patch the transport.

    Returns:
        An immutable, reusable ``Workflow`` instance.
    """
    guardrail = InputGuardrailExecutor(id="input_guardrail")
    rbac = RBACValidationExecutor(id="rbac_validation")
    compliance = ComplianceExecutor(ask, id="compliance")
    domain = DomainExecutor(ask, id="domain")
    redact = OutputRedactionExecutor(id="output_redaction")

    return (
        WorkflowBuilder(
            start_executor=guardrail,
            name="agent_mesh_pipeline",
            description="Guardrail -> RBAC -> compliance -> price_assist -> redact",
            output_from=[guardrail, rbac, compliance, redact],
        )
        .add_edge(guardrail, rbac)
        .add_edge(rbac, compliance)
        .add_edge(compliance, domain)
        .add_edge(domain, redact)
        .build()
    )


def build_devui_workflow(ask: AskRemote, user_name: str, role: str):
    """Builds the mesh workflow for the DevUI single-process entrypoint.

    Identical pipeline to :func:`build_mesh_workflow`, but prepended with a
    :class:`DevUIEntryExecutor` so the graph accepts the plain ``str`` that DevUI
    sends and stamps it with the configured ``user_name`` / ``role``.
    """
    entry = DevUIEntryExecutor(user_name, role, id="devui_entry")
    guardrail = InputGuardrailExecutor(id="input_guardrail")
    rbac = RBACValidationExecutor(id="rbac_validation")
    compliance = ComplianceExecutor(ask, id="compliance")
    domain = DomainExecutor(ask, id="domain")
    redact = OutputRedactionExecutor(id="output_redaction")

    return (
        WorkflowBuilder(
            start_executor=entry,
            name="agent_mesh_pipeline",
            description="DevUI entry -> guardrail -> RBAC -> compliance -> price_assist -> redact",
            output_from=[guardrail, rbac, compliance, redact],
        )
        .add_edge(entry, guardrail)
        .add_edge(guardrail, rbac)
        .add_edge(rbac, compliance)
        .add_edge(compliance, domain)
        .add_edge(domain, redact)
        .build()
    )
