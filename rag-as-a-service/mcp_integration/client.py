"""Smoke test for the MCP server: list tools, then call ``search_documents``.

    python -m mcp_integration.client

Requires the RAG service to be running (see README) and RAG_API_KEY to match
the backend ``API_KEY``. The server is launched as a subprocess over stdio.
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    # Launch the server with the *same* interpreter as `-m mcp_integration.server`
    # so it inherits this venv (and RAG_API_URL / RAG_API_KEY from the environment).
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_integration.server"],
        env=os.environ.copy(),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("\nAvailable Tools:")
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description.splitlines()[0]}")

            result = await session.call_tool(
                "search_documents",
                {"query": "What is the KYC / pricing policy?", "top_k": 5},
            )

            print("\nResult:")
            for block in result.content:
                if getattr(block, "type", None) == "text":
                    print(block.text)


if __name__ == "__main__":
    asyncio.run(main())
