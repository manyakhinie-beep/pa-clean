"""
Unit tests for ``deadline_extractor`` — извлечение сроков из писем
+ horizon-фильтрация для GTD-правил.

Coverage:
  * Абсолютные даты: «15.06.2026», «15.06», «15 июня», «15 июня 2026»
  * Относительные: «сегодня», «завтра», «послезавтра»,
    «через 2 дня», «через неделю», «через месяц»
  * Концы периодов: «до конца недели/месяца/года»
  * Дни недели: «до пятницы», «к понедельнику»
  * Trigger words: «срок», «до», «дедлайн»
  * Horizon evaluation: today/this_week/next_week/this_month/next_month
  * Edge cases: пустой текст, прошлые даты, кривые даты
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from personal_assistant.services.deadline_extractor import (
    DEADLINE_HORIZONS,
    extract_deadline,
    fits_horizon,
    horizon_end,
)


# Контрольная точка отсчёта для большинства тестов — среда 27 мая 2026.
# ISO weekday = 3, в этой неделе пн 25 → вс 31, в следующей пн 1.06 → вс 7.06,
# конец мая = 31.05, конец июня = 30.06.
REF = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)


def _d(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# Абсолютные даты
# ----------------------------------------------------------------------


class TestNumericDates:
    def test_dd_mm_yyyy_with_dots(self):
        assert extract_deadline("Срок до 15.06.2026", reference_date=REF) == _d(2026, 6, 15)

    def test_dd_mm_yyyy_with_slashes(self):
        assert extract_deadline("К 15/06/2026", reference_date=REF) == _d(2026, 6, 15)

    def test_dd_mm_two_digit_year(self):
        assert extract_deadline("До 15.06.26", reference_date=REF) == _d(2026, 6, 15)

    def test_dd_mm_no_year_uses_current(self):
        assert extract_deadline("Срок 15.06", reference_date=REF) == _d(2026, 6, 15)

    def test_dd_mm_no_year_rolls_to_next_when_past(self):
        # Reference is 27.05.2026; «15.04» уже прошёл → берём 15.04.2027
        assert extract_deadline("Срок 15.04", reference_date=REF) == _d(2027, 4, 15)

    def test_invalid_day_returns_none(self):
        assert extract_deadline("32.06.2026", reference_date=REF) is None


class TestTextDates:
    def test_dd_month_name(self):
        assert extract_deadline("К 15 июня нужен отчёт", reference_date=REF) == _d(2026, 6, 15)

    def test_dd_month_with_year(self):
        assert extract_deadline("Сдать 1 июля 2026", reference_date=REF) == _d(2026, 7, 1)

    def test_dd_month_no_year_rolls_to_next_when_past(self):
        # 1 января явно в прошлом для 27.05.2026 → ожидаем 1.01.2027
        assert extract_deadline("Поручение к 1 января", reference_date=REF) == _d(2027, 1, 1)

    def test_genitive_month_endings(self):
        # «января», «апреля» — родительный
        for month, num in (("января", 1), ("февраля", 2), ("марта", 3), ("апреля", 4)):
            d = extract_deadline(f"К 10 {month}", reference_date=REF)
            # 10 января 2027 (rolls), 10 февраля 2027, 10 марта 2027, 10 апреля 2027
            assert d is not None and d.month == num


# ----------------------------------------------------------------------
# Относительные даты
# ----------------------------------------------------------------------


class TestRelativeDates:
    def test_today(self):
        assert extract_deadline("Нужно сегодня", reference_date=REF) == _d(2026, 5, 27)

    def test_tomorrow(self):
        assert extract_deadline("Срок завтра", reference_date=REF) == _d(2026, 5, 28)

    def test_day_after_tomorrow(self):
        assert extract_deadline("К послезавтра", reference_date=REF) == _d(2026, 5, 29)

    def test_through_n_days(self):
        assert extract_deadline("Через 5 дней", reference_date=REF) == _d(2026, 6, 1)

    def test_through_n_weeks(self):
        assert extract_deadline("Через 2 недели", reference_date=REF) == _d(2026, 6, 10)

    def test_through_n_months(self):
        # 30-дневная аппроксимация
        result = extract_deadline("Через 1 месяц", reference_date=REF)
        assert result == _d(2026, 6, 26)

    def test_through_one_week(self):
        assert extract_deadline("Через неделю", reference_date=REF) == _d(2026, 6, 3)


# ----------------------------------------------------------------------
# Концы периодов и дни недели
# ----------------------------------------------------------------------


class TestPeriodEnds:
    def test_end_of_week(self):
        # Ref среда 27.05.2026; конец недели = воскресенье 31.05
        assert extract_deadline("До конца недели", reference_date=REF) == _d(2026, 5, 31)

    def test_end_of_month(self):
        # Конец мая 2026 = 31.05
        assert extract_deadline("Сдать до конца месяца", reference_date=REF) == _d(2026, 5, 31)

    def test_end_of_year(self):
        assert extract_deadline("До конца года", reference_date=REF) == _d(2026, 12, 31)

    def test_this_week(self):
        assert extract_deadline("На этой неделе", reference_date=REF) == _d(2026, 5, 31)

    def test_next_week(self):
        # След неделя: 1.06 → 7.06
        assert extract_deadline("На следующей неделе", reference_date=REF) == _d(2026, 6, 7)

    def test_this_month(self):
        assert extract_deadline("В этом месяце", reference_date=REF) == _d(2026, 5, 31)

    def test_next_month(self):
        # Конец июня 2026 = 30.06
        assert extract_deadline("В следующем месяце", reference_date=REF) == _d(2026, 6, 30)


class TestWeekdayNames:
    def test_until_friday(self):
        # Ref среда → ближайшая пятница 29.05
        assert extract_deadline("До пятницы", reference_date=REF) == _d(2026, 5, 29)

    def test_until_monday(self):
        # Ref среда → ближайший понедельник 1.06
        assert extract_deadline("К понедельнику", reference_date=REF) == _d(2026, 6, 1)

    def test_until_same_weekday_rolls_to_next_week(self):
        # Ref среда → «до среды» = следующая среда 3.06
        assert extract_deadline("До среды", reference_date=REF) == _d(2026, 6, 3)


# ----------------------------------------------------------------------
# Trigger words и приоритет
# ----------------------------------------------------------------------


class TestTriggerWords:
    def test_trigger_finds_nearest_date(self):
        text = "Заметка про прошлое: 1.01.2025. А срок — до 5 июня."
        assert extract_deadline(text, reference_date=REF) == _d(2026, 6, 5)

    def test_no_trigger_falls_back_to_first_future_date(self):
        text = "Встретимся 10 июня где-нибудь"
        assert extract_deadline(text, reference_date=REF) == _d(2026, 6, 10)

    def test_past_dates_filtered_out(self):
        text = "Письмо от 1 января, обсуждаем планы"
        # Все даты в прошлом, нет будущих → None? Нет: «1 января» по логике
        # rolls в следующий год. Тест проверяет что extractor возвращает
        # какую-то будущую дату либо None.
        result = extract_deadline(text, reference_date=REF)
        # Допустимы оба исхода (зависит от парсинга «1 января» как trigger-цели).
        assert result is None or result >= REF.replace(hour=0, minute=0, second=0)


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_text(self):
        assert extract_deadline("", reference_date=REF) is None

    def test_none_text(self):
        assert extract_deadline(None, reference_date=REF) is None  # type: ignore[arg-type]

    def test_no_date_anywhere(self):
        assert extract_deadline("Привет, как дела?", reference_date=REF) is None

    def test_only_past_dates_returns_none(self):
        # «1 января 2020» — год явно указан, не rolls
        text = "Старое письмо от 1 января 2020"
        assert extract_deadline(text, reference_date=REF) is None

    def test_naive_reference_treated_as_utc(self):
        naive_ref = datetime(2026, 5, 27, 10, 0)  # без tzinfo
        result = extract_deadline("Завтра", reference_date=naive_ref)
        assert result == _d(2026, 5, 28)

    def test_no_reference_uses_now(self):
        # Sanity: вызов без reference_date не падает
        result = extract_deadline("Через 100 лет")
        # «через 100 лет» не матчится regex (только дн/недел/месяц).
        # Главное — нет исключения.
        assert result is None or isinstance(result, datetime)


# ----------------------------------------------------------------------
# Horizon evaluation
# ----------------------------------------------------------------------


class TestHorizons:
    def test_any_horizon_returns_none(self):
        assert horizon_end("any", now=REF) is None

    def test_today_ends_at_eod(self):
        end = horizon_end("today", now=REF)
        assert end is not None
        assert end.date() == REF.date()
        assert end.hour == 23

    def test_this_week_ends_sunday(self):
        end = horizon_end("this_week", now=REF)
        # Ref среда 27.05 → воскресенье 31.05
        assert end is not None and end.date() == REF.date().replace(day=31)

    def test_next_week_ends_following_sunday(self):
        end = horizon_end("next_week", now=REF)
        # Следующее воскресенье 7.06
        assert end is not None and end.day == 7 and end.month == 6

    def test_this_month_ends_last_day(self):
        end = horizon_end("this_month", now=REF)
        # Май = 31 день
        assert end is not None and end.day == 31 and end.month == 5

    def test_next_month_ends_last_day(self):
        end = horizon_end("next_month", now=REF)
        # Июнь = 30 дней
        assert end is not None and end.day == 30 and end.month == 6

    def test_unknown_horizon_returns_none(self):
        assert horizon_end("bogus", now=REF) is None


class TestFitsHorizon:
    def test_any_horizon_always_fits(self):
        assert fits_horizon(None, "any", now=REF) is True
        assert fits_horizon(_d(2030, 1, 1), "any", now=REF) is True

    def test_none_deadline_fails_when_horizon_set(self):
        assert fits_horizon(None, "today", now=REF) is False
        assert fits_horizon(None, "this_week", now=REF) is False

    def test_deadline_within_horizon_fits(self):
        # Дедлайн 29.05 (пятница) → попадает в this_week (до 31.05)
        assert fits_horizon(_d(2026, 5, 29), "this_week", now=REF) is True

    def test_deadline_outside_horizon_does_not_fit(self):
        # Дедлайн 10.06 → НЕ попадает в this_week (которая до 31.05)
        assert fits_horizon(_d(2026, 6, 10), "this_week", now=REF) is False

    def test_deadline_today_fits_today_horizon(self):
        assert fits_horizon(REF, "today", now=REF) is True

    def test_deadline_tomorrow_does_not_fit_today_horizon(self):
        assert fits_horizon(_d(2026, 5, 28), "today", now=REF) is False

    def test_deadline_in_next_week_fits_next_week_horizon(self):
        assert fits_horizon(_d(2026, 6, 3), "next_week", now=REF) is True

    def test_empty_horizon_string_treated_as_any(self):
        assert fits_horizon(None, "", now=REF) is True


# ----------------------------------------------------------------------
# Все horizon-значения валидны
# ----------------------------------------------------------------------


def test_all_documented_horizons_are_listed():
    assert set(DEADLINE_HORIZONS) == {
        "any", "today", "this_week", "this_month", "next_week", "next_month",
    }
