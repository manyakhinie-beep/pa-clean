"""Unit tests for tag_history_service (file-backed, pure; no MLX/Apple)."""

from __future__ import annotations

import pytest

from personal_assistant.services import tag_history_service as th
from personal_assistant.services.tag_history_service import (
    TagChange,
    clear_history,
    delete_change,
    list_changes,
    record_change,
)


@pytest.fixture
def hist(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "_HISTORY_FILE", tmp_path / "tag_history.json")
    return tmp_path


def test_record_and_list(hist):
    c = record_change("item1", "", "urgent", section="mail", changed_by="user")
    assert isinstance(c, TagChange)
    rows = list_changes()
    assert len(rows) == 1
    assert rows[0].item_id == "item1"
    assert rows[0].new_value == "urgent"
    assert rows[0].section == "mail"


def test_list_filters_by_item_and_section(hist):
    record_change("a", "", "x", section="mail")
    record_change("b", "", "y", section="calendar")
    assert [r.item_id for r in list_changes(item_id="a")] == ["a"]
    assert [r.item_id for r in list_changes(section="calendar")] == ["b"]


def test_list_date_range(hist):
    record_change("a", "", "x")  # changed_at ~ now (2026)
    assert len(list_changes(since="2000-01-01")) == 1
    assert len(list_changes(until="2000-01-01")) == 0


def test_list_limit(hist):
    for i in range(5):
        record_change(f"i{i}", "", str(i))
    assert len(list_changes(limit=3)) == 3


def test_delete_change(hist):
    c = record_change("a", "", "x")
    assert delete_change(c.id) is True
    assert delete_change("does-not-exist") is False
    assert list_changes() == []


def test_clear_history_by_item_then_all(hist):
    record_change("a", "", "x")
    record_change("b", "", "y")
    assert clear_history(item_id="a") == 1
    assert [r.item_id for r in list_changes()] == ["b"]
    assert clear_history() == 1
    assert list_changes() == []


def test_corrupt_history_is_ignored(hist):
    (hist / "tag_history.json").write_text("{ not json", encoding="utf-8")
    assert list_changes() == []
