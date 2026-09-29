"""Sampled health monitoring for Loot Browser's local dependencies."""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import time
from pathlib import Path
from typing import Any, Dict

import browser
from config import DATA_DIR


ENDPOINTS = {
    "open_websearch": ("127.0.0.1", 3210),
    "smolvlm": ("127.0.0.1", 3211),
    "florence": ("127.0.0.1", 3212),
    "ollama": ("127.0.0.1", 11434),
}
LOG_PATH = DATA_DIR / "health.jsonl"
MAX_LOG_BYTES = 2 * 1024 * 1024
_task: asyncio.Task | None = None
_last: Dict[str, Any] = {}
_interval = 60


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.75):
            return True
    except OSError:
        return False


async def collect() -> Dict[str, Any]:
    endpoints = {}
    for name, (host, port) in ENDPOINTS.items():
        started = time.perf_counter()
        reachable = await asyncio.to_thread(_port_open, host, port)
        endpoints[name] = {"reachable": reachable, "host": host, "port": port,
                           "latency_ms": round((time.perf_counter() - started) * 1000)}
    contexts = sum(1 for state in browser._client_states.values() if state.context)
    pages = sum(
        len([page for page in state.context.pages if not page.is_closed()])
        for state in browser._client_states.values() if state.context
    )
    disk = shutil.disk_usage(DATA_DIR)
    result = {
        "timestamp": round(time.time()),
        "status": "healthy" if all(item["reachable"] for item in endpoints.values()) else "degraded",
        "endpoints": endpoints,
        "browser": {
            "playwright_started": browser._playwright is not None,
            "browser_processes": len(browser._browser_pool),
            "client_states": len(browser._client_states),
            "contexts": contexts, "pages": pages,
            "shared_rooms": len(set(browser._shared_bindings.values())),
        },
        "profiles": {
            "storage_states": len(list(browser.SESSION_STATE_DIR.glob("*.json"))),
            "metadata_files": len(list(browser.BROWSER_META_DIR.glob("*.json"))),
        },
        "disk": {"free_mb": round(disk.free / 1024 / 1024), "total_mb": round(disk.total / 1024 / 1024)},
        "canonical_screenshot": Path(r"D:\Projects\Zoro\scripts\screenshot.py").exists(),
    }
    return result


async def _loop() -> None:
    global _last
    while True:
        _last = await collect()
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG_PATH.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_last) + "\n")
        await asyncio.sleep(_interval)


async def start(interval_seconds: int = 60) -> Dict[str, Any]:
    global _task, _interval, _last
    _interval = max(10, min(int(interval_seconds), 3600))
    if _task and not _task.done():
        return {"started": True, "already_running": True, "interval_seconds": _interval, "last": _last}
    _last = await collect()
    _task = asyncio.create_task(_loop(), name="loot-browser-health-monitor")
    return {"started": True, "interval_seconds": _interval, "last": _last, "log": str(LOG_PATH)}


async def stop() -> Dict[str, Any]:
    global _task
    if not _task or _task.done():
        return {"stopped": False, "running": False}
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    return {"stopped": True, "running": False}


async def status(refresh: bool = False) -> Dict[str, Any]:
    global _last
    if refresh or not _last:
        _last = await collect()
    return {"running": bool(_task and not _task.done()), "interval_seconds": _interval, "last": _last, "log": str(LOG_PATH)}
