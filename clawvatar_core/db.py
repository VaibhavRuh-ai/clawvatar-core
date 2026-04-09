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

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_id TEXT,
            soul_md TEXT DEFAULT '',
            instructions_override TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            voice TEXT DEFAULT '',
            model TEXT DEFAULT '',
            openclaw_agent_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (avatar_id) REFERENCES avatars(id)
        );

        CREATE TABLE IF NOT EXISTS call_sessions (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            room_name TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (agent_id) REFERENCES agents(id)
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
    conn.execute("UPDATE agents SET avatar_id=NULL WHERE avatar_id=?", (avatar_id,))
    conn.commit()
    conn.close()


# --- Agents ---

def save_agent(agent_id: str, name: str = "", avatar_id: str = "", soul_md: str = "",
               instructions_override: str = "", provider: str = "", voice: str = "",
               model: str = "", openclaw_agent_id: str = ""):
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO agents
        (id, name, avatar_id, soul_md, instructions_override, provider, voice, model, openclaw_agent_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (agent_id, name or agent_id, avatar_id, soul_md, instructions_override,
          provider, voice, model, openclaw_agent_id))
    conn.commit()
    conn.close()


def get_agent(agent_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_agents() -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, av.name as avatar_name, av.file_path as avatar_path
        FROM agents a LEFT JOIN avatars av ON a.avatar_id = av.id
        ORDER BY a.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def assign_avatar(agent_id: str, avatar_id: str):
    conn = get_db()
    conn.execute("UPDATE agents SET avatar_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (avatar_id, agent_id))
    conn.commit()
    conn.close()


def delete_agent(agent_id: str):
    conn = get_db()
    conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    conn.commit()
    conn.close()


# --- SOUL.md ---

def read_soul_md(openclaw_agent_id: str, openclaw_base: str = "") -> str:
    """Read SOUL.md for an OpenClaw agent.

    Looks in: <openclaw_base>/workspace-<agent_id>/SOUL.md
    Falls back to ~/.openclaw/workspace-<agent_id>/SOUL.md
    """
    if not openclaw_base:
        openclaw_base = os.path.expanduser("~/.openclaw")

    soul_path = Path(openclaw_base) / f"workspace-{openclaw_agent_id}" / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text()

    # Try without workspace- prefix
    soul_path2 = Path(openclaw_base) / openclaw_agent_id / "SOUL.md"
    if soul_path2.exists():
        return soul_path2.read_text()

    return ""


def sync_openclaw_agents(agent_list: list[dict], openclaw_base: str = ""):
    """Sync discovered OpenClaw agents into local DB with their SOUL.md."""
    for oc_agent in agent_list:
        agent_id = oc_agent.get("id", "")
        if not agent_id:
            continue

        existing = get_agent(agent_id)
        soul = read_soul_md(agent_id, openclaw_base)

        save_agent(
            agent_id=agent_id,
            name=existing.get("name", agent_id) if existing else agent_id,
            avatar_id=existing.get("avatar_id", "") if existing else "",
            soul_md=soul,
            instructions_override=existing.get("instructions_override", "") if existing else "",
            provider=existing.get("provider", "") if existing else "",
            voice=existing.get("voice", "") if existing else "",
            model=existing.get("model", "") if existing else "",
            openclaw_agent_id=agent_id,
        )

    logger.info(f"Synced {len(agent_list)} OpenClaw agents to DB")
