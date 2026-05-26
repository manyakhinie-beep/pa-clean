"""
Unit tests for meeting_prep_service (Stage 5: Smart Meeting Prep).

Coverage:
  MP01–MP05  _parse_participants — various field names and formats
  MP06–MP08  _emails_from_participants — email extraction
  MP09–MP11  _find_event_in_vault — vault scan, not-found, frontmatter fallbacks
  MP12–MP14  _scan_recent_emails — filters by date + participant
  MP15–MP16  _scan_related_projects — name/email matching
  MP17–MP18  _scan_previous_meetings — past only, excludes self
  MP19–MP20  _scan_open_action_items — regex extraction
  MP21–MP22  _rule_based_brief — content checks
  MP23       _build_context_prompt — all sections present
  MP24–MP27  build_meeting_prep — full integration, no vault, graceful fallback
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Service under test
# ---------------------------------------------------------------------------
from personal_assistant.services.meeting_prep_service import (
    _build_context_prompt,
    _emails_from_participants,
    _find_event_in_vault,
    _parse_participants,
    _rule_based_brief,
    _scan_open_action_items,
    _scan_previous_meetings,
    _scan_recent_emails,
    _scan_related_projects,
    build_meeting_prep,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_md(path: Path, frontmatter: str, body: str = "") -> Path:
    """Write a minimal .md file with YAML frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n{body.strip()}"
    path.write_text(content, encoding="utf-8")
    return path


def _iso_days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


