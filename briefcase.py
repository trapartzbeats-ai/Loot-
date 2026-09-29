"""briefcase.py — per-profile encrypted secrets vault for loot-browser-v3.

Every agent session (profile) gets its own Fernet-encrypted store. Agents keep
credentials here instead of hardcoding them in scripts — other agents can't read
a profile they're not switched into, and nothing is stored in plaintext on disk.

Files (under DATA_DIR/briefcase/):
  <session>.key  — Fernet key for that profile
  <session>.enc  — encrypted JSON map {name: value}

Tools in server.py:
  briefcase_add(name, value[, session_id])   store a secret
  briefcase_get(name[, session_id])          retrieve a secret
  briefcase_list([session_id])               names only (no values)
  briefcase_remove(name[, session_id])       delete a secret

session_id defaults to the ACTIVE browser session (follows switch_session).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet

from config import DATA_DIR

import browser  # reuse the active-session pointer

BRIEFCASE_DIR = DATA_DIR / "briefcase"
BRIEFCASE_DIR.mkdir(parents=True, exist_ok=True)


def _active_session() -> str:
    return browser.get_current_session()


def _key_path(session_id: str) -> Path:
    return BRIEFCASE_DIR / f"{session_id}.key"


def _store_path(session_id: str) -> Path:
    return BRIEFCASE_DIR / f"{session_id}.enc"


def _fernet(session_id: str) -> Fernet:
    kp = _key_path(session_id)
    if kp.exists():
        return Fernet(kp.read_bytes())
    key = Fernet.generate_key()
    kp.write_bytes(key)
    return Fernet(key)


def _load(session_id: str) -> Dict[str, str]:
    sp = _store_path(session_id)
    if not sp.exists():
        return {}
    try:
        return json.loads(_fernet(session_id).decrypt(sp.read_bytes()).decode())
    except Exception:
        return {}


def _save(session_id: str, data: Dict[str, str]) -> None:
    _store_path(session_id).write_bytes(
        _fernet(session_id).encrypt(json.dumps(data).encode())
    )


def briefcase_add(name: str, value: str, session_id: str = "") -> Dict[str, Any]:
    """Store an encrypted secret in the profile's briefcase."""
    sid = session_id or _active_session()
    data = _load(sid)
    data[name] = value
    _save(sid, data)
    return {"stored": True, "session": sid, "name": name}


def briefcase_get(name: str, session_id: str = "") -> Dict[str, Any]:
    """Retrieve a secret from the profile's briefcase."""
    sid = session_id or _active_session()
    data = _load(sid)
    if name not in data:
        return {"found": False, "session": sid, "name": name}
    return {"found": True, "session": sid, "name": name, "value": data[name]}


def briefcase_list(session_id: str = "") -> Dict[str, Any]:
    """List secret names (values never leave the encrypted store)."""
    sid = session_id or _active_session()
    return {"session": sid, "names": sorted(_load(sid).keys())}


def briefcase_remove(name: str, session_id: str = "") -> Dict[str, Any]:
    """Remove a secret from the profile's briefcase."""
    sid = session_id or _active_session()
    data = _load(sid)
    removed = data.pop(name, None) is not None
    _save(sid, data)
    return {"removed": removed, "session": sid, "name": name}
