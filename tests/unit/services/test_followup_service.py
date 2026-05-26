"""Unit tests for follow-up detection (pure logic + tmp vault scan)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from personal_assistant.services.followup_service import (
    _age_days,
    _wants_reply,
    detect_followup_needed,
    enrich_with_followup,
    has_outgoing_in_thread,
)


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


def test_wants_reply_from_extraction():
    assert _wants_reply({"extraction": {"reply_required": True}}) is True
    assert _wants_reply({"extraction": {"intent": "request"}}) is True
    assert _wants_reply({"extraction": {"intent": "question"}}) is True
    assert _wants_reply({"extraction": {"intent": "info"}}) is False


def test_wants_reply_tag_fallback():
    assert _wants_reply({"tags_raw": ["urgency:urgent"]}) is True
    assert _wants_reply({"tags_raw": ["newsletter"]}) is False


def test_age_days():
    assert _age_days(_days_ago(3)) == 3
    assert _age_days(None) == 0


def test_detect_followup_filters():
    items = [
        {"id": "1", "type": "email", "date": _days_ago(5), "extraction": {"reply_required": True}},
        {"id": "2", "type": "email", "date": _days_ago(5), "extraction": {"intent": "info"}},
        {"id": "3", "type": "email", "date": _days_ago(0), "extraction": {"reply_required": True}},
        {"id": "4", "type": "meeting", "date": _days_ago(5), "extraction": {"reply_required": True}},
    ]
    assert detect_followup_needed(items, threshold_days=2) == ["1"]


def test_enrich_with_followup_flag():
    items = [
        {"id": "1", "type": "email", "date": _days_ago(5), "extraction": {"reply_required": True}},
        {"id": "2", "type": "email", "date": _days_ago(0), "extraction": {"reply_required": True}},
    ]
    out = enrich_with_followup(items, threshold_days=2)
    assert out[0]["followup_needed"] is True
    assert out[1]["followup_needed"] is False


def test_has_outgoing_in_thread(tmp_path):
    mail = tmp_path / "mail"
    mail.mkdir()
    (mail / "msg.md").write_text(
        '---\nthread_id: "T1"\nfrom: me@corp.com\n---\nbody\n', encoding="utf-8"
    )
    assert has_outgoing_in_thread("T1", tmp_path, "me@corp.com") is True
    assert has_outgoing_in_thread("T1", tmp_path, "other@corp.com") is False
    assert has_outgoing_in_thread("T1", tmp_path, "") is False  # no email -> can't tell


def test_detect_skips_when_already_replied(tmp_path):
    mail = tmp_path / "mail"
    mail.mkdir()
    (mail / "msg.md").write_text(
        '---\nthread_id: "T1"\nfrom: me@corp.com\n---\n', encoding="utf-8"
    )
    items = [
        {
            "id": "1", "type": "email", "date": _days_ago(5),
            "thread_id": "T1", "extraction": {"reply_required": True},
        }
    ]
    flagged = detect_followup_needed(
        items, vault_path=tmp_path, my_email="me@corp.com", threshold_days=2
    )
    assert flagged == []
