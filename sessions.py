from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_DIR

SESSIONS_DIR = DATA_DIR / "sessions"
VAULT_DIR = DATA_DIR / "vault"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
VAULT_DIR.mkdir(parents=True, exist_ok=True)

# Retention settings
SESSION_RETENTION_DAYS = 7
VAULT_RETENTION_DAYS = 90


class SessionRecorder:
    """Records browser sessions with screenshots + DOM snapshots."""

    def __init__(self):
        self._active: Dict[str, Dict] = {}

    def start(self, name: str = "") -> str:
        """Start recording a session."""
        session_id = str(uuid.uuid8())[:12] if hasattr(uuid, "uuid8") else str(uuid.uuid4())[:12]
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "screenshots").mkdir(exist_ok=True)
        (session_dir / "snapshots").mkdir(exist_ok=True)

        self._active[session_id] = {
            "id": session_id,
            "name": name or session_id,
            "started_at": time.time(),
            "steps": [],
            "dir": str(session_dir),
        }
        return session_id

    def record_step(self, session_id: str, tool: str, args: Dict, result: Any,
                    screenshot: str = "", snapshot: str = "") -> None:
        """Record a single step."""
        if session_id not in self._active:
            return

        step = {
            "step": len(self._active[session_id]["steps"]),
            "tool": tool,
            "args": {k: v for k, v in args.items() if k != "session_id"},
            "result_summary": str(result)[:500] if result else "",
            "timestamp": time.time(),
        }

        # Save screenshot if provided
        if screenshot:
            screenshot_path = Path(self._active[session_id]["dir"]) / "screenshots" / f"step_{step['step']:03d}.png"
            try:
                import base64
                screenshot_path.write_bytes(base64.b64decode(screenshot))
                step["screenshot"] = str(screenshot_path)
            except Exception:
                pass

        # Save DOM snapshot if provided
        if snapshot:
            snapshot_path = Path(self._active[session_id]["dir"]) / "snapshots" / f"step_{step['step']:03d}.html"
            snapshot_path.write_text(snapshot[:50000], encoding="utf-8")
            step["snapshot"] = str(snapshot_path)

        self._active[session_id]["steps"].append(step)

    def stop(self, session_id: str) -> Optional[Dict]:
        """Stop recording and save metadata."""
        session = self._active.pop(session_id, None)
        if not session:
            return None

        session["stopped_at"] = time.time()
        session["duration_s"] = session["stopped_at"] - session["started_at"]
        session["step_count"] = len(session["steps"])

        # Save metadata
        metadata_path = Path(session["dir"]) / "metadata.json"
        metadata_path.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")

        # Save action log
        actions_path = Path(session["dir"]) / "actions.jsonl"
        with open(actions_path, "w", encoding="utf-8") as f:
            for step in session["steps"]:
                f.write(json.dumps(step, default=str) + "\n")

        return session

    def get(self, session_id: str) -> Optional[Dict]:
        """Get active session info."""
        return self._active.get(session_id)

    def is_active(self, session_id: str) -> bool:
        """Check if a session is currently recording."""
        return session_id in self._active


class SessionPlayer:
    """Replays recorded sessions."""

    @staticmethod
    def get_metadata(session_id: str) -> Optional[Dict]:
        """Get session metadata."""
        metadata_path = SESSIONS_DIR / session_id / "metadata.json"
        if not metadata_path.exists():
            return None
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    @staticmethod
    def get_actions(session_id: str) -> List[Dict]:
        """Get action-level log."""
        actions_path = SESSIONS_DIR / session_id / "actions.jsonl"
        if not actions_path.exists():
            return []
        actions = []
        with open(actions_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    actions.append(json.loads(line))
        return actions

    @staticmethod
    def get_screenshots(session_id: str) -> List[str]:
        """Get list of screenshot paths."""
        screenshots_dir = SESSIONS_DIR / session_id / "screenshots"
        if not screenshots_dir.exists():
            return []
        return sorted([str(p) for p in screenshots_dir.glob("*.png")])

    @staticmethod
    def list_sessions() -> List[Dict]:
        """List all recorded sessions."""
        sessions = []
        for d in sorted(SESSIONS_DIR.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            metadata_path = d / "metadata.json"
            if metadata_path.exists():
                try:
                    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                    sessions.append({
                        "id": meta.get("id", d.name),
                        "name": meta.get("name", d.name),
                        "started_at": meta.get("started_at"),
                        "duration_s": meta.get("duration_s"),
                        "step_count": meta.get("step_count"),
                    })
                except Exception:
                    continue
        return sessions


class RetentionPolicy:
    """Automatic cleanup and vault management."""

    @staticmethod
    def weekly_cleanup() -> Dict[str, Any]:
        """Run weekly cleanup: move important sessions to vault, purge old."""
        now = time.time()
        moved = 0
        purged = 0

        # Move sessions older than SESSION_RETENTION_DAYS to vault if important
        for d in SESSIONS_DIR.iterdir():
            if not d.is_dir():
                continue
            metadata_path = d / "metadata.json"
            if not metadata_path.exists():
                continue
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                age_days = (now - meta.get("started_at", now)) / 86400

                if age_days > SESSION_RETENTION_DAYS:
                    # Check if session is "important" (has many steps or specific tools)
                    is_important = meta.get("step_count", 0) > 10
                    if is_important:
                        # Move to vault
                        vault_path = VAULT_DIR / d.name
                        if vault_path.exists():
                            import shutil
                            shutil.rmtree(vault_path)
                        d.rename(vault_path)
                        moved += 1
                    else:
                        # Purge
                        import shutil
                        shutil.rmtree(d)
                        purged += 1
            except Exception:
                continue

        # Purge vault items older than VAULT_RETENTION_DAYS
        vault_purged = 0
        for d in VAULT_DIR.iterdir():
            if not d.is_dir():
                continue
            metadata_path = d / "metadata.json"
            if not metadata_path.exists():
                continue
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                age_days = (now - meta.get("started_at", now)) / 86400
                if age_days > VAULT_RETENTION_DAYS:
                    import shutil
                    shutil.rmtree(d)
                    vault_purged += 1
            except Exception:
                continue

        return {
            "moved_to_vault": moved,
            "purged_sessions": purged,
            "purged_vault": vault_purged,
        }

    @staticmethod
    def vault_list() -> List[Dict]:
        """List vaulted sessions."""
        items = []
        for d in sorted(VAULT_DIR.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            metadata_path = d / "metadata.json"
            if metadata_path.exists():
                try:
                    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                    items.append({
                        "id": meta.get("id", d.name),
                        "name": meta.get("name", d.name),
                        "started_at": meta.get("started_at"),
                        "step_count": meta.get("step_count"),
                    })
                except Exception:
                    continue
        return items


_recorder = SessionRecorder()
_player = SessionPlayer()
_retention = RetentionPolicy()


def get_recorder() -> SessionRecorder:
    return _recorder


def get_player() -> SessionPlayer:
    return _player


def get_retention() -> RetentionPolicy:
    return _retention
