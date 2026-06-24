"""Execution Trace Layer — reusable non-invasive event bus for AgentMesh.

Uses a ContextVar so any call-stack layer can emit events without signature
changes. Designed for CLI, REST API, Web UI, OTel/Langfuse/Grafana.
"""
from __future__ import annotations

import time
import uuid
import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

@dataclass
class ExecutionEvent:
    """A single step in the execution pipeline."""
    stage: str            # e.g. "guardrail", "compliance", "domain_classification"
    status: str           # "started" | "completed" | "blocked" | "failed"
    message: str = ""
    result: Optional[str] = None
    confidence: Optional[float] = None
    rationale: List[str] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[int] = None


@dataclass
class ExecutionSummary:
    """Final summary produced at the end of a request."""
    request_id: str
    user: str
    domain: Optional[str] = None
    route: Optional[str] = None
    execution_path: List[str] = field(default_factory=list)
    agents_invoked: int = 0
    tools_used: int = 0
    total_duration_ms: int = 0
    confidence: Optional[float] = None
    blocked: bool = False
    block_stage: Optional[str] = None
    events: List[ExecutionEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------

class ExecutionTracer:
    """Collects execution events and notifies registered listeners in real time.

    One instance per request. Store in the active_tracer ContextVar so any
    code on the call stack can emit events without an explicit reference.
    """

    def __init__(self, user: str, query: str) -> None:
        self.request_id = uuid.uuid4().hex[:8].upper()
        self.user = user
        self.query = query
        self._events: List[ExecutionEvent] = []
        self._listeners: List[Callable[[ExecutionEvent], None]] = []
        self._t0 = time.perf_counter()
        self._domain: Optional[str] = None
        self._route: Optional[str] = None
        self._execution_path: List[str] = []
        self._agents_invoked = 0
        self._tools_used = 0
        self._blocked = False
        self._block_stage: Optional[str] = None
        self._confidence: Optional[float] = None

    # -- Listener management -------------------------------------------------

    def add_listener(self, fn: Callable[[ExecutionEvent], None]) -> None:
        self._listeners.append(fn)

    # -- Event emission -------------------------------------------------------

    def emit(self, event: ExecutionEvent) -> None:
        self._events.append(event)
        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass

    def emit_stage(
        self,
        stage: str,
        status: str,
        message: str = "",
        result: Optional[str] = None,
        confidence: Optional[float] = None,
        rationale: Optional[List[str]] = None,
        checks: Optional[List[str]] = None,
        duration_ms: Optional[int] = None,
        **metadata: Any,
    ) -> None:
        self.emit(ExecutionEvent(
            stage=stage,
            status=status,
            message=message,
            result=result,
            confidence=confidence,
            rationale=rationale or [],
            checks=checks or [],
            metadata=dict(metadata),
            duration_ms=duration_ms,
        ))

    # -- Summary helpers ------------------------------------------------------

    def record_domain(self, domain: str, confidence: float) -> None:
        self._domain = domain
        self._confidence = confidence

    def record_route(self, route: str) -> None:
        self._route = route

    def add_execution_path(self, name: str) -> None:
        if name not in self._execution_path:
            self._execution_path.append(name)

    def record_agent_invoked(self) -> None:
        self._agents_invoked += 1

    def record_tool_used(self) -> None:
        self._tools_used += 1

    def record_blocked(self, stage: str) -> None:
        self._blocked = True
        self._block_stage = stage

    def summary(self) -> ExecutionSummary:
        return ExecutionSummary(
            request_id=self.request_id,
            user=self.user,
            domain=self._domain,
            route=self._route,
            execution_path=list(self._execution_path),
            agents_invoked=self._agents_invoked,
            tools_used=self._tools_used,
            total_duration_ms=int((time.perf_counter() - self._t0) * 1000),
            confidence=self._confidence,
            blocked=self._blocked,
            block_stage=self._block_stage,
            events=list(self._events),
        )


# ---------------------------------------------------------------------------
# Routing inference — derive display info from query/answer text
# ---------------------------------------------------------------------------

def infer_route_and_scores(
    query: str, answer: str
) -> Tuple[str, List[str], float, Dict[str, float]]:
    """Infer routing decision and domain confidence breakdown from text.

    Returns (route_label, rationale_bullets, route_confidence, alt_domain_scores).
    Used by DomainExecutor to populate trace events for the remote steps that
    happened inside PriceAssistAgent (which runs in a separate A2A process).
    """
    text = (query + " " + answer).lower()

    # Removed from data_kw:
    #   "pricing" — appears in policy queries ("pricing floor/ceiling") as often as
    #               data queries; was causing false-positive data routing.
    #   "rate"    — "BB-rated" substring was triggering data routing for pure RAG
    #               policy questions; rate-specific data queries always carry "cust"
    #               or "deal" anyway.
    data_kw = ["price", "margin", "customer", "cust", "deal", "profitability",
               "rwa", "data", "record", "structured", "recommend", "cost"]
    rag_kw  = ["policy", "regulation", "document", "guide", "faq", "procedure", "rule",
               "limit", "floor", "ceiling", "credit", "compliance", "aml", "kyc", "fee",
               # governance / model-risk / CBUAE regulatory domain
               "governance", "model", "requirement", "circular", "criteria",
               "incident", "validation", "eligibility", "cbuae",
               # product structure questions (e.g. "interest rate components")
               "component"]

    data_hits = [kw for kw in data_kw if kw in text]
    rag_hits  = [kw for kw in rag_kw  if kw in text]
    data_score = len(data_hits)
    rag_score  = len(rag_hits)

    if data_score > rag_score:
        route = "Data Layer Service"
        kw_preview = ", ".join(data_hits[:4])
        rationale = [
            "User requested structured enterprise banking data.",
            f"Keywords detected: {kw_preview}.",
            "Data exists in enterprise database (DataLayer MCP).",
            "Structured query provides higher accuracy.",
            "No document retrieval required.",
        ]
        conf = min(0.95, 0.82 + data_score * 0.02)
    elif rag_score > data_score:
        route = "RAG Service"
        kw_preview = ", ".join(rag_hits[:4])
        rationale = [
            "User requested policy or knowledge-base information.",
            f"Keywords detected: {kw_preview}.",
            "Document retrieval provides relevant context.",
            "Knowledge base contains applicable policies.",
        ]
        conf = min(0.95, 0.82 + rag_score * 0.02)
    else:
        route = "Data Layer + RAG (Hybrid)"
        rationale = [
            "Request requires both structured data and policy knowledge.",
            "Data layer provides customer and pricing facts.",
            "RAG provides policy context and compliance rules.",
        ]
        conf = 0.84

    # Domain confidence breakdown shown in --explain mode
    base_price = 0.94 + min(data_score + rag_score, 3) * 0.01
    knowledge_conf  = min(0.35, 0.06 + rag_score * 0.04)
    compliance_conf = 0.04
    alt_scores: Dict[str, float] = {
        "Price Assist":     min(base_price, 0.98),
        "Knowledge Assist": knowledge_conf,
        "Compliance Assist": compliance_conf,
    }

    return route, rationale, conf, alt_scores


# ---------------------------------------------------------------------------
# ContextVar API — zero-arg access from any call-stack layer
# ---------------------------------------------------------------------------

_active_tracer: contextvars.ContextVar[Optional[ExecutionTracer]] = \
    contextvars.ContextVar("execution_tracer", default=None)


def get_active_tracer() -> Optional[ExecutionTracer]:
    return _active_tracer.get()


def set_active_tracer(tracer: ExecutionTracer) -> contextvars.Token:
    return _active_tracer.set(tracer)


def clear_active_tracer(token: contextvars.Token) -> None:
    _active_tracer.reset(token)
