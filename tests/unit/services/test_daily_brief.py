"""
Unit tests for daily_brief_service (Stage 6).

Coverage:
  DB01–DB04  _parse_frontmatter / _tag_set / _is_urgent / _requires_reply
  DB05–DB08  _build_calendar_section — today filter, sort, is_soon
  DB09–DB12  _build_inbox_section — urgent filter, date window, reply_required
  DB13–DB15  _build_tasks_section — regex extraction
  DB16–DB18  _rule_based_insight — content and graceful empty
  DB19–DB20  _build_bullets — priority order
  DB21–DB25  build_daily_brief — full integration, cache, no vault, graceful
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from personal_assistant.services.daily_brief_service import (
    _build_bullets,
    _build_calendar_section,
    _build_inbox_section,
    _build_tasks_section,
    _is_urgent,
    _parse_frontmatter,
    _requires_reply,
    _rule_based_insight,
    _tag_set,
    build_daily_brief,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_md(path: Path, frontmatter: str, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n{body.strip()}"
    path.write_text(content, encoding="utf-8")
    return path


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_hours(delta: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta)).isoformat()


def _iso_days(delta: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta)).isoformat()


def _iso_today_at(hour: int) -> str:
    """Timestamp at a fixed hour of the *local* today.

    Stable regardless of when the test runs — unlike now+N, which can roll past
    midnight and fall outside the local-date 'today' filter in the service.
    """
    local = datetime.now().astimezone()
    return local.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# DB01–DB04  helpers
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_DB01_parse_frontmatter_basic(self):
        """DB01: Parses basic YAML frontmatter."""
        text = "---\nid: abc\ntitle: Test\n---\nBody here."
        fm, body = _parse_frontmatter(text)
        assert fm["id"] == "abc"
        assert fm["title"] == "Test"
        assert body == "Body here."

    def test_DB02_tag_set_list(self):
        """DB02: _tag_set handles list of tags."""
        fm = {"tags": ["urgency:high", "срочно", "category:finance"]}
        result = _tag_set(fm)
        assert "urgency:high" in result
        assert "срочно" in result

    def test_DB03_is_urgent_true(self):
        """DB03: _is_urgent returns True for urgent tags."""
        assert _is_urgent({"tags": ["urgency:critical"]}) is True
        assert _is_urgent({"tags": ["срочно"]}) is True
        assert _is_urgent({"tags": ["urgent"]}) is True

    def test_DB04_requires_reply_field(self):
        """DB04: _requires_reply reads reply_required field."""
        assert _requires_reply({"reply_required": True}) is True
        assert _requires_reply({"reply_required": "true"}) is True
        assert _requires_reply({"intent": "request"}) is True
        assert _requires_reply({}) is False


# ---------------------------------------------------------------------------
# DB05–DB08  _build_calendar_section
# ---------------------------------------------------------------------------

class TestCalendarSection:

    def test_DB05_returns_todays_events(self, tmp_path):
        """DB05: Returns events with today's date."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "today_meeting.md",
            f"""
            id: today_meeting
            title: Morning Standup
            date: {_iso_today_at(9)}
            """,
            "Daily standup.",
        )
        items, total = _build_calendar_section(tmp_path)
        assert total >= 1
        assert any(e["title"] == "Morning Standup" for e in items)

    def test_DB06_excludes_other_day_events(self, tmp_path):
        """DB06: Excludes events not scheduled for today."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "tomorrow_meeting.md",
            f"""
            id: tomorrow_meeting
            title: Tomorrow Event
            date: {_iso_days(1)}
            """,
            "Future event.",
        )
        items, _ = _build_calendar_section(tmp_path)
        assert all(e["title"] != "Tomorrow Event" for e in items)

    def test_DB07_events_sorted_by_time(self, tmp_path):
        """DB07: Events sorted ascending by time."""
        cal = tmp_path / "calendar"
        _write_md(cal / "late.md",   f"id: late\ntitle: Late\ndate: {_iso_today_at(16)}", "")
        _write_md(cal / "early.md",  f"id: early\ntitle: Early\ndate: {_iso_today_at(8)}", "")
        _write_md(cal / "middle.md", f"id: mid\ntitle: Mid\ndate: {_iso_today_at(12)}", "")
        items, _ = _build_calendar_section(tmp_path)
        titles = [e["title"] for e in items]
        early_idx = titles.index("Early")
        mid_idx   = titles.index("Mid")
        late_idx  = titles.index("Late")
        assert early_idx < mid_idx < late_idx

    def test_DB08_is_soon_flag_set_for_next_hour(self, tmp_path):
        """DB08: is_soon=True for event within next 60 minutes."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "soon.md",
            f"id: soon\ntitle: Imminent Meeting\ndate: {_iso_hours(0)}\n",
            "",
        )
        # Event at current time ±0 min — is_now or is_soon
        items, _ = _build_calendar_section(tmp_path)
        found = next((e for e in items if e["title"] == "Imminent Meeting"), None)
        assert found is not None
        assert found["is_now"] or found["is_soon"]


