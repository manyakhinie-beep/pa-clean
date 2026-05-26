"""
Apple Calendar reader via osascript (Calendar.app).

Reads events using AppleScript through Calendar.app.
Requires Automation permission:
  System Settings → Privacy & Security → Automation → Terminal → Calendar

Performance notes:
  - Splits into per-calendar AppleScript calls to isolate timeouts.
  - Skips read-only / subscribed calendars (Holidays, iCloud-shared, etc.)
    unless calendar_names is explicitly specified.
  - Attendees are NOT fetched by default (each attendee = one IPC call →
    300 events × 5 attendees = 1 500 round-trips → minutes).
    Pass fetch_attendees=True to include them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from personal_assistant.models import CalendarEvent, Contact
from personal_assistant.readers.applescript_base import (
    AS_PREAMBLE,
    run_applescript,
    safe_str,
    sanitize_json,
)

# ---------------------------------------------------------------------------
# Script 1: list all calendars  (fast, ~0.5 s)
# ---------------------------------------------------------------------------

_LIST_CALENDARS_SCRIPT = """\
tell application "Calendar"
    set result_lines to {}
    repeat with cal in calendars
        set calName to name of cal as string
        set isW to true
        try
            set isW to writable of cal
        end try
        set end of result_lines to calName & "|||" & (isW as string)
    end repeat
end tell
set AppleScript's text item delimiters to "\n"
set output to result_lines as string
set AppleScript's text item delimiters to ""
return output
"""

# ---------------------------------------------------------------------------
# Script 2: fetch events for ONE calendar  (per-calendar, capped, no attendees)
# ---------------------------------------------------------------------------

_FETCH_CAL_SCRIPT = (
    AS_PREAMBLE
    + """\
set startDate to (current date) - ({days_back} * days)
set endDate   to (current date) + ({days_forward} * days)
set maxEvts   to {max_events}

set entries to {{}}

tell application "Calendar"
    set targetCal to missing value
    repeat with cal in calendars
        if (name of cal as string) is "{cal_name_esc}" then
            set targetCal to cal
            exit repeat
        end if
    end repeat
    if targetCal is missing value then return "[]"

    set evts to (every event of targetCal whose start date ≥ startDate and start date ≤ endDate)
    set evCount to my minVal(count of evts, maxEvts)
    repeat with i from 1 to evCount
        set ev to item i of evts

        set evUID to uid of ev as string

        set evTitle to ""
        try
            set evTitle to my esc(summary of ev)
        end try

        set evStart to my isoDate(start date of ev)
        set evEnd   to my isoDate(end date of ev)

        set evAllDay to "false"
        try
            if allday event of ev then set evAllDay to "true"
        end try

        set evLocation to ""
        try
            if location of ev is not missing value then
                set evLocation to my esc(location of ev)
            end if
        end try

        set evNotes to ""
        try
            if description of ev is not missing value then
                set evNotes to my esc(description of ev)
            end if
        end try

        set evURL to ""
        try
            if url of ev is not missing value then
                set evURL to my esc(url of ev as string)
            end if
        end try

        set entry to "{{" & ¬
            "\\"uid\\":\\"" & my esc(evUID) & "\\"," & ¬
            "\\"title\\":\\"" & evTitle & "\\"," & ¬
            "\\"start\\":\\"" & evStart & "\\"," & ¬
            "\\"end\\":\\"" & evEnd & "\\"," & ¬
            "\\"all_day\\":" & evAllDay & "," & ¬
            "\\"calendar\\":\\"" & my esc("{cal_name_esc}") & "\\"," & ¬
            "\\"location\\":\\"" & evLocation & "\\"," & ¬
            "\\"notes\\":\\"" & evNotes & "\\"," & ¬
            "\\"url\\":\\"" & evURL & "\\"," & ¬
            "\\"attendees\\":\\"\\"," & ¬
            "\\"organizer\\":\\"\\"," & ¬
            "\\"source\\":\\"calendar\\"" & ¬
            "}}"
        set end of entries to entry
    end repeat
end tell

set AppleScript's text item delimiters to ","
set output to "[" & (entries as string) & "]"
set AppleScript's text item delimiters to ""
return output
"""
)

# ---------------------------------------------------------------------------
# Script 3: attendees for ONE calendar  (optional, slow)
# ---------------------------------------------------------------------------

_FETCH_ATTENDEES_SCRIPT = (
    AS_PREAMBLE
    + """\
set startDate to (current date) - ({days_back} * days)
set endDate   to (current date) + ({days_forward} * days)
set maxEvts   to {max_events}

set entries to {{}}

