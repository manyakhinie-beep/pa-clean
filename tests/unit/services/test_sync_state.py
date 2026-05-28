"""
Unit tests for ``services.sync_state`` — persistent per-source watermarks
that drive incremental sync (the A half of «A+B sync resilience»).

Coverage:
  * load / save round-trip with atomic-write semantics
  * malformed / missing JSON falls back to an empty skeleton
  * get_watermark applies the 15-minute overlap window
  * record_failure preserves the previous ``last_synced_at``
  * record_success overwrites the timestamp + count
  * clear wipes the right buckets
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personal_assistant.services import sync_state


# ----------------------------------------------------------------------
# load / save
# ----------------------------------------------------------------------


def test_load_returns_empty_when_file_missing(tmp_path: Path):
    p = tmp_path / "sync_state.json"
    assert not p.exists()
    state = sync_state.load(p)
    assert state["version"] == 1
    assert state["mail"] == {}
    assert state["calendar"] == {}


def test_load_returns_empty_on_malformed_json(tmp_path: Path):
    p = tmp_path / "sync_state.json"
    p.write_text("not valid {{{", encoding="utf-8")
    state = sync_state.load(p)
    assert state["mail"] == {}
    assert state["calendar"] == {}


def test_load_backfills_missing_keys(tmp_path: Path):
    p = tmp_path / "sync_state.json"
    # Legacy file with only the mail bucket
    p.write_text(json.dumps({"mail": {"Work/INBOX": {"last_synced_at": ""}}}), encoding="utf-8")
    state = sync_state.load(p)
    assert "calendar" in state
    assert state["calendar"] == {}
    assert "Work/INBOX" in state["mail"]


def test_save_and_load_roundtrip(tmp_path: Path):
    p = tmp_path / "sync_state.json"
    state = sync_state.load(p)
    when = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    sync_state.record_success(state, "mail", "iCloud/INBOX", count=7, at=when)
    sync_state.save(state, p)

    state2 = sync_state.load(p)
    assert state2["mail"]["iCloud/INBOX"]["count"] == 7
    assert state2["mail"]["iCloud/INBOX"]["ok"] is True
    # updated_at is populated by save()
    assert state2["updated_at"]


def test_save_is_atomic_no_leftover_temp(tmp_path: Path):
    """A successful save should not leave any .tmp sibling behind."""
    p = tmp_path / "sync_state.json"
    state = sync_state.load(p)
    sync_state.record_success(
        state, "mail", "X", count=1,
        at=datetime.now(tz=timezone.utc),
    )
    sync_state.save(state, p)
    leftovers = [f for f in tmp_path.iterdir() if f.name.startswith(".sync_state.")]
    assert leftovers == []


# ----------------------------------------------------------------------
# get_watermark / overlap
# ----------------------------------------------------------------------


def test_get_watermark_subtracts_overlap_seconds():
    state = sync_state._empty_state()
    base = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    sync_state.record_success(state, "mail", "INBOX", count=3, at=base)
    wm = sync_state.get_watermark(state, "mail", "INBOX")
    assert wm is not None
    # 15-minute overlap applied
    assert wm == base - timedelta(seconds=sync_state.OVERLAP_SECONDS)


def test_get_watermark_returns_none_when_missing():
    state = sync_state._empty_state()
    assert sync_state.get_watermark(state, "mail", "INBOX") is None


def test_get_watermark_returns_none_on_invalid_iso():
    state = sync_state._empty_state()
    state["mail"]["X"] = {"last_synced_at": "not-a-date"}
    assert sync_state.get_watermark(state, "mail", "X") is None


# ----------------------------------------------------------------------
# record_success / record_failure semantics
# ----------------------------------------------------------------------


def test_record_failure_preserves_previous_last_synced_at():
    """If a mailbox failed after a successful run, the next attempt should
    still benefit from the previous watermark — otherwise we'd widen the
    window back to the full days_back ceiling."""
    state = sync_state._empty_state()
    success_time = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    sync_state.record_success(state, "mail", "INBOX", count=10, at=success_time)

    sync_state.record_failure(state, "mail", "INBOX", error="timeout after 45s")

    entry = state["mail"]["INBOX"]
    assert entry["ok"] is False
    assert entry["error"] == "timeout after 45s"
    # Previous last_synced_at must survive
    assert entry["last_synced_at"] == success_time.isoformat()


def test_record_success_overrides_previous_failure():
    state = sync_state._empty_state()
    sync_state.record_failure(state, "mail", "INBOX", error="boom")
    sync_state.record_success(
        state, "mail", "INBOX", count=5,
        at=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )
    entry = state["mail"]["INBOX"]
    assert entry["ok"] is True
    assert entry["error"] == ""
    assert entry["count"] == 5


def test_record_failure_truncates_long_error_strings():
    state = sync_state._empty_state()
    huge = "x" * 5000
    sync_state.record_failure(state, "mail", "X", error=huge)
    assert len(state["mail"]["X"]["error"]) <= 500


# ----------------------------------------------------------------------
# clear
# ----------------------------------------------------------------------


def test_clear_all_sources():
    state = sync_state._empty_state()
    sync_state.record_success(state, "mail", "X", count=1)
    sync_state.record_success(state, "calendar", "Work", count=2)
    sync_state.clear(state)
    assert state["mail"] == {}
    assert state["calendar"] == {}


def test_clear_single_source():
    state = sync_state._empty_state()
    sync_state.record_success(state, "mail", "X", count=1)
    sync_state.record_success(state, "calendar", "Work", count=2)
    sync_state.clear(state, source="mail")
    assert state["mail"] == {}
    assert state["calendar"] == {"Work": pytest.approx(state["calendar"]["Work"])} or "Work" in state["calendar"]


# ----------------------------------------------------------------------
# summarize
# ----------------------------------------------------------------------


def test_summarize_counts_ok_and_failed():
    state = sync_state._empty_state()
    sync_state.record_success(state, "mail", "A", count=1)
    sync_state.record_success(state, "mail", "B", count=2)
    sync_state.record_failure(state, "mail", "C", error="boom")
    summary = sync_state.summarize(state, "mail")
    assert summary["buckets"] == 3
    assert summary["ok"] == 2
    assert summary["failed"] == ["C"]
