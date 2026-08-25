import asyncio
from fastmcp import Client

async def main():
    async with Client("mcp_server.py") as mcp_client:
        result = await mcp_client.call_tool(
            "get_deals_for_customer",
            {"customer_id": "CUST001"}
        )

        print(result)

asyncio.run(main())