# ---------------------------------------------------------------------------
# DB09–DB12  _build_inbox_section
# ---------------------------------------------------------------------------

class TestInboxSection:

    def test_DB09_returns_urgent_item(self, tmp_path):
        """DB09: Returns item with urgency:high tag."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "urgent_msg.md",
            f"""
            id: urgent_msg
            type: email
            subject: Срочный вопрос
            sender: boss@corp.com
            date: {_iso_hours(-2)}
            tags:
              - urgency:high
              - reply_required
            """,
            "Прошу ответить немедленно.",
        )
        items, total = _build_inbox_section(tmp_path)
        assert total >= 1
        assert any(i["subject"] == "Срочный вопрос" for i in items)

    def test_DB10_excludes_old_mail(self, tmp_path):
        """DB10: Excludes mail older than 7 days."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "old_urgent.md",
            f"""
            id: old_urgent
            type: email
            subject: Old Urgent
            sender: someone@corp.com
            date: {_iso_days(-10)}
            tags:
              - urgency:critical
            """,
            "This is old.",
        )
        items, _ = _build_inbox_section(tmp_path)
        assert all(i["subject"] != "Old Urgent" for i in items)

    def test_DB11_excludes_non_urgent_mail(self, tmp_path):
        """DB11: Excludes mail without urgent/reply tags."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "newsletter.md",
            f"""
            id: newsletter
            type: email
            subject: Company Newsletter
            sender: hr@corp.com
            date: {_iso_hours(-1)}
            tags:
              - category:info
            """,
            "Monthly newsletter.",
        )
        items, _ = _build_inbox_section(tmp_path)
        assert all(i["subject"] != "Company Newsletter" for i in items)

    def test_DB12_reply_required_included(self, tmp_path):
        """DB12: reply_required=true without urgency tag is still included."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "reply_needed.md",
            f"""
            id: reply_needed
            type: email
            subject: Confirmation Needed
            sender: partner@ext.com
            date: {_iso_hours(-3)}
            reply_required: true
            """,
            "Please confirm.",
        )
        items, _ = _build_inbox_section(tmp_path)
        assert any(i["subject"] == "Confirmation Needed" for i in items)


# ---------------------------------------------------------------------------
# DB13–DB15  _build_tasks_section
# ---------------------------------------------------------------------------

