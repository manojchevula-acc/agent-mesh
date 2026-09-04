"""One-shot LLM calls, isolated from the rest of the codebase.

Two responsibilities:

  complete_with_tools()  — ONE model step with tool declarations, returning the
      assistant message WITHOUT executing any tool. This codebase runs its own tool
      step (circuit breaker §9.3, verbatim-question enforcement, audit log, and the
      error->tool-result self-correction loop), so the client's own function-invocation
      loop must stay out of the way.

  complete() / acomplete() — plain prompt -> text, for the intent classifier and the
      dynamic SQL generator. complete() is the SYNC form: it is called from tool
      bodies, which ToolsExecutor already runs on a worker thread via asyncio.to_thread,
      so a private event loop there is safe and keeps async out of the dynamic pipeline.
"""

from __future__ import annotations

import asyncio

from agent_framework import ChatOptions, Content, Message

from sql_agent.llm.factory import Step, get_llm, log_usage


async def complete_with_tools(step: Step, messages: list[Message],
                              tools: list, tool_choice: str):
    """Returns the ChatResponse for ONE model step. No tool is executed.

    Nothing here suppresses tool execution: the factory hands back a Raw* client
    (RawOpenAIChatCompletionClient), which has no FunctionInvocationLayer in its MRO,
    so a tool call simply comes back as function_call content. That is the whole
    mechanism.

    tool_choice: "required" (LangChain's "any") | "auto" | "none". ToolMode is a
    TypedDict, so it is passed as {"mode": ...}, not a bare string.
    """
    client, options = get_llm(step)
    response = await client.get_response(
        messages,
        options=ChatOptions(tools=tools, tool_choice={"mode": tool_choice}, **options),
    )
    log_usage(step, response)
    return response          # .messages / .text / .usage_details


async def acomplete(step: Step, prompt: str) -> str:
    client, options = get_llm(step)
    response = await client.get_response(
        [Message("user", [Content.from_text(prompt)])],
        options=ChatOptions(**options),
    )
    log_usage(step, response)
    return response.text or ""


def complete(step: Step, prompt: str) -> str:
    """Sync wrapper. MUST NOT be called from the event loop thread — it is for tool
    bodies and other code already running on a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(acomplete(step, prompt))
    raise RuntimeError(
        "llm.complete() called from the event loop; use acomplete() instead."
    )
