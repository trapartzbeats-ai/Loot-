# Loot Browser Agent Torture Benchmark

`loot-browser-v3` includes a repeatable real-site benchmark for browser agents.
It uses the persistent `codex-benchmark` profile and records both the score and
the reason for every failed action.

## MCP tools (full endpoint)

- `benchmark_scenarios()` lists available scenarios and failure categories.
- `benchmark_run()` runs all scenarios.
- `benchmark_run(["lastest_tricky_recovery"])` runs selected scenarios.

These specialist controls are exposed by `server.py`, not the reduced-context
`server_core.py` used by normal agents.

## Command line

```powershell
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\agent_benchmark.py
```

Pass scenario IDs to run a subset:

```powershell
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\agent_benchmark.py lastest_tricky_recovery
```

## Current coverage

- nested frame discovery and frame text extraction;
- open Shadow DOM fill and click;
- Shadow DOM inside an iframe;
- iframe hosted inside Shadow DOM;
- stale dynamic IDs and selector recovery;
- non-breaking-space text normalization;
- duplicate button disambiguation;
- moving-element stabilization;
- transparent overlay interception and force-click recovery;
- HTTPS certificate mismatch tolerance for test environments;
- JavaScript evaluation inside a genuinely cross-origin iframe;
- network mocking plus request/response and console capture;
- login/session-expiry detection;
- concurrent MCP-client context/page/profile isolation;
- Chromium, Firefox, and WebKit navigation, including Chromium stealth hints.
- headed/silent/headless lifecycle, visible user handoff, tab restoration,
  silent dialog dismissal, and optional ad blocking;
- silent-mode signal checks on bot.sannysoft.com.

## Failure classes

`element_not_found`, `wrong_frame`, `shadow_boundary`, `stale_element`,
`visual_mismatch`, `click_intercepted`, `incorrect_scroll`, `timing`, `popup`,
`navigation`, `network_observation`, `session_expired`, `client_isolation`,
`browser_unavailable`, and `agent_action`.

Action results also report the selected locator strategy, frame path, shadow
depth, bounding box, recovery attempts, and whether a stale selector was
recovered.

## Agent response discipline

`observe_elements` and `find_element` return compact JSON text by default so
FastMCP does not duplicate large snapshots in both text and
`structuredContent`. Use `offset` and `limit` to page observations. Rich
element data remains cached for subsequent actions; request `compact=false`
only when the additional fields are needed for reasoning or diagnostics.

For frame-specific diagnostics, use `evaluate_in_frame`. The broader
`run_browser_code` escape hatch is blocked by default and is only enabled for
trusted local clients by setting `LOOT_BROWSER_ALLOW_RUN_CODE=1` before the
server starts.

## Current result

The complete suite passed **16/16 (100%)** on 2026-08-09 after the client
isolation, multi-browser, and browser-mode upgrades. Firefox and WebKit
Playwright runtimes are installed on this machine in addition to Chromium.

Agent-surface usability is checked separately by `usability_benchmark.py`.
The full endpoint currently exposes 152 direct specialist tools; the normal
endpoint exposes 14 initial BM25 tools backed by 64 core capabilities.
