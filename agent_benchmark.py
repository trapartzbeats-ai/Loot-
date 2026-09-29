"""Repeatable real-site torture tests for loot-browser agents.

The benchmark intentionally reports *why* a scenario failed. It is small enough
to run during development and uses public practice sites with no signup.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any, Dict, List

import browser
import perception

PROFILE = "codex-benchmark"

FAILURE_CLASSES = [
    "element_not_found",
    "wrong_frame",
    "shadow_boundary",
    "stale_element",
    "visual_mismatch",
    "click_intercepted",
    "incorrect_scroll",
    "timing",
    "popup",
    "navigation",
    "network_observation",
    "session_expired",
    "client_isolation",
    "browser_unavailable",
    "agent_action",
    "bot_protection",
    "shared_room_locked",
]

SCENARIOS: Dict[str, Dict[str, str]] = {
    "xqa_nested_frames_read": {
        "url": "https://xqa.io/practice/nested-frames",
        "description": "Read text from the deepest nested frame.",
    },
    "xqa_shadow_fill": {
        "url": "https://xqa.io/practice/shadow-dom",
        "description": "Fill and verify an input inside an open shadow root.",
    },
    "xqa_shadow_in_iframe_click": {
        "url": "https://xqa.io/practice/shadow-dom-in-iframe",
        "description": "Click a shadow-root button inside an iframe.",
    },
    "xqa_iframe_in_shadow_click": {
        "url": "https://xqa.io/practice/iframe-in-shadow-dom",
        "description": "Click a button in an iframe hosted by a shadow root.",
    },
    "lastest_shadow_actions": {
        "url": "https://lastest.cloud/playground/shadow-dom",
        "description": "Fill and click controls inside Lastest shadow roots.",
    },
    "lastest_nested_frame": {
        "url": "https://lastest.cloud/playground/iframes",
        "description": "Read and interact with the deepest Lastest iframe.",
    },
    "expand_shadow_click": {
        "url": "https://practice.expandtesting.com/shadowdom",
        "description": "Click Expand Testing's shadow-root button.",
    },
    "uitp_dynamic_click": {
        "url": "https://www.uitestingplayground.com/dynamicid",
        "description": "Recover from HTTPS mismatch and click a dynamic-ID button.",
    },
    "lastest_tricky_recovery": {
        "url": "https://lastest.cloud/playground/tricky",
        "description": "Recover from stale IDs, NBSP text, duplicates, motion, and overlays.",
    },
    "cross_origin_frame_evaluate": {
        "url": "https://example.com",
        "description": "Evaluate JavaScript inside a genuinely different-origin iframe.",
    },
    "network_console_mock": {
        "url": "https://example.com",
        "description": "Mock an API response and observe both network and console events.",
    },
    "session_expiry_detection": {
        "url": "https://example.com",
        "description": "Detect a login prompt and return a re-authentication recommendation.",
    },
    "concurrent_client_isolation": {
        "url": "https://example.com",
        "description": "Keep concurrent MCP client contexts, pages, profiles, and URLs isolated.",
    },
    "multi_browser_navigation": {
        "url": "https://example.com",
        "description": "Navigate in Chromium, Firefox, and WebKit and verify Chromium stealth hints.",
    },
    "browser_mode_lifecycle": {
        "url": "https://example.com",
        "description": "Preserve tabs through headed/silent/headless switches and exercise handoff plus silent controls.",
    },
    "silent_anti_detection_site": {
        "url": "https://bot.sannysoft.com",
        "description": "Load an anti-detection playground in silent mode and verify basic browser signals.",
    },
}


def list_scenarios() -> Dict[str, Any]:
    return {
        "profile": PROFILE,
        "failure_classes": FAILURE_CLASSES,
        "scenarios": [
            {"id": scenario_id, **definition}
            for scenario_id, definition in SCENARIOS.items()
        ],
    }


def _deepest_frame(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []

    def visit(node: Dict[str, Any], depth: int) -> None:
        candidates.append({**node, "depth": depth})
        for child in node.get("children", []):
            visit(child, depth + 1)

    for root in nodes:
        visit(root, 0)
    return max(candidates, key=lambda item: item["depth"]) if candidates else {}


async def _find(query: str) -> Dict[str, Any]:
    result = await perception.find(query, compact=False)
    return result.get("matches", [None])[0] if result.get("matches") else {}


async def _act(element: Dict[str, Any], action: str, text: str = "") -> Dict[str, Any]:
    if not element:
        return {
            "success": False,
            "failure_class": "element_not_found",
            "error": "target was not perceived",
        }
    result = (
        await perception.fill_element(element["id"], text)
        if action == "fill"
        else await perception.click_element(element["id"])
    )
    success = bool(result.get("filled") if action == "fill" else result.get("clicked"))
    return {"success": success, "target": element, "result": result}


async def _isolated_client_probe(scope: str, profile: str, url: str) -> Dict[str, Any]:
    token = browser.set_client_scope(scope)
    try:
        await browser.switch_session(profile)
        navigation = await browser.navigate(url, 30000)
        status = await browser.client_isolation_status()
        await asyncio.sleep(0.2)
        return {"navigation": navigation, "status": status}
    finally:
        await browser.browser_close()
        await asyncio.sleep(0.1)
        browser.reset_client_scope(token)


async def _run_one(scenario_id: str) -> Dict[str, Any]:
    definition = SCENARIOS[scenario_id]
    started = time.perf_counter()
    report: Dict[str, Any] = {
        "id": scenario_id,
        "url": definition["url"],
        "description": definition["description"],
        "passed": False,
        "steps": [],
    }
    navigation = await browser.navigate(definition["url"], 30000)
    report["steps"].append({"navigate": navigation})
    if not navigation.get("navigated", True):
        report["failure_class"] = "navigation"
        report["error"] = navigation.get("error", "navigation failed")
        report["duration_ms"] = round((time.perf_counter() - started) * 1000)
        return report

    if scenario_id == "xqa_nested_frames_read":
        tree = await perception.frame_tree()
        deepest = _deepest_frame(tree.get("frames", []))
        frame_result = await perception.read_frame(deepest.get("index", -1))
        report["steps"].extend([{"frame_tree": tree}, {"deepest_frame": frame_result}])
        report["passed"] = bool(
            frame_result.get("found")
            and frame_result.get("frameDepth", 0) >= 2
            and frame_result.get("text", "").strip()
        )
        if not report["passed"]:
            report["failure_class"] = "wrong_frame"

    elif scenario_id == "xqa_shadow_fill":
        action = await _act(await _find("Type here"), "fill", "Zoro was here")
        report["steps"].append({"fill": action})
        report["passed"] = bool(
            action.get("success")
            and action.get("result", {}).get("verified")
            and action.get("target", {}).get("shadowDepth", 0) >= 1
        )
        if not report["passed"]:
            report["failure_class"] = action.get("result", {}).get("failure_class", "shadow_boundary")

    elif scenario_id == "xqa_shadow_in_iframe_click":
        action = await _act(await _find("Click Destiny"), "click")
        report["steps"].append({"click": action})
        report["passed"] = bool(
            action.get("success")
            and action.get("target", {}).get("frameDepth", 0) >= 1
            and action.get("target", {}).get("shadowDepth", 0) >= 1
        )
        if not report["passed"]:
            report["failure_class"] = action.get("result", {}).get("failure_class", "shadow_boundary")

    elif scenario_id == "xqa_iframe_in_shadow_click":
        action = await _act(await _find("Current Destiny"), "click")
        report["steps"].append({"click": action})
        report["passed"] = bool(action.get("success") and action.get("target", {}).get("frameDepth", 0) >= 1)
        if not report["passed"]:
            report["failure_class"] = action.get("result", {}).get("failure_class", "wrong_frame")

    elif scenario_id == "lastest_shadow_actions":
        fill = await _act(await _find("SHADOW INPUT"), "fill", "Zoro was here")
        click = await _act(await _find("Click me"), "click")
        report["steps"].extend([{"fill": fill}, {"click": click}])
        report["passed"] = bool(fill.get("success") and click.get("success"))
        if not report["passed"]:
            failed = fill if not fill.get("success") else click
            report["failure_class"] = failed.get("result", {}).get("failure_class", "shadow_boundary")

    elif scenario_id == "lastest_nested_frame":
        tree = await perception.frame_tree()
        deepest = _deepest_frame(tree.get("frames", []))
        frame_result = await perception.read_frame(deepest.get("index", -1))
        click = await _act(await _find("Message the top page"), "click")
        report["steps"].extend([{"deepest_frame": frame_result}, {"click": click}])
        report["passed"] = bool(frame_result.get("text", "").strip() and click.get("success"))
        if not report["passed"]:
            report["failure_class"] = click.get("result", {}).get("failure_class", "wrong_frame")

    elif scenario_id == "expand_shadow_click":
        action = await _act(await _find("inside a Shadow DOM"), "click")
        report["steps"].append({"click": action})
        report["passed"] = bool(action.get("success") and action.get("target", {}).get("shadowDepth", 0) >= 1)
        if not report["passed"]:
            report["failure_class"] = action.get("result", {}).get("failure_class", "shadow_boundary")

    elif scenario_id == "uitp_dynamic_click":
        action = await _act(await _find("Button with Dynamic ID"), "click")
        report["steps"].append({"click": action})
        report["passed"] = bool(action.get("success"))
        if not report["passed"]:
            report["failure_class"] = action.get("result", {}).get("failure_class", "element_not_found")

    elif scenario_id == "lastest_tricky_recovery":
        dynamic = await _find("Click me by my text")
        await browser.reload()
        stale_click = await _act(dynamic, "click")

        nbsp_click = await _act(await _find("Submit Order"), "click")

        submit_matches = (await perception.find("Submit")).get("matches", [])
        exact_submits = [
            element for element in submit_matches
            if element.get("role") == "button" and " ".join(element.get("name", "").replace("\u00a0", " ").split()) == "Submit"
        ]
        middle_submit = await _act(exact_submits[1] if len(exact_submits) >= 2 else {}, "click")

        moving = await _find("Click me when I stop moving")
        stable = await perception.wait_element_stable(moving.get("id", ""), 800, 8000) if moving else {"stable": False, "failure_class": "element_not_found"}
        moving_click = await _act(moving, "click") if stable.get("stable") else {"success": False, "result": stable}

        covered_click = await _act(await _find("Perfectly normal button"), "click")
        interception_detected = bool(covered_click.get("result", {}).get("attempts"))

        report["steps"].extend([
            {"stale_dynamic_click": stale_click},
            {"nbsp_click": nbsp_click},
            {"middle_submit": middle_submit},
            {"wait_stable": stable},
            {"moving_click": moving_click},
            {"covered_click": covered_click, "interception_detected": interception_detected},
        ])
        report["passed"] = all([
            stale_click.get("success"),
            nbsp_click.get("success"),
            middle_submit.get("success"),
            stable.get("stable"),
            moving_click.get("success"),
            covered_click.get("success"),
            interception_detected,
        ])
        if not report["passed"]:
            failed = next((step for step in (stale_click, nbsp_click, middle_submit, moving_click, covered_click) if not step.get("success")), {})
            report["failure_class"] = failed.get("result", {}).get("failure_class", stable.get("failure_class", "agent_action"))

    elif scenario_id == "cross_origin_frame_evaluate":
        page = await browser._get_page()
        await page.set_content("<h1>Parent</h1><iframe src='https://example.com'></iframe>")
        for _ in range(50):
            if len(page.frames) >= 2 and page.frames[1].url.startswith("https://example.com"):
                break
            await page.wait_for_timeout(100)
        evaluated = await perception.evaluate(1, "() => ({origin: location.origin, text: document.body.innerText})")
        report["steps"].append({"cross_origin_evaluate": evaluated})
        result = evaluated.get("result", {})
        report["passed"] = bool(result.get("origin") == "https://example.com" and result.get("text", "").strip())
        if not report["passed"]:
            report["failure_class"] = "wrong_frame"

    elif scenario_id == "network_console_mock":
        mocked = await browser.network_mock("**/api/loot-benchmark", '{"ok":true,"source":"loot"}', 207)
        page = await browser._get_page()
        fetched = await page.evaluate("""async () => {
            const response = await fetch('/api/loot-benchmark');
            return {status: response.status, body: await response.json()};
        }""")
        await page.evaluate("() => console.warn('loot-benchmark-console')")
        await page.wait_for_timeout(100)
        requests = await browser.network_requests(url_contains="loot-benchmark")
        logs = await browser.console_logs(level="warning")
        unmocked = await browser.network_unmock("**/api/loot-benchmark")
        report["steps"].append({
            "mock": mocked, "fetch": fetched, "requests": requests,
            "console": logs, "unmock": unmocked,
        })
        report["passed"] = bool(
            fetched.get("status") == 207
            and fetched.get("body", {}).get("source") == "loot"
            and requests.get("matched", 0) >= 1
            and logs.get("matched", 0) >= 1
            and unmocked.get("count") == 1
        )
        if not report["passed"]:
            report["failure_class"] = "network_observation"

    elif scenario_id == "session_expiry_detection":
        page = await browser._get_page()
        await page.set_content("<form><input name='email'><input type='password'></form>")
        health = await browser.session_health("https://example.com/private")
        report["steps"].append({"session_health": health})
        report["passed"] = bool(
            health.get("status") == "login_required"
            and health.get("login_required")
            and health.get("recommendation")
        )
        if not report["passed"]:
            report["failure_class"] = "session_expired"

    elif scenario_id == "concurrent_client_isolation":
        first, second = await asyncio.gather(
            _isolated_client_probe("benchmark-client-a", "benchmark-isolation-a", "https://example.com"),
            _isolated_client_probe("benchmark-client-b", "benchmark-isolation-b", "https://www.iana.org/help/example-domains"),
        )
        first_status, second_status = first["status"], second["status"]
        report["steps"].append({"client_a": first, "client_b": second})
        report["passed"] = bool(
            first["navigation"].get("navigated")
            and second["navigation"].get("navigated")
            and first_status.get("context_id") != second_status.get("context_id")
            and first_status.get("page_id") != second_status.get("page_id")
            and first_status.get("session_id") != second_status.get("session_id")
            and first_status.get("url", "").startswith("https://example.com")
            and second_status.get("url", "").startswith("https://www.iana.org")
        )
        if not report["passed"]:
            report["failure_class"] = "client_isolation"

    elif scenario_id == "multi_browser_navigation":
        engines: Dict[str, Any] = {}
        for engine in ("chromium", "firefox", "webkit"):
            configured = await browser.configure_browser(engine, stealth=(engine == "chromium"))
            entry: Dict[str, Any] = {"configure": configured}
            if configured.get("configured"):
                entry["navigate"] = await browser.navigate("https://example.com", 30000)
                if engine == "chromium":
                    page = await browser._get_page()
                    entry["webdriver"] = await page.evaluate("() => navigator.webdriver")
            engines[engine] = entry
        reset = await browser.configure_browser("chromium", stealth=False)
        report["steps"].append({"engines": engines, "reset": reset})
        report["passed"] = bool(
            all(
                entry.get("configure", {}).get("configured")
                and entry.get("navigate", {}).get("navigated")
                for entry in engines.values()
            )
            and engines.get("chromium", {}).get("webdriver") is None
            and reset.get("configured")
        )
        if not report["passed"]:
            report["failure_class"] = "browser_unavailable"

    elif scenario_id == "browser_mode_lifecycle":
        await browser.set_browser_mode("headless", auto_block_ads=False)
        initial_tabs = await browser.list_tabs()
        await browser.new_tab("https://www.iana.org/help/example-domains")
        expected_tabs = len(initial_tabs["tabs"]) + 1
        headed = await browser.set_browser_mode("headed")
        headed_tabs = await browser.list_tabs()
        visible = await browser.is_browser_visible()
        viewport = await browser.set_viewport(1280, 800)
        headed_page = await browser._get_page()
        signals = await headed_page.evaluate("() => ({webdriver: navigator.webdriver, ua: navigator.userAgent})")
        signal = await browser.signal_user("Automated benchmark handoff")
        wait_task = asyncio.create_task(browser.wait_for_user("complete benchmark handoff", 10))
        await asyncio.sleep(0.5)
        await headed_page.goto("https://example.com", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(0.8)
        await headed_page.locator("#__loot_agent_wait button").click()
        handoff = await wait_task

        silent = await browser.set_browser_mode("silent", auto_block_ads=True)
        silent_tabs = await browser.list_tabs()
        silent_page = await browser._get_page()
        await silent_page.set_content("<button id='alert' onclick=\"alert('benchmark-dialog')\">Alert</button>")
        await silent_page.locator("#alert").click()
        await silent_page.wait_for_timeout(100)
        try:
            await silent_page.goto("https://doubleclick.net/loot-mode-benchmark", timeout=5000)
        except Exception:
            pass
        silent_status = await browser.get_browser_mode()
        reset = await browser.set_browser_mode("headless", auto_block_ads=False)
        reset_tabs = await browser.list_tabs()
        while len((await browser.list_tabs())["tabs"]) > len(initial_tabs["tabs"]):
            tabs = (await browser.list_tabs())["tabs"]
            await browser.close_tab(len(tabs) - 1)
        report["steps"].append({
            "initial_tabs": initial_tabs,
            "headed": headed,
            "headed_tabs": headed_tabs,
            "visible": visible,
            "viewport": viewport,
            "signals": signals,
            "signal": signal,
            "handoff": handoff,
            "silent": silent,
            "silent_tabs": silent_tabs,
            "silent_status": silent_status,
            "reset": reset,
            "reset_tabs": reset_tabs,
        })
        report["passed"] = bool(
            headed.get("mode") == "headed"
            and visible.get("visible")
            and len(headed_tabs["tabs"]) == expected_tabs
            and viewport.get("outer_window")
            and signals.get("webdriver") is None
            and "HeadlessChrome" not in signals.get("ua", "")
            and signal.get("shown")
            and handoff.get("completed")
            and silent.get("mode") == "silent"
            and len(silent_tabs["tabs"]) == expected_tabs
            and silent_status.get("dialogs_dismissed", 0) >= 1
            and silent_status.get("blocked_ads", 0) >= 1
            and reset.get("mode") == "headless"
            and len(reset_tabs["tabs"]) == expected_tabs
        )
        if not report["passed"]:
            report["failure_class"] = "agent_action"

    elif scenario_id == "silent_anti_detection_site":
        silent = await browser.set_browser_mode("silent", auto_block_ads=False)
        navigation = await browser.navigate(definition["url"], 30000)
        page = await browser._get_page()
        signals = await page.evaluate("""() => ({
            webdriver: navigator.webdriver,
            userAgent: navigator.userAgent,
            languages: navigator.languages,
            plugins: navigator.plugins.length,
            title: document.title,
            bodyLength: document.body?.innerText?.length || 0
        })""")
        reset = await browser.set_browser_mode("headless", auto_block_ads=False)
        report["steps"].append({"silent": silent, "navigation_silent": navigation, "signals": signals, "reset": reset})
        report["passed"] = bool(
            navigation.get("navigated")
            and signals.get("webdriver") is None
            and "HeadlessChrome" not in signals.get("userAgent", "")
            and signals.get("languages")
            and signals.get("plugins", 0) >= 1
            and signals.get("bodyLength", 0) > 100
            and reset.get("mode") == "headless"
        )
        if not report["passed"]:
            report["failure_class"] = "visual_mismatch"

    report["duration_ms"] = round((time.perf_counter() - started) * 1000)
    return report


async def run(scenario_ids: List[str] | None = None) -> Dict[str, Any]:
    selected = scenario_ids or list(SCENARIOS)
    unknown = [scenario_id for scenario_id in selected if scenario_id not in SCENARIOS]
    if unknown:
        return {"error": "unknown scenarios", "unknown": unknown, **list_scenarios()}

    await browser.switch_session(PROFILE)
    results = [await _run_one(scenario_id) for scenario_id in selected]
    await browser.save_session(PROFILE)
    passed = sum(1 for result in results if result.get("passed"))
    failures: Dict[str, int] = {}
    for result in results:
        if not result.get("passed"):
            category = result.get("failure_class", "agent_action")
            failures[category] = failures.get(category, 0) + 1
    return {
        "profile": PROFILE,
        "passed": passed,
        "total": len(results),
        "score_percent": round((passed / len(results)) * 100, 1) if results else 0.0,
        "failure_summary": failures,
        "results": results,
    }


async def _cli() -> int:
    scenario_ids = sys.argv[1:]
    try:
        result = await run(scenario_ids or None)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("passed") == result.get("total") else 1
    finally:
        await browser.browser_close(all_clients=True)
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli()))
