from __future__ import annotations

import json
import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.exceptions import ToolError

import browser, scraper, vision, hands, history, tasks, briefcase, extensions, zext, perception, agent_benchmark
import macros, webarena, media_pipeline, health_monitor
import approvals, workflows, sessions
from config import TOOLSET

CORE_ALWAYS_VISIBLE = [
    "browser_start", "browser_action", "browser_state", "browser_wait",
    "browser_debug", "browser_capabilities", "navigate", "save_session",
    "set_browser_mode", "signal_user", "wait_for_user", "browser_close",
]

# Normal agents get a compact progressive-disclosure catalog. The full
# specialist endpoint remains direct-call compatible for development tools.
_transforms = (
    [BM25SearchTransform(max_results=6, always_visible=CORE_ALWAYS_VISIBLE)]
    if TOOLSET == "core"
    else None
)
mcp = FastMCP("loot-browser", transforms=_transforms)

MUTATING_TOOLS = {
    "navigate", "browser_start", "browser_action", "back", "forward", "reload",
    "new_tab", "switch_tab", "close_tab", "submit", "press_key", "upload_file",
    "fill_form", "set_browser_mode", "set_viewport", "network_mock", "network_unmock",
    "click_element", "fill_element", "macro_replay", "webarena_start", "browser_close",
}


class ClientIsolationMiddleware(Middleware):
    """Bind every tool call to its MCP client's private browser state."""

    async def on_call_tool(self, context, call_next):
        fast_context = context.fastmcp_context
        scope = "local"
        if fast_context is not None:
            try:
                scope = fast_context.client_id or fast_context.session_id
            except RuntimeError:
                pass
        token = browser.set_client_scope(scope)
        try:
            tool_name = context.message.name
            if tool_name == "call_tool":
                tool_name = str((context.message.arguments or {}).get("name", ""))
            if tool_name in MUTATING_TOOLS:
                permission = browser.shared_mutation_allowed()
                if not permission.get("allowed"):
                    raise ToolError(json.dumps({"failure_class": "shared_room_locked", **permission}))
            return await call_next(context)
        finally:
            browser.reset_client_scope(token)


mcp.add_middleware(ClientIsolationMiddleware())


# ── Browser Tools ──

@mcp.tool
async def navigate(url: str, timeout: int = 15000, session_id: str = "") -> Dict[str, Any]:
    """Navigate to a URL and wait for page load. Use session_id for isolated sessions."""
    result = await browser.navigate(url, timeout, session_id)
    if result.get("navigated"):
        macros.record(browser.get_effective_scope(), "navigate", {"url": url, "timeout": timeout})
    return result


@mcp.tool
async def get_text() -> Dict[str, str]:
    """Get visible text of current page."""
    return await browser.get_text()


@mcp.tool
async def get_html() -> Dict[str, str]:
    """Get full HTML of current page."""
    return await browser.get_html()


@mcp.tool
async def get_title() -> Dict[str, str]:
    """Get current page title."""
    return await browser.get_title()


@mcp.tool
async def get_url() -> Dict[str, str]:
    """Get current page URL."""
    return await browser.get_url()


@mcp.tool
async def click(selector: str = "", x: float = 0, y: float = 0) -> Dict[str, Any]:
    """Click element (CSS selector) or coordinates (x, y)."""
    return await browser.click(selector, x, y)


@mcp.tool
async def type_text(selector: str, text: str) -> Dict[str, Any]:
    """Type text into an input element."""
    return await browser.type_text(selector, text)


@mcp.tool
async def scroll(x: int = 0, y: int = 0) -> Dict[str, Any]:
    """Scroll page to coordinates."""
    return await browser.scroll(x, y)


@mcp.tool
async def evaluate(code: str) -> Dict[str, Any]:
    """Execute JavaScript in current page."""
    return await browser.evaluate(code)


@mcp.tool
async def back() -> Dict[str, Any]:
    """Go back one page."""
    return await browser.back()


@mcp.tool
async def forward() -> Dict[str, Any]:
    """Go forward one page."""
    return await browser.forward()


@mcp.tool
async def reload() -> Dict[str, Any]:
    """Reload current page."""
    return await browser.reload()


@mcp.tool
async def get_links(limit: int = 100) -> Dict[str, Any]:
    """Get links on current page (capped at limit to avoid context bloat).
    Each link has href (raw attribute — use in click) + url (resolved) + text."""
    return await browser.get_links(limit)


@mcp.tool
async def get_attributes(selector: str, attributes: List[str]) -> Dict[str, Any]:
    """Get attributes from first matching element."""
    return await browser.get_attributes(selector, attributes)


@mcp.tool
async def wait_for(selector: str, timeout: int = 10000) -> Dict[str, Any]:
    """Wait for element to appear."""
    return await browser.wait_for(selector, timeout)


@mcp.tool
async def list_tabs() -> Dict[str, Any]:
    """List all open tabs."""
    return await browser.list_tabs()


@mcp.tool
async def new_tab(url: str = "") -> Dict[str, Any]:
    """Open new tab with optional URL."""
    return await browser.new_tab(url)


@mcp.tool
async def switch_tab(tab_id: int) -> Dict[str, Any]:
    """Switch to tab by ID."""
    return await browser.switch_tab(tab_id)


@mcp.tool
async def close_tab(tab_id: int) -> Dict[str, Any]:
    """Close tab by ID."""
    return await browser.close_tab(tab_id)


@mcp.tool
async def submit(selector: str = "") -> Dict[str, Any]:
    """Submit a form."""
    return await browser.submit(selector)


@mcp.tool
async def press_key(key: str, selector: str = "") -> Dict[str, Any]:
    """Press a keyboard key or combo (e.g. Enter, Control+C)."""
    result = await browser.press_key(key, selector)
    macros.record(browser.get_effective_scope(), "press_key", {"key": key, "selector": selector})
    return result


@mcp.tool
async def upload_file(selector: str, file_paths: List[str]) -> Dict[str, Any]:
    """Upload files to a file input."""
    return await browser.upload_file(selector, file_paths)


@mcp.tool
async def screenshot(full_page: bool = False, save_path: str = "",
                     max_dimension: int = 1600, format: str = "jpeg",
                     quality: int = 72) -> Dict[str, Any]:
    """Capture screenshot. Returns downscaled JPEG base64 by default.

    save_path: write full-res PNG to disk, return path only (no base64).
    max_dimension=0 disables downscaling (returns raw PNG base64).
    """
    return await browser.screenshot(full_page, save_path, max_dimension, format, quality)


@mcp.tool
async def fullscreen(selector: str = "") -> Dict[str, Any]:
    """Toggle fullscreen (optionally on specific element)."""
    return await browser.fullscreen(selector)


@mcp.tool
async def browser_close(close_shared: bool = False) -> Dict[str, Any]:
    """Close private state; shared clients detach unless close_shared is explicitly true."""
    return await browser.browser_close(close_shared=close_shared)


# ── Interactive Browser Tools ──

@mcp.tool
async def download_file(url: str, output_path: str) -> Dict[str, Any]:
    """Download a file from URL to local path."""
    return await browser.download_file(url, output_path)


@mcp.tool
async def save_element_image(selector: str, output_path: str) -> Dict[str, Any]:
    """Save an image element to a local file."""
    return await browser.save_element_image(selector, output_path)


