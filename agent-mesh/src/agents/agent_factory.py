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

    # 2. Bind audit callback to the LLM so it fires on every LLM call.
    callbacks = [AuditCallbackHandler(agent_name=name, log_path=log_path)]
    llm = llm.with_config({"callbacks": callbacks})

    # 3. Create LangGraph ReAct agent with system prompt injected via
    #    state_modifier (applied before every LLM call).
    return create_react_agent(
        llm,
        tools or [],
        state_modifier=SystemMessage(content=instructions),
        checkpointer=None,
    )
