"""MCP server exposing GERNAS RAG retrieval as an agent tool.

Run standalone over stdio (for Claude Desktop / Claude Code / the example agent):

    python -m mcp_integration.server

Configuration is read from the project ``.env`` (same file the backend uses):
    RAG_API_URL          Base URL of the RAG service. Falls back to STREAMLIT_API_BASE,
                         then http://localhost:8000.
    RAG_API_KEY          Value sent as ``X-API-Key``. Falls back to the backend's API_KEY.
    RAG_GENERATE_ANSWER  Default for the tool's generate_answer flag (true/false).
"""

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load the project .env so this standalone process shares the backend's config.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# URL: prefer RAG_API_URL, else reuse the backend's STREAMLIT_API_BASE, else default.
RAG_API_URL = (
    os.getenv("RAG_API_URL")
    or os.getenv("STREAMLIT_API_BASE")
    or "http://localhost:8000"
).rstrip("/")
# Key: prefer RAG_API_KEY, else reuse the backend's API_KEY (the X-API-Key value).
RAG_API_KEY = os.getenv("RAG_API_KEY") or os.getenv("API_KEY", "")

# Per-call ``generate_answer`` wins; this only sets the default when the caller
# (agent) omits it. Set RAG_GENERATE_ANSWER=true in .env to default to full answers.
DEFAULT_GENERATE_ANSWER = os.getenv("RAG_GENERATE_ANSWER", "false").lower() in {
    "1",
    "true",
    "yes",
}

# Transport: "stdio" (default — co-located clients spawn this as a subprocess) or
# "http" (run as a network service so a remote agent connects to a URL). MCP_HOST/
# MCP_PORT apply to http; the tool is served at http://MCP_HOST:MCP_PORT/mcp.
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").lower()
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "9000"))

mcp = FastMCP("gernas-rag-search", host=MCP_HOST, port=MCP_PORT)


def _format_chunks(payload: dict) -> dict:
    """Reshape the raw /retrieve response into a compact, citation-friendly form.

    The agent reads ``results`` to ground its answer; each entry carries the
    source document and clause so the model can cite ``[source · clause]``.
    """
    chunks = payload.get("chunks", []) or []
    results = []
    seen: set[str] = set()
    for c in chunks:
        # Prefer the full parent section when available (small-to-big retrieval).
        text = c.get("parent_text") or c.get("text", "")
        # Sibling child chunks resolve to the same parent section — collapse those
        # duplicates so the agent isn't fed the same text several times.
        dedup_key = f"{c.get('source', '')}|{text[:200]}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        results.append(
            {
                "source": c.get("source", ""),
                "clause": c.get("clause_reference", ""),
                "section": c.get("section_heading", ""),
                "effective_date": c.get("effective_date", ""),
                "stale": bool(c.get("freshness_warning", False)),
                "score": round(float(c.get("score", 0.0)), 4),
                "text": text,
            }
        )
    out = {
        "results": results,
        "total_results": len(results),
        "freshness_warning": payload.get("freshness_warning_global", False),
        "latency_ms": payload.get("latency_ms"),
    }
    # Present only when the RAG service generated an answer (generate_answer=True).
    if payload.get("answer") is not None:
        out["answer"] = payload["answer"]
    return out


@mcp.tool()
async def search_documents(
    query: str, top_k: int = 5, generate_answer: bool = DEFAULT_GENERATE_ANSWER
) -> dict:
    """Search FAB credit & regulatory policy documents for grounded context.

    Call this whenever answering a question requires authoritative bank policy,
    regulatory, pricing, model-risk, or concentration-limit information — i.e.
    any time you need source-of-truth context you don't already have. Always
    returns cited document chunks (source + clause + effective date).

    Args:
        query: The natural-language question or topic to retrieve context for.
        top_k: How many chunks to return (1-20, default 5).
        generate_answer: If True, the RAG service's LLM also composes a complete,
            cited answer from the chunks, returned in the ``answer`` field. If
            False (default), only the chunks are returned and you should reason
            over them and cite ``[source · clause]`` yourself.

    Returns:
        ``{"results": [{source, clause, section, effective_date, stale, score,
        text}], "total_results", "freshness_warning", "latency_ms"}``, plus an
        ``answer`` string when ``generate_answer`` is True. On error,
        ``{"error": <message>}``.
    """
    top_k = max(1, min(int(top_k), 20))
    headers = {"X-API-Key": RAG_API_KEY} if RAG_API_KEY else {}

    try:
        async with httpx.AsyncClient(timeout=60 if generate_answer else 30) as client:
            response = await client.post(
                f"{RAG_API_URL}/api/v1/retrieve",
                json={
                    "query": query,
                    "top_k": top_k,
                    "generate_answer": generate_answer,
                },
                headers=headers,
            )
            response.raise_for_status()
            return _format_chunks(response.json())
    except httpx.HTTPStatusError as exc:
        return {
            "error": f"RAG service returned {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        }
    except httpx.HTTPError as exc:
        return {"error": f"Could not reach RAG service at {RAG_API_URL}: {exc}"}


if __name__ == "__main__":
    if MCP_TRANSPORT in {"http", "streamable-http"}:
        # Network service: remote agents connect to http://MCP_HOST:MCP_PORT/mcp
        mcp.run(transport="streamable-http")
    elif MCP_TRANSPORT == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run()  # stdio (default)
