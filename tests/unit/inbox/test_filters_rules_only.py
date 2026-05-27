"""
Inbox filters (Срочно / Важно) must be driven **only** by Правила →
GTD-правила + структурные правила.

This pins the contract the user pinned down explicitly:
«Кнопки 'Срочно', 'Важно' должны управляться только на основании
'Правила' - 'GTD-правила'».

Coverage:
  * Item tagged ``urgency:critical`` BUT no matching rule → not urgent
  * Item tagged ``важно`` BUT no matching rule → not important
  * Item with no tags but a structured rule matches → flags set by rules
  * GTD-rule (simple) match also sets the flags
  * Legacy env-var ``PA_INBOX_TAG_URGENCY_ENABLED=true`` restores the
    union-with-tags behaviour
  * ``meeting`` content-type detection stays tag-driven (it's a type,
    not a priority signal — must not regress)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _doc_stub(tags: list[str], section: str = "mail", subject: str = "Hi"):
    """Build a minimal vault doc compatible with ``_doc_to_item``.

    Real VaultDoc has many properties / accessors that ``_doc_to_item``
    touches.  We use a plain class instead of MagicMock so unset
    attributes raise (catching new code paths) rather than silently
    returning a MagicMock that later trips ``re.sub``.
    """
    class _Doc:
        pass
    d = _Doc()
    d.frontmatter = {
        "id": "msg1",
        "subject": subject,
        "sender": "Alice <alice@example.com>",
        "sender_name": "Alice",
        "from": "alice@example.com",
        "tags": list(tags),
    }
    d.tags = list(tags)
    d.section = section
    d.path = Path(f"/tmp/{section}/msg1.md")
    d.date = "2026-05-26T12:00:00+0000"
    d.content = "Body of the message."
    d.sender_email = "alice@example.com"
    d.ui_preview = lambda n=180: "Preview body."
    d.short_summary = lambda n=300: "Summary."
    return d


def _call_doc_to_item(doc, state: dict | None = None):
    from personal_assistant.inbox.routes import _doc_to_item
    return _doc_to_item(doc, state or {})


# ----------------------------------------------------------------------
# Default (flag off): tags alone don't trigger Срочно / Важно
# ----------------------------------------------------------------------


def test_urgency_critical_tag_does_not_mark_urgent_by_default(monkeypatch):
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)
    item = _call_doc_to_item(_doc_stub(["urgency:critical"]))
    assert item["is_urgent"] is False, (
        "without a matching rule, classifier tag must NOT auto-flag urgent"
    )


def test_vazhno_tag_does_not_mark_important_by_default(monkeypatch):
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)
    item = _call_doc_to_item(_doc_stub(["важно"]))
    assert item["is_important"] is False


def test_finance_tag_does_not_auto_mark_important_by_default(monkeypatch):
    """Classifier-derived `category:finance` was a long-standing
    auto-importance signal. The new contract: only rules matter."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)
    item = _call_doc_to_item(_doc_stub(["category:finance"]))
    assert item["is_important"] is False


# ----------------------------------------------------------------------
# Legacy env-var restores tag-set behaviour
# ----------------------------------------------------------------------


def test_legacy_flag_restores_tag_based_urgency(monkeypatch):
    """``PA_INBOX_TAG_URGENCY_ENABLED=true`` — back to the historic
    union-with-tags behaviour for users who depended on it."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", True)
    item = _call_doc_to_item(_doc_stub(["urgency:critical"]))
    assert item["is_urgent"] is True


def test_legacy_flag_restores_tag_based_importance(monkeypatch):
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", True)
    item = _call_doc_to_item(_doc_stub(["важно"]))
    assert item["is_important"] is True


# ----------------------------------------------------------------------
# Rule-derived path: rules supply the flags (unchanged from F17)
# ----------------------------------------------------------------------


def test_gtd_keyword_rule_marks_urgent(monkeypatch):
    """Simple GTD keyword rule → quadrant q1 → is_urgent+is_important.
    Reuses the existing inbox_rules_service contract, just verifying
    that flags don't depend on legacy tag-set."""
    from personal_assistant.config import settings
    from personal_assistant.services.inbox_rules_service import (
        apply_rules_to_item,
    )
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)

    item = _call_doc_to_item(_doc_stub(
        tags=[],
        subject="Срочный отчёт по бюджету Q3",
    ))
    # Before rules: clean state
    assert item["is_urgent"] is False
    assert item["is_important"] is False

    # Apply a single GTD rule that matches the subject
    apply_rules_to_item(
        item,
        structured_rules=[],
        gtd_rules=[{"id": "g1", "keyword": "срочный", "action": "inbox", "quadrant": "q1"}],
    )
    assert item["is_urgent"] is True
    assert item["is_important"] is True


def test_structured_rule_marks_important(monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.services.inbox_rules_service import apply_rules_to_item
    from personal_assistant.services.rule_engine import (
        ActionType,
        EisenhowerQuadrant,
        Rule,
    )
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)

    item = _call_doc_to_item(_doc_stub(
        tags=[],
        subject="Контракт на согласование",
    ))
    apply_rules_to_item(
        item,
        structured_rules=[Rule(
            name="contracts",
            keywords=["контракт"],
            eisenhower_quadrant=EisenhowerQuadrant.Q2,
            action_type=ActionType.SCHEDULE,
        )],
        gtd_rules=[],
    )
    assert item["is_urgent"] is False
    assert item["is_important"] is True


def test_no_rules_no_tags_means_no_flags(monkeypatch):
    """Clean baseline: no rules + no tags → both flags False, doesn't
    accidentally pick up signals from elsewhere."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)
    item = _call_doc_to_item(_doc_stub(tags=[]))
    assert item["is_urgent"] is False
    assert item["is_important"] is False


# ----------------------------------------------------------------------
# Meeting type detection stays tag-driven
# ----------------------------------------------------------------------


def test_meeting_type_still_tag_driven_for_calendar_section(monkeypatch):
    """The Mail / События filter is a content-type signal, not priority.
    Must NOT regress when we disable the urgency tag-set check."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)
    item = _call_doc_to_item(_doc_stub(tags=["meeting"], section="calendar"))
    assert item["type"] == "meeting"


def test_meeting_tag_in_mail_section_marks_meeting(monkeypatch):
    """A mail item explicitly tagged ``встреча`` still surfaces as
    meeting (content-type), independent of urgency rules."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "inbox_tag_urgency_enabled", False)
    item = _call_doc_to_item(_doc_stub(tags=["встреча"], section="mail"))
    assert item["type"] == "meeting"
