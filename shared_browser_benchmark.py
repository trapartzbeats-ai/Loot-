"""MCP-level proof for opt-in shared browser rooms and safe detach."""

from __future__ import annotations

import asyncio
import json

from fastmcp import Client

import browser
import server_core


def payload(result):
    if getattr(result, "data", None) is not None:
        return result.data
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def hidden(client: Client, name: str, arguments=None):
    found = payload(await client.call_tool("search_tools", {"query": name}))
    matches = found.get("result", []) if isinstance(found, dict) else found
    assert any(tool["name"] == name for tool in matches), (name, matches)
    return payload(await client.call_tool("call_tool", {"name": name, "arguments": arguments or {}}))


async def main() -> None:
    try:
        async with Client(server_core.mcp, name="shared-a") as a, Client(server_core.mcp, name="shared-b") as b:
            joined_a = await hidden(a, "shared_browser_join", {"name": "benchmark-room"})
            await a.call_tool("browser_start", {"profile": "shared-room-benchmark", "url": "https://example.com", "mode": "headless"})
            joined_b = await hidden(b, "shared_browser_join", {"name": "benchmark-room"})
            denied = await b.call_tool("navigate", {"url": "https://www.iana.org/help/example-domains"}, raise_on_error=False)
            assert denied.is_error, denied
            lease = await hidden(a, "shared_browser_acquire", {"ttl_seconds": 30})
            assert lease["acquired"] and lease["token"]
            navigated = payload(await a.call_tool("navigate", {"url": "https://www.iana.org/help/example-domains"}))
            assert navigated["navigated"]
            status_a = await hidden(a, "shared_browser_status")
            status_b = await hidden(b, "shared_browser_status")
            url_b = await hidden(b, "get_url")
            assert joined_a["joined"] and joined_b["joined"]
            assert status_a["context_id"] == status_b["context_id"]
            assert status_a["page_id"] == status_b["page_id"]
            assert "iana.org" in url_b["url"]
            released = await hidden(a, "shared_browser_release", {"token": lease["token"]})
            assert released["released"]
            left_b = await hidden(b, "shared_browser_leave")
            private_b = await hidden(b, "shared_browser_status")
            assert left_b["left"] and not private_b["shared"]
            print(json.dumps({"passed": True, "shared": status_a, "detached": private_b}, indent=2))
    finally:
        await browser.browser_close(all_clients=True)


if __name__ == "__main__":
    asyncio.run(main())
