"""Performance metrics tool — get page performance data."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(**kwargs) -> dict:
    """Get page performance metrics."""
    from browser import _get_page
    import asyncio

    page = asyncio.get_event_loop().run_until_complete(_get_page())

    metrics = asyncio.get_event_loop().run_until_complete(page.evaluate(
        """() => {
            const nav = performance.getEntriesByType('navigation')[0];
            const resources = performance.getEntriesByType('resource');
            return {
                url: window.location.href,
                title: document.title,
                dom_content_loaded: nav ? nav.domContentLoadedEventEnd : 0,
                load_complete: nav ? nav.loadEventEnd : 0,
                transfer_size: nav ? nav.transferSize : 0,
                encoded_body_size: nav ? nav.encodedBodySize : 0,
                decoded_body_size: nav ? nav.decodedBodySize : 0,
                resource_count: resources.length,
                total_transfer_size: resources.reduce((sum, r) => sum + (r.transferSize || 0), 0),
                slowest_resources: resources.sort((a, b) => (b.duration || 0) - (a.duration || 0)).slice(0, 5).map(r => ({
                    name: r.name.substring(0, 100),
                    type: r.initiatorType,
                    duration: Math.round(r.duration),
                    size: r.transferSize
                })),
                paint_entries: performance.getEntriesByType('paint').map(p => ({name: p.name, time: Math.round(p.startTime)})),
            };
        }"""
    ))

    return metrics or {"error": "Performance data not available"}
