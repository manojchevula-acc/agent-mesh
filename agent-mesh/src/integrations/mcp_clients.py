"""MCP client connectors for external services.

Uses the ``mcp`` library directly (no langchain-mcp-adapters dependency) to
avoid version-compatibility issues between the two packages.

Each connector opens a streamable-HTTP MCP session, auto-discovers the remote
server's tools, wraps them as LangChain ``StructuredTool`` instances, and
returns ``(wrapper, tools_list)``.  The A2A server holds the wrapper alive for
the node's lifetime and calls ``wrapper.__aexit__`` on shutdown.
"""
import sys
import pathlib
from contextlib import AsyncExitStack
from typing import Any, Optional

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from langchain_core.tools import StructuredTool
from pydantic import create_model, Field

from src.config import Config


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_JSON_TO_PYTHON: dict = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_langchain_tool(session: ClientSession, mcp_tool) -> StructuredTool:
    """Converts one MCP ToolDef to a callable LangChain StructuredTool."""
    name: str = mcp_tool.name
    description: str = mcp_tool.description or name
    schema: dict = getattr(mcp_tool, "inputSchema", None) or {}
    props: dict = schema.get("properties", {})
    required_fields: set = set(schema.get("required", []))

    pydantic_fields: dict = {}
    for fname, fschema in props.items():
        py_type = _JSON_TO_PYTHON.get(fschema.get("type", "string"), Any)
        fdesc: str = fschema.get("description", "")
        if fname in required_fields:
            pydantic_fields[fname] = (py_type, Field(description=fdesc))
        else:
            pydantic_fields[fname] = (py_type, Field(default=None, description=fdesc))

    args_model = create_model(f"{name}Args", **pydantic_fields) if pydantic_fields else None

    async def _call(**kwargs: Any) -> str:
        result = await session.call_tool(name, kwargs)
        texts = [c.text for c in result.content if hasattr(c, "text") and c.text]
        return "\n".join(texts) if texts else "(no output)"

    _call.__name__ = name
    extra = {"args_schema": args_model} if args_model else {}
    return StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=description,
        **extra,
    )


# ---------------------------------------------------------------------------
# Session wrapper
# ---------------------------------------------------------------------------

class _MCPSessionWrapper:
    """Owns an MCP streamable-HTTP session and exposes its tools as LangChain tools.

    Lifecycle::

        wrapper = _MCPSessionWrapper(url)
        await wrapper.connect()
        tools = wrapper.get_tools()
        # … serve …
        await wrapper.__aexit__(None, None, None)  # clean close
    """

    def __init__(self, url: str, headers: Optional[dict] = None):
        self._url = url
        self._headers = headers or {}
        self._stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None
        self._tools: list = []

    async def connect(self) -> "_MCPSessionWrapper":
        # Pass headers only if the installed mcp version supports the kwarg.
        import inspect
        _sc_params = inspect.signature(streamable_http_client).parameters
        _kwargs = {"headers": self._headers} if (self._headers and "headers" in _sc_params) else {}
        # Older mcp versions return (read, write); newer return (read, write, get_session_id).
        transport = await self._stack.enter_async_context(
            streamable_http_client(self._url, **_kwargs)
        )
        read, write = transport[0], transport[1]
        self._session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        # CancelledError (BaseException in Python 3.8+) is raised by anyio when the
        # MCP server is not reachable and the request times out. Convert to a plain
        # ConnectionError so the retry loop in a2a_server.py (except Exception) catches it.
        import asyncio
        try:
            await self._session.initialize()
            tools_result = await self._session.list_tools()
        except asyncio.CancelledError:
            raise ConnectionError(
                f"MCP server at {self._url!r} did not respond — is the service running?"
            )
        self._tools = [_build_langchain_tool(self._session, t) for t in tools_result.tools]
        return self

    async def __aexit__(self, *args) -> None:
        await self._stack.aclose()

    def get_tools(self) -> list:
        return list(self._tools)


# ---------------------------------------------------------------------------
# Public connectors
# ---------------------------------------------------------------------------

async def connect_datalayer_mcp() -> tuple:
    """Returns (wrapper, tools_list). Caller must call wrapper.__aexit__ on shutdown."""
    wrapper = _MCPSessionWrapper(Config.DATALAYER_MCP_URL)
    await wrapper.connect()
    return wrapper, wrapper.get_tools()


async def connect_rag_mcp() -> tuple:
    """Returns (wrapper, tools_list). Caller must call wrapper.__aexit__ on shutdown."""
    headers = {"X-API-Key": Config.RAG_API_KEY} if Config.RAG_API_KEY else {}
    wrapper = _MCPSessionWrapper(Config.RAG_MCP_URL, headers=headers)
    await wrapper.connect()
    return wrapper, wrapper.get_tools()


# node name -> async connector function
MCP_CONNECTORS = {
    "data_agent": connect_datalayer_mcp,
    "rag_agent":  connect_rag_mcp,
}
