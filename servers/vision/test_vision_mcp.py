"""End-to-end acceptance test using the exact Hermes Vision stdio command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastmcp import Client


ROOT = Path(__file__).parent
IMAGE = ROOT / "work" / "test_text.png"
CONFIG = {"mcpServers": {"vision": {
    "command": r"C:\Python314\python.exe", "args": ["D:/Projects/Hermes/vision/server.py"],
}}}


def payload(result):
    if getattr(result, "data", None) is not None:
        return result.data
    if getattr(result, "structured_content", None):
        return result.structured_content
    return result.content[0].text if result.content else None


async def main() -> None:
    checks = []
    async with Client(CONFIG, name="vision-acceptance") as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        expected = {"screenshot", "get_metadata", "get_colors", "get_layout", "crop", "compare",
                    "read_text", "describe", "palette_ui", "image_inspector"}
        assert names == expected, names

        async def call(name, arguments):
            result = await client.call_tool(name, arguments, raise_on_error=False)
            assert not result.is_error, (name, result)
            data = payload(result)
            checks.append(name)
            return data

        metadata = await call("get_metadata", {"image": str(IMAGE)})
        assert metadata["width"] == 600 and metadata["height"] == 200
        colors = await call("get_colors", {"image": str(IMAGE), "count": 4})
        assert colors["count"] == 4
        layout = await call("get_layout", {"image": str(IMAGE)})
        assert len(layout["regions_3x3"]) == 9
        cropped = await call("crop", {"image": str(IMAGE), "x": 0, "y": 0, "width": 100, "height": 80,
                                      "out_name": "acceptance-crop.png"})
        assert Path(cropped["saved_to"]).exists()
        comparison = await call("compare", {"image_a": str(IMAGE), "image_b": str(IMAGE)})
        assert comparison["similarity"] == 1.0 and comparison["same"]
        ocr = await call("read_text", {"image": str(IMAGE)})
        assert ocr["has_text"] and "STREET" in ocr["text"].upper()
        description = await call("describe", {"image": str(IMAGE), "prompt": "State the main visible text briefly."})
        assert description["description"] and description["model"]
        shot = await call("screenshot", {"mode": "region", "x": 0, "y": 0, "width": 64, "height": 48,
                                           "out_name": "vision-acceptance"})
        assert shot["width"] == 64 and shot["height"] == 48 and Path(shot["saved_to"]).exists()
        await call("palette_ui", {"image": str(IMAGE)})
        inspector = await call("image_inspector", {"path": str(IMAGE)})
        assert "background" in json.dumps(inspector, default=str)

        resource = await client.read_resource("vision://image/test_text.png")
        resource_data = json.loads(resource[0].text)
        assert resource_data["width"] == 600 and resource_data["preview"].startswith("data:image/png;base64,")
        prompts = {prompt.name for prompt in await client.list_prompts()}
        assert prompts == {"analyze_logo", "compare_screenshots"}

    print(json.dumps({"passed": True, "tools": len(checks), "checks": checks}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
