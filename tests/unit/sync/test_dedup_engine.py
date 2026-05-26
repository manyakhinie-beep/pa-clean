"""
Unit tests for personal_assistant.sync.dedup_engine
"""

from __future__ import annotations

from datetime import datetime, timezone

from personal_assistant.models import CalendarEvent, MailMessage
from personal_assistant.sync.dedup_engine import (
    DedupEngine,
    event_fingerprint,
    message_fingerprint,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DT = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
_DT2 = datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc)


def _msg(
    message_id: str = "msg1@test",
    subject: str = "Hello world",
    sender: str = "alice@example.com",
    date: datetime = _DT,
    body: str | None = None,
    source: str = "mail",
) -> MailMessage:
    return MailMessage(
        message_id=message_id,
        subject=subject,
        sender_email=sender,
        date=date,
        source=source,
        body=body,
    )


def _event(
    uid: str = "event1@cal",
    title: str = "Team standup",
    start: datetime = _DT,
    end: datetime | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        uid=uid,
        title=title,
        start=start,
        end=end or datetime(start.year, start.month, start.day, start.hour + 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# message_fingerprint
# ---------------------------------------------------------------------------

class TestMessageFingerprint:
    def test_same_message_same_fingerprint(self):
        m1 = _msg()
        m2 = _msg()
        assert message_fingerprint(m1) == message_fingerprint(m2)

    def test_different_subject_different_fingerprint(self):
        assert message_fingerprint(_msg(subject="Hello")) != message_fingerprint(_msg(subject="Bye"))

    def test_reply_prefix_stripped(self):
        """Re: Re: Hello world should fingerprint same as Hello world."""
        m1 = _msg(subject="Hello world")
        m2 = _msg(subject="Re: Hello world")
        m3 = _msg(subject="Re: Re: Hello world")
        assert message_fingerprint(m1) == message_fingerprint(m2)
        assert message_fingerprint(m1) == message_fingerprint(m3)

    def test_fwd_prefix_stripped(self):
        m1 = _msg(subject="Project update")
        m2 = _msg(subject="Fwd: Project update")
        assert message_fingerprint(m1) == message_fingerprint(m2)

    def test_russian_prefixes_stripped(self):
        m1 = _msg(subject="Отчёт по проекту")
        m2 = _msg(subject="Отв: Отчёт по проекту")
        assert message_fingerprint(m1) == message_fingerprint(m2)

    def test_different_sender_different_fingerprint(self):
        m1 = _msg(sender="alice@example.com")
        m2 = _msg(sender="bob@example.com")
        assert message_fingerprint(m1) != message_fingerprint(m2)

    def test_different_date_different_fingerprint(self):
        m1 = _msg(date=_DT)
        m2 = _msg(date=_DT2)
        assert message_fingerprint(m1) != message_fingerprint(m2)

    def test_fingerprint_is_16_chars(self):
        fp = message_fingerprint(_msg())
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# event_fingerprint
# ---------------------------------------------------------------------------

class TestEventFingerprint:
    def test_same_event_same_fingerprint(self):
        e1 = _event()
        e2 = _event()
        assert event_fingerprint(e1) == event_fingerprint(e2)

    def test_different_title_different_fingerprint(self):
        e1 = _event(title="Standup")
        e2 = _event(title="Retrospective")
        assert event_fingerprint(e1) != event_fingerprint(e2)

    def test_different_start_different_fingerprint(self):
        e1 = _event(start=_DT)
        e2 = _event(start=_DT2)
        assert event_fingerprint(e1) != event_fingerprint(e2)

    def test_fingerprint_is_16_chars(self):
        fp = event_fingerprint(_event())
        assert len(fp) == 16


# ---------------------------------------------------------------------------
# DedupEngine — messages
# ---------------------------------------------------------------------------

class TestDedupEngineMessages:
    def test_unique_messages_all_kept(self):
        engine = DedupEngine()
        msgs = [
            _msg("m1@t", "Subject A"),
            _msg("m2@t", "Subject B"),
            _msg("m3@t", "Subject C"),
        ]
        result = engine.dedup_messages(msgs)
        assert len(result) == 3
        assert engine.stats["messages"]["kept"] == 3
        assert engine.stats["messages"]["dropped"] == 0

    def test_exact_duplicate_dropped(self):
        engine = DedupEngine()
        m = _msg("dup@t", "Dup subject")
        result = engine.dedup_messages([m, m])
        assert len(result) == 1
        assert engine.stats["messages"]["dropped"] == 1

    def test_same_id_different_source_richer_wins(self):
        """Same message_id from outlook_sqlite (with body) should replace outlook_as (no body)."""
        m_as = _msg("x@t", body=None, source="outlook")
        m_sq = _msg("x@t", body="Full email body here", source="outlook_sqlite")
        engine = DedupEngine()
        result = engine.dedup_messages([m_as, m_sq])
        assert len(result) == 1
        assert result[0].source == "outlook_sqlite"
        assert result[0].body == "Full email body here"

    def test_same_content_different_id_deduped_by_fingerprint(self):
        """Same logical email with different IDs (outlook_as vs outlook_sqlite)."""
        m1 = _msg("id1@t", "Project status", source="outlook")
        m2 = _msg("id2@t", "Project status", source="outlook_sqlite")
        engine = DedupEngine()
        result = engine.dedup_messages([m1, m2])
        assert len(result) == 1

    def test_reply_chain_not_deduped(self):
        """Re: and original are different messages, not duplicates."""
        original = _msg("orig@t", "Project status", date=_DT)
        reply = _msg("reply@t", "Re: Project status", date=_DT2)
        engine = DedupEngine()
        result = engine.dedup_messages([original, reply])
        assert len(result) == 2

    def test_reset_clears_state(self):
        engine = DedupEngine()
        engine.dedup_messages([_msg("m1@t")])
        engine.reset()
        assert engine.stats["messages"]["kept"] == 0
        assert engine.stats["messages"]["dropped"] == 0

    def test_add_message_returns_bool(self):
        engine = DedupEngine()
        m = _msg("m1@t")
        assert engine.add_message(m) is True
        assert engine.add_message(m) is False  # duplicate

    def test_richer_record_wins_on_fingerprint_collision(self):
        """When two messages have the same fingerprint, the richer one is kept."""
        m_poor = _msg("id1@t", "Status update", source="outlook")
        m_rich = _msg("id2@t", "Status update", body="Long body", source="outlook_sqlite")
        engine = DedupEngine()
        # Poor added first, rich second
        engine.dedup_messages([m_poor, m_rich])
        assert len(engine._msg_fp) == 1
        kept = list(engine._msg_fp.values())[0]
        assert kept.body == "Long body"

    def test_empty_list(self):
        engine = DedupEngine()
        assert engine.dedup_messages([]) == []


# ---------------------------------------------------------------------------
# DedupEngine — events
# ---------------------------------------------------------------------------

class TestDedupEngineEvents:
    def test_unique_events_all_kept(self):
        engine = DedupEngine()
        evs = [
            _event("e1@cal", "Standup", _DT),
            _event("e2@cal", "Retro", _DT2),
        ]
        result = engine.dedup_events(evs)
        assert len(result) == 2

    def test_exact_duplicate_dropped(self):
        engine = DedupEngine()
        e = _event("e1@cal")
        result = engine.dedup_events([e, e])
        assert len(result) == 1
        assert engine.stats["events"]["dropped"] == 1

    def test_same_content_different_source_deduped(self):
        """Same event from calendar and outlook_sqlite."""
        e_cal = _event("cal_uid_001", "Weekly sync", _DT)
        e_sq  = _event("sql_uid_001", "Weekly sync", _DT)
        engine = DedupEngine()
        result = engine.dedup_events([e_cal, e_sq])
        assert len(result) == 1

    def test_richer_event_wins(self):
        """Event with URL wins over one without."""
        e_poor = CalendarEvent(uid="e1", title="Sprint review", start=_DT,
                               end=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc))
        e_rich = CalendarEvent(uid="e2", title="Sprint review", start=_DT,
                               end=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc),
                               url="https://meet.example.com/room",
                               notes="Agenda: demo, retro")
        engine = DedupEngine()
        engine.dedup_events([e_poor, e_rich])
        kept = list(engine._ev_fp.values())[0]
        assert kept.url == "https://meet.example.com/room"

    def test_add_event_returns_bool(self):
        engine = DedupEngine()
        e = _event()
        assert engine.add_event(e) is True
        assert engine.add_event(e) is False

    def test_empty_list(self):
        engine = DedupEngine()
        assert engine.dedup_events([]) == []


# ---------------------------------------------------------------------------
# DedupEngine — dedup_all
# ---------------------------------------------------------------------------

class TestDedupAll:
    def test_dedup_all_returns_both(self):
        engine = DedupEngine()
        msgs = [_msg("m1@t"), _msg("m1@t")]          # 1 dup → 1 unique
        evs  = [
            _event("e1@cal", "Standup", _DT),         # different title+start
            _event("e2@cal", "Retrospective", _DT2),  # → 2 unique fingerprints
        ]
        unique_msgs, unique_evs = engine.dedup_all(msgs, evs)
        assert len(unique_msgs) == 1
        assert len(unique_evs) == 2
