"""Acceptance proof for catalog, macros, WebArena adapter, and media pipeline."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import browser
import extensions
import server
import webarena


PAGE = b"""<!doctype html><title>Platform Fixture</title><body>
<audio controls src='/sample.mp3'></audio>
<button onclick="document.title='macro-clicked'">Run Macro</button></body>"""
MEDIA = b"ID3" + (b"loot-media-fixture" * 32)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = MEDIA if self.path == "/sample.mp3" else PAGE
        kind = "audio/mpeg" if self.path == "/sample.mp3" else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return


async def main() -> None:
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{fixture.server_port}/"
    try:
        await browser.browser_start("platform-foundations", base, mode="headless")
        catalog = extensions.catalog_list()
        assert catalog["count"] >= 4

        server.macro_start("platform-foundations")
        await server.navigate(base)
        action = await server.browser_action("Run Macro")
        stopped = server.macro_stop()
        assert action["success"] and stopped["steps"] == 2, (action, stopped)
        replay = await server.macro_replay("platform-foundations")
        assert replay["replayed"] and replay["steps_run"] == 2, replay
        assert await (await browser._get_page()).title() == "macro-clicked"

        media = await server.media_discover()
        item = next(item for item in media["items"] if item["url"].endswith("sample.mp3"))
        downloaded = await server.media_download(item["url"], "platform-fixture.mp3", max_mb=1)
        assert downloaded["downloaded"] and downloaded["size_bytes"] == len(MEDIA), downloaded

        configured = webarena.configure(base)
        task = webarena.resolve("local-task", "/task/1")
        assert configured["configured"] and task["url"].endswith("/task/1")
        print(json.dumps({
            "passed": True, "catalog_count": catalog["count"], "macro": replay,
            "media": downloaded, "webarena": task,
        }, indent=2))
    finally:
        fixture.shutdown()
        fixture.server_close()
        await browser.browser_close(all_clients=True)


if __name__ == "__main__":
    asyncio.run(main())
