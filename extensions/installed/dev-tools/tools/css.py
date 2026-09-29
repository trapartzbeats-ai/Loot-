"""CSS extraction tool — get styles from current page."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(selector: str = "", **kwargs) -> dict:
    """Extract CSS styles from current page."""
    from browser import _get_page
    import asyncio

    page = asyncio.get_event_loop().run_until_complete(_get_page())

    if selector:
        # Get computed styles for specific element
        styles = asyncio.get_event_loop().run_until_complete(page.evaluate(
            f"""(() => {{
                const el = document.querySelector('{selector}');
                if (!el) return null;
                const computed = window.getComputedStyle(el);
                const result = {{}};
                for (let i = 0; i < computed.length; i++) {{
                    const prop = computed[i];
                    result[prop] = computed.getPropertyValue(prop);
                }}
                return result;
            }})()"""
        ))
        return {"selector": selector, "computed_styles": styles}
    else:
        # Get all stylesheets
        sheets = asyncio.get_event_loop().run_until_complete(page.evaluate(
            """() => {
                const result = [];
                for (const sheet of document.styleSheets) {
                    try {
                        const rules = [];
                        for (const rule of sheet.cssRules || []) {
                            rules.push(rule.cssText.substring(0, 200));
                        }
                        result.push({href: sheet.href || 'inline', rules_count: rules.length, rules: rules.slice(0, 20)});
                    } catch (e) {
                        result.push({href: sheet.href || 'inline', error: 'cross-origin'});
                    }
                }
                return result;
            }"""
        ))
        return {"stylesheets": sheets, "count": len(sheets)}
