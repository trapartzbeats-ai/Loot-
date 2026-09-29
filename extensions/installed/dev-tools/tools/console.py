"""JS console tool — execute JavaScript and capture output."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(code: str = "", **kwargs) -> dict:
    """Execute JavaScript in current page and capture result."""
    from browser import _get_page
    import asyncio

    page = asyncio.get_event_loop().run_until_complete(_get_page())

    try:
        result = asyncio.get_event_loop().run_until_complete(page.evaluate(code))
        return {"executed": True, "result": result, "type": type(result).__name__}
    except Exception as e:
        return {"executed": False, "error": str(e)}
