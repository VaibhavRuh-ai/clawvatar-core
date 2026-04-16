"""SQLite database — persistent storage for all Clawvatar configuration.

Stores: settings, agents (discovered from OpenClaw), avatars, sessions.
Location: ~/.clawvatar/clawvatar.db

No hardcoded values — everything comes from the UI or API.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_DIR = Path.home() / ".clawvatar"
DB_PATH = DB_DIR / "clawvatar.db"


def get_db() -> sqlite3.Connection:
    """Get database connection, create tables if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS avatars (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            format TEXT DEFAULT 'vrm',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_avatars (
            agent_id TEXT PRIMARY KEY,
            avatar_id TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (avatar_id) REFERENCES avatars(id)
        );
    """)
    conn.commit()


# --- Settings ---

def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_all_settings() -> dict[str, str]:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def is_configured() -> bool:
    """Check if the app has been configured (has LiveKit + LLM creds)."""
    s = get_all_settings()
    return bool(s.get("livekit_url") and s.get("livekit_api_key") and (s.get("google_api_key") or s.get("openai_api_key")))


# --- Avatars ---

def add_avatar(avatar_id: str, name: str, file_path: str, format: str = "vrm"):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO avatars (id, name, file_path, format) VALUES (?, ?, ?, ?)",
        (avatar_id, name, file_path, format),
    )
    conn.commit()
    conn.close()


def get_avatar(avatar_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM avatars WHERE id=?", (avatar_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_avatars() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM avatars ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_avatar(avatar_id: str):
    conn = get_db()
    conn.execute("DELETE FROM avatars WHERE id=?", (avatar_id,))
    conn.execute("DELETE FROM agent_avatars WHERE avatar_id=?", (avatar_id,))
    conn.commit()
    conn.close()


# --- Agent ↔ Avatar Assignment ---

def assign_avatar(agent_id: str, avatar_id: str):
    """Assign an avatar to an OpenClaw agent."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO agent_avatars (agent_id, avatar_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (agent_id, avatar_id),
    )
    conn.commit()
    conn.close()


def get_agent_avatar(agent_id: str) -> Optional[dict]:
    """Get the avatar assigned to an agent."""
    conn = get_db()
    row = conn.execute("""
        SELECT aa.agent_id, a.id as avatar_id, a.name, a.file_path, a.format
        FROM agent_avatars aa JOIN avatars a ON aa.avatar_id = a.id
        WHERE aa.agent_id = ?
    """, (agent_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_assignments() -> dict[str, dict]:
    """Get all agent → avatar assignments. Returns {agent_id: avatar_info}."""
    conn = get_db()
    rows = conn.execute("""
        SELECT aa.agent_id, a.id as avatar_id, a.name, a.file_path
        FROM agent_avatars aa JOIN avatars a ON aa.avatar_id = a.id
    """).fetchall()
    conn.close()
    return {r["agent_id"]: dict(r) for r in rows}


def unassign_avatar(agent_id: str):
    conn = get_db()
    conn.execute("DELETE FROM agent_avatars WHERE agent_id=?", (agent_id,))
    conn.commit()
    conn.close()


# --- SOUL.md ---

def read_soul_md(agent_id: str, openclaw_base: str = "") -> str:
    """Read SOUL.md for an OpenClaw agent from the filesystem."""
    if not openclaw_base:
        openclaw_base = get_setting("openclaw_base", os.path.expanduser("~/.openclaw"))

    soul_path = Path(openclaw_base) / f"workspace-{agent_id}" / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text()
    return ""