@mcp.tool
async def fill_form(fields: Dict[str, str]) -> Dict[str, Any]:
    """Fill multiple form fields at once. Pass {selector: value} pairs."""
    return await browser.fill_form(fields)


@mcp.tool
async def handle_dialog(accept: bool = True, prompt_text: str = "") -> Dict[str, Any]:
    """Handle JavaScript dialogs (alert, confirm, prompt)."""
    return await browser.handle_dialog(accept, prompt_text)


@mcp.tool
async def get_cookies(domain: str = "") -> Dict[str, Any]:
    """Get cookies for current context, optionally filtered by domain."""
    return await browser.get_cookies(domain)


@mcp.tool
async def set_cookie(name: str, value: str, domain: str = "") -> Dict[str, Any]:
    """Set a cookie."""
    return await browser.set_cookie(name, value, domain)


@mcp.tool
async def get_local_storage(key: str = "") -> Dict[str, Any]:
    """Get localStorage items."""
    return await browser.get_local_storage(key)


@mcp.tool
async def set_local_storage(key: str, value: str) -> Dict[str, Any]:
    """Set a localStorage item."""
    return await browser.set_local_storage(key, value)


@mcp.tool
async def right_click(selector: str) -> Dict[str, Any]:
    """Right-click an element."""
    return await browser.right_click(selector)


@mcp.tool
async def drag_and_drop(source_selector: str, target_selector: str) -> Dict[str, Any]:
    """Drag from source to target."""
    return await browser.drag_and_drop(source_selector, target_selector)


@mcp.tool
async def select_option(selector: str, value: str = "", label: str = "", index: int = -1) -> Dict[str, Any]:
    """Select a dropdown option by value, label, or index."""
    return await browser.select_option(selector, value, label, index)


@mcp.tool
async def hover(selector: str) -> Dict[str, Any]:
    """Hover over an element."""
    return await browser.hover(selector)


@mcp.tool
async def focus_element(selector: str) -> Dict[str, Any]:
    """Focus an element."""
    return await browser.focus(selector)


@mcp.tool
async def get_selected_text() -> Dict[str, Any]:
    """Get currently selected/highlighted text."""
    return await browser.get_selected_text()


@mcp.tool
async def clear_input(selector: str) -> Dict[str, Any]:
    """Clear an input field."""
    return await browser.clear_input(selector)


@mcp.tool
async def wait_for_url(url_pattern: str, timeout: int = 10000) -> Dict[str, Any]:
    """Wait for URL to match pattern (useful after redirects)."""
    return await browser.wait_for_url(url_pattern, timeout)


@mcp.tool
async def get_element_count(selector: str) -> Dict[str, Any]:
    """Count elements matching a selector."""
    return await browser.get_element_count(selector)


# ── Extension Tools (Route A: Chrome MV3) ──

@mcp.tool
def extension_load(path: str) -> Dict[str, Any]:
    """Load a Chrome extension from a folder path (unpacked MV3)."""
    try:
        ext = extensions.get_manager().load_from_path(path)
        return {"loaded": True, "name": ext.name, "path": ext.path, "manifest_version": ext.manifest.get("manifest_version")}
    except Exception as e:
        return {"loaded": False, "error": str(e)}


@mcp.tool
def extension_discover() -> Dict[str, Any]:
    """Discover and load all extensions in the extensions/ folder."""
    found = extensions.get_manager().discover_extensions()
    return {"loaded": len(found), "extensions": [e.name for e in found]}


@mcp.tool
def extension_list() -> Dict[str, Any]:
    """List all loaded extensions with their capabilities."""
    exts = extensions.get_manager().list_to_dict()
    return {"count": len(exts), "extensions": exts}


@mcp.tool
def extension_id(name: str) -> Dict[str, Any]:
    """Get extension ID by name."""
    ext = extensions.get_manager().get_by_name(name)
    if ext and ext.id:
        return {"name": name, "id": ext.id}
    return {"name": name, "id": None, "note": "Extension loaded but ID not yet available (open popup first)"}


@mcp.tool
async def extension_refresh_ids() -> Dict[str, Any]:
    """Refresh extension IDs from browser service workers. Call after navigate."""
    try:
        context = await browser._get_context()
        results = {}
        # Try getting existing service workers
        try:
            sws = context.service_workers
            for sw in sws:
                url = sw.url
                ext_id = extensions.extract_extension_id(url)
                if ext_id:
                    for name, ext in extensions.get_manager()._loaded.items():
                        if not ext.id:
                            extensions.get_manager().set_extension_id(name, ext_id)
                            results[name] = ext_id
        except AttributeError:
            # Older Playwright - try waiting for service worker event
            sw_url = await extensions.wait_for_service_worker(context)
            if sw_url:
                ext_id = extensions.extract_extension_id(sw_url)
                if ext_id:
                    for name, ext in extensions.get_manager()._loaded.items():
                        if not ext.id:
                            extensions.get_manager().set_extension_id(name, ext_id)
                            results[name] = ext_id
        return {"found": len(results), "extensions": results}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool
def extension_manifest(name: str) -> Dict[str, Any]:
    """Get extension manifest (permissions, capabilities)."""
    ext = extensions.get_manager().get_by_name(name)
    if ext:
        return {"name": name, "manifest": ext.manifest}
    return {"error": "Extension not found"}


