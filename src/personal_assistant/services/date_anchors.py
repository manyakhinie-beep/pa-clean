"""
date_anchors.py — фиксированные опорные даты для one-shot LLM-промптов.

Зачем: ``draft / summarize / delegate`` — это одношаговые вызовы
``engine.ask(...)`` БЕЗ tool calling.  У модели нет возможности вызвать
``date_calc`` для вычисления «через 2 недели» или «следующая пятница» —
она вынуждена угадывать, что приводит к галлюцинациям конкретных дат.

Решение: считаем даты на сервере (через тот же ``date_calc`` и
``deadline_extractor``, что использует chat-режим) и инжектим
итоговый блок в системный/пользовательский промпт:

    ## Опорные даты — используй ТОЛЬКО эти значения
    Сегодня:                2026-05-28 (среда)
    Дата письма:            2026-05-27 (вторник)
    Извлечённый срок:       2026-06-10 (среда) — «через 2 недели» от даты письма

    Если в письме упомянуты другие относительные даты («до пятницы»,
    «до конца месяца»), считай их от даты письма, не от сегодня.
    НЕ выдумывай конкретные числа — если уверенности нет, пиши «срок
    не указан» или [УТОЧНИТЬ: <дата>].

Это backend-вариант того, что в chat-mode делает ``date_calc`` tool:
дата приходит к модели уже разрешённой, ей остаётся только использовать.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from personal_assistant.services.deadline_extractor import extract_deadline


_RU_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
)


def _fmt_with_weekday(dt: datetime) -> str:
    """``2026-05-28 (среда)`` — единый формат для всего модуля."""
    return f"{dt.date().isoformat()} ({_RU_WEEKDAYS[dt.weekday()]})"


def _parse_iso_or_none(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_date_anchor_block(
    *,
    email_date: Optional[str] = None,
    email_text: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Собрать блок с опорными датами для one-shot промпта.

    :param email_date: ISO-дата письма (``MailMessage.date`` / ``item.date``).
        Используется как reference для относительных фраз в теле письма
        и как явный анкер «дата письма».
    :param email_text: текст письма (subject + body) — из него
        ``deadline_extractor`` достаёт явный срок, если он есть.
    :param now: «сейчас» (для тестов).  По умолчанию текущий UTC.
    :returns: готовый markdown-блок (с двумя пустыми строками вокруг),
        либо пустая строка, если ни одна дата не известна.
    """
    cur = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    email_dt = _parse_iso_or_none(email_date)

    deadline_dt = None
    if email_text:
        try:
            deadline_dt = extract_deadline(email_text, reference_date=email_dt or cur)
        except Exception:
            deadline_dt = None

    lines: list[str] = ["## Опорные даты — используй ТОЛЬКО эти значения"]
    lines.append(f"Сегодня:           {_fmt_with_weekday(cur)}")
    if email_dt is not None:
        lines.append(f"Дата письма:       {_fmt_with_weekday(email_dt)}")
    if deadline_dt is not None:
        lines.append(f"Извлечённый срок:  {_fmt_with_weekday(deadline_dt)}")

    # Если кроме «сегодня» вообще ничего нет — анкер тоже полезен (модель
    # знает текущую дату), но это малая ценность.  Выдаём блок всегда —
    # одна строка стоит дешевле любой галлюцинации.

    lines.append("")
    lines.append(
        "Если в письме встречаются другие относительные даты («до пятницы», "
        "«до конца месяца», «через неделю») — считай их от даты письма, "
        "не от сегодня. НЕ выдумывай конкретные числа: если уверенности "
        "нет, пиши «срок не указан» или [УТОЧНИТЬ: <дата>]."
    )
    return "\n".join(lines) + "\n\n"
