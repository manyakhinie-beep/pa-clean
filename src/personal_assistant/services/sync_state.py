"""
Per-source sync watermarks — incremental sync support.

Stores ``data/sync_state.json``:

    {
      "version": 1,
      "updated_at": "2026-05-28T12:34:56+03:00",
      "mail": {
        "iCloud/INBOX": {
          "last_synced_at": "2026-05-28T12:00:00+00:00",
          "count": 12,
          "ok": true,
          "error": ""
        },
        ...
      },
      "calendar": {
        "Work": { ... }
      }
    }

Consumers (``mlx_server/server.py`` orchestrator):

    state = sync_state.load()
    since = sync_state.get_watermark(state, "mail", "iCloud/INBOX")
    # … call reader with since=since …
    sync_state.record_success(state, "mail", "iCloud/INBOX",
                              count=len(msgs), at=started_at)
    sync_state.save(state)

Design notes:
  * Atomic write via ``tempfile + os.replace`` so a crash mid-write does
    not leave a half-flushed JSON that crashes the next start.
  * Watermarks are timezone-aware UTC datetimes — readers add a 15-minute
    overlap to absorb client/server clock skew.
  * Missing / malformed file → empty state (caller falls back to
    ``days_back`` configured window, which is the same behaviour as
    before this module existed — no regression risk).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# 15-minute overlap window applied when computing "since" from a watermark.
# Covers IMAP/Calendar clock drift and messages whose Received-date is
# slightly earlier than when Mail.app first observed them.  Smaller →
# more chance of missing late-arriving messages; larger → more redundant
# re-reads.  15 min is the empirical sweet spot.
OVERLAP_SECONDS: int = 15 * 60

_STATE_VERSION = 1


def default_state_path() -> Path:
    """Project-relative ``data/sync_state.json``."""
    # services/sync_state.py → project root
    return Path(__file__).resolve().parents[3] / "data" / "sync_state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "updated_at": "",
        "mail": {},
        "calendar": {},
    }


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------


def load(path: Optional[Path] = None) -> dict[str, Any]:
    """Load sync state; return an empty skeleton on any error."""
    p = path or default_state_path()
    if not p.exists():
        return _empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"[sync_state] could not load {p}: {exc} — using empty state")
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    # Backfill missing keys so callers can index unconditionally.
    data.setdefault("version", _STATE_VERSION)
    data.setdefault("mail", {})
    data.setdefault("calendar", {})
    if not isinstance(data.get("mail"), dict):
        data["mail"] = {}
    if not isinstance(data.get("calendar"), dict):
        data["calendar"] = {}
    return data


def save(state: dict[str, Any], path: Optional[Path] = None) -> None:
    """Atomically write *state* to ``data/sync_state.json``."""
    p = path or default_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(tz=timezone.utc).isoformat()

    # Write to sibling temp file, fsync, rename — atomic on POSIX.
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".sync_state.", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, p)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Watermarks
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_watermark(
    state: dict[str, Any], source: str, key: str
) -> Optional[datetime]:
    """Return the ``last_synced_at`` for (source, key) minus overlap window.

    Caller uses the result as ``since`` parameter to the reader.
    Returns ``None`` when no watermark exists — caller should fall back
    to ``days_back`` window.
    """
    bucket = state.get(source) or {}
    entry = bucket.get(key) or {}
    last = _parse_iso(entry.get("last_synced_at", ""))
    if last is None:
        return None
    # Subtract overlap so we re-scan a small window for safety.
    from datetime import timedelta

    return last - timedelta(seconds=OVERLAP_SECONDS)


def record_success(
    state: dict[str, Any],
    source: str,
    key: str,
    *,
    count: int,
    at: Optional[datetime] = None,
) -> None:
    """Mark (source, key) as synced successfully *at* the given timestamp."""
    when = (at or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    bucket = state.setdefault(source, {})
    bucket[key] = {
        "last_synced_at": when.isoformat(),
        "count": int(count),
        "ok": True,
        "error": "",
    }


def record_failure(
    state: dict[str, Any], source: str, key: str, *, error: str
) -> None:
    """Mark (source, key) as failed.

    Crucially does NOT clear ``last_synced_at`` — if the last successful
    sync was 2 hours ago and this one timed out, the next attempt still
    only needs to cover the last 2 hours (plus overlap), not the full
    ``days_back`` window again.
    """
    bucket = state.setdefault(source, {})
    prev = bucket.get(key) or {}
    bucket[key] = {
        "last_synced_at": prev.get("last_synced_at", ""),
        "count": prev.get("count", 0),
        "ok": False,
        "error": (error or "")[:500],  # cap to keep file small
    }


def clear(state: dict[str, Any], source: Optional[str] = None) -> None:
    """Drop all watermarks (or just one source's) — forces a full re-sync."""
    if source is None:
        state["mail"] = {}
        state["calendar"] = {}
    elif source in state:
        state[source] = {}


def summarize(state: dict[str, Any], source: str) -> dict[str, Any]:
    """Compact summary used by ``/sync/status`` to expose per-bucket health."""
    bucket = state.get(source) or {}
    failed = [k for k, v in bucket.items() if isinstance(v, dict) and v.get("ok") is False]
    return {
        "buckets": len(bucket),
        "failed": failed,
        "ok": len(bucket) - len(failed),
    }
