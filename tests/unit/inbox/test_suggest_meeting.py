"""
Unit tests for suggest-meeting slot generation logic.

Tests are isolated — no vault on disk required.
The core function _suggest_meeting_slots is tested via the
inbox routes module imported directly.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path, mails: list[dict], events: list[dict]) -> Path:
    """Build a minimal vault directory with mail and calendar docs."""
    vault = tmp_path / "vault"

    if mails:
        mail_dir = vault / "mail"
        mail_dir.mkdir(parents=True)
        for m in mails:
            fname = m.get("id", "msg1")
            text = textwrap.dedent(f"""\
                ---
                id: "{fname}"
                subject: "{m.get('subject', 'Test subject')}"
                sender: "{m.get('sender', 'alice@example.com')}"
                recipients: "{m.get('recipients', 'bob@example.com')}"
                cc: "{m.get('cc', '')}"
                date: "{m.get('date', '2026-05-01T10:00:00+00:00')}"
                ---
                {m.get('body', 'Email body text.')}
            """)
            (mail_dir / f"{fname}.md").write_text(text, encoding="utf-8")

    if events:
        cal_dir = vault / "calendar"
        cal_dir.mkdir(parents=True)
        for e in events:
            fname = e.get("id", "event1")
            text = textwrap.dedent(f"""\
                ---
                id: "{fname}"
                title: "{e.get('title', 'Meeting')}"
                date: "{e.get('date', '2026-05-01T10:00:00+00:00')}"
                duration_minutes: {e.get('duration_minutes', 60)}
                ---
                Event body.
            """)
            (cal_dir / f"{fname}.md").write_text(text, encoding="utf-8")

    return vault


# ---------------------------------------------------------------------------
# Import the private helper directly
# ---------------------------------------------------------------------------

from personal_assistant.inbox.routes import _suggest_meeting_slots  # noqa: E402


class TestSuggestMeetingSlots:
    """Tests for _suggest_meeting_slots (rule-based slot generator)."""

    def test_returns_three_slots_on_empty_calendar(self, tmp_path):
        """With no busy events, three slots should always be returned."""
        vault = _make_vault(tmp_path, mails=[], events=[])
        result = _suggest_meeting_slots("msg1", vault_path=vault, num_slots=3)

        assert "slots" in result
        assert len(result["slots"]) == 3

    def test_slot_structure(self, tmp_path):
        """Each slot must have start_iso, end_iso, display_str."""
        vault = _make_vault(tmp_path, mails=[], events=[])
        result = _suggest_meeting_slots("msg1", vault_path=vault, num_slots=1)

        slot = result["slots"][0]
        assert "start_iso" in slot
        assert "end_iso" in slot
        assert "display_str" in slot

    def test_slots_are_future(self, tmp_path):
        """All proposed slots must be in the future."""
        vault = _make_vault(tmp_path, mails=[], events=[])
        now = datetime.now(timezone.utc)
        result = _suggest_meeting_slots("msg1", vault_path=vault, num_slots=3)

        for slot in result["slots"]:
            start = datetime.fromisoformat(slot["start_iso"])
            assert start > now, f"Slot {slot['start_iso']} is in the past"

    def test_slots_skip_weekends(self, tmp_path):
        """No slot should land on Saturday (5) or Sunday (6)."""
        vault = _make_vault(tmp_path, mails=[], events=[])
        result = _suggest_meeting_slots("msg1", vault_path=vault, num_slots=3)

        for slot in result["slots"]:
            dt = datetime.fromisoformat(slot["start_iso"])
            assert dt.weekday() not in (5, 6), \
                f"Slot {slot['start_iso']} is on a weekend"

    def test_slot_duration_is_one_hour(self, tmp_path):
        """Each slot should be exactly 1 hour long."""
        vault = _make_vault(tmp_path, mails=[], events=[])
        result = _suggest_meeting_slots("msg1", vault_path=vault, num_slots=3)

        for slot in result["slots"]:
            start = datetime.fromisoformat(slot["start_iso"])
            end = datetime.fromisoformat(slot["end_iso"])
            delta = end - start
            assert delta == timedelta(hours=1), \
                f"Slot duration is {delta}, expected 1h"

    def test_busy_slots_are_excluded(self, tmp_path):
        """Slots overlapping busy calendar events must be skipped."""
        # Create a busy event tomorrow at 09:00 for 60 min
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        busy_start = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, 9, 0, 0,
            tzinfo=timezone.utc
        )
        vault = _make_vault(
            tmp_path,
            mails=[],
            events=[{
                "id": "busy1",
                "title": "Busy meeting",
                "date": busy_start.isoformat(),
                "duration_minutes": 60,
            }],
        )
        result = _suggest_meeting_slots("msg1", vault_path=vault, num_slots=3)

        # 09:00 tomorrow should NOT appear in slots
        for slot in result["slots"]:
            start = datetime.fromisoformat(slot["start_iso"])
            local_start = start.astimezone(timezone.utc)
            if local_start.date() == tomorrow:
                assert local_start.hour != 9, \
                    f"Busy slot 09:00 was proposed: {slot['start_iso']}"

    def test_participants_extracted_from_doc(self, tmp_path):
        """Participants should be extracted from email frontmatter."""
        vault = _make_vault(
            tmp_path,
            mails=[{
                "id": "email_abc",
                "subject": "Project update",
                "sender": "alice@example.com",
                "recipients": "bob@example.com, carol@example.com",
                "cc": "dave@example.com",
            }],
            events=[],
        )
        result = _suggest_meeting_slots("email_abc", vault_path=vault)

        assert result["doc_found"] is True
        # sender + 2 recipients + 1 cc = 4 unique participants
        assert len(result["participants"]) >= 3

    def test_title_includes_subject(self, tmp_path):
        """Meeting title should include the email subject."""
        vault = _make_vault(
            tmp_path,
            mails=[{
                "id": "email_xyz",
                "subject": "Квартальный отчёт",
                "sender": "alice@example.com",
                "recipients": "bob@example.com",
            }],
            events=[],
        )
        result = _suggest_meeting_slots("email_xyz", vault_path=vault)

        assert "Квартальный отчёт" in result["title"]

    def test_no_vault_returns_slots_anyway(self):
        """With vault_path=None, 3 slots should still be returned (no crash)."""
        result = _suggest_meeting_slots("nonexistent_id", vault_path=None, num_slots=3)

        assert "slots" in result
        assert len(result["slots"]) == 3
        assert result["doc_found"] is False

    def test_busy_count_matches_events(self, tmp_path):
        """busy_count should reflect how many calendar events were found."""
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        busy_start = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, 9, 0, 0,
            tzinfo=timezone.utc
        )
        vault = _make_vault(
            tmp_path,
            mails=[],
            events=[
                {"id": "e1", "date": busy_start.isoformat()},
                {"id": "e2", "date": (busy_start + timedelta(hours=3)).isoformat()},
            ],
        )
        result = _suggest_meeting_slots("msg1", vault_path=vault)
        assert result["busy_count"] == 2

    def test_my_email_excluded_from_participants(self, tmp_path):
        """The user's own email should not appear in participants."""
        vault = _make_vault(
            tmp_path,
            mails=[{
                "id": "email_me",
                "subject": "Test",
                "sender": "me@example.com",
                "recipients": "alice@example.com",
            }],
            events=[],
        )
        with patch(
            "personal_assistant.inbox.routes._get_my_email",
            return_value="me@example.com",
        ):
            result = _suggest_meeting_slots("email_me", vault_path=vault)

        participants_lower = [p.lower() for p in result["participants"]]
        assert "me@example.com" not in participants_lower


