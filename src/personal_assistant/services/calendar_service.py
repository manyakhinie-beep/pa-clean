"""
calendar_service.py — Service stubs for Apple Calendar integration.

Provides high-level operations on top of the calendar_reader AppleScript
reader and vault writer.

Current stubs (ready for full implementation):
  - create_meeting_draft    — open a pre-filled meeting invitation draft
  - create_event_draft      — open a pre-filled calendar event in Calendar.app
  - fetch_upcoming_events   — return upcoming vault .md events (from vault)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

# ---------------------------------------------------------------------------
# create_meeting_draft
# ---------------------------------------------------------------------------


def create_meeting_draft(
    title: str,
    start_dt: datetime,
    end_dt: Optional[datetime] = None,
    location: str = "",
    notes: str = "",
    attendees: Optional[list[str]] = None,
    calendar_name: str = "",
) -> dict:
    """Open a pre-filled meeting invitation in Apple Calendar via AppleScript.

    Creates a new event in Calendar.app and opens its edit window so the user
    can review and send invitations.

    Args:
        title:         Event / meeting title.
        start_dt:      Start datetime (timezone-aware recommended).
        end_dt:        End datetime; defaults to start_dt + 1 hour.
        location:      Location string (optional).
        notes:         Meeting agenda / body text (optional).
        attendees:     List of attendee email addresses (optional).
        calendar_name: Target calendar name; if empty, uses the default calendar.

    Returns:
        ``{"ok": True, "message": "..."}`` on success.

    Raises:
        RuntimeError: if osascript fails or platform is not macOS.
    """
    import platform

    if platform.system() != "Darwin":
        raise RuntimeError("Calendar draft creation is only supported on macOS")

    if end_dt is None:
        end_dt = start_dt + timedelta(hours=1)

    from personal_assistant.readers.applescript_base import run_applescript

    def _esc(s: str) -> str:
        s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
        s = " ".join(s.split())
        return s.replace("\\", "\\\\").replace('"', '" & quote & "')

    esc_title = _esc(title)
    esc_location = _esc(location)
    esc_notes = _esc(notes[:2000])  # cap notes length for AppleScript safety

    cal_clause = (
        f'set targetCal to first calendar whose name = "{_esc(calendar_name)}"'
        if calendar_name
        else 'set targetCal to default calendar'
    )

    # Build attendees block
    attendee_lines = ""
    if attendees:
        lines = []
        for addr in attendees[:20]:
            lines.append(
                f'        make new attendee at end of attendees of newEvent '
                f'with properties {{email:"{_esc(addr)}"}}'
            )
        attendee_lines = "\n".join(lines)

    # Use numeric date property assignment to avoid locale-sensitive date string
    # parsing (error -2741 on non-English macOS).  Matches calendar_writer.py approach.
    def _dt_block(var: str, dt: datetime) -> str:
        return (
            f'    set {var} to (current date)\n'
            f'    set year of {var} to {dt.year}\n'
            f'    set month of {var} to {dt.month}\n'
            f'    set day of {var} to {dt.day}\n'
            f'    set hours of {var} to {dt.hour}\n'
            f'    set minutes of {var} to {dt.minute}\n'
            f'    set seconds of {var} to {dt.second}'
        )

    start_block = _dt_block("startDate", start_dt)
    end_block   = _dt_block("endDate",   end_dt)

    script = f"""\
tell application "Calendar"
    {cal_clause}
{start_block}
{end_block}
    set newEvent to make new event at end of events of targetCal with properties {{
        summary: "{esc_title}",
        start date: startDate,
        end date: endDate,
        location: "{esc_location}",
        description: "{esc_notes}"
    }}
{attendee_lines}
    show newEvent
