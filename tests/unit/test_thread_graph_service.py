"""
Unit tests for thread_graph_service.

Tests cover:
  - Basic graph construction from vault docs
  - Participant role assignment (initiator / responder / observer)
  - my_turn detection (last sender ≠ me)
  - days_without_reply calculation
  - Timeline ordering
  - Empty / single-message thread edge cases
  - CC-only observer detection
  - Graceful handling of missing email fields
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

from personal_assistant.services.thread_graph_service import (
    build_thread_graph,
    graph_to_dict,
)

# ---------------------------------------------------------------------------
# Helpers — minimal VaultDoc stubs
# ---------------------------------------------------------------------------

def _make_doc(
    thread_id: str,
    sender_email: str,
    sender_name: str = "",
    date: str = "2026-05-20T10:00:00+03:00",
    recipients: Optional[list[str]] = None,
    cc: Optional[list[str]] = None,
    subject: str = "Тестовый тред",
    section: str = "mail",
    item_id: Optional[str] = None,
) -> MagicMock:
    """Return a minimal VaultDoc-like mock."""
    doc = MagicMock()
    doc.section = section
    doc.sender_email = sender_email
    doc.date = date
    _id = item_id or f"msg_{sender_email.split('@')[0]}_{date[:10]}"
    doc.path = Path(f"/vault/mail/2026/05/{_id}.md")
    doc.frontmatter = {
        "thread_id": thread_id,
        "sender": sender_name or sender_email,
        "sender_email": sender_email,
        "subject": subject,
        "recipients": recipients or [],
        "cc": cc or [],
        "id": _id,
    }
    return doc


def _iso(offset_hours: int = 0) -> str:
    """Return ISO date string offset from a fixed base time."""
    base = datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(hours=offset_hours)).isoformat()


# ---------------------------------------------------------------------------
# Tests — basic graph building
# ---------------------------------------------------------------------------

class TestBuildThreadGraph:

    def test_returns_none_for_unknown_thread(self):
        docs = [_make_doc("thread_abc", "a@x.com")]
        result = build_thread_graph("nonexistent_id", docs)
        assert result is None

    def test_returns_none_for_empty_docs(self):
        result = build_thread_graph("any", [])
        assert result is None

    def test_single_message_graph(self):
        docs = [_make_doc("t1", "alice@x.com", "Alice")]
        g = build_thread_graph("t1", docs, my_email="me@x.com")
        assert g is not None
        assert g.message_count == 1
        assert g.thread_id == "t1"
        # my_turn=True only if last message is NOT from me, and there's a known my_email
        assert g.my_turn is True  # alice sent it, not me

    def test_ignores_calendar_docs(self):
        mail_doc = _make_doc("t1", "a@x.com")
        cal_doc  = MagicMock()
        cal_doc.section = "calendar"
        cal_doc.frontmatter = {"thread_id": "t1"}
        g = build_thread_graph("t1", [mail_doc, cal_doc])
        assert g.message_count == 1  # calendar doc excluded

    def test_message_count_correct(self):
        docs = [
            _make_doc("t1", "a@x.com", date=_iso(0)),
            _make_doc("t1", "b@x.com", date=_iso(1)),
            _make_doc("t1", "a@x.com", date=_iso(2)),
        ]
        g = build_thread_graph("t1", docs)
        assert g.message_count == 3


# ---------------------------------------------------------------------------
# Tests — participant roles
# ---------------------------------------------------------------------------

class TestParticipantRoles:

    def test_first_sender_is_initiator(self):
        docs = [
            _make_doc("t1", "alice@x.com", "Alice", date=_iso(0)),
            _make_doc("t1", "bob@x.com",   "Bob",   date=_iso(2)),
        ]
        g = build_thread_graph("t1", docs)
        assert g.initiator is not None
        assert g.initiator.email == "alice@x.com"
        assert g.initiator.role == "initiator"

    def test_last_sender_set_correctly(self):
        docs = [
            _make_doc("t1", "alice@x.com", date=_iso(0)),
            _make_doc("t1", "bob@x.com",   date=_iso(5)),
        ]
        g = build_thread_graph("t1", docs)
        assert g.last_sender is not None
        assert g.last_sender.email == "bob@x.com"

    def test_cc_only_participant_is_observer(self):
        docs = [
            _make_doc("t1", "alice@x.com", recipients=["bob@x.com"],
                      cc=["observer@x.com"], date=_iso(0)),
        ]
        g = build_thread_graph("t1", docs)
        obs = next((p for p in g.participants if p.email == "observer@x.com"), None)
        assert obs is not None
        assert obs.role == "observer"
        assert obs.messages_sent == 0

    def test_cc_participant_who_also_replies_is_not_observer(self):
        docs = [
            _make_doc("t1", "alice@x.com", cc=["charlie@x.com"], date=_iso(0)),
            _make_doc("t1", "charlie@x.com", date=_iso(1)),  # charlie replied
        ]
        g = build_thread_graph("t1", docs)
        charlie = next((p for p in g.participants if p.email == "charlie@x.com"), None)
        assert charlie is not None
        assert charlie.role != "observer"
        assert charlie.messages_sent == 1

    def test_messages_sent_count(self):
        docs = [
            _make_doc("t1", "alice@x.com", date=_iso(0)),
            _make_doc("t1", "alice@x.com", date=_iso(2)),
            _make_doc("t1", "bob@x.com",   date=_iso(4)),
        ]
        g = build_thread_graph("t1", docs)
        alice = next(p for p in g.participants if p.email == "alice@x.com")
        bob   = next(p for p in g.participants if p.email == "bob@x.com")
        assert alice.messages_sent == 2
        assert bob.messages_sent == 1


# ---------------------------------------------------------------------------
# Tests — my_turn detection
# ---------------------------------------------------------------------------

class TestMyTurnDetection:

    def test_my_turn_true_when_last_sender_is_not_me(self):
        docs = [
            _make_doc("t1", "me@x.com",    date=_iso(0)),
            _make_doc("t1", "other@x.com", date=_iso(2)),  # other replied last
        ]
        g = build_thread_graph("t1", docs, my_email="me@x.com")
        assert g.my_turn is True

    def test_my_turn_false_when_i_sent_last(self):
        docs = [
            _make_doc("t1", "other@x.com", date=_iso(0)),
            _make_doc("t1", "me@x.com",    date=_iso(2)),  # I replied last
        ]
        g = build_thread_graph("t1", docs, my_email="me@x.com")
        assert g.my_turn is False

    def test_my_turn_false_when_no_my_email(self):
        docs = [
            _make_doc("t1", "other@x.com", date=_iso(0)),
        ]
        g = build_thread_graph("t1", docs, my_email="")
        assert g.my_turn is False

    def test_days_without_reply_nonzero_when_my_turn(self, monkeypatch):
        """days_without_reply should be > 0 when last message is several days ago."""
        from personal_assistant.services import thread_graph_service as svc

        # Patch datetime.now to a fixed future date
        fixed_now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)

        original_now = svc.datetime

        class _FakeDT:
            @staticmethod
            def now(**kwargs):
                return fixed_now
            @staticmethod
            def fromisoformat(s):
                return original_now.fromisoformat(s)
            min = original_now.min

        monkeypatch.setattr(svc, "datetime", _FakeDT)

        docs = [
            _make_doc("t1", "other@x.com", date="2026-05-20T10:00:00+00:00"),
        ]
        g = build_thread_graph("t1", docs, my_email="me@x.com")
        assert g.my_turn is True
        assert g.days_without_reply == 5


# ---------------------------------------------------------------------------
# Tests — timeline ordering
# ---------------------------------------------------------------------------

class TestTimeline:

    def test_timeline_is_chronological(self):
        docs = [
            _make_doc("t1", "c@x.com", date=_iso(10)),
            _make_doc("t1", "a@x.com", date=_iso(0)),
            _make_doc("t1", "b@x.com", date=_iso(5)),
        ]
        g = build_thread_graph("t1", docs)
        emails = [e.sender_email for e in g.timeline]
        assert emails == ["a@x.com", "b@x.com", "c@x.com"]

    def test_timeline_is_me_flag(self):
        docs = [
            _make_doc("t1", "other@x.com", date=_iso(0)),
            _make_doc("t1", "me@x.com",    date=_iso(1)),
        ]
        g = build_thread_graph("t1", docs, my_email="me@x.com")
        assert g.timeline[0].is_me is False
        assert g.timeline[1].is_me is True

    def test_timeline_item_id_set(self):
        docs = [_make_doc("t1", "a@x.com", item_id="msg_001")]
        g = build_thread_graph("t1", docs)
        assert g.timeline[0].item_id == "msg_001"


# ---------------------------------------------------------------------------
# Tests — subject normalisation
# ---------------------------------------------------------------------------

class TestSubjectNormalisation:

    def test_strips_reply_prefix(self):
        docs = [_make_doc("t1", "a@x.com", subject="Re: Совещание по проекту")]
        g = build_thread_graph("t1", docs)
        assert g.subject == "Совещание по проекту"

    def test_strips_forward_prefix(self):
        docs = [_make_doc("t1", "a@x.com", subject="Fwd: Счёт на оплату")]
        g = build_thread_graph("t1", docs)
        assert g.subject == "Счёт на оплату"

    def test_strips_russian_prefix(self):
        docs = [_make_doc("t1", "a@x.com", subject="Отв: Встреча завтра")]
        g = build_thread_graph("t1", docs)
        assert g.subject == "Встреча завтра"


# ---------------------------------------------------------------------------
# Tests — graph_to_dict serialization
# ---------------------------------------------------------------------------

class TestGraphToDict:

    def test_serializes_all_top_level_keys(self):
        docs = [
            _make_doc("t1", "a@x.com", "Alice", date=_iso(0)),
            _make_doc("t1", "b@x.com", "Bob",   date=_iso(2)),
        ]
        g = build_thread_graph("t1", docs, my_email="me@x.com")
        d = graph_to_dict(g)

        assert "thread_id" in d
        assert "subject" in d
        assert "message_count" in d
        assert "participant_count" in d
        assert "participants" in d
        assert "initiator" in d
        assert "last_sender" in d
        assert "my_turn" in d
        assert "days_without_reply" in d
        assert "timeline" in d

    def test_participants_have_required_fields(self):
        docs = [_make_doc("t1", "a@x.com", "Alice")]
        g = build_thread_graph("t1", docs)
        d = graph_to_dict(g)
        p = d["participants"][0]
        assert "email" in p
        assert "name" in p
        assert "initials" in p
        assert "avatar_color" in p
        assert "role" in p
        assert "is_me" in p
        assert "messages_sent" in p

    def test_timeline_entries_have_required_fields(self):
        docs = [_make_doc("t1", "a@x.com", date=_iso(0))]
        g = build_thread_graph("t1", docs)
        d = graph_to_dict(g)
        t = d["timeline"][0]
        assert "date" in t
        assert "date_display" in t
        assert "sender_name" in t
        assert "sender_email" in t
        assert "is_me" in t
        assert "item_id" in t
        assert "path" in t


# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_missing_sender_email_does_not_crash(self):
        doc = MagicMock()
        doc.section = "mail"
        doc.sender_email = None
        doc.date = _iso(0)
        doc.path = Path("/vault/mail/test.md")
        doc.frontmatter = {
            "thread_id": "t1",
            "sender": "",
            "sender_email": "",
            "subject": "Test",
            "recipients": [],
            "cc": [],
            "id": "test",
        }
        # Should not raise
        build_thread_graph("t1", [doc])
        # May return None or a graph with 0 named participants — both valid
        # Key: no exception

    def test_duplicate_sender_not_double_counted(self):
        """Same sender appearing in multiple messages: counted once, messages_sent incremented."""
        docs = [
            _make_doc("t1", "alice@x.com", date=_iso(0)),
            _make_doc("t1", "alice@x.com", date=_iso(2)),
        ]
        g = build_thread_graph("t1", docs)
        alices = [p for p in g.participants if p.email == "alice@x.com"]
        assert len(alices) == 1
        assert alices[0].messages_sent == 2

    def test_participant_count_matches_participants_list(self):
        docs = [
            _make_doc("t1", "a@x.com", date=_iso(0)),
            _make_doc("t1", "b@x.com", date=_iso(1)),
        ]
        g = build_thread_graph("t1", docs)
        assert g.participant_count == len(g.participants)