@mcp.tool
async def extension_popup_open(name: str) -> Dict[str, Any]:
    """Open an extension's popup and return its content."""
    ext = extensions.get_manager().get_by_name(name)
    if not ext:
        return {"error": f"Extension not found: {name}"}
    if not ext.id:
        return {"error": "Extension ID not available. Navigate to a page first."}

    popup_url = extensions.get_extension_popup_url(ext.id, ext.manifest)
    if not popup_url:
        return {"error": "Extension has no popup"}

    try:
        context = await browser._get_context()
        popup_page = await context.new_page()
        await popup_page.goto(popup_url)
        await popup_page.wait_for_timeout(1000)
        text = await popup_page.evaluate("() => document.body.innerText")
        html = await popup_page.content()
        await popup_page.close()
        return {"name": name, "popup_url": popup_url, "text": text[:3000], "html_length": len(html)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool
async def extension_options_read(name: str) -> Dict[str, Any]:
    """Read an extension's options/settings page."""
    ext = extensions.get_manager().get_by_name(name)
    if not ext:
        return {"error": f"Extension not found: {name}"}
    if not ext.id:
        return {"error": "Extension ID not available"}

    options_url = extensions.get_extension_options_url(ext.id, ext.manifest)
    if not options_url:
        return {"error": "Extension has no options page"}

    try:
        context = await browser._get_context()
        page = await context.new_page()
        await page.goto(options_url)
        await page.wait_for_timeout(1000)
        text = await page.evaluate("() => document.body.innerText")
        await page.close()
        return {"name": name, "options_url": options_url, "text": text[:3000]}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool
async def extension_content_detect(name: str, url: str) -> Dict[str, Any]:
    """Navigate to a URL and detect what the extension injects."""
    ext = extensions.get_manager().get_by_name(name)
    if not ext:
        return {"error": f"Extension not found: {name}"}

    try:
        page = await browser._get_page()
        await page.goto(url)
        await page.wait_for_timeout(2000)

        # Detect common injection patterns
        detections = {}

        # Check for added styles
        styles = await page.evaluate("() => document.querySelectorAll('style[data-extension], style[id*=extension]').length")
        if styles:
            detections["injected_styles"] = styles

        # Check for added scripts
        scripts = await page.evaluate("() => document.querySelectorAll('script[src*=extension], script[data-extension]').length")
        if scripts:
            detections["injected_scripts"] = scripts

        # Check for added DOM elements
        added = await page.evaluate("""() => {
            const marks = document.querySelectorAll('[data-extension], [data-ext-id], [class*="extension"]');
            return marks.length;
        }""")
        if added:
            detections["marked_elements"] = added

        runtime_id = await page.evaluate("() => document.documentElement.getAttribute('data-test-extension-id') || ''")
        if runtime_id:
            extensions.get_manager().set_extension_id(name, runtime_id)
            detections["runtime_id"] = runtime_id

        # Get content script matches from manifest
        content_scripts = ext.manifest.get("content_scripts", [])
        matches = []
        for cs in content_scripts:
            for match in cs.get("matches", []):
                if match == "<all_urls>" or url in match:
                    matches.append(match)
        detections["matched_urls"] = matches

        return {"name": name, "url": url, "detections": detections, "content_scripts": len(content_scripts)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool
def extension_store_catalog() -> Dict[str, Any]:
    """List the validated extension catalog and current installation state."""
    return extensions.catalog_list()


@mcp.tool
def extension_store_install(ext_id: str, update: bool = False) -> Dict[str, Any]:
    """Install or explicitly update one catalog extension through a staged copy."""
    return extensions.catalog_install(ext_id, update)


# ── Session Tools (v4 feature in v3) ──

@mcp.tool
async def switch_session(session_id: str) -> Dict[str, Any]:
    """Switch to a different browser session (isolated cookies/storage)."""
    return await browser.switch_session(session_id)


@mcp.tool
async def shared_browser_join(name: str) -> Dict[str, Any]:
    """Explicitly join a named browser room shared with other opted-in MCP clients."""
    return await browser.shared_browser_join(name)


@mcp.tool
async def shared_browser_leave() -> Dict[str, Any]:
    """Leave the shared browser room and return to this client's private state."""
    return await browser.shared_browser_leave()


@mcp.tool
async def shared_browser_status() -> Dict[str, Any]:
    """Report shared-room membership, participants, and shared page identity."""
    return await browser.shared_browser_status()


@mcp.tool
async def shared_browser_acquire(ttl_seconds: int = 30) -> Dict[str, Any]:
    """Acquire or renew the cooperative mutation lease for a shared room."""
    return await browser.shared_browser_acquire(ttl_seconds)


@mcp.tool
async def shared_browser_release(token: str) -> Dict[str, Any]:
    """Release this client's shared-room mutation lease."""
    return await browser.shared_browser_release(token)


@mcp.tool
async def browser_start(profile: str, url: str = "", mode: str = "silent",
                        engine: str = "chromium", stealth: bool = False,
                        auto_block_ads: bool = False) -> Dict[str, Any]:
    """PREFERRED START: open a profile, mode, engine, and optional URL in one call."""
    await health_monitor.start(60)
    return await browser.browser_start(profile, url, mode, engine, stealth, auto_block_ads)


@mcp.tool
async def save_session(session_id: str = "") -> Dict[str, Any]:
    """Save current session cookies/storage to disk."""
    return await browser.save_session(session_id)


@mcp.tool
async def list_sessions() -> Dict[str, Any]:
    """List all saved sessions."""
    return await browser.list_sessions()


@mcp.tool
async def session_health() -> Dict[str, Any]:
    """Detect login redirects, visible password prompts, and stale session age."""
    return await browser.session_health()


@mcp.tool
async def network_requests(limit: int = 50, since_timestamp: int = 0,
                           url_contains: str = "", method: str = "",
                           clear: bool = False, compact: bool = True,
                           failures_only: bool = False) -> Dict[str, Any]:
    """Return recent response/failure records for this client's context."""
    return await browser.network_requests(limit, since_timestamp, url_contains, method, clear, compact, failures_only)


@mcp.tool
async def console_logs(limit: int = 50, level: str = "", clear: bool = False,
                       compact: bool = True) -> Dict[str, Any]:
    """Return recent browser console and uncaught page-error records."""
    return await browser.console_logs(limit, level, clear, compact)


@mcp.tool
async def network_mock(pattern: str, body: str = "", status: int = 200,
                       content_type: str = "application/json", method: str = "",
                       headers: Dict[str, str] = {}) -> Dict[str, Any]:
    """Mock a URL glob in this client's context, e.g. **/api/items."""
    return await browser.network_mock(pattern, body, status, content_type, method, headers)


@mcp.tool
async def network_unmock(pattern: str = "") -> Dict[str, Any]:
    """Remove one network mock, or all mocks when pattern is omitted."""
    return await browser.network_unmock(pattern)


@mcp.tool
async def configure_browser(engine: str = "chromium", stealth: bool = False) -> Dict[str, Any]:
    """Use Chromium, Firefox, or WebKit for this client; optionally reduce basic automation signals."""
    return await browser.configure_browser(engine, stealth)


@mcp.tool
async def set_browser_mode(mode: str, auto_block_ads: Optional[bool] = None) -> Dict[str, Any]:
    """Switch this client between headless, headed, and silent modes while preserving tabs."""
    result = await browser.set_browser_mode(mode, auto_block_ads)
    if result.get("mode") == mode:
        macros.record(browser.get_effective_scope(), "set_browser_mode", {"mode": mode, "auto_block_ads": auto_block_ads})
    return result


@mcp.tool
async def get_browser_mode() -> Dict[str, Any]:
    """Report this client's mode, visibility, silent controls, and viewport."""
    return await browser.get_browser_mode()


@mcp.tool
async def is_browser_visible() -> Dict[str, Any]:
    """Check whether this client's active browser is a visible headed window."""
    return await browser.is_browser_visible()


@mcp.tool
async def set_viewport(width: int, height: int) -> Dict[str, Any]:
    """Resize the page viewport and, for headed Chromium, the visible window."""
    return await browser.set_viewport(width, height)


@mcp.tool
async def signal_user(message: str) -> Dict[str, Any]:
    """Show a dismissible banner in the visible headed browser."""
    return await browser.signal_user(message)


@mcp.tool
async def wait_for_user(action: str = "complete the task", timeout: int = 120) -> Dict[str, Any]:
    """Pause while the user completes an action in headed mode and clicks Done."""
    return await browser.wait_for_user(action, timeout)


@mcp.tool
async def client_isolation_status() -> Dict[str, Any]:
    """Report this client's isolated context/page identity and engine."""
    return await browser.client_isolation_status()


@mcp.tool
async def browser_state(include_elements: bool = False, limit: int = 25) -> Dict[str, Any]:
    """PREFERRED STATUS: current profile, mode, page, health, tabs, and optional compact elements."""
    state = await browser.browser_state()
    if include_elements:
        observed = await perception.observe_elements(compact=True, limit=max(1, min(limit, 100)))
        state["elements"] = observed["elements"]
        state["element_total"] = observed["total"]
        state["elements_truncated"] = observed["truncated"]
    return state


@mcp.tool
async def browser_action(target: str, action: str = "click", text: str = "",
                         wait_stable: bool = False, visual_recovery: bool = True) -> Dict[str, Any]:
    """PREFERRED ACTION: semantic click/fill with optional screenshot-grounding recovery."""
    action = action.lower().strip()
    if action not in {"click", "fill"}:
        return {"success": False, "failure_class": "agent_action", "error": "action must be click or fill"}
    if action == "fill" and not text:
        return {"success": False, "failure_class": "agent_action", "error": "text is required for fill"}
    macros.record(browser.get_effective_scope(), "browser_action", {
        "target": target, "action": action, "text": text,
        "wait_stable": wait_stable, "visual_recovery": visual_recovery,
    })
    found = await perception.find(target, compact=False, limit=5)
    matches = found.get("matches", [])
    if not matches:
        bot = await browser.bot_protection_status()
        if bot.get("blocked"):
            return {"success": False, "failure_class": "bot_protection", "target": target, "bot_protection": bot}
        visual = await browser.visual_action(target, action, text) if visual_recovery else None
        if visual and visual.get("success"):
            return {"success": True, "action": action, "target": target, "result": visual, "recovered": True}
        return {"success": False, "failure_class": "element_not_found", "target": target, "visual_recovery": visual}
    element = matches[0]
    # Surface ambiguity: if the top match's name/placeholder ties with other
    # candidates, report them so the agent can disambiguate via click_element.
    ambiguous = []
    if len(matches) > 1:
        norm = lambda value: " ".join((value or "").casefold().split())
        top_name = norm(element.get("name", "")) or norm(element.get("placeholder", ""))
        for other in matches[1:]:
            other_name = norm(other.get("name", "")) or norm(other.get("placeholder", ""))
            if other_name and other_name == top_name:
                ambiguous.append({
                    "id": other.get("id"), "frame": other.get("frame"),
                    "role": other.get("role"), "name": other.get("name"),
                })
    stable = None
    if wait_stable:
        stable = await perception.wait_element_stable(element["id"])
        if not stable.get("stable"):
            return {"success": False, "target": target, "element_id": element["id"], "stability": stable}
    result = (
        await perception.fill_element(element["id"], text)
        if action == "fill"
        else await perception.click_element(element["id"])
    )
    success = bool(
        result.get("filled") and result.get("verified")
        if action == "fill"
        else result.get("clicked")
    )
    visual = None
    if not success and visual_recovery:
        visual = await browser.visual_action(target, action, text)
        if visual.get("success"):
            return {
                "success": True, "action": action, "target": target,
                "result": visual, "recovered": True, "dom_attempt": result,
            }
    return {
        "success": success,
        "action": action,
        "target": {key: element.get(key) for key in ("id", "frame", "role", "name", "frameDepth", "shadowDepth")},
        "result": result,
        "stability": stable,
        "ambiguous": ambiguous if ambiguous else None,
        "visual_recovery": visual,
    }


@mcp.tool
async def browser_debug(limit: int = 20) -> Dict[str, Any]:
    """PREFERRED DEBUG: compact session health, failed requests, warnings, and errors."""
    health = await browser.session_health()
    requests = await browser.network_requests(limit=limit, compact=True, failures_only=True)
    logs = await browser.console_logs(limit=limit, compact=True)
    important_logs = [
        entry for entry in logs["logs"]
        if entry.get("type") in {"warning", "error", "pageerror", "dialog-dismissed"}
    ]
    problem_logs = [entry for entry in important_logs if entry.get("type") in {"warning", "error", "pageerror"}]
    bot = await browser.bot_protection_status()
    return {
        "session": {key: health.get(key) for key in ("status", "reason", "recommendation", "url")},
        "failed_requests": requests["requests"],
        "console": important_logs,
        "bot_protection": bot,
        "has_issues": bool(health["status"] != "healthy" or requests["requests"] or problem_logs or bot.get("blocked")),
    }


@mcp.tool
async def bot_protection_status() -> Dict[str, Any]:
    """Detect challenge/block pages and return local recovery recommendations."""
    return await browser.bot_protection_status()


@mcp.tool
async def closed_shadow_status() -> Dict[str, Any]:
    """Report early closed-shadow capture, observed hosts, and its hard boundary."""
    return await browser.closed_shadow_status()


@mcp.tool
async def system_health(refresh: bool = True) -> Dict[str, Any]:
    """Check local daemons, browser processes, profiles, disk, and screenshot dependency."""
    return await health_monitor.status(refresh)


@mcp.tool
async def health_monitor_start(interval_seconds: int = 60) -> Dict[str, Any]:
    """Start sampled health monitoring with bounded JSONL history."""
    return await health_monitor.start(interval_seconds)


@mcp.tool
async def health_monitor_stop() -> Dict[str, Any]:
    """Stop the sampled local health monitor."""
    return await health_monitor.stop()


@mcp.tool
def browser_capabilities(task: str = "") -> Dict[str, Any]:
    """PREFERRED GUIDE: compact workflows and tool groups for this browser."""
    workflows = {
        "standard": ["browser_start", "browser_state", "browser_action", "browser_wait", "browser_debug", "save_session"],
        "manual_login": ["browser_start(mode='headed')", "wait_for_user", "save_session", "set_browser_mode('silent')"],
        "frames_shadow": ["browser_state(include_elements=true)", "find_element", "click_element/fill_element", "evaluate_in_frame"],
        "network_debug": ["browser_debug", "network_requests", "console_logs", "network_mock/network_unmock"],
        "operations": ["system_health", "bot_protection_status", "closed_shadow_status"],
        "shared_room": ["shared_browser_join", "shared_browser_acquire", "shared_browser_release", "shared_browser_leave"],
    }
    return {
        "recommended_core": ["browser_start", "browser_state", "browser_action", "browser_debug", "browser_capabilities"],
        "workflows": workflows,
        "selection_rule": "Use browser_action for named click/fill targets; use element tools only for ambiguity or diagnostics.",
        "discovery_rule": "Use search_tools for specialist capabilities, then call_tool with the returned exact tool name.",
        "freshness_rule": "Re-observe after navigation or major DOM changes.",
        "task": task,
        "toolset": TOOLSET,
        "full_server": "server.py",
        "core_server": "server_core.py",
    }


@mcp.tool
async def browser_wait(text: str = "", selector: str = "", value_selector: str = "",
                       value_attribute: str = "aria-valuenow", value_at_least: float = -1,
                       click_target: str = "", timeout: int = 15000,
                       poll_ms: int = 50) -> Dict[str, Any]:
    """PREFERRED WAIT: server-side text, selector, or numeric-value wait with optional semantic click."""
    conditions = sum(bool(item) for item in (text, selector, value_selector))
    if conditions != 1:
        return {"matched": False, "error": "provide exactly one of text, selector, or value_selector"}
    macros.record(browser.get_effective_scope(), "browser_wait", {
        "text": text, "selector": selector, "value_selector": value_selector,
        "value_attribute": value_attribute, "value_at_least": value_at_least,
        "click_target": click_target, "timeout": timeout, "poll_ms": poll_ms,
    })
    timeout = max(100, min(int(timeout), 120000))
    poll_ms = max(10, min(int(poll_ms), 1000))
    page = await browser._get_page()
    started = time.perf_counter()
    samples = 0
    observed_value = None
    matched_frame = None
    while (time.perf_counter() - started) * 1000 < timeout:
        samples += 1
        remaining_ms = max(1, timeout - int((time.perf_counter() - started) * 1000))
        operation_timeout = min(1000, remaining_ms)
        frames = page.frames
        for frame_index, frame in enumerate(frames):
            try:
                if text:
                    matched = await frame.evaluate("needle => (document.body?.innerText || '').includes(needle)", text)
                elif selector:
                    locator = frame.locator(selector)
                    matched = bool(await locator.count() and await locator.first.is_visible(timeout=operation_timeout))
                else:
                    locator = frame.locator(value_selector).first
                    raw = await locator.get_attribute(value_attribute, timeout=operation_timeout) if value_attribute else None
                    if raw in (None, ""):
                        raw = await locator.text_content(timeout=operation_timeout)
                    number = re.search(r"-?\d+(?:\.\d+)?", raw or "")
                    observed_value = float(number.group(0)) if number else None
                    matched = observed_value is not None and observed_value >= value_at_least
                if matched:
                    matched_frame = frame_index
                    action_result = None
                    if click_target:
                        found = await perception.find(click_target, compact=False, limit=5)
                        matches = found.get("matches", [])
                        action_result = (
                            await perception.click_element(matches[0]["id"])
                            if matches else {"clicked": False, "failure_class": "element_not_found"}
                        )
                    return {
                        "matched": True,
                        "condition": "text" if text else "selector" if selector else "value",
                        "target": text or selector or value_selector,
                        "value": observed_value,
                        "frame": matched_frame,
                        "samples": samples,
                        "waited_ms": round((time.perf_counter() - started) * 1000),
                        "action": action_result,
                    }
            except Exception:
                continue
        await asyncio.sleep(poll_ms / 1000)
    return {
        "matched": False,
        "failure_class": "timing",
        "target": text or selector or value_selector,
        "value": observed_value,
        "samples": samples,
        "waited_ms": round((time.perf_counter() - started) * 1000),
        "error": "condition did not match before timeout",
    }


@mcp.tool
async def spa_readiness(timeout: int = 5000, quiet_ms: int = 300) -> Dict[str, Any]:
    """Detect SPA hydration and wait for a stable, non-loading application root."""
    return await browser.wait_for_spa_ready(timeout=timeout, quiet_ms=quiet_ms)


# ── Macros, WebArena adapter, and media pipeline ──

@mcp.tool
def macro_start(name: str) -> Dict[str, Any]:
    """Start recording high-level navigation, semantic actions, waits, keys, and mode changes."""
    return macros.start(browser.get_effective_scope(), name)


@mcp.tool
def macro_stop(save: bool = True) -> Dict[str, Any]:
    """Stop the active macro recording and optionally persist it."""
    return macros.stop(browser.get_effective_scope(), save)


@mcp.tool
def macro_list() -> Dict[str, Any]:
    """List saved browser macros."""
    return macros.list_macros()


@mcp.tool
async def macro_replay(name: str, stop_on_error: bool = True) -> Dict[str, Any]:
    """Replay a saved high-level macro in the active private or shared browser."""
    scope = browser.get_effective_scope()
    try:
        payload = macros.load(name)
    except Exception as exc:
        return {"replayed": False, "error": str(exc)}
    results = []
    allowed = {"navigate", "browser_action", "browser_wait", "press_key", "set_browser_mode"}
    macros.begin_replay(scope)
    try:
        for index, step in enumerate(payload.get("steps", [])):
            tool = step.get("tool")
            arguments = step.get("arguments") or {}
            if tool not in allowed:
                result = {"success": False, "error": f"unsupported macro tool: {tool}"}
            elif tool == "navigate":
                result = await navigate(**arguments)
            elif tool == "browser_action":
                result = await browser_action(**arguments)
            elif tool == "browser_wait":
                result = await browser_wait(**arguments)
            elif tool == "press_key":
                result = await press_key(**arguments)
            else:
                result = await set_browser_mode(**arguments)
            failed = bool(result.get("error") or result.get("success") is False or result.get("matched") is False or result.get("navigated") is False)
            results.append({"index": index, "tool": tool, "failed": failed, "result": result})
            if failed and stop_on_error:
                break
    finally:
        macros.end_replay(scope)
    return {"replayed": True, "name": name, "steps_total": len(payload.get("steps", [])), "steps_run": len(results), "stopped_on_error": bool(results and results[-1]["failed"]), "results": results}


@mcp.tool
def webarena_configure(base_url: str, sites: Dict[str, str] = {}) -> Dict[str, Any]:
    """Configure the external WebArena or BrowserGym deployment URL."""
    return webarena.configure(base_url, sites)


@mcp.tool
async def webarena_start(task_id: str, path: str = "", profile: str = "webarena", mode: str = "silent") -> Dict[str, Any]:
    """Resolve and open one externally hosted WebArena task in a dedicated profile."""
    task = webarena.resolve(task_id, path)
    if not task.get("url"):
        return {"started": False, **task}
    started = await browser.browser_start(profile, task["url"], mode=mode)
    return {"task": task, "browser": started, "started": bool(started.get("started"))}


@mcp.tool
def webarena_status() -> Dict[str, Any]:
    """Report WebArena adapter configuration."""
    return webarena.config()


@mcp.tool
def webarena_record_result(task_id: str, success: bool, details: str = "") -> Dict[str, Any]:
    """Persist a scored result from an external WebArena task."""
    return webarena.record_result(task_id, success, details)


@mcp.tool
async def media_discover(limit: int = 100) -> Dict[str, Any]:
    """Discover image, audio, video, and stream URLs across the current page's frames."""
    return await media_pipeline.discover(await browser._get_page(), limit)


@mcp.tool
async def media_download(url: str, filename: str = "", max_mb: int = 100) -> Dict[str, Any]:
    """Download one discovered media URL with the active browser context's authenticated request client."""
    return await media_pipeline.download(await browser._get_context(), url, filename, max_mb)


# ── Scraping Tools (LOOT) ──

@mcp.tool
def page_to_text(url: str, max_chars: int = 5000) -> Dict[str, Any]:
    """Fetch URL and return clean text + metadata."""
    return scraper.fetch_url(url, max_chars)


@mcp.tool
def page_to_structured(url: str) -> Dict[str, Any]:
    """Extract JSON-LD, Open Graph, Twitter Cards from URL."""
    return scraper.extract_structured(url)


@mcp.tool
def query_page(url: str, selector: str, limit: int = 25) -> Dict[str, Any]:
    """Run CSS selector against a URL."""
    return scraper.query_page(url, selector, limit)


@mcp.tool
def extract_links(url: str) -> Dict[str, Any]:
    """Extract all links from URL."""
    return scraper.extract_links(url)


@mcp.tool
def extract_images(url: str) -> Dict[str, Any]:
    """Extract all images from URL."""
    return scraper.extract_images(url)


@mcp.tool
def discover_feeds(url: str) -> Dict[str, Any]:
    """Discover RSS/Atom feeds from URL."""
    return scraper.discover_feeds(url)


@mcp.tool
def discover_sitemap(url: str) -> Dict[str, Any]:
    """Discover sitemap URLs from robots.txt."""
    return scraper.discover_sitemap(url)


@mcp.tool
def web_search(query: str, limit: int = 10, engine: str = "duckduckgo") -> Dict[str, Any]:
    """Search the web."""
    return scraper.web_search(query, limit, engine)


@mcp.tool
def batch_scrape(urls: List[str], max_chars: int = 2000) -> Dict[str, Any]:
    """Scrape multiple URLs efficiently."""
    return scraper.batch_scrape(urls, max_chars)


# ── Vision Tools ──

@mcp.tool
def describe_image(image: str) -> Dict[str, Any]:
    """Get VLM description of an image (path, URL, or base64)."""
    return vision.describe(image)


@mcp.tool
def read_image_text(image: str) -> Dict[str, Any]:
    """OCR: extract all text from an image."""
    return vision.read_text(image)


@mcp.tool
def detect_objects(image: str) -> Dict[str, Any]:
    """Object detection (Florence-2 fast tier): returns [{bbox, label}]."""
    return vision.detect_objects(image)


@mcp.tool
def find_on_screen(image: str, what: str) -> Dict[str, Any]:
    """Visual grounding (Florence-2 fast tier): find 'what' in the image, returns boxes."""
    return vision.find_on_screen(image, what)


@mcp.tool
def get_image_colors(image: str, count: int = 8) -> Dict[str, Any]:
    """Extract dominant colors from an image."""
    return vision.get_colors(image, count)


@mcp.tool
def get_image_layout(image: str) -> Dict[str, Any]:
    """Get spatial layout analysis (brightness grid, contrast)."""
    return vision.get_layout(image)


@mcp.tool
def compare_images(a: str, b: str) -> Dict[str, Any]:
    """Compare two images (perceptual hash similarity)."""
    return vision.compare_images(a, b)


@mcp.tool
def image_metadata(image: str) -> Dict[str, Any]:
    """Get image metadata (dimensions, format)."""
    return vision.image_metadata(image)


# ── Hands Tools (Windows) ──

@mcp.tool
def run_command(cmd: str, timeout: int = 30) -> Dict[str, Any]:
    """Run a PowerShell/system command."""
    return hands.run_command(cmd, timeout)


@mcp.tool
def launch_app(path: str) -> Dict[str, Any]:
    """Launch an application."""
    return hands.launch_app(path)


@mcp.tool
def list_windows() -> Dict[str, Any]:
    """List visible windows."""
    return hands.list_windows()


@mcp.tool
def focus_window(title: str) -> Dict[str, Any]:
    """Bring window to front by title."""
    return hands.focus_window(title)


@mcp.tool
def click_window_element(name: str = "", auto_id: str = "", className: str = "") -> Dict[str, Any]:
    """Click a Windows control by name/auto_id/class."""
    return hands.click_element(name, auto_id, className)


@mcp.tool
def type_text_ctrl(text: str, handle: int = 0) -> Dict[str, Any]:
    """Type text into a focused control."""
    return hands.type_text_ctrl(text, handle)


@mcp.tool
def press_hotkey(keys: str) -> Dict[str, Any]:
    """Send hotkey (e.g. ctrl+c, alt+tab)."""
    return hands.press_hotkey(keys)


# ── History Tools ──

@mcp.tool
def history_search(query: str, limit: int = 20) -> Dict[str, Any]:
    """Full-text search across all visited pages."""
    results = history.search_history(query, limit)
    return {"query": query, "count": len(results), "results": results}


@mcp.tool
def history_recall(url: str) -> Dict[str, Any]:
    """Recall the most recent snapshot of a URL."""
    result = history.recall_visit(url)
    return {"url": url, "found": result is not None, "snapshot": result}


@mcp.tool
def history_list(limit: int = 50) -> Dict[str, Any]:
    """List recent browsing history."""
    results = history.list_history(limit)
    return {"count": len(results), "history": results}


@mcp.tool
def history_compare(url: str) -> Dict[str, Any]:
    """Compare latest two visits to a URL."""
    return history.compare_visits(url)


@mcp.tool
def save_visit(url: str, title: str = "", text: str = "", markdown: str = "",
               structured: str = "", screenshot: str = "", vision: str = "") -> Dict[str, Any]:
    """Save a page visit to history manually."""
    vid = history.add_visit(url, title, text, markdown, structured, screenshot, vision)
    return {"saved": True, "id": vid}


# ── Approval Gates (Kestra-inspired human-in-the-loop) ──

@mcp.tool
def set_approval_policy(actions: List[str]) -> Dict[str, Any]:
    """Configure which action types require human approval."""
    valid = {"forms", "purchases", "deletions", "account_changes", "publishing"}
    filtered = [a for a in actions if a in valid]
    approvals.get_manager().set_policy(filtered)
    return {"policy": approvals.get_manager().get_policy()}


@mcp.tool
def get_approval_policy() -> Dict[str, Any]:
    """Get current approval policy."""
    return {"policy": approvals.get_manager().get_policy()}


@mcp.tool
def request_approval(action: str, context: str = "", tool_name: str = "", arguments: Dict = None) -> Dict[str, Any]:
    """Request human approval before performing an action."""
    approval_id = approvals.get_manager().request(
        action=action,
        context={"description": context},
        tool=tool_name,
        args=arguments,
    )
    return {"approval_id": approval_id, "status": "pending", "message": "Approval requested. Call pending_approvals() to review."}


@mcp.tool
def pending_approvals() -> Dict[str, Any]:
    """List all pending approval requests."""
    pending = approvals.get_manager().pending()
    return {"count": len(pending), "approvals": pending}


@mcp.tool
def approve_action(approval_id: str) -> Dict[str, Any]:
    """Approve a pending action."""
    return approvals.get_manager().approve(approval_id)


@mcp.tool
def reject_action(approval_id: str) -> Dict[str, Any]:
    """Reject a pending action."""
    return approvals.get_manager().reject(approval_id)


@mcp.tool
def approval_history(limit: int = 50) -> Dict[str, Any]:
    """Get approval history."""
    history = approvals.get_manager().history(limit)
    return {"count": len(history), "history": history}


# ── Declarative Workflows (Kestra-inspired JSON workflows) ──

@mcp.tool
def workflow_create(definition: Dict[str, Any], scope: str = "private") -> Dict[str, Any]:
    """Create a browser workflow from a JSON definition."""
    try:
        name = workflows.get_engine().create(definition, scope)
        return {"created": True, "name": name, "scope": scope}
    except Exception as e:
        return {"created": False, "error": str(e)}


@mcp.tool
def workflow_generate(description: str) -> Dict[str, Any]:
    """Generate a workflow JSON from a natural language description."""
    return workflows.get_engine().generate_from_description(description)


@mcp.tool
def workflow_list(scope: str = "all") -> Dict[str, Any]:
    """List available workflows."""
    return {"workflows": workflows.get_engine().list_workflows(scope)}


@mcp.tool
def workflow_get(name: str, scope: str = "private") -> Dict[str, Any]:
    """Get a workflow definition."""
    try:
        wf = workflows.get_engine().load(name, scope)
        return {"found": True, "workflow": wf}
    except FileNotFoundError:
        return {"found": False, "error": f"Workflow not found: {name}"}


@mcp.tool
def workflow_validate(name: str, scope: str = "private") -> Dict[str, Any]:
    """Validate a workflow without executing."""
    return workflows.get_engine().validate(name, scope)


@mcp.tool
def workflow_delete(name: str, scope: str = "private") -> Dict[str, Any]:
    """Delete a workflow."""
    success = workflows.get_engine().delete(name, scope)
    return {"deleted": success}


# ── Session Recording + Replay (Kestra-inspired execution history) ──

@mcp.tool
def session_start(name: str = "") -> Dict[str, Any]:
    """Start recording a browser session."""
    session_id = sessions.get_recorder().start(name)
    return {"session_id": session_id, "recording": True}


@mcp.tool
def session_stop() -> Dict[str, Any]:
    """Stop recording the current session."""
    # Find active session
    active = None
    for sid, info in sessions.get_recorder()._active.items():
        active = sid
        break
    if not active:
        return {"error": "No active session"}
    result = sessions.get_recorder().stop(active)
    return {"stopped": True, "session": result}


@mcp.tool
def session_list() -> Dict[str, Any]:
    """List all recorded sessions."""
    return {"sessions": sessions.get_player().list_sessions()}


@mcp.tool
def session_actions(session_id: str) -> Dict[str, Any]:
    """Get action-level log for a session."""
    return {"session_id": session_id, "actions": sessions.get_player().get_actions(session_id)}


@mcp.tool
def session_replay(session_id: str) -> Dict[str, Any]:
    """Get session replay data (screenshots + snapshots)."""
    metadata = sessions.get_player().get_metadata(session_id)
    screenshots = sessions.get_player().get_screenshots(session_id)
    return {"session_id": session_id, "metadata": metadata, "screenshots": screenshots}


@mcp.tool
def session_vault_list() -> Dict[str, Any]:
    """List vaulted (important) sessions."""
    return {"vault": sessions.get_retention().vault_list()}


@mcp.tool
def session_cleanup() -> Dict[str, Any]:
    """Run weekly cleanup (move important to vault, purge old)."""
    return sessions.get_retention().weekly_cleanup()


# ── Briefcase Tools (per-profile encrypted secrets) ──
# Every session/profile has its own Fernet-encrypted store. Agents should keep
# credentials HERE instead of hardcoding them in scripts — other agents can't
# read a profile they aren't switched into, and nothing is plaintext on disk.

@mcp.tool
def briefcase_add(name: str, value: str, session_id: str = "") -> Dict[str, Any]:
    """Store an encrypted secret in the ACTIVE profile's briefcase (or a named session).
    Use for credentials, tokens, API keys — never hardcode them in scripts.
    Values are Fernet-encrypted at rest and scoped to the profile."""
    return briefcase.briefcase_add(name, value, session_id)


@mcp.tool
def briefcase_get(name: str, session_id: str = "") -> Dict[str, Any]:
    """Retrieve a secret from the ACTIVE profile's briefcase (or a named session)."""
    return briefcase.briefcase_get(name, session_id)


@mcp.tool
def briefcase_list(session_id: str = "") -> Dict[str, Any]:
    """List secret NAMES in the ACTIVE profile's briefcase (values never returned)."""
    return briefcase.briefcase_list(session_id)


@mcp.tool
def briefcase_remove(name: str, session_id: str = "") -> Dict[str, Any]:
    """Remove a secret from the ACTIVE profile's briefcase (or a named session)."""
    return briefcase.briefcase_remove(name, session_id)


# ── Perception Tools (CDP: see through iframes/shadow DOM) ──
# Page.getFrameTree + Accessibility.getFullAXTree -> normalized elements.
# Agents reference elements by id (e_N) and frames by index — no raw selectors.

@mcp.tool
async def page_frames() -> Dict[str, Any]:
    """Map the FULL frame hierarchy (nested + cross-origin iframes) via CDP.
    Each frame has an index (use in observe_elements/click_element) + depth."""
    return await perception.frame_tree()


@mcp.tool
async def read_frame(frame_index: int) -> Dict[str, Any]:
    """Read text and metadata from a frame by index, including nested frames."""
    return await perception.read_frame(frame_index)


@mcp.tool(output_schema=None)
async def observe_elements(frame_index: int = -1, include_hidden: bool = False,
                           compact: bool = True, limit: int = 100,
                           offset: int = 0) -> str:
    """Normalized interactive elements from the AX tree (roles, names, values).
    frame_index: -1 = ALL frames (default), or a specific frame's index.
    compact=True: returns ONLY {id, frame, role, name} — token discipline
    (full schema stays in the element cache for click/fill). Pass compact=False
    for rich details (values, placeholders, bounds).
    limit/offset: paginate results (total and next_offset are always reported).
    Elements are ids e_N usable with click_element."""
    if frame_index == -1:
        frame_index = None
    result = await perception.observe_elements(frame_index, include_hidden, compact, limit, offset)
    return json.dumps(result, separators=(",", ":"), default=str)


@mcp.tool(output_schema=None)
async def find_element(what: str, frame_index: int = -1,
                       compact: bool = True, limit: int = 25) -> str:
    """Find interactive elements by name/role/placeholder text (case-insensitive)."""
    if frame_index == -1:
        frame_index = None
    result = await perception.find(what, frame_index, compact, limit)
    return json.dumps(result, separators=(",", ":"), default=str)


@mcp.tool
async def click_element(element_id: str) -> Dict[str, Any]:
    """Click an observed element by id (e_N). Works across iframes/shadow DOM."""
    return await perception.click_element(element_id)


@mcp.tool
async def fill_element(element_id: str, text: str) -> Dict[str, Any]:
    """Fill an observed element by id (e_N) with text. Works across iframes."""
    return await perception.fill_element(element_id, text)


@mcp.tool
async def wait_element_stable(element_id: str, stable_ms: int = 800, timeout: int = 8000) -> Dict[str, Any]:
    """Wait until an observed element stops moving before acting on it."""
    return await perception.wait_element_stable(element_id, stable_ms, timeout)


@mcp.tool
async def evaluate_in_frame(frame_index: int = 0, code: str = "") -> Dict[str, Any]:
    """Run JS inside a SPECIFIC frame (default: main). Cross-frame escape hatch —
    reaches iframes the top-page evaluate can't (e.g. mail.com's compose)."""
    return await perception.evaluate(frame_index, code)


@mcp.tool
async def run_browser_code(code: str = "") -> Dict[str, Any]:
    """Run async PYTHON Playwright code against the live page (RCE-equivalent —
    trusted local server only). In scope: page, frames, context, browser.
    Example: 't = await page.title()\nreturn {"title": t}'"""
    return await perception.run_code(code)


# ── Resources ──

@mcp.resource("history://{visit_id}")
def get_visit_resource(visit_id: str) -> str:
    """Read a past visit by ID."""
    try:
        vid = int(visit_id)
    except ValueError:
        return json.dumps({"error": "Invalid visit ID"})
    visit = history.get_visit_by_id(vid)
    if visit is None:
        return json.dumps({"error": "Visit not found"})
    return json.dumps(visit, default=str)


@mcp.resource("page://current")
async def get_current_page() -> str:
    """Get current page snapshot."""
    url = await browser.get_url()
    title = await browser.get_title()
    text = await browser.get_text()
    return json.dumps({"url": url.get("url"), "title": title.get("title"), "text_preview": text.get("text", "")[:2000]}, default=str)


# ── Prompts ──

@mcp.prompt
def recall(topic: str) -> str:
    """Search history and summarize findings."""
    results = history.search_history(topic, 10)
    urls = [r.get("url", "") for r in results[:5]]
    return (
        f"Search your browsing history for '{topic}'. "
        f"Relevant URLs found: {', '.join(urls) if urls else 'none'}. "
        "Use history_recall() to get full details on any URL."
    )


@mcp.prompt
def research(query: str) -> str:
    """Full research workflow."""
    return (
        f"Research '{query}':\n"
        "1. Use web_search to find results\n"
        "2. Use batch_scrape to get content from top URLs\n"
        "3. Use page_to_structured for metadata\n"
        "4. Save findings with save_visit\n"
        "5. Summarize key findings"
    )


@mcp.prompt
def compare_versions(url: str) -> str:
    """Compare current page with previous visit."""
    return (
        f"Compare {url} with its previous version:\n"
        "1. Use history_compare to detect changes\n"
        "2. Navigate to the URL for current state\n"
        "3. Use history_recall to get the previous snapshot\n"
        "4. Summarize what changed"
    )


# ── Zext Extensions (Route B: custom agent extensions) ──

@mcp.tool
def zext_load(path: str) -> Dict[str, Any]:
    """Load a .zext extension from a folder path."""
    try:
        ext = zext.get_manager().load(path)
        return {"loaded": True, "id": ext.id, "name": ext.name, "tools": [t["name"] for t in ext.agent_tools]}
    except Exception as e:
        return {"loaded": False, "error": str(e)}


@mcp.tool
def zext_discover() -> Dict[str, Any]:
    """Discover and load all .zext extensions in the extensions/installed folder."""
    found = zext.get_manager().discover()
    _register_zext_tools()
    return {"loaded": len(found), "extensions": [{"id": e.id, "name": e.name, "tools": len(e.agent_tools)} for e in found]}


@mcp.tool
def zext_list() -> Dict[str, Any]:
    """List all loaded .zext extensions."""
    return {"extensions": zext.get_manager().list_all()}


@mcp.tool
def zext_tools(ext_id: str) -> Dict[str, Any]:
    """List tools provided by a specific .zext extension."""
    ext = zext.get_manager().get(ext_id)
    if not ext:
        return {"error": "Extension not found"}
    return {"id": ext_id, "name": ext.name, "tools": [t["name"] for t in ext.agent_tools]}


# Register tools from all loaded .zext extensions
_registered_zext_tools = set()


def _register_zext_tools():
    """Dynamically register MCP tools from loaded .zext extensions."""
    import inspect
    from typing import Optional, get_type_hints

    all_tools = zext.get_manager().get_all_tools()
    for tool_name, (func, ext_id) in all_tools.items():
        if tool_name in _registered_zext_tools:
            continue
        # Get explicit parameters from function signature
        sig = inspect.signature(func)
        hints = get_type_hints(func)

        # Build explicit parameter list
        params_code = []
        params_call = []
        for pname, param in sig.parameters.items():
            if pname == "kwargs":
                continue
            ptype = hints.get(pname, str)
            if param.default is inspect.Parameter.empty:
                params_code.append(f"{pname}: {ptype.__name__}")
                params_call.append(f"{pname}={pname}")
            else:
                default_repr = repr(param.default)
                params_code.append(f"{pname}: {ptype.__name__} = {default_repr}")
                params_call.append(f"{pname}={pname}")

        # Create wrapper function with explicit signature
        params_str = ", ".join(params_code)
        call_str = ", ".join(params_call)

        wrapper_code = f"""
async def _wrapper({params_str}) -> dict:
    try:
        result = await _func({call_str})
        return result if isinstance(result, dict) else {{"result": result}}
    except Exception as e:
        return {{"error": str(e), "tool": "{tool_name}"}}
"""
        local_ns = {"_func": func}
        exec(wrapper_code, local_ns)
        wrapper = local_ns["_wrapper"]
        wrapper.__name__ = tool_name
        wrapper.__doc__ = f"Tool from .zext extension {ext_id}"

        try:
            mcp.tool(name=tool_name)(wrapper)
            _registered_zext_tools.add(tool_name)
        except Exception as e:
            print(f"[zext] Failed to register {tool_name}: {e}")


# Make installed agent extensions available on a fresh server process. Calling
# zext_discover later remains useful for newly-added extensions.
zext.get_manager().discover()
_register_zext_tools()


# ── Agent Torture Benchmark ──

@mcp.tool
def benchmark_scenarios() -> Dict[str, Any]:
    """List permanent real-site agent torture-test scenarios."""
    return agent_benchmark.list_scenarios()


@mcp.tool
async def benchmark_run(scenario_ids: List[str] = []) -> Dict[str, Any]:
    """Run selected torture tests (empty = all) and return a scored failure report."""
    return await agent_benchmark.run(scenario_ids or None)


# ── Background Tasks (v4 feature in v3) ──

@mcp.tool
def describe_image_async(image: str) -> Dict[str, Any]:
    """Start background VLM image description. Returns task_id to poll."""
    task_id = tasks.start_task(vision.describe, image)
    return {"task_id": task_id, "status": "running", "note": "Use check_task to get results"}


@mcp.tool
def check_task(task_id: str) -> Dict[str, Any]:
    """Check status/result of a background task."""
    return tasks.get_task(task_id)


@mcp.tool
def list_tasks() -> Dict[str, Any]:
    """List all background tasks."""
    return tasks.list_tasks()


CORE_TOOLS = {
    "browser_start", "browser_state", "browser_action", "browser_debug", "browser_capabilities",
    "browser_wait",
    "navigate", "get_text", "get_title", "get_url", "back", "forward", "reload",
    "list_tabs", "new_tab", "switch_tab", "close_tab", "press_key", "screenshot",
    "upload_file", "download_file", "browser_close",
    "save_session", "list_sessions", "session_health", "configure_browser",
    "set_browser_mode", "get_browser_mode", "is_browser_visible", "set_viewport",
    "signal_user", "wait_for_user",
    "network_requests", "console_logs", "network_mock", "network_unmock",
    "briefcase_add", "briefcase_get", "briefcase_list", "briefcase_remove",
    "page_frames", "read_frame", "observe_elements", "find_element", "click_element",
    "fill_element", "wait_element_stable", "evaluate_in_frame",
    "shared_browser_join", "shared_browser_leave", "shared_browser_status",
    "shared_browser_acquire", "shared_browser_release",
    "spa_readiness",
    "extension_store_catalog", "extension_store_install",
    "macro_start", "macro_stop", "macro_list", "macro_replay",
    "webarena_configure", "webarena_start", "webarena_status", "webarena_record_result",
    "media_discover", "media_download",
    "system_health", "health_monitor_start", "health_monitor_stop",
    "bot_protection_status", "closed_shadow_status",
}


def _apply_toolset() -> None:
    if TOOLSET != "core":
        return
    components = list(mcp._local_provider._components.items())
    for key, component in components:
        if key.startswith("tool:") and component.name not in CORE_TOOLS:
            mcp.local_provider.remove_tool(component.name)


_apply_toolset()


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000):
    """Run the server. transport='stdio' for Hermes, 'http' for remote access."""
    if transport == "http":
        print(f"[loot-browser] HTTP server on {host}:{port}")
        mcp.run(transport="http", host=host, port=port, show_banner=False)
    else:
        mcp.run(show_banner=False)


if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    main(transport=transport, port=port)
