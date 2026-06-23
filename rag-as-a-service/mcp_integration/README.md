# MCP integration — RAG search as an agent tool

Exposes the GERNAS RAG `POST /api/v1/retrieve` endpoint as an **MCP tool**
(`search_documents`) so LLM agents can pull cited, freshness-aware policy context
on demand — calling it *only when they need source-of-truth context*.

```
agent ──(MCP/stdio)──▶ server.py ──(HTTP /api/v1/retrieve)──▶ RAG service
```

| File         | Role                                                                       |
| ------------ | -------------------------------------------------------------------------- |
| `server.py`  | MCP server. Wraps `/api/v1/retrieve`, returns compact cited chunks.        |
| `client.py`  | Smoke test: lists tools and calls `search_documents` once.                 |
| `agent.py`   | Anthropic Claude agent that calls the tool autonomously in a tool-use loop. |
| `../.mcp.json`| Registers the server for Claude Code / Claude Desktop (no glue code).      |

## 1. Prerequisites

```bash
uv sync                       # installs mcp[cli] (already added to pyproject)
cp .env.example .env          # set API_KEY + RAG__LLM__GROQ_API_KEY
# Start the RAG service (embedded Qdrant needs no Docker):
uvicorn gernas_rag.main:app --app-dir src
# In another shell, ingest the sample corpus once:
python scripts/ingest_docs.py --path ./docs
```

The MCP server reads two env vars (must match the backend):

```bash
export RAG_API_URL=http://localhost:8000
export RAG_API_KEY=dev-secret-key-change-in-production   # == backend API_KEY
```

## 2. Smoke-test the tool (no LLM needed)

```bash
python -m mcp_integration.client
```

Expected: the tool list, then a JSON block of retrieved chunks with `source`,
`clause`, `score`, and `text`. If you see `{"error": ...}`, the RAG service is
down or the API key is wrong.

## 3. Run the autonomous agent

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m mcp_integration.agent "What is the pricing floor for a BB-rated 4-year AED loan?"
```

The agent decides on its own to call `search_documents`, reads the chunks, and
answers with `[source · clause]` citations. You'll see `→ calling search_documents(...)`
when it pulls context.

## 4. Use it inside Claude Code / Claude Desktop

`../.mcp.json` already registers the server. In **Claude Code**, run `/mcp` to
confirm `gernas-rag-search` is connected, then just ask a policy question — Claude
will call the tool when it needs context. For **Claude Desktop**, copy the
`mcpServers` block into `claude_desktop_config.json` (use the absolute path to the
venv Python; on macOS/Linux that's `.venv/bin/python`, on Windows `.venv/Scripts/python.exe`).

## Chunks vs. full answers (`generate_answer`)

`search_documents` takes a `generate_answer` flag:

- `generate_answer=false` (default) → returns only cited chunks; the agent reasons
  over them and cites `[source · clause]` itself.
- `generate_answer=true` → the RAG service's own LLM also composes a complete,
  cited answer, returned in an extra `answer` field.

The agent (or Claude Code) can pass the flag per call. To change the **default**
for every call without code changes, set an env var:

```bash
export RAG_GENERATE_ANSWER=true     # full answers by default
```

Test it directly:

```bash
# context only
python -c "import asyncio,sys,os;from mcp import ClientSession,StdioServerParameters;from mcp.client.stdio import stdio_client" # see client.py
# full answer via the API (no MCP needed):
curl -X POST http://localhost:8000/api/v1/retrieve \
  -H 'X-API-Key: dev-secret-key-change-in-production' -H 'Content-Type: application/json' \
  -d '{"query":"pricing floor BB-rated 4-year AED loan","top_k":5,"generate_answer":true}'
```

## Notes

- `top_k` is clamped to 1–20 to match the API contract.
- The server fails soft: HTTP/connection errors come back as `{"error": ...}`
  rather than crashing the agent.
