"""
SQLite backend for PersonalVault.

Tables:
  threads    (id, root_subject, participants_json, created_at)
  items      (id, item_type, thread_id, parent_message_id, subject,
              sender, sender_email, recipients_json, full_body,
              body_html, body_plain, date_iso, metadata_json, created_at)
  attachments (id, item_id, filename, mime_type, size_bytes, content_id)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

from personal_assistant.config import settings
from personal_assistant.personal_vault.models import Attachment, Thread, VaultItem
from personal_assistant.utils.timezone import format_to_msk_iso


def _resolve_db_path() -> Path:
    """Return the personal-vault DB path, creating parent dirs where possible.

    Falls back to ~/.personal_assistant/data/personal_vault.db if the
    configured vault parent directory doesn't exist on this machine
    (e.g. sandbox / CI path stored in .env).
    """
    candidate = settings.vault_path.parent / "data" / "personal_vault.db"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        fallback = Path.home() / ".personal_assistant" / "data" / "personal_vault.db"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


_DB_PATH: Path = _resolve_db_path()

_local = threading.local()


_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS threads (
    id            TEXT PRIMARY KEY,
    root_subject  TEXT NOT NULL,
    participants  TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id                TEXT PRIMARY KEY,
    item_type         TEXT NOT NULL CHECK(item_type IN ('email','meeting')),
    thread_id         TEXT REFERENCES threads(id) ON DELETE CASCADE,
    parent_message_id TEXT,
    subject           TEXT NOT NULL,
    sender            TEXT NOT NULL,
    sender_email      TEXT,
    recipients        TEXT NOT NULL DEFAULT '[]',
    full_body         TEXT NOT NULL DEFAULT '',
    body_html         TEXT,
    body_plain        TEXT,
    date_iso          TEXT NOT NULL,
    metadata          TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    mime_type  TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    content_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_thread ON items(thread_id, date_iso);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_message_id);
"""

import logging as _logging  # noqa: E402

_log = _logging.getLogger(__name__)


