"""
Spike 001: can a headless Python script authenticate to Nirvana's MCP server
using a personal access token (no interactive OAuth, no human in the loop)?

Run: ./venv/bin/python spike.py
"""
import asyncio
import os
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

NIRVANA_MCP_URL = "https://mcp.nirvanahq.com/mcp"


async def main():
    pat = os.environ.get("NIRVANA_PAT")
    if not pat:
        print("NIRVANA_PAT not set in environment")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {pat}"}

    print(f"[{time.strftime('%X')}] connecting to {NIRVANA_MCP_URL} ...")
    async with streamablehttp_client(NIRVANA_MCP_URL, headers=headers) as (
        read_stream,
        write_stream,
        get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            print(f"[{time.strftime('%X')}] initializing session ...")
            init_result = await session.initialize()
            print(f"[{time.strftime('%X')}] initialized. server info:")
            print(f"  name: {init_result.serverInfo.name}")
            print(f"  version: {init_result.serverInfo.version}")
            print(f"  session_id: {get_session_id()}")

            print(f"[{time.strftime('%X')}] listing tools ...")
            tools = await session.list_tools()
            print(f"  {len(tools.tools)} tools exposed:")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")

            print(f"\n[{time.strftime('%X')}] SUCCESS — authenticated headlessly via PAT, no OAuth prompt.")


if __name__ == "__main__":
    asyncio.run(main())
