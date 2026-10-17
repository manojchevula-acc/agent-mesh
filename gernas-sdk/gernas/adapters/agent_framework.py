"""Microsoft Agent Framework (MAF) adapter — maps MAF execution onto the common GERNAS model.

| MAF concept                    | GERNAS span   | Middleware                  |
|--------------------------------|---------------|-----------------------------|
| agent invocation               | ``agent.run`` | :class:`GernasAgentMiddleware` |
| chat-client request            | ``llm.call``  | :class:`GernasChatMiddleware`  |
| function / MCP tool execution  | ``tool.call`` | :class:`GernasFunctionMiddleware` |
| workflow executor              | ``agent.step``| ``@gernas.trace_step`` on the executor handler |

    from gernas.adapters.agent_framework import gernas_middleware
    Agent(client, name="DataAgent", middleware=[*gernas_middleware("data_agent", agent_type="worker",
                                                                    provider="azure_openai", mcp_server="crm_mcp")])

Requires ``agent-framework`` (``pip install gernas-telemetry[agent-framework]``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

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
from gernas.decorators.tool import ToolSpec

Next = Callable[[], Awaitable[None]]


class _AgentSpec(AgentSpec):
    def start(self, span: Any, call: Call) -> None:
        context: AgentContext = call.args[0]
        call.state["payload"] = [getattr(m, "text", str(m)) for m in (getattr(context, "messages", None) or [])]
        super().start(span, call)


class GernasAgentMiddleware(AgentMiddleware):
    """One ``agent.run`` span per MAF agent invocation."""

    def __init__(self, agent_id: str, *, agent_type: str, version: str | None = None) -> None:
        def agent_name(context: AgentContext, call_next: Next) -> str:
            return getattr(context.agent, "name", None) or agent_id

        self._run = wrap(self._invoke, _AgentSpec(agent_id, agent_name, version, agent_type, "agent_framework"))

    @staticmethod
    async def _invoke(context: AgentContext, call_next: Next) -> Any:
        await call_next()
        return context.result

    async def process(self, context: AgentContext, call_next: Next) -> None:
        await self._run(context, call_next)


class _ChatSpec(LLMSpec):
    def _model(self, call: Call) -> str | None:
        context: ChatContext = call.args[0]
        options = getattr(context, "options", None) or {}
        model = (options.get("model_id") or options.get("model")) if isinstance(options, dict) else None
        client = getattr(context, "client", None)
        model = model or self.model_id or getattr(client, "model_id", None) or getattr(client, "model", None)
        return None if model is None else str(model)


class GernasChatMiddleware(ChatMiddleware):
    """One ``llm.call`` span per chat-client request (tokens from ``usage_details``)."""

    def __init__(
        self,
        *,
        provider: str,
        deployment_id: str | None = None,
        prompt_template_id: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._run = wrap(
            self._invoke, _ChatSpec(None, provider, deployment_id, prompt_template_id, prompt_version, None)
        )

    @staticmethod
    async def _invoke(context: ChatContext, call_next: Next) -> Any:
        await call_next()
        return context.result

    async def process(self, context: ChatContext, call_next: Next) -> None:
        await self._run(context, call_next)


class _FunctionSpec(ToolSpec):
    def start(self, span: Any, call: Call) -> None:
        arguments = getattr(call.args[0], "arguments", None)
        dump = getattr(arguments, "model_dump", None)
        call.state["payload"] = dump() if callable(dump) else arguments
        super().start(span, call)


def _function_name(context: FunctionInvocationContext, call_next: Next) -> str:
    return str(getattr(context.function, "name", None) or "function")


class GernasFunctionMiddleware(FunctionMiddleware):
    """One ``tool.call`` span per real tool execution (``tool_type="mcp"`` with an MCP server)."""

    def __init__(self, *, mcp_server: str | None = None) -> None:
        tool_type = "mcp" if mcp_server else "function"
        self._run = wrap(self._invoke, _FunctionSpec(_function_name, tool_type, None, None, mcp_server, None))

    @staticmethod
    async def _invoke(context: FunctionInvocationContext, call_next: Next) -> Any:
        await call_next()
        return context.result

    async def process(self, context: FunctionInvocationContext, call_next: Next) -> None:
        await self._run(context, call_next)


def disable_native_telemetry() -> None:
    """Switch off Agent Framework's built-in OTel instrumentation for this process.

    MAF otherwise emits its own ``invoke_agent`` / ``chat`` / ``execute_tool`` /
    ``workflow.*`` spans (and, with ``ENABLE_SENSITIVE_DATA``, raw prompts) next to the
    GERNAS spans — a second instrumentation path for the same operations. Call once at
    startup when the Gernas middlewares are the instrumentation of record. Sticky
    (see ``agent_framework.observability.disable_instrumentation``).
    """
    from agent_framework.observability import disable_instrumentation

    disable_instrumentation()


def gernas_middleware(
    agent_id: str,
    *,
    agent_type: str,
    provider: str,
    mcp_server: str | None = None,
    version: str | None = None,
    trace_functions: bool = True,
) -> list[Any]:
    """Agent + Chat (+ Function) middlewares for ``Agent(middleware=[...])``.

    Set ``trace_functions=False`` when the agent's function tools already carry
    ``@gernas.trace_tool`` (avoids duplicate tool spans).
    """
    mws: list[Any] = [
        GernasAgentMiddleware(agent_id, agent_type=agent_type, version=version),
        GernasChatMiddleware(provider=provider),
    ]
    if trace_functions:
        mws.append(GernasFunctionMiddleware(mcp_server=mcp_server))
    return mws
