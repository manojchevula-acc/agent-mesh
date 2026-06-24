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

import sys
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
            await ctx.yield_output(state)
            return
        state.trail.append("guardrail_pass")
        _log.info("Input guardrail PASS", extra={"user": state.user_name, "status": "PASS"})
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
            await ctx.yield_output(state)
            return
        state.trail.append(f"rbac_pass:{state.role}")
        _log.info("RBAC PASS role=%s", state.role,
                  extra={"user": state.user_name, "status": "PASS"})
        await ctx.send_message(state)


class ComplianceExecutor(Executor):
    """Semantic safety review via the Compliance node over A2A (hard gate)."""

    def __init__(self, ask: AskRemote, id: str = "compliance") -> None:
        super().__init__(id=id)
        self._ask = ask

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        verdict = await self._ask("compliance", f"Review this request for safety: '{state.query}'")
        state.compliance_verdict = verdict
        if "compliance_failed" in verdict.lower():
            state.blocked = True
            state.block_stage = "compliance"
            state.answer = "Request blocked by the Compliance agent (semantic safety review)."
            state.trail.append("compliance_failed")
            _log.warning("Compliance FAIL: %s", verdict[:160],
                         extra={"user": state.user_name, "status": "FAIL"})
            await ctx.yield_output(state)
            return
        state.trail.append("compliance_pass")
        _log.info("Compliance PASS", extra={"user": state.user_name, "status": "PASS"})
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

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState]) -> None:
        try:
            answer = await self._ask("price_assist", state.query)
        except Exception as exc:
            answer = f"The banking assistant is currently unavailable ({exc})."
            state.trail.append("domain_error:price_assist")
            _log.warning("Domain hop failed node=price_assist: %s", exc,
                         extra={"status": "ERROR"})
        else:
            state.trail.append("domain_answer:price_assist")
            _log.info("Domain answer (%d chars)", len(answer or ""),
                      extra={"status": "SUCCESS"})
        state.answer = answer
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
