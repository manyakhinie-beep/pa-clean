"""
Chat thread persistence — SQLite backend.

Tables:
  threads    (id, title, created_at, updated_at)
  messages   (id, thread_id, role, content, tool_calls, created_at)

Thread lifecycle:
  create → add messages → clear (keep thread, drop messages) → delete (drop thread + messages)
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from personal_assistant.config import settings


def _resolve_db_path() -> Path:
    """Return the chat DB path, creating parent dirs where possible.

    Preferred location: <vault_parent>/data/chat.db
    Fallback: ~/.personal_assistant/data/chat.db  (if vault parent is non-existent
    or looks like a CI / sandbox path that doesn't exist on this machine)
    """
    candidate = settings.vault_path.parent / "data" / "chat.db"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        fallback = Path.home() / ".personal_assistant" / "data" / "chat.db"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


_DB_PATH: Path = _resolve_db_path()

_local = threading.local()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS threads (
    id        TEXT PRIMARY KEY,
    title     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content    TEXT NOT NULL DEFAULT '',
    tool_calls TEXT DEFAULT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at);
"""


def _make_conn(target: str) -> sqlite3.Connection:
    c = sqlite3.connect(target, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _init_connection() -> sqlite3.Connection:
    """Return a ready-to-use connection with schema applied.

    Tries the real file path (with journal cleanup on first failure), then
    falls back to :memory: which always works, even on virtiofs mounts.
    """
    for attempt in range(2):
        try:
            conn = _make_conn(str(_DB_PATH))
            conn.execute("PRAGMA journal_mode=DELETE")
            _apply_schema(conn)
            return conn
        except sqlite3.OperationalError as exc:
            if attempt == 0:
                logger.warning(f"[chat_db] DB error ({exc}) — retrying after journal cleanup")
                for p in (_DB_PATH, Path(str(_DB_PATH) + "-journal"), Path(str(_DB_PATH) + "-wal")):
                    try:
                        p.unlink()
                    except Exception:
                        pass
            else:
                logger.warning(f"[chat_db] File DB unusable ({exc}) — using :memory: fallback")

    conn = _make_conn(":memory:")
    _apply_schema(conn)
    return conn


def _conn() -> sqlite3.Connection:
    """Thread-local SQLite connection (schema guaranteed to exist)."""
    if not hasattr(_local, "db"):
        _local.db = _init_connection()
    return _local.db


# Initialise on first import (validates connectivity)
_conn()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Thread:
    id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class Message:
    id: int
    thread_id: str
    role: str
    content: str
    tool_calls: Optional[str]
    created_at: str


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_thread(title: str = "Новый чат") -> Thread:
    now = _now()
    tid = f"t{now.replace(':', '').replace('-', '').replace(' ', '')}"
    _conn().execute(
        "INSERT INTO threads (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (tid, title, now, now),
    )
    _conn().commit()
    logger.debug(f"[chat_db] created thread {tid}")
    return Thread(id=tid, title=title, created_at=now, updated_at=now)


def get_thread(tid: str) -> Optional[Thread]:
    row = _conn().execute("SELECT * FROM threads WHERE id = ?", (tid,)).fetchone()
    if row:
        return Thread(**dict(row))
    return None


def list_threads(limit: int = 50) -> list[Thread]:
    rows = (
        _conn()
        .execute("SELECT * FROM threads ORDER BY updated_at DESC LIMIT ?", (limit,))
        .fetchall()
    )
    return [Thread(**dict(r)) for r in rows]


def update_thread_title(tid: str, title: str) -> None:
    now = _now()
    _conn().execute(
        "UPDATE threads SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, tid),
    )
    _conn().commit()


def delete_thread(tid: str) -> bool:
    """Delete thread and all its messages. Returns True if existed."""
    cur = _conn().execute("DELETE FROM threads WHERE id = ?", (tid,))
    _conn().commit()
    logger.debug(f"[chat_db] deleted thread {tid} (rows={cur.rowcount})")
    return cur.rowcount > 0


def clear_thread(tid: str) -> None:
    """Drop all messages for a thread but keep the thread row."""
    _conn().execute("DELETE FROM messages WHERE thread_id = ?", (tid,))
    _conn().commit()
    logger.debug(f"[chat_db] cleared messages for thread {tid}")


def delete_all_threads() -> int:
    """Delete every thread and all messages. Returns count of deleted threads."""
    count = _conn().execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    _conn().execute("DELETE FROM messages")
    _conn().execute("DELETE FROM threads")
    _conn().commit()
    logger.info(f"[chat_db] deleted all threads ({count})")
    return count


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def add_message(
    tid: str,
    role: str,
    content: str,
    tool_calls: Optional[str] = None,
) -> Message:
    now = _now()
    cur = _conn().execute(
        """
        INSERT INTO messages (thread_id, role, content, tool_calls, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tid, role, content, tool_calls, now),
    )
    _conn().commit()
    # Bump thread updated_at
    _conn().execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, tid))
    _conn().commit()
    return Message(
        id=cur.lastrowid or 0,
        thread_id=tid,
        role=role,
        content=content,
        tool_calls=tool_calls,
        created_at=now,
    )


def get_messages(tid: str, limit: int = 100) -> list[Message]:
    rows = (
        _conn()
        .execute(
            """
        SELECT * FROM messages
        WHERE thread_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
            (tid, limit),
        )
        .fetchall()
    )
    # Return oldest first
    msgs = [Message(**dict(r)) for r in rows]
    msgs.reverse()
    return msgs


def message_count(tid: str) -> int:
    row = (
        _conn()
        .execute("SELECT COUNT(*) AS cnt FROM messages WHERE thread_id = ?", (tid,))
        .fetchone()
    )
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# History window / summarisation helpers
# ---------------------------------------------------------------------------


def trim_messages(tid: str, keep_last: int = 20) -> None:
    """Delete all but the last *keep_last* messages."""
    _conn().execute(
        """
        DELETE FROM messages
        WHERE id IN (
            SELECT id FROM messages
            WHERE thread_id = ?
            ORDER BY created_at DESC
            OFFSET ?
        )
        """,
        (tid, keep_last),
    )
    _conn().commit()


def total_chars(tid: str) -> int:
    row = (
        _conn()
        .execute(
            "SELECT COALESCE(SUM(LENGTH(content)), 0) AS total FROM messages WHERE thread_id = ?",
            (tid,),
        )
        .fetchone()
    )
    return row["total"] if row else 0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _now() -> str:
    from personal_assistant.utils.timezone import format_to_msk_iso
    return format_to_msk_iso()
