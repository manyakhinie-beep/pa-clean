"""Unit tests for MSK timezone helpers (pure)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from personal_assistant.utils import timezone as tz


def test_get_now_msk_is_aware_and_utc_plus_3():
    now = tz.get_now_msk()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 3 * 3600


def test_format_to_msk_iso_naive_gets_offset():
    s = tz.format_to_msk_iso(datetime(2026, 5, 20, 8, 0, 0))
    assert s.endswith("+03:00")


def test_format_to_msk_iso_aware_is_preserved():
    s = tz.format_to_msk_iso(datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc))
    assert "+00:00" in s


def test_format_to_msk_iso_none_uses_now():
    assert tz.format_to_msk_iso().endswith("+03:00")


def test_prompt_str_has_correct_weekday():
    # 2026-05-20 is a Wednesday
    s = tz.format_to_msk_prompt_str(datetime(2026, 5, 20, 8, 38, 28))
    assert s.startswith("Ср (среда),")
    assert "2026-05-20 08:38:28 MSK (UTC+3)" in s


def test_parse_msk_iso_naive_becomes_msk():
    dt = tz.parse_msk_iso("2026-05-20T10:00:00")
    assert dt.utcoffset().total_seconds() == 3 * 3600


def test_parse_msk_iso_converts_utc_to_msk():
    dt = tz.parse_msk_iso("2026-05-20T07:00:00+00:00")
    assert dt.hour == 10  # 07:00 UTC == 10:00 MSK
    assert dt.utcoffset().total_seconds() == 3 * 3600


def test_parse_relative_to_msk_garbage_returns_error():
    res = tz.parse_relative_to_msk("zzz nonsense qqq", anchor=date(2026, 5, 20))
    assert isinstance(res, dict)
    assert "error" in res
