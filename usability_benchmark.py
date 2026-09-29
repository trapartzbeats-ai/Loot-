"""Permanent agent-usability check for the reduced Loot Browser core server."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

from fastmcp import Client

import browser
import server_core


def _payload(result) -> Any:
    if getattr(result, "data", None) is not None:
        return result.data
    if getattr(result, "structured_content", None):
        return result.structured_content
    content = getattr(result, "content", [])
    if content and hasattr(content[0], "text"):
        try:
            return json.loads(content[0].text)
        except Exception:
            return content[0].text
    return {}


async def _call(client: Client, name: str, arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
    started = time.perf_counter()
    data = _payload(await client.call_tool(name, arguments or {}))
    return {
        "tool": name,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "response_chars": len(json.dumps(data, default=str)),
        "data": data,
    }


async def run() -> Dict[str, Any]:
    async with Client(server_core.mcp, name="usability-benchmark") as client:
        tools = await client.list_tools()
        catalog = json.dumps([
            {"name": tool.name, "description": tool.description, "input": tool.inputSchema, "output": tool.outputSchema}
            for tool in tools
        ], default=str)
        steps = [
            await _call(client, "browser_start", {
                "profile": "usability-benchmark",
                "url": "https://www.uitestingplayground.com/dynamicid",
                "mode": "silent",
            }),
            await _call(client, "browser_action", {
                "target": "Button with Dynamic ID",
                "action": "click",
            }),
            await _call(client, "browser_state"),
            await _call(client, "browser_debug"),
        ]
        discovery = _payload(await client.call_tool("search_tools", {
            "query": "inspect the nested iframe hierarchy",
        }))
        matches = discovery.get("result", []) if isinstance(discovery, dict) else discovery
        hidden_title = _payload(await client.call_tool("call_tool", {
            "name": "get_title", "arguments": {},
        }))
        passed = bool(
            len(tools) <= 14
            and len(catalog) <= 10000
            and len(steps) == 4
            and steps[0]["data"].get("started")
            and steps[1]["data"].get("success")
            and steps[2]["data"].get("url", "").endswith("/dynamicid")
            and any(tool["name"] == "page_frames" for tool in matches)
            and hidden_title.get("title") == "Dynamic ID"
        )
        return {
            "passed": passed,
            "tool_count": len(tools),
            "catalog_chars": len(catalog),
            "estimated_catalog_tokens": round(len(catalog) / 4),
            "workflow_calls": len(steps),
            "workflow_response_chars": sum(step["response_chars"] for step in steps),
            "bm25_discovered": "page_frames",
            "hidden_call_title": hidden_title.get("title"),
            "steps": steps,
        }


async def _main() -> int:
    try:
        result = await run()
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    finally:
        await browser.browser_close(all_clients=True)
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
