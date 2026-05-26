"""
intent_parser.py — Rule-based NLP parser for natural language event creation.

Converts Russian natural language phrases like:
  "Встреча с Ивановым в следующий четверг в 15:00"
  "Созвон по проекту во вторник утром на час"
  "Блокировать время для отчёта в пятницу 14-16"

into a structured EventDraft.

No external NLP libs required. Works fully offline.
MLX fallback for complex phrases (if engine available).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from personal_assistant.config import settings

# ---------------------------------------------------------------------------
# EventDraft
# ---------------------------------------------------------------------------


@dataclass
class EventDraft:
    """Structured representation of a parsed calendar event."""

    title: str = "Новое событие"
    date_iso: str = ""                   # YYYY-MM-DD
    time_str: str = "09:00"             # HH:MM (local)
    duration_minutes: int = 60
    participants: list[str] = field(default_factory=list)
    location: str = ""
    calendar_name: Optional[str] = None  # None → ask user to pick
    notes: str = ""
    start_iso: str = ""                  # full ISO datetime (local)
    end_iso: str = ""                    # full ISO datetime (local)
    confidence: float = 1.0             # 0..1
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Russian weekday/month tables
# ---------------------------------------------------------------------------

_WEEKDAY_RU = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "среду": 2, "ср": 2, "среде": 2, "средой": 2,
    "четверг": 3, "чт": 3, "четвергу": 3,
    "пятница": 4, "пт": 4, "пятницу": 4, "пятнице": 4,
    "суббота": 5, "сб": 5, "субботу": 5, "субботе": 5,
    "воскресенье": 6, "вс": 6, "воскресенью": 6,
}

_MONTH_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январе": 1, "феврале": 2, "марте": 3, "апреле": 4,
    "мае": 5, "июне": 6, "июле": 7, "августе": 8,
    "сентябре": 9, "октябре": 10, "ноябре": 11, "декабре": 12,
}

_NUM_WORDS_RU = {
    "один": 1, "одну": 1, "одного": 1, "первый": 1, "первую": 1,
    "два": 2, "две": 2, "двух": 2, "второй": 2, "вторую": 2,
    "три": 3, "трёх": 3, "третий": 3, "третью": 3,
    "четыре": 4, "четырёх": 4, "четвёртый": 4,
    "пять": 5, "пяти": 5, "пятый": 5,
    "шесть": 6, "шести": 6, "шестой": 6,
    "семь": 7, "семи": 7, "седьмой": 7,
    "восемь": 8, "восьми": 8, "восьмой": 8,
    "девять": 9, "девяти": 9, "девятый": 9,
    "десять": 10, "десяти": 10, "десятый": 10,
    "полчаса": None,  # handled separately
    "полтора": None,  # 1.5 hours
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> date:
    return datetime.now(timezone.utc).astimezone().date()


def _next_weekday(weekday: int, from_date: date | None = None, next_week: bool = False) -> date:
    """Return next occurrence of weekday (0=Mon … 6=Sun).

    If next_week=True: skip directly to next week's occurrence.
    """
    base = from_date or _today()
    days_ahead = (weekday - base.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # same weekday → next week
    if next_week and days_ahead < 7:
        days_ahead += 7
    return base + timedelta(days=days_ahead)


def _hhmm_to_minutes(hhmm: str) -> int:
    """'14:30' → 870"""
    parts = re.split(r"[:.]", hhmm)
    h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    return h * 60 + m


def _minutes_to_hhmm(minutes: int) -> str:
    """870 → '14:30'"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _build_iso(d: date, hhmm: str) -> str:
    """Build local ISO datetime string from date + 'HH:MM'."""
    h, m = map(int, hhmm.split(":"))
    dt = datetime(d.year, d.month, d.day, h, m, 0)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(
    text: str,
    reference_date: date | None = None,
) -> tuple[date | None, list[str]]:
    """Extract date from Russian text. Returns (date, warnings).

    Args:
        text:           Input text (Russian).
        reference_date: Override for «today»; defaults to actual today.
    """
    t = text.lower()
    warnings: list[str] = []
    today = reference_date or _today()

    # ── absolute: "25 мая", "1 июня 2026" ──────────────────────────────────
    for month_name, month_num in _MONTH_RU.items():
        pattern = rf"\b(\d{{1,2}})\s+{re.escape(month_name)}(?:\s+(\d{{4}}))?"
        m = re.search(pattern, t)
        if m:
            day = int(m.group(1))
            year = int(m.group(2)) if m.group(2) else today.year
            try:
                return date(year, month_num, day), warnings
            except ValueError:
                pass

    # ── "через неделю" (shorthand for "через 1 неделю") ────────────────────
    if re.search(r"через\s+неделю\b", t):
        return today + timedelta(weeks=1), warnings

    # ── "через N дней/недель" ───────────────────────────────────────────────
    m = re.search(r"через\s+(\d+|один|два|три|четыре|пять|шесть|семь)\s+"
                  r"(день|дня|дней|неделю|недели|недель)", t)
    if m:
        raw_n = m.group(1)
        n = _NUM_WORDS_RU.get(raw_n) or int(raw_n)
        unit = m.group(2)
        if "недел" in unit:
            return today + timedelta(weeks=n), warnings
        return today + timedelta(days=n), warnings

    # ── "следующий/следующей <weekday>" ────────────────────────────────────
    m = re.search(
        r"следующ(?:ий|ей|ую|ем)\s+(" + "|".join(_WEEKDAY_RU.keys()) + r")\b", t
    )
    if m:
        wd = _WEEKDAY_RU[m.group(1)]
        return _next_weekday(wd, from_date=today, next_week=True), warnings

    # ── "в/во <weekday>" ───────────────────────────────────────────────────
    m = re.search(r"\bво?\b\s+(" + "|".join(_WEEKDAY_RU.keys()) + r")\b", t)
    if m:
        wd = _WEEKDAY_RU[m.group(1)]
        return _next_weekday(wd, from_date=today), warnings

    # ── <weekday> (bare word) ──────────────────────────────────────────────
    for wd_name, wd_num in sorted(_WEEKDAY_RU.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(wd_name) + r"\b", t):
            return _next_weekday(wd_num, from_date=today), warnings

    # ── "сегодня", "завтра", "послезавтра" ─────────────────────────────────
    if "послезавтра" in t:
        return today + timedelta(days=2), warnings
    if "завтра" in t:
        return today + timedelta(days=1), warnings
    if "сегодня" in t:
        return today, warnings

    warnings.append("Дата не найдена, используется завтра")
    return today + timedelta(days=1), warnings


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def _parse_time(text: str) -> tuple[str, list[str]]:
    """Extract HH:MM from Russian text. Returns (time_str, warnings)."""
    t = text.lower()
    warnings: list[str] = []

    # ── pure hour range "14-16" or "9-11" (both ≤ 23, no colon/dot) ────────
    # Must come BEFORE HH:MM to avoid mis-parsing
    m = re.search(r"\b([01]?\d|2[0-3])\s*[-–]\s*([01]?\d|2[0-3])\b", t)
    if m:
        h1, h2 = int(m.group(1)), int(m.group(2))
        if 0 <= h1 <= 23 and 0 <= h2 <= 23 and h1 < h2:
            return f"{h1:02d}:00", warnings

    # ── time range with minutes "14:00-16:00" or "14.00-16.00" ─────────────
    m = re.search(r"\b(\d{1,2})[:.–](\d{2})\s*[-–]\s*\d{1,2}[:.–]?\d*\b", t)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2))
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return f"{h:02d}:{mins:02d}", warnings

    # ── "HH:MM" / "HH.MM" ─────────────────────────────────────────────────
    m = re.search(r"\b(\d{1,2})[:.–](\d{2})\b", t)
    if m:
        h, mins = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return f"{h:02d}:{mins:02d}", warnings

    # ── "в N часов/часа" or "в N" with single digit ────────────────────────
    m = re.search(
        r"в\s+(\d{1,2}|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять|"
        r"одиннадцать|двенадцать)\s*(час[ао]в?|ч\.?)?", t
    )
    if m:
        raw_n = m.group(1)
        h = _NUM_WORDS_RU.get(raw_n)  # type: ignore[assignment]  # narrowed below
        if h is None:
            try:
                h = int(raw_n)
            except ValueError:
                h = None
        if h is not None and 0 <= h <= 23:
            # afternoon convention: if < 8 and no explicit "утра" → add 12
            if h < 8 and "утр" not in t and "ночи" not in t:
                h += 12
            return f"{h:02d}:00", warnings

    # ── time-of-day hints ──────────────────────────────────────────────────
    _TOD = {
        "утром": "09:00", "с утра": "09:00",
        "в обед": "13:00", "на обед": "13:00",
        "после обеда": "14:00",
        "вечером": "18:00", "к вечеру": "18:00",
        "ночью": "22:00",
    }
    for phrase, t_val in _TOD.items():
        if phrase in t:
            return t_val, warnings

    warnings.append("Время не найдено, используется 09:00")
    return "09:00", warnings


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

