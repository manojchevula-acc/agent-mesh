"""Framework-neutral (custom Python) agent instrumented with the Gernas SDK.

Shows: agent run, workflow steps, LLM call (sync and streaming), tool call, MCP tool,
retrieval, guardrail, nested agent via delegation, async fan-out, a handled exception.
No network calls — the "model" is a stub returning a provider-shaped response.

    set GERNAS_SERVICE_NAME=pricing-assistant GERNAS_USE_CASE_ID=UC-PRICING-001 ^
        GERNAS_APPLICATION_ID=pricing-app GERNAS_BUSINESS_DOMAIN=retail_banking ^
        GERNAS_ENVIRONMENT=dev GERNAS_EXPORTER=console
    python examples/custom_agent.py
"""

from __future__ import annotations

import asyncio

import gernas


@gernas.trace_llm("gpt-4o", provider="azure_openai", prompt_template_id="pricing_answer", prompt_version="3")
async def call_model(messages: list[dict]) -> dict:
    await asyncio.sleep(0)
    return {
        "choices": [{"message": {"content": "0.75%"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 8},
    }


@gernas.trace_llm("gpt-4o", provider="azure_openai")
async def stream_model(messages: list[dict]):
    for token in ("0.", "75", "%"):
        await asyncio.sleep(0)
        yield {"delta": token}
    yield {"usage": {"prompt_tokens": 120, "completion_tokens": 3}, "finish_reason": "stop"}


@gernas.trace_tool("fx_rates", tool_type="api", api_operation="GET /rates", dependency="treasury")
async def get_rate(currency: str) -> dict:
    return {"currency": currency, "rate": 3.6725}


@gernas.trace_tool("customer_360", tool_type="mcp", dependency="datalayer")
async def customer_profile(customer_id: str) -> dict:
    return {"segment": "premier"}


@gernas.trace_retrieval(index_id="pricing_policies", strategy="hybrid", knowledge_domain="pricing")
async def search_policies(query: str, top_k: int = 5) -> list[dict]:
    return [{"id": "policy-12", "score": 0.87}]


@gernas.trace_guardrail("input_injection", risk_classification="prompt_injection")
def check_input(text: str) -> tuple[bool, str | None]:
    return ("ignore previous" in text.lower(), None)


@gernas.trace_agent("rag_agent", agent_type="worker")
async def rag_agent(question: str) -> list[dict]:
    return await search_policies(question)


@gernas.trace_delegation("rag_agent")
async def ask_rag_agent(question: str) -> list[dict]:
    return await rag_agent(question)


@gernas.trace_step("gather_context", step_type="plan_step")
async def gather_context(question: str, customer_id: str) -> list:
    return await asyncio.gather(get_rate("USD"), customer_profile(customer_id), ask_rag_agent(question))


@gernas.trace_agent("pricing_assistant", agent_type="orchestrator")
async def pricing_assistant(question: str, customer_id: str) -> str:
    if check_input(question)[0]:
        return "Request blocked."
    context = await gather_context(question, customer_id)
    reply = await call_model([{"role": "user", "content": question}, {"role": "system", "content": str(context)}])
    streamed = "".join([c.get("delta", "") async for c in stream_model([{"role": "user", "content": question}])])
    return reply["choices"][0]["message"]["content"] + " / " + streamed


@gernas.trace_tool("core_banking", tool_type="api", dependency="core_banking")
async def failing_tool() -> None:
    raise ConnectionError("core banking unavailable")


@gernas.trace_agent("balance_agent", agent_type="worker")
async def agent_with_handled_error() -> str:
    for _attempt in range(2):
        try:
            await failing_tool()
            return "live"
        except ConnectionError:
            gernas.record_retry()  # the failed tool.call span carries status ERROR + error_category
    return "core banking unavailable, please retry later"


@gernas.trace_use_case(channel="web", request_type="pricing_query", outcome_from=lambda r: "success")
async def handle_request(question: str, customer_id: str) -> str:
    with gernas.request_context(session_id="sess-001", task_id="task-001", user_id="u-42", principal_type="human"):
        answer = await pricing_assistant(question, customer_id)
        await agent_with_handled_error()
        return answer


def main() -> None:
    handle = gernas.init_gernas()
    try:
        print(asyncio.run(handle_request("What is the FX margin for a premier customer?", "C-1001")))
    finally:
        handle.shutdown()


if __name__ == "__main__":
    main()
