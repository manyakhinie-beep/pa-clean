"""Unit tests for the Eisenhower/GTD rule engine (pure logic, no MLX/Apple)."""

from __future__ import annotations

import pytest

from personal_assistant.services import rule_engine as re_mod
from personal_assistant.services.rule_engine import (
    ActionType,
    EisenhowerQuadrant,
    Rule,
    classify_item,
    load_rules,
    save_rules,
)


def test_classify_no_rules_returns_unclassified():
    r = classify_item("anything at all")
    assert r.matched_rule_id is None
    assert r.matched_rule_name == "Unclassified"
    assert r.eisenhower_quadrant == EisenhowerQuadrant.Q2
    assert r.action_type == ActionType.INFO
    assert r.score == 0


def test_classify_keyword_match_returns_rule_fields():
    rules = [
        Rule(
            name="Invoices",
            keywords=["счёт", "invoice"],
            eisenhower_quadrant=EisenhowerQuadrant.Q1,
            action_type=ActionType.EXECUTE,
            tags=["finance"],
        )
    ]
    r = classify_item("Просьба оплатить INVOICE #5", rules=rules)
    assert r.matched_rule_name == "Invoices"
    assert r.eisenhower_quadrant == EisenhowerQuadrant.Q1
    assert r.action_type == ActionType.EXECUTE
    assert r.tags == ["finance"]
    assert r.score == 1


def test_classify_is_case_insensitive():
    rules = [Rule(name="K", keywords=["ASAP"])]
    assert classify_item("please reply asap", rules=rules).matched_rule_name == "K"


def test_classify_contact_match_case_insensitive():
    rules = [Rule(name="Boss", contacts=["boss@corp.com"])]
    r = classify_item("hi", contacts_found=["Boss@Corp.com"], rules=rules)
    assert r.matched_rule_name == "Boss"


def test_classify_requires_both_keyword_and_contact():
    rules = [Rule(name="Both", keywords=["urgent"], contacts=["boss@corp.com"])]
    # keyword present but wrong contact -> no match
    assert classify_item("urgent", contacts_found=["x@y.com"], rules=rules).matched_rule_id is None
    # both present -> match
    assert classify_item("urgent", contacts_found=["boss@corp.com"], rules=rules).matched_rule_name == "Both"


def test_classify_priority_lower_wins():
    low = Rule(name="low", keywords=["x"], priority=200)
    high = Rule(name="high", keywords=["x"], priority=10)
    assert classify_item("x marks it", rules=[low, high]).matched_rule_name == "high"


def test_classify_disabled_rule_skipped():
    rules = [Rule(name="off", keywords=["x"], enabled=False)]
    assert classify_item("x", rules=rules).matched_rule_id is None


@pytest.mark.parametrize("bad_priority", [0, 1000])
def test_rule_priority_validation(bad_priority):
    with pytest.raises(Exception):
        Rule(priority=bad_priority)


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod, "_RULES_FILE", tmp_path / "rules.json")
    rules = [
        Rule(name="A", keywords=["a"], tags=["t1"]),
        Rule(name="B", contacts=["b@x.com"], action_type=ActionType.DELEGATE),
    ]
    save_rules(rules)
    loaded = load_rules()
    assert [r.name for r in loaded] == ["A", "B"]
    assert loaded[0].keywords == ["a"]
    assert loaded[1].action_type == ActionType.DELEGATE


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod, "_RULES_FILE", tmp_path / "nope.json")
    assert load_rules() == []


def test_load_corrupt_file_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "rules.json"
    f.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(re_mod, "_RULES_FILE", f)
    assert load_rules() == []
