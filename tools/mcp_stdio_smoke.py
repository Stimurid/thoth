"""Actual MCP protocol smoke against a configured Thoth catalog; no code actions."""
import asyncio
import json
import os
import sys
from pathlib import Path
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main():
    root = os.environ.get("THOTH_ARCHIVE_ROOT")
    if not root:
        raise RuntimeError("THOTH_ARCHIVE_ROOT_REQUIRED")
    module = Path(__file__).with_name("thoth_mcp_port.py")
    params = StdioServerParameters(command=sys.executable, args=[str(module)],
        env={**os.environ, "THOTH_ARCHIVE_ROOT": root}, cwd=str(module.parent))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = {x.name for x in tools.tools}
            wanted = {"thoth_status", "thoth_search_sessions",
                      "thoth_get_session", "thoth_projects"}
            assert names == wanted, names
            for name, arguments in [
                ("thoth_status", {}),
                ("thoth_search_sessions", {"query": "hydra", "limit": 2}),
                ("thoth_projects", {"limit": 2}),
            ]:
                x = await client.call_tool(name, arguments)
                assert not x.is_error, name
                result = x.structured_content
                if result is None:
                    result = json.loads(x.content[0].text)
                assert result["ok"] and result["read_only"], name
                print("MCP_TOOL_OK", name,
                      result.get("total_records", result.get("count", 0)))
    print("THOTH_MCP_STDIO_PASS")


if __name__ == "__main__":
    asyncio.run(main())
