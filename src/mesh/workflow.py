"""Mesh orchestration as a Microsoft Agent Framework Workflow.

The request pipeline is expressed as a typed ``WorkflowBuilder`` graph so the
framework emits native observability spans for the whole orchestration:

    workflow.run
      └─ executor.process input_guardrail
      └─ executor.process router          ──(A2A)──► invoke_agent GatewayAgent
      └─ executor.process access_control
      └─ executor.process compliance      ──(A2A)──► invoke_agent ComplianceAgent
      └─ executor.process payment_gate     (human-in-the-loop, finance payments)
      └─ executor.process domain           ──(A2A, parallel)──► invoke_agent <DomainAgent(s)>
      └─ executor.process output_redaction

Each hop is an ``executor.process`` span; ``WorkflowContext.send_message``
auto-propagates trace context between executors, and the A2A client propagates
it across process boundaries (see ``src/a2a/clients.py``). This replaces the old
hand-rolled, custom-JSONL pipeline with framework-native telemetry.

Design notes
------------
- A single :class:`MeshState` message flows through the graph. Each gate either
  forwards (``ctx.send_message``) to proceed or yields (``ctx.yield_output``) to
  terminate early (blocked). This keeps the graph linear and type-safe while
  preserving the exact defense-in-depth stage order.
- Multi-domain queries: the Gateway can return more than one domain. The
  ``AccessControlExecutor`` filters to only the domains the user's role permits.
  ``DomainExecutor`` fans out to all allowed domains in parallel (asyncio.gather)
  and merges the answers into a single response.
- A2A-calling executors use an injected ``ask`` callable so the offline test
  suite can patch the transport at the ``orchestrator.ask_remote`` seam.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import pathlib
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from typing_extensions import Never

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from src.config import Config
from src.guardrails.deterministic_filters import screen_input, redact_pii
from src.agents.gateway_agent import parse_domains
from src.observability import get_logger, CAT_WORKFLOW, CAT_APPROVALS

_log = get_logger(CAT_WORKFLOW)
_approval_log = get_logger(CAT_APPROVALS)

# Requests that imply moving money -> require a deterministic human approval gate.
_PAYMENT_RE = re.compile(r"\b(pay|payment|payout|remit|transfer|wire|disburse)\b", re.IGNORECASE)

# Type aliases for the injected dependencies.
AskRemote = Callable[..., Awaitable[str]]
Approver = Callable[[str], bool]


def _load_role_access() -> dict:
    try:
        with open(Config.POLICIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("role_access", {})
    except Exception:
        return {}


_ROLE_ACCESS = _load_role_access()


def _allowed(domain: str, role: str) -> tuple[bool, str]:
    """Returns (allowed, denial_message) for a (domain, role) pair."""
    rule = _ROLE_ACCESS.get(domain)
    if not rule:
        return True, ""
    if role in rule.get("allowed_roles", []):
        return True, ""
    return False, rule.get("denial_message", f"Access denied: {domain} is restricted.")


@dataclass
class MeshState:
    """The single message that flows through the mesh workflow graph."""
    user_name: str
    role: str
    query: str
    session_id: str = "default_session"
    # domains: all domains resolved by the gateway (may be more than one for multi-topic queries).
    # domain:  primary domain (first in domains); kept for single-domain compat and logging.
    domains: List[str] = field(default_factory=list)
    domain: Optional[str] = None
    router_raw: str = ""
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
    off to the normal guardrail stage. It exists only for the single-process DevUI
    entrypoint; the distributed orchestrator seeds ``MeshState`` itself.
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
    """Deterministic input screen (hard gate, pre-routing). Workflow start node."""

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


class RouterExecutor(Executor):
    """Routes the request to a domain via the Gateway node over A2A."""

    def __init__(self, ask: AskRemote, id: str = "router") -> None:
        super().__init__(id=id)
        self._ask = ask

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState]) -> None:
        router_text = await self._ask("gateway", state.query)
        state.router_raw = router_text
        state.domains = parse_domains(router_text)
        state.domain = state.domains[0]
        state.trail.append(f"route:{','.join(state.domains)}")
        _log.info("Routed to domains=%s", state.domains,
                  extra={"domain": state.domain, "user": state.user_name})
        await ctx.send_message(state)


class AccessControlExecutor(Executor):
    """Role-based access control gate (e.g. finance = leadership only)."""

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        allowed, denied = [], []
        for d in state.domains:
            ok, msg = _allowed(d, state.role)
            if ok:
                allowed.append(d)
            else:
                denied.append((d, msg))

        if not allowed:
            # Every requested domain is restricted for this role — block entirely.
            state.blocked = True
            state.block_stage = "access_control"
            state.answer = denied[0][1]
            state.trail.append(f"access_denied:{','.join(d for d, _ in denied)}")
            _log.warning("Access DENY role=%s domains=%s", state.role, [d for d, _ in denied],
                         extra={"domain": state.domain, "user": state.user_name, "status": "DENY"})
            await ctx.yield_output(state)
            return

        # Partial access: serve only the domains this role can reach.
        for d in allowed:
            state.trail.append(f"access_ok:{d}")
        for d, _ in denied:
            state.trail.append(f"access_partial_deny:{d}")

        state.domains = allowed
        state.domain = allowed[0]
        _log.info(
            "Access OK role=%s domains=%s%s", state.role, allowed,
            f" (partial deny: {[d for d, _ in denied]})" if denied else "",
            extra={"domain": state.domain, "user": state.user_name, "status": "PASS"},
        )
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
                         extra={"domain": state.domain, "status": "FAIL"})
            await ctx.yield_output(state)
            return
        state.trail.append("compliance_pass")
        _log.info("Compliance PASS", extra={"domain": state.domain, "status": "PASS"})
        await ctx.send_message(state)


class PaymentApprovalExecutor(Executor):
    """Deterministic human-in-the-loop gate for outbound finance payments.

    Runs in the orchestrator process, so it can drive an interactive approver
    even across the A2A boundary (the remote Finance agent never sees approval
    content). Non-payment requests pass straight through.
    """

    def __init__(self, approver: Approver, id: str = "payment_gate") -> None:
        super().__init__(id=id)
        self._approver = approver

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState, MeshState]) -> None:
        is_payment = "finance" in state.domains and bool(_PAYMENT_RE.search(state.query))
        if not is_payment:
            await ctx.send_message(state)
            return

        _approval_log.info("Outbound payment requires human approval",
                           extra={"user": state.user_name, "domain": "finance"})
        approved = self._approver("Approve this outbound finance payment?")
        if not approved:
            state.blocked = True
            state.block_stage = "approval"
            state.answer = "Payment was not approved by the operator."
            state.trail.append("payment_denied")
            _approval_log.warning("Payment DENIED by operator",
                                  extra={"user": state.user_name, "status": "DENY"})
            await ctx.yield_output(state)
            return
        state.trail.append("payment_approved")
        _approval_log.info("Payment APPROVED by operator",
                           extra={"user": state.user_name, "status": "APPROVE"})
        await ctx.send_message(state)


class DomainExecutor(Executor):
    """Dispatches the request to the resolved domain node over A2A."""

    def __init__(self, ask: AskRemote, id: str = "domain") -> None:
        super().__init__(id=id)
        self._ask = ask

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState]) -> None:
        if len(state.domains) == 1:
            answer = await self._ask(state.domains[0], state.query)
            state.answer = answer
            state.trail.append(f"domain_answer:{state.domains[0]}")
            _log.info("Domain answer domain=%s (%d chars)", state.domains[0], len(answer or ""),
                      extra={"domain": state.domain, "status": "SUCCESS"})
        else:
            # Multi-domain: fan out to all allowed domain agents in parallel.
            results = await asyncio.gather(
                *[self._ask(d, state.query) for d in state.domains],
                return_exceptions=True,
            )
            sections = []
            for domain, result in zip(state.domains, results):
                label = domain.replace("_", " ").title()
                if isinstance(result, Exception):
                    _log.warning("Domain %s error: %s", domain, result,
                                 extra={"domain": domain, "status": "ERROR"})
                    sections.append(f"### {label}\n*(Unable to retrieve — {result})*")
                else:
                    sections.append(f"### {label}\n{result}")
                state.trail.append(f"domain_answer:{domain}")
            state.answer = "\n\n".join(sections)
            _log.info("Multi-domain answer domains=%s (%d chars)", state.domains, len(state.answer),
                      extra={"domain": state.domain, "status": "SUCCESS"})

        await ctx.send_message(state)


class OutputRedactionExecutor(Executor):
    """Deterministic output redaction (PII). Terminal node — yields the answer."""

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[Never, MeshState]) -> None:
        state.answer = redact_pii(state.answer)
        state.trail.append("output_redacted")
        _log.info("Request complete domains=%s trail=%s", state.domains or [state.domain],
                  " -> ".join(state.trail),
                  extra={"domain": state.domain, "status": "SUCCESS"})
        await ctx.yield_output(state)


def build_mesh_workflow(ask: AskRemote, approver: Approver):
    """Builds the mesh orchestration workflow.

    Args:
        ask: async callable ``(node, prompt, **kwargs) -> str`` used for A2A hops.
             The orchestrator passes its module-level ``ask_remote`` so the
             offline test suite can patch the transport.
        approver: sync callable ``(prompt) -> bool`` for the payment HITL gate.

    Returns:
        An immutable, reusable ``Workflow`` instance.
    """
    guardrail = InputGuardrailExecutor(id="input_guardrail")
    router = RouterExecutor(ask, id="router")
    access = AccessControlExecutor(id="access_control")
    compliance = ComplianceExecutor(ask, id="compliance")
    payment = PaymentApprovalExecutor(approver, id="payment_gate")
    domain = DomainExecutor(ask, id="domain")
    redact = OutputRedactionExecutor(id="output_redaction")

    return (
        WorkflowBuilder(
            start_executor=guardrail,
            name="agent_mesh_pipeline",
            description="Guardrail -> route -> access -> compliance -> approval -> domain(s) -> redact",
            # Every executor that can yield a terminal output (block points + final).
            output_from=[guardrail, access, compliance, payment, redact],
        )
        .add_edge(guardrail, router)
        .add_edge(router, access)
        .add_edge(access, compliance)
        .add_edge(compliance, payment)
        .add_edge(payment, domain)
        .add_edge(domain, redact)
        .build()
    )


def build_devui_workflow(ask: AskRemote, approver: Approver, user_name: str, role: str):
    """Builds the mesh workflow for the DevUI single-process entrypoint.

    Identical pipeline to :func:`build_mesh_workflow`, but prepended with a
    :class:`DevUIEntryExecutor` so the graph accepts the plain ``str`` that DevUI
    sends and stamps it with the configured ``user_name`` / ``role``. The injected
    ``ask`` here is an in-process adapter (calls each node agent directly), which
    keeps the entire trace tree in one process so DevUI can visualize it.

    Args:
        ask: async ``(node, prompt, **kwargs) -> str`` transport (in-process for DevUI).
        approver: sync ``(prompt) -> bool`` for the payment HITL gate.
        user_name: identity stamped on every DevUI request.
        role: role used for access-control decisions in DevUI.

    Returns:
        An immutable, reusable ``Workflow`` instance whose input is ``str``.
    """
    entry = DevUIEntryExecutor(user_name, role, id="devui_entry")
    guardrail = InputGuardrailExecutor(id="input_guardrail")
    router = RouterExecutor(ask, id="router")
    access = AccessControlExecutor(id="access_control")
    compliance = ComplianceExecutor(ask, id="compliance")
    payment = PaymentApprovalExecutor(approver, id="payment_gate")
    domain = DomainExecutor(ask, id="domain")
    redact = OutputRedactionExecutor(id="output_redaction")

    return (
        WorkflowBuilder(
            start_executor=entry,
            name="agent_mesh_pipeline",
            description="DevUI entry -> guardrail -> route -> access -> compliance -> approval -> domain(s) -> redact",
            output_from=[guardrail, access, compliance, payment, redact],
        )
        .add_edge(entry, guardrail)
        .add_edge(guardrail, router)
        .add_edge(router, access)
        .add_edge(access, compliance)
        .add_edge(compliance, payment)
        .add_edge(payment, domain)
        .add_edge(domain, redact)
        .build()
    )
