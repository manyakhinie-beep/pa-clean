"""
AppleScript Scenario Tests — проверяют реальное взаимодействие с Calendar.app и Mail.app.

Пропускаются автоматически если:
  - Не macOS
  - Нет разрешения Automation для Terminal → Calendar / Mail
  - Приложения не установлены

Запуск:
    uv run pytest tests/scenarios/test_applescript_scenarios.py -v

Внимание: тесты записи создают и удаляют тестовое событие в Calendar.app.
Тесты чтения Mail не изменяют данные.
"""

from __future__ import annotations

import sys
import uuid

import pytest

# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------


def _skip_reason() -> str | None:
    if sys.platform != "darwin":
        return "requires macOS"

    from personal_assistant.readers.applescript_base import is_app_running, run_applescript

    # Fast check: System Events must respond
    try:
        run_applescript(
            'tell application "System Events" to return "ok"',
            timeout=5,
        )
    except Exception as exc:
        return f"AppleScript unavailable or no permission: {exc}"

    # Calendar.app must be running; launching it in a sandbox/ssh session hangs
    if not is_app_running("Calendar"):
        return "Calendar.app is not running"
    if not is_app_running("Mail"):
        return "Mail.app is not running"

    return None


# This module drives real Calendar.app / Mail.app via AppleScript and needs
# macOS Automation permission. Mark it 'live' so unattended runs can exclude it
# with ``-m "not live"``. The access probe runs at setup time (autouse fixture
# below) rather than at import, so a ``-m "not live"`` selection never triggers
# the macOS permission prompt during collection.
#
# The filename "test_applescript_scenarios.py" carries no ``calendar``/``mail``
# keywords, so the path-based auto-marker in tests/conftest.py does not tag
# this file. Add both sub-markers explicitly so it is selected by
# ``-m "scenario and live and calendar"`` and ``... and mail"``.
pytestmark = [pytest.mark.live, pytest.mark.calendar, pytest.mark.mail]


@pytest.fixture(autouse=True, scope="module")
def _require_applescript_access():
    reason = _skip_reason()
    if reason:
        pytest.skip(f"AppleScript scenario skipped: {reason}")
    yield


@pytest.fixture(autouse=True, scope="module")
def _disable_e2e_test_mode():
    """Root conftest forces ``e2e_test_mode=True`` so unit/e2e never touch
    real Apple apps. But these are *live* tests — ``calendar_writer.create_event``
    would otherwise short-circuit to ``event_uid="e2e-test-mode"`` instead of
    actually creating an event in Calendar.app, breaking the real-write
    assertions.

    Module-scoped (not function-scoped) so that this fixture runs BEFORE the
    class-scoped ``test_event_uid`` fixture below — otherwise the test event
    would be created with the stub UID before this short-circuit could be
    disabled. ``monkeypatch`` is function-scoped so we save/restore manually."""
    from personal_assistant.config import settings

    orig = settings.e2e_test_mode
    settings.e2e_test_mode = False
    yield
    settings.e2e_test_mode = orig


# ---------------------------------------------------------------------------
# SC-AS-01: Basic AppleScript runner
# ---------------------------------------------------------------------------


class TestAppleScriptBase:
    def test_run_applescript_returns_ok(self):
        from personal_assistant.readers.applescript_base import run_applescript

        out = run_applescript('return "hello from AS"', timeout=5)
        assert out == "hello from AS"

    def test_run_applescript_unicode(self):
        from personal_assistant.readers.applescript_base import run_applescript

        out = run_applescript('return "Привет, мир! 🍎"', timeout=5)
        assert "Привет" in out

    def test_is_app_running_returns_bool(self):
        from personal_assistant.readers.applescript_base import is_app_running

        # Finder is always running on macOS
        assert is_app_running("Finder") is True

    def test_is_app_installed_finds_safari(self):
        from personal_assistant.readers.applescript_base import is_app_installed

        # Safari lives in /System/Applications which is in _APP_SEARCH_PATHS
        assert is_app_installed("Safari") is True


# ---------------------------------------------------------------------------
# SC-AS-02: Calendar read operations
# ---------------------------------------------------------------------------


