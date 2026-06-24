"""MCP server exposing GERNAS RAG retrieval as an agent tool.

Run as a network service (streamable HTTP for remote agents):

    MCP_TRANSPORT=http python -m mcp_integration.server

Configuration is read from the project ``.env`` (same file the backend uses):
    MCP_HOST             Host to bind the MCP server (default: 127.0.0.1)
    MCP_PORT             Port to bind the MCP server (default: 9000)
    RAG_GENERATE_ANSWER  Default for the tool's generate_answer flag (true/false).

The retrieval pipeline (embedder → Qdrant → reranker → LLM) is initialised lazily
on the first tool call using the same settings/config as the REST backend.  This
makes the MCP server fully self-contained — no separate REST API process is needed.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add src to sys.path so gernas_rag is importable from this standalone process.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Load project .env so this process shares the backend's config.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from mcp.server.fastmcp import FastMCP  # noqa: E402 (after path setup)

DEFAULT_GENERATE_ANSWER = os.getenv("RAG_GENERATE_ANSWER", "false").lower() in {
    "1",
    "true",
    "yes",
}

MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").lower()
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "9000"))

mcp = FastMCP("gernas-rag-search", host=MCP_HOST, port=MCP_PORT)

# ── Lazy pipeline singletons ───────────────────────────────────────────────────
_pipeline = None
_generator = None
_init_lock = asyncio.Lock()
_init_error: str | None = None


async def _ensure_pipeline():
    """Initialise the retrieval pipeline once on first use (thread-safe)."""
    global _pipeline, _generator, _init_error

    if _pipeline is not None:
        return _pipeline, _generator

    async with _init_lock:
        # Re-check after acquiring the lock in case another coroutine initialised first.
        if _pipeline is not None:
            return _pipeline, _generator
        if _init_error is not None:
            raise RuntimeError(f"Pipeline failed to initialise: {_init_error}")

        try:
            from gernas_rag.config.settings import get_settings
            from gernas_rag.embeddings.factory import get_embedder
            from gernas_rag.generation.generator import ResponseGenerator
            from gernas_rag.llm.factory import get_llm
            from gernas_rag.retrieval.pipeline import RetrievalPipeline
            from gernas_rag.vectordb.factory import get_vectordb

            settings = get_settings()
            embedder = get_embedder(settings.embedding)
            vectordb = get_vectordb(settings.vectordb)
            llm = get_llm(settings.llm)

            await vectordb.create_collection(
                settings.vectordb.collection_name, embedder.dense_dim
            )

            _pipeline = RetrievalPipeline(settings, embedder, vectordb)
            _generator = ResponseGenerator(settings, llm)
        except Exception as exc:
            _init_error = str(exc)
            raise

    return _pipeline, _generator


def _format_chunks(payload: dict) -> dict:
    """Reshape a RetrieveResponse.model_dump() into a compact, citation-friendly form.

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
    # Present only when the RAG pipeline generated an answer (generate_answer=True).
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
        generate_answer: If True, the RAG pipeline's LLM also composes a complete,
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

    try:
        pipeline, generator = await _ensure_pipeline()

        from gernas_rag.models.retrieval import RetrieveRequest

        request = RetrieveRequest(query=query, top_k=top_k, generate_answer=False)
        response = await pipeline.retrieve(request)

        if generate_answer and response.chunks:
            answer = await generator.generate(query, response.chunks)
            response = response.model_copy(update={"answer": answer})

        return _format_chunks(response.model_dump())

    except Exception as exc:
        return {"error": f"RAG pipeline error: {exc}"}


if __name__ == "__main__":
    if MCP_TRANSPORT in {"http", "streamable-http"}:
        # Network service: remote agents connect to http://MCP_HOST:MCP_PORT/mcp
        mcp.run(transport="streamable-http")
    elif MCP_TRANSPORT == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run()  # stdio (default — co-located clients spawn this as a subprocess)
