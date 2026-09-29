"""High-level browser macro recording and replay persistence."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from config import DATA_DIR


MACRO_DIR = DATA_DIR / "macros"
MACRO_DIR.mkdir(parents=True, exist_ok=True)
_recordings: Dict[str, Dict[str, Any]] = {}
_replaying: set[str] = set()


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name):
        raise ValueError("macro name may contain only letters, numbers, dot, underscore, and hyphen")
    return name


def start(scope: str, name: str) -> Dict[str, Any]:
    name = _safe_name(name)
    _recordings[scope] = {"name": name, "started_at": round(time.time()), "steps": []}
    return {"recording": True, "name": name, "scope": scope}


def record(scope: str, tool: str, arguments: Dict[str, Any]) -> None:
    if scope in _replaying or scope not in _recordings:
        return
    safe_arguments = {key: value for key, value in arguments.items() if key not in {"session_id"}}
    _recordings[scope]["steps"].append({"tool": tool, "arguments": safe_arguments})


def stop(scope: str, save: bool = True) -> Dict[str, Any]:
    recording = _recordings.pop(scope, None)
    if not recording:
        return {"recording": False, "error": "no macro recording is active"}
    path = MACRO_DIR / f"{recording['name']}.json"
    payload = {"version": 1, **recording, "stopped_at": round(time.time())}
    if save:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(path)
    return {"recording": False, "saved": save, "name": recording["name"], "steps": len(recording["steps"]), "path": str(path) if save else ""}


def list_macros() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for path in sorted(MACRO_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items.append({"name": payload.get("name", path.stem), "steps": len(payload.get("steps", [])), "path": str(path)})
        except Exception:
            continue
    return {"count": len(items), "macros": items}


def load(name: str) -> Dict[str, Any]:
    path = MACRO_DIR / f"{_safe_name(name)}.json"
    if not path.exists():
        raise FileNotFoundError(f"macro not found: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def begin_replay(scope: str) -> None:
    _replaying.add(scope)


def end_replay(scope: str) -> None:
    _replaying.discard(scope)
