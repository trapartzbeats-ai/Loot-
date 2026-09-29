"""High-friction end-to-end benchmark for the Loot Browser core MCP surface."""
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


async def run() -> Dict[str, Any]:
    calls = []
    visible_tools: set[str] = set()
    discovered_tools: set[str] = set()

    async def call(client: Client, tool: str, arguments: Dict[str, Any] | None = None):
        if tool not in visible_tools:
            if tool not in discovered_tools:
                discovery_started = time.perf_counter()
                discovery = _payload(await client.call_tool("search_tools", {"query": tool}))
                matches = discovery.get("result", []) if isinstance(discovery, dict) else discovery
                assert any(match["name"] == tool for match in matches), f"{tool} not discoverable"
                discovered_tools.add(tool)
                calls.append({
                    "tool": "search_tools",
                    "target": tool,
                    "duration_ms": round((time.perf_counter() - discovery_started) * 1000),
                    "response_chars": len(json.dumps(discovery, default=str)),
                })
            wire_tool = "call_tool"
            wire_arguments = {"name": tool, "arguments": arguments or {}}
        else:
            wire_tool = tool
            wire_arguments = arguments or {}
        started = time.perf_counter()
        data = _payload(await client.call_tool(wire_tool, wire_arguments))
        calls.append({
            "tool": tool,
            "via": wire_tool,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "response_chars": len(json.dumps(data, default=str)),
        })
        return data

    started = time.perf_counter()
    async with Client(server_core.mcp, name="hard-mission-benchmark") as client:
        visible_tools.update(tool.name for tool in await client.list_tools())
        opened = await call(client, "browser_start", {
            "profile": "hard-mission-benchmark",
            "url": "https://www.uitestingplayground.com/ajax",
            "mode": "silent",
        })
        ajax_click = await call(client, "browser_action", {"target": "Button Triggering AJAX Request"})
        ajax_wait = await call(client, "browser_wait", {
            "text": "Data loaded with AJAX get request.", "timeout": 25000, "poll_ms": 100,
        })

        await call(client, "navigate", {"url": "https://www.uitestingplayground.com/scrollbars", "timeout": 30000})
        offscreen = await call(client, "browser_action", {"target": "Hiding Button"})

        await call(client, "navigate", {"url": "https://www.uitestingplayground.com/overlapped", "timeout": 30000})
        overlap = await call(client, "browser_action", {
            "target": "Name", "action": "fill", "text": "Loot Browser hard mission",
        })

        await call(client, "navigate", {"url": "https://www.uitestingplayground.com/progressbar", "timeout": 30000})
        progress_start = await call(client, "browser_action", {"target": "Start"})
        progress_wait = await call(client, "browser_wait", {
            "value_selector": "#progressBar",
            "value_attribute": "aria-valuenow",
            "value_at_least": 75,
            "click_target": "Stop",
            "timeout": 30000,
            "poll_ms": 25,
        })
        final_progress = await call(client, "evaluate_in_frame", {
            "frame_index": 0,
            "code": "() => Number(document.querySelector('#progressBar')?.getAttribute('aria-valuenow') || 0)",
        })

        await call(client, "navigate", {"url": "https://www.uitestingplayground.com/alerts", "timeout": 30000})
        alert = await call(client, "browser_action", {"target": "Alert"})
        await asyncio.sleep(0.2)
        state = await call(client, "browser_state")
        debug = await call(client, "browser_debug")

        passed = all([
            opened.get("started"),
            ajax_click.get("success"),
            ajax_wait.get("matched"),
            offscreen.get("success"),
            overlap.get("success") and overlap.get("result", {}).get("verified"),
            progress_start.get("success"),
            progress_wait.get("matched") and progress_wait.get("action", {}).get("clicked"),
            75 <= int(final_progress.get("result") or 0) <= 80,
            alert.get("success"),
            state.get("dialogs_dismissed", 0) >= 1,
            not debug.get("has_issues"),
        ])
        return {
            "passed": passed,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "call_count": len(calls),
            "response_chars": sum(item["response_chars"] for item in calls),
            "ajax_wait": ajax_wait,
            "offscreen_click": offscreen.get("success"),
            "overlapped_fill_verified": overlap.get("result", {}).get("verified"),
            "progress_wait": progress_wait,
            "final_progress": final_progress.get("result"),
            "dialog_dismissed": state.get("dialogs_dismissed", 0),
            "debug_has_issues": debug.get("has_issues"),
            "calls": calls,
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
