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
import time
import pathlib
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from typing_extensions import Never

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from src.config import Config
from src.guardrails.deterministic_filters import screen_input, redact_pii
from src.agents.gateway_agent import parse_domain_queries
from src.observability import get_logger, CAT_WORKFLOW, CAT_APPROVALS, CAT_SECURITY

_log = get_logger(CAT_WORKFLOW)
_approval_log = get_logger(CAT_APPROVALS)
_security_log = get_logger(CAT_SECURITY)

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
    # domain_queries: per-domain sub-questions decomposed by the gateway.
    #   Single-domain:  {"hr": full_query}
    #   Multi-domain:   {"hr": "leave sub-q", "finance": "budget sub-q"}
    # domains: ordered list of keys from domain_queries (preserved for access control / logging).
    # domain:  primary domain (first); kept for single-domain compat.
    domain_queries: Dict[str, str] = field(default_factory=dict)
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
            _security_log.warning("Input guardrail BLOCK: %s", screen.reason[:160],
                                  extra={"event_type": "guardrail_block",
                                         "categories": ",".join(screen.categories),
                                         "user": state.user_name, "status": "BLOCK"})
            from src.observability import record_guardrail
            record_guardrail(categories=screen.categories, role=state.role)
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
        # Detect whether keyword fallback resolved the domain (parse_domain_queries
        # returns a fallback when the LLM output had no valid domain token).
        parsed = parse_domain_queries(router_text, state.query)
        tl = (router_text or "").lower()
        fallback_used = not any(d in tl for d in ("finance", "hr", "internal_job"))
        state.domain_queries = parsed
        state.domains = list(state.domain_queries.keys())
        state.domain = state.domains[0]
        state.trail.append(f"route:{','.join(state.domains)}")
        _log.info("Routed to domains=%s fallback=%s", state.domains, fallback_used,
                  extra={"domain": state.domain, "user": state.user_name})
        from src.observability import record_routing
        record_routing(
            domains_count=len(state.domains),
            fallback_used=fallback_used,
            role=state.role,
        )
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
            _security_log.warning("Access DENY role=%s domains=%s", state.role, [d for d, _ in denied],
                                  extra={"event_type": "rbac_deny", "role": state.role,
                                         "denied_domains": ",".join(d for d, _ in denied),
                                         "domain": state.domain, "user": state.user_name,
                                         "status": "DENY"})
            from src.observability import record_access_denied
            for d, _ in denied:
                record_access_denied(domain=d, role=state.role)
            await ctx.yield_output(state)
            return

        # Partial access: serve only the domains this role can reach.
        for d in allowed:
            state.trail.append(f"access_ok:{d}")
        for d, _ in denied:
            state.trail.append(f"access_partial_deny:{d}")
        if denied:
            from src.observability import record_access_denied
            for d, _ in denied:
                record_access_denied(domain=d, role=state.role)

        state.domains = allowed
        state.domain = allowed[0]
        state.domain_queries = {d: state.domain_queries.get(d, state.query) for d in allowed}
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
            _security_log.warning("Compliance FAIL: %s", verdict[:160],
                                  extra={"event_type": "compliance_block",
                                         "domain": state.domain, "status": "FAIL"})
            from src.observability import record_compliance
            record_compliance(domain=state.domain or "unknown")
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
        _t_prompt = time.perf_counter()
        approved = self._approver("Approve this outbound finance payment?")
        wait_ms = (time.perf_counter() - _t_prompt) * 1000
        from src.observability import record_payment_gate, record_approval_wait
        record_approval_wait(wait_ms=wait_ms, approved=approved)
        if not approved:
            state.blocked = True
            state.block_stage = "approval"
            state.answer = "Payment was not approved by the operator."
            state.trail.append("payment_denied")
            _approval_log.warning("Payment DENIED by operator (waited %.0f ms)",
                                  wait_ms,
                                  extra={"user": state.user_name, "status": "DENY"})
            record_payment_gate(approved=False)
            await ctx.yield_output(state)
            return
        state.trail.append("payment_approved")
        _approval_log.info("Payment APPROVED by operator (waited %.0f ms)",
                           wait_ms,
                           extra={"user": state.user_name, "status": "APPROVE"})
        record_payment_gate(approved=True)
        await ctx.send_message(state)


class DomainExecutor(Executor):
    """Dispatches the request to the resolved domain node over A2A."""

    def __init__(self, ask: AskRemote, id: str = "domain") -> None:
        super().__init__(id=id)
        self._ask = ask

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[MeshState]) -> None:
        if len(state.domains) == 1:
            d = state.domains[0]
            sub_query = state.domain_queries.get(d, state.query)
            answer = await self._ask(d, sub_query)
            state.answer = answer
            state.trail.append(f"domain_answer:{d}")
            _log.info("Domain answer domain=%s (%d chars)", d, len(answer or ""),
                      extra={"domain": state.domain, "status": "SUCCESS"})
        else:
            # Multi-domain: fan out to each domain with its specific sub-question in parallel.
            results = await asyncio.gather(
                *[self._ask(d, state.domain_queries.get(d, state.query)) for d in state.domains],
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


# Phrases that suggest the LLM is speculating rather than grounding its answer.
_HALLUCINATION_INDICATORS = re.compile(
    r"\b(i('m| am) not (sure|certain|aware)|i don'?t (know|have (access|information))|"
    r"as (of|far as) (my|i) know|i (cannot|can'?t) (confirm|verify|access)|"
    r"i (believe|think|assume) (but|though) (i'?m|i am) not|"
    r"this (may|might|could) (not )?be (accurate|correct|up.?to.?date)|"
    r"(please|you should) (verify|confirm|check) (this|with))\b",
    re.IGNORECASE,
)


class OutputRedactionExecutor(Executor):
    """Deterministic output redaction (PII) + hallucination heuristic. Terminal node."""

    @handler
    async def run(self, state: MeshState, ctx: WorkflowContext[Never, MeshState]) -> None:
        state.answer = redact_pii(state.answer)

        # Heuristic hallucination check: flag responses that contain speculation phrases.
        match = _HALLUCINATION_INDICATORS.search(state.answer or "")
        if match:
            indicator = "speculation_phrase"
            _security_log.warning(
                "Hallucination heuristic triggered domain=%s indicator=%s matched=%r",
                state.domain, indicator, match.group(0)[:60],
                extra={"domain": state.domain, "event_type": "hallucination_suspected"},
            )
            from src.observability import record_hallucination_suspected
            record_hallucination_suspected(domain=state.domain or "unknown", indicator=indicator)
            state.trail.append("hallucination_suspected")

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
