"""Deterministic benchmark for early closed Shadow DOM capture."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import browser
import perception


HTML = """<!doctype html><html><head><title>closed-ready</title></head><body>
<div id="closed-host"></div>
<script>
const host = document.querySelector('#closed-host');
const root = host.attachShadow({mode: 'closed'});
root.innerHTML = '<button id="closed-button">Launch closed action</button>';
root.querySelector('button').addEventListener('click', () => {
  document.title = 'closed-clicked';
  document.body.setAttribute('data-result', 'clicked');
});
</script></body></html>"""


async def main() -> None:
    token = browser.set_client_scope("closed-shadow-benchmark")
    try:
        url = "data:text/html;charset=utf-8," + quote(HTML)
        started = await browser.browser_start("closed-shadow-benchmark", url, "silent", "chromium")
        assert started.get("started"), started
        observed = await perception.find("Launch closed action", compact=False)
        assert observed["total"] == 1, observed
        element = observed["matches"][0]
        assert element["closedShadowCaptured"] is True, element
        clicked = await perception.click_element(element["id"])
        assert clicked.get("clicked"), clicked
        assert clicked.get("closedShadowCaptured") is True, clicked
        page = await browser._get_page()
        assert await page.title() == "closed-clicked"
        status = await browser.closed_shadow_status()
        assert status["enabled"] and status["captured_hosts"] == 1, status
        print(json.dumps({"passed": True, "element": element, "click": clicked, "status": status}, indent=2))
    finally:
        await browser.browser_close()
        browser.reset_client_scope(token)


if __name__ == "__main__":
    asyncio.run(main())
