import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from typing import Any, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent

from src.config import Config
from src.middleware.audit_middleware import AuditCallbackHandler


def create_demo_agent(
    name: str,
    instructions: str,
    tools: Optional[List[Any]] = None,
    extra_middlewares: Optional[List[Any]] = None,
    log_path: str = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
):
    """
    Creates and returns a LangGraph ReAct agent powered by Groq via the
    OpenAI-compatible endpoint.

    The audit callback is bound to the LLM so every invocation is logged to
    the JSONL audit trail regardless of which code path calls ainvoke().
    """
    # 1. Instantiate LLM via OpenAI Chat Completions-compatible Groq endpoint.
    llm = ChatOpenAI(
        model=model or Config.GROQ_MODEL,
        api_key=api_key or Config.GROQ_API_KEY,
        base_url=Config.LLM_BASE_URL,
    )

    # 2. Create the ReAct agent from the raw LLM so create_react_agent can call
    #    llm.bind_tools() on the underlying ChatOpenAI instance directly.
    #    Wrapping the LLM with with_config() before this step produces a
    #    RunnableBinding that prevents bind_tools() from working, which means tool
    #    schemas are never sent to the Groq API and the model falls back to
    #    generating <function=...> text that Groq rejects with a 400.
    agent = create_react_agent(
        llm,
        tools or [],
        prompt=SystemMessage(content=instructions),
    )

    # 3. Attach the audit callback to the finished agent (not the LLM).
    #    LangChain propagates callbacks to all child runnables, so on_llm_start /
    #    on_llm_end still fire correctly inside the agent's LLM calls.
    callbacks = [AuditCallbackHandler(agent_name=name, log_path=log_path)]
    return agent.with_config({"callbacks": callbacks})
