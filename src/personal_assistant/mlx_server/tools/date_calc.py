"""
Тул для расчёта абсолютных дат из относительных выражений.

Поддерживаемые формы (русский + английский):
  "через 3 дня", "через неделю", "через 2 недели", "через год", "через 2 года"
  "сегодня"/"today", "завтра"/"tomorrow", "послезавтра"/"day after tomorrow",
  "вчера"/"yesterday"
  "следующий понедельник", "следующая среда"
  "в понедельник", "в пятницу", "в среду" (ближайший)
  "на этой неделе", "эта неделя"           → понедельник текущей недели
  "на следующей неделе", "следующая неделя" → понедельник следующей недели
  "в конце недели"                          → пятница текущей недели
  "в начале [следующей] недели"             → понедельник [следующей] недели
  "в следующем месяце", "через месяц"
  "2025-12-31" — уже абсолютная, возвращаем как есть
  "16.05.2026", "16.05.2026 10:00" — российский формат

Возвращает ISO 8601 (YYYY-MM-DD) и human-readable строку.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Optional

from loguru import logger

# Russian weekday names → iso weekday (1=Monday … 7=Sunday)
_WD_RU = {
    "понедельник": 1,
    "вторник": 2,
    "среда": 3,
    "четверг": 4,
    "пятница": 5,
    "суббота": 6,
    "воскресенье": 7,
}

# Accusative / prepositional forms used after "в/во" (в среду, в пятницу…)
_WD_ACC_RU = {
    "понедельник": 1,
    "вторник": 2,
    "среду": 3,        # среда → среду
    "четверг": 4,
    "пятницу": 5,      # пятница → пятницу
    "субботу": 6,      # суббота → субботу
    "воскресенье": 7,
}

# All forms combined for regex alternation
_ALL_WD = {**_WD_RU, **_WD_ACC_RU}

_REL_DAY_RE = re.compile(
    r"^(?:"
    r"через\s+(\d+)\s+(?:дн(?:я|ей)|день)"  # через N дней
    r"|сегодня|завтра|послезавтра|вчера"      # Russian
    r"|today|tomorrow|day after tomorrow|yesterday"  # English
    r")$",
    flags=re.IGNORECASE,
)
_REL_WEEK_RE = re.compile(
    r"^через\s+(\d+)?\s*недел(?:ю|и|ь)$",
    flags=re.IGNORECASE,
)
_REL_MONTH_RE = re.compile(
    r"^через\s+(\d+)?\s*месяц(?:а|ев)?$",
    flags=re.IGNORECASE,
)
_REL_YEAR_RE = re.compile(
    r"^через\s+(\d+)?\s*(?:год|года|лет)$",
    flags=re.IGNORECASE,
)
# "следующий/следующая/следующее [день недели]" or bare "[день недели]"
_NEXT_WD_RE = re.compile(
    r"^(?:следующ(?:ий|ая|ее)\s+)?(" + "|".join(_WD_RU) + r")$",
    flags=re.IGNORECASE,
)
# "в понедельник", "в среду", "во вторник"
_PREP_WD_RE = re.compile(
    r"^во?\s+(" + "|".join(_ALL_WD) + r")$",
    flags=re.IGNORECASE,
)
# "на этой/текущей неделе", "эта неделя", "в эту неделю"
_THIS_WEEK_RE = re.compile(
    r"^(?:(?:на|в)\s+)?(?:эт(?:ой|у)|текущей)\s+недел(?:е|ю|ь|я)|^эта\s+неделя$",
    flags=re.IGNORECASE,
)
# "на следующей неделе", "следующая неделя", "в следующую неделю"
_NEXT_WEEK_RE = re.compile(
    r"^(?:(?:на|в)\s+)?следующей\s+недел(?:е|ю|ь)|^следующая\s+неделя$",
    flags=re.IGNORECASE,
)
# "в конце недели" / "в конце этой недели"
_END_WEEK_RE = re.compile(
    r"^в\s+конце\s+(?:этой\s+|текущей\s+)?недел(?:и|е|ь)$",
    flags=re.IGNORECASE,
)
# "в начале [следующей] недели"
_START_WEEK_RE = re.compile(
    r"^в\s+начале\s+(?:(следующей)\s+)?недел(?:и|е|ь)$",
    flags=re.IGNORECASE,
)
_ABS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Russian/European short format: DD.MM.YYYY  or  D.M.YYYY
_ABS_DMY_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
# With time suffix DD.MM.YYYY HH:MM — time is parsed but discarded
_ABS_DMY_T_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+\d{1,2}:\d{2}")


def parse_relative_date(text: str, anchor: Optional[date] = None) -> Optional[dict]:
    """
    Parse a relative date string and return {"iso": "YYYY-MM-DD", "human": "..."}
    or None if parsing failed.
    """
    text = text.strip().lower()
    if not text:
        return None

    from personal_assistant.utils.timezone import get_now_msk
    anchor = anchor or get_now_msk().date()

    # ── Absolute: YYYY-MM-DD ────────────────────────────────────────────
    if _ABS_RE.match(text):
        try:
            d = date.fromisoformat(text)
            return {"iso": d.isoformat(), "human": _fmt(d)}
        except ValueError:
            return None

    # ── Absolute: DD.MM.YYYY [HH:MM] ─────────────────────────────────────
    m = _ABS_DMY_T_RE.match(text) or _ABS_DMY_RE.match(text)
    if m:
        try:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            d = date(year, month, day)
            return {"iso": d.isoformat(), "human": _fmt(d)}
        except ValueError:
            return None

    # ── "сегодня"/"today", "завтра"/"tomorrow", "послезавтра"/"day after tomorrow",
    #    "вчера"/"yesterday", "через N дней" ──────────────────────────────────────
    m = _REL_DAY_RE.match(text)
    if m:
        groups = m.groups()
        if groups[0]:
            d = anchor + timedelta(days=int(groups[0]))
            return {"iso": d.isoformat(), "human": _fmt(d)}
        if text in ("послезавтра", "day after tomorrow"):
            return {"iso": (anchor + timedelta(days=2)).isoformat(), "human": _fmt(anchor + timedelta(days=2))}
        if text in ("завтра", "tomorrow"):
            return {"iso": (anchor + timedelta(days=1)).isoformat(), "human": _fmt(anchor + timedelta(days=1))}
        if text in ("вчера", "yesterday"):
            return {"iso": (anchor - timedelta(days=1)).isoformat(), "human": _fmt(anchor - timedelta(days=1))}
        # "сегодня" / "today"
        return {"iso": anchor.isoformat(), "human": _fmt(anchor)}

    # ── "через N недель" ────────────────────────────────────────────────
    m = _REL_WEEK_RE.match(text)
    if m:
        num = int(m.group(1)) if m.group(1) else 1
        d = anchor + timedelta(weeks=num)
        return {"iso": d.isoformat(), "human": _fmt(d)}

    # ── "через N месяцев" ───────────────────────────────────────────────
    m = _REL_MONTH_RE.match(text)
    if m:
        num = int(m.group(1)) if m.group(1) else 1
        d = _add_months(anchor, num)
        return {"iso": d.isoformat(), "human": _fmt(d)}

    # ── "через год / через 2 года / через 3 лет" ────────────────────────
    m = _REL_YEAR_RE.match(text)
    if m:
        num = int(m.group(1)) if m.group(1) else 1
        d = _add_years(anchor, num)
        return {"iso": d.isoformat(), "human": _fmt(d)}

    # ── "на этой неделе", "эта неделя" → понедельник текущей недели ────
    if _THIS_WEEK_RE.match(text):
        monday = anchor - timedelta(days=anchor.isoweekday() - 1)
        return {"iso": monday.isoformat(), "human": _fmt(monday)}

    # ── "на следующей неделе", "следующая неделя" → пн следующей ───────
    if _NEXT_WEEK_RE.match(text):
        days_to_next_monday = 8 - anchor.isoweekday()  # always ≥ 1
        monday = anchor + timedelta(days=days_to_next_monday)
        return {"iso": monday.isoformat(), "human": _fmt(monday)}

    # ── "в конце недели" → пятница текущей недели ───────────────────────
    if _END_WEEK_RE.match(text):
        friday = anchor + timedelta(days=5 - anchor.isoweekday())
        if friday < anchor:
            friday += timedelta(weeks=1)
        return {"iso": friday.isoformat(), "human": _fmt(friday)}

    # ── "в начале [следующей] недели" → понедельник [следующей] ─────────
    m = _START_WEEK_RE.match(text)
    if m:
        if m.group(1):  # "следующей"
            days_to_next_monday = 8 - anchor.isoweekday()
            monday = anchor + timedelta(days=days_to_next_monday)
        else:
            monday = anchor - timedelta(days=anchor.isoweekday() - 1)
        return {"iso": monday.isoformat(), "human": _fmt(monday)}

    # ── "следующий/следующая [день]" или просто "[день]" (номинатив) ───
    m = _NEXT_WD_RE.match(text)
    if m:
        wd_name = m.group(1).lower()
        target_wd = _WD_RU.get(wd_name)
        if target_wd:
            d = _next_weekday(anchor, target_wd)
            return {"iso": d.isoformat(), "human": _fmt(d)}

    # ── "в понедельник", "в среду", "в пятницу" (аккузатив) ────────────
    m = _PREP_WD_RE.match(text)
    if m:
        wd_name = m.group(1).lower()
        target_wd = _ALL_WD.get(wd_name)
        if target_wd:
            d = _next_weekday(anchor, target_wd)
            return {"iso": d.isoformat(), "human": _fmt(d)}

    logger.debug(f"[date_calc] unable to parse '{text}'")
    return None


def _fmt(d: date) -> str:
    """Human-readable Russian date."""
    wd = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][d.weekday()]
    months = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    return f"{wd}, {d.day} {months[d.month - 1]} {d.year}"


def _add_years(d: date, years: int) -> date:
    """Add *years* to *d*, capping Feb-29 to Feb-28 in non-leap years."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _add_months(d: date, months: int) -> date:
    """Add *months* to a date, capping to month-end."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_weekday(d: date, target_wd: int) -> date:
    """Return the next occurrence of *target_wd* (1=Mon) after *d*."""
    days_ahead = target_wd - d.isoweekday()
    if days_ahead <= 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Tool interface (used by context builder / chat router)
# ---------------------------------------------------------------------------


def tool_spec() -> dict:
    return {
        "name": "date_calc",
        "description": (
            "Преобразует даты в абсолютный формат YYYY-MM-DD. "
            "Поддерживает русский и английский язык. "
            "Русский: 'сегодня', 'завтра', 'послезавтра', 'вчера'; "
            "English: 'today', 'tomorrow', 'day after tomorrow', 'yesterday'; "
            "'через 3 дня', 'через неделю', 'через 2 недели', 'через месяц', 'через год', 'через 2 года'; "
            "'следующий понедельник', 'следующая среда'; "
            "'в понедельник', 'в пятницу', 'в среду' (ближайший день недели); "
            "'на этой неделе', 'эта неделя' (понедельник текущей недели); "
            "'на следующей неделе', 'следующая неделя' (понедельник следующей недели); "
            "'в конце недели' (пятница текущей недели); "
            "'в начале недели', 'в начале следующей недели'; "
            "ISO формат '2026-05-16'; российский формат '16.05.2026', '16.05.2026 10:00'. "
            "Используй всегда, когда пользователь упоминает любую дату."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Относительное или абсолютное выражение даты",
                }
            },
            "required": ["expression"],
        },
    }


def run(expression: str) -> dict:
    """Public tool entrypoint — always returns MSK-aligned ISO."""
    from personal_assistant.utils.timezone import parse_relative_to_msk
    return parse_relative_to_msk(expression)
