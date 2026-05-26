"""
Calendar API — /api/v1/calendar

Stage 5: Smart Meeting Prep
  GET  /api/v1/calendar/upcoming          — list of upcoming vault events (today + N days)
  GET  /api/v1/calendar/{event_id}/prep   — full meeting prep context for one event

Stage 7: Calendar Intent NLP
  POST /api/v1/calendar/parse-intent      — parse NL text → EventDraft preview (no creation)
  POST /api/v1/calendar/create-from-text  — parse + create event via AppleScript
  GET  /api/v1/calendar/calendars         — list writable calendars
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UPCOMING_DAYS = 7   # how many days ahead to show


def _get_vault_path() -> Optional[Path]:
    try:
        from personal_assistant.config import settings
        p = settings.vault_path
        return p if p and p.exists() else None
    except Exception:
        return None


def _get_my_email() -> str:
    try:
        from personal_assistant.config import settings
        return str(settings.user_email or "")
    except Exception:
        return ""


def _get_mlx_engine():
    try:
        from personal_assistant.mlx_server.server import state
        return state.engine  # may be None
    except Exception:
        return None


def _parse_iso(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fmt_relative(dt: datetime) -> str:
    """Return human-readable relative time: 'сегодня 14:00', 'завтра 10:30', ..."""
    now = datetime.now(timezone.utc).astimezone()
    local_dt = dt.astimezone()
    delta = (local_dt.date() - now.date()).days
    time_str = local_dt.strftime("%H:%M")
    if delta == 0:
        return f"сегодня {time_str}"
    if delta == 1:
        return f"завтра {time_str}"
    if delta < 0:
        return f"{abs(delta)} дн. назад"
    days_ru = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    return f"{days_ru[local_dt.weekday()]} {time_str}"


def _scan_upcoming_events(
    vault_path: Path,
    days_ahead: int = _UPCOMING_DAYS,
) -> list[dict]:
    """Scan vault/calendar/**/*.md for events in the next N days."""
    results: list[dict] = []
    if not vault_path:
        return results
    calendar_dir = vault_path / "calendar"
    if not calendar_dir.exists():
        return results

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    for md_path in sorted(calendar_dir.rglob("*.md")):
        try:
            raw = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Quick check for date field
        if "date:" not in raw and "start:" not in raw:
            continue

        try:
            import unicodedata

            from personal_assistant.utils.frontmatter import parse_lenient

            # Tolerant parse — falls back to repair if legacy vault files
            # have run-on YAML from the old event.md.j2 template.
            fm = parse_lenient(raw)
            if not fm:
                continue

            date_str = str(fm.get("date") or fm.get("start") or "")
            dt = _parse_iso(date_str)
            if dt is None:
                continue
            if not (now <= dt <= cutoff):
                continue

            doc_id = str(fm.get("id") or md_path.stem).strip()
            title = unicodedata.normalize("NFC", str(
                fm.get("title") or fm.get("subject") or md_path.stem
            ).strip())
            participants: list = []
            for field in ("attendees", "participants", "contacts"):
                v = fm.get(field)
                if isinstance(v, list):
                    participants.extend(str(x) for x in v if x)
                elif isinstance(v, str) and v.strip():
                    participants.extend(p.strip() for p in v.split(",") if p.strip())

            results.append({
                "id": doc_id,
                "title": title,
                "date": date_str,
                "relative": _fmt_relative(dt),
                "location": str(fm.get("location") or ""),
                "participants": participants[:10],
                "participant_count": len(participants),
            })
        except Exception as exc:
            logger.debug(f"[calendar] parse error {md_path}: {exc}")
            continue

    results.sort(key=lambda r: r["date"])
    return results


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/upcoming")
def get_upcoming_events(days: int = _UPCOMING_DAYS):
    """
    List vault calendar events scheduled within the next N days.

    Returns:
        {
            events: [{ id, title, date, relative, location, participants, participant_count }],
            count:  int,
            days_ahead: int
        }
    """
    days = max(1, min(days, 90))
    vault_path = _get_vault_path()
    events = _scan_upcoming_events(vault_path, days_ahead=days) if vault_path else []
    return {
        "events": events,
        "count": len(events),
        "days_ahead": days,
    }


@router.get("/{event_id}/prep")
def get_meeting_prep(event_id: str):
    """
    Build full meeting preparation context for a single event.

    Scans the vault for:
    - Recent emails from participants (last 7 days)
    - Related projects mentioning participants
    - Previous meetings with overlapping attendees
    - Open action items from correspondence

    Produces a prep_brief (rule-based or MLX-generated) and a
    ready-to-use context_prompt for /api/chat/send.

    Graceful degradation:
    - Returns minimal context when vault is not loaded.
    - Works without MLX (rule-based brief fallback).

    Returns:
        {
            event_id, title, participants, participant_emails, event_date,
            location, recent_emails, related_projects, previous_meetings,
            open_action_items, prep_brief, context_prompt,
            event_found, message_count
        }
    """
    from personal_assistant.services.meeting_prep_service import build_meeting_prep

    vault_path = _get_vault_path()
    my_email = _get_my_email()
    mlx_engine = _get_mlx_engine()

    try:
        ctx = build_meeting_prep(
            event_id=event_id,
            vault_path=vault_path,
            my_email=my_email,
            mlx_engine=mlx_engine,
        )
    except Exception as exc:
        logger.warning(f"[calendar] prep failed for {event_id!r}: {exc}")
        ctx = {
            "event_id": event_id,
            "title": "Без названия",
            "participants": [],
            "participant_emails": [],
            "event_date": "",
            "location": "",
            "recent_emails": [],
            "related_projects": [],
            "previous_meetings": [],
            "open_action_items": [],
            "prep_brief": "Контекст недоступен.",
            "context_prompt": (
                f"Помоги подготовиться к встрече (id: {event_id}). "
                "Расскажи, что стоит учесть и какие вопросы задать."
            ),
            "event_found": False,
            "message_count": 0,
        }

    return ctx


# ---------------------------------------------------------------------------
# Stage 7: Calendar Intent NLP
# ---------------------------------------------------------------------------


class ParseIntentRequest(BaseModel):
    text: str
    reference_date: Optional[str] = None   # YYYY-MM-DD, for testing


class CreateFromTextRequest(BaseModel):
    text: str
    reference_date: Optional[str] = None
    dry_run: bool = False               # preview without creating
    confirmed: bool = False             # must be True to actually create
    calendar_name: Optional[str] = None # override auto-detected calendar


@router.post("/parse-intent")
def parse_calendar_intent(body: ParseIntentRequest):
    """
    Parse natural language text into a structured EventDraft.

    No event is created — this is a preview/validation step.

    Examples:
      "Встреча с Ивановым в следующий четверг в 15:00"
      "Созвон по проекту во вторник утром на час"
      "Блокировать время для отчёта в пятницу 14-16"

    Returns:
        {
            draft: { title, date_iso, time_str, duration_minutes,
                     participants, location, calendar_name,
                     start_iso, end_iso, confidence, warnings },
            preview_text: str,   — human-readable summary
        }
    """
    from datetime import date as _date

    from personal_assistant.calendar.intent_parser import parse_event_intent

    text = (body.text or "").strip()
    if not text:
        return {
            "draft": None,
            "preview_text": "Введите описание события",
            "error": "empty_text",
        }

    ref_date: Optional[_date] = None
    if body.reference_date:
        try:
            ref_date = _date.fromisoformat(body.reference_date)
        except ValueError:
            pass

    mlx_engine = _get_mlx_engine()

    try:
        draft = parse_event_intent(text, mlx_engine=mlx_engine, reference_date=ref_date)
    except Exception as exc:
        logger.warning(f"[calendar] parse_intent error: {exc}")
        return {"draft": None, "preview_text": "Ошибка парсинга", "error": str(exc)}

    # Build human-readable preview
    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    try:
        from datetime import date as _date2
        d = _date2.fromisoformat(draft.date_iso)
        wd = days_ru[d.weekday()]
        date_str = f"{wd}, {d.day:02d}.{d.month:02d}.{d.year}"
    except Exception:
        date_str = draft.date_iso

    parts = [f"📅 {date_str} в {draft.time_str}"]
    parts.append(f"⏱ {draft.duration_minutes} мин")
    if draft.location:
        parts.append(f"📍 {draft.location}")
    if draft.participants:
        parts.append(f"👥 {', '.join(draft.participants)}")
    if draft.calendar_name:
        parts.append(f"🗓 Календарь: {draft.calendar_name}")
    else:
        parts.append("🗓 Календарь: не указан")

    preview_text = f"**{draft.title}**\n" + "  ".join(parts)
    if draft.warnings:
        preview_text += "\n⚠️ " + "; ".join(draft.warnings)

    result: dict[str, object] = {
        "draft": draft.to_dict(),
        "preview_text": preview_text,
    }

    # If calendar is not detected, ask user to choose
    if not draft.calendar_name:
        from personal_assistant.calendar.calendar_writer import list_calendars
        try:
            available = list_calendars()
        except Exception:
            available = []
        result["needs_calendar"] = True
        result["available_calendars"] = available

    return result


@router.post("/create-from-text")
def create_event_from_text(body: CreateFromTextRequest):
    """
    Parse natural language text and optionally create the event in Calendar.app.

    Two-step workflow:
      1. First call with confirmed=False (or omit) → returns preview draft
      2. Second call with confirmed=True → creates the event via AppleScript

    With dry_run=True: builds AppleScript but does NOT execute (safe for CI/testing).

    Returns:
        {
            draft: {...},
            preview_text: str,
            created: bool,
            event_uid: str | None,
            error: str | None,
        }
    """
    from datetime import date as _date

    from personal_assistant.calendar.calendar_writer import CalendarWriteError, create_event
    from personal_assistant.calendar.intent_parser import parse_event_intent

    text = (body.text or "").strip()
    if not text:
        return {
            "draft": None,
            "preview_text": "Введите описание события",
            "created": False,
            "event_uid": None,
            "error": "empty_text",
        }

    ref_date: Optional[_date] = None
    if body.reference_date:
        try:
            ref_date = _date.fromisoformat(body.reference_date)
        except ValueError:
            pass

    mlx_engine = _get_mlx_engine()

    try:
        draft = parse_event_intent(text, mlx_engine=mlx_engine, reference_date=ref_date)
    except Exception as exc:
        logger.warning(f"[calendar] create_from_text parse error: {exc}")
        return {
            "draft": None,
            "preview_text": "Ошибка парсинга",
            "created": False,
            "event_uid": None,
            "error": str(exc),
        }

    # Apply user override for calendar name
    if body.calendar_name:
        draft.calendar_name = body.calendar_name

    # Build preview text (reuse parse_intent logic)
    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    try:
        from datetime import date as _date2
        d = _date2.fromisoformat(draft.date_iso)
        wd = days_ru[d.weekday()]
        date_str = f"{wd}, {d.day:02d}.{d.month:02d}.{d.year}"
    except Exception:
        date_str = draft.date_iso

    parts = [f"📅 {date_str} в {draft.time_str}"]
    parts.append(f"⏱ {draft.duration_minutes} мин")
    if draft.location:
        parts.append(f"📍 {draft.location}")
    if draft.participants:
        parts.append(f"👥 {', '.join(draft.participants)}")
    if draft.calendar_name:
        parts.append(f"🗓 Календарь: {draft.calendar_name}")
    else:
        parts.append("🗓 Календарь: не указан")

    preview_text = f"**{draft.title}**\n" + "  ".join(parts)
    if draft.warnings:
        preview_text += "\n⚠️ " + "; ".join(draft.warnings)

    # Optional conflict check (gated by calendar_check_conflicts). Non-blocking:
    # any failure here must never prevent the user from creating their event.
    from personal_assistant.config import settings as _cfg
    if _cfg.calendar_check_conflicts:
        try:
            from personal_assistant.services.calendar_service import (
                fetch_upcoming_events,
                find_conflicts,
            )
            upcoming = fetch_upcoming_events(days_forward=_cfg.calendar_days_forward)
            conflicts = find_conflicts(draft.start_iso, draft.end_iso, upcoming)
            if conflicts:
                titles = ", ".join(c.get("title", "?") for c in conflicts[:5])
                preview_text += f"\n⛔ Возможный конфликт с: {titles}"
        except Exception as exc:  # noqa: BLE001 — conflict check is best-effort
            logger.debug(f"[calendar] conflict check skipped: {exc}")

    # If calendar not chosen, ask user before creating
    # (dry_run skips this check — it just generates AppleScript, no real calendar needed)
    if not draft.calendar_name and not body.dry_run:
        from personal_assistant.calendar.calendar_writer import list_calendars
        try:
            available = list_calendars()
        except Exception:
            available = []
        return {
            "draft": draft.to_dict(),
            "preview_text": preview_text,
            "created": False,
            "event_uid": None,
            "error": None,
            "needs_calendar": True,
            "available_calendars": available,
        }

    # If not confirmed, return preview only
    if not body.confirmed and not body.dry_run:
        return {
            "draft": draft.to_dict(),
            "preview_text": preview_text,
            "created": False,
            "event_uid": None,
            "error": None,
        }

    # Create (or dry_run)
    try:
        write_result = create_event(draft, dry_run=body.dry_run)
    except CalendarWriteError as exc:
        return {
            "draft": draft.to_dict(),
            "preview_text": preview_text,
            "created": False,
            "event_uid": None,
            "error": str(exc),
        }

    return {
        "draft": draft.to_dict(),
        "preview_text": preview_text,
        "created": write_result["success"],
        "event_uid": write_result.get("event_uid"),
        "error": write_result.get("error"),
    }


@router.get("/calendars")
def get_calendars():
    """
    List ALL calendars from Calendar.app (writable and read-only).

    Returns:
        { calendars: [str], count: int }
    """
    from personal_assistant.calendar.calendar_writer import list_calendars

    try:
        names = list_calendars()
    except Exception as exc:
        logger.warning(f"[calendar] list_calendars error: {exc}")
        names = []

    return {"calendars": names, "count": len(names)}
