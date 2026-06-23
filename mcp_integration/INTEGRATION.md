# Integrating the RAG search tool into another agent

The `search_documents` tool retrieves cited FAB credit/regulatory policy context
(and optionally a full grounded answer). There are two ways another agent can call
it. Pick based on whether the agent framework speaks MCP.

| Your agent…                                                                  | Use                        | Section              |
| ----------------------------------------------------------------------------- | -------------------------- | -------------------- |
| Speaks MCP (Claude Code, LangChain-MCP, OpenAI Agents SDK, custom MCP client) | The MCP tool               | A (stdio) / B (HTTP) |
| Has no MCP support                                                            | The REST endpoint directly | C                    |

In all cases the actual retrieval runs in the **RAG service** — the agent never
needs that codebase, only network access (and, for stdio, this folder).

---

## A. MCP over stdio — agent co-located on the same machine

The client launches the server as a subprocess. Add this to the agent's MCP config
(`.mcp.json` for Claude Code; `claude_desktop_config.json` for Claude Desktop —
same block). Use the absolute path to this repo's venv Python.

```json
{
  "mcpServers": {
    "gernas-rag-search": {
      "command": "/abs/path/to/agent-mesh/.venv/bin/python",
      "args": ["-m", "mcp_integration.server"],
      "cwd": "/abs/path/to/agent-mesh"
    }
  }
}
```

Config (RAG URL/key, `RAG_GENERATE_ANSWER`) comes from this repo's `.env`.

---

## B. MCP over HTTP — agent is a separate service / host

Run the server once as a network service:

```bash
# in agent-mesh, with .env configured
MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_PORT=9000 \
  .venv/bin/python -m mcp_integration.server
# tool is now served at http://<this-host>:9000/mcp
```

(Or set `MCP_TRANSPORT=http`, `MCP_HOST`, `MCP_PORT` in `.env` and just run
`python -m mcp_integration.server`.)

The colleague's agent connects to the **URL** — no shared code:

```python
# pip install mcp
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def use_tool():
    async with streamablehttp_client("http://<your-host>:9000/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "search_documents",
                {"query": "pricing floor BB-rated 4yr AED loan",
                 "top_k": 5, "generate_answer": True},
            )
            print(result.content[0].text)
```

Any MCP-aware framework (LangChain `langchain-mcp-adapters`, OpenAI Agents SDK,
etc.) connects the same way and auto-exposes `search_documents` as a tool to its
model, which then calls it when it needs context.

> Note: `MCP_HOST=0.0.0.0` exposes the server on the network. Keep it on a trusted
> network/VPN, or front it with a reverse proxy that adds auth — the MCP layer
> itself doesn't authenticate the *agent* (the backend `X-API-Key` is between the
> tool and the RAG service, not between the agent and the tool).

---

## C. No MCP — call the REST endpoint directly

The MCP layer is just a wrapper. The underlying contract is one HTTP call, which
any agent/HTTP client can make:

```
POST http://<rag-host>:8000/api/v1/retrieve
Headers: X-API-Key: <API_KEY>   Content-Type: application/json
Body:
{
  "query": "pricing floor BB-rated 4-year AED loan",
  "top_k": 5,
  "generate_answer": false          // true → response includes a cited "answer"
}
```

Response (abridged):

```json
{
  "chunks": [
    {"text": "...", "source": "FAB_Credit_Pricing_Policy_v2.4",
     "clause_reference": "4.2.1", "score": 0.91,
     "effective_date": "2024-06-01", "freshness_warning": false,
     "parent_text": "..."}
  ],
  "total_results": 5,
  "freshness_warning_global": false,
  "answer": null
}
```

The colleague wraps that call as a tool/function in his own framework. This is the
lowest-friction option when his agent has no MCP support.

---

## Which to give your colleague

- Separate service + MCP-aware → **B** (give him the URL).
- Same machine + MCP-aware → **A**.
- No MCP support → **C** (give him the endpoint + key).

All three hit the same retrieval pipeline and honor `generate_answer`.