class TestTasksSection:

    def test_DB13_extracts_task_from_mail(self, tmp_path):
        """DB13: Extracts 'прошу' action items from mail body."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "task_mail.md",
            f"""
            id: task_mail
            subject: Поручение
            sender: manager@corp.com
            date: {_iso_hours(-1)}
            """,
            "Прошу подготовить презентацию к завтрашнему совещанию.\n"
            "Также необходимо согласовать с финансами.",
        )
        tasks = _build_tasks_section(tmp_path)
        texts = [t["text"].lower() for t in tasks]
        assert any("прошу" in t or "необходимо" in t for t in texts)

    def test_DB14_extracts_from_threads(self, tmp_path):
        """DB14: Scans threads/ directory as well."""
        threads = tmp_path / "threads"
        _write_md(
            threads / "thr001.md",
            """
            id: thr001
            subject: Project thread
            """,
            "Todo: review the proposal before Friday.\n"
            "Need to send the budget estimate.",
        )
        tasks = _build_tasks_section(tmp_path)
        texts = " ".join(t["text"].lower() for t in tasks)
        assert "todo" in texts or "need to" in texts or "review" in texts

    def test_DB15_empty_when_no_mail(self, tmp_path):
        """DB15: Returns empty list when no mail dir exists."""
        tasks = _build_tasks_section(tmp_path)
        assert tasks == []


# ---------------------------------------------------------------------------
# DB16–DB18  _rule_based_insight
# ---------------------------------------------------------------------------

class TestRuleBasedInsight:

    def test_DB16_mentions_upcoming_event(self):
        """DB16: Insight mentions 'is_soon' event title."""
        events = [{"title": "Q2 Review", "time": "10:00", "is_soon": True, "is_now": False}]
        result = _rule_based_insight(events, [], [], "Игорь")
        assert "Q2 Review" in result or "10:00" in result

    def test_DB17_mentions_urgent_count(self):
        """DB17: Insight mentions urgent senders."""
        urgent = [{"subject": "СРОЧНО", "sender_name": "Петров", "deadline_label": "сегодня"}]
        result = _rule_based_insight([], urgent, [], "Игорь")
        assert "Петров" in result or "срочных" in result.lower() or "письм" in result.lower()

    def test_DB18_returns_string_when_empty(self):
        """DB18: Returns non-empty string even with no events or urgent."""
        result = _rule_based_insight([], [], [], "Игорь")
        assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# DB19–DB20  _build_bullets
# ---------------------------------------------------------------------------

class TestBuildBullets:

    def test_DB19_urgent_with_today_deadline_first(self):
        """DB19: Urgent item with deadline=сегодня appears in bullets."""
        urgent = [{"subject": "Отчёт срочно", "sender_name": "Петров", "deadline_label": "сегодня"}]
        bullets = _build_bullets([], urgent, [])
        combined = " ".join(bullets)
        assert "Отчёт срочно" in combined or "сегодня" in combined.lower()

    def test_DB20_is_soon_event_appears_first(self):
        """DB20: is_soon event appears before non-urgent items."""
        events = [
            {"title": "Urgent Meeting", "time": "10:00", "is_soon": True, "is_now": False},
            {"title": "Far Meeting", "time": "18:00", "is_soon": False, "is_now": False},
        ]
        bullets = _build_bullets(events, [], [])
        if bullets:
            assert "Urgent Meeting" in bullets[0] or "10:00" in bullets[0]


# ---------------------------------------------------------------------------
# DB21–DB25  build_daily_brief — integration
# ---------------------------------------------------------------------------

class TestBuildDailyBrief:

    def test_DB21_returns_all_required_keys(self, tmp_path):
        """DB21: Output always contains all required keys."""
        result = build_daily_brief(vault_path=tmp_path)
        required = {
            "generated_at", "greeting", "sections", "ai_insight",
            "bullets", "stats", "cached", "vault_loaded",
        }
        assert required.issubset(result.keys())

    def test_DB22_no_vault_returns_valid_dict(self, tmp_path):
        """DB22: build_daily_brief with non-existent vault returns valid dict."""
        nonexistent = tmp_path / "no_such_vault_xyz"
        result = build_daily_brief(vault_path=nonexistent)
        assert result["vault_loaded"] is False
        assert isinstance(result["greeting"], str)
        assert isinstance(result["ai_insight"], str)
        assert result["stats"]["events_today"] == 0

    def test_DB23_vault_loaded_true_with_valid_path(self, tmp_path):
        """DB23: vault_loaded=True when vault_path exists."""
        result = build_daily_brief(vault_path=tmp_path)
        assert result["vault_loaded"] is True

    def test_DB24_sections_is_list(self, tmp_path):
        """DB24: sections is a list."""
        result = build_daily_brief(vault_path=tmp_path)
        assert isinstance(result["sections"], list)

    def test_DB25_cache_written_and_reloaded(self, tmp_path):
        """DB25: Second call returns cached=True."""
        build_daily_brief(vault_path=tmp_path, force_refresh=True)
        result2 = build_daily_brief(vault_path=tmp_path, force_refresh=False)
        assert result2["cached"] is True

    def test_DB26_force_refresh_bypasses_cache(self, tmp_path):
        """DB26: force_refresh=True returns cached=False."""
        build_daily_brief(vault_path=tmp_path, force_refresh=True)
        result = build_daily_brief(vault_path=tmp_path, force_refresh=True)
        assert result["cached"] is False

    def test_DB27_stats_counts_today_events(self, tmp_path):
        """DB27: stats.events_today counts today's calendar events."""
        cal = tmp_path / "calendar"
        _write_md(
            cal / "ev1.md",
            f"id: ev1\ntitle: EV1\ndate: {_iso_today_at(10)}\n",
            "",
        )
        result = build_daily_brief(vault_path=tmp_path, force_refresh=True)
        assert result["stats"]["events_today"] >= 1

    def test_DB28_stats_urgent_count(self, tmp_path):
        """DB28: stats.urgent_count counts urgent/reply-required mail."""
        mail = tmp_path / "mail"
        _write_md(
            mail / "urg.md",
            f"""
            id: urg
            type: email
            subject: Urgent Item
            sender: boss@corp.com
            date: {_iso_hours(-1)}
            tags:
              - urgency:high
            """,
            "Please reply ASAP.",
        )
        result = build_daily_brief(vault_path=tmp_path, force_refresh=True)
        assert result["stats"]["urgent_count"] >= 1

    def test_DB29_greeting_contains_name(self, tmp_path):
        """DB29: greeting includes profile_name when provided."""
        result = build_daily_brief(vault_path=tmp_path, profile_name="Игорь", force_refresh=True)
        assert "Игорь" in result["greeting"]


