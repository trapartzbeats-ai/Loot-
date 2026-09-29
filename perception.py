"""perception.py — frame-aware page perception for loot-browser-v3.

The problem: top-page `evaluate()` can't pierce cross-origin iframes, so agents
get stuck on iframe-heavy sites (e.g. mail.com's compose lives 2 frames deep).

Solution: Playwright's frame locators ALREADY traverse cross-origin frames
natively. This module scans every frame and returns a normalized element list
{id, frame, role, name, value, placeholder, enabled} + a selector_hint.
Agents reference elements by id (e_N) — no raw selectors needed.

Perception = Playwright frame locators (works today, cross-origin safe).
Action = click_element/fill_element using get_by_role / get_by_label /
selector_hint on the right frame.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import browser
from config import ALLOW_BROWSER_RUN_CODE

INTERACTIVE_ROLES = {
    "button", "textbox", "searchbox", "combobox", "link", "checkbox", "radio",
    "menuitem", "tab", "spinbutton", "slider", "switch", "listbox", "option",
    "treeitem", "menuitemcheckbox", "menuitemradio",
}

# element cache for click/fill
_CACHES: Dict[str, Dict[str, Dict]] = {}
# frame OBJECT cache — Playwright Frame objects are stable live handles even
# when page.frames reorders or URLs collide (e.g. multiple about:srcdoc frames)
_FRAME_OBJ_CACHES: Dict[str, Dict[str, Any]] = {}
_GENERATIONS: Dict[str, int] = {}


def _cache() -> Dict[str, Dict]:
    return _CACHES.setdefault(browser.get_client_scope(), {})


def _frame_obj_cache() -> Dict[str, Any]:
    return _FRAME_OBJ_CACHES.setdefault(browser.get_client_scope(), {})


def _next_generation() -> int:
    scope = browser.get_client_scope()
    _GENERATIONS[scope] = _GENERATIONS.get(scope, 0) + 1
    return _GENERATIONS[scope]


async def frame_tree() -> Dict[str, Any]:
    """Full frame hierarchy (index = position in page.frames, stable)."""
    page = await browser._get_page()
    try:
        frames = page.frames

        def node(f):
            return {
                "index": frames.index(f),
                "url": (f.url or "")[:120],
                "name": (f.name or "")[:60],
                "children": [node(c) for c in frames if c.parent_frame is f],
            }

        roots = [f for f in frames if f.parent_frame is None]
        return {"frames": [node(r) for r in roots], "total": len(frames)}
    except Exception as e:
        return {"error": str(e)[:200]}


def _frame_depth(frame) -> int:
    d = 0
    f = frame
    seen = set()
    while f is not None and f not in seen:
        seen.add(f)
        f = f.parent_frame
        d += 1
    return d - 1


def _frame_path(frame, frames) -> List[int]:
    path: List[int] = []
    current = frame
    while current is not None:
        try:
            path.append(frames.index(current))
        except ValueError:
            break
        current = current.parent_frame
    return list(reversed(path))


_EVAL_JS = """els => els.map(el => {
    const rects = el.getClientRects();
    const vis = rects.length > 0 && rects[0].width > 0;
    const tag = el.tagName;
    let role = el.getAttribute('role') || '';
    if (!role) {
        if (tag === 'BUTTON') role = 'button';
        else if (tag === 'A') role = 'link';
        else if (tag === 'TEXTAREA') role = 'textbox';
        else if (tag === 'INPUT') {
            role = ({text:'textbox',email:'textbox',tel:'textbox',url:'textbox',number:'spinbutton',
                     search:'searchbox',password:'textbox',checkbox:'checkbox',radio:'radio',
                     submit:'button',button:'button'})[el.type] || 'textbox';
        }
        else if (el.isContentEditable) role = 'textbox';
    }
    const label = (el.labels && el.labels[0]) ? el.labels[0].innerText.trim() : '';
    const name = (el.getAttribute('aria-label') || el.title || label ||
                  (el.innerText || '').trim().slice(0, 40) ||
                  el.getAttribute('placeholder') || '').trim().slice(0, 80);
    let domDepth = 0;
    let cursor = el;
    while (cursor) {
        domDepth += 1;
        cursor = cursor.parentElement || (cursor.getRootNode && cursor.getRootNode().host) || null;
    }
    let shadowDepth = 0;
    let closedShadowCaptured = false;
    let root = el.getRootNode ? el.getRootNode() : null;
    while (root && root.host) {
        shadowDepth += 1;
        if (root.host.getAttribute && root.host.getAttribute('data-loot-original-shadow-mode') === 'closed') {
            closedShadowCaptured = true;
        }
        root = root.host.getRootNode ? root.host.getRootNode() : null;
    }
    const rect = rects.length ? rects[0] : null;
    return {
        tag, role,
        name,
        value: (el.value || '').slice(0, 50),
        text: (el.innerText || el.textContent || '').trim().slice(0, 160),
        type: el.getAttribute('type') || '',
        href: el.getAttribute('href') || '',
        ariaLabel: el.getAttribute('aria-label') || '',
        title: el.getAttribute('title') || '',
        placeholder: el.getAttribute('placeholder') || '',
        nameAttr: el.getAttribute('name') || '',
        idAttr: el.getAttribute('id') || '',
        disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
        hidden: !vis,
        domDepth,
        shadowDepth,
        closedShadowCaptured,
        bbox: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null
    };
})"""


async def observe_elements(frame_index: Optional[int] = None, include_hidden: bool = False,
                           compact: bool = True, limit: int = 100,
                           offset: int = 0) -> Dict[str, Any]:
    """Normalized interactive elements across ALL frames (or one frame).

    compact=True (default): each element returns ONLY {id, frame, role, name} —
    the full rich schema stays in the element cache for actions. Saves tokens
    hard (86 elements x 15 fields → 86 x 4).
    limit/offset: paginate large pages; total and next_offset are always reported.
    """
    cache = _cache()
    frame_cache = _frame_obj_cache()
    generation = _next_generation()
    page = await browser._get_page()
    frames = page.frames
    targets = [frames[frame_index]] if frame_index is not None else frames
    elements: List[Dict] = []
    errors: List[str] = []
    cache.clear()
    frame_cache.clear()

    for i, f in enumerate(frames):
        if f not in targets:
            continue
        try:
            els = await f.locator(
                "button, [role=button], input, textarea, a[href], [contenteditable], "
                "[role=textbox], [role=combobox], [role=checkbox], [role=radio], [role=searchbox], "
                "[role=slider], [role=switch], [role=menuitem], [role=tab]"
            ).evaluate_all(_EVAL_JS)
            for e in els:
                if e["role"] not in INTERACTIVE_ROLES:
                    continue
                if not include_hidden and e["hidden"]:
                    continue
                hint = f"frame{i} {e['tag'].lower()}"
                if e["idAttr"]:
                    hint += f"#{e['idAttr']}"
                if e["nameAttr"]:
                    hint += f"[name={e['nameAttr']}]"
                elements.append({
                    "id": f"e_{len(elements)}",
                    "generation": generation,
                    "frame": i,
                    "framePath": _frame_path(f, frames),
                    "frame_url": (f.url or "")[:200],
                    "frameDepth": _frame_depth(f),
                    "role": e["role"],
                    "tag": e["tag"].lower(),
                    "name": e["name"][:80],
                    "value": e["value"],
                    "text": e["text"],
                    "type": e["type"],
                    "href": e["href"][:300],
                    "idAttr": e["idAttr"],
                    "nameAttr": e["nameAttr"],
                    "ariaLabel": e["ariaLabel"],
                    "title": e["title"],
                    "enabled": not e["disabled"],
                    "visible": not e["hidden"],
                    "placeholder": e["placeholder"][:60],
                    "domDepth": e["domDepth"],
                    "shadowDepth": e["shadowDepth"],
                    "closedShadowCaptured": e["closedShadowCaptured"],
                    "boundingBox": e["bbox"],
                    "selector_hint": hint,
                })
        except Exception as ex:
            errors.append(f"frame {i}: {str(ex)[:80]}")

    cache.update({e["id"]: e for e in elements})
    for e in elements:
        frame_cache[e["id"]] = frames[e["frame"]]

    if compact:
        out = [
            {"id": e["id"], "frame": e["frame"], "role": e["role"], "name": e["name"]}
            for e in elements
        ]
    else:
        out = elements
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    page_items = out[offset:offset + limit]
    next_offset = offset + len(page_items)
    has_more = next_offset < len(out)
    return {
        "elements": page_items,
        "total": len(elements),
        "offset": offset,
        "limit": limit,
        "returned": len(page_items),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "truncated": has_more,
        "generation": generation,
        "frames_scanned": len(targets),
        "errors": errors,
    }


async def read_frame(frame_index: int) -> Dict[str, Any]:
    """Read text and metadata from a specific frame, including nested frames."""
    page = await browser._get_page()
    frames = page.frames
    if frame_index < 0 or frame_index >= len(frames):
        return {
            "found": False,
            "failure_class": "wrong_frame",
            "error": f"frame index {frame_index} out of range (0..{len(frames) - 1})",
        }
    frame = frames[frame_index]
    try:
        text = await frame.locator("body").inner_text(timeout=5000)
    except Exception:
        try:
            text = await frame.evaluate("() => document.body?.innerText || document.documentElement?.innerText || ''")
        except Exception as ex:
            return {
                "found": False,
                "failure_class": "wrong_frame",
                "error": str(ex)[:300],
                "frame": frame_index,
            }
    return {
        "found": True,
        "frame": frame_index,
        "framePath": _frame_path(frame, frames),
        "frameDepth": _frame_depth(frame),
        "url": frame.url,
        "name": frame.name,
        "text": text,
        "length": len(text),
    }


async def find(what: str, frame_index: Optional[int] = None,
               compact: bool = True, limit: int = 25) -> Dict[str, Any]:
    """Find elements by name/role/placeholder substring (case-insensitive)."""
    await observe_elements(frame_index, compact=False, limit=1)
    normalize = lambda value: " ".join((value or "").replace("\u00a0", " ").casefold().split())
    q = normalize(what)
    # Role priority used ONLY as a tie-breaker when names score identically —
    # actionable controls (buttons, checkboxes, tabs) beat nav chrome (links,
    # generic containers) so semantic clicks land on the thing that acts.
    CLICK_ROLE_PRIORITY = {
        "button": 6, "checkbox": 6, "radio": 6, "switch": 6, "tab": 6,
        "menuitem": 6, "menuitemcheckbox": 6, "menuitemradio": 6, "combobox": 5,
        "gridcell": 5, "row": 5, "textbox": 4, "searchbox": 4, "link": 3,
    }
    ranked = []
    for element in _cache().values():
        name = normalize(element.get("name", ""))
        placeholder = normalize(element.get("placeholder", ""))
        role = normalize(element.get("role", ""))
        score = 0
        if name == q:
            score += 10
        elif name.startswith(q):
            score += 6
        elif q in name:
            score += 4
        if q in placeholder:
            score += 3
        if q == role:
            score += 2
        if score:
            ranked.append((score, element))
    def tie_rank(item):
        score, element = item
        role_prio = CLICK_ROLE_PRIORITY.get(normalize(element.get("role", "")), 1)
        enabled = 1 if element.get("enabled", True) else 0
        visible = 1 if element.get("visible", True) else 0
        depth = int(element.get("domDepth") or 0)
        return (-score, -role_prio, -enabled, -visible, -depth, element.get("frameDepth", 0))
    ranked.sort(key=lambda item: item[1]["id"])       # stable pre-sort by id
    ranked.sort(key=tie_rank)                          # relevance, then tie-break
    hits = [element for _, element in ranked]
    limit = max(1, min(int(limit), 100))
    if compact:
        matches = [
            {
                "id": element["id"],
                "frame": element["frame"],
                "role": element["role"],
                "name": element["name"],
                "frameDepth": element["frameDepth"],
                "shadowDepth": element["shadowDepth"],
                "closedShadowCaptured": element.get("closedShadowCaptured", False),
            }
            for element in hits[:limit]
        ]
    else:
        matches = hits[:limit]
    return {
        "matches": matches,
        "total": len(hits),
        "returned": len(matches),
        "truncated": len(hits) > len(matches),
        "query": what,
        "normalized_query": q,
    }


async def _locate(frame, e: Dict):
    """Return the first locator that actually resolves, with its strategy."""
    candidates = []
    role = e.get("role", "")
    name = e.get("name", "")
    tag = e.get("tag", "") or "*"

    if e.get("idAttr"):
        candidates.append(("id", frame.locator(f"#{e['idAttr']}")))
    if e.get("nameAttr"):
        candidates.append(("name-attribute", frame.locator(f"[name='{e['nameAttr']}']")))
    if e.get("placeholder"):
        candidates.append(("placeholder", frame.get_by_placeholder(e["placeholder"], exact=True)))
    if role and name:
        candidates.append(("role-exact", frame.get_by_role(role, name=name, exact=True)))
        candidates.append(("role-fuzzy", frame.get_by_role(role, name=name)))
    if role in ("button", "link") and name:
        selector = "button, [role=button]" if role == "button" else "a[href], [role=link]"
        candidates.append(("visible-text", frame.locator(selector).filter(has_text=name)))
    if name:
        candidates.append(("tag-text", frame.locator(tag).filter(has_text=name)))
    if role:
        candidates.append(("role-first", frame.get_by_role(role)))

    diagnostics = []
    for strategy, locator in candidates:
        try:
            count = await locator.count()
            diagnostics.append({"strategy": strategy, "count": count})
            if count:
                return locator.first, strategy, diagnostics
        except Exception as ex:
            diagnostics.append({"strategy": strategy, "error": str(ex)[:100]})
    return None, "none", diagnostics


async def _reveal_in_scroll_containers(locator) -> None:
    """Center an element inside nested scroll containers, including when an overlay clips it."""
    await locator.evaluate("""el => {
        let parent = el.parentElement;
        while (parent) {
            const style = getComputedStyle(parent);
            const scrollY = /(auto|scroll)/.test(style.overflowY) && parent.scrollHeight > parent.clientHeight;
            const scrollX = /(auto|scroll)/.test(style.overflowX) && parent.scrollWidth > parent.clientWidth;
            if (scrollY || scrollX) {
                const er = el.getBoundingClientRect();
                const pr = parent.getBoundingClientRect();
                if (scrollY) parent.scrollTop += (er.top + er.height / 2) - (pr.top + pr.height / 2);
                if (scrollX) parent.scrollLeft += (er.left + er.width / 2) - (pr.left + pr.width / 2);
            }
            parent = parent.parentElement;
        }
        el.scrollIntoView({block: 'nearest', inline: 'nearest'});
    }""", timeout=3000)


async def _refresh_element(e: Dict) -> Optional[Dict]:
    """Re-observe and recover an element after DOM/frame churn."""
    await observe_elements(compact=False, limit=1)
    candidates = list(_cache().values())
    ranked = []
    for candidate in candidates:
        score = 0
        if candidate.get("role") == e.get("role"):
            score += 3
        if candidate.get("name") == e.get("name"):
            score += 5
        if candidate.get("placeholder") == e.get("placeholder") and e.get("placeholder"):
            score += 2
        if candidate.get("frame_url") == e.get("frame_url"):
            score += 2
        if candidate.get("idAttr") == e.get("idAttr") and e.get("idAttr"):
            score += 6
        if score:
            ranked.append((score, candidate))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def _resolve_frame(page, e: Dict):
    """Resolve the element's frame — cached Frame OBJECT first (stable live
    handle, immune to frame reordering/URL collisions), then by URL, then index."""
    fid = e.get("id")
    frame_cache = _frame_obj_cache()
    if fid in frame_cache:
        try:
            # verify the frame is still attached
            _ = frame_cache[fid].url
            return frame_cache[fid]
        except Exception:
            pass
    url = e.get("frame_url", "")
    if url:
        for f in page.frames:
            if (f.url or "")[:200] == url:
                return f
    try:
        return page.frames[e["frame"]]
    except (IndexError, KeyError):
        return None


async def _frame_page_offset(frame):
    """Offset of a frame's viewport origin in top-page coordinates (x, y).

    Walks the ancestor chain, summing each enclosing iframe's bounding-box
    origin. Used to translate AX boundingBox (frame coords) into page coords
    for precise mouse clicks on elements Playwright locators cannot reach
    (closed shadow DOM / deeply nested web components).
    """
    ox = oy = 0.0
    cur = frame
    while cur is not None and cur.parent_frame is not None:
        parent = cur.parent_frame
        name = getattr(cur, "name", "") or ""
        url = cur.url or ""
        target = None
        try:
            for ifr in await parent.locator("iframe, frame").all():
                attrs = await ifr.evaluate(
                    "el => ({src: el.getAttribute('src')||'', "
                    "name: el.getAttribute('name')||'', "
                    "srcdoc: !!el.getAttribute('srcdoc')})"
                )
                if name and attrs["name"] == name:
                    target = ifr
                    break
                if attrs["src"] and url.startswith(attrs["src"]):
                    target = ifr
                    break
                if attrs["srcdoc"] and url.startswith("about:srcdoc"):
                    target = ifr
                    break
        except Exception:
            target = None
        if target is not None:
            box = await target.bounding_box()
            if box:
                ox += box["x"]
                oy += box["y"]
        cur = parent
    return ox, oy


async def _click_box(page, frame, e):
    """Click the observed element's bounding-box center in page coordinates."""
    box = e.get("boundingBox")
    if not box:
        raise RuntimeError("no bounding box in cache for element")
    ox, oy = await _frame_page_offset(frame)
    x = ox + box["x"] + (box.get("width", 0) or 0) / 2
    y = oy + box["y"] + (box.get("height", 0) or 0) / 2
    await page.mouse.move(x, y)
    await page.mouse.click(x, y)
    return {"x": round(x), "y": round(y)}


