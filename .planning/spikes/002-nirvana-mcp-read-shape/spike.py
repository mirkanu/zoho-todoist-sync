"""
Spike 002: what does the actual task JSON shape look like via MCP?
Compares against what the sync worker will need: GTD state, Focus flag,
due date, project/tags, completion status.

Run: ../001-nirvana-mcp-auth-headless/venv/bin/python spike.py
"""
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

NIRVANA_MCP_URL = "https://mcp.nirvanahq.com/mcp"


async def main():
    pat = os.environ["NIRVANA_PAT"]
    headers = {"Authorization": f"Bearer {pat}"}

    async with streamablehttp_client(NIRVANA_MCP_URL, headers=headers) as (r, w, sid):
        async with ClientSession(r, w) as session:
            await session.initialize()

            print("=== get_tasks (state=next, limit small) ===")
            result = await session.call_tool("get_tasks", {"state": "next", "limit": 3})
            for c in result.content:
                print(c.text if hasattr(c, "text") else c)

            print("\n=== get_tasks (state=focus) ===")
            result = await session.call_tool("get_tasks", {"state": "focus"})
            for c in result.content:
                print(c.text if hasattr(c, "text") else c)

            print("\n=== get_tasks (overdue=true) ===")
            result = await session.call_tool("get_tasks", {"overdue": True, "limit": 3})
            for c in result.content:
                print(c.text if hasattr(c, "text") else c)

            print("\n=== get_tags ===")
            result = await session.call_tool("get_tags", {})
            for c in result.content:
                print(c.text if hasattr(c, "text") else c)


if __name__ == "__main__":
    asyncio.run(main())
