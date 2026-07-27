# MCP Hub Plan

## Context

Currently each agent node that needs MCP tools (data_agent → :9100, rag_agent → :9000) holds a direct `MCPStreamableHTTPTool` connection to its respective MCP server. This means:
- Agents must know server URLs at startup
- Adding a new MCP server requires code changes in `mcp_clients.py` + `node_registry.py`
- No central place to inspect, gate, or version-control which MCP servers exist

The goal is a **MCP Hub**: a single MCP server that all agents connect to. The hub internally connects to downstream MCP servers, aggregates their tools (namespaced by server name), and routes tool calls back to the correct server. Clients only ever know one URL (`http://127.0.0.1:9200/mcp`).

---

## Why Not MAF Native

The installed `agent_framework` package (`_mcp.py`) exposes only three transport clients (`MCPStdioTool`, `MCPStreamableHTTPTool`, `MCPWebsocketTool`) — no hub, proxy, aggregator, or registry class exists. Confirmed via full grep of `.venv313/Lib/site-packages/agent_framework/`. We build the hub ourselves using **FastMCP** (already a project dependency used for the DataLayer and RAG servers).

---

## Architecture

```
agent (MCPStreamableHTTPTool → :9200/mcp)
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │              MCP Hub  (:9200)               │
  │  Registry: {datalayer: :9100, rag: :9000}   │
  │  Tools exposed:                             │
  │    datalayer__customer_360                  │
  │    datalayer__pricing_recommendation …      │
  │    rag__search_documents                    │
  └──────────────┬──────────────────────────────┘
                 │ routes by prefix
        ┌────────┴────────┐
        ▼                 ▼
  DataLayer MCP      RAG MCP
    (:9100)           (:9000)
```

---

## Implementation Plan

### Step 1 — MCP Hub server (`src/mcp_hub/hub.py`)

Use `fastmcp` to create a hub server. Since downstream servers are external HTTP processes we use a **proxy/aggregation** approach:

```python
# src/mcp_hub/hub.py
from fastmcp import FastMCP
from fastmcp.client import Client as MCPClient

class MCPHub:
    """Aggregates downstream MCP servers into one unified endpoint."""

    def __init__(self, registry: dict[str, str]):
        # registry = {"datalayer": "http://127.0.0.1:9100/mcp", "rag": "http://127.0.0.1:9000/mcp"}
        self.registry = registry
        self.mcp = FastMCP("mcp-hub")
        self._backend_clients: dict[str, MCPClient] = {}

    async def startup(self):
        for name, url in self.registry.items():
            client = MCPClient(url)
            await client.__aenter__()
            tools = await client.list_tools()
            for tool in tools:
                self._register_proxy_tool(name, url, tool)
            self._backend_clients[name] = client

    def _register_proxy_tool(self, server_name, url, tool):
        # Namespaced tool: "datalayer__customer_360"
        prefixed_name = f"{server_name}__{tool.name}"
        async def proxy_fn(**kwargs):
            client = self._backend_clients[server_name]
            return await client.call_tool(tool.name, kwargs)
        proxy_fn.__name__ = prefixed_name
        self.mcp.tool(name=prefixed_name, description=tool.description)(proxy_fn)
```

Hub startup connects to each downstream server, pulls `tools/list`, and re-registers every tool under `{server}__{tool}` on its own FastMCP instance.

### Step 2 — Hub registry config (`src/mcp_hub/registry.py`)

```python
# src/mcp_hub/registry.py
from src.config import Config

HUB_SERVER_REGISTRY: dict[str, str] = {
    "datalayer": Config.DATALAYER_MCP_URL,   # http://127.0.0.1:9100/mcp
    "rag":       Config.RAG_MCP_URL,          # http://127.0.0.1:9000/mcp
}
```

Adding a new MCP server = one new line here. No agent code changes needed.

### Step 3 — Hub entrypoint (`src/mcp_hub/server.py`)

```python
# src/mcp_hub/server.py
import asyncio, uvicorn
from .hub import MCPHub
from .registry import HUB_SERVER_REGISTRY

async def main():
    hub = MCPHub(HUB_SERVER_REGISTRY)
    await hub.startup()
    app = hub.mcp.streamable_http_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=9200)
    await uvicorn.Server(config).serve()

if __name__ == "__main__":
    asyncio.run(main())
```

### Step 4 — Add `MCP_HUB_URL` to config (`src/config.py`)

```python
MCP_HUB_URL: str = os.getenv("MCP_HUB_URL", "http://127.0.0.1:9200/mcp")
```

### Step 5 — Update `src/integrations/mcp_clients.py`

Replace two separate tool factories with a **single hub tool factory**:

```python
# Before: two factories, agents know server URLs
def make_datalayer_mcp_tool(): ...
def make_rag_mcp_tool(): ...
MCP_TOOL_FACTORIES = {"data_agent": make_datalayer_mcp_tool, "rag_agent": make_rag_mcp_tool}

# After: one hub tool, agents know only the hub URL
def make_hub_mcp_tool():
    return MCPStreamableHTTPTool(name="hub", url=Config.MCP_HUB_URL)

MCP_TOOL_FACTORIES = {
    "data_agent": make_hub_mcp_tool,
    "rag_agent":  make_hub_mcp_tool,
}
```

### Step 6 — Launcher (`start_mcp_hub.py`)

```python
# start_mcp_hub.py (new top-level script)
from src.mcp_hub.server import main
import asyncio
asyncio.run(main())
```

Start order: downstream servers first → hub → agents.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `src/mcp_hub/__init__.py` | Create | Package marker |
| `src/mcp_hub/hub.py` | Create | MCPHub aggregation class |
| `src/mcp_hub/registry.py` | Create | Server name → URL registry |
| `src/mcp_hub/server.py` | Create | Hub uvicorn entrypoint |
| `start_mcp_hub.py` | Create | Top-level launch script |
| `src/config.py` | Modify | Add `MCP_HUB_URL` (port 9200) |
| `src/integrations/mcp_clients.py` | Modify | Replace two factories with one hub factory |

No changes needed to agent classes, workflow, orchestrator, or node_registry.

---

## Verification

1. Start downstream servers: `python start_datalayer.py` (:9100), `python start_rag.py` (:9000)
2. Start hub: `python start_mcp_hub.py` (:9200)
3. Curl hub tools list:
   ```
   curl -X POST http://127.0.0.1:9200/mcp -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
   ```
   Should return all tools prefixed with `datalayer__` and `rag__`
4. Start agents: `python a2a_server.py data_agent` — connects to hub, not direct servers
5. Send a real query through the mesh and verify tool calls in logs (`src/middleware/tool_call_logger.py`) show `datalayer__*` tool names
6. Kill one downstream server → hub returns graceful error; other server's tools still work
