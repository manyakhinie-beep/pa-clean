"""
End-to-end tests: GTD-правило с deadline_horizon фильтрует Inbox по
сроку, извлечённому из текста письма.

Сценарий из спеки пользователя:
  «поручение мне со сроком через две недели → правило для «срочно»
   с horizon=this_month включается только если deadline ∈ этот месяц»

Покрытие:
  * extracted_deadline появляется в item после apply_rules_to_item
  * horizon=any (default) — не фильтрует, поведение как раньше
  * structured rule с horizon=this_week срабатывает на письме с
    «до пятницы» и НЕ срабатывает на письме «через 2 месяца»
  * GTD-правило с horizon=today срабатывает только на письмах
    с «сегодня» в тексте
  * Письмо без явного срока → правило с horizon ≠ any не матчит
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import patch

import pytest

from personal_assistant.services.inbox_rules_service import apply_rules_to_item
from personal_assistant.services.rule_engine import (
    ActionType,
    EisenhowerQuadrant,
    Rule,
)

# Фиксированный «сейчас» — среда 27 мая 2026.  В этой неделе пн 25 → вс 31.
NOW = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)


def _item(**overrides) -> dict:
    base = {
        "id": "m1",
        "subject": "Тест",
        "sender_name": "Иван",
        "sender_email": "ivan@example.com",
        "preview": "",
        "body": "",
        "tags": [],
        "is_urgent": False,
        "is_important": False,
        "followup_needed": False,
        # Дата письма = «сейчас» — все относительные сроки считаются от неё.
        "date": NOW.isoformat(),
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# Извлечение deadline в item
# ----------------------------------------------------------------------


def test_apply_rules_extracts_deadline_into_item():
    """`apply_rules_to_item` должен записать извлечённый срок в
    `item['deadline']` (для отображения в UI)."""
    rule = Rule(
        name="any",
        keywords=["договор"],
        eisenhower_quadrant=EisenhowerQuadrant.Q2,
        deadline_horizon="any",
    )
    it = _item(
        subject="Договор",
        body="Прошу подписать договор до 5 июня.",
    )
    apply_rules_to_item(it, [rule], [])
    assert it.get("deadline") is not None
    assert "2026-06-05" in it["deadline"]


def test_no_deadline_in_email_means_no_deadline_field():
    """Если в письме нет ни одного распознаваемого срока — поле
    `deadline` не должно появляться (или должно быть None)."""
    rule = Rule(
        name="any",
        keywords=["привет"],
        eisenhower_quadrant=EisenhowerQuadrant.Q2,
    )
    it = _item(subject="Привет", body="Просто здороваюсь, без даты.")
    apply_rules_to_item(it, [rule], [])
    assert not it.get("deadline")


# ----------------------------------------------------------------------
# horizon=any — back-compat
# ----------------------------------------------------------------------


def test_horizon_any_does_not_filter():
    """horizon=any — правило срабатывает независимо от срока."""
    rule = Rule(
        name="No filter",
        keywords=["срочно"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        action_type=ActionType.EXECUTE,
        deadline_horizon="any",
    )
    it = _item(subject="Срочно: подготовить отчёт")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True


# ----------------------------------------------------------------------
# Structured rules + horizon
# ----------------------------------------------------------------------


def test_structured_rule_horizon_this_week_matches_when_deadline_fits():
    """Письмо с deadline=пятница 29.05 → попадает в this_week → правило
    с horizon=this_week срабатывает."""
    rule = Rule(
        name="urgent",
        keywords=["счёт"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        deadline_horizon="this_week",
    )
    it = _item(subject="Счёт", body="Оплатить до пятницы.")
    with patch("personal_assistant.services.deadline_extractor.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        # Сохраняем реальный datetime для прочих использований
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True


def test_structured_rule_horizon_this_week_does_not_match_when_deadline_far():
    """Письмо с дедлайном «через 2 месяца» → НЕ попадает в this_week →
    правило не срабатывает, флаг is_urgent остаётся False."""
    rule = Rule(
        name="urgent",
        keywords=["счёт"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        deadline_horizon="this_week",
    )
    it = _item(subject="Счёт", body="Оплатить через 2 месяца.")
    with patch("personal_assistant.services.deadline_extractor.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is False


def test_structured_rule_horizon_not_any_requires_deadline():
    """Если у правила horizon=this_week, а в письме НЕТ срока —
    правило НЕ срабатывает (None deadline → fits_horizon=False)."""
    rule = Rule(
        name="urgent",
        keywords=["счёт"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        deadline_horizon="this_week",
    )
    it = _item(subject="Счёт без срока", body="Просто счёт, никакого срока.")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is False


# ----------------------------------------------------------------------
# GTD rules + horizon
# ----------------------------------------------------------------------


def test_gtd_rule_horizon_today_matches_today_deadline():
    gtd = [{
        "id": "g1",
        "keyword": "срочно",
        "action": "inbox",
        "quadrant": "q1",
        "deadline_horizon": "today",
    }]
    it = _item(subject="Срочно", body="Нужно сегодня же.")
    with patch("personal_assistant.services.deadline_extractor.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        apply_rules_to_item(it, [], gtd)
    assert it["is_urgent"] is True


def test_gtd_rule_horizon_today_does_not_match_tomorrow_deadline():
    gtd = [{
        "id": "g1",
        "keyword": "срочно",
        "action": "inbox",
        "quadrant": "q1",
        "deadline_horizon": "today",
    }]
    it = _item(subject="Срочно", body="Нужно завтра.")
    with patch("personal_assistant.services.deadline_extractor.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        apply_rules_to_item(it, [], gtd)
    assert it["is_urgent"] is False


def test_gtd_rule_horizon_missing_defaults_to_any():
    """Старый формат GTD-правила (без deadline_horizon) ведёт себя как
    horizon=any — back-compat для legacy gtd_rules.json."""
    gtd = [{"id": "g1", "keyword": "срочно", "action": "inbox", "quadrant": "q1"}]
    it = _item(subject="Срочно", body="Никакой даты не указано.")
    apply_rules_to_item(it, [], gtd)
    assert it["is_urgent"] is True


# ----------------------------------------------------------------------
# Сценарий из user spec
# ----------------------------------------------------------------------


def test_user_spec_scenario_two_week_assignment_with_this_month_rule():
    """User spec:
       «поручение мне со сроком через две недели → правило для 'срочно'
        с horizon=this_month включается».

    Reference: среда 27.05.2026; «через 2 недели» = 10.06.2026 →
    попадает в next_month (this_month=май заканчивается 31.05).
    """
    rule = Rule(
        name="Срочно",
        keywords=["поручение"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        deadline_horizon="next_month",
    )
    it = _item(
        subject="Поручение",
        body="Прошу подготовить материалы через 2 недели.",
    )
    with patch("personal_assistant.services.deadline_extractor.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True
    # Дедлайн сохранён в item
    assert "2026-06-10" in (it.get("deadline") or "")
