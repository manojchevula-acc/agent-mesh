# MCP Integration

AgentMesh uses the **Model Context Protocol (MCP)** over StreamableHTTP transport to connect DataAgent and RAGAgent to their respective external services. Neither agent contains business logic — it lives entirely in the external MCP services.

---

## Files

| File | Purpose |
|---|---|
| `src/integrations/mcp_clients.py` | Factory functions for MCP tool instances |
| `src/agents/data_agent.py` | DataAgent — uses DataLayer MCP tool |
| `src/agents/rag_agent.py` | RAGAgent — uses RAG MCP tool |
| `a2a_server.py` | Holds MCP sessions open for MCP-backed nodes |
| `src/agents/node_registry.py` | `MCP_BACKED_NODES` set |

---

## MCP Client Factories

**File:** `src/integrations/mcp_clients.py`

### DataLayer MCP Tool

```python
make_datalayer_mcp_tool()
```

- Transport: `MCPStreamableHTTPTool`
- URL: `DATALAYER_MCP_URL` (default `http://127.0.0.1:9100/mcp`)
- **Auto-discovers 18 SQL-view tools** from the DataLayer service on connection
- No auth header by default (internal network)

### RAG MCP Tool

```python
make_rag_mcp_tool()
```

- Transport: `MCPStreamableHTTPTool`
- URL: `RAG_MCP_URL` (default `http://127.0.0.1:9000/mcp`)
- Single tool exposed: `search_documents`
- Optional `X-API-Key` header support (set via env var)

---

## Session Lifecycle

MCP-backed nodes require a **persistent session** for the node's lifetime (not per-request). `a2a_server.py` manages this:

```python
# For MCP-backed nodes (data_agent, rag_agent)
async with mcp_tool:                 # opens MCP session, discovers tools
    await start_a2a_server(agent)    # A2A server lifetime
# session closes when node shuts down
```

**Startup retry:** 8 attempts with exponential backoff before giving up.  
**Mid-session reconnect:** automatic reconnect on transport drops.

`MCP_BACKED_NODES = {"data_agent", "rag_agent"}` in `node_registry.py` controls which nodes get this treatment.

---

## DataLayer-as-a-Service (External)

**Port:** 9100  
**Not in this repo** — a separate FastMCP server.

Exposes **18 SQL-view tools** over structured banking data, for example:
- Customer account data
- Deal and pricing data
- Portfolio views
- Rate and product information

DataAgent uses MAF's tool runner to select and call these tools. `max_function_calls=4` prevents runaway recursive calls within one request.

---

## RAG-as-a-Service (External)

**Port:** 9000  
**Not in this repo** — a separate MCP server backed by a vector/hybrid retrieval stack.

Exposes a single tool:
- `search_documents(query: str)` → returns relevant policy document chunks + citations

RAGAgent calls this exactly once per request (`max_function_calls=1` by design). The LLM then synthesizes the retrieved chunks into a coherent answer with citations.

---

## Tool Call Logging

`ToolCallLogMiddleware` (wired into every agent via the agent factory) logs each MCP tool call with:
- Tool name
- Input arguments
- Output (truncated)
- Latency (ms)

These logs appear in `data/logs/agent_mesh.log` and are queryable via `GET /api/logs`.

---

## OTel Metrics for MCP

Custom counter from `src/observability/metrics.py`:
- `fab.mcp.calls.total` — incremented per MCP tool call, labelled by agent and tool name