class TestCalendarReaderReal:
    def test_list_calendars_returns_non_empty(self):
        from personal_assistant.readers.calendar_reader import CalendarReader

        reader = CalendarReader()
        cals = reader._list_calendars()
        assert isinstance(cals, list)
        # Most macOS users have at least one calendar
        assert len(cals) >= 1
        names = {c["name"] for c in cals}
        assert len(names) == len(cals), "Duplicate calendar names detected"
        for c in cals:
            assert "name" in c
            assert "writable" in c

    def test_fetch_events_returns_list(self):
        from personal_assistant.readers.calendar_reader import CalendarReader

        reader = CalendarReader()
        # Very wide window to catch any existing events
        events = reader.fetch_events(days_back=365, days_forward=365, max_events_per_calendar=10)
        assert isinstance(events, list)
        # We don't assert len > 0 because the user might have an empty calendar
        for ev in events:
            assert ev.title
            assert ev.start is not None
            assert ev.uid

    def test_fetch_events_with_attendees_does_not_crash(self):
        from personal_assistant.readers.calendar_reader import CalendarReader

        reader = CalendarReader()
        events = reader.fetch_events(
            days_back=30,
            days_forward=30,
            fetch_attendees=True,
            max_events_per_calendar=5,
        )
        assert isinstance(events, list)


# ---------------------------------------------------------------------------
# SC-AS-03: Calendar write operations (create + cleanup)
# ---------------------------------------------------------------------------


class TestCalendarWriterReal:
    """Создаём реальное событие, проверяем, удаляем."""

    @pytest.fixture(scope="class")
    def test_event_uid(self) -> str | None:
        """Create a test event and yield its UID; delete after class."""
        from personal_assistant.calendar.calendar_writer import create_event
        from personal_assistant.calendar.intent_parser import parse_event_intent
        from personal_assistant.readers.applescript_base import run_applescript

        unique = f"pa-merge-test-{uuid.uuid4().hex[:8]}"
        draft = parse_event_intent(
            f"Тестовое событие {unique} завтра в 12:00 на 30 минут"
        )
        # Ensure title contains our marker for easy cleanup
        draft.title = f"pa-merge-test {unique}"
        draft.calendar_name = "Work"  # commonly exists

        result = create_event(draft, dry_run=False)
        if not result["success"]:
            pytest.skip(f"Cannot create test event: {result['error']}")

        uid = result.get("event_uid") or ""
        yield uid

        # Cleanup: delete the test event by title match
        cleanup_script = '''\
tell application "Calendar"
    repeat with cal in calendars
        repeat with ev in events of cal
            if summary of ev contains "pa-merge-test" then
                delete ev
            end if
        end repeat
    end repeat
end tell
return "cleaned"
'''
        try:
            run_applescript(cleanup_script, timeout=30)
        except Exception:
            pass  # best-effort cleanup

    def test_create_event_success(self, test_event_uid: str | None):
        assert test_event_uid
        assert test_event_uid != "dry-run"

    def test_created_event_appears_in_fetch(self, test_event_uid: str | None):
        from personal_assistant.readers.calendar_reader import CalendarReader

        reader = CalendarReader()
        events = reader.fetch_events(days_back=1, days_forward=7, max_events_per_calendar=50)
        uids = {ev.uid for ev in events}
        assert test_event_uid in uids, (
            f"Created event {test_event_uid} not found in fetch. "
            f"Available UIDs: {list(uids)[:10]}"
        )

    def test_dry_run_returns_script(self):
        from personal_assistant.calendar.calendar_writer import create_event
        from personal_assistant.calendar.intent_parser import parse_event_intent

        draft = parse_event_intent("Встреча с Ивановым завтра в 15:00")
        result = create_event(draft, dry_run=True)
        assert result["success"] is True
        assert result["event_uid"] == "dry-run"
        assert "tell application \"Calendar\"" in result["applescript"]

    def test_list_writable_calendars_returns_non_empty(self):
        from personal_assistant.calendar.calendar_writer import list_writable_calendars

        names = list_writable_calendars()
        assert isinstance(names, list)
        # Almost every macOS user has at least one writable calendar
        if not names:
            pytest.skip("No writable calendars found on this Mac")


# ---------------------------------------------------------------------------
# SC-AS-04: Mail read operations
# ---------------------------------------------------------------------------