async def click_element(element_id: str) -> Dict[str, Any]:
    """Click an observed element by id (e_N). Cross-frame safe."""
    cache = _cache()
    e = cache.get(element_id)
    if not e:
        await observe_elements()
        e = cache.get(element_id)
    if not e:
        return {"clicked": False, "failure_class": "element_not_found", "error": f"unknown element id {element_id} — call observe first"}
    page = await browser._get_page()
    frame = _resolve_frame(page, e)
    if frame is None:
        refreshed = await _refresh_element(e)
        if refreshed:
            e = refreshed
            frame = _resolve_frame(page, e)
        if frame is None:
            return {"clicked": False, "failure_class": "wrong_frame", "error": f"frame for {element_id} gone after recovery"}
    try:
        loc, strategy, diagnostics = await _locate(frame, e)
        # Degenerate fallback: "role-first" resolves to the FIRST element of the
        # role in the DOM (e.g. the hamburger button), NOT the observed element.
        # This happens for elements inside closed shadow DOM that no locator can
        # reach. When we have observed bounds, click the exact box instead of
        # the wrong element.
        if strategy == "role-first" and e.get("boundingBox"):
            try:
                point = await _click_box(page, frame, e)
                return {
                    "clicked": True,
                    "element": element_id,
                    "role": e["role"],
                    "name": e["name"],
                    "frame": e["frame"],
                    "framePath": e.get("framePath", []),
                    "shadowDepth": e.get("shadowDepth", 0),
                    "closedShadowCaptured": e.get("closedShadowCaptured", False),
                    "via": "coordinates",
                    "strategy": "bounding-box",
                    "recovered": True,
                    "selector_recovered": False,
                    "point": point,
                    "attempts": [{"via": "role-first", "error": "locator fallback would target the first DOM element of that role; used observed bounding box instead"}],
                    "diagnostics": diagnostics,
                }
            except Exception as box_err:
                loc, strategy, diagnostics = await _locate(frame, e)  # refresh; fall through
        if loc is None:
            refreshed = await _refresh_element(e)
            if refreshed:
                e = refreshed
                frame = _resolve_frame(page, e)
                if frame:
                    loc, strategy, diagnostics = await _locate(frame, e)
        if loc is None:
            # Last resort before failing: click the observed bounds directly.
            if e.get("boundingBox"):
                try:
                    point = await _click_box(page, frame, e)
                    return {
                        "clicked": True,
                        "element": element_id,
                        "role": e["role"],
                        "name": e["name"],
                        "frame": e["frame"],
                        "framePath": e.get("framePath", []),
                        "shadowDepth": e.get("shadowDepth", 0),
                        "closedShadowCaptured": e.get("closedShadowCaptured", False),
                        "via": "coordinates",
                        "strategy": "bounding-box",
                        "recovered": True,
                        "selector_recovered": False,
                        "point": point,
                        "diagnostics": diagnostics,
                    }
                except Exception as box_err:
                    return {
                        "clicked": False,
                        "failure_class": "element_not_found",
                        "error": "no locator resolved and bounding-box click failed",
                        "box_error": str(box_err)[:180],
                        "diagnostics": diagnostics,
                    }
            return {
                "clicked": False,
                "failure_class": "element_not_found",
                "error": "no locator resolved",
                "diagnostics": diagnostics,
            }
        attempts = []
        try:
            await _reveal_in_scroll_containers(loc.first)
            await loc.scroll_into_view_if_needed(timeout=3000)
            await loc.first.click(timeout=6000)
            via = "locator"
        except Exception as first_error:
            attempts.append({"via": "locator", "error": str(first_error)[:180]})
            try:
                await loc.first.click(timeout=4000, force=True)
                via = "force-click"
            except Exception as force_error:
                attempts.append({"via": "force-click", "error": str(force_error)[:180]})
                try:
                    await loc.first.evaluate("el => el.click()", timeout=4000)
                    via = "js-click"
                except Exception as js_error:
                    attempts.append({"via": "js-click", "error": str(js_error)[:180]})
                    message = " ".join(a["error"] for a in attempts).lower()
                    failure = "click_intercepted" if "intercept" in message or "overlay" in message else "stale_element" if "detached" in message else "element_not_found"
                    return {
                        "clicked": False,
                        "failure_class": failure,
                        "error": str(js_error)[:250],
                        "element": element_id,
                        "strategy": strategy,
                        "attempts": attempts,
                        "diagnostics": diagnostics,
                    }
        selector_recovered = bool(e.get("idAttr") and strategy != "id")
        return {
            "clicked": True,
            "element": element_id,
            "role": e["role"],
            "name": e["name"],
            "frame": e["frame"],
            "framePath": e.get("framePath", []),
            "shadowDepth": e.get("shadowDepth", 0),
            "closedShadowCaptured": e.get("closedShadowCaptured", False),
            "via": via,
            "strategy": strategy,
            "recovered": bool(attempts) or selector_recovered,
            "selector_recovered": selector_recovered,
            "attempts": attempts,
        }
    except Exception as ex:
        return {"clicked": False, "failure_class": "agent_action", "error": str(ex)[:250], "element": element_id}


