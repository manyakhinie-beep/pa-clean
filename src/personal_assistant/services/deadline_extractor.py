"""
deadline_extractor.py — извлечение срока («deadline») из текста письма.

Принцип: **нет LLM**, чисто regex + небольшая лексика.  Это и быстрее
(миллисекунды на письмо вместо секунд через MLX), и детерминистично —
правила в Inbox должны фильтровать одинаково при каждом ре-запуске.

Поддерживаемые паттерны (от высокой к низкой уверенности):

  * Абсолютные даты: «до 15.06.2026», «15 июня», «15 июня 2026»,
    «15.06», «15/06».
  * Относительные: «сегодня», «завтра», «послезавтра»,
    «через 2 дня / 3 недели / месяц», «через неделю».
  * Дни недели: «до пятницы», «к понедельнику», «на четверг».
  * Концы периодов: «до конца недели», «до конца месяца», «до конца года».
  * «На этой неделе», «на следующей неделе», «в этом месяце»,
    «в следующем месяце».

Все относительные даты считаются от *даты письма*, не от «сейчас» —
когда письмо было написано, «через 2 недели» означало 2 недели от того
момента, а не от сегодняшнего просмотра.

Возвращает ``Optional[datetime]`` (timezone-aware UTC) либо ``None``,
если уверенного срока в тексте нет.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------

# Месяцы в родительном падеже (так они стоят после числа: «15 июня»).
# Ключ — нормализованная подстрока, значение — номер месяца.
_MONTHS_RU: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# Дни недели → ISO weekday (понедельник=1, воскресенье=7).
_WEEKDAYS_RU: dict[str, int] = {
    "понедельник": 1, "вторник": 2, "сред": 3, "четверг": 4,
    "пятниц": 5, "суббот": 6, "воскресень": 7,
    # Короткие формы
    "пн": 1, "вт": 2, "ср": 3, "чт": 4, "пт": 5, "сб": 6, "вс": 7,
}

# Trigger-слова означающие «впереди срок».  Поднимают уверенность, если
# дата найдена в радиусе 80 символов после них.
_DEADLINE_TRIGGERS: tuple[str, ...] = (
    "срок", "дедлайн", "deadline",
    " до ", " к ", "не позднее", "не позже",
    "требуется к", "требуется до",
    "необходимо к", "необходимо до",
    "выполнить до", "выполнить к",
    "подготовить к", "подготовить до",
    "направить до", "направить к",
    "сдать к", "сдать до",
    "ответ до", "ответ к",
)

# Максимальное «расстояние» от trigger-слова до даты в символах.
_TRIGGER_RADIUS = 80


# ---------------------------------------------------------------------------
# Date parsers — каждый возвращает Optional[date] или None
# ---------------------------------------------------------------------------


_NUM_DATE_RE = re.compile(
    r"\b(?P<d>0?[1-9]|[12]\d|3[01])"
    r"[./-]"
    r"(?P<m>0?[1-9]|1[0-2])"
    r"(?:[./-](?P<y>\d{2,4}))?\b"
)


def _parse_numeric_date(s: str, ref: date) -> Optional[date]:
    """`15.06.2026`, `15.06`, `15/6/26` → date.  Без года → берём ref.year."""
    m = _NUM_DATE_RE.search(s)
    if not m:
        return None
    day = int(m.group("d"))
    month = int(m.group("m"))
    year_s = m.group("y")
    if year_s:
        year = int(year_s)
        if year < 100:
            year += 2000
    else:
        # Без года — берём текущий, или следующий если дата уже прошла.
        year = ref.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if not year_s and d < ref:
        # «15.06» в письме от 2026-08-01 → имеется в виду 2027-06-15.
        try:
            d = date(year + 1, month, day)
        except ValueError:
            return None
    return d


_TEXT_DATE_RE = re.compile(
    r"\b(?P<d>0?[1-9]|[12]\d|3[01])\s+"
    r"(?P<mon>январ\w*|феврал\w*|март\w*|апрел\w*|ма[яе]|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
    r"(?:\s+(?P<y>\d{4}))?\b",
    re.IGNORECASE,
)


def _parse_text_date(s: str, ref: date) -> Optional[date]:
    """«15 июня», «15 июня 2026»."""
    m = _TEXT_DATE_RE.search(s)
    if not m:
        return None
    day = int(m.group("d"))
    mon_raw = m.group("mon").lower()
    month: Optional[int] = None
    for key, idx in _MONTHS_RU.items():
        if mon_raw.startswith(key):
            month = idx
            break
    if month is None:
        return None
    year_s = m.group("y")
    year = int(year_s) if year_s else ref.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if not year_s and d < ref:
        try:
            d = date(year + 1, month, day)
        except ValueError:
            return None
    return d


_THROUGH_N_DAYS_RE = re.compile(
    r"через\s+(?P<n>\d+)\s+(?P<unit>дн|недел|месяц)",
    re.IGNORECASE,
)


def _parse_through_n(s: str, ref: date) -> Optional[date]:
    """«через 2 дня», «через 3 недели», «через месяц»."""
    m = _THROUGH_N_DAYS_RE.search(s)
    if not m:
        return None
    n = int(m.group("n"))
    unit = m.group("unit").lower()
    if unit.startswith("дн"):
        return ref + timedelta(days=n)
    if unit.startswith("недел"):
        return ref + timedelta(weeks=n)
    if unit.startswith("месяц"):
        # Приближаем месяц 30 днями — точная арифметика месяцев избыточна
        # для бизнес-сроков (пользователю не критично 30 vs 31).
        return ref + timedelta(days=n * 30)
    return None


_THROUGH_ONE_RE = re.compile(
    r"через\s+(?P<unit>день|недел[юи]|месяц)",
    re.IGNORECASE,
)


def _parse_through_one(s: str, ref: date) -> Optional[date]:
    """«через день», «через неделю», «через месяц»."""
    m = _THROUGH_ONE_RE.search(s)
    if not m:
        return None
    unit = m.group("unit").lower()
    if unit.startswith("ден"):
        return ref + timedelta(days=1)
    if unit.startswith("недел"):
        return ref + timedelta(weeks=1)
    if unit.startswith("месяц"):
        return ref + timedelta(days=30)
    return None


_RELATIVE_RE = re.compile(
    r"\b(?P<word>сегодня|завтра|послезавтра)\b",
    re.IGNORECASE,
)


def _parse_relative(s: str, ref: date) -> Optional[date]:
    """«сегодня» / «завтра» / «послезавтра»."""
    m = _RELATIVE_RE.search(s)
    if not m:
        return None
    word = m.group("word").lower()
    if word == "сегодня":
        return ref
    if word == "завтра":
        return ref + timedelta(days=1)
    if word == "послезавтра":
        return ref + timedelta(days=2)
    return None


_PERIOD_END_RE = re.compile(
    r"(?:до\s+)?конца\s+(?P<period>(этой\s+)?недел[ьи]|"
    r"(этого\s+)?месяца|(этого\s+)?года)",
    re.IGNORECASE,
)


def _parse_period_end(s: str, ref: date) -> Optional[date]:
    """«до конца недели», «до конца месяца», «до конца года»."""
    m = _PERIOD_END_RE.search(s)
    if not m:
        return None
    period = m.group("period").lower()
    if "недел" in period:
        # ISO weekday: понедельник=1...воскресенье=7; конец = воскресенье
        return ref + timedelta(days=(7 - ref.isoweekday()))
    if "месяц" in period:
        # Последний день текущего месяца
        if ref.month == 12:
            return date(ref.year, 12, 31)
        return date(ref.year, ref.month + 1, 1) - timedelta(days=1)
    if "год" in period:
        return date(ref.year, 12, 31)
    return None


_THIS_WEEK_RE = re.compile(
    r"\b(?:на\s+этой\s+недел[еи]|на\s+следующей\s+недел[еи]|"
    r"в\s+этом\s+месяце|в\s+следующем\s+месяце)\b",
    re.IGNORECASE,
)


def _parse_this_period(s: str, ref: date) -> Optional[date]:
    """«на этой неделе» → конец недели; «в этом месяце» → конец месяца;
    «на следующей неделе» → конец следующей недели; и т. д.

    Возвращаем КОНЕЦ периода как самую позднюю допустимую дату — это
    самая безопасная интерпретация для «дедлайна».
    """
    m = _THIS_WEEK_RE.search(s)
    if not m:
        return None
    raw = m.group(0).lower()
    if "следующей недел" in raw:
        end_this = ref + timedelta(days=(7 - ref.isoweekday()))
        return end_this + timedelta(days=7)
    if "этой недел" in raw:
        return ref + timedelta(days=(7 - ref.isoweekday()))
    if "следующем месяце" in raw:
        if ref.month == 12:
            return date(ref.year + 1, 1, 31)
        next_m = ref.month + 1
        next_y = ref.year
        if next_m == 12:
            return date(next_y, 12, 31)
        return date(next_y, next_m + 1, 1) - timedelta(days=1)
    if "этом месяце" in raw:
        if ref.month == 12:
            return date(ref.year, 12, 31)
        return date(ref.year, ref.month + 1, 1) - timedelta(days=1)
    return None


_WEEKDAY_RE = re.compile(
    r"\b(?:до|к|на)\s+"
    r"(?P<day>понедельник\w*|вторник\w*|сред[уы]|четверг\w*|"
    r"пятниц[уы]|суббот[уы]|воскресень\w*|пн|вт|ср|чт|пт|сб|вс)\b",
    re.IGNORECASE,
)


def _parse_weekday(s: str, ref: date) -> Optional[date]:
    """«до пятницы», «к понедельнику», «на четверг»."""
    m = _WEEKDAY_RE.search(s)
    if not m:
        return None
    raw = m.group("day").lower()
    target_iso: Optional[int] = None
    for key, iso in _WEEKDAYS_RU.items():
        if raw.startswith(key):
            target_iso = iso
            break
    if target_iso is None:
        return None
    # «к пятнице» = ближайшая пятница, не раньше завтра (если уже среда,
    # то ближайшая в эту неделю; если уже пятница — то СЛЕДУЮЩАЯ).
    delta = (target_iso - ref.isoweekday()) % 7
    if delta == 0:
        delta = 7
    return ref + timedelta(days=delta)


# Порядок попыток — от самых надёжных к самым общим.
_PARSERS = (
    _parse_text_date,       # «15 июня» — очень надёжно
    _parse_numeric_date,    # «15.06.2026»
    _parse_through_n,       # «через 2 дня»
    _parse_through_one,     # «через неделю»
    _parse_relative,        # «завтра»
    _parse_period_end,      # «до конца недели»
    _parse_this_period,     # «на этой неделе»
    _parse_weekday,         # «к пятнице»
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_deadline(
    text: str,
    *,
    reference_date: Optional[datetime] = None,
) -> Optional[datetime]:
    """Найти срок («deadline») в *text* и вернуть как timezone-aware UTC.

    Алгоритм:
      1. Если в тексте есть trigger-слово («срок», «до», «к …», «дедлайн»),
         ищем дату в радиусе ``_TRIGGER_RADIUS`` символов **после** него
         — это самая высокая уверенность.
      2. Если триггера нет, прогоняем все парсеры по полному тексту и
         возвращаем самую раннюю найденную дату ≥ reference_date.

    :param text: subject + body (или любая комбинация — функция чистая).
    :param reference_date: «сегодня» для относительных дат.  По умолчанию
        — текущий UTC момент.  Передавай дату письма, чтобы «через 2
        недели» считалось от момента отправки.
    :returns: ``datetime`` (00:00 UTC дня дедлайна) или ``None``.
    """
    if not text or not isinstance(text, str):
        return None

    ref_dt = reference_date or datetime.now(tz=timezone.utc)
    if ref_dt.tzinfo is None:
        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
    ref_d = ref_dt.date()

    lower = text.lower()
    candidates: list[date] = []

    # Шаг 1 — поиск рядом с trigger-словами.
    for trig in _DEADLINE_TRIGGERS:
        pos = 0
        while True:
            idx = lower.find(trig, pos)
            if idx < 0:
                break
            chunk = text[idx : idx + len(trig) + _TRIGGER_RADIUS]
            for parser in _PARSERS:
                d = parser(chunk, ref_d)
                if d:
                    candidates.append(d)
            pos = idx + len(trig)

    # Шаг 2 — если триггер не сработал, прогоняем парсеры по всему тексту.
    if not candidates:
        for parser in _PARSERS:
            d = parser(text, ref_d)
            if d:
                candidates.append(d)

    # Фильтр: даты в прошлом игнорируем (дедлайн не может быть «вчера»).
    future = [d for d in candidates if d >= ref_d]
    if not future:
        return None

    chosen = min(future)
    return datetime(chosen.year, chosen.month, chosen.day, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Horizon evaluation — used by rule engine
# ---------------------------------------------------------------------------


# Список разрешённых значений deadline_horizon.  «any» означает «правило
# не фильтрует по сроку» — это поведение по умолчанию (back-compat).
DEADLINE_HORIZONS: tuple[str, ...] = (
    "any", "today", "this_week", "this_month", "next_week", "next_month",
)


def horizon_end(horizon: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """Конец временного окна для данного horizon-а, относительно ``now``.

    Возвращает ``None`` для ``"any"`` — сигнал «нет фильтра».
    Остальные значения возвращают timezone-aware UTC datetime,
    соответствующий 23:59:59.999999 последнего дня окна.
    """
    if horizon == "any" or horizon not in DEADLINE_HORIZONS:
        return None

    cur = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    cur_d = cur.date()

    if horizon == "today":
        end_d = cur_d
    elif horizon == "this_week":
        end_d = cur_d + timedelta(days=(7 - cur_d.isoweekday()))
    elif horizon == "next_week":
        end_this = cur_d + timedelta(days=(7 - cur_d.isoweekday()))
        end_d = end_this + timedelta(days=7)
    elif horizon == "this_month":
        if cur_d.month == 12:
            end_d = date(cur_d.year, 12, 31)
        else:
            end_d = date(cur_d.year, cur_d.month + 1, 1) - timedelta(days=1)
    elif horizon == "next_month":
        if cur_d.month == 12:
            end_d = date(cur_d.year + 1, 1, 31)
        else:
            next_m = cur_d.month + 1
            if next_m == 12:
                end_d = date(cur_d.year, 12, 31)
            else:
                end_d = date(cur_d.year, next_m + 1, 1) - timedelta(days=1)
    else:
        return None

    return datetime(end_d.year, end_d.month, end_d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def fits_horizon(
    deadline: Optional[datetime],
    horizon: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Подходит ли *deadline* под окно правила.

      * ``horizon == "any"`` → True всегда (нет фильтра по сроку).
      * Любой другой horizon: deadline должен быть не ``None`` и
        попадать в диапазон [сегодня 00:00 … horizon_end].

    Если deadline = None и horizon != "any" — возвращаем False
    (правило с конкретным окном не должно срабатывать на письмах
    без явного срока).
    """
    if horizon == "any" or not horizon:
        return True
    if deadline is None:
        return False
    cur = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    end = horizon_end(horizon, now=cur)
    if end is None:
        return True
    cur_start = cur.replace(hour=0, minute=0, second=0, microsecond=0)
    return cur_start <= deadline <= end
