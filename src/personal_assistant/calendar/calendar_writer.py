"""
calendar_writer.py — Create events in Apple Calendar via AppleScript.

Only works on macOS with Calendar.app and Automation permission granted.
All write operations are non-destructive: we never modify or delete
existing events — only create new ones.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from personal_assistant.calendar.intent_parser import EventDraft
from personal_assistant.config import settings
from personal_assistant.readers.applescript_base import run_applescript

# ---------------------------------------------------------------------------
# AppleScript templates
# ---------------------------------------------------------------------------

# Creates an event in the named calendar.
# We set date properties numerically to avoid locale-specific parsing issues.
_CREATE_EVENT_SCRIPT = """\
tell application "Calendar"
    -- Find or use the first writable calendar
    set targetCal to missing value
    repeat with cal in calendars
        if (name of cal as string) is "{cal_name}" then
            set targetCal to cal
            exit repeat
        end if
    end repeat
    if targetCal is missing value then
        -- Fallback: first writable calendar
        repeat with cal in calendars
            set isW to true
            try
                set isW to writable of cal
            end try
            if isW then
                set targetCal to cal
                exit repeat
            end if
        end repeat
    end if
    if targetCal is missing value then
        return "ERROR: No writable calendar found"
    end if

    -- Build start date
    set startDate to current date
    set year of startDate to {start_year}
    set month of startDate to {start_month}
    set day of startDate to {start_day}
    set hours of startDate to {start_hour}
    set minutes of startDate to {start_minute}
    set seconds of startDate to 0

    -- Build end date
    set endDate to current date
    set year of endDate to {end_year}
    set month of endDate to {end_month}
    set day of endDate to {end_day}
    set hours of endDate to {end_hour}
    set minutes of endDate to {end_minute}
    set seconds of endDate to 0

    -- Create the event
    set newEvent to make new event at end of events of targetCal with properties {{summary: "{title_esc}", start date: startDate, end date: endDate{location_prop}{notes_prop}}}

    -- Add attendees / invitees note if participants given
    {attendees_script}

    return uid of newEvent as string
end tell
"""

_ATTENDEES_SCRIPT = """\
    -- Best-effort: appending attendees as a notes line must NOT abort the
    -- script (the event has already been created). Some macOS Calendar
    -- versions return ``notes`` as a rich-text object that can't be coerced
    -- to text and raise -1700, which previously hid the (successful) ``uid``
    -- return value and made the caller think creation failed.
    try
        set existingNotes to ""
        try
            set existingNotes to (notes of newEvent) as string
        end try
        set notes of newEvent to existingNotes & "{attendees_note}"
    end try
"""


def _esc_as(text: str) -> str:
    """Escape text for AppleScript double-quoted string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _build_create_script(draft: EventDraft) -> str:
    """Build the AppleScript to create an event from an EventDraft."""
    start_dt = datetime.fromisoformat(draft.start_iso)
    end_dt = datetime.fromisoformat(draft.end_iso)

    # Location property (optional)
    location_prop = ""
    if draft.location:
        loc_esc = _esc_as(draft.location)
        location_prop = f', location: "{loc_esc}"'

    # Notes property (optional)
    notes_prop = ""
    if draft.notes:
        notes_esc = _esc_as(draft.notes)
        notes_prop = f', notes: "{notes_esc}"'

    # Attendees — appended to notes (Calendar.app doesn't expose attendee
    # creation via AppleScript in non-Exchange calendars)
    attendees_script = ""
    if draft.participants:
        names = ", ".join(draft.participants)
        note_line = f"\\nУчастники: {names}"
        attendees_script = _ATTENDEES_SCRIPT.format(
            attendees_note=_esc_as(note_line)
        )

    # Fallback calendar name for dry_run when user hasn't picked one yet
    cal_name = draft.calendar_name or "Calendar"
    return _CREATE_EVENT_SCRIPT.format(
        cal_name=_esc_as(cal_name),
        start_year=start_dt.year,
        start_month=start_dt.month,
        start_day=start_dt.day,
        start_hour=start_dt.hour,
        start_minute=start_dt.minute,
        end_year=end_dt.year,
        end_month=end_dt.month,
        end_day=end_dt.day,
        end_hour=end_dt.hour,
        end_minute=end_dt.minute,
        title_esc=_esc_as(draft.title),
        location_prop=location_prop,
        notes_prop=notes_prop,
        attendees_script=attendees_script,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CalendarWriteError(RuntimeError):
    """Raised when AppleScript event creation fails."""


def create_event(draft: EventDraft, dry_run: bool = False) -> dict:
    """
    Create a Calendar.app event from an EventDraft.

    Args:
        draft:   EventDraft with all required fields populated.
        dry_run: If True, build and return the AppleScript but don't execute.

    Returns:
        {
            success: bool,
            event_uid: str | None,
            applescript: str,   # for debugging / dry_run
            error: str | None,
        }

    Raises:
        CalendarWriteError on unexpected failures (caller decides how to handle).
    """
    script = _build_create_script(draft)
    result: dict = {
        "success": False,
        "event_uid": None,
        "applescript": script,
        "error": None,
    }

    if dry_run:
        result["success"] = True
        result["event_uid"] = "dry-run"
        return result

    # Test mode: never touch the real Calendar — simulate a successful write so
    # scenario/e2e tests can exercise the full flow without side effects.
    if settings.e2e_test_mode:
        result["success"] = True
        result["event_uid"] = "e2e-test-mode"
        logger.info(
            f"[calendar_writer] e2e_test_mode: skipped real event '{draft.title}'"
        )
        return result

    try:
        output = run_applescript(script, timeout=30)
        output = output.strip()
        if output.startswith("ERROR:"):
            result["error"] = output
            logger.warning(f"[calendar_writer] AppleScript error: {output}")
        else:
            result["success"] = True
            result["event_uid"] = output or "created"
            logger.info(
                f"[calendar_writer] Created event '{draft.title}' "
                f"uid={output!r} cal={draft.calendar_name!r}"
            )
    except Exception as exc:
        msg = str(exc)
        result["error"] = msg
        logger.warning(f"[calendar_writer] create_event failed: {msg}")

    return result


def list_calendars() -> list[str]:
    """Return names of ALL calendars from Calendar.app (writable and read-only)."""
    script = """\
tell application "Calendar"
    set result_lines to {}
    repeat with cal in calendars
        set end of result_lines to name of cal as string
    end repeat
end tell
set AppleScript's text item delimiters to "\\n"
set output to result_lines as string
set AppleScript's text item delimiters to ""
return output
"""
    try:
        output = run_applescript(script, timeout=10)
        names = [n.strip() for n in output.splitlines() if n.strip()]
        return names
    except Exception as exc:
        logger.debug(f"[calendar_writer] list_calendars failed: {exc}")
        return []


def list_writable_calendars() -> list[str]:
    """Return names of writable calendars only (excludes Holidays, Birthdays, etc.)."""
    script = """\
tell application "Calendar"
    set result_lines to {}
    repeat with cal in calendars
        try
            if writable of cal is true then
                set end of result_lines to name of cal as string
            end if
        end try
    end repeat
end tell
set AppleScript's text item delimiters to "\\n"
set output to result_lines as string
set AppleScript's text item delimiters to ""
return output
"""
    try:
        output = run_applescript(script, timeout=10)
        names = [n.strip() for n in output.splitlines() if n.strip()]
        return names
    except Exception as exc:
        logger.debug(f"[calendar_writer] list_writable_calendars failed: {exc}")
        return []
