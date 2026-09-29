# Loot Browser post-audit phases

Implemented and verified on 2026-08-09.

## Phase 1 — Integrated visual recovery

`browser_action` keeps semantic DOM/frame/open-Shadow-DOM actions first. When
those fail and visual recovery is enabled, it captures the viewport, calls the
live Florence grounding daemon, maps normalized boxes to viewport coordinates,
checks the hit element, and clicks or fills at the grounded center. The result
reports model, box, point, hit element, strategy, and recovery status.

Proof: `visual_recovery_benchmark.py` clicks a canvas-only target that has no
semantic element.

## Phase 2 — Opt-in shared browser rooms

`shared_browser_join`, `shared_browser_leave`, and `shared_browser_status` map
explicitly opted-in MCP clients to one context/page state. Isolation remains
the default. Detaching does not close another participant's browser.

Proof: `shared_browser_benchmark.py` uses two MCP clients, verifies identical
context/page IDs and URL, then safely detaches one client.

## Phase 3 — MV3 lifecycle

Loaded MV3 extensions use Playwright persistent Chromium contexts with stable
profile directories. Python Playwright service-worker APIs were corrected, and
runtime IDs can be recovered from content injection.

Proof: `mv3_lifecycle_benchmark.py` verifies injection and a stable extension
ID before and after a full browser restart.

## Phase 4 — SPA readiness

Navigation no longer uses a fixed post-load sleep. Common SPA roots are sampled
for loading indicators, controls, text, DOM stability, and readiness state.
Results are `static_ready`, `hydrated`, or `hydration_timeout` with diagnostics.

Proof: `spa_readiness_benchmark.py` waits through deterministic delayed
hydration and returns only after the application is interactive.

## Phase 5 — Platform foundations

- extension catalog with staged install, explicit updates, package validation,
  and zip path-traversal protection;
- persisted high-level macro record/replay with a strict operation allowlist;
- external WebArena/BrowserGym configuration, task start, and result logging;
- cross-frame media discovery and size-bounded download through the active
  authenticated Playwright request context.

Proof: `platform_foundations_benchmark.py` validates all four surfaces against
a deterministic local fixture.

## Current surfaces

- Normal core: 14 initially visible BM25 tools, 64 underlying capabilities.
- Full specialist endpoint: 152 directly visible tools.
- Permanent hostile benchmark: 16/16.
