"""Central Gernas SDK wiring for Agent Mesh.

Every entrypoint (CLI, API server, A2A nodes, DevUI) calls :func:`init_telemetry`
once, and every instrumented module imports its decorators from here. This keeps
the SDK an optional, swappable dependency: if ``gernas`` is not installed or the
mandatory ``GERNAS_*`` configuration is missing, telemetry is disabled with one
warning and every decorator degrades to a pass-through — the mesh never fails
because of telemetry.

Configuration is environment-only (``GERNAS_*`` in ``agent-mesh/.env``); see
``gernas-sdk/docs/getting-started.md``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

# Single instrumentation path: the a2a-sdk library ships its own OTel tracing
# (a2a.server.events.* / a2a.client.transports.* spans), decided once at import of
# ``a2a``. This module is imported by every entrypoint before ``a2a`` is, so the
# default below switches it off; Gernas spans the A2A hop (agent.delegate, ASGI server).
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

_log = logging.getLogger("mesh.system")

try:  # pragma: no branch - import guard
    import gernas
    from gernas import Ctx
    AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    gernas = None  # type: ignore[assignment]
    Ctx = None  # type: ignore[assignment]
    AVAILABLE = False
    _IMPORT_ERROR = _exc

# Model provider of the mesh's OpenAI-compatible endpoint (LLM_BASE_URL); mesh configuration.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")

_handle: Any = None
_channel: str | None = None


def _passthrough(*_a: Any, **_k: Any) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        return fn
    return deco


def init_telemetry(service_name: str, *, channel: str | None = None, exporter: str | None = None,
                   framework_native_telemetry: bool = False) -> Any:
    """Initialise the Gernas SDK for this process. Returns the handle or None.

    ``channel`` (cli / api / devui / a2a) is stamped on every ``use_case.request``.
    Agent Framework's own instrumentation is switched off so the Gernas SDK is the
    single instrumentation path (``framework_native_telemetry=True`` keeps it, e.g.
    for DevUI's local trace view, which exports nothing).
    """
    global _handle, _channel
    _channel = channel
    if not AVAILABLE:
        _log.warning("Gernas SDK not installed — telemetry disabled (%s)", _IMPORT_ERROR)
        return None
    if exporter:
        os.environ["GERNAS_EXPORTER"] = exporter
    try:
        cfg = gernas.GernasConfig.from_env(service_name=service_name)
    except gernas.GernasConfigError as exc:
        _log.warning("Gernas SDK telemetry disabled: %s", exc)
        return None
    _handle = gernas.init_gernas(cfg)
    if not framework_native_telemetry:
        try:
            from gernas.adapters.agent_framework import disable_native_telemetry
            disable_native_telemetry()
        except Exception as exc:  # noqa: BLE001 - agent-framework missing: nothing to disable
            _log.debug("Agent Framework native telemetry not disabled: %s", exc)
    _log.info("Gernas SDK %s initialised: service=%s exporter=%s endpoint=%s",
              gernas.__version__, cfg.service_name, cfg.exporter, cfg.otlp_endpoint or "-")
    return _handle


def describe() -> str:
    """One-line description of where this process sends telemetry (startup banners)."""
    if _handle is None:
        return "Gernas SDK telemetry disabled"
    cfg = _handle.config
    return f"Gernas SDK {gernas.__version__} -> {cfg.exporter} {cfg.otlp_endpoint or ''}".rstrip()


def correlation() -> dict:
    """trace_id / span_id / agent_run_id / session_id / task_id for log and audit records."""
    if not AVAILABLE:
        return {}
    return gernas.correlation()


def finalize() -> None:
    """Flush pending telemetry (call before the process exits)."""
    if _handle is None:
        return
    try:
        _handle.flush(10_000)
    except Exception:  # noqa: BLE001
        pass


def shutdown() -> None:
    if _handle is not None:
        finalize()
        _handle.shutdown()


# ── decorators (pass-through when the SDK is absent) ─────────────────────────

def _get(name: str) -> Callable[..., Any]:
    return getattr(gernas, name) if AVAILABLE else _passthrough


trace_agent = _get("trace_agent")
trace_step = _get("trace_step")
trace_llm = _get("trace_llm")
trace_tool = _get("trace_tool")
trace_retrieval = _get("trace_retrieval")
trace_embedding = _get("trace_embedding")
trace_memory = _get("trace_memory")
trace_use_case = _get("trace_use_case")
trace_delegation = _get("trace_delegation")
trace_guardrail = _get("trace_guardrail")
trace_content_safety = _get("trace_content_safety")


def current_channel(*_a: Any, **_k: Any) -> str | None:
    return _channel


def trace_workflow_step(step_id: str) -> Callable[[Callable], Callable]:
    """``agent.step`` for one executor of the mesh workflow (Use Case → Agent → Run → Step)."""
    if not AVAILABLE:
        return _passthrough()
    return gernas.trace_step(step_id, step_type="workflow_executor")


def trace_mesh_run() -> Callable[[Callable], Callable]:
    """``agent.run`` of the mesh orchestrator around one workflow execution."""
    if not AVAILABLE:
        return _passthrough()
    return gernas.trace_agent("agent_mesh", agent_type="orchestrator", name="Agent Mesh Orchestrator",
                              framework="agent_framework")


def start_hitl(ticket_id: str, action: str, **kwargs: Any) -> None:
    if AVAILABLE:
        gernas.start_hitl(ticket_id, action, **kwargs)


def complete_hitl(ticket_id: str, outcome: str, **kwargs: Any) -> None:
    if AVAILABLE:
        gernas.complete_hitl(ticket_id, outcome, **kwargs)


def mark_policy_block() -> None:
    """The active run ended because a policy gate blocked it (termination_reason=policy_block)."""
    if AVAILABLE:
        gernas.set_termination_reason("policy_block")


def record_retry(n: int = 1) -> None:
    if AVAILABLE:
        gernas.record_retry(n)


def spawn(coro: Any, name: str | None = None) -> Any:
    """``asyncio.create_task`` that keeps background work in the caller's trace."""
    import asyncio
    if AVAILABLE:
        return gernas.gernas_task(coro, name=name)
    return asyncio.create_task(coro, name=name)


def request_context(*, request_id: str, session_id: str, user: str) -> Any:
    """GERNAS request context: session, task (= mesh request id), channel and the
    initiating (human) principal. The user id is tokenized by the SDK (GERNAS_USER_TOKEN_KEY);
    the banking role is not a Blueprint field and is not recorded."""
    import contextlib
    if not AVAILABLE:
        return contextlib.nullcontext()
    return gernas.request_context(session_id=session_id or None, task_id=request_id or None, channel=_channel,
                                  user_id=user or None, principal_type="human")


_AGENT_TYPES = {"price_assist": "orchestrator", "compliance": "guardrail"}


def agent_middleware(agent_id: str, *, mcp_server_id: str | None = None) -> list[Any]:
    """MAF Agent + Chat middlewares for one mesh node, plus the Function middleware
    (``tool.call`` with ``tool_type=mcp`` per real MCP tool call) on MCP-backed nodes.
    Function tools of non-MCP nodes already carry ``@trace_tool`` (no duplicate spans)."""
    if not AVAILABLE:
        return []
    try:
        from gernas.adapters.agent_framework import gernas_middleware
        return gernas_middleware(agent_id, agent_type=_AGENT_TYPES.get(agent_id, "worker"), provider=LLM_PROVIDER,
                                 mcp_server=mcp_server_id, trace_functions=mcp_server_id is not None)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Gernas MAF middleware unavailable: %s", exc)
        return []


def a2a_interceptor() -> Any:
    """A2A client interceptor that propagates trace context (None if unavailable)."""
    if not AVAILABLE:
        return None
    try:
        from gernas.adapters.a2a import GernasA2AInterceptor
        return GernasA2AInterceptor()
    except Exception as exc:  # noqa: BLE001
        _log.warning("Gernas A2A interceptor unavailable: %s", exc)
        return None


def asgi_middleware() -> Any:
    """The ASGI server middleware class (None if unavailable)."""
    if not AVAILABLE:
        return None
    from gernas.adapters.asgi import GernasASGIMiddleware
    return GernasASGIMiddleware


# ── raw OpenAI-compatible chat calls (summarizer, cache judge, entity extractor) ──

def _response_json(resp: Any) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _usage(resp: Any) -> tuple[int | None, int | None]:
    usage = _response_json(resp).get("usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def _annotate(template_id: str, resp: Any) -> None:
    """Prompt template and stop reason on the current ``llm.call`` span (no prompt text)."""
    if not AVAILABLE:
        return
    attributes: dict[str, Any] = {Ctx.PROMPT_TEMPLATE_ID: template_id}
    choices = (_response_json(resp).get("choices") or []) if resp is not None else []
    if choices and choices[0].get("finish_reason"):
        attributes[Ctx.STOP_REASON] = str(choices[0]["finish_reason"])
    gernas.annotate(attributes)


async def _chat_post(client: Any, url: str, *, json: dict, headers: dict, model: str, template_id: str) -> Any:
    resp = await client.post(url, json=json, headers=headers)
    _annotate(template_id, resp)
    return resp


def _chat_post_sync(client: Any, url: str, *, json: dict, headers: dict, model: str, template_id: str) -> Any:
    resp = client.post(url, json=json, headers=headers)
    _annotate(template_id, resp)
    return resp


chat_post = gernas.trace_llm(provider=LLM_PROVIDER, usage_from=_usage)(_chat_post) if AVAILABLE else _chat_post
chat_post_sync = (gernas.trace_llm(provider=LLM_PROVIDER, usage_from=_usage)(_chat_post_sync) if AVAILABLE
                  else _chat_post_sync)


def business_outcome(result: Any) -> str:
    """Use-case outcome for Task Success Rate: blocked → failure, pending HITL → partial."""
    if getattr(result, "blocked", False):
        return "failure"
    if getattr(result, "hitl_pending", False):
        return "partial"
    return "success" if getattr(result, "answer", None) else "failure"