def _parse_duration(
    text: str, start_time: str | None = None, default: int | None = None
) -> int:
    """Return duration in minutes. Uses time range if present, else keywords.

    When nothing in the text specifies a duration, fall back to *default*
    (the configured ``calendar_default_duration``) rather than a hardcoded hour.
    """
    t = text.lower()

    # ── time range: "14:00-16:00" or "14-16" ──────────────────────────────
    m = re.search(r"\b(\d{1,2})[:.]?(\d{2})?\s*[-–]\s*(\d{1,2})[:.]?(\d{2})?\b", t)
    if m:
        h1 = int(m.group(1))
        m1 = int(m.group(2) or 0)
        h2 = int(m.group(3))
        m2 = int(m.group(4) or 0)
        duration = (h2 * 60 + m2) - (h1 * 60 + m1)
        if 0 < duration <= 480:
            return duration

    # ── "полтора часа" → 90 ───────────────────────────────────────────────
    if re.search(r"полтора\s+час", t):
        return 90

    # ── "полчаса" → 30 ────────────────────────────────────────────────────
    if "полчаса" in t or "полу часа" in t:
        return 30

    # ── "на N часов/час/минут" ─────────────────────────────────────────────
    m = re.search(
        r"на\s+(\d+|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять|полтора|полчаса)"
        r"\s*(час[ао]в?|ч\.?|минут[уы]?|мин\.?)?",
        t,
    )
    if m:
        raw_n = m.group(1)
        unit = (m.group(2) or "").strip()

        if raw_n == "полтора":
            return 90
        if raw_n == "полчаса":
            return 30

        n = _NUM_WORDS_RU.get(raw_n)
        if n is None:
            try:
                n = int(raw_n)
            except ValueError:
                n = None
        if n is not None:
            if "мин" in unit:
                return min(n, 480)
            return min(n * 60, 480)  # default: hours

    return default if default is not None else 60  # configured fallback


