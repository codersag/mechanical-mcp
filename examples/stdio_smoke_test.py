"""Start the server over stdio like an MCP client would, list tools and call two of them."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "acp_mcp_server.py")])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools (%d): %s" % (len(tools.tools), ", ".join(t.name for t in tools.tools)))
            res = await session.call_tool("acp_status", {})
            print("acp_status:", json.loads(res.content[0].text))
            if len(sys.argv) > 1:
                res = await session.call_tool("acp_inspect_cdb", {"path": sys.argv[1]})
                d = json.loads(res.content[0].text)
                print("inspect:", d["nodes"], "nodes,", d["elements"], "elements")


asyncio.run(main())
