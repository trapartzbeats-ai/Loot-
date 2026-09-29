"""Deterministic delayed-hydration acceptance test."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import browser


PAGE = b"""<!doctype html><html><head><script src='/bundle.js'></script></head>
<body><div id='root' aria-busy='true'>Loading...</div><script>
setTimeout(()=>{const r=document.querySelector('#root');r.removeAttribute('aria-busy');
r.innerHTML='<h1>Hydrated Loot Application</h1><button>Continue</button>';},800);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"console.log('bundle')" if self.path == "/bundle.js" else PAGE
        kind = "application/javascript" if self.path == "/bundle.js" else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = await browser.browser_start(
            "spa-readiness-benchmark", f"http://127.0.0.1:{server.server_port}/", mode="headless"
        )
        readiness = result.get("navigation", {}).get("readiness")
        page = await browser._get_page()
        assert readiness == "hydrated", result
        assert "Hydrated Loot Application" in await page.locator("body").inner_text()
        print(json.dumps({"passed": True, "start": result}, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        await browser.browser_close(all_clients=True)


if __name__ == "__main__":
    asyncio.run(main())
