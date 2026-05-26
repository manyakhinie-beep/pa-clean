"""Unit tests for reports/store.py (atomic JSON persistence)."""

from __future__ import annotations

import pytest

from personal_assistant.report_schemas import ReportRecord, ReportType
from personal_assistant.reports import store


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    p = tmp_path / "reports.json"
    monkeypatch.setattr(store, "_STORE_PATH", p)
    return p


def _rec(content="c"):
    return ReportRecord(type=ReportType.DAILY_AGENDA, content=content)


def test_save_and_list_newest_first(store_path):
    store.save_report(_rec("r1"))
    store.save_report(_rec("r2"))
    items = store.list_reports()
    assert [r.content for r in items] == ["r2", "r1"]


def test_get_report(store_path):
    rec = _rec("x")
    store.save_report(rec)
    assert store.get_report(rec.id).content == "x"
    assert store.get_report("missing") is None


def test_delete_report(store_path):
    rec = _rec()
    store.save_report(rec)
    assert store.delete_report(rec.id) is True
    assert store.delete_report(rec.id) is False
    assert store.list_reports() == []


def test_list_respects_limit(store_path):
    for i in range(5):
        store.save_report(_rec(str(i)))
    assert len(store.list_reports(limit=2)) == 2


def test_corrupt_store_returns_empty(store_path):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{ not json", encoding="utf-8")
    assert store.list_reports() == []