# ---------------------------------------------------------------------------
# Participants parsing
# ---------------------------------------------------------------------------

_WITH_PATTERN = re.compile(
    r"(?:с\s+|вместе\s+с\s+|встреча\s+с\s+|созвон\s+с\s+|звонок\s+с\s+)"
    r"((?:[А-ЯЁа-яёA-Za-z][а-яёА-ЯЁA-Za-z]+)"
    r"(?:\s+(?:и\s+)?(?:[А-ЯЁа-яёA-Za-z][а-яёА-ЯЁA-Za-z]+))*)"
)

_TEAM_WORDS = {"командой", "командой,", "коллегами", "отделом", "группой"}


def _parse_participants(text: str) -> list[str]:
    """Extract participant names from text."""
    t = text.lower()
    participants: list[str] = []

    # Check for team mentions
    for word in _TEAM_WORDS:
        if word in t:
            participants.append("команда")
            break

    # Extract names after "с", "встреча с", etc.
    for m in _WITH_PATTERN.finditer(text):
        chunk = m.group(1)
        # Split on "и", commas
        names = re.split(r"\s+и\s+|,\s*", chunk)
        for name in names:
            name = name.strip()
            # Filter out common prepositions/articles that may match
            if name and len(name) > 2 and name.lower() not in {
                "ним", "ней", "нами", "ними", "мной", "тобой",
                "командой", "коллегами",
            }:
                participants.append(name)

    return list(dict.fromkeys(participants))  # dedupe preserving order


