"""Deterministic checks for bot diagnostics and sampled health monitoring."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import browser
import health_monitor


CHALLENGE = """<!doctype html><html><head><title>Just a moment...</title></head>
<body><main><h1>Verify you are human</h1><p>Cloudflare security check</p></main></body></html>"""


async def main() -> None:
    token = browser.set_client_scope("operational-gaps-benchmark")
    try:
        started_monitor = await health_monitor.start(10)
        assert started_monitor["started"] and started_monitor["last"]["canonical_screenshot"]
        monitor_status = await health_monitor.status()
        assert monitor_status["running"], monitor_status

        url = "data:text/html;charset=utf-8," + quote(CHALLENGE)
        started = await browser.browser_start("operational-gaps-benchmark", url, "silent", "chromium")
        assert started.get("started"), started
        bot = await browser.bot_protection_status()
        assert bot["blocked"], bot
        assert "cloudflare_challenge" in bot["signals"], bot
        assert any(item["strategy"] == "headed_manual_handoff" for item in bot["recovery_routes"]), bot

        stopped = await health_monitor.stop()
        assert stopped["stopped"], stopped
        print(json.dumps({
            "passed": True,
            "bot_protection": bot,
            "health": monitor_status,
            "monitor_stopped": stopped,
        }, indent=2))
    finally:
        await health_monitor.stop()
        await browser.browser_close()
        browser.reset_client_scope(token)


if __name__ == "__main__":
    asyncio.run(main())
