"""Microsoft Agent Framework agent instrumented with the Gernas SDK middleware.

The chat client is a local stub (first turn requests the ``get_rate`` tool, second
turn answers) so the example runs offline; replace it with your real client.

    pip install gernas-telemetry[agent-framework]
    set GERNAS_SERVICE_NAME=fx-agent GERNAS_USE_CASE_ID=UC-FX-001 GERNAS_APPLICATION_ID=fx-app ^
        GERNAS_BUSINESS_DOMAIN=treasury GERNAS_ENVIRONMENT=dev GERNAS_EXPORTER=console
    python examples/maf_agent.py
"""

from __future__ import annotations

import asyncio

from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)

import gernas
from gernas.adapters.agent_framework import gernas_middleware


class StubChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.model_id = "gpt-4o"
        self._turn = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def respond() -> ChatResponse:
            self._turn += 1
            if self._turn % 2 == 1:
                call = Content.from_function_call(
                    call_id=f"c{self._turn}", name="get_rate", arguments={"pair": "USDAED"}
                )
                return ChatResponse(
                    messages=[Message("assistant", [call])],
                    usage_details={"input_token_count": 80, "output_token_count": 12},
                )
            return ChatResponse(
                messages=[Message("assistant", ["USD/AED is 3.6725"])],
                finish_reason="stop",
                usage_details={"input_token_count": 140, "output_token_count": 9},
            )

        return respond()


@tool
def get_rate(pair: str) -> str:
    """Return the spot rate for a currency pair."""
    return "3.6725"


@gernas.trace_step("fx_quote", step_type="workflow_executor")
async def fx_quote_executor(agent: Agent, question: str) -> str:
    return (await agent.run(question)).text


@gernas.trace_use_case(channel="api", request_type="fx_quote")
async def handle(question: str) -> str:
    agent = Agent(
        StubChatClient(),
        name="FxAgent",
        tools=[get_rate],
        middleware=gernas_middleware("fx_agent", agent_type="worker", provider="azure_openai"),
    )
    with gernas.request_context(session_id="sess-fx-1"):
        return await fx_quote_executor(agent, question)


def main() -> None:
    handle_ = gernas.init_gernas()
    try:
        print(asyncio.run(handle("What is USD/AED?")))
    finally:
        handle_.shutdown()


if __name__ == "__main__":
    main()
