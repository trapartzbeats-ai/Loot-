# Loot Browser Agent Usability

Normal browser agents should use `server_core.py`. Specialist development,
desktop-control, scraping, vision, extension, and Zext workflows can use the
complete `server.py` endpoint.

## Preferred workflow

1. `browser_start(profile, url, mode)`
2. `browser_action(target, action, text?)`
3. `browser_state(include_elements?)`
4. `browser_debug()` when something looks wrong
5. `save_session()` after login or important storage changes

Use the lower-level perception tools only when the target is ambiguous or the
high-level action returns diagnostics that need investigation.

## Surfaces

| Surface | Tools | Approximate schema size | Use |
|---|---:|---:|---|
| Core | 14 initially visible / 64 underlying | measured by `usability_benchmark.py` | Normal browser agents |
| Full | 152 | direct specialist catalog | Specialist and development tasks |

The core endpoint uses FastMCP v3 BM25 progressive disclosure to reduce initial
tool-schema context without
removing frame/Shadow DOM perception, profiles, modes, network debugging,
briefcases, or user handoff. Benchmark controls remain on the full endpoint.
The expanded underlying catalog also includes opt-in shared rooms, visual
recovery, SPA readiness, extension-store operations, macros, a WebArena
adapter, and authenticated media downloads.

The always-visible workflow is `browser_start`, `browser_action`,
`browser_state`, `browser_wait`, `browser_debug`, `browser_capabilities`,
`navigate`, `save_session`, `set_browser_mode`, `signal_user`, `wait_for_user`,
and `browser_close`, plus generated `search_tools` and `call_tool`. Discover
specialized tools by capability and invoke the exact returned name.

## High-level tools

- `browser_start`: combines profile selection, engine/mode setup, tab restore,
  navigation, and session-health reporting.
- `browser_action`: freshly observes and semantically finds a named target,
  then clicks or fills it with structured recovery.
- `browser_state`: combines profile, mode, URL, title, tabs, session status,
  counters, and optionally compact elements.
- `browser_debug`: returns only authentication trouble, failed HTTP requests,
  and important console events.
- `browser_wait`: waits server-side for text, a visible selector, or a numeric
  element value. It can trigger a semantic click at the matching threshold,
  avoiding hundreds of agent polling calls.
- `browser_capabilities`: gives agents compact workflows and selection rules.

## Regression check

```powershell
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\usability_benchmark.py
```

Acceptance limits: at most 14 initially visible tools, at most 10K schema
characters, four calls for the standard navigate-and-click workflow, a
successful semantic click, and successful BM25 discovery plus hidden-tool call.

Run the high-friction mission separately:

```powershell
C:\Python314\python.exe D:\Projects\Hermes\loot-browser-v3\hard_mission_benchmark.py
```

It covers delayed AJAX text, off-screen clicking, an overlapped input inside a
nested scroll container, precision progress monitoring, silent dialog
dismissal, and BM25 discovery of a hidden frame-evaluation tool. The optimized
mission uses 16 MCP calls instead of the original 184.

Run the phase acceptance tests:

```powershell
C:\Python314\python.exe visual_recovery_benchmark.py
C:\Python314\python.exe shared_browser_benchmark.py
C:\Python314\python.exe mv3_lifecycle_benchmark.py
C:\Python314\python.exe spa_readiness_benchmark.py
C:\Python314\python.exe platform_foundations_benchmark.py
```
