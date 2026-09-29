"""DOM inspection tool — get page structure."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(selector: str = "body", max_depth: int = 3, **kwargs) -> dict:
    """Inspect DOM structure of current page."""
    from browser import _get_page
    import asyncio

    page = asyncio.get_event_loop().run_until_complete(_get_page())

    # Extract DOM tree
    tree = asyncio.get_event_loop().run_until_complete(page.evaluate(
        f"""(() => {{
            const el = document.querySelector('{selector}');
            if (!el) return null;
            function walk(node, depth) {{
                if (depth > {max_depth}) return null;
                const result = {{
                    tag: node.tagName?.toLowerCase() || '',
                    id: node.id || '',
                    classes: Array.from(node.classList || []),
                    children: []
                }};
                for (const child of node.children) {{
                    const childTree = walk(child, depth + 1);
                    if (childTree) result.children.push(childTree);
                }}
                return result;
            }}
            return walk(el, 0);
        }})()"""
    ))

    # Count elements
    counts = asyncio.get_event_loop().run_until_complete(page.evaluate(
        """() => ({
            total: document.querySelectorAll('*').length,
            divs: document.querySelectorAll('div').length,
            links: document.querySelectorAll('a').length,
            images: document.querySelectorAll('img').length,
            forms: document.querySelectorAll('form').length,
            scripts: document.querySelectorAll('script').length,
            styles: document.querySelectorAll('style').length
        })"""
    ))

    return {
        "selector": selector,
        "dom_tree": tree,
        "element_counts": counts,
    }
