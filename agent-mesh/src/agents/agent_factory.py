import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from typing import Any, List, Optional
from agent_framework import Agent, AgentMiddleware
from agent_framework.openai import OpenAIChatCompletionClient
from src.config import Config
from src.middleware.audit_middleware import AuditMiddleware
from src.middleware.tool_call_logger import ToolCallLogMiddleware

def create_demo_agent(
    name: str,
    instructions: str,
    tools: Optional[List[Any]] = None,
    extra_middlewares: Optional[List[AgentMiddleware]] = None,
    log_path: str = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_function_calls: Optional[int] = None,
) -> Agent:
    """
    Creates and returns a Microsoft Agent Framework Agent powered by Groq.
    Optionally wires function/MCP/A2A tools the agent may call.

    max_function_calls: hard cap on the total number of tool invocations the
        agent may make while answering a single request. Enforced by the
        framework's function-invocation loop (not just the prompt), so it
        reliably stops runaway re-search / re-call behavior. None = unlimited.
    """
    # 1. Instantiate Groq client via OpenAI Chat Completions-compatible endpoint
    client_kwargs: dict[str, Any] = dict(
        model=model or Config.GROQ_MODEL,
        api_key=api_key or Config.GROQ_API_KEY,
        base_url=Config.LLM_BASE_URL,
    )
    if max_function_calls is not None:
        client_kwargs["function_invocation_configuration"] = {
            "max_function_calls": max_function_calls
        }
    client = OpenAIChatCompletionClient(**client_kwargs)

    # 2. Setup standard middleware:
    #    - AuditMiddleware: per agent-invocation audit trail.
    #    - ToolCallLogMiddleware: per REAL tool-call ground truth (fires only on
    #      actual MCP invocations), logging call_index + duplicate detection so we
    #      can see definitively whether a second real retrieval happened.
    audit = AuditMiddleware(log_path=log_path)
    tool_logger = ToolCallLogMiddleware(agent_name=name)

    middlewares = [audit, tool_logger]
    if extra_middlewares:
        middlewares.extend(extra_middlewares)

    # 3. Create Agent
    agent_kwargs: dict[str, Any] = dict(
        client=client,
        name=name,
        instructions=instructions,
        middleware=middlewares,
    )
    if tools:
        agent_kwargs["tools"] = tools
    return Agent(**agent_kwargs)
