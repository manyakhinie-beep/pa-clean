"""Unit tests for vault_filter_service (DB layer stubbed via list_items)."""

from __future__ import annotations

import types
from datetime import date

from personal_assistant.services import vault_filter_service as vf


def _item(date_iso, status=None):
    metadata = {"status": status} if status else {}
    return types.SimpleNamespace(date_iso=date_iso, metadata=metadata)


def test_get_items_for_today_filters_by_date_prefix(monkeypatch):
    items = [_item("2026-05-25T10:00:00"), _item("2026-05-24T09:00:00")]
    monkeypatch.setattr(vf, "list_items", lambda limit=500: items)
    out = vf.get_items_for_today(date(2026, 5, 25))
    assert len(out) == 1
    assert out[0].date_iso.startswith("2026-05-25")


def test_get_completed_today_filters_status(monkeypatch):
    items = [
        _item("2026-05-25T10:00:00", status="completed"),
        _item("2026-05-25T11:00:00", status="open"),
        _item("2026-05-25T12:00:00"),
    ]
    monkeypatch.setattr(vf, "list_items", lambda limit=500: items)
    out = vf.get_completed_today(date(2026, 5, 25))
    assert len(out) == 1


def test_get_items_last_7_days_window(monkeypatch):
    items = [
        _item("2026-05-25T10:00:00"),  # in
        _item("2026-05-20T10:00:00"),  # in
        _item("2026-05-10T10:00:00"),  # out (>6 days)
        _item("badformat"),            # skipped
    ]
    monkeypatch.setattr(vf, "list_items", lambda limit=1000: items)
    out = vf.get_items_last_7_days(date(2026, 5, 25))
    assert len(out) == 2