async def fill_element(element_id: str, text: str) -> Dict[str, Any]:
    """Fill an observed element by id (e_N). Cross-frame safe."""
    cache = _cache()
    e = cache.get(element_id)
    if not e:
        await observe_elements()
        e = cache.get(element_id)
    if not e:
        return {"filled": False, "failure_class": "element_not_found", "error": f"unknown element id {element_id} — call observe first"}
    page = await browser._get_page()
    frame = _resolve_frame(page, e)
    if frame is None:
        return {"filled": False, "failure_class": "wrong_frame", "error": f"frame for {element_id} gone — re-observe"}
    try:
        loc, strategy, diagnostics = await _locate(frame, e)
        if loc is None:
            return {"filled": False, "failure_class": "element_not_found", "error": "no locator resolved", "diagnostics": diagnostics}
        try:
            await _reveal_in_scroll_containers(loc.first)
            await loc.scroll_into_view_if_needed(timeout=3000)
            await loc.fill(text, timeout=8000)
            via = "fill"
        except Exception as first_error:
            try:
                await loc.click(timeout=3000, force=True)
                await loc.press("Control+A")
                await loc.press_sequentially(text, delay=10)
                via = "keyboard-recovery"
            except Exception as second_error:
                return {
                    "filled": False,
                    "failure_class": "stale_element" if "detached" in str(second_error).lower() else "agent_action",
                    "error": str(second_error)[:250],
                    "attempts": [str(first_error)[:180], str(second_error)[:180]],
                    "strategy": strategy,
                }
        value = await loc.input_value(timeout=3000)
        verification_attempts = []
        if value != text:
            verification_attempts.append({"via": via, "value": value, "reason": "verification_mismatch"})
            try:
                await loc.click(timeout=3000, force=True)
                await loc.press("Control+A")
                await loc.press_sequentially(text, delay=5)
                value = await loc.input_value(timeout=3000)
                via = "keyboard-verification-recovery"
            except Exception as keyboard_error:
                verification_attempts.append({"via": "keyboard-verification-recovery", "error": str(keyboard_error)[:180]})
        if value != text:
            try:
                await loc.evaluate(
                    "(el, value) => { el.focus(); el.value = value; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
                    text,
                    timeout=3000,
                )
                value = await loc.input_value(timeout=3000)
                via = "js-input-recovery"
            except Exception as js_error:
                verification_attempts.append({"via": "js-input-recovery", "error": str(js_error)[:180]})
        verified = value == text
        return {
            "filled": verified,
            "verified": verified,
            "value": value,
            "element": element_id,
            "role": e["role"],
            "frame": e["frame"],
            "framePath": e.get("framePath", []),
            "shadowDepth": e.get("shadowDepth", 0),
            "closedShadowCaptured": e.get("closedShadowCaptured", False),
            "strategy": strategy,
            "via": via,
            "attempts": verification_attempts,
            **({"failure_class": "agent_action", "error": "input value did not match requested text"} if not verified else {}),
        }
    except Exception as ex:
        return {"filled": False, "failure_class": "agent_action", "error": str(ex)[:250], "element": element_id}


