"""SQLite persistence for accounts, chat history, favorites and preferences.

The database is a single file (``instance/spherex.db`` by default) accessed with
the standard library.  Each operation opens a short-lived connection, which is
safe under Flask's threaded development server and keeps the deployment free of
extra services.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

DB_PATH = os.path.abspath(
    os.environ.get("SPHEREX_DB", os.path.join(config.INSTANCE_DIR, "spherex.db"))
)

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    dataset_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tool_name       TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, dataset_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id, updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


@contextmanager
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def create_user(username: str, email: str, password_hash: str,
                display_name: str | None = None) -> dict:
    with _lock, connect() as conn:
        cursor = conn.execute(
            """INSERT INTO users (username, email, password_hash, display_name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (username, email, password_hash, display_name, _now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _dict(row) or {}


def get_user(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _dict(row)


def get_user_by_login(login: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (login, login),
        ).fetchone()
        return _dict(row)


def touch_login(user_id: int) -> None:
    with _lock, connect() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))


def count_users() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) if row else 0


def user_stats(user_id: int) -> dict:
    with connect() as conn:
        conversations = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        messages = conn.execute(
            """SELECT COUNT(*) AS n FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.user_id = ?""",
            (user_id,),
        ).fetchone()
        favorites = conn.execute(
            "SELECT COUNT(*) AS n FROM favorites WHERE user_id = ?", (user_id,)
        ).fetchone()
    return {
        "conversations": int(conversations["n"]) if conversations else 0,
        "messages": int(messages["n"]) if messages else 0,
        "favorites": int(favorites["n"]) if favorites else 0,
    }


# ---------------------------------------------------------------------------
# Conversations & messages
# ---------------------------------------------------------------------------
def create_conversation(user_id: int, title: str, dataset_id: str | None) -> dict:
    now = _now()
    with _lock, connect() as conn:
        cursor = conn.execute(
            """INSERT INTO conversations (user_id, title, dataset_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, title[:120], dataset_id, now, now),
        )
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _dict(row) or {}


def list_conversations(user_id: int, limit: int = 60) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n_messages,
                      (SELECT m.content FROM messages m WHERE m.conversation_id = c.id
                       ORDER BY m.id DESC LIMIT 1) AS last_message
               FROM conversations c
               WHERE c.user_id = ?
               ORDER BY c.updated_at DESC, c.id DESC
               LIMIT ?""",
            (user_id, max(1, min(limit, 200))),
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(conversation_id: int, user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        return _dict(row)


def rename_conversation(conversation_id: int, user_id: int, title: str) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title[:120], _now(), conversation_id, user_id),
        )


def touch_conversation(conversation_id: int) -> None:
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )


def delete_conversation(conversation_id: int, user_id: int) -> bool:
    with _lock, connect() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return cursor.rowcount > 0


def add_message(conversation_id: int, role: str, content: str,
                tool_name: str | None = None) -> dict:
    with _lock, connect() as conn:
        cursor = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, tool_name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, role, content, tool_name, _now()),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _dict(row) or {}


def list_messages(conversation_id: int, limit: int = 400) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM messages WHERE conversation_id = ?
               ORDER BY id ASC LIMIT ?""",
            (conversation_id, max(1, min(limit, 1000))),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Favorites & preferences
# ---------------------------------------------------------------------------
def list_favorites(user_id: int) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT dataset_id FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [row["dataset_id"] for row in rows]


def toggle_favorite(user_id: int, dataset_id: str) -> bool:
    """Add or remove a favorite; returns True when it is now saved."""
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND dataset_id = ?",
            (user_id, dataset_id),
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND dataset_id = ?",
                (user_id, dataset_id),
            )
            return False
        conn.execute(
            "INSERT INTO favorites (user_id, dataset_id, created_at) VALUES (?, ?, ?)",
            (user_id, dataset_id, _now()),
        )
        return True


def get_preferences(user_id: int) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT data FROM preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["data"])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def set_preferences(user_id: int, data: dict) -> dict:
    clean = {str(k): v for k, v in data.items() if isinstance(k, str)}
    with _lock, connect() as conn:
        conn.execute(
            """INSERT INTO preferences (user_id, data, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET data = excluded.data,
                                                  updated_at = excluded.updated_at""",
            (user_id, json.dumps(clean, ensure_ascii=False), _now()),
        )
    return clean
