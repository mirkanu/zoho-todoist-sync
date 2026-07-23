"""
Spike 003: do writes via MCP (create, update, complete, retag, star) actually
persist and read back correctly? Requires Nirvana Pro (update_tasks is Pro-gated).

Creates one throwaway test task, mutates it several ways, reads it back after
each mutation, then soft-deletes it (moves to trash) at the end.

Run: ../001-nirvana-mcp-auth-headless/venv/bin/python spike.py
"""
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

NIRVANA_MCP_URL = "https://mcp.nirvanahq.com/mcp"
TEST_TASK_NAME = "ZTS-SPIKE-003 throwaway test task — safe to delete"


def parse(result):
    text = result.content[0].text
    return json.loads(text)


async def main():
    pat = os.environ["NIRVANA_PAT"]
    headers = {"Authorization": f"Bearer {pat}"}

    async with streamablehttp_client(NIRVANA_MCP_URL, headers=headers) as (r, w, sid):
        async with ClientSession(r, w) as session:
            await session.initialize()

            print("=== create_tasks ===")
            result = await session.call_tool(
                "create_tasks",
                {"tasks": [{"name": TEST_TASK_NAME, "state": "next", "tags": ["ZTS-Spike"]}]},
            )
            created = parse(result)
            print(json.dumps(created, indent=2))
            task_id = created["tasks"][0]["id"]
            print(f"created task id: {task_id}")

            print("\n=== update_tasks: set duedate + starred + tags ===")
            result = await session.call_tool(
                "update_tasks",
                {
                    "updates": [
                        {
                            "id": task_id,
                            "duedate": "2026-08-01",
                            "starred": True,
                            "tags": ["ZTS-Spike", "Personal"],
                        }
                    ]
                },
            )
            print(json.dumps(parse(result), indent=2))

            print("\n=== get_tasks: verify duedate/starred/tags persisted ===")
            result = await session.call_tool("get_tasks", {"query": "ZTS-SPIKE-003"})
            fetched = parse(result)
            print(json.dumps(fetched, indent=2))

            print("\n=== update_tasks: move state to waiting ===")
            result = await session.call_tool(
                "update_tasks", {"updates": [{"id": task_id, "state": "waiting", "waitingfor": "spike-verification"}]}
            )
            print(json.dumps(parse(result), indent=2))

            print("\n=== update_tasks: mark completed ===")
            result = await session.call_tool(
                "update_tasks", {"updates": [{"id": task_id, "completed": True}]}
            )
            print(json.dumps(parse(result), indent=2))

            print("\n=== get_tasks: verify completion persisted ===")
            result = await session.call_tool("get_tasks", {"query": "ZTS-SPIKE-003"})
            print(json.dumps(parse(result), indent=2))

            print("\n=== update_tasks: soft-delete (move to trash) cleanup ===")
            result = await session.call_tool(
                "update_tasks", {"updates": [{"id": task_id, "state": "trash"}]}
            )
            print(json.dumps(parse(result), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