async def wait_element_stable(element_id: str, stable_ms: int = 800, timeout: int = 8000) -> Dict[str, Any]:
    """Wait until an observed element's bounding box stops moving."""
    e = _cache().get(element_id)
    if not e:
        return {"stable": False, "failure_class": "element_not_found", "error": f"unknown element id {element_id}"}
    page = await browser._get_page()
    frame = _resolve_frame(page, e)
    if frame is None:
        return {"stable": False, "failure_class": "wrong_frame", "error": f"frame for {element_id} is gone"}
    loc, strategy, diagnostics = await _locate(frame, e)
    if loc is None:
        return {"stable": False, "failure_class": "element_not_found", "diagnostics": diagnostics}

    started = time.perf_counter()
    stable_started = None
    previous = None
    samples = 0
    while (time.perf_counter() - started) * 1000 < timeout:
        try:
            box = await loc.bounding_box(timeout=1000)
        except Exception:
            box = None
        samples += 1
        current = None if box is None else tuple(round(box[key], 1) for key in ("x", "y", "width", "height"))
        if current is not None and current == previous:
            stable_started = stable_started or time.perf_counter()
            if (time.perf_counter() - stable_started) * 1000 >= stable_ms:
                return {
                    "stable": True,
                    "element": element_id,
                    "strategy": strategy,
                    "boundingBox": box,
                    "samples": samples,
                    "waited_ms": round((time.perf_counter() - started) * 1000),
                }
        else:
            stable_started = None
            previous = current
        await asyncio.sleep(0.1)
    return {
        "stable": False,
        "failure_class": "timing",
        "element": element_id,
        "samples": samples,
        "error": f"element did not settle within {timeout}ms",
    }


