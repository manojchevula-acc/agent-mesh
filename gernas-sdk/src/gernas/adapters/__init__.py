"""Framework adapters. Import the specific module so optional dependencies stay optional.

All adapters map framework-native execution onto the same GERNAS spans and attributes:

* ``gernas.adapters.agent_framework``  — Microsoft Agent Framework middlewares (agent-framework)
* ``gernas.adapters.langchain``        — LangChain callback handler (langchain-core)
* ``gernas.adapters.langgraph``        — LangGraph graphs: run → ``agent.run``, node → ``agent.step``
* ``gernas.adapters.a2a``              — A2A client interceptor for cross-agent propagation (a2a-sdk)
* ``gernas.adapters.mcp``              — MCP server-side context continuation (fastmcp / mcp)
* ``gernas.adapters.asgi``             — inbound HTTP context extraction (no dependency)
* ``gernas.adapters.httpx``            — outbound HTTP context injection (no dependency)
"""
