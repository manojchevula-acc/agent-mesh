"""Microsoft Agent Framework (MAF) middlewares → GERNAS spans.

Add them to any ``agent_framework.Agent``::

    from gernas.adapters.agent_framework import gernas_middleware
    Agent(client, name="data_agent", middleware=[*gernas_middleware("data_agent", mcp_server_id="datalayer")])

* :class:`GernasAgentMiddleware` → ``agent.run`` per agent invocation (run id,
  termination reason, loop/step/retry detection);
* :class:`GernasChatMiddleware` → ``llm.call`` per model request (tokens from
  ``usage_details``, finish reason, cost);
* :class:`GernasFunctionMiddleware` → ``tool.{name}`` per tool call, or
  ``mcp.call`` when the agent's tools come from an MCP server.

Built on the framework's public middleware API — no auto-instrumentation package.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)

from gernas.decorators._core import Call, wrap
from gernas.decorators.agent import AgentSpec
from gernas.decorators.llm import LLMSpec
from gernas.decorators.tool import MCPSpec, ToolSpec
from gernas.utils.hashing import hash_inputs

Next = Callable[[], Awaitable[None]]


def _args_hash(arguments: Any) -> str:
    try:
        if hasattr(arguments, "model_dump"):
            arguments = arguments.model_dump()
        return hash_inputs(json.loads(json.dumps(arguments, default=str, sort_keys=True)))
    except Exception:  # noqa: BLE001
        return hash_inputs(str(arguments))


def _messages_hash(messages: Any) -> str:
    try:
        return hash_inputs([getattr(m, "text", str(m)) for m in (messages or [])])
    except Exception:  # noqa: BLE001
        return hash_inputs(str(messages))


class _AdapterAgentSpec(AgentSpec):
    def start(self, span: Any, call: Call) -> None:
        context: AgentContext = call.args[0]
        call.state["input_hash"] = _messages_hash(getattr(context, "messages", None))
        super().start(span, call)


class GernasAgentMiddleware(AgentMiddleware):
    """One ``agent.run`` span per MAF agent invocation."""

    def __init__(self, agent_id: str | None = None, *, version: str | None = None,
                 agent_type: str | None = None) -> None:
        def framework_name(context: AgentContext, call_next: Next) -> str:
            return getattr(context.agent, "name", None) or agent_id or "agent"

        spec = _AdapterAgentSpec(agent_id or framework_name, framework_name, version, "agent_framework",
                                 agent_type, None, None)
        self._run = wrap(self._invoke, spec)

    @staticmethod
    async def _invoke(context: AgentContext, call_next: Next) -> Any:
        await call_next()
        return context.result

    async def process(self, context: AgentContext, call_next: Next) -> None:
        await self._run(context, call_next)


class _AdapterLLMSpec(LLMSpec):
    def _model(self, call: Call) -> str:
        context: ChatContext = call.args[0]
        options = getattr(context, "options", None) or {}
        model = (options.get("model_id") or options.get("model")) if isinstance(options, dict) else None
        client = getattr(context, "client", None)
        model = model or self.model or getattr(client, "model_id", None) or getattr(client, "model", None)
        return str(model) if model else "unknown"


class GernasChatMiddleware(ChatMiddleware):
    """One ``llm.call`` span per chat-client request."""

    def __init__(self, provider: str | None = None, *, deployment_id: str | None = None,
                 prompt_template_id: str | None = None, prompt_version: str | None = None) -> None:
        spec = _AdapterLLMSpec(None, provider, deployment_id, "chat", prompt_template_id, prompt_version,
                               None, None, None)
        self._spec = spec
        self._run = wrap(self._invoke, spec)

    @staticmethod
    async def _invoke(context: ChatContext, call_next: Next) -> Any:
        await call_next()
        return context.result

    async def process(self, context: ChatContext, call_next: Next) -> None:
        await self._run(context, call_next)


class _AdapterToolSpec(ToolSpec):
    def start(self, span: Any, call: Call) -> None:
        call.state["input_hash"] = _args_hash(getattr(call.args[0], "arguments", None))
        super().start(span, call)


class _AdapterMCPSpec(MCPSpec):
    def start(self, span: Any, call: Call) -> None:
        call.state["input_hash"] = _args_hash(getattr(call.args[0], "arguments", None))
        super().start(span, call)


def _function_name(context: FunctionInvocationContext, call_next: Next) -> str:
    return str(getattr(context.function, "name", None) or "function")


class GernasFunctionMiddleware(FunctionMiddleware):
    """One span per real tool execution (``mcp.call`` when ``mcp_server_id`` is set)."""

    def __init__(self, mcp_server_id: str | None = None, *, transport: str | None = "streamable_http",
                 tool_type: str = "function") -> None:
        if mcp_server_id:
            spec: Any = _AdapterMCPSpec(mcp_server_id, "tool", _function_name, transport)
        else:
            spec = _AdapterToolSpec(_function_name, tool_type, None, None)
        self._run = wrap(self._invoke, spec)

    @staticmethod
    async def _invoke(context: FunctionInvocationContext, call_next: Next) -> Any:
        await call_next()
        return context.result

    async def process(self, context: FunctionInvocationContext, call_next: Next) -> None:
        await self._run(context, call_next)


def gernas_middleware(agent_id: str | None = None, *, provider: str | None = None,
                      mcp_server_id: str | None = None, version: str | None = None,
                      agent_type: str | None = None) -> list[Any]:
    """The three middlewares, ready to splat into ``Agent(middleware=[...])``."""
    return [
        GernasAgentMiddleware(agent_id, version=version, agent_type=agent_type),
        GernasChatMiddleware(provider),
        GernasFunctionMiddleware(mcp_server_id),
    ]