tell application "Calendar"
    set targetCal to missing value
    repeat with cal in calendars
        if (name of cal as string) is "{cal_name_esc}" then
            set targetCal to cal
            exit repeat
        end if
    end repeat
    if targetCal is missing value then return "[]"

    set evts to (every event of targetCal whose start date ≥ startDate and start date ≤ endDate)
    set evCount to my minVal(count of evts, maxEvts)
    repeat with i from 1 to evCount
        set ev to item i of evts
        set evUID to uid of ev as string

        set attEmails to ""
        try
            repeat with att in attendees of ev
                set attEmail to ""
                try
                    set attEmail to email of att
                on error
                    try
                        set attEmail to my esc(display name of att)
                    end try
                end try
                if attEmail is not "" then
                    set attEmails to attEmails & attEmail & ","
                end if
            end repeat
        end try

        set evOrganizer to ""
        try
            set org to organizer of ev
            if org is not missing value then
                try
                    set evOrganizer to email of org
                on error
                    set evOrganizer to my esc(display name of org)
                end try
            end if
        end try

        set entry to "{{" & ¬
            "\\"uid\\":\\"" & my esc(evUID) & "\\"," & ¬
            "\\"attendees\\":\\"" & attEmails & "\\"," & ¬
            "\\"organizer\\":\\"" & evOrganizer & "\\"" & ¬
            "}}"
        set end of entries to entry
    end repeat
end tell