# ---------------------------------------------------------------------------
# E2E smoke: POST /api/v1/inbox/{id}/suggest-meeting returns 200
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402


class TestSuggestMeetingEndpoint:
    """Smoke tests for the suggest-meeting HTTP endpoint."""

    def _make_app(self):
        from fastapi import FastAPI

        from personal_assistant.inbox.routes import router as inbox_router

        app = FastAPI()
        app.include_router(inbox_router)
        return app

    def test_endpoint_returns_200(self):
        client = TestClient(self._make_app())
        resp = client.post("/api/v1/inbox/unknown_id/suggest-meeting")
        assert resp.status_code == 200

    def test_endpoint_response_shape(self):
        client = TestClient(self._make_app())
        resp = client.post("/api/v1/inbox/unknown_id/suggest-meeting")
        data = resp.json()

        assert "item_id" in data
        assert "slots" in data
        assert "participants" in data
        assert "title" in data
        assert "busy_count" in data
        assert "doc_found" in data

    def test_endpoint_slots_is_list(self):
        client = TestClient(self._make_app())
        resp = client.post("/api/v1/inbox/test_item/suggest-meeting")
        data = resp.json()
        assert isinstance(data["slots"], list)

    def test_endpoint_item_id_echoed(self):
        client = TestClient(self._make_app())
        resp = client.post("/api/v1/inbox/my_item_123/suggest-meeting")
        data = resp.json()
        assert data["item_id"] == "my_item_123"
