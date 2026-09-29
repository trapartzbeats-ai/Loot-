# Loot Browser Agent Guide

This is the shortest reliable operating contract for browser agents.

## Start a task

1. Call `browser_start` with a stable profile, URL, mode, and optional engine.
2. Use `browser_action` for ordinary named clicks and fills.
3. Use `browser_state` when you need page/profile status or a compact element list.
4. Use `browser_wait` for delayed text, selectors, or numeric progress instead of polling from the agent.
5. Use `browser_debug` only when something fails or the page behaves unexpectedly.
6. Fall back to `observe_elements` / `find_element` and element IDs for ambiguity or diagnostics.
7. Call `save_session` after authentication or important storage changes.

Use `server_core.py` for normal agents. It initially exposes 14 tools while
retaining 71 underlying browser capabilities through FastMCP v3 BM25 discovery.
Use `search_tools` with a capability phrase, then `call_tool` with the exact
returned name. Use `server.py` when the task needs scraping, vision, desktop
control, extensions, Zext tools, or direct access to the complete catalog.

MCP clients are isolated automatically. Each gets a private browser context,
active tab, perception cache, logs, mocks, engine choice, and profile pointer.
Use `client_isolation_status` when diagnosing client/session mix-ups.

## Perception and actions

- Prefer `observe_elements`, `find_element`, `click_element`, and
  `fill_element`; these traverse Playwright frames and open Shadow DOM.
- Closed roots requested after context initialization are captured early by
  default and their elements report `closedShadowCaptured=true`. Check
  `closed_shadow_status` for observed hosts and the remaining hard boundary.
- Compact observations are the default. Page them with `offset` and `limit`.
- Re-observe after navigation or major DOM changes because element IDs are
  generation-scoped.
- Use `page_frames` / `read_frame` for hierarchy and text.
- Use `evaluate_in_frame` for frame-specific JavaScript, including
  Playwright-exposed cross-origin frames.
- Raw CSS `click`, `type_text`, and `wait_for` operate on the main page only.
- `browser_action` automatically tries bounded Florence screenshot grounding
  only after semantic/frame/Shadow DOM recovery fails. Set
  `visual_recovery=false` for DOM-only actions.

## Shared rooms

Isolation remains the default. Use `shared_browser_join(name)` from each client
only when agents deliberately need the same tabs and cookies. A joined client
shares page state immediately. When two or more clients are present, acquire a
bounded mutation lease with `shared_browser_acquire`, pass the returned token
to `shared_browser_release` when done, and let other clients observe meanwhile.
Mutation without the lease returns `shared_room_locked` instead of racing.
`shared_browser_leave()` restores its
private state, and ordinary `browser_close()` detaches instead of closing the
room unless `close_shared=true` is explicit.

## SPA readiness

Navigation detects common SPA roots and reports `static_ready`, `hydrated`, or
`hydration_timeout`. Use `spa_readiness` to re-check manually and
`browser_wait` for an application-specific readiness condition.

## Extensions, macros, tasks, and media

- MV3 extensions run in persistent Chromium contexts and are lifecycle-tested
  for content injection and restart recovery.
- `extension_store_catalog` and `extension_store_install` provide staged,
  path-validated local or zip catalog installation. Updates are explicit.
- `macro_start`, `macro_stop`, and `macro_replay` persist high-level semantic
  workflows; replay allows only the recorded browser-operation allowlist.
- `webarena_configure` and `webarena_start` connect to an externally hosted
  WebArena/BrowserGym deployment; the adapter does not bundle that environment.
- `media_discover` scans all frames and `media_download` uses the active
  authenticated context with a bounded file-size limit.

## Debugging

- `network_requests`: inspect method, URL, status, resource type, failures, and
  frame URL. Filter with `url_contains`, `method`, or `since_timestamp`.
- `network_mock`: install a client-scoped URL-glob mock before the request.
- `network_unmock`: remove one pattern or all current-client mocks.
- `console_logs`: inspect console messages and uncaught page errors. Use
  `level` and `clear` to keep responses compact.
- `session_health`: reports `login_required`, `reauth_recommended`, or
  `healthy`. Healthy means no login indicators were detected; it is not proof
  of authorization.
- `bot_protection_status`: returns challenge signals, HTTP status when known,
  and structured recovery routes. Prefer headed manual handoff over repeated
  retries when a challenge is confirmed.
- `system_health`: checks browser pools, profiles, disk, canonical screenshot,
  and local LOOT/SmolVLM/Florence/Ollama endpoints. Monitoring starts on the
  first `browser_start`; use `health_monitor_start/stop` to change its lifecycle.

## Browser engines and safety

- Chromium, Firefox, and WebKit runtimes are installed on this machine.
- `configure_browser(engine="chromium", stealth=true)` reduces basic webdriver
  signals but is not a promise that bot protection will be bypassed.
- `run_browser_code` is an RCE-equivalent Python escape hatch and remains
  blocked unless the trusted server owner sets
  `LOOT_BROWSER_ALLOW_RUN_CODE=1` before startup.
- Closed Shadow DOM is still inaccessible when created before instrumentation
  or inside an isolated extension world. Set
  `LOOT_BROWSER_CAPTURE_CLOSED_SHADOW=0` to preserve native closed-root semantics.

## Browser modes

- `headless`: fastest default mode, with no visible window or automatic dialog handling.
- `headed`: opens a real visible browser window. Use `signal_user` for a banner
  or `wait_for_user` when a person must complete CAPTCHA, login, verification,
  or another manual step. The handoff survives page navigation and completes
  only when the user presses Done.
- `silent`: hidden browser with basic anti-detection hints, muted audio,
  disabled notifications, automatic dialog dismissal, and optional lightweight
  ad/tracker blocking.

Mode and engine settings are client-scoped. Tabs, active-tab selection,
viewport, engine, mode, and ad-block preference are stored per profile and
restored after a mode switch or server restart. Common secret-like URL query
parameters are removed before tab URLs are written to disk.

Useful tools: `set_browser_mode`, `get_browser_mode`, `is_browser_visible`,
`set_viewport`, `signal_user`, and `wait_for_user`.

## Self-test

Run all permanent scenarios:

```powershell
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\agent_benchmark.py
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\closed_shadow_benchmark.py
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\operational_gaps_benchmark.py
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\shared_browser_benchmark.py
```

Expected current result: `16/16`, `100%`, empty `failure_summary`.