# ---------------------------------------------------------------------------
# Location parsing
# ---------------------------------------------------------------------------

_LOCATION_PATTERNS = [
    (re.compile(r"\b(zoom|зум)\b", re.I), "Zoom"),
    (re.compile(r"\b(teams|тимс)\b", re.I), "Microsoft Teams"),
    (re.compile(r"\b(meet|гугл\s*мит)\b", re.I), "Google Meet"),
    (re.compile(r"\b(skype|скайп)\b", re.I), "Skype"),
    (re.compile(r"онлайн\b", re.I), "Онлайн"),
    (re.compile(r"переговорн\S+\s+([\w\-–А-ЯЁа-яёA-Za-z0-9]+(?:[\-–]\d+)?)", re.I | re.UNICODE), None),  # "переговорная А-201"
    (re.compile(r"\bофис[ае]?\b", re.I), "Офис"),
]


def _parse_location(text: str) -> str:
    for pattern, location in _LOCATION_PATTERNS:
        m = pattern.search(text)
        if m:
            if location is not None:
                return location
            # Dynamic location: "переговорная А-201" → "Переговорная А-201"
            room = m.group(1).strip() if m.lastindex else m.group(0).strip()
            return f"Переговорная {room}"
    return ""


# ---------------------------------------------------------------------------
# Calendar selection
# ---------------------------------------------------------------------------

_CALENDAR_HINTS = {
    "личн": "Personal",
    "персональн": "Personal",
    "рабоч": "Work",
    "работ": "Work",
    "домашн": "Home",
}


def _parse_calendar(text: str) -> Optional[str]:
    t = text.lower()
    for hint, cal in _CALENDAR_HINTS.items():
        if hint in t:
            return cal
    return None  # no explicit calendar keyword → ask user


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

_MEETING_TYPES = re.compile(
    r"^(встреча|созвон|звонок|синк|митинг|meeting|call|синк|обсуждение|"
    r"презентация|демо|demo|interview|собеседование|воркшоп|тренинг|"
    r"лекция|семинар)(?:\s+|$)",
    re.I,
)

_TITLE_ACTION_PREFIXES = re.compile(
    r"^(?:создай\s+|добавь\s+|назначь\s+|поставь\s+|запланируй\s+|"
    r"забронируй\s+|заблокируй\s+время\s+(?:для\s+)?)",
    re.I,
)

_WEEKDAYS_JOINED = "|".join(sorted(_WEEKDAY_RU.keys(), key=lambda x: -len(x)))

_TITLE_STOPWORDS = re.compile(
    r"\s+(?:во?\s+(?:следующ\S+\s+)?(?:" + _WEEKDAYS_JOINED + r")"
    r"|сегодня|завтра|послезавтра"
    r"|через\s+\S+(?:\s+\S+)?"
    r"|\b\d{1,2}[:.]\d{2}\b"
    r"|\b[01]?\d[-–][01]?\d\b"
    r"|в\s+\d{1,2}\s*час"
    r"|утром|вечером|в\s+обед|после\s+обеда|с\s+утра"
    r"|на\s+(?:час|полчаса|полтора|\d+\s*(?:час|мин))"
    r"|в\s+zoom|в\s+teams|в\s+переговорн\S+"
    r"|онлайн|в\s+офис"
    r").*",
    re.I,
)


