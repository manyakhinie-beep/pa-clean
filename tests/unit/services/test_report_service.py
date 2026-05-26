"""Unit tests for report_service (build_prompt, persistence, generate_report).

MLX and the vault DB are stubbed, so these run on Linux without MLX/Apple.
"""

from __future__ import annotations

import types

import pytest

from personal_assistant.report_schemas import ReportRecord, ReportRequest, ReportType
from personal_assistant.services import report_service as rs


def _item(item_id="i1", subject="Subj", item_type="mail", date_iso="2026-05-25T10:00:00"):
    return types.SimpleNamespace(
        id=item_id, subject=subject, item_type=item_type,
        date_iso=date_iso, full_body="body text",
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(rs, "_REPORTS_FILE", tmp_path / "reports.json")
    return tmp_path


def test_build_prompt_daily():
    p = rs.build_prompt(ReportType.DAILY_AGENDA, [_item(subject="Meet")], "2026-05-25")
    assert "2026-05-25" in p
    assert "Meet" in p
    assert "русском языке" in p


def test_build_prompt_completed_and_weekly():
    assert "выполненных" in rs.build_prompt(ReportType.COMPLETED_REVIEW, [], "2026-05-25")
    assert "недельный" in rs.build_prompt(ReportType.WEEKLY_REVIEW, [], "2026-05-25")


def test_persistence_roundtrip(data_dir):
    rec = ReportRecord(type=ReportType.DAILY_AGENDA, content="hi")
    rs._append_report(rec)
    loaded = rs.load_reports()
    assert len(loaded) == 1 and loaded[0].content == "hi"
    assert rs.get_report_by_id(rec.id).content == "hi"
    assert rs.get_report_by_id("nope") is None


def test_generate_report_no_data(data_dir, monkeypatch):
    monkeypatch.setattr(rs, "get_items_for_today", lambda d=None: [])
    rec = rs.generate_report(
        ReportRequest(report_type=ReportType.DAILY_AGENDA, target_date="2026-05-25")
    )
    assert "Нет данных" in rec.content
    assert rec.target_date == "2026-05-25"
    assert rs.load_reports()[0].id == rec.id


def test_generate_report_with_data(data_dir, monkeypatch):
    monkeypatch.setattr(rs, "get_items_for_today", lambda d=None: [_item("x1"), _item("x2")])
    monkeypatch.setattr(rs, "_mlx_adapter", rs.MLXAdapter(mock_fn=lambda prompt: "GENERATED"))
    rec = rs.generate_report(
        ReportRequest(report_type=ReportType.DAILY_AGENDA, target_date="2026-05-25")
    )
    assert rec.content == "GENERATED"
    assert rec.vault_scope_ids == ["x1", "x2"]


def test_generate_report_weekly_and_completed_no_data(data_dir, monkeypatch):
    monkeypatch.setattr(rs, "get_items_last_7_days", lambda d=None: [])
    monkeypatch.setattr(rs, "get_completed_today", lambda d=None: [])
    weekly = rs.generate_report(ReportRequest(report_type=ReportType.WEEKLY_REVIEW))
    completed = rs.generate_report(ReportRequest(report_type=ReportType.COMPLETED_REVIEW))
    assert "Нет данных" in weekly.content
    assert "Нет данных" in completed.content
