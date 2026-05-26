"""
Tag history service — stores old/new tag changes per item_id for AI fine-tuning.

Changes are stored in ``data/tag_history.json`` as an append-only list.
Each :class:`TagChange` captures: who changed it, from what, to what, when.

The ``list_changes()`` helper supports filtering by ``item_id``, ``section``,
and date range for use in fine-tuning pipelines.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TagChange(BaseModel):
    """A single tag-change event for an item.

    :param id: Unique change ID (UUID4 short).
    :param item_id: The vault doc path or project/rule ID that was tagged.
    :param section: Vault section or resource type (``mail``, ``calendar``,
        ``project``, ``rule``, …).
    :param old_value: Previous tag value (empty string = tag was added).
    :param new_value: New tag value (empty string = tag was removed).
    :param changed_by: Who or what made the change (``user``, ``rule_engine``,
        ``classifier``, …).
    :param changed_at: ISO-8601 UTC timestamp.
    :param note: Optional free-text note (e.g. rule name that triggered change).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    item_id: str
    section: str = ""
    old_value: str = ""
    new_value: str = ""
    changed_by: str = "user"
    changed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    note: str = ""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_HISTORY_FILE = _PROJECT_ROOT / "data" / "tag_history.json"


def _history_file() -> Path:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _HISTORY_FILE


def _load_raw() -> list[dict]:
    f = _history_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_raw(records: list[dict]) -> None:
    f = _history_file()
    tmp_fd, tmp_path = tempfile.mkstemp(dir=f.parent, suffix=".json.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, f)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_change(
    item_id: str,
    old_value: str,
    new_value: str,
    section: str = "",
    changed_by: str = "user",
    note: str = "",
) -> TagChange:
    """Append a tag-change event to the history log.

    :param item_id: Vault path or resource ID of the changed item.
    :param old_value: Previous tag value.
    :param new_value: New tag value.
    :param section: Vault section or resource type.
    :param changed_by: Source of the change.
    :param note: Optional context note.
    :returns: The persisted :class:`TagChange`.
    """
    change = TagChange(
        item_id=item_id,
        section=section,
        old_value=old_value,
        new_value=new_value,
        changed_by=changed_by,
        note=note,
    )
    records = _load_raw()
    records.append(change.model_dump())
    _save_raw(records)
    return change


def list_changes(
    item_id: Optional[str] = None,
    section: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 200,
) -> list[TagChange]:
    """Query tag-change history with optional filters.

    :param item_id: Filter by item ID (exact match).
    :param section: Filter by section.
    :param since: ISO-8601 UTC lower bound for ``changed_at``.
    :param until: ISO-8601 UTC upper bound for ``changed_at``.
    :param limit: Maximum number of records to return (most recent first).
    :returns: List of :class:`TagChange` objects.
    """
    records = _load_raw()

    if item_id:
        records = [r for r in records if r.get("item_id") == item_id]
    if section:
        records = [r for r in records if r.get("section") == section]
    if since:
        records = [r for r in records if (r.get("changed_at") or "") >= since]
    if until:
        records = [r for r in records if (r.get("changed_at") or "") <= until]

    # Most recent first
    records = sorted(records, key=lambda r: r.get("changed_at", ""), reverse=True)
    records = records[:limit]

    return [TagChange.model_validate(r) for r in records]


def delete_change(change_id: str) -> bool:
    """Delete a single tag-change record by ID.

    :param change_id: ID of the record to delete.
    :returns: ``True`` if a record was deleted, ``False`` if not found.
    """
    records = _load_raw()
    filtered = [r for r in records if r.get("id") != change_id]
    if len(filtered) == len(records):
        return False
    _save_raw(filtered)
    return True


def clear_history(item_id: Optional[str] = None) -> int:
    """Delete all history records (or only those for a specific item).

    :param item_id: If given, only records for this item are deleted.
    :returns: Number of records deleted.
    """
    records = _load_raw()
    if item_id:
        kept = [r for r in records if r.get("item_id") != item_id]
    else:
        kept = []
    deleted = len(records) - len(kept)
    _save_raw(kept)
    return deleted
