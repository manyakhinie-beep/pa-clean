"""
Unit tests for CalendarReader (calendar_reader.py).

All AppleScript / osascript calls are mocked via @patch('subprocess.run')
so these tests run on any platform (including Linux CI).

Test strategy:
  - Mock subprocess.run to return pre-baked osascript output
  - Verify CalendarReader parses the JSON correctly into CalendarEvent models
  - Cover: list calendars, fetch events, writable/read-only filtering,
    per-calendar timeout, empty vault, malformed JSON graceful handling
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_result(stdout: str, returncode: int = 0, stderr: str = ""):
    """Build a mock subprocess.CompletedProcess."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = stderr
    return result


def _cal_list_output(*cals: tuple[str, bool]) -> str:
    """Build the raw output of _LIST_CALENDARS_SCRIPT."""
    return "\n".join(f"{name}|||{str(writable).lower()}" for name, writable in cals)


def _events_json(*events: dict) -> str:
    """Build a minimal JSON array of event dicts as osascript would return."""
    return json.dumps(list(events))


_MINIMAL_EVENT = {
    "uid": "EVT-001",
    "title": "Team Standup",
    "start": "2026-05-20T09:00:00",
    "end": "2026-05-20T09:30:00",
    "all_day": False,
    "calendar": "Work",
    "location": "Zoom",
    "notes": "Daily sync",
    "url": "",
    "attendees": "",
    "organizer": "",
    "source": "calendar",
}


# ---------------------------------------------------------------------------
# T01: List calendars — basic parsing
# ---------------------------------------------------------------------------


class TestCalendarReaderListCalendars:
    def test_list_calendars_parses_writable(self):
        """_list_calendars() returns writable=True for 'true' entries."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result(
                _cal_list_output(("Work", True), ("Personal", True))
            ),
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            cals = reader._list_calendars()

        assert len(cals) == 2
        assert all(c["writable"] for c in cals)
        assert {c["name"] for c in cals} == {"Work", "Personal"}

    def test_list_calendars_parses_read_only(self):
        """_list_calendars() marks subscribed calendars as writable=False."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result(
                _cal_list_output(("Holidays", False), ("Work", True))
            ),
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            cals = reader._list_calendars()

        by_name = {c["name"]: c for c in cals}
        assert by_name["Holidays"]["writable"] is False
        assert by_name["Work"]["writable"] is True

    def test_list_calendars_empty_output(self):
        """_list_calendars() returns [] when osascript returns empty string."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", return_value=_make_run_result("")
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            cals = reader._list_calendars()

        assert cals == []

    def test_list_calendars_runtime_error_on_osascript_failure(self):
        """_list_calendars() raises RuntimeError when osascript exits non-zero."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result("", returncode=1, stderr="Not allowed"),
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            with pytest.raises(RuntimeError, match="osascript error"):
                reader._list_calendars()


# ---------------------------------------------------------------------------
# T02: fetch_events — happy path
# ---------------------------------------------------------------------------


