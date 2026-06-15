"""Mesh client orchestrator.

Drives a single user request across the distributed agent mesh, talking to each
node over A2A. Enforces defense-in-depth guardrails and role-based access:

  1. Deterministic input screen  (hard gate: injection / PII / destructive)
  2. Router node (A2A)           -> domain classification
  3. Role-based access control   (e.g. finance = leadership only)
  4. Compliance node (A2A)       -> semantic safety review (hard gate)
  5. Domain node (A2A)           -> the actual answer (may consult Policy node)
  6. Deterministic output redaction (PII)

Every hop is a real agent-to-agent call to an isolated port, and is recorded by
each node's AuditMiddleware.
"""
import sys
import json
import re
import time
import uuid
import pathlib
from dataclasses import dataclass, field
from typing import Callable, List, Optional

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.auth.identity_provider import User, Role
from src.agents.gateway_agent import parse_domain
from src.guardrails.deterministic_filters import screen_input, redact_pii
from src.a2a.clients import ask_remote
from src.utils.console_logger import AgentLogger
from src.observability import tracer

# Requests that imply moving money -> require a deterministic human approval gate.
_PAYMENT_RE = re.compile(r"\b(pay|payment|payout|remit|transfer|wire|disburse)\b", re.IGNORECASE)


def _cli_approver(prompt: str) -> bool:
    """Default human approver: a CLI yes/no. Works across the A2A boundary because
    it runs in the orchestrator (client) process, not inside the agent."""
    try:
        return input(f"\n>>> {prompt} (yes/no): ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _load_role_access() -> dict:
    try:
        with open(Config.POLICIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("role_access", {})
    except Exception:
        return {}


_ROLE_ACCESS = _load_role_access()


@dataclass
class MeshResult:
    answer: str
    domain: Optional[str] = None
    blocked: bool = False
    block_stage: Optional[str] = None
    trail: List[str] = field(default_factory=list)


def _allowed(domain: str, role: Role) -> tuple[bool, str]:
    rule = _ROLE_ACCESS.get(domain)
    if not rule:
        return True, ""
    allowed_roles = rule.get("allowed_roles", [])
    if role.value in allowed_roles:
        return True, ""
    return False, rule.get("denial_message", f"Access denied: {domain} is restricted.")


async def handle_request(user: User, query: str, approver: Callable[[str], bool] = _cli_approver) -> MeshResult:
    """Runs one request through the full mesh pipeline."""
    trail: List[str] = []
    trace_id = uuid.uuid4().hex  # correlates all trace events for this single request

    tracer.trace_flow_step("request_start", "SUCCESS", 0,
        {"username": user.username, "role": user.role.value, "query_length": len(query)},
        trace_id=trace_id)

    # 1. Deterministic input guardrail (hard gate, pre-routing)
    AgentLogger.print_agent_header("Guardrails", "Deterministic input screen (injection/PII/destructive)")
    t0 = time.perf_counter()
    screen = screen_input(query)
    tracer.trace_guardrail(query, screen.allowed, screen.categories,
        int((time.perf_counter() - t0) * 1000), trace_id=trace_id)
    if not screen.allowed:
        AgentLogger.print_agent_response("Guardrails", f"BLOCKED: {screen.reason}")
        trail.append(f"guardrail_block:{','.join(screen.categories)}")
        tracer.trace_flow_step("request_blocked", "BLOCK", 0,
            {"stage": "input_guardrail", "reason": screen.reason[:120]}, trace_id=trace_id)
        return MeshResult(
            answer=f"Request blocked by security guardrails ({', '.join(screen.categories)}).",
            blocked=True, block_stage="input_guardrail", trail=trail,
        )
    AgentLogger.print_agent_response("Guardrails", "PASSED")
    trail.append("guardrail_pass")

    # 2. Router node (A2A)
    AgentLogger.print_agent_header("GatewayAgent", "Routing via A2A (:%d)" % Config.AGENT_PORTS["gateway"])
    t0 = time.perf_counter()
    router_text = await ask_remote("gateway", query, trace_id=trace_id)
    domain = parse_domain(router_text)
    tracer.trace_flow_step("routing", "SUCCESS", int((time.perf_counter() - t0) * 1000),
        {"domain": domain, "router_raw": router_text[:60]}, trace_id=trace_id)
    AgentLogger.print_agent_response("GatewayAgent", f"domain = {domain}")
    trail.append(f"route:{domain}")

    # 3. Role-based access control
    ok, denial = _allowed(domain, user.role)
    tracer.trace_access_control(domain, user.role.value, ok, denial or "OK", trace_id=trace_id)
    if not ok:
        AgentLogger.print_agent_header("AccessControl", f"DENY {user.role.value} -> {domain}")
        AgentLogger.print_agent_response("AccessControl", denial)
        trail.append(f"access_denied:{domain}")
        tracer.trace_flow_step("request_blocked", "BLOCK", 0,
            {"stage": "access_control", "domain": domain, "role": user.role.value}, trace_id=trace_id)
        return MeshResult(answer=denial, domain=domain, blocked=True, block_stage="access_control", trail=trail)
    trail.append(f"access_ok:{domain}")

    # 4. Compliance node (A2A) — semantic safety review (hard gate)
    AgentLogger.print_agent_header("ComplianceAgent", "Semantic safety review via A2A (:%d)" % Config.AGENT_PORTS["compliance"])
    t0 = time.perf_counter()
    compliance_text = await ask_remote("compliance", f"Review this request for safety: '{query}'", trace_id=trace_id)
    tracer.trace_compliance(query, compliance_text, int((time.perf_counter() - t0) * 1000), trace_id=trace_id)
    AgentLogger.print_agent_response("ComplianceAgent", compliance_text)
    if "compliance_failed" in compliance_text.lower():
        trail.append("compliance_failed")
        tracer.trace_flow_step("request_blocked", "BLOCK", 0,
            {"stage": "compliance", "verdict": compliance_text[:120]}, trace_id=trace_id)
        return MeshResult(
            answer="Request blocked by the Compliance agent (semantic safety review).",
            domain=domain, blocked=True, block_stage="compliance", trail=trail,
        )
    trail.append("compliance_pass")

    # 4b. Deterministic payment approval gate (human-in-the-loop; works over A2A)
    if domain == "finance" and _PAYMENT_RE.search(query):
        AgentLogger.print_agent_header("ApprovalGate", "Outbound payment requires human approval")
        approved = approver("Approve this outbound finance payment?")
        tracer.trace_payment_gate(approved, trace_id=trace_id)
        if not approved:
            AgentLogger.print_agent_response("ApprovalGate", "DENIED by operator")
            trail.append("payment_denied")
            tracer.trace_flow_step("request_blocked", "BLOCK", 0,
                {"stage": "approval"}, trace_id=trace_id)
            return MeshResult(
                answer="Payment was not approved by the operator.",
                domain=domain, blocked=True, block_stage="approval", trail=trail,
            )
        AgentLogger.print_agent_response("ApprovalGate", "APPROVED by operator")
        trail.append("payment_approved")

    # 5. Domain node (A2A) — the actual answer
    AgentLogger.print_agent_header(domain, "Handling request via A2A (:%d)" % Config.AGENT_PORTS[domain])
    t0 = time.perf_counter()
    answer = await ask_remote(domain, query, trace_id=trace_id)
    tracer.trace_flow_step("domain_answer", "SUCCESS", int((time.perf_counter() - t0) * 1000),
        {"domain": domain}, trace_id=trace_id)
    trail.append(f"domain_answer:{domain}")

    # 6. Deterministic output redaction
    safe_answer = redact_pii(answer)
    AgentLogger.print_agent_response(domain, safe_answer)
    trail.append("output_redacted")

    tracer.trace_flow_step("request_complete", "SUCCESS", 0,
        {"domain": domain, "answer_length": len(safe_answer),
         "trail": " -> ".join(trail)}, trace_id=trace_id)
    return MeshResult(answer=safe_answer, domain=domain, trail=trail)
