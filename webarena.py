"""Small adapter for externally hosted WebArena/BrowserGym tasks."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin, urlsplit

from config import DATA_DIR


CONFIG_PATH = DATA_DIR / "webarena.json"
RESULTS_PATH = DATA_DIR / "webarena_results.jsonl"


def _valid_base_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be an absolute http(s) URL")
    return url.rstrip("/") + "/"


def configure(base_url: str, sites: Dict[str, str] | None = None) -> Dict[str, Any]:
    payload = {"base_url": _valid_base_url(base_url), "sites": sites or {}, "updated_at": round(time.time())}
    CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"configured": True, **payload, "path": str(CONFIG_PATH)}


def config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"configured": False, "recommendation": "Call webarena_configure with the external deployment URL."}
    return {"configured": True, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}


def resolve(task_id: str, path: str = "") -> Dict[str, Any]:
    current = config()
    if not current.get("configured"):
        return current
    return {"task_id": task_id, "url": urljoin(current["base_url"], path.lstrip("/")), "base_url": current["base_url"]}


def record_result(task_id: str, success: bool, details: str = "") -> Dict[str, Any]:
    item = {"timestamp": round(time.time()), "task_id": task_id, "success": bool(success), "details": details[:4000]}
    with RESULTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item) + "\n")
    return {"recorded": True, **item, "path": str(RESULTS_PATH)}
