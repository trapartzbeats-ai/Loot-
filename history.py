from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import HISTORY_DB


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(HISTORY_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = _db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            content_hash TEXT,
            text_content TEXT,
            markdown_content TEXT,
            structured_data TEXT,
            screenshot_path TEXT,
            vision_analysis TEXT,
            session_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_visits_url ON visits(url);
        CREATE INDEX IF NOT EXISTS idx_visits_session ON visits(session_id);
        CREATE INDEX IF NOT EXISTS idx_visits_timestamp ON visits(timestamp);

        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            engine TEXT DEFAULT 'duckduckgo',
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            results_count INTEGER,
            urls TEXT,
            session_id TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS visits_fts USING fts5(
            text_content, url, title,
            content='visits', content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS visits_ai AFTER INSERT ON visits BEGIN
            INSERT INTO visits_fts(rowid, text_content, url, title)
            VALUES (new.id, new.text_content, new.url, new.title);
        END;
        CREATE TRIGGER IF NOT EXISTS visits_ad AFTER DELETE ON visits BEGIN
            INSERT INTO visits_fts(visits_fts, rowid, text_content, url, title)
            VALUES ('delete', old.id, old.text_content, old.url, old.title);
        END;
        CREATE TRIGGER IF NOT EXISTS visits_au AFTER UPDATE ON visits BEGIN
            INSERT INTO visits_fts(visits_fts, rowid, text_content, url, title)
            VALUES ('delete', old.id, old.text_content, old.url, old.title);
            INSERT INTO visits_fts(rowid, text_content, url, title)
            VALUES (new.id, new.text_content, new.url, new.title);
        END;
    """)
    conn.commit()
    conn.close()


def add_visit(url: str, title: str = "", text_content: str = "",
              markdown_content: str = "", structured_data: str = "",
              screenshot_path: str = "", vision_analysis: str = "",
              session_id: str = "") -> int:
    content_hash = hashlib.md5(text_content.encode()).hexdigest() if text_content else ""
    conn = _db()
    cur = conn.execute(
        """INSERT INTO visits (url, title, content_hash, text_content, markdown_content,
           structured_data, screenshot_path, vision_analysis, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (url, title, content_hash, text_content, markdown_content,
         structured_data, screenshot_path, vision_analysis, session_id),
    )
    conn.commit()
    vid = cur.lastrowid
    conn.close()
    return vid


def search_history(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    conn = _db()
    rows = conn.execute(
        """SELECT v.*, snippet(visits_fts, 0, '<b>', '</b>', '...', 30) as snippet
           FROM visits_fts JOIN visits v ON v.id = visits_fts.rowid
           WHERE visits_fts MATCH ?
           ORDER BY rank LIMIT ?""",
        (query, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recall_visit(url: str) -> Optional[Dict[str, Any]]:
    conn = _db()
    row = conn.execute(
        "SELECT * FROM visits WHERE url = ? ORDER BY timestamp DESC LIMIT 1", (url,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_history(limit: int = 50, session_id: str = "") -> List[Dict[str, Any]]:
    conn = _db()
    if session_id:
        rows = conn.execute(
            "SELECT id, url, title, timestamp, content_hash FROM visits WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, url, title, timestamp, content_hash FROM visits ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def compare_visits(url: str) -> Dict[str, Any]:
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM visits WHERE url = ? ORDER BY timestamp DESC LIMIT 2", (url,)
    ).fetchall()
    conn.close()
    if len(rows) < 2:
        return {"url": url, "changes": [], "message": "Need at least 2 visits to compare"}
    latest, previous = dict(rows[0]), dict(rows[1])
    changes = []
    for key in ["title", "content_hash", "text_content"]:
        if latest.get(key) != previous.get(key):
            changes.append({"field": key, "changed": True})
    return {"url": url, "changes": changes, "previous_timestamp": previous.get("timestamp"), "latest_timestamp": latest.get("timestamp")}


def get_visit_by_id(visit_id: int) -> Optional[Dict[str, Any]]:
    conn = _db()
    row = conn.execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


init_db()