def _make_conn(target: str) -> sqlite3.Connection:
    c = sqlite3.connect(target, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Run schema SQL on *conn*; raises sqlite3.OperationalError on failure."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _init_connection() -> sqlite3.Connection:
    """Return a ready-to-use connection with schema applied.

    Strategy:
      1. Try the real file path.
      2. Try deleting stale journal/wal and retry the file path.
      3. Fall back to :memory: (happens on virtiofs mounts in dev/test).
    """
    targets = [str(_DB_PATH)]

    # Attempt 1 + 2: real file (with optional journal cleanup before retry)
    for attempt in range(2):
        try:
            conn = _make_conn(targets[0])
            conn.execute("PRAGMA journal_mode=DELETE")
            _apply_schema(conn)
            return conn
        except sqlite3.OperationalError as exc:
            if attempt == 0:
                _log.warning("[personal_vault] DB error (%s) — retrying after journal cleanup", exc)
                for p in (_DB_PATH, Path(str(_DB_PATH) + "-journal"), Path(str(_DB_PATH) + "-wal")):
                    try:
                        p.unlink()
                    except Exception:
                        pass
            else:
                _log.warning("[personal_vault] File DB unusable (%s) — using :memory: fallback", exc)

    # Attempt 3: in-memory (always works)
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
# CRUD helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return format_to_msk_iso()


def _generate_id() -> str:
    return f"pv_{uuid.uuid4().hex[:16]}"


def _attachments_for_item(item_id: str) -> list[Attachment]:
    rows = (
        _conn()
        .execute(
            "SELECT * FROM attachments WHERE item_id = ? ORDER BY id",
            (item_id,),
        )
        .fetchall()
    )
    return [
        Attachment(
            filename=r["filename"],
            mime_type=r["mime_type"],
            size_bytes=r["size_bytes"],
            content_id=r["content_id"],
        )
        for r in rows
    ]


def _row_to_item(row: sqlite3.Row) -> VaultItem:
    return VaultItem(
        id=row["id"],
        item_type=row["item_type"],
        thread_id=row["thread_id"],
        parent_message_id=row["parent_message_id"],
        subject=row["subject"],
        sender=row["sender"],
        sender_email=row["sender_email"],
        recipients=json.loads(row["recipients"]),
        full_body=row["full_body"],
        body_html=row["body_html"],
        body_plain=row["body_plain"],
        date_iso=row["date_iso"],
        attachments=_attachments_for_item(row["id"]),
        metadata=json.loads(row["metadata"]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_thread(
    root_subject: str,
    participants: list[str],
    tid: Optional[str] = None,
) -> str:
    """Create a new thread row and return its ID.

    Args:
        root_subject: Subject/title of the thread.
        participants: List of participant identifiers.
        tid: Optional explicit thread ID. If omitted, a unique ID is generated.
    """
    tid = tid or _generate_id()
    _conn().execute(
        "INSERT INTO threads (id, root_subject, participants, created_at) VALUES (?, ?, ?, ?)",
        (tid, root_subject, json.dumps(participants), _now()),
    )
    _conn().commit()
    return tid


def ensure_thread(tid: str, root_subject: str, participants: list[str]) -> str:
    """Create a thread row only if one with *tid* does not yet exist.

    Returns the thread ID (same as *tid*).
    """
    if get_thread(tid) is None:
        create_thread(root_subject=root_subject, participants=participants, tid=tid)
    return tid


def update_thread_participants(tid: str, new_participants: list[str]) -> None:
    """Merge *new_participants* into the thread's existing participant list.

    Deduplicates and sorts the result. No-op if the thread doesn't exist.
    This ensures every sender/recipient who contributes a message is reflected
    in the thread metadata visible to the UI and AI context.
    """
    row = _conn().execute(
        "SELECT participants FROM threads WHERE id = ?", (tid,)
    ).fetchone()
    if row is None:
        return
    existing: set[str] = set(json.loads(row["participants"]))
    merged = sorted(existing | {p for p in new_participants if p})
    _conn().execute(
        "UPDATE threads SET participants = ? WHERE id = ?",
        (json.dumps(merged), tid),
    )
    _conn().commit()


def insert_item(item: VaultItem) -> None:
    """Persist a VaultItem and its attachments."""
    _conn().execute(
        """
        INSERT INTO items (
            id, item_type, thread_id, parent_message_id, subject,
            sender, sender_email, recipients, full_body, body_html,
            body_plain, date_iso, metadata, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item.id,
            item.item_type,
            item.thread_id,
            item.parent_message_id,
            item.subject,
            item.sender,
            item.sender_email,
            json.dumps(item.recipients),
            item.full_body,
            item.body_html,
            item.body_plain,
            item.date_iso,
            json.dumps(item.metadata),
            _now(),
        ),
    )
    for att in item.attachments:
        _conn().execute(
            """
            INSERT INTO attachments (item_id, filename, mime_type, size_bytes, content_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item.id, att.filename, att.mime_type, att.size_bytes, att.content_id),
        )
    _conn().commit()


def get_item(item_id: str) -> Optional[VaultItem]:
    row = _conn().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def get_thread(tid: str) -> Optional[Thread]:
    trow = _conn().execute("SELECT * FROM threads WHERE id = ?", (tid,)).fetchone()
    if trow is None:
        return None
    rows = (
        _conn()
        .execute(
            "SELECT * FROM items WHERE thread_id = ? ORDER BY date_iso ASC",
            (tid,),
        )
        .fetchall()
    )
    items = [_row_to_item(r) for r in rows]
    participants = json.loads(trow["participants"])
    return Thread(
        id=tid,
        root_subject=trow["root_subject"],
        items=items,
        participants=participants,
    )


def list_threads(limit: int = 50) -> list[Thread]:
    trows = (
        _conn()
        .execute(
            "SELECT * FROM threads ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        .fetchall()
    )
    threads = [get_thread(r["id"]) for r in trows]
    return [t for t in threads if t is not None]


def list_items(
    item_type: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 500,
) -> list[VaultItem]:
    conditions: list[str] = []
    params: list[str] = []
    if item_type:
        conditions.append("item_type = ?")
        params.append(item_type)
    if thread_id:
        conditions.append("thread_id = ?")
        params.append(thread_id)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM items {where} ORDER BY date_iso DESC LIMIT ?"
    params.append(str(limit))
    rows = _conn().execute(sql, params).fetchall()
    return [_row_to_item(r) for r in rows]


def delete_thread(tid: str) -> bool:
    cur = _conn().execute("DELETE FROM threads WHERE id = ?", (tid,))
    _conn().commit()
    return cur.rowcount > 0


def thread_item_count(tid: str) -> int:
    row = (
        _conn()
        .execute("SELECT COUNT(*) AS cnt FROM items WHERE thread_id = ?", (tid,))
        .fetchone()
    )
    return row["cnt"] if row else 0


def get_item_by_index(tid: str, index: int) -> Optional[VaultItem]:
    """Fetch the N-th item (0-based) inside a thread ordered by date."""
    row = (
        _conn()
        .execute(
            "SELECT * FROM items WHERE thread_id = ? ORDER BY date_iso ASC LIMIT 1 OFFSET ?",
            (tid, index),
        )
        .fetchone()
    )
    if row is None:
        return None
    return _row_to_item(row)
