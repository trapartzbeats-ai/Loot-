# Loot Browser MCP v3 — Setup Guide

## What it is
A browser automation MCP server that lets an AI agent (like Hermes) browse the web, take screenshots, read pages, click elements, fill forms, and extract data.

## Requirements
- Python 3.11+
- Playwright (`pip install playwright && playwright install chromium`)
- Hermes Agent (or any MCP-compatible client)

## Setup
1. Unzip `loot-browser-v3.zip` to any folder
2. Install dependencies:
   ```
   pip install playwright mcp
   playwright install chromium
   ```
3. Register in Hermes config (`config.yaml`):
   ```yaml
   loot-browser-v3:
     command: python
     args: ["path/to/loot-browser-v3/server_core.py"]
     enabled: true
   ```
4. Run `hermes restart` or restart Hermes

## Files included
- `server.py` — main MCP server (tool registration, request handling)
- `server_core.py` — entry point
- `browser.py` — browser engine (82KB, core logic)
- `perception.py` — screenshot analysis, visual detection
- `scraper.py` — page scraping
- `sessions.py` — session management
- `config.py`, `approvals.py`, `hands.py` — supporting modules
- 6 benchmark/test files for validation
- Various .md docs for usage reference

## Verifying it works
Run: `hermes mcp test loot-browser-v3`
Expected: "✓ Connected" with tool list (browse, screenshot, click, extract, etc.)

## Notes
- Playwright installs its own Chromium browser (~400MB)
- The server runs as a stdio MCP process, not HTTP