class TestCalendarReaderFetchEvents:
    def _mock_subprocess_for_fetch(self, cal_list_output: str, events_json_str: str):
        """Return side_effect list: first call = list cals, second = fetch events."""
        return [
            _make_run_result(cal_list_output),
            _make_run_result(events_json_str),
        ]

    def test_fetch_events_returns_calendar_events(self):
        """fetch_events() parses JSON and returns CalendarEvent objects."""
        from personal_assistant.models import CalendarEvent

        side_effects = self._mock_subprocess_for_fetch(
            _cal_list_output(("Work", True)),
            _events_json(_MINIMAL_EVENT),
        )

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events(days_back=7, days_forward=30)

        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, CalendarEvent)
        assert ev.uid == "EVT-001"
        assert ev.title == "Team Standup"
        assert ev.calendar_name == "Work"

    def test_fetch_events_includes_readonly_calendars(self):
        """By default, ALL calendars are fetched — read-only ones contain useful events."""
        # Two calendars: Holidays (read-only) and Work (writable) — both fetched
        side_effects = [
            _make_run_result(
                _cal_list_output(("Holidays", False), ("Work", True))
            ),
            _make_run_result(_events_json(_MINIMAL_EVENT)),  # fetch Holidays
            _make_run_result(_events_json(_MINIMAL_EVENT)),  # fetch Work
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ) as mock_run:
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            reader.fetch_events()

        # osascript was called three times: list + two calendar fetches
        assert mock_run.call_count == 3

    def test_fetch_events_with_explicit_calendar_names(self):
        """calendar_names allowlist overrides writable filtering."""
        # Holidays is read-only, but user explicitly requests it
        side_effects = [
            _make_run_result(
                _cal_list_output(("Holidays", False), ("Work", True))
            ),
            _make_run_result(_events_json({**_MINIMAL_EVENT, "calendar": "Holidays"})),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events(calendar_names=["Holidays"])

        assert len(events) == 1
        assert events[0].calendar_name == "Holidays"

    def test_fetch_events_empty_when_no_calendars(self):
        """fetch_events() returns [] when no writable calendars exist."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result(
                _cal_list_output(("Holidays", False))
            ),
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        assert events == []

    def test_fetch_events_returns_empty_list_on_osascript_calendar_list_error(self):
        """fetch_events() returns [] gracefully if calendar listing fails."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result("", returncode=1, stderr="Access denied"),
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        assert events == []

    def test_fetch_multiple_events(self):
        """fetch_events() handles multiple events from one calendar."""
        event2 = {**_MINIMAL_EVENT, "uid": "EVT-002", "title": "Design Review"}

        side_effects = [
            _make_run_result(_cal_list_output(("Work", True))),
            _make_run_result(_events_json(_MINIMAL_EVENT, event2)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        assert len(events) == 2
        titles = {e.title for e in events}
        assert titles == {"Team Standup", "Design Review"}

    def test_fetch_events_multiple_calendars(self):
        """Events are aggregated across multiple calendars."""
        event_personal = {
            **_MINIMAL_EVENT,
            "uid": "EVT-P01",
            "title": "Birthday",
            "calendar": "Personal",
        }

        side_effects = [
            _make_run_result(_cal_list_output(("Work", True), ("Personal", True))),
            _make_run_result(_events_json(_MINIMAL_EVENT)),       # Work
            _make_run_result(_events_json(event_personal)),        # Personal
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        assert len(events) == 2
        calendars = {e.calendar_name for e in events}
        assert calendars == {"Work", "Personal"}


# ---------------------------------------------------------------------------
# T03: Malformed / edge-case JSON handling
# ---------------------------------------------------------------------------


class TestCalendarReaderMalformedJSON:
    def test_malformed_json_returns_empty_for_that_calendar(self):
        """Malformed osascript JSON output does not crash — returns []."""
        side_effects = [
            _make_run_result(_cal_list_output(("Work", True))),
            _make_run_result("not valid json at all"),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        # Should return [] or partial, not raise
        assert isinstance(events, list)

    def test_empty_json_array_returns_empty(self):
        """fetch_events() returns [] when the event script returns '[]'."""
        side_effects = [
            _make_run_result(_cal_list_output(("Work", True))),
            _make_run_result("[]"),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        assert events == []


# ---------------------------------------------------------------------------
# T04: Non-macOS platform raises RuntimeError in run_applescript
# ---------------------------------------------------------------------------


class TestCalendarReaderPlatform:
    def test_run_applescript_raises_on_linux(self):
        """run_applescript() raises RuntimeError on non-macOS platforms."""
        with patch("sys.platform", "linux"):
            from personal_assistant.readers.applescript_base import run_applescript

            with pytest.raises(RuntimeError, match="macOS"):
                run_applescript("tell application X to quit")

    def test_calendar_reader_fetch_returns_empty_on_non_macos(self):
        """CalendarReader.fetch_events() returns [] gracefully on Linux."""
        with patch("sys.platform", "linux"):
            from personal_assistant.readers.calendar_reader import CalendarReader

            reader = CalendarReader()
            events = reader.fetch_events()

        assert events == []
