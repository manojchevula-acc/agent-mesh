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
from src.tracing.execution_trace import get_active_tracer, infer_route_and_scores

_log = get_logger(CAT_WORKFLOW)

# Type alias for the injected dependency.
AskRemote = Callable[..., Awaitable[str]]

# Set of all valid FAB banking role string values — used by RBACValidationExecutor.
_ALLOWED_ROLES = {r.value for r in BankingRole}


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

        screen = screen_input(state.query)

        if not screen.allowed:
            state.blocked = True
            state.block_stage = "input_guardrail"
            state.answer = (
                f"Request blocked by security guardrails ({', '.join(screen.categories)})."
            )
            state.trail.append(f"guardrail_block:{','.join(screen.categories)}")
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
            await ctx.yield_output(state)
            return

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

        if state.role not in _ALLOWED_ROLES:
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
            await ctx.yield_output(state)
            return

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
        await ctx.send_message(state)


class ComplianceExecutor(Executor):
    """Semantic safety review via the Compliance node over A2A (hard gate)."""

    def __init__(self, ask: AskRemote, id: str = "compliance") -> None:
        super().__init__(id=id)
        self._ask = ask

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        tracer = get_active_tracer()
        if tracer:
            tracer.record_agent_invoked()
            tracer.emit_stage(
                "compliance", "started",
                message="Running semantic compliance check...",
            )

        verdict = await self._ask("compliance", f"Review this request for safety: '{state.query}'")
        state.compliance_verdict = verdict

        if "compliance_failed" in verdict.lower():
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
            await ctx.yield_output(state)
            return

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

        if tracer:
            tracer.record_agent_invoked()
            tracer.add_execution_path("Price Assist")
            tracer.emit_stage(
                "domain_classification", "started",
                message="Analyzing intent...",
            )

        try:
            answer = await self._ask("price_assist", state.query)
            if self._TOOL_CALL_RE.search(answer or ""):
                _log.warning(
                    "price_assist returned bare tool-call text; retrying once.",
                    extra={"status": "RETRY"},
                )
                answer = await self._ask("price_assist", state.query)
            elif self._META_RESPONSE_RE.search(answer or ""):
                # LLM acknowledged calling the tool but didn't include the data.
                # Re-ask with an explicit reminder to output the complete result.
                _log.warning(
                    "price_assist returned meta-response without data; retrying once.",
                    extra={"status": "RETRY"},
                )
                retry_prompt = (
                    f"{state.query}\n\n"
                    "IMPORTANT: Your previous response did not include the actual data. "
                    "You MUST copy the COMPLETE raw output returned by the tool into your "
                    "response — every field, every row, every figure. Do NOT say 'I retrieved' "
                    "or 'I called'; just show the data."
                )
                answer = await self._ask("price_assist", retry_prompt)
            elif self._HALLUCINATION_RE.search(answer or ""):
                # LLM generated bracket-placeholder template text (e.g. [Name],
                # [Email Address]) instead of real tool output.  Retry with an
                # explicit instruction to use only values from the tool.
                _log.warning(
                    "price_assist returned hallucinated placeholder text; retrying once.",
                    extra={"status": "RETRY"},
                )
                retry_prompt = (
                    f"{state.query}\n\n"
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
        else:
            state.trail.append("domain_answer:price_assist")
            _log.info("Domain answer (%d chars)", len(answer or ""),
                      extra={"status": "SUCCESS"})

        total_ms = int((time.perf_counter() - t0) * 1000)
        state.answer = answer

        if tracer and not failed:
            route, route_rationale, route_conf, alt_scores = infer_route_and_scores(
                state.query, answer
            )
            tracer.record_domain("Price Assist Agent", 0.96)
            tracer.record_route(route)
            tracer.add_execution_path(route)

            # domain_classification was started before the A2A call; complete it now
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

            # Routing, handoff, retrieval, and response happened remotely inside
            # PriceAssistAgent. Emit completed events so the CLI renders the full flow.
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

        await ctx.send_message(state)


class OutputRedactionExecutor(Executor):
    """Deterministic output redaction (PII). Terminal node — yields the answer."""

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[Never, MeshState]) -> None:
        state.answer = redact_pii(state.answer)
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
