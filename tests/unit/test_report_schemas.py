"""Unit tests for report_schemas (pydantic v2 validation)."""

from __future__ import annotations

import pytest

from personal_assistant.report_schemas import ReportRecord, ReportRequest, ReportType


def test_request_accepts_valid_date():
    r = ReportRequest(report_type=ReportType.DAILY_AGENDA, target_date="2026-05-25")
    assert r.target_date == "2026-05-25"


def test_request_allows_none_date():
    assert ReportRequest(report_type=ReportType.WEEKLY_REVIEW).target_date is None


def test_request_rejects_bad_date():
    with pytest.raises(Exception):
        ReportRequest(report_type=ReportType.DAILY_AGENDA, target_date="25-05-2026")


def test_request_rejects_nonstring_date():
    with pytest.raises(Exception):
        ReportRequest(report_type=ReportType.DAILY_AGENDA, target_date=20260525)


def test_request_forbids_extra_fields():
    with pytest.raises(Exception):
        ReportRequest(report_type=ReportType.DAILY_AGENDA, bogus=1)


def test_record_defaults():
    rec = ReportRecord(type=ReportType.DAILY_AGENDA, content="hello")
    assert len(rec.id) == 8
    assert rec.content == "hello"
    assert rec.vault_scope_ids == []
    assert rec.generated_at  # default ISO timestamp present
