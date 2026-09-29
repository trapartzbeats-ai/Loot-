"""Prove MV3 content injection and recovery across a browser restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import browser
import extensions


EXTENSION = Path(__file__).parent / "extensions" / "test-extension"


async def injected(profile: str) -> dict:
    started = await browser.browser_start(profile, "https://example.com", mode="headless", engine="chromium")
    page = await browser._get_page()
    await page.wait_for_timeout(1000)
    marker = await page.get_attribute("html", "data-test-extension")
    runtime_id = await page.get_attribute("html", "data-test-extension-id")
    return {"started": started.get("started"), "marker": marker, "runtime_id": runtime_id, "url": page.url}


async def main() -> None:
    try:
        manager = extensions.get_manager()
        manager.load_from_path(str(EXTENSION))
        first = await injected("mv3-lifecycle")
        assert first["marker"] == "active" and first["runtime_id"], first
        manager.set_extension_id("Test Extension", first["runtime_id"])
        await browser.browser_close(all_clients=True)
        second = await injected("mv3-lifecycle")
        assert second["marker"] == "active", second
        assert second["runtime_id"] == first["runtime_id"], (first, second)
        print(json.dumps({"passed": True, "first": first, "after_restart": second}, indent=2))
    finally:
        await browser.browser_close(all_clients=True)


if __name__ == "__main__":
    asyncio.run(main())
