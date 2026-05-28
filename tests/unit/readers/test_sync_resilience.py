"""
Unit tests for the «A+B sync resilience» surface on MailReader and
CalendarReader:

  * fetch_messages / fetch_events accept ``since`` and ``since_per_mailbox`` /
    ``since_per_calendar`` parameters
  * ``last_report`` is populated after every run with per-bucket {ok, count,
    error, duration_s, since}
  * a per-mailbox / per-calendar timeout is isolated and reported, the rest
    of the buckets keep going (skip-on-fail)
  * ``_resolve_seconds_back`` respects ``days_back`` ceiling and 60s floor
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ok(stdout: str):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = 0
    r.stderr = ""
    return r


def _mbox_list(*pairs):
    return "\n".join(f"{a}|||{m}" for a, m in pairs)


def _cal_list(*pairs):
    return "\n".join(f"{name}|||{str(w).lower()}" for name, w in pairs)


_MIN_MAIL = {
    "id": "M1",
    "subject": "x",
    "sender": "a@a.com",
    "recipients": "",
    "cc": "",
    "date": "2026-05-28T10:00:00",
    "mailbox": "INBOX",
    "body": "",
    "has_attachments": "false",
    "attachment_names": "",
    "source": "mail",
}

_MIN_EVENT = {
    "uid": "E1",
    "title": "Standup",
    "start": "2026-05-28T09:00:00",
    "end": "2026-05-28T09:30:00",
    "all_day": False,
    "calendar": "Work",
    "location": "",
    "notes": "",
    "url": "",
    "attendees": "",
    "organizer": "",
    "source": "calendar",
}


# ----------------------------------------------------------------------
# _resolve_seconds_back
# ----------------------------------------------------------------------


class TestResolveSecondsBack:
    def test_no_since_uses_full_days_back(self):
        from personal_assistant.readers.mail_reader import _resolve_seconds_back

        assert _resolve_seconds_back(7, None) == 7 * 86400

    def test_since_in_past_uses_min_with_days_back(self):
        from personal_assistant.readers.mail_reader import _resolve_seconds_back

        since = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        seconds = _resolve_seconds_back(30, since)
        # ~2 hours = 7200 s; allow ±60 s for test execution time.
        assert 7200 - 60 <= seconds <= 7200 + 60

    def test_since_older_than_days_back_clamps_to_ceiling(self):
        from personal_assistant.readers.mail_reader import _resolve_seconds_back

        # since = 90 days ago, ceiling = 7 days
        since = datetime.now(tz=timezone.utc) - timedelta(days=90)
        assert _resolve_seconds_back(7, since) == 7 * 86400

    def test_since_in_future_floors_to_minimum_window(self):
        from personal_assistant.readers.mail_reader import (
            _MIN_WINDOW_SECONDS,
            _resolve_seconds_back,
        )

        since = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        # clock-skew: watermark from "future" → never go below 60 s window
        assert _resolve_seconds_back(7, since) == _MIN_WINDOW_SECONDS

    def test_naive_datetime_treated_as_utc(self):
        from personal_assistant.readers.mail_reader import _resolve_seconds_back

        naive = datetime.now() - timedelta(hours=1)
        seconds = _resolve_seconds_back(7, naive)
        assert seconds > 0


# ----------------------------------------------------------------------
# MailReader: last_report + since wiring + skip-on-fail
# ----------------------------------------------------------------------


class TestMailReaderResilience:
    def test_last_report_populated_on_success(self):
        side_effects = [
            _ok(_mbox_list(("Work", "INBOX"))),
            _ok(json.dumps([_MIN_MAIL])),
        ]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            r = MailReader()
            msgs = r.fetch_messages(days_back=7)

        assert len(msgs) == 1
        assert "Work/INBOX" in r.last_report
        entry = r.last_report["Work/INBOX"]
        assert entry["ok"] is True
        assert entry["count"] == 1
        assert entry["error"] == ""
        assert "duration_s" in entry

    def test_timeout_in_one_mailbox_isolated(self):
        """One mailbox times out → reported as failure, the other still succeeds."""
        side_effects = [
            _ok(_mbox_list(("Work", "INBOX"), ("Personal", "Mail"))),
            subprocess.TimeoutExpired(cmd="osascript", timeout=1),
            subprocess.TimeoutExpired(cmd="osascript", timeout=1),
            subprocess.TimeoutExpired(cmd="osascript", timeout=1),
            _ok(json.dumps([_MIN_MAIL])),
        ]
        from personal_assistant.readers import applescript_base

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ), patch.object(applescript_base, "RETRY_BACKOFF_SECONDS", (0.0, 0.0)):
            from personal_assistant.readers.mail_reader import MailReader

            r = MailReader()
            msgs = r.fetch_messages(days_back=7)

        # Only the second mailbox produced a message
        assert len(msgs) == 1
        # Both mailboxes recorded
        assert set(r.last_report.keys()) == {"Work/INBOX", "Personal/Mail"}
        assert r.last_report["Work/INBOX"]["ok"] is False
        assert "timeout" in r.last_report["Work/INBOX"]["error"]
        assert r.last_report["Personal/Mail"]["ok"] is True

    def test_since_per_mailbox_overrides_global_since(self):
        """When per-mailbox watermark is set, global ``since`` is ignored
        for that mailbox.  We can't easily peek into the AppleScript text
        (it's formatted with the seconds), but we can confirm via report.since
        which we record verbatim."""
        side_effects = [
            _ok(_mbox_list(("Work", "INBOX"))),
            _ok("[]"),
        ]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            r = MailReader()
            specific = datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc)
            global_since = datetime(2026, 5, 27, tzinfo=timezone.utc)
            r.fetch_messages(
                days_back=30,
                since=global_since,
                since_per_mailbox={"Work/INBOX": specific},
            )

        entry = r.last_report["Work/INBOX"]
        assert entry["since"] == specific.isoformat()

    def test_last_report_reset_between_runs(self):
        """A fresh call should not carry telemetry from previous runs."""
        from personal_assistant.readers.mail_reader import MailReader

        r = MailReader()
        r.last_report = {"stale": {"ok": True, "count": 99, "error": "", "duration_s": 1}}
        side_effects = [_ok(_mbox_list(("Work", "INBOX"))), _ok("[]")]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            r.fetch_messages(days_back=1)
        assert "stale" not in r.last_report

    def test_fallback_to_flat_listing_when_recursive_fails(self):
        """If the recursive AppleScript errors out (e.g. -2741 «expected
        class name, got identifier» on edge-case accounts), MailReader
        falls back to the legacy flat listing — top-level mailboxes
        still show up, no «0 mailboxes» regression."""
        # First call (recursive script): error.  Second call (flat
        # fallback): success.  Then the fetch script for that one mailbox.
        side_effects = [
            # Recursive fails
            MagicMock(stdout="", returncode=1, stderr="execution error: -2741"),
            # Flat fallback succeeds
            MagicMock(stdout=_mbox_list(("Work", "INBOX")), returncode=0, stderr=""),
            # Per-mailbox fetch
            MagicMock(stdout=json.dumps([_MIN_MAIL]), returncode=0, stderr=""),
        ]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            r = MailReader()
            msgs = r.fetch_messages(days_back=1)

        # Fallback path returned a mailbox → message came through
        assert len(msgs) == 1
        assert "Work/INBOX" in r.last_report


# ----------------------------------------------------------------------
# CalendarReader: same surface
# ----------------------------------------------------------------------


class TestCalendarReaderResilience:
    def test_last_report_populated_on_success(self):
        side_effects = [
            _ok(_cal_list(("Work", True))),
            _ok(json.dumps([_MIN_EVENT])),
        ]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            r = CalendarReader()
            evts = r.fetch_events(days_back=7, days_forward=7)

        assert len(evts) == 1
        assert "Work" in r.last_report
        assert r.last_report["Work"]["ok"] is True
        assert r.last_report["Work"]["count"] == 1

    def test_timeout_in_one_calendar_isolated(self):
        """Big subscribed calendar times out; the other still loads."""
        side_effects = [
            _ok(_cal_list(("Work", True), ("Holidays", True))),
            subprocess.TimeoutExpired(cmd="osascript", timeout=1),
            subprocess.TimeoutExpired(cmd="osascript", timeout=1),
            subprocess.TimeoutExpired(cmd="osascript", timeout=1),
            _ok(json.dumps([_MIN_EVENT])),
        ]
        from personal_assistant.readers import applescript_base

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ), patch.object(applescript_base, "RETRY_BACKOFF_SECONDS", (0.0, 0.0)):
            from personal_assistant.readers.calendar_reader import CalendarReader

            r = CalendarReader()
            evts = r.fetch_events(days_back=7, days_forward=7)

        assert len(evts) == 1
        assert set(r.last_report.keys()) == {"Work", "Holidays"}
        assert r.last_report["Work"]["ok"] is False
        assert r.last_report["Holidays"]["ok"] is True

    def test_since_per_calendar_recorded_in_report(self):
        side_effects = [
            _ok(_cal_list(("Work", True))),
            _ok("[]"),
        ]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            r = CalendarReader()
            since = datetime(2026, 5, 28, 6, 0, tzinfo=timezone.utc)
            r.fetch_events(
                days_back=30, days_forward=30,
                since_per_calendar={"Work": since},
            )

        assert r.last_report["Work"]["since"] == since.isoformat()