def _extract_title(text: str) -> str:
    """Best-effort title extraction from the input text."""
    t = text.strip()

    # Capture leading meeting type ("встреча", "созвон", etc.)
    type_prefix = ""
    m_type = _MEETING_TYPES.match(t.lower())
    if m_type:
        type_prefix = t[:m_type.end()].strip()
        t_rest = t[m_type.end():].strip()
    else:
        # Remove leading action verbs (создай, добавь, ...) only
        t_rest = _TITLE_ACTION_PREFIXES.sub("", t, count=1).strip()
        # Check again for meeting type after action verb
        m_type2 = _MEETING_TYPES.match(t_rest.lower())
        if m_type2:
            type_prefix = t_rest[:m_type2.end()].strip()
            t_rest = t_rest[m_type2.end():].strip()

    # Remove date/time/location suffixes
    t_stripped = _TITLE_STOPWORDS.sub("", t_rest).strip()

    # Remove participant clause at end ("с Ивановым", "с командой")
    t_stripped = re.sub(
        r"\s+с\s+[А-ЯЁа-яёA-Za-z][а-яёА-ЯЁA-Za-z]*(?:\s+и\s+[А-ЯЁа-яёA-Za-z][а-яёА-ЯЁA-Za-z]*)*$",
        "",
        t_stripped,
    ).strip()

    if t_stripped and len(t_stripped) >= 2:
        result = (type_prefix + " " + t_stripped).strip() if type_prefix else t_stripped
        return result[:80]

    # Fallback: just use the type + first content words
    if type_prefix:
        # Try to grab content words from t_rest (before date/time stopwords)
        content = _TITLE_STOPWORDS.sub("", t_rest).strip()
        content = re.sub(
            r"\s+с\s+[А-ЯЁа-яёA-Za-z][а-яёА-ЯЁA-Za-z]*.*$", "", content
        ).strip()
        if content and len(content) >= 2:
            return (type_prefix + " " + content)[:80]
        # For "встреча с Ивановым", keep participant name as title subject
        m_with = re.search(r"\bс\s+([А-ЯЁ][а-яёА-ЯЁ]+(?:\s+[А-ЯЁ][а-яёА-ЯЁ]+)?)\b", t_rest)
        if m_with:
            return (type_prefix + " с " + m_with.group(1))[:80]
        return type_prefix

    # Last resort: first 5 words of original
    words = text.strip().split()
    return " ".join(words[:5]) if words else "Новое событие"


# ---------------------------------------------------------------------------
# MLX-based fallback
# ---------------------------------------------------------------------------

_MLX_PROMPT_TMPL = """Ты — ассистент планировщик. Извлеки из текста поля события и верни ТОЛЬКО валидный JSON.

Текст: "{text}"
Сегодня: {today}

Верни JSON:
{{
  "title": "...",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "duration_minutes": 60,
  "participants": [],
  "location": "",
  "calendar_name": null,
  "notes": ""
}}

Только JSON, без объяснений."""


