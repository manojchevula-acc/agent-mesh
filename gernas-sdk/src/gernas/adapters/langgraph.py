"""LangGraph adapter: a compiled graph run is an ``agent.run``, each node an ``agent.step``.

LangGraph emits LangChain callbacks (nodes carry ``metadata.langgraph_node``), so this
is the LangChain handler with ``framework="langgraph"`` — one schema for both.

    from gernas.adapters.langgraph import instrument_graph
    app = instrument_graph(builder.compile(), agent_id="pricing_graph", agent_type="orchestrator",
                           provider="azure_openai")
    app.invoke({"question": "..."})

Requires ``langchain-core`` (installed with ``langgraph``).
"""

from __future__ import annotations

from typing import Any

from gernas.adapters.langchain import GernasCallbackHandler, instrument


def instrument_graph(
    graph: Any,
    *,
    agent_id: str,
    agent_type: str,
    provider: str | None = None,
    agent_name: str | None = None,
    agent_version: str | None = None,
    trace_tools: bool = True,
) -> Any:
    """Bind a GERNAS callback handler to a compiled LangGraph graph."""
    return instrument(
        graph,
        agent_id=agent_id,
        agent_type=agent_type,
        provider=provider,
        agent_name=agent_name,
        agent_version=agent_version,
        framework="langgraph",
        trace_tools=trace_tools,
    )


__all__ = ["instrument_graph", "GernasCallbackHandler"]
