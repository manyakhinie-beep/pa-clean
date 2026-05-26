"""
Timezone helpers — everything is anchored to Europe/Moscow (MSK, UTC+3).

Backend stores datetimes in ISO 8601 with explicit +03:00 offset.
Frontend receives ISO strings and formats them via Intl.DateTimeFormat.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")

# Russian short weekday names (Monday-first, matching datetime.weekday())
_WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_WEEKDAYS_RU_FULL = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def get_now_msk() -> datetime:
    """Current datetime in Europe/Moscow (aware)."""
    return datetime.now(_MSK)


def format_to_msk_iso(dt: Optional[datetime] = None) -> str:
    """ISO 8601 string with explicit +03:00 offset."""
    if dt is None:
        dt = get_now_msk()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_MSK)
    return dt.isoformat()


def format_to_msk_prompt_str(dt: Optional[datetime] = None) -> str:
    """Human-readable string for system prompt.

    Format: «Ср (среда), 2026-05-20 08:38:28 MSK (UTC+3)»
    Explicit weekday prevents the LLM from hallucinating the day of week.
    """
    if dt is None:
        dt = get_now_msk()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_MSK)
    wd_short = _WEEKDAYS_RU[dt.weekday()]
    wd_full = _WEEKDAYS_RU_FULL[dt.weekday()]
    return f"{wd_short} ({wd_full}), {dt.strftime('%Y-%m-%d %H:%M:%S')} MSK (UTC+3)"


def parse_msk_iso(iso: str) -> datetime:
    """Parse an ISO string, forcing Europe/Moscow if naive."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_MSK)
    return dt.astimezone(_MSK)


def parse_relative_to_msk(expression: str, anchor: Optional[date] = None) -> dict:
    """
    Wrapper around date_calc that guarantees MSK anchor and +03:00 ISO output.
    Returns {"iso": "YYYY-MM-DD+03:00", "human": "..."}.
    """
    from personal_assistant.mlx_server.tools.date_calc import parse_relative_date

    anchor = anchor or get_now_msk().date()
    result = parse_relative_date(expression, anchor=anchor)
    if result is None:
        return {"error": f"Не удалось распознать дату: {expression!r}"}
    d = date.fromisoformat(result["iso"])
    # Attach MSK offset for unambiguous ISO
    dt_msk = datetime(d.year, d.month, d.day, tzinfo=_MSK)
    return {
        "iso": dt_msk.isoformat(),
        "human": result["human"],
    }