set AppleScript's text item delimiters to ","
set output to "[" & (entries as string) & "]"
set AppleScript's text item delimiters to ""
return output
"""
)


# ---------------------------------------------------------------------------
# CalendarReader
# ---------------------------------------------------------------------------


class CalendarReader:
    """Reads Apple Calendar events via osascript.

    Splits work into per-calendar AppleScript calls so one slow or
    subscribed calendar cannot block all others.
    """

    # Per-calendar timeout (seconds).  Most calendars finish in < 5 s.
    # Subscribed / large calendars that exceed this are skipped gracefully.
    PER_CAL_TIMEOUT: int = 45
    # Attendee pass gets its own (longer) timeout because it is O(events × attendees).
    ATTENDEES_TIMEOUT: int = 60
    # Default cap: never fetch more than this many events from a single calendar.
    DEFAULT_MAX_EVENTS: int = 300

    def fetch_events(
        self,
        days_back: int = 30,
        days_forward: int = 90,
        calendar_names: Optional[list[str]] = None,
        fetch_attendees: bool = False,
        max_events_per_calendar: int = DEFAULT_MAX_EVENTS,
    ) -> list[CalendarEvent]:
        """Fetch events in [now - days_back, now + days_forward].

        Args:
            days_back: past days to include
            days_forward: future days to include
            calendar_names: allowlist of calendar names.
                            None = all calendars, including read-only (Holidays, Birthdays, etc.).
            fetch_attendees: also fetch attendee emails (slow; adds ~1 s per
                             event with participants). Default False.
            max_events_per_calendar: hard cap per calendar to avoid runaway loops.
        """
        # Step 1 — list all calendars
        try:
            all_cals = self._list_calendars()
        except RuntimeError as e:
            logger.error(f"[calendar] Cannot list calendars: {e}")
            return []

        # Step 2 — filter
        if calendar_names:
            # User explicitly named calendars — fetch even if read-only
            wanted = {n.strip() for n in calendar_names}
            selected = [c for c in all_cals if c["name"] in wanted]
            if not selected:
                logger.warning(
                    f"[calendar] None of the requested calendars found: {calendar_names}. "
                    f"Available: {[c['name'] for c in all_cals]}"
                )
        else:
            # Default: fetch ALL calendars — read-only ones (Holidays, Birthdays)
            # contain useful events.  We never write, only read.
            selected = all_cals

        if not selected:
            logger.warning("[calendar] No calendars to sync")
            return []

        logger.info(
            f"[calendar] Fetching {len(selected)} calendars: "
            f"{[c['name'] for c in selected]}  "
            f"({days_back}d back / {days_forward}d forward, "
            f"max {max_events_per_calendar} events each)"
        )

        # Step 3 — fetch per calendar
        all_events: list[CalendarEvent] = []
        for cal in selected:
            events = self._fetch_one_calendar(
                cal_name=cal["name"],
                days_back=days_back,
                days_forward=days_forward,
                max_events=max_events_per_calendar,
            )
            all_events.extend(events)

        logger.info(f"[calendar] Total events fetched: {len(all_events)}")

        # Step 4 — optional attendees pass
        if fetch_attendees and all_events:
            self._enrich_attendees(
                all_events,
                selected,
                days_back=days_back,
                days_forward=days_forward,
                max_events=max_events_per_calendar,
            )

        return all_events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_calendars(self) -> list[dict]:
        """Return list of {name, writable} for all Calendar.app calendars."""
        raw = run_applescript(_LIST_CALENDARS_SCRIPT, timeout=15)
        result: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||", 1)
            name = parts[0].strip()
            writable = (parts[1].strip().lower() == "true") if len(parts) > 1 else True
            if name:
                result.append({"name": name, "writable": writable})
        return result

    def _esc_name(self, name: str) -> str:
        """Escape calendar name for inline AppleScript string literal."""
        return name.replace("\\", "\\\\").replace('"', '\\"')

    def _fetch_one_calendar(
        self,
        cal_name: str,
        days_back: int,
        days_forward: int,
        max_events: int,
    ) -> list[CalendarEvent]:
        """Fetch events from a single calendar with per-calendar timeout."""
        script = _FETCH_CAL_SCRIPT.format(
            days_back=days_back,
            days_forward=days_forward,
            max_events=max_events,
            cal_name_esc=self._esc_name(cal_name),
        )
        try:
            raw = run_applescript(script, timeout=self.PER_CAL_TIMEOUT)
        except RuntimeError as e:
            err = str(e)
            if "timed out" in err.lower():
                logger.warning(
                    f"[calendar] '{cal_name}' timed out after {self.PER_CAL_TIMEOUT}s "
                    f"— skipping. Try reducing PA_CALENDAR_DAYS_BACK/FORWARD "
                    f"or exclude this calendar with PA_CALENDAR_NAMES."
                )
            elif "1743" in err or "not allowed" in err.lower():
                logger.error(
                    "[calendar] Access denied (error 1743). "
                    "Go to System Settings → Privacy → Automation and allow access to Calendar."
                )
            else:
                logger.warning(f"[calendar] '{cal_name}' error: {e}")
            return []

        events = self._parse(raw, cal_name)
        logger.debug(f"[calendar] '{cal_name}': {len(events)} events")
        return events

    def _enrich_attendees(
        self,
        events: list[CalendarEvent],
        calendars: list[dict],
        days_back: int,
        days_forward: int,
        max_events: int,
    ) -> None:
        """Fetch attendees per calendar and merge into events list (slow pass)."""
        uid_map: dict[str, CalendarEvent] = {ev.uid: ev for ev in events}

        for cal in calendars:
            script = _FETCH_ATTENDEES_SCRIPT.format(
                days_back=days_back,
                days_forward=days_forward,
                max_events=max_events,
                cal_name_esc=self._esc_name(cal["name"]),
            )
            try:
                raw = run_applescript(script, timeout=self.ATTENDEES_TIMEOUT)
            except RuntimeError as e:
                logger.warning(f"[calendar] attendees '{cal['name']}' error: {e}")
                continue

            try:
                rows: list[dict] = json.loads(sanitize_json(raw))
            except Exception:
                continue

            for row in rows:
                uid = row.get("uid", "")
                ev = uid_map.get(uid)
                if ev is None:
                    continue
                att_str = row.get("attendees", "")
                ev.attendees = [
                    a.strip().lower()
                    for a in att_str.split(",")
                    if a.strip() and "@" in a
                ]
                organizer = row.get("organizer", "").strip()
                if organizer:
                    ev.organizer = organizer

        logger.info("[calendar] Attendees enrichment done")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, raw: str, cal_name: str = "") -> list[CalendarEvent]:
        if not raw or raw.strip() == "[]":
            return []
        try:
            data: list[dict] = json.loads(sanitize_json(raw))
        except json.JSONDecodeError as e:
            logger.error(f"[calendar] JSON parse error ({cal_name}): {e}")
            logger.debug(f"Raw (first 300): {raw[:300]}")
            return []

        events: list[CalendarEvent] = []
        for item in data:
            try:
                events.append(self._convert(item))
            except Exception as exc:
                logger.warning(
                    f"[calendar] Skipping event {item.get('uid', '?')!r}: {exc}"
                )
        return events

    def _convert(self, item: dict) -> CalendarEvent:
        def _dt(s: str) -> datetime:
            # AppleScript's isoDate() emits LOCAL wall-clock digits without an
            # offset.  We keep the legacy convention of tagging them as UTC for
            # storage compatibility; the display layer in
            # ``calendar/routes.py``, ``today/routes.py`` and
            # ``daily_brief_service`` deliberately reads digits as wall-clock
            # time and does NOT apply ``.astimezone()`` — see those modules
            # for rationale (events show as scheduled, not shifted by the
            # reader/viewer tz offset).
            if not s:
                return datetime.now(tz=timezone.utc)
            try:
                return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            except ValueError:
                return datetime.now(tz=timezone.utc)

        attendees = [
            a.strip().lower()
            for a in item.get("attendees", "").split(",")
            if a.strip() and "@" in a
        ]

        return CalendarEvent(
            uid=item.get("uid", ""),
            title=item.get("title", "Untitled"),
            start=_dt(item.get("start", "")),
            end=_dt(item.get("end", "")),
            all_day=item.get("all_day", False) in (True, "true"),
            location=safe_str(item.get("location")),
            notes=safe_str(item.get("notes")),
            calendar_name=safe_str(item.get("calendar")),
            attendees=attendees,
            organizer=safe_str(item.get("organizer")),
            url=safe_str(item.get("url")),
        )

    # ------------------------------------------------------------------
    # Contact extraction
    # ------------------------------------------------------------------

    def extract_contacts(self, events: list[CalendarEvent]) -> list[Contact]:
        seen: dict[str, Contact] = {}
        for ev in events:
            emails = list(ev.attendees)
            if ev.organizer:
                emails.append(ev.organizer)
            for email in emails:
                email = email.strip().lower()
                if not email or "@" not in email:
                    continue
                if email not in seen:
                    seen[email] = Contact(email=email, sources=["calendar"])
                elif "calendar" not in seen[email].sources:
                    seen[email].sources.append("calendar")
        return list(seen.values())
