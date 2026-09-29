# Loot Browser Modes

Loot Browser v3.3 supports three modes. Mode state is private to each MCP
client and stored with the active browser profile.

| Mode | Window | Basic anti-detection | Dialogs | Audio | Notifications | Primary use |
|---|---:|---:|---:|---:|---:|---|
| `headless` | hidden | off | manual | normal | normal | fast automation and scraping |
| `headed` | visible | on | manual | normal | normal | CAPTCHA, login, verification, user handoff |
| `silent` | hidden | on | auto-dismiss | muted | disabled | unattended low-noise automation |

## MCP tools

- `set_browser_mode(mode, auto_block_ads?)` switches mode and restores tabs.
- `get_browser_mode()` reports mode controls, counters, viewport, profile, and client scope.
- `is_browser_visible()` confirms whether the current page has a headed window.
- `set_viewport(width, height)` resizes the viewport and headed Chromium window.
- `signal_user(message)` shows a dismissible headed-browser banner.
- `wait_for_user(action, timeout)` shows a Done button and waits up to 600 seconds.

`wait_for_user` uses an exposed browser binding rather than treating a missing
DOM banner as completion. If login or verification navigates the page, the
banner is reinserted and the wait continues until the user presses Done.

## Persistence

Profile metadata is stored under `data/browser_sessions`. It contains mode,
engine, viewport, ad-block preference, tab URLs, and active-tab index. Cookies
and local storage remain in `data/sessions`. Common credential-like query
parameters and URL fragments are removed from tab URLs before persistence.

## Silent-mode boundaries

Silent mode uses Playwright launch flags and initialization scripts to reduce
basic automation signals. Optional ad blocking is a lightweight host-based
blocklist, not the full Ghostery engine. Neither feature guarantees bypass of
advanced bot protection. Closed Shadow DOM also remains outside normal DOM
automation.