end tell
"""

    try:
        run_applescript(script, timeout=30)
        logger.info(f"[calendar_service] Meeting draft opened: {title!r}")
        return {"ok": True, "message": f"Событие «{title}» создано в Календаре"}
    except Exception as exc:
        logger.error(f"[calendar_service] create_meeting_draft failed: {exc}")
        raise RuntimeError(f"Failed to create meeting draft: {exc}") from exc


# ---------------------------------------------------------------------------
# create_event_draft
# ---------------------------------------------------------------------------


def create_event_draft(
    title: str,
    start_dt: datetime,
    end_dt: Optional[datetime] = None,
    all_day: bool = False,
    location: str = "",
    notes: str = "",
    calendar_name: str = "",
) -> dict:
    """Create a calendar event (no attendees / no invitation).

    Simpler variant of :func:`create_meeting_draft` for personal reminders
    and appointments that don't require inviting other people.

    Args:
        title:         Event title.
        start_dt:      Start datetime.
        end_dt:        End datetime; defaults to start_dt + 1 hour.
        all_day:       Whether to create an all-day event.
        location:      Location string (optional).
        notes:         Event notes / description (optional).
        calendar_name: Target calendar; uses default if empty.

    Returns:
        ``{"ok": True, "message": "..."}`` on success.
    """
    return create_meeting_draft(
        title=title,
        start_dt=start_dt,
        end_dt=end_dt,
        location=location,
        notes=notes,
        attendees=None,
        calendar_name=calendar_name,
    )


# ---------------------------------------------------------------------------
# fetch_upcoming_events
# ---------------------------------------------------------------------------


def fetch_upcoming_events(
    days_forward: int = 7,
    vault_path: Optional[Path] = None,
) -> list[dict]:
    """Return upcoming calendar events from the vault.

    Scans vault/calendar/**/*.md and returns events whose ``date`` frontmatter
    field falls within ``now … now + days_forward`` days.

    Args:
        days_forward: Number of days ahead to look.
        vault_path:   Vault root; defaults to settings.vault_path.

    Returns:
        List of dicts with keys: ``path``, ``title``, ``date``, ``location``,
        ``attendees``, ``body_snippet``.  Sorted by ``date`` ascending.
    """
    from personal_assistant.config import settings

    root = vault_path or settings.vault_path
    cal_path = root / "calendar"
    if not cal_path.exists():
        return []

    # TZ-aware: vault md files store ISO-with-offset (e.g. ``+00:00``).
    # Comparing against ``datetime.now()`` (naive local) drops the event when
    # the local-time offset crosses a day boundary. Use UTC throughout.
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_forward)

    results: list[dict] = []
    for md_file in cal_path.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            # Calendar md frontmatter uses ``start`` (mirrors vCalendar field);
            # some legacy entries may have ``date``. Accept either.
            date_str = str(fm.get("date") or fm.get("start") or "")
            if not date_str:
                continue
            # Parse ISO date / datetime — assume UTC when offset missing.
            try:
                event_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if event_dt.tzinfo is None:
                    event_dt = event_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if not (now <= event_dt <= cutoff):
                continue
            body_start = text.find("\n---\n", text.find("---") + 3)
            body = text[body_start + 5:].strip() if body_start != -1 else ""
            results.append({
                "path": str(md_file),
                "title": fm.get("title", fm.get("subject", md_file.stem)),
                "date": date_str,
                "end": fm.get("end", ""),
                "location": fm.get("location", ""),
                "attendees": fm.get("attendees", []),
                "body_snippet": body[:300],
            })
        except Exception:
            continue

    results.sort(key=lambda r: str(r["date"]))
    return results


# ---------------------------------------------------------------------------
# Conflict detection (free/busy)
# ---------------------------------------------------------------------------


def _to_naive(iso: str) -> datetime:
    """Parse an ISO datetime string to a naive (tz-stripped) datetime."""
    dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


def find_conflicts(start_iso: str, end_iso: str, events: list[dict]) -> list[dict]:
    """Return the subset of *events* that overlap the ``[start, end)`` interval.

    Pure and side-effect free, so it is unit-testable without Calendar.app.
    Each event dict should carry ``start`` (or ``date``) and ``end`` ISO
    datetime strings; events without a valid end time are skipped. Overlap is
    half-open — a meeting that ends exactly when another starts does NOT
    conflict.

    Args:
        start_iso: proposed event start (ISO datetime).
        end_iso:   proposed event end (ISO datetime).
        events:    existing events, e.g. from :func:`fetch_upcoming_events`.

    Returns:
        Events that overlap, in input order.
    """
    try:
        new_start = _to_naive(start_iso)
        new_end = _to_naive(end_iso)
    except (ValueError, TypeError):
        return []
    if new_end <= new_start:
        return []

    conflicts: list[dict] = []
    for ev in events:
        raw_start = ev.get("start") or ev.get("date") or ""
        raw_end = ev.get("end") or ""
        if not raw_end:
            continue
        try:
            ev_start = _to_naive(raw_start)
            ev_end = _to_naive(raw_end)
        except (ValueError, TypeError):
            continue
        if ev_end <= ev_start:
            continue
        if ev_start < new_end and new_start < ev_end:
            conflicts.append(ev)
    return conflicts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between first pair of ``---`` delimiters."""
    import yaml  # noqa: PLC0415

    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    try:
        return yaml.safe_load(fm_text) or {}
    except Exception:
        return {}
