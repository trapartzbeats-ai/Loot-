# ZoroBrowser / loot-browser-v3 — Agent Torture Test Baseline

Established 2026-08-09 (Zoro, parallel with Codex's upgrades).
Sites from the CDP perception doc (paste): XQA -> UI Testing Playground -> Expand Testing -> Lastest -> WebArena.

## Baseline Run 1

| Exercise | Result | Notes |
|----------|--------|-------|
| UITP /shadowdom — fill shadow input | PASS | perception finds shadow textbox + buttons through OPEN shadow root |
| UITP /shadowdom — click Generate Text | PASS | value changed (random GUID generated) — click landed inside root |
| UITP /frames — nested srcdoc iframes (2 deep) | PASS | page_frames shows 3 levels; find + click deepest button (e_9 f1, e_13 f2) |
| UITP /shadowdom — probe | PASS | top-doc querySelectorAll sees 0 inputs / 1 shadowRoot; perception sees the shadow elements |
| XQA /practice/shadow-dom/cypress | BLOCKED | Next.js SPA never hydrates in headless: 0 inputs, bodyLen stuck at 2607 (static shell). Bot protection or lazy hydration. Retry with headed/stealth or longer wait. |
| UITP /nestedframes | 404 | real path is /frames |
| example.com find+click (smoke) | PASS | one False during Codex's mid-edit browser.py; re-test after settle: clicked via locator path -> iana.org ✓ |

## Failure categories to track (from doc)
element_not_found / wrong_frame / shadow_boundary / stale_element / visual_mismatch / click_intercepted / incorrect_scroll / timing / popup / agent_reasoning

## Next exercises (unrun)
- UITP: /click, /hiddenlayers, /dynamalid, /loaddelay, /ajax, /scrollbars, /overlay, /spinner, /clientdelay, /alert, /mouseover, /textinput, /progressbar
- Expand Testing (practice.expandtesting.com): Large & Deep DOM, shadow, infinite scroll
- Lastest Playground: moving buttons, invisible overlays, class soup
- WebArena / BrowserGym: full benchmark (shopping, forum, CMS, maps, wiki, calculator)

## Known boundaries
1. Closed Shadow DOM cannot be pierced through the normal DOM APIs.
2. Raw CSS helpers target the main page. Agents should use `observe_elements`, `find_element`, `click_element`, and `fill_element` for frame/shadow-aware actions.
3. `run_browser_code` is intentionally disabled unless `LOOT_BROWSER_ALLOW_RUN_CODE=1` is set for a trusted local client.
4. Session health is deliberately heuristic: `healthy` means no login indicators were found, not that the site proved the account is authenticated.
5. Stealth mode reduces basic automation signals but cannot guarantee bypass of advanced bot protection.

## P0 Upgrade Pass (Zoro + Codex, 2026-08-09)
- `observe_elements`: compact by default (`id`, `frame`, `role`, `name`), with `offset`/`limit` pagination, a 200-item cap, and `has_more`/`next_offset` metadata. The rich schema remains in the private action cache.
- `find_element`: compact by default, but searches the rich cache so placeholder, frame, shadow, and stale-element recovery still work.
- High-volume observation tools return one compact JSON text payload. FastMCP no longer mirrors the same data into `structuredContent`.
- `evaluate_in_frame(frame_index, code)`: JavaScript in any Playwright frame, including cross-origin frames available through CDP/Playwright traversal.
- `run_browser_code(code)`: async Python Playwright escape hatch with page/frames/context/browser in scope. It is RCE-equivalent and therefore blocked by default behind `LOOT_BROWSER_ALLOW_RUN_CODE=1`.
- Click/fill are unaffected by compact responses because the cache keeps rich action data.
- Codex parallel: read_frame, wait_element_stable, agent_benchmark (FAILURE_CLASSES harness) landed in same window.

## P1/P2 Hardening Pass (Codex, 2026-08-09)
- Per-MCP-client state: independent context, active page, profile, perception cache, network log, console log, mocks, engine, and stealth setting.
- `network_requests`: bounded/filterable response and failure records with method, URL, status, resource type, and frame URL.
- `network_mock` / `network_unmock`: client-scoped Playwright routing with method, status, body, content type, and custom headers.
- `console_logs`: bounded console and uncaught page-error collection with level filtering and clear-on-read support.
- `session_health`: login URL/password-form detection plus configurable 8-hour age warning (`LOOT_BROWSER_SESSION_TTL_SECONDS`). Navigation responses include this result automatically.
- `configure_browser`: Chromium, Firefox, and WebKit selection per client. Chromium supports optional basic stealth hints.
- Firefox 153 and WebKit 26.5 Playwright runtimes installed alongside Chromium on this machine.

## Post-upgrade proof
- Compact pagination smoke: 88 elements, five returned per page, correct `next_offset`, and no mirrored `structuredContent`.
- Frame evaluation smoke: frame 1 on XQA Shadow DOM in Iframe returned the iframe body text and frame path `[0, 1]`.
- Hostile cross-origin proof: an `https://example.com` iframe embedded in a different-origin parent returned its origin and body through `evaluate_in_frame`.
- FastMCP concurrency proof: two simultaneous clients received different MCP scopes, browser contexts, pages, profiles, and preserved URLs.
- Network/console proof: a mocked fetch returned HTTP 207, appeared in `network_requests`, and a warning appeared in `console_logs`.
- Multi-browser proof: Chromium, Firefox, and WebKit each navigated successfully; Chromium stealth returned `navigator.webdriver === undefined`.
- Headed proof: Windows reported a real visible `Google Chrome for Testing` window with a nonzero window handle and the restored Example Domains tab.
- User handoff proof: the headed banner survived a navigation and returned only after the Done button called the browser-to-agent binding.
- Silent proof: alerts auto-dismissed, audio/notifications were disabled, the optional ad route blocked a DoubleClick request, and tabs survived every mode switch.
- Anti-detection playground: bot.sannysoft.com loaded in silent mode with a Chrome 151-matched UA, no `HeadlessChrome`, undefined `navigator.webdriver`, languages, and plugins.
- Permanent real-site benchmark: **16/16 PASS (100%)** after all hardening and browser-mode changes.