async def evaluate(frame_index: int = 0, code: str = "") -> Dict[str, Any]:
    """Run JS in a specific frame (default: main frame). code = JS expression
    or arrow-function body. Cross-frame escape hatch — runs inside ANY frame
    (mail.com's compose iframe would work with frame_index=5)."""
    page = await browser._get_page()
    try:
        frame = page.frames[frame_index]
    except (IndexError, AttributeError):
        return {"error": f"frame {frame_index} not found", "frame_index": frame_index}
    try:
        result = await frame.evaluate(code)
        return {
            "result": result,
            "frame": frame_index,
            "framePath": _frame_path(frame, page.frames),
            "url": frame.url,
        }
    except Exception as ex:
        return {"error": str(ex)[:200], "frame": frame_index}


async def run_code(code: str = "") -> Dict[str, Any]:
    """Run async PYTHON Playwright code against the live page (RCE-equivalent —
    trusted local server only). `page`, `frames`, `context`, `browser` are in
    scope as Python variables. Example:
        t = await page.title()
        return {"title": t, "frameCount": len(frames)}
    """
    if not ALLOW_BROWSER_RUN_CODE:
        return {
            "blocked": True,
            "reason": "trusted_clients_only",
            "message": "Set LOOT_BROWSER_ALLOW_RUN_CODE=1 before server startup to enable Python Playwright execution.",
        }
    if not code.strip():
        return {"error": "code is required"}
    page = await browser._get_page()
    frames = page.frames
    context = browser.get_active_context()
    browser_obj = await browser._get_browser()
    ns: Dict[str, Any] = {}
    body = "\n".join("    " + line for line in code.splitlines())
    fn = f"async def _run(page, frames, context, browser):\n{body}\n"
    try:
        exec(fn, ns)
        result = await ns["_run"](page, frames, context, browser_obj)
        return {"result": result}
    except Exception as ex:
        return {"error": str(ex)[:200]}
