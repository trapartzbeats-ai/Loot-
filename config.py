from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(r"D:\Projects\Hermes\loot-browser-v3")
DATA_DIR = BASE_DIR / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
HISTORY_DB = DATA_DIR / "history.db"

OLLAMA_URL = "http://localhost:11434"
SMOLVLM_URL = "http://127.0.0.1:3211"
FLORENCE_URL = "http://127.0.0.1:3212"  # fast OCR/detect/grounding daemon (Florence-2-base)
OPEN_WEBSEARCH_DAEMON = "http://127.0.0.1:3210"

VLM_MODELS = ["qwen3-vl:4b", "moondream"]
OCR_MODEL = "qwen3-vl:4b"
MAX_IMAGE_DIM = 1600
REQUEST_TIMEOUT = 30.0

# Agent browsers need to be able to inspect deliberately misconfigured test
# sites (UI Testing Playground currently presents a certificate-name mismatch).
# Keep this configurable so stricter deployments can turn it off.
IGNORE_HTTPS_ERRORS = os.getenv("LOOT_BROWSER_IGNORE_HTTPS_ERRORS", "1").lower() not in {
    "0", "false", "no", "off"
}

# Python Playwright code execution is intentionally opt-in. It is equivalent to
# local code execution and should only be enabled for a trusted MCP client.
ALLOW_BROWSER_RUN_CODE = os.getenv("LOOT_BROWSER_ALLOW_RUN_CODE", "0").lower() in {
    "1", "true", "yes", "on"
}

# Long-running agents should re-check authentication before assuming an old
# browser context is still valid. This age is advisory; URL/form detection can
# report login_required sooner.
SESSION_TTL_SECONDS = max(60, int(os.getenv("LOOT_BROWSER_SESSION_TTL_SECONDS", "28800")))

NETWORK_LOG_LIMIT = max(100, min(int(os.getenv("LOOT_BROWSER_NETWORK_LOG_LIMIT", "1000")), 10000))
CONSOLE_LOG_LIMIT = max(100, min(int(os.getenv("LOOT_BROWSER_CONSOLE_LOG_LIMIT", "1000")), 10000))

BROWSER_MODE = os.getenv("LOOT_BROWSER_MODE", "headless").lower()
if BROWSER_MODE not in {"headless", "headed", "silent"}:
    BROWSER_MODE = "headless"
VIEWPORT_WIDTH = max(320, min(int(os.getenv("LOOT_BROWSER_VIEWPORT_WIDTH", "1920")), 7680))
VIEWPORT_HEIGHT = max(240, min(int(os.getenv("LOOT_BROWSER_VIEWPORT_HEIGHT", "1080")), 4320))
ANTI_DETECT = os.getenv("LOOT_BROWSER_ANTI_DETECT", "1").lower() in {"1", "true", "yes", "on"}
AUTO_DISMISS_DIALOGS = os.getenv("LOOT_BROWSER_AUTO_DISMISS_DIALOGS", "1").lower() in {"1", "true", "yes", "on"}
AUTO_BLOCK_ADS = os.getenv("LOOT_BROWSER_AUTO_BLOCK_ADS", "0").lower() in {"1", "true", "yes", "on"}
VISUAL_RECOVERY = os.getenv("LOOT_BROWSER_VISUAL_RECOVERY", "1").lower() in {"1", "true", "yes", "on"}
# Closed roots cannot be inspected after creation. When enabled, an init script
# preserves roots requested as closed as inspectable roots before site code runs.
# This is intentionally configurable because it changes host.shadowRoot semantics.
CAPTURE_CLOSED_SHADOW = os.getenv("LOOT_BROWSER_CAPTURE_CLOSED_SHADOW", "1").lower() in {
    "1", "true", "yes", "on"
}
TOOLSET = os.getenv("LOOT_BROWSER_TOOLSET", "full").lower()
if TOOLSET not in {"full", "core"}:
    TOOLSET = "full"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

for d in [DATA_DIR, SCREENSHOT_DIR]:
    d.mkdir(parents=True, exist_ok=True)