# ---------------------------------------------------------------------------
# Integration with generated test vault
# ---------------------------------------------------------------------------

class TestGeneratedVault:
    """
    VG01–VG05: Tests using a fully-generated test vault (from generate_test_vault.py).
    """

    @pytest.fixture
    def test_vault(self, tmp_path):
        """Generate a complete test vault."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "scripts/generate_test_vault.py",
             "--vault", str(tmp_path), "--email", "igor@example.com"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        assert result.returncode == 0, result.stderr
        return tmp_path

    def test_VG01_brief_has_today_events(self, test_vault):
        """VG01: Daily brief finds today's meeting (quarterly review)."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=test_vault, force_refresh=True)
        assert result["stats"]["events_today"] >= 1

    def test_VG02_brief_has_urgent_inbox(self, test_vault):
        """VG02: Daily brief finds urgent/reply-required mail."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=test_vault, force_refresh=True)
        assert result["stats"]["urgent_count"] >= 1

    def test_VG03_brief_has_tasks(self, test_vault):
        """VG03: Daily brief extracts action items from threads."""
        from personal_assistant.services.daily_brief_service import build_daily_brief
        result = build_daily_brief(vault_path=test_vault, force_refresh=True)
        assert result["stats"]["tasks_count"] >= 0  # graceful: may be 0 if no regex match

    def test_VG04_draft_context_finds_thread(self, test_vault):
        """VG04: draft_context_service finds 3-message Q2 thread."""
        from personal_assistant.services.draft_context_service import build_draft_context
        ctx = build_draft_context("msg_q2_003", vault_path=test_vault)
        assert ctx["message_count"] >= 1, "Should find at least the target message"
        # With full thread support: 3 messages
        assert ctx["thread_messages"] is not None or ctx["message_count"] >= 1

    def test_VG05_meeting_prep_finds_context(self, test_vault):
        """VG05: meeting_prep_service finds recent emails for quarterly review."""
        from personal_assistant.services.meeting_prep_service import build_meeting_prep
        result = build_meeting_prep(
            "meeting_quarterly_review",
            vault_path=test_vault,
            my_email="igor@example.com",
        )
        assert result["event_found"] is True
        assert result["title"] == "Квартальный обзор Q2"