def _mlx_parse(text: str, mlx_engine) -> dict | None:
    """Try to extract event fields via MLX. Returns dict or None."""
    if mlx_engine is None:
        return None
    try:
        import json as _json

        prompt = _MLX_PROMPT_TMPL.format(
            text=text[:300],
            today=str(_today()),
        )
        result_text = mlx_engine.generate(
            prompt=prompt,
            max_tokens=256,
            temperature=0.1,
        )

        # Extract JSON
        m = re.search(r"\{.*\}", result_text, re.DOTALL)
        if m:
            return _json.loads(m.group(0))
    except Exception as exc:
        logger.debug(f"[intent_parser] mlx_parse failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_event_intent(
    text: str,
    mlx_engine=None,
    reference_date: date | None = None,
) -> EventDraft:
    """
    Parse natural language text into an EventDraft.

    Args:
        text:           User's natural language input (Russian or mixed).
        mlx_engine:     Optional MLX engine for complex phrase handling.
        reference_date: Override "today" for testing.

    Returns:
        EventDraft with all fields populated (using defaults for missing).
    """
    if not text or not text.strip():
        draft = EventDraft()
        draft.warnings.append("Пустой ввод")
        return draft

    # Override today for testing
    if reference_date:
        import builtins
        _orig = builtins.__dict__.get("_INTENT_TODAY_OVERRIDE")
        # We patch via the module-level _today helper indirectly through reference_date
        # by passing it to sub-parsers that accept it

    clean = text.strip()
    all_warnings: list[str] = []

    # ── 1. Rule-based extraction ────────────────────────────────────────────
    parsed_date, date_warnings = _parse_date(clean, reference_date=reference_date)
    all_warnings.extend(date_warnings)

    parsed_time, time_warnings = _parse_time(clean)
    all_warnings.extend(time_warnings)

    duration = _parse_duration(
        clean, start_time=parsed_time, default=settings.calendar_default_duration
    )
    participants = _parse_participants(clean)
    location = _parse_location(clean)
    calendar_name = _parse_calendar(clean)
    title = _extract_title(clean)

    rule_confidence = 1.0
    if date_warnings:
        rule_confidence -= 0.2
    if time_warnings:
        rule_confidence -= 0.15

    # ── 2. MLX refinement (for complex/ambiguous phrases) ──────────────────
    if mlx_engine and rule_confidence < 0.7:
        mlx_result = _mlx_parse(clean, mlx_engine)
        if mlx_result and isinstance(mlx_result, dict):
            logger.debug(f"[intent_parser] MLX refinement: {mlx_result}")
            if mlx_result.get("title") and len(mlx_result["title"]) > 2:
                title = mlx_result["title"][:80]
            if mlx_result.get("date"):
                try:
                    parsed_date = date.fromisoformat(mlx_result["date"])
                    all_warnings = [w for w in all_warnings if "Дата" not in w]
                except ValueError:
                    pass
            if mlx_result.get("time"):
                m = re.match(r"(\d{1,2}):(\d{2})", mlx_result["time"])
                if m:
                    parsed_time = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
                    all_warnings = [w for w in all_warnings if "Время" not in w]
            if mlx_result.get("duration_minutes"):
                try:
                    d_raw = int(mlx_result["duration_minutes"])
                    if 5 <= d_raw <= 480:
                        duration = d_raw
                except (ValueError, TypeError):
                    pass
            if mlx_result.get("participants"):
                participants = mlx_result["participants"]
            if mlx_result.get("location"):
                location = str(mlx_result["location"])
            if mlx_result.get("calendar_name"):
                calendar_name = str(mlx_result["calendar_name"])
            rule_confidence = min(rule_confidence + 0.3, 1.0)

    # ── 3. Build ISO datetimes ──────────────────────────────────────────────
    start_iso = _build_iso(parsed_date or date.today(), parsed_time)
    start_dt = datetime.fromisoformat(start_iso)
    end_dt = start_dt + timedelta(minutes=duration)
    end_iso = end_dt.isoformat()

    return EventDraft(
        title=title,
        date_iso=str(parsed_date),
        time_str=parsed_time,
        duration_minutes=duration,
        participants=participants,
        location=location,
        calendar_name=calendar_name,
        notes="",
        start_iso=start_iso,
        end_iso=end_iso,
        confidence=round(rule_confidence, 2),
        warnings=all_warnings,
        raw_text=clean,
    )
