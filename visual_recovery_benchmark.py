"""Deterministic proof that browser_action can recover through visual grounding."""

from __future__ import annotations

import asyncio
import json

import browser
import server
import vision


async def main() -> None:
    original = vision.find_on_screen
    try:
        vision.find_on_screen = lambda image, what: {
            "boxes": [{"bbox": [300, 300, 700, 700], "label": what}],
            "model": "deterministic-test-grounder", "time_s": 0.0,
        }
        await browser.browser_start("visual-recovery-benchmark", mode="headless")
        page = await browser._get_page()
        await page.set_content("""<style>html,body{margin:0;width:100%;height:100%}</style>
        <canvas id=c width=512 height=400 style='position:fixed;left:calc(50% - 256px);top:calc(50% - 200px)'></canvas>
        <script>
        const c=document.querySelector('#c'),x=c.getContext('2d');
        x.fillStyle='#2457d6';x.fillRect(0,0,512,400);x.fillStyle='white';
        x.font='28px sans-serif';x.fillText('VISUAL FALLBACK TARGET',70,210);
        c.onclick=()=>document.title='visual-recovery-clicked';
        </script>""")
        result = await server.browser_action("VISUAL FALLBACK TARGET")
        assert result.get("success") and result.get("recovered"), result
        assert result.get("result", {}).get("via") == "visual_grounding", result
        assert await page.title() == "visual-recovery-clicked"
        print(json.dumps({"passed": True, "result": result}, indent=2))
    finally:
        vision.find_on_screen = original
        await browser.browser_close(all_clients=True)


if __name__ == "__main__":
    asyncio.run(main())
