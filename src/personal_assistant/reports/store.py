"""
store.py — Atomic JSON persistence for generated reports.

Storage path: <vault_parent>/data/reports.json
Each record is a ReportRecord serialised to dict.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from personal_assistant.config import settings
from personal_assistant.report_schemas import ReportRecord

_STORE_PATH: Path = settings.vault_path.parent / "data" / "reports.json"


def _ensure_dir() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_raw() -> list[dict]:
    """Load all records from disk (empty list on missing / corrupt file)."""
    _ensure_dir()
    if not _STORE_PATH.exists():
        return []
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning(f"[reports] Failed to read store: {exc}")
    return []


def _save_raw(records: list[dict]) -> None:
    """Atomically write records to disk."""
    _ensure_dir()
    raw = json.dumps(records, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=_STORE_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.replace(tmp, _STORE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_reports(limit: int = 50) -> list[ReportRecord]:
    """Return the most recent *limit* reports, newest first."""
    raw = _load_raw()
    records: list[ReportRecord] = []
    for item in reversed(raw):
        try:
            records.append(ReportRecord.model_validate(item))
        except Exception as exc:
            logger.debug(f"[reports] Skipping malformed record: {exc}")
    return records[:limit]


def get_report(report_id: str) -> Optional[ReportRecord]:
    """Return report by short id, or None if not found."""
    for item in _load_raw():
        if item.get("id") == report_id:
            try:
                return ReportRecord.model_validate(item)
            except Exception:
                return None
    return None


def save_report(record: ReportRecord) -> None:
    """Append a new report record to the store."""
    raw = _load_raw()
    raw.append(record.model_dump(mode="json"))
    _save_raw(raw)
    logger.info(f"[reports] Saved report id={record.id} type={record.type} date={record.target_date}")


def delete_report(report_id: str) -> bool:
    """Delete a report by short id. Returns True if found and deleted."""
    raw = _load_raw()
    new_raw = [r for r in raw if r.get("id") != report_id]
    if len(new_raw) == len(raw):
        return False
    _save_raw(new_raw)
    logger.info(f"[reports] Deleted report id={report_id}")
    return True
