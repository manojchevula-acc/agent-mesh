"""MCP integration for the GERNAS RAG layer.

Exposes the RAG ``/api/v1/retrieve`` endpoint as an MCP ``search_documents``
tool so that LLM agents can pull authoritative, cited policy/regulatory context
on demand. See ``server.py`` (the tool), ``client.py`` (a smoke test), and
``agent.py`` (an Anthropic agent that calls the tool autonomously).
"""
