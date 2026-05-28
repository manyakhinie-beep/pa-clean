"""
Unit tests for ``inbox_rules_service`` — verify that user-defined GTD and
structured rules drive the Срочно / Важно / Ответить filters in Inbox.

These tests pin down the contract reported as a bug by the user:
"правила для Срочно-Важно-Ответить должны работать в Inbox".

Coverage:
  * Eisenhower quadrant mapping (q1 / q2 / q3 / q4)
  * Structured rule: keyword match → is_urgent / is_important set
  * Structured rule: action_type EXECUTE → followup_needed
  * Structured rule: tags containing "followup"/"ответить" → followup_needed
  * Structured rule: contact-only match (no keywords)
  * GTD rule: keyword + quadrant → flags
  * GTD rule: action contains "ответить" → followup_needed
  * Rule tags are merged into item.tags
  * No-op when no rules configured
  * Existing True flags are preserved (OR-combine, never demote)
  * Disabled structured rule is skipped
  * Malformed user data does not crash the inbox
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_assistant.services.inbox_rules_service import (
    _flags_from_quadrant,
    apply_rules_to_item,
    apply_rules_to_items,
    load_gtd_rules,
    load_structured_rules,
)
from personal_assistant.services.rule_engine import (
    ActionType,
    EisenhowerQuadrant,
    Rule,
)


# ----------------------------------------------------------------------
# Quadrant mapping
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "quadrant,expected",
    [
        ("q1", (True, True)),
        ("q2", (False, True)),
        ("q3", (True, False)),
        ("q4", (False, False)),
        ("",   (False, False)),
        ("Q1", (True, True)),   # case-insensitive
    ],
)
def test_flags_from_quadrant(quadrant, expected):
    assert _flags_from_quadrant(quadrant) == expected


# ----------------------------------------------------------------------
# Structured rules
# ----------------------------------------------------------------------


def _item(**overrides) -> dict:
    base = {
        "id": "m1",
        "subject": "Привет",
        "sender_name": "Иван Иванов",
        "sender_email": "ivanov@example.com",
        "sender_role": "",
        "preview": "Привет, поговорим завтра?",
        "tags": [],
        "is_urgent": False,
        "is_important": False,
        "followup_needed": False,
    }
    base.update(overrides)
    return base


def test_structured_rule_q1_sets_urgent_and_important():
    rule = Rule(
        name="Срочно от босса",
        keywords=["срочно"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        action_type=ActionType.EXECUTE,
        tags=["q1"],
    )
    it = _item(subject="Срочно: бюджет на завтра")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True
    # action_type EXECUTE → also flag followup
    assert it["followup_needed"] is True


def test_structured_rule_q2_sets_important_only():
    rule = Rule(
        name="Контракты",
        keywords=["контракт"],
        eisenhower_quadrant=EisenhowerQuadrant.Q2,
        action_type=ActionType.SCHEDULE,
    )
    it = _item(subject="Контракт на 2026 год")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is False
    assert it["is_important"] is True
    assert it["followup_needed"] is False


def test_structured_rule_q3_sets_urgent_only():
    rule = Rule(
        name="Заявка на согласование",
        keywords=["согласовать"],
        eisenhower_quadrant=EisenhowerQuadrant.Q3,
        action_type=ActionType.DELEGATE,
    )
    it = _item(subject="Прошу согласовать счёт")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is False


def test_structured_rule_q4_no_flags():
    rule = Rule(
        name="Промо",
        keywords=["скидк"],
        eisenhower_quadrant=EisenhowerQuadrant.Q4,
        action_type=ActionType.SKIP,
    )
    it = _item(subject="Скидки до 50%")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is False
    assert it["is_important"] is False


def test_structured_rule_followup_tag_sets_followup():
    rule = Rule(
        name="Reply required",
        keywords=["ответить"],
        eisenhower_quadrant=EisenhowerQuadrant.Q2,
        action_type=ActionType.INFO,
        tags=["ответить"],
    )
    it = _item(subject="Прошу ответить до пятницы")
    apply_rules_to_item(it, [rule], [])
    assert it["followup_needed"] is True


def test_structured_rule_contact_only_match():
    rule = Rule(
        name="From boss",
        contacts=["boss@example.com"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        action_type=ActionType.EXECUTE,
    )
    it = _item(subject="Привет", sender_email="boss@example.com")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True


def test_structured_rule_disabled_skipped():
    rule = Rule(
        name="Disabled",
        keywords=["срочно"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        enabled=False,
    )
    it = _item(subject="Срочно!")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is False


def test_structured_rule_tags_merged_into_item():
    rule = Rule(
        name="Finance",
        keywords=["счёт"],
        eisenhower_quadrant=EisenhowerQuadrant.Q2,
        tags=["финансы", "category:finance"],
    )
    it = _item(subject="Счёт от подрядчика", tags=[{"label": "почта", "cls": "section"}])
    apply_rules_to_item(it, [rule], [])
    labels = [t.get("label") if isinstance(t, dict) else t for t in it["tags"]]
    assert "финансы" in labels
    assert "category:finance" in labels
    # Original tag preserved
    assert "почта" in labels


def test_structured_rule_matched_rules_recorded():
    rule = Rule(
        id="r123",
        name="Boss",
        keywords=["бюджет"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        action_type=ActionType.EXECUTE,
    )
    it = _item(subject="Бюджет на Q3")
    apply_rules_to_item(it, [rule], [])
    assert it["matched_rules"]
    assert it["matched_rules"][0]["id"] == "r123"
    assert it["matched_rules"][0]["quadrant"] == "q1"


# ----------------------------------------------------------------------
# GTD rules
# ----------------------------------------------------------------------


def test_gtd_rule_keyword_match_q1():
    gtd = [{"id": "g1", "keyword": "срочно", "action": "inbox", "quadrant": "q1"}]
    it = _item(subject="Срочно нужен ответ")
    apply_rules_to_item(it, [], gtd)
    assert it["is_urgent"] is True
    assert it["is_important"] is True


def test_gtd_rule_action_followup_sets_followup_needed():
    gtd = [{"id": "g2", "keyword": "встреча", "action": "ответить", "quadrant": "q2"}]
    it = _item(subject="Встреча по проекту X")
    apply_rules_to_item(it, [], gtd)
    assert it["followup_needed"] is True
    assert it["is_important"] is True


def test_gtd_rule_empty_keyword_skipped():
    gtd = [{"id": "g3", "keyword": "", "action": "inbox", "quadrant": "q1"}]
    it = _item(subject="Срочно")
    apply_rules_to_item(it, [], gtd)
    assert it["is_urgent"] is False


def test_gtd_rule_no_match():
    gtd = [{"id": "g4", "keyword": "налоги", "action": "inbox", "quadrant": "q1"}]
    it = _item(subject="Привет")
    apply_rules_to_item(it, [], gtd)
    assert it["is_urgent"] is False
    assert it["is_important"] is False


# ----------------------------------------------------------------------
# Combined / preservation semantics
# ----------------------------------------------------------------------


def test_existing_true_flags_preserved():
    """Tag-based detection runs first; rule application must not demote it."""
    it = _item(subject="Hi", is_urgent=True, is_important=True)
    # A q4 rule should NOT clear urgent/important flags
    rule = Rule(keywords=["hi"], eisenhower_quadrant=EisenhowerQuadrant.Q4)
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True


def test_no_rules_is_noop():
    it = _item(subject="Срочно!")
    before = it.copy()
    apply_rules_to_item(it, [], [])
    assert it == before


def test_apply_rules_to_items_handles_empty_list():
    assert apply_rules_to_items([]) == []


def test_apply_rules_to_items_with_no_configured_rules(tmp_path, monkeypatch):
    # When data/rules.json + data/gtd_rules.json are missing/empty, must no-op.
    monkeypatch.setattr(
        "personal_assistant.services.inbox_rules_service._project_root",
        lambda: tmp_path,
    )
    items = [_item(subject="anything")]
    result = apply_rules_to_items(items)
    assert result[0]["is_urgent"] is False
    assert result[0]["is_important"] is False


def test_malformed_rules_do_not_crash(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "rules.json").write_text("[{\"this\": \"is bogus\"}]")
    (tmp_path / "data" / "gtd_rules.json").write_text('{"rules": "not a list"}')
    monkeypatch.setattr(
        "personal_assistant.services.inbox_rules_service._project_root",
        lambda: tmp_path,
    )
    items = [_item(subject="x")]
    # Must not raise; flags remain False
    apply_rules_to_items(items)
    assert items[0]["is_urgent"] is False


# ----------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------


def test_load_structured_rules_from_file(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    payload = [
        {
            "id": "abc",
            "name": "test",
            "keywords": ["urgent"],
            "eisenhower_quadrant": "q1",
            "action_type": "execute",
            "priority": 10,
            "tags": ["alpha"],
            "enabled": True,
        }
    ]
    (tmp_path / "data" / "rules.json").write_text(json.dumps(payload))
    monkeypatch.setattr(
        "personal_assistant.services.inbox_rules_service._project_root",
        lambda: tmp_path,
    )
    rules = load_structured_rules()
    assert len(rules) == 1
    assert rules[0].name == "test"
    assert rules[0].eisenhower_quadrant == EisenhowerQuadrant.Q1


def test_load_gtd_rules_dict_format(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "gtd_rules.json").write_text(
        json.dumps({"rules": [
            {"id": "1", "keyword": "tax", "action": "next", "quadrant": "q2"},
            {"id": "2", "keyword": "", "action": "inbox", "quadrant": "q4"},
        ]})
    )
    monkeypatch.setattr(
        "personal_assistant.services.inbox_rules_service._project_root",
        lambda: tmp_path,
    )
    rules = load_gtd_rules()
    # Empty-keyword rule filtered out
    assert len(rules) == 1
    assert rules[0]["keyword"] == "tax"


def test_load_gtd_rules_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "personal_assistant.services.inbox_rules_service._project_root",
        lambda: tmp_path,
    )
    assert load_gtd_rules() == []


# ----------------------------------------------------------------------
# Regression: keywords must match against the email *body*, not just the
# 180-char preview.  Reported by the user as «добавление в Правила и
# применение сейчас не работает» — long emails where the keyword sits in
# paragraph 2+ never triggered the rule.
# ----------------------------------------------------------------------


def test_structured_rule_matches_keyword_in_body():
    rule = Rule(
        name="Финансы",
        keywords=["счёт"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
        action_type=ActionType.EXECUTE,
    )
    # Preview deliberately does NOT contain the keyword.
    it = _item(
        subject="Финансовый отчёт",
        preview="Здравствуйте, направляю отчёт за май.",
        body=(
            "Здравствуйте, направляю отчёт за май.\n\n"
            "В приложении вы найдёте счёт на оплату услуг по договору № 1234.\n"
            "Срок оплаты — 5 рабочих дней."
        ),
    )
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True


def test_gtd_rule_matches_keyword_in_body():
    gtd = [{"id": "g1", "keyword": "договор", "action": "ответить", "quadrant": "q2"}]
    it = _item(
        subject="Документы",
        preview="Прилагаю необходимые материалы.",
        body=(
            "Прилагаю необходимые материалы.\n"
            "Прошу подписать договор и вернуть скан до конца недели."
        ),
    )
    apply_rules_to_item(it, [], gtd)
    assert it["is_important"] is True
    assert it["followup_needed"] is True


def test_body_truncated_to_scan_limit():
    """Keywords past ~4 KB of body are intentionally ignored to keep the
    in-memory scan bounded on very large MIME blobs."""
    from personal_assistant.services.inbox_rules_service import _BODY_SCAN_LIMIT

    rule = Rule(
        name="Late keyword",
        keywords=["налоги"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
    )
    padding = "x" * (_BODY_SCAN_LIMIT + 200)
    it = _item(subject="Привет", preview="нет совпадения", body=padding + " налоги")
    apply_rules_to_item(it, [rule], [])
    # Keyword sits after the scan limit → must NOT match.
    assert it["is_urgent"] is False
    assert it["is_important"] is False


def test_body_keyword_within_scan_limit_matches():
    from personal_assistant.services.inbox_rules_service import _BODY_SCAN_LIMIT

    rule = Rule(
        name="Within limit",
        keywords=["налоги"],
        eisenhower_quadrant=EisenhowerQuadrant.Q1,
    )
    # Place the keyword JUST under the limit.
    padding = "y" * (_BODY_SCAN_LIMIT - 20)
    it = _item(subject="Привет", preview="нет совпадения", body=padding + " налоги")
    apply_rules_to_item(it, [rule], [])
    assert it["is_urgent"] is True
    assert it["is_important"] is True
