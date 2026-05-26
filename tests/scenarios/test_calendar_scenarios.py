"""
Scenario tests for Calendar integration fixes.

Covers:
  1. All calendars (including read-only) are synced to vault
  2. Meeting creation asks for calendar when not specified
  3. User can override calendar name in create-from-text

These tests use mocks for AppleScript and do not require Calendar.app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    from personal_assistant.mlx_server.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary vault directory and patch settings.vault_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "calendar").mkdir()

    mock_settings = MagicMock()
    mock_settings.vault_path = vault

    with patch("personal_assistant.config.settings", mock_settings):
        yield vault


# ---------------------------------------------------------------------------
# 1. All calendars synced (including read-only)
# ---------------------------------------------------------------------------


class TestCalendarSyncAll:
    def test_fetch_messages_includes_read_only(self) -> None:
        """CalendarReader.fetch_messages should include read-only calendars by default."""
        from personal_assistant.readers.calendar_reader import CalendarReader

        reader = CalendarReader()

        # Mock _list_calendars to return both writable and read-only
        with patch.object(
            reader,
            "_list_calendars",
            return_value=[
                {"name": "Work", "writable": True},
                {"name": "Personal", "writable": True},
                {"name": "Holidays", "writable": False},
                {"name": "Birthdays", "writable": False},
            ],
        ), patch.object(
            reader,
            "_fetch_one_calendar",
            return_value=[],
        ) as mock_fetch:
            reader.fetch_events(days_back=7, days_forward=7)

        # Should fetch events for ALL 4 calendars, not just writable ones
        fetched_names = [call.kwargs.get("cal_name") or (call.args[0] if call.args else None) for call in mock_fetch.call_args_list]
        assert "Work" in fetched_names
        assert "Personal" in fetched_names
        assert "Holidays" in fetched_names
        assert "Birthdays" in fetched_names
        assert len(fetched_names) == 4

    def test_fetch_messages_with_explicit_names_bypasses_filter(self) -> None:
        """When calendar_names is provided, only named calendars are fetched."""
        from personal_assistant.readers.calendar_reader import CalendarReader

        reader = CalendarReader()

        with patch.object(
            reader,
            "_list_calendars",
            return_value=[
                {"name": "Work", "writable": True},
                {"name": "Holidays", "writable": False},
            ],
        ), patch.object(
            reader,
            "_fetch_one_calendar",
            return_value=[],
        ) as mock_fetch:
            reader.fetch_events(
                days_back=7,
                days_forward=7,
                calendar_names=["Holidays"],
            )

        fetched_names = [call.kwargs.get("cal_name") or (call.args[0] if call.args else None) for call in mock_fetch.call_args_list]
        assert fetched_names == ["Holidays"]


# ---------------------------------------------------------------------------
# 2. Meeting creation asks for calendar
# ---------------------------------------------------------------------------


class TestMeetingCalendarSelection:
    def test_parse_intent_needs_calendar_when_not_specified(self, client: TestClient) -> None:
        """parse-intent returns needs_calendar=True when no calendar hint in text."""
        with patch(
            "personal_assistant.calendar.calendar_writer.list_calendars",
            return_value=["Work", "Personal", "Home"],
        ):
            resp = client.post(
                "/api/v1/calendar/parse-intent",
                json={"text": "Встреча с Ивановым завтра в 15:00"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["draft"]["calendar_name"] is None
        assert data["needs_calendar"] is True
        assert "Work" in data["available_calendars"]
        assert "Personal" in data["available_calendars"]

    def test_parse_intent_detects_calendar_from_text(self, client: TestClient) -> None:
        """parse-intent returns calendar_name when Russian hint is present."""
        resp = client.post(
            "/api/v1/calendar/parse-intent",
            json={"text": "Встреча в личном календаре завтра в 15:00"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["draft"]["calendar_name"] == "Personal"
        assert "needs_calendar" not in data

    def test_parse_intent_detects_work_calendar(self, client: TestClient) -> None:
        """parse-intent returns Work when Russian work hint is present."""
        resp = client.post(
            "/api/v1/calendar/parse-intent",
            json={"text": "Рабочая встреча завтра в 10:00"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["draft"]["calendar_name"] == "Work"

    def test_create_from_text_asks_for_calendar(self, client: TestClient) -> None:
        """create-from-text returns needs_calendar when calendar not specified."""
        with patch(
            "personal_assistant.calendar.calendar_writer.list_calendars",
            return_value=["Work", "Personal"],
        ):
            resp = client.post(
                "/api/v1/calendar/create-from-text",
                json={
                    "text": "Созвон с командой в пятницу в 16:00",
                    "confirmed": False,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is False
        assert data["needs_calendar"] is True
        assert "Work" in data["available_calendars"]

    def test_create_from_text_with_override_calendar(self, client: TestClient) -> None:
        """create-from-text uses user-provided calendar_name override."""
        with patch(
            "personal_assistant.calendar.calendar_writer.run_applescript",
            return_value="new-event-uid-123",
        ):
            resp = client.post(
                "/api/v1/calendar/create-from-text",
                json={
                    "text": "Встреча завтра в 12:00",
                    "confirmed": True,
                    "calendar_name": "Personal",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is True
        assert data["draft"]["calendar_name"] == "Personal"

    def test_create_from_text_with_detected_calendar(self, client: TestClient) -> None:
        """create-from-text works when calendar is auto-detected from text."""
        with patch(
            "personal_assistant.calendar.calendar_writer.run_applescript",
            return_value="new-event-uid-456",
        ):
            resp = client.post(
                "/api/v1/calendar/create-from-text",
                json={
                    "text": "Домашнее совещание в субботу в 11:00",
                    "confirmed": True,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] is True
        assert data["draft"]["calendar_name"] == "Home"

    def test_list_calendars_returns_all(self, client: TestClient) -> None:
        """GET /calendars returns all calendars including read-only."""
        with patch(
            "personal_assistant.calendar.calendar_writer.run_applescript",
            return_value="Work\nPersonal\nHolidays\nBirthdays",
        ):
            resp = client.get("/api/v1/calendar/calendars")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 4
        assert "Holidays" in data["calendars"]
        assert "Birthdays" in data["calendars"]


# ---------------------------------------------------------------------------
# 3. calendar_writer.create_event handles None calendar_name
# ---------------------------------------------------------------------------


class TestCalendarWriterFallback:
    def test_create_event_script_uses_fallback_when_no_calendar(self) -> None:
        """AppleScript template falls back to first writable calendar."""
        from personal_assistant.calendar.calendar_writer import _CREATE_EVENT_SCRIPT
        from personal_assistant.calendar.intent_parser import EventDraft

        _draft = EventDraft(
            title="Test Event",
            date_iso="2024-06-15",
            time_str="14:00",
            duration_minutes=60,
            calendar_name=None,
        )
        # The script should contain fallback logic for missing calendar
        assert "targetCal is missing value" in _CREATE_EVENT_SCRIPT
        assert "first writable calendar" in _CREATE_EVENT_SCRIPT or "writable" in _CREATE_EVENT_SCRIPT