class TestMailReaderReal:
    def test_list_mailboxes_returns_list(self):
        from personal_assistant.readers.mail_reader import MailReader

        reader = MailReader()
        mboxes = reader._list_mailboxes()
        assert isinstance(mboxes, list)
        # If Mail.app is configured there should be mailboxes
        for mb in mboxes:
            assert "account" in mb
            assert "mailbox" in mb

    def test_fetch_messages_returns_list(self):
        from personal_assistant.readers.mail_reader import MailReader

        reader = MailReader()
        # Small window to keep it fast; no body to keep it fast
        msgs = reader.fetch_messages(days_back=30, fetch_body=False)
        assert isinstance(msgs, list)
        for m in msgs:
            assert m.message_id is not None
            assert m.subject is not None
            assert m.date is not None

    def test_fetch_messages_noise_folders_skipped(self):
        from personal_assistant.readers.mail_reader import MailReader

        reader = MailReader()
        msgs = reader.fetch_messages(days_back=7, fetch_body=False)
        for m in msgs:
            assert m.mailbox not in {
                "Sent Messages",
                "Sent",
                "Trash",
                "Junk",
                "Drafts",
                "Archive",
            }, f"Noise folder {m.mailbox} was not skipped"

    def test_extract_contacts_from_real_mail(self):
        from personal_assistant.readers.mail_reader import MailReader

        reader = MailReader()
        msgs = reader.fetch_messages(days_back=30, fetch_body=False)
        if not msgs:
            pytest.skip("No messages to extract contacts from")
        contacts = reader.extract_contacts(msgs)
        assert all("@" in c.email for c in contacts)


# ---------------------------------------------------------------------------
# SC-AS-05: Calendar intent parser — rule-based (no MLX needed)
# ---------------------------------------------------------------------------


class TestIntentParserReal:
    def test_parse_tomorrow_meeting(self):
        from datetime import date, timedelta

        from personal_assistant.calendar.intent_parser import parse_event_intent

        draft = parse_event_intent("Встреча с Ивановым завтра в 15:00")
        assert "встреча" in draft.title.lower() or "иванов" in draft.title.lower()
        assert draft.time_str == "15:00"
        expected_date = (date.today() + timedelta(days=1)).isoformat()
        assert draft.date_iso == expected_date

    def test_parse_next_week(self):
        from datetime import date, timedelta

        from personal_assistant.calendar.intent_parser import parse_event_intent

        draft = parse_event_intent("Созвон с командой в следующий понедельник в 10:00")
        assert draft.time_str == "10:00"
        # Date should be in the future
        assert draft.date_iso
        parsed = date.fromisoformat(draft.date_iso)
        assert parsed > date.today() - timedelta(days=1)
        assert parsed.weekday() == 0  # Monday

    def test_parse_duration_and_location(self):
        from personal_assistant.calendar.intent_parser import parse_event_intent

        draft = parse_event_intent(
            "Встреча с заказчиком в четверг в 14:00 на полтора часа в Zoom"
        )
        assert draft.duration_minutes == 90
        assert "zoom" in draft.location.lower()
        assert draft.time_str == "14:00"

    def test_parse_participants(self):
        from personal_assistant.calendar.intent_parser import parse_event_intent

        draft = parse_event_intent("Встреча с Петровым и Сидоровым завтра в 11:00")
        participants_lower = [p.lower() for p in draft.participants]
        assert any("петров" in p for p in participants_lower)
        assert any("сидоров" in p for p in participants_lower)

    def test_parse_russian_month(self):
        from personal_assistant.calendar.intent_parser import parse_event_intent

        draft = parse_event_intent("Собеседование 15 июня 2026 в 13:00")
        assert draft.date_iso == "2026-06-15"
        assert draft.time_str == "13:00"


# ---------------------------------------------------------------------------
# SC-AS-06: Thread ID computation (pure Python, but critical for AS pipeline)
# ---------------------------------------------------------------------------


class TestThreadIdReal:
    def test_compute_thread_id_stable_for_replies(self):
        from personal_assistant.readers.applescript_base import compute_thread_id

        base = compute_thread_id("Project Update")
        re1 = compute_thread_id("Re: Project Update")
        re2 = compute_thread_id("Отв: Project Update")
        fwd = compute_thread_id("Fwd: Re: Project Update")

        assert base == re1 == re2 == fwd
        assert len(base) == 12

    def test_compute_thread_id_different_subjects(self):
        from personal_assistant.readers.applescript_base import compute_thread_id

        a = compute_thread_id("Invoice #1042")
        b = compute_thread_id("Invoice #1043")
        assert a != b
