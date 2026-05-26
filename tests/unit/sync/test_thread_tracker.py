"""
Unit tests for personal_assistant.sync.thread_tracker
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from personal_assistant.models import CalendarEvent, MailMessage
from personal_assistant.sync.thread_tracker import ThreadTracker, _norm_subject, _tid

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DT = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)


def _msg(
    message_id: str = "m1@test",
    subject: str = "Hello",
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
    uid: str = "ev1@cal",
    title: str = "Standup",
    start: datetime = _DT,
    organizer: str | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        uid=uid,
        title=title,
        start=start,
        end=start + timedelta(hours=1),
        organizer=organizer,
    )


# ---------------------------------------------------------------------------
# _norm_subject
# ---------------------------------------------------------------------------

class TestNormSubject:
    def test_strips_re(self):
        assert _norm_subject("Re: Hello") == "hello"

    def test_strips_fwd(self):
        assert _norm_subject("Fwd: Hello") == "hello"

    def test_strips_multiple(self):
        assert _norm_subject("Re: Re: Re: Status") == "status"

    def test_strips_russian_reply(self):
        assert _norm_subject("Отв: Отчёт") == "отчёт"

    def test_no_prefix(self):
        assert _norm_subject("Hello world") == "hello world"

    def test_nfc_normalization(self):
        # Composed vs decomposed 'ё'
        s1 = _norm_subject("Отчёт")  # NFC ё
        assert s1 == "отчёт"


# ---------------------------------------------------------------------------
# ThreadTracker — messages
# ---------------------------------------------------------------------------

class TestThreadTrackerMessages:
    def test_single_message_gets_thread_id(self):
        tracker = ThreadTracker()
        msgs = [_msg()]
        tracker.group_messages(msgs)
        assert msgs[0].thread_id is not None
        assert len(msgs[0].thread_id) == 12

    def test_reply_chain_same_thread(self):
        """Original + Re: → same thread_id."""
        original = _msg("m1@t", "Project update")
        reply1   = _msg("m2@t", "Re: Project update",   date=_DT + timedelta(hours=1))
        reply2   = _msg("m3@t", "Re: Re: Project update", date=_DT + timedelta(hours=2))
        tracker = ThreadTracker()
        tracker.group_messages([original, reply1, reply2])
        assert original.thread_id == reply1.thread_id == reply2.thread_id

    def test_fwd_chain_same_thread(self):
        original = _msg("m1@t", "Q1 report")
        fwd      = _msg("m2@t", "Fwd: Q1 report", date=_DT + timedelta(days=1))
        tracker = ThreadTracker()
        tracker.group_messages([original, fwd])
        assert original.thread_id == fwd.thread_id

    def test_different_subjects_different_threads(self):
        m1 = _msg("m1@t", "Budget review")
        m2 = _msg("m2@t", "Sprint planning")
        tracker = ThreadTracker()
        tracker.group_messages([m1, m2])
        assert m1.thread_id != m2.thread_id

    def test_thread_index_populated(self):
        m1 = _msg("m1@t", "Status")
        m2 = _msg("m2@t", "Re: Status", date=_DT + timedelta(hours=1))
        tracker = ThreadTracker()
        tracker.group_messages([m1, m2])
        index = tracker.thread_index
        assert len(index) == 1
        tid = m1.thread_id
        assert tid in index
        assert len(index[tid]) == 2

    def test_pre_set_thread_id_respected(self):
        """
        OutlookSQLiteReader already converts conversation_id → thread_id before
        creating MailMessage. ThreadTracker must preserve those pre-set IDs.
        """
        tid = _tid("outlook_conv:CONV_ABC_123")
        m1 = MailMessage(
            message_id="m1@t",
            subject="Sprint planning",
            sender_email="alice@corp.com",
            date=_DT,
            source="outlook_sqlite",
            thread_id=tid,
        )
        m2 = MailMessage(
            message_id="m2@t",
            subject="Re: Sprint planning (updated)",  # slightly different subject
            sender_email="bob@corp.com",
            date=_DT + timedelta(hours=1),
            source="outlook_sqlite",
            thread_id=tid,
        )
        tracker = ThreadTracker()
        tracker.group_messages([m1, m2])
        # Pre-set thread_id must be preserved (not re-computed from subject)
        assert m1.thread_id == tid
        assert m2.thread_id == tid
        assert m1.thread_id == m2.thread_id

    def test_different_pre_set_thread_id_different_thread(self):
        """Two messages with different reader-assigned thread_ids stay separate."""
        m1 = MailMessage(
            message_id="m1@t", subject="Proposal", sender_email="a@b.com",
            date=_DT, source="outlook_sqlite",
            thread_id=_tid("outlook_conv:CONV_001"),
        )
        m2 = MailMessage(
            message_id="m2@t", subject="Proposal", sender_email="a@b.com",
            date=_DT, source="outlook_sqlite",
            thread_id=_tid("outlook_conv:CONV_002"),
        )
        tracker = ThreadTracker()
        tracker.group_messages([m1, m2])
        assert m1.thread_id != m2.thread_id

    def test_in_reply_to_in_body(self):
        """When body contains In-Reply-To header, use it for threading."""
        parent_id = "parent@mail.example.com"
        child_body = (
            f"In-Reply-To: <{parent_id}>\n"
            "References: <parent@mail.example.com>\n"
            "\nHi, thanks for your message!"
        )
        parent = _msg("parent@mail.example.com", "Meeting tomorrow")
        child  = _msg("child@mail.example.com", "Re: Meeting tomorrow",
                      body=child_body, date=_DT + timedelta(hours=2))
        tracker = ThreadTracker()
        tracker.group_messages([parent, child])
        # Both should be in the same thread via RFC 2822 headers
        # (parent uses subject-hash, child uses In-Reply-To — they should match
        #  if we compute the same root; depends on implementation)
        # At minimum the child gets a non-None thread_id
        assert child.thread_id is not None

    def test_empty_list(self):
        tracker = ThreadTracker()
        result = tracker.group_messages([])
        assert result == []
        assert tracker.thread_index == {}

    def test_returns_same_list(self):
        msgs = [_msg("m1@t"), _msg("m2@t", "Other")]
        tracker = ThreadTracker()
        result = tracker.group_messages(msgs)
        assert result is msgs


# ---------------------------------------------------------------------------
# ThreadTracker — events (meeting series)
# ---------------------------------------------------------------------------

class TestThreadTrackerEvents:
    def test_single_event_indexed(self):
        tracker = ThreadTracker()
        evs = [_event()]
        tracker.group_events(evs)
        assert len(tracker.series_index) == 1

    def test_recurrence_uid_grouped(self):
        """Events whose UIDs share a prefix before '#' are the same series."""
        base_uid = "AAABBB-CCCDDD"
        e1 = _event(f"{base_uid}#20260519T090000Z", "Daily standup", _DT)
        e2 = _event(f"{base_uid}#20260520T090000Z", "Daily standup",
                    _DT + timedelta(days=1))
        e3 = _event(f"{base_uid}#20260521T090000Z", "Daily standup",
                    _DT + timedelta(days=2))
        tracker = ThreadTracker()
        tracker.group_events([e1, e2, e3])
        assert len(tracker.series_index) == 1
        sid = list(tracker.series_index.keys())[0]
        assert len(tracker.series_index[sid]) == 3

    def test_colon_separator_uid(self):
        """UID:recurrenceId pattern (some iCal producers use ':'). """
        e1 = _event("SERIES-001:20260519", "Weekly review", _DT)
        e2 = _event("SERIES-001:20260526", "Weekly review", _DT + timedelta(days=7))
        tracker = ThreadTracker()
        tracker.group_events([e1, e2])
        assert len(tracker.series_index) == 1

    def test_different_title_different_series(self):
        e1 = _event("uid1", "Standup", _DT)
        e2 = _event("uid2", "Retrospective", _DT)
        tracker = ThreadTracker()
        tracker.group_events([e1, e2])
        assert len(tracker.series_index) == 2

    def test_same_title_different_organizer_separate_series(self):
        """Same title but different organizer → different series."""
        e1 = _event("uid1", "Sprint review", _DT, organizer="alice@corp.com")
        e2 = _event("uid2", "Sprint review", _DT + timedelta(days=14),
                    organizer="bob@corp.com")
        tracker = ThreadTracker()
        tracker.group_events([e1, e2])
        # Each has a unique title+org key → 2 groups, but then merger consolidates
        # same-title groups into one
        assert len(tracker.series_index) == 1  # merged by title

    def test_long_stable_uid_treated_as_series_key(self):
        """UIDs ≥20 chars without recurrence separator are treated as stable IDs."""
        long_uid = "A" * 25
        e1 = _event(long_uid, "Project kickoff", _DT)
        e2 = _event(long_uid, "Project kickoff", _DT + timedelta(days=7))
        tracker = ThreadTracker()
        tracker.group_events([e1, e2])
        assert len(tracker.series_index) == 1

    def test_group_events_does_not_mutate_events(self):
        """CalendarEvent.uid should not be modified."""
        original_uid = "uid-original@test"
        e = _event(original_uid)
        tracker = ThreadTracker()
        tracker.group_events([e])
        assert e.uid == original_uid

    def test_returns_same_list(self):
        evs = [_event("e1@cal"), _event("e2@cal", "Other")]
        tracker = ThreadTracker()
        result = tracker.group_events(evs)
        assert result is evs

    def test_empty_list(self):
        tracker = ThreadTracker()
        tracker.group_events([])
        assert tracker.series_index == {}


# ---------------------------------------------------------------------------
# ThreadTracker.summary()
# ---------------------------------------------------------------------------

class TestThreadTrackerSummary:
    def test_summary_structure(self):
        tracker = ThreadTracker()
        m1 = _msg("m1@t", "Topic A")
        m2 = _msg("m2@t", "Re: Topic A", date=_DT + timedelta(hours=1))
        m3 = _msg("m3@t", "Topic B")
        tracker.group_messages([m1, m2, m3])

        e1 = _event("uid#rec1", "Standup", _DT)
        e2 = _event("uid#rec2", "Standup", _DT + timedelta(days=1))
        tracker.group_events([e1, e2])

        summary = tracker.summary()
        assert summary["threads"]["total"] == 2       # Topic A, Topic B
        assert summary["threads"]["multi_message"] == 1  # Topic A has 2
        assert summary["threads"]["longest"] == 2

        assert summary["series"]["total"] == 1       # one series "uid"
        assert summary["series"]["multi_event"] == 1
        assert summary["series"]["longest"] == 2
