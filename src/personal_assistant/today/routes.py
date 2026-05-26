"""
Today API — /api/v1/today

Агрегирует данные для вкладки «Сегодня»:
  GET /api/v1/today  — события дня, срочные письма, предложения ассистента, сводка
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from loguru import logger

router = APIRouter(prefix="/api/v1/today", tags=["today"])

# ---------------------------------------------------------------------------
# Helpers shared with inbox (duplicated to avoid circular imports)
# ---------------------------------------------------------------------------

_TAG_URGENT  = {"срочно", "urgency:critical", "urgency:high", "urgency:urgent", "urgent"}
_TAG_IMPORT  = {"важно", "important", "urgency:medium", "urgency:important",
                "category:finance", "category:legal", "finance", "finances", "финансы"}
_TAG_MEETING = {"meeting", "встреча", "calendar", "событие", "category:meetings"}

_AVATAR_COLORS = [
    "#4F6AF5", "#7C3AED", "#DB2777", "#059669",
    "#D97706", "#0284C7", "#DC2626", "#65A30D",
]


def _avatar_color(name: str) -> str:
    idx = int(hashlib.md5(name.encode(), usedforsecurity=False).hexdigest(), 16) % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


def _initials(name: str) -> str:
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _fmt_time(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now.date() - dt.date()
        if delta.days == 0:
            return dt.strftime("%H:%M")
        if delta.days == 1:
            return "вчера"
        _days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        return _days[dt.weekday()] if delta.days <= 6 else dt.strftime("%d.%m")
    except Exception:
        return str(date_str)[:5]


def _is_today(date_str: Optional[str]) -> bool:
    if not date_str:
        return False
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_now = datetime.now(timezone.utc).astimezone()
        local_dt  = dt.astimezone()
        return local_dt.date() == local_now.date()
    except Exception:
        return False


def _parse_sender(sender_raw: str) -> tuple[str, str]:
    name = re.sub(r"\s*<[^>]+>", "", sender_raw).strip()
    for sep in (" · ", " — ", " - ", ", "):
        if sep in name:
            parts = name.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return name, ""


def _get_index():
    try:
        from personal_assistant.mlx_server import server as _srv
        return getattr(_srv.state, "index", None)
    except Exception:
        return None


def _get_profile_name() -> str:
    try:
        from personal_assistant.profile.service import load_profile
        p = load_profile()
        return (p.full_name or "").split()[0] or "Игорь"
    except Exception:
        return "Игорь"


# ---------------------------------------------------------------------------
# Greeting
# ---------------------------------------------------------------------------

def _greeting(first_name: str) -> str:
    hour = datetime.now().hour
    if hour < 12:
        return f"Доброе утро, {first_name}"
    if hour < 17:
        return f"Добрый день, {first_name}"
    return f"Добрый вечер, {first_name}"


# ---------------------------------------------------------------------------
# Next update time (every 2 hours, on the hour)
# ---------------------------------------------------------------------------

def _next_update_label() -> str:
    now = datetime.now()
    next_hour = (now.hour // 2 + 1) * 2
    if next_hour >= 24:
        return "00:00"
    return f"{next_hour:02d}:00"


# ---------------------------------------------------------------------------
# Build today data from vault index
# ---------------------------------------------------------------------------

def _build_today_data(idx) -> dict:
    docs = list(idx.docs)
    today_docs = [d for d in docs if d.section in ("mail", "calendar")]

    # ── Calendar events for today ────────────────────────────────────────────
    calendar_docs = [d for d in today_docs if d.section == "calendar"]
    today_events_raw = [d for d in calendar_docs if _is_today(d.date)]

    # If no today-dated events, take the most recent 6 calendar docs as fallback
    if not today_events_raw:
        calendar_docs.sort(key=lambda d: str(d.date or ""), reverse=True)
        today_events_raw = calendar_docs[:6]
    else:
        today_events_raw.sort(key=lambda d: str(d.date or ""))

    def _event_from_doc(doc) -> dict:
        fm = doc.frontmatter
        subject = str(fm.get("subject") or fm.get("title") or doc.path.stem).strip()
        tags_raw = doc.tags
        is_urgent = any(t.lower() in _TAG_URGENT for t in tags_raw)
        is_focus = any(t.lower() in {"focus", "фокус", "deep-work"} for t in tags_raw)

        # attendees from frontmatter
        attendees_raw = fm.get("attendees") or fm.get("participants") or ""
        if isinstance(attendees_raw, list):
            attendees = [str(a).split("<")[0].strip() for a in attendees_raw][:3]
        elif isinstance(attendees_raw, str) and attendees_raw:
            attendees = [a.strip() for a in re.split(r"[,;]", attendees_raw)][:3]
        else:
            attendees = []

        has_brief = any(t.lower() in {"brief", "бриф", "бриф готов"} for t in tags_raw)
        location  = str(fm.get("location") or fm.get("url") or "").strip()

        time_label = _fmt_time(doc.date) if doc.date else ""

        # Determine status dot
        now = datetime.now(timezone.utc)
        status = "past"
        try:
            if doc.date:
                dt = datetime.fromisoformat(str(doc.date).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                diff = (dt - now).total_seconds()
                if diff > 3600:
                    status = "upcoming"
                elif diff > -3600:
                    status = "active"
                else:
                    status = "past"
        except Exception:
            pass

        return {
            "id": str(fm.get("id") or doc.path.stem),
            "title": subject,
            "time": time_label,
            "status": status,       # "active" | "upcoming" | "past"
            "attendees": attendees,
            "location": location,
            "description": str(fm.get("description") or "").strip()[:80],
            "is_urgent": is_urgent,
            "is_focus": is_focus,
            "has_brief": has_brief,
            "tags_raw": tags_raw,
            "path": str(doc.path),
        }

    events = [_event_from_doc(d) for d in today_events_raw[:8]]

    # ── Mail docs sorted by date desc ─────────────────────────────────────────
    mail_docs = [d for d in today_docs if d.section == "mail"]
    mail_docs.sort(key=lambda d: str(d.date or ""), reverse=True)

    def _attention_from_doc(doc) -> dict:
        fm = doc.frontmatter
        sender_raw = str(fm.get("sender_name") or fm.get("sender") or fm.get("from") or "").strip() or "Неизвестный"
        sender_name, sender_role = _parse_sender(sender_raw)
        subject = str(fm.get("subject") or fm.get("title") or doc.path.stem).strip()
        tags_raw = doc.tags
        preview = doc.ui_preview(120)
        return {
            "id": str(fm.get("id") or doc.path.stem),
            "sender_name": sender_name,
            "sender_role": sender_role,
            "sender_initials": _initials(sender_name),
            "sender_color": _avatar_color(sender_name),
            "subject": subject,
            "time_label": _fmt_time(doc.date),
            "preview": preview,
            "tags_raw": tags_raw,
            "is_urgent": any(t.lower() in _TAG_URGENT for t in tags_raw),
            "path": str(doc.path),
        }

    # Attention items: urgent first, then important, then latest
    urgent_mail  = [d for d in mail_docs if any(t.lower() in _TAG_URGENT for t in d.tags)]
    import_mail  = [d for d in mail_docs if any(t.lower() in _TAG_IMPORT for t in d.tags)
                    and d not in urgent_mail]
    attention_docs = (urgent_mail + import_mail)[:3]
    if len(attention_docs) < 3:
        rest = [d for d in mail_docs if d not in attention_docs]
        attention_docs += rest[:3 - len(attention_docs)]

    attention = [_attention_from_doc(d) for d in attention_docs]

    total_mail   = len(mail_docs)
    urgent_count = len(urgent_mail)

    # ── Summary bullets ───────────────────────────────────────────────────────
    bullets: list[str] = []

    # Events bullet
    ev_count = len(events)
    if ev_count > 0:
        key_event = next((e for e in events if e["status"] in ("active", "upcoming")), events[0])
        bullets.append(
            f"{ev_count} {'встреча' if ev_count == 1 else 'встречи' if ev_count in (2,3,4) else 'встреч'} "
            f"сегодня — ключевая «{key_event['title']}»"
            + (f" в {key_event['time']}" if key_event["time"] else "")
        )
    else:
        bullets.append("Встреч сегодня нет — хороший день для сосредоточенной работы")

    # Mail bullet
    if total_mail > 0:
        if urgent_count > 0:
            bullets.append(
                f"{total_mail} {'письмо' if total_mail == 1 else 'писем'}, "
                f"<b>{urgent_count} срочных</b>"
                + (" в Inbox" if urgent_count > 0 else "")
            )
        else:
            bullets.append(f"{total_mail} {'новое письмо' if total_mail == 1 else 'писем'} в Inbox")
    else:
        bullets.append("Входящих писем нет")

    # Focus slot bullet
    focus_events = [e for e in events if e["is_focus"]]
    if focus_events:
        fe = focus_events[0]
        bullets.append(f"Фокус-слот {fe['time']} — {fe['title']}")

    # ── Assistant suggestions ─────────────────────────────────────────────────
    suggestions: list[dict] = []

    # Suggestion 1: reply to most urgent mail
    if urgent_mail:
        doc = urgent_mail[0]
        fm  = doc.frontmatter
        sender_raw = str(fm.get("sender_name") or fm.get("sender") or fm.get("from") or "").strip()
        sender_name, sender_role = _parse_sender(sender_raw)
        subject = str(fm.get("subject") or fm.get("title") or doc.path.stem).strip()
        thread_count = int(fm.get("thread_count") or 0)
        label = f"Подготовить ответ {sender_name.split()[0] if sender_name else 'отправителю'}"
        detail = f"draft на основе треда «{subject[:40]}»"
        if thread_count > 1:
            detail += f" — {thread_count} писем"
        suggestions.append({
            "icon": "draft",
            "label": label,
            "detail": detail,
            "action": "draft",
            "path": str(doc.path),
            "message": f"/draft {subject}",
        })

    # Suggestion 2: upcoming event brief
    upcoming_events = [e for e in events if e["status"] == "upcoming"]
    if upcoming_events:
        ev = upcoming_events[0]
        suggestions.append({
            "icon": "brief",
            "label": f"Бриф к «{ev['title'][:35]}»",
            "detail": "собрать контекст из писем и прошлых встреч",
            "action": "summarize",
            "path": ev["path"],
            "message": f"/summarize {ev['title']}",
        })

    # Suggestion 3: focus slot
    if focus_events:
        fe = focus_events[0]
        suggestions.append({
            "icon": "focus",
            "label": "Забронировать фокус-слот",
            "detail": f"{fe['time']} — {fe['title']}",
            "action": "chat",
            "path": fe["path"],
            "message": f"Запланируй фокус-работу на {fe['time']}",
        })

    # Fill to 3 suggestions from important mail
    if len(suggestions) < 3 and import_mail:
        doc = import_mail[0]
        fm  = doc.frontmatter
        subject = str(fm.get("subject") or fm.get("title") or doc.path.stem).strip()
        suggestions.append({
            "icon": "summarize",
            "label": "Суммаризировать переписку",
            "detail": f"«{subject[:45]}»",
            "action": "summarize",
            "path": str(doc.path),
            "message": f"/summarize {subject}",
        })

    suggestions = suggestions[:3]

    now_str = datetime.now().strftime("%H:%M")

    return {
        "greeting": _greeting(_get_profile_name()),
        "updated_at": now_str,
        "next_update": _next_update_label(),
        "bullets": bullets,
        "events": events,
        "events_total": ev_count,
        "attention": attention,
        "attention_total": total_mail,
        "urgent_count": urgent_count,
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("")
def get_today():
    """Return aggregated today dashboard data."""
    idx = _get_index()
    if idx is None:
        # Return empty structure so frontend can render gracefully
        name = _get_profile_name()
        return {
            "greeting": _greeting(name),
            "updated_at": datetime.now().strftime("%H:%M"),
            "next_update": _next_update_label(),
            "bullets": ["Vault не загружен — запустите синхронизацию"],
            "events": [],
            "events_total": 0,
            "attention": [],
            "attention_total": 0,
            "urgent_count": 0,
            "suggestions": [],
        }

    try:
        return _build_today_data(idx)
    except Exception as exc:
        logger.exception(f"[today] error building dashboard: {exc}")
        return {
            "greeting": _greeting(_get_profile_name()),
            "updated_at": datetime.now().strftime("%H:%M"),
            "next_update": _next_update_label(),
            "bullets": [f"Ошибка загрузки: {exc}"],
            "events": [],
            "events_total": 0,
            "attention": [],
            "attention_total": 0,
            "urgent_count": 0,
            "suggestions": [],
        }
