# Loot Browser v3 Operational Hardening

The remaining operational gaps are implemented and covered by deterministic
benchmarks.

- Health: sampled checks for LOOT search, SmolVLM, Florence, Ollama, browser
  pools, contexts, profiles, disk, and the canonical screenshot dependency.
- Bot protection: structured challenge signals and bounded recovery routes;
  browser actions fail as `bot_protection` before visual guessing.
- Shared rooms: cooperative, expiring mutation leases prevent multiple agents
  from racing the same tabs while preserving shared observation.
- Closed Shadow DOM: an early context init script preserves newly requested
  closed roots for agent perception and labels recovered elements. This does
  not reach roots created before instrumentation or isolated extension worlds.
- Screenshots: Loot Vision, Hands, and Zoro call the canonical import-safe
  `D:\Projects\Zoro\scripts\screenshot.py` library without nested stdio
  subprocesses.

Verification commands:

```powershell
python -m compileall -q .
python closed_shadow_benchmark.py
python operational_gaps_benchmark.py
python shared_browser_benchmark.py
python agent_benchmark.py
```