def _iso_days_ahead(days: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# MP01-MP05  _parse_participants
# ---------------------------------------------------------------------------

class TestParseParticipants:

    def test_MP01_list_attendees(self):
        """MP01: Reads 'attendees' as a list."""
        fm = {"attendees": ["Alice <alice@corp.com>", "Bob <bob@corp.com>"]}
        result = _parse_participants(fm)
        assert "Alice <alice@corp.com>" in result
        assert "Bob <bob@corp.com>" in result

    def test_MP02_comma_string_participants(self):
        """MP02: Parses comma-separated 'participants' string."""
        fm = {"participants": "alice@corp.com, bob@corp.com, carol@corp.com"}
        result = _parse_participants(fm)
        assert len(result) == 3
        assert "alice@corp.com" in result

    def test_MP03_contacts_field(self):
        """MP03: Reads 'contacts' field."""
        fm = {"contacts": ["ivan@corp.ru"]}
        result = _parse_participants(fm)
        assert "ivan@corp.ru" in result

    def test_MP04_invitees_field(self):
        """MP04: Reads 'invitees' field."""
        fm = {"invitees": ["user@example.com"]}
        result = _parse_participants(fm)
        assert "user@example.com" in result

    def test_MP05_empty_returns_empty(self):
        """MP05: No participant fields returns empty list."""
        assert _parse_participants({}) == []
        assert _parse_participants({"title": "Review"}) == []


# ---------------------------------------------------------------------------
# MP06-MP08  _emails_from_participants
# ---------------------------------------------------------------------------

class TestEmailsFromParticipants:

    def test_MP06_extracts_angle_bracket_email(self):
        """MP06: Extracts email from 'Name <email>' format."""
        result = _emails_from_participants(["Alice <alice@corp.com>"])
        assert result == ["alice@corp.com"]

    def test_MP07_bare_email_passthrough(self):
        """MP07: Passes bare email through unchanged (lowercased)."""
        result = _emails_from_participants(["Bob@Corp.COM"])
        assert result == ["bob@corp.com"]

    def test_MP08_deduplicates(self):
        """MP08: Removes duplicate emails."""
        result = _emails_from_participants([
            "Alice <alice@x.com>", "alice@x.com", "alice@x.com"
        ])
        assert result.count("alice@x.com") == 1


# ---------------------------------------------------------------------------
# MP09-MP11  _find_event_in_vault
# ---------------------------------------------------------------------------

class TestFindEventInVault:

    def test_MP09_finds_event_by_id(self, tmp_path):
        """MP09: Finds a calendar event by its 'id' frontmatter field."""
        cal = tmp_path / "calendar" / "2026" / "05"
        _write_md(
            cal / "review.md",
            """
            id: event_review_001
            title: Quarterly Review
            date: 2026-05-30T14:00:00+03:00
            attendees:
              - Alice <alice@corp.com>
              - Bob <bob@corp.com>
            location: Room 101
            """,
            "Discuss Q2 results.",
        )

        result = _find_event_in_vault("event_review_001", tmp_path)
        assert result is not None
        assert result["id"] == "event_review_001"
        assert result["title"] == "Quarterly Review"
        assert "Alice <alice@corp.com>" in result["participants"]
        assert "alice@corp.com" in result["participant_emails"]
        assert result["location"] == "Room 101"

    def test_MP10_returns_none_for_unknown_id(self, tmp_path):
        """MP10: Returns None when event_id not found in vault."""
        (tmp_path / "calendar").mkdir()
        result = _find_event_in_vault("nonexistent_event_xyz", tmp_path)
        assert result is None

    def test_MP11_finds_by_stem_fallback(self, tmp_path):
        """MP11: Falls back to using filename stem as id."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "standup_2026_05_30.md",
            """
            title: Daily Standup
            date: 2026-05-30T10:00:00+03:00
            """,
            "Daily standup meeting.",
        )
        result = _find_event_in_vault("standup_2026_05_30", tmp_path)
        assert result is not None
        assert result["title"] == "Daily Standup"


# ---------------------------------------------------------------------------
# MP12-MP14  _scan_recent_emails
# ---------------------------------------------------------------------------

class TestScanRecentEmails:

    def test_MP12_returns_recent_emails_from_participant(self, tmp_path):
        """MP12: Includes emails where sender is in participant_emails and within date window."""
        mail = tmp_path / "mail" / "2026" / "05"
        _write_md(
            mail / "msg001.md",
            f"""
            id: msg001
            type: email
            subject: "Project update"
            sender: Alice <alice@corp.com>
            date: {_iso_days_ago(2)}
            """,
            "Hi, here is the update.",
        )
        results = _scan_recent_emails(tmp_path, ["alice@corp.com"])
        assert len(results) == 1
        assert results[0]["subject"] == "Project update"

    def test_MP13_excludes_old_emails(self, tmp_path):
        """MP13: Excludes emails older than recent_days window."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "old_msg.md",
            f"""
            id: old_msg
            type: email
            subject: "Old news"
            sender: alice@corp.com
            date: {_iso_days_ago(30)}
            """,
            "This is old.",
        )
        results = _scan_recent_emails(tmp_path, ["alice@corp.com"], recent_days=7)
        assert results == []

    def test_MP14_excludes_unrelated_sender(self, tmp_path):
        """MP14: Excludes emails where sender is not in participant_emails."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "msg_stranger.md",
            f"""
            id: msg_stranger
            type: email
            subject: "Hello from stranger"
            sender: stranger@unknown.com
            date: {_iso_days_ago(1)}
            """,
            "Random message.",
        )
        results = _scan_recent_emails(tmp_path, ["alice@corp.com"])
        assert results == []


# ---------------------------------------------------------------------------
# MP15-MP16  _scan_related_projects
# ---------------------------------------------------------------------------

class TestScanRelatedProjects:

    def test_MP15_finds_project_mentioning_participant(self, tmp_path):
        """MP15: Finds project that mentions a participant by name."""
        proj = tmp_path / "projects"
        _write_md(
            proj / "alpha.md",
            """
            id: proj_alpha
            title: Alpha Project
            """,
            "This project involves Alice and Bob from the team.",
        )
        results = _scan_related_projects(tmp_path, ["Alice <alice@corp.com>"])
        assert len(results) == 1
        assert results[0]["title"] == "Alpha Project"

    def test_MP16_skips_project_without_participant(self, tmp_path):
        """MP16: Skips projects that do not mention any participant."""
        proj = tmp_path / "projects"
        _write_md(
            proj / "unrelated.md",
            """
            id: proj_unrelated
            title: Unrelated Project
            """,
            "This project involves Charlie only.",
        )
        results = _scan_related_projects(tmp_path, ["alice@corp.com"])
        assert results == []


# ---------------------------------------------------------------------------
# MP17-MP18  _scan_previous_meetings
# ---------------------------------------------------------------------------

class TestScanPreviousMeetings:

    def test_MP17_finds_past_meeting_with_overlapping_participant(self, tmp_path):
        """MP17: Returns past meeting that mentions a shared participant."""
        cal = tmp_path / "calendar" / "2026" / "04"
        _write_md(
            cal / "kickoff.md",
            f"""
            id: past_kickoff
            title: Project Kickoff
            date: {_iso_days_ago(10)}
            attendees:
              - Alice <alice@corp.com>
            """,
            "Discussed project scope with Alice.",
        )
        results = _scan_previous_meetings(
            tmp_path, ["Alice <alice@corp.com>"], exclude_event_id="event_new"
        )
        assert len(results) == 1
        assert results[0]["title"] == "Project Kickoff"

    def test_MP18_excludes_self_and_future_events(self, tmp_path):
        """MP18: Excludes the event itself and future events."""
        cal = tmp_path / "calendar"
        # Future event with Alice — should be excluded
        _write_md(
            cal / "future_meeting.md",
            f"""
            id: future_meeting
            title: Future Meeting
            date: {_iso_days_ahead(3)}
            attendees:
              - Alice <alice@corp.com>
            """,
            "Alice will attend.",
        )
        # Self — should be excluded
        _write_md(
            cal / "self_event.md",
            f"""
            id: self_event
            title: Self Event
            date: {_iso_days_ago(2)}
            attendees:
              - Alice <alice@corp.com>
            """,
            "This is the current event.",
        )
        results = _scan_previous_meetings(
            tmp_path, ["Alice <alice@corp.com>"], exclude_event_id="self_event"
        )
        # Neither future_meeting (future) nor self_event (excluded) should appear
        ids = [r["id"] for r in results]
        assert "future_meeting" not in ids
        assert "self_event" not in ids


# ---------------------------------------------------------------------------
# MP19-MP20  _scan_open_action_items
# ---------------------------------------------------------------------------

class TestScanOpenActionItems:

    def test_MP19_extracts_russian_task_phrases(self, tmp_path):
        """MP19: Detects Russian action-item patterns in mail body."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "task_msg.md",
            f"""
            id: task_msg
            subject: Поручение
            sender: alice@corp.com
            date: {_iso_days_ago(1)}
            """,
            "Привет Иван, прошу подготовить отчёт к пятнице.\nТакже необходимо согласовать бюджет с финансами.",
        )
        items = _scan_open_action_items(tmp_path, ["alice@corp.com"])
        assert len(items) >= 1
        # At least one item should contain a task phrase
        combined = " ".join(items).lower()
        assert any(word in combined for word in ["прошу", "необходимо", "подготовить", "согласовать"])

    def test_MP20_no_items_when_no_participants(self, tmp_path):
        """MP20: Returns empty list when participants list is empty."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "some_mail.md",
            """
            id: some_mail
            subject: Misc
            sender: unknown@corp.com
            """,
            "Please review this document.",
        )
        items = _scan_open_action_items(tmp_path, [])
        assert items == []


# ---------------------------------------------------------------------------
# MP21-MP22  _rule_based_brief
# ---------------------------------------------------------------------------

class TestRuleBasedBrief:

    def test_MP21_includes_all_sections_when_data_present(self):
        """MP21: Brief includes title, emails, projects, meetings, action items."""
        event = {
            "title": "Q2 Review",
            "event_date": "2026-06-01T14:00:00+03:00",
            "participants": ["Alice <alice@corp.com>"],
        }
        emails = [{
            "date": "2026-05-28",
            "sender": "alice@corp.com",
            "subject": "Q2 numbers",
            "body_snippet": "Here are the Q2 numbers.",
        }]
        projects = [{"title": "Alpha Project"}]
        meetings = [{"date": "2026-05-01", "title": "Q1 Review", "snippet": "reviewed Q1"}]
        action_items = ["Подготовить финансовый отчёт"]

        brief = _rule_based_brief(event, emails, projects, meetings, action_items)
        assert "Q2 Review" in brief
        assert "Q2 numbers" in brief
        assert "Alpha Project" in brief
        assert "Q1 Review" in brief
        assert "Подготовить финансовый отчёт" in brief

    def test_MP22_graceful_with_no_context(self):
        """MP22: Brief handles empty context without errors."""
        event = {"title": "Новая встреча", "participants": [], "event_date": ""}
        brief = _rule_based_brief(event, [], [], [], [])
        assert "встреч" in brief.lower() or "новая" in brief.lower()
        # Should note that no context was found
        assert "впервые" in brief.lower() or "не найден" in brief.lower()


# ---------------------------------------------------------------------------
# MP23  _build_context_prompt
# ---------------------------------------------------------------------------

class TestBuildContextPrompt:

    def test_MP23_all_sections_in_prompt(self):
        """MP23: Context prompt contains event info, brief, emails, action items."""
        event = {
            "title": "Budget Planning",
            "event_date": "2026-06-01T10:00:00+03:00",
            "participants": ["Finance Team <finance@corp.com>"],
        }
        emails = [{
            "date": "2026-05-25",
            "sender": "finance@corp.com",
            "subject": "Q2 forecast",
            "body_snippet": "Attached Q2 forecast.",
        }]
        brief = "Обсудить Q2 прогноз."
        action_items = ["Согласовать бюджет с CFO"]

        prompt = _build_context_prompt(event, emails, [], [], action_items, brief)
        assert "Budget Planning" in prompt
        assert "Обсудить Q2 прогноз" in prompt
        assert "Q2 forecast" in prompt
        assert "Согласовать бюджет" in prompt
        assert "═══" in prompt  # section dividers


# ---------------------------------------------------------------------------
# MP24-MP27  build_meeting_prep — full integration
# ---------------------------------------------------------------------------

class TestBuildMeetingPrep:

    def test_MP24_returns_all_required_keys(self, tmp_path):
        """MP24: Output dict always contains all required keys."""
        result = build_meeting_prep("nonexistent", vault_path=tmp_path)
        required_keys = {
            "event_id", "title", "participants", "participant_emails",
            "event_date", "location", "recent_emails", "related_projects",
            "previous_meetings", "open_action_items", "prep_brief",
            "context_prompt", "event_found", "message_count",
        }
        assert required_keys.issubset(result.keys())

    def test_MP25_event_found_with_vault_data(self, tmp_path):
        """MP25: event_found=True when event is located in vault."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "weekly_sync.md",
            f"""
            id: weekly_sync_001
            title: Weekly Sync
            date: {_iso_days_ahead(2)}
            attendees:
              - Alice <alice@corp.com>
            """,
            "Regular sync.",
        )
        result = build_meeting_prep("weekly_sync_001", vault_path=tmp_path)
        assert result["event_found"] is True
        assert result["title"] == "Weekly Sync"
        assert result["event_id"] == "weekly_sync_001"

    def test_MP26_event_not_found_graceful(self, tmp_path):
        """MP26: Returns graceful minimal dict when event not found."""
        (tmp_path / "calendar").mkdir()
        result = build_meeting_prep("totally_unknown_event", vault_path=tmp_path)
        assert result["event_found"] is False
        assert result["title"] == "Без названия"
        assert isinstance(result["prep_brief"], str)
        assert len(result["prep_brief"]) > 0
        assert isinstance(result["context_prompt"], str)

    def test_MP27_excludes_my_email_from_participants(self, tmp_path):
        """MP27: build_meeting_prep filters out the user's own email."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "team_meeting.md",
            f"""
            id: team_meeting_001
            title: Team Meeting
            date: {_iso_days_ahead(1)}
            attendees:
              - Me <me@corp.com>
              - Colleague <col@corp.com>
            """,
            "Team meeting agenda.",
        )
        result = build_meeting_prep(
            "team_meeting_001",
            vault_path=tmp_path,
            my_email="me@corp.com",
        )
        assert "me@corp.com" not in result["participant_emails"]
        assert "col@corp.com" in result["participant_emails"]

    def test_MP28_no_vault_returns_valid_result(self):
        """MP28: Works without vault (vault_path=None), no exceptions."""
        result = build_meeting_prep("some_event_id", vault_path=None)
        assert result["event_id"] == "some_event_id"
        assert result["event_found"] is False
        assert isinstance(result["context_prompt"], str)
        assert result["message_count"] == 0

    def test_MP29_message_count_sums_context_docs(self, tmp_path):
        """MP29: message_count = len(recent_emails) + len(projects) + len(prev_meetings)."""
        cal = tmp_path / "calendar"
        mail = tmp_path / "mail"
        proj = tmp_path / "projects"

        _write_md(
            cal / "event_abc.md",
            f"""
            id: event_abc
            title: Project Review
            date: {_iso_days_ahead(1)}
            attendees:
              - Alice <alice@corp.com>
            """,
            "Review project status.",
        )
        _write_md(
            mail / "recent_msg.md",
            f"""
            id: recent_msg
            type: email
            subject: "Status update"
            sender: Alice <alice@corp.com>
            date: {_iso_days_ago(1)}
            """,
            "Here is the status.",
        )
        _write_md(
            proj / "proj_x.md",
            """
            id: proj_x
            title: Project X
            """,
            "Alice leads this project.",
        )

        result = build_meeting_prep("event_abc", vault_path=tmp_path)
        assert result["event_found"] is True
        # message_count should include the recent email and the project
        assert result["message_count"] >= 1
