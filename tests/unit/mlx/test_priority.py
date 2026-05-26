"""
Unit tests for Stage 2 — AI Priority Score and Follow-up Detection.

Covers:
  Priority engine (priority.py):
    - compute_priority: all scoring components individually
    - build_contact_graph: vault scanning
    - enrich_with_priority: batch enrichment
    - priority_label / priority_color
    - MLX boost fallback when engine unavailable

  Follow-up service (followup_service.py):
    - detect_followup_needed: all conditions
    - has_outgoing_in_thread: vault scanning for outgoing mail
    - enrich_with_followup: batch enrichment
    - Edge cases: empty vault, missing fields, future-dated items

Tests run entirely offline — no MLX model, no vault files (temp dirs used).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "src"))

from personal_assistant.mlx_server.tasks.priority import (
    _deadline_score,
    _parse_date,
    _recency_penalty,
    _reply_score,
    _sender_score,
    _unread_bonus,
    _urgency_score,
    build_contact_graph,
    compute_priority,
    enrich_with_priority,
    priority_color,
    priority_label,
)
from personal_assistant.services.followup_service import (
    _age_days,
    _wants_reply,
    detect_followup_needed,
    enrich_with_followup,
    has_outgoing_in_thread,
)

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

TODAY = date.today()  # dynamic — tests must not be tied to a specific calendar date


def make_item(
    *,
    id="msg001",
    item_type="email",
    tags_raw=None,
    is_urgent=False,
    is_important=False,
    read=False,
    sender_email="ivan@corp.ru",
    date_str=None,
    extraction=None,
    thread_id="thread_abc",
) -> dict:
    return {
        "id": id,
        "type": item_type,
        "tags_raw": tags_raw or [],
        "is_urgent": is_urgent,
        "is_important": is_important,
        "read": read,
        "sender_email": sender_email,
        "date": date_str or TODAY.isoformat(),
        "extraction": extraction,
        "thread_id": thread_id,
        "subject": "Test subject",
        "body_preview": "Test preview",
        "sender_name": "Ivan",
    }


def make_vault(tmp_path: Path, files: list[dict]) -> Path:
    """Create a minimal vault directory structure with .md files."""
    mail_dir = tmp_path / "mail"
    mail_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        fm_lines = ["---"]
        for k, v in f.items():
            fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        fm_lines.append("")
        fm_lines.append(f.get("_body", "Message body."))
        content = "\n".join(fm_lines)
        filename = f.get("_filename", f"{f.get('id', 'msg')}.md")
        (mail_dir / filename).write_text(content, encoding="utf-8")
    return tmp_path


# ===========================================================================
# SECTION 1: Scoring components
# ===========================================================================

class TestParseDate:
    def test_iso_date(self):
        assert _parse_date("2026-05-24") == date(2026, 5, 24)

    def test_iso_datetime(self):
        assert _parse_date("2026-05-24T10:30:00") == date(2026, 5, 24)

    def test_iso_with_tz(self):
        assert _parse_date("2026-05-24T10:30:00+03:00") == date(2026, 5, 24)

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_invalid_returns_none(self):
        assert _parse_date("not-a-date") is None


class TestUrgencyScore:
    def test_urgent_tag(self):
        assert _urgency_score(["urgency:urgent"], False, False) == 40

    def test_critical_tag(self):
        assert _urgency_score(["urgency:critical"], False, False) == 40

    def test_russian_urgent(self):
        assert _urgency_score(["срочно"], False, False) == 40

    def test_is_urgent_flag(self):
        assert _urgency_score([], True, False) == 40

    def test_important_tag(self):
        assert _urgency_score(["urgency:important"], False, False) == 20

    def test_finance_tag(self):
        assert _urgency_score(["category:finance"], False, False) == 20

    def test_is_important_flag(self):
        assert _urgency_score([], False, True) == 20

    def test_no_tags(self):
        assert _urgency_score([], False, False) == 0

    def test_urgent_takes_precedence_over_important(self):
        assert _urgency_score(["urgency:urgent", "urgency:important"], False, False) == 40


class TestReplyScore:
    def test_reply_required_extraction(self):
        item = make_item(extraction={"reply_required": True, "intent": "info"})
        assert _reply_score(item) == 15

    def test_request_intent(self):
        item = make_item(extraction={"reply_required": False, "intent": "request"})
        assert _reply_score(item) == 10

    def test_question_intent(self):
        item = make_item(extraction={"reply_required": False, "intent": "question"})
        assert _reply_score(item) == 10

    def test_no_extraction(self):
        item = make_item(extraction=None)
        assert _reply_score(item) == 0

    def test_info_intent_no_score(self):
        item = make_item(extraction={"reply_required": False, "intent": "info"})
        assert _reply_score(item) == 0

    def test_reply_required_overrides_intent(self):
        # reply_required=True gives 15, which > question's 10
        item = make_item(extraction={"reply_required": True, "intent": "question"})
        assert _reply_score(item) == 15


class TestDeadlineScore:
    def test_overdue(self):
        item = make_item(extraction={"deadline": "2026-05-20"})  # 4 days ago
        assert _deadline_score(item, TODAY) == 25

    def test_today(self):
        item = make_item(extraction={"deadline": TODAY.isoformat()})
        assert _deadline_score(item, TODAY) == 25

    def test_tomorrow(self):
        item = make_item(extraction={"deadline": (TODAY + timedelta(1)).isoformat()})
        assert _deadline_score(item, TODAY) == 20

    def test_three_days(self):
        item = make_item(extraction={"deadline": (TODAY + timedelta(3)).isoformat()})
        assert _deadline_score(item, TODAY) == 15

    def test_week(self):
        item = make_item(extraction={"deadline": (TODAY + timedelta(7)).isoformat()})
        assert _deadline_score(item, TODAY) == 10

    def test_far_future(self):
        item = make_item(extraction={"deadline": (TODAY + timedelta(30)).isoformat()})
        assert _deadline_score(item, TODAY) == 5

    def test_no_deadline(self):
        item = make_item(extraction={"deadline": None})
        assert _deadline_score(item, TODAY) == 0

    def test_null_extraction(self):
        item = make_item(extraction=None)
        assert _deadline_score(item, TODAY) == 0

    def test_tag_deadline_today(self):
        item = make_item(tags_raw=["deadline:today"], extraction=None)
        assert _deadline_score(item, TODAY) == 25

    def test_tag_deadline_this_week(self):
        item = make_item(tags_raw=["deadline:this_week"], extraction=None)
        assert _deadline_score(item, TODAY) == 10


class TestSenderScore:
    def test_high_frequency_capped_at_15(self):
        cg = {"ivan@corp.ru": {"freq": 10, "name": "Ivan"}}
        assert _sender_score("ivan@corp.ru", cg) == 15

    def test_freq_1(self):
        cg = {"ivan@corp.ru": {"freq": 1, "name": "Ivan"}}
        assert _sender_score("ivan@corp.ru", cg) == 3

    def test_freq_5(self):
        cg = {"ivan@corp.ru": {"freq": 5, "name": "Ivan"}}
        assert _sender_score("ivan@corp.ru", cg) == 15

    def test_unknown_sender(self):
        cg = {"other@corp.ru": {"freq": 5, "name": "Other"}}
        assert _sender_score("ivan@corp.ru", cg) == 0

    def test_empty_graph(self):
        assert _sender_score("ivan@corp.ru", {}) == 0

    def test_empty_email(self):
        cg = {"ivan@corp.ru": {"freq": 5}}
        assert _sender_score("", cg) == 0

    def test_case_insensitive(self):
        cg = {"ivan@corp.ru": {"freq": 3, "name": "Ivan"}}
        assert _sender_score("IVAN@CORP.RU", cg) == 9


class TestRecencyPenalty:
    def test_today_no_penalty(self):
        item = make_item(date_str=TODAY.isoformat())
        assert _recency_penalty(item, TODAY) == 0

    def test_one_day_ago(self):
        item = make_item(date_str=(TODAY - timedelta(1)).isoformat())
        assert _recency_penalty(item, TODAY) == 2

    def test_five_days_ago(self):
        item = make_item(date_str=(TODAY - timedelta(5)).isoformat())
        assert _recency_penalty(item, TODAY) == 10

    def test_capped_at_20(self):
        item = make_item(date_str=(TODAY - timedelta(30)).isoformat())
        assert _recency_penalty(item, TODAY) == 20

    def test_future_no_penalty(self):
        item = make_item(date_str=(TODAY + timedelta(5)).isoformat())
        assert _recency_penalty(item, TODAY) == 0

    def test_no_date_no_penalty(self):
        item = make_item(date_str=None)
        assert _recency_penalty(item, TODAY) == 0


class TestUnreadBonus:
    def test_unread_gives_5(self):
        item = make_item(read=False)
        assert _unread_bonus(item) == 5

    def test_read_gives_0(self):
        item = make_item(read=True)
        assert _unread_bonus(item) == 0


# ===========================================================================
# SECTION 2: compute_priority — integration
# ===========================================================================

class TestComputePriority:
    def test_returns_int(self):
        item = make_item()
        result = compute_priority(item, today=TODAY)
        assert isinstance(result, int)

    def test_range_0_to_100(self):
        # Very urgent item
        item = make_item(
            is_urgent=True,
            extraction={"reply_required": True, "deadline": TODAY.isoformat(), "intent": "request"},
            date_str=TODAY.isoformat(),
        )
        score = compute_priority(item, today=TODAY)
        assert 0 <= score <= 100

    def test_empty_item_returns_low(self):
        item = make_item(is_urgent=False, is_important=False, extraction=None)
        score = compute_priority(item, today=TODAY)
        assert score <= 30  # unread bonus only

    def test_urgent_item_scores_high(self):
        item = make_item(
            is_urgent=True,
            extraction={"reply_required": True, "deadline": (TODAY + timedelta(1)).isoformat(), "intent": "request"},
            date_str=TODAY.isoformat(),
        )
        score = compute_priority(item, today=TODAY)
        assert score >= 60

    def test_read_old_item_scores_low(self):
        item = make_item(
            read=True,
            is_urgent=False,
            date_str=(TODAY - timedelta(10)).isoformat(),
            extraction=None,
        )
        score = compute_priority(item, today=TODAY)
        assert score <= 15

    def test_sender_graph_increases_score(self):
        item_no_cg = make_item(sender_email="ivan@corp.ru")
        item_with_cg = make_item(sender_email="ivan@corp.ru")
        cg = {"ivan@corp.ru": {"freq": 5, "name": "Ivan"}}

        score_no = compute_priority(item_no_cg, contact_graph={}, today=TODAY)
        score_with = compute_priority(item_with_cg, contact_graph=cg, today=TODAY)
        assert score_with >= score_no

    def test_overdue_deadline_increases_score(self):
        base = make_item()
        overdue = make_item(extraction={"deadline": (TODAY - timedelta(2)).isoformat(), "reply_required": False, "intent": "info"})
        assert compute_priority(overdue, today=TODAY) > compute_priority(base, today=TODAY)

    def test_mlx_engine_none_does_not_crash(self):
        item = make_item(is_urgent=True)
        score = compute_priority(item, mlx_engine=None, today=TODAY)
        assert 0 <= score <= 100

    def test_mlx_engine_error_graceful(self):
        """Simulate MLX engine that raises — should return rule-based score."""
        class BrokenEngine:
            def generate(self, *a, **kw):
                raise RuntimeError("GPU exploded")

        item = make_item(
            is_urgent=False,
            extraction={"reply_required": True, "intent": "request"},
            date_str=(TODAY - timedelta(1)).isoformat(),
        )
        # Score should be in borderline range (30–60) to trigger boost attempt
        compute_priority(item, mlx_engine=None, today=TODAY)
        # With broken engine — should not raise
        boosted = compute_priority(item, mlx_engine=BrokenEngine(), today=TODAY)
        assert 0 <= boosted <= 100


class TestPriorityLabel:
    def test_high(self):
        assert priority_label(80) == "high"
        assert priority_label(67) == "high"

    def test_medium(self):
        assert priority_label(50) == "medium"
        assert priority_label(34) == "medium"

    def test_low(self):
        assert priority_label(10) == "low"
        assert priority_label(0) == "low"
        assert priority_label(33) == "low"

    def test_boundary_67(self):
        assert priority_label(66) == "medium"
        assert priority_label(67) == "high"


class TestPriorityColor:
    def test_color_matches_label(self):
        for score in [0, 20, 33, 34, 50, 66, 67, 80, 100]:
            assert priority_color(score) == priority_label(score)


# ===========================================================================
# SECTION 3: build_contact_graph
# ===========================================================================

class TestBuildContactGraph:
    def test_empty_vault(self, tmp_path):
        (tmp_path / "mail").mkdir()
        graph = build_contact_graph(tmp_path)
        assert graph == {}

    def test_counts_sender_frequency(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "m1", "from": "ivan@corp.ru", "sender_name": "Ivan", "_filename": "m1.md"},
            {"id": "m2", "from": "ivan@corp.ru", "sender_name": "Ivan", "_filename": "m2.md"},
            {"id": "m3", "from": "anna@corp.ru", "sender_name": "Anna", "_filename": "m3.md"},
        ])
        graph = build_contact_graph(tmp_path)
        assert "ivan@corp.ru" in graph
        assert graph["ivan@corp.ru"]["freq"] == 2
        assert "anna@corp.ru" in graph
        assert graph["anna@corp.ru"]["freq"] == 1

    def test_extracts_name(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "m1", "from": "ivan@corp.ru", "sender_name": "Иван Петров", "_filename": "m1.md"},
        ])
        graph = build_contact_graph(tmp_path)
        assert graph["ivan@corp.ru"]["name"] == "Иван Петров"

    def test_email_in_angle_brackets(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "m1", "from": "Иван Петров <ivan@corp.ru>", "_filename": "m1.md"},
        ])
        graph = build_contact_graph(tmp_path)
        assert "ivan@corp.ru" in graph

    def test_no_mail_dir_falls_back_to_root(self, tmp_path):
        """If no mail/ subdir, should scan root."""
        (tmp_path / "m1.md").write_text(
            "---\nfrom: test@example.com\n---\nbody\n", encoding="utf-8"
        )
        graph = build_contact_graph(tmp_path)
        assert "test@example.com" in graph


class TestEnrichWithPriority:
    def test_adds_priority_field(self):
        items = [make_item(id="a"), make_item(id="b")]
        enrich_with_priority(items)
        for it in items:
            assert "priority" in it
            assert "priority_label" in it

    def test_returns_same_list(self):
        items = [make_item()]
        result = enrich_with_priority(items)
        assert result is items

    def test_priority_in_range(self):
        items = [make_item(is_urgent=True), make_item(is_important=True), make_item()]
        enrich_with_priority(items)
        for it in items:
            assert 0 <= it["priority"] <= 100


# ===========================================================================
# SECTION 4: Follow-up Detection
# ===========================================================================

class TestAgedays:
    def test_today_is_0(self):
        assert _age_days(TODAY.isoformat()) == 0

    def test_yesterday_is_1(self):
        assert _age_days((TODAY - timedelta(1)).isoformat()) == 1

    def test_future_is_negative(self):
        assert _age_days((TODAY + timedelta(3)).isoformat()) == -3

    def test_none_is_0(self):
        assert _age_days(None) == 0


class TestWantsReply:
    def test_reply_required_true(self):
        assert _wants_reply({"extraction": {"reply_required": True, "intent": "info"}, "tags_raw": []})

    def test_request_intent(self):
        assert _wants_reply({"extraction": {"reply_required": False, "intent": "request"}, "tags_raw": []})

    def test_question_intent(self):
        assert _wants_reply({"extraction": {"reply_required": False, "intent": "question"}, "tags_raw": []})

    def test_info_intent_no_reply(self):
        assert not _wants_reply({"extraction": {"reply_required": False, "intent": "info"}, "tags_raw": []})

    def test_urgent_tag_fallback(self):
        assert _wants_reply({"extraction": None, "tags_raw": ["urgency:urgent"]})

    def test_no_extraction_no_tags(self):
        assert not _wants_reply({"extraction": None, "tags_raw": []})


class TestHasOutgoingInThread:
    def test_no_thread_id_returns_false(self, tmp_path):
        assert not has_outgoing_in_thread("", tmp_path, "me@corp.ru")

    def test_no_my_email_returns_false(self, tmp_path):
        assert not has_outgoing_in_thread("thread_abc", tmp_path, "")

    def test_finds_outgoing(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "reply1", "thread_id": "thread_abc", "from": "me@corp.ru", "_filename": "reply1.md"},
        ])
        assert has_outgoing_in_thread("thread_abc", tmp_path, "me@corp.ru")

    def test_different_thread_not_found(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "reply1", "thread_id": "thread_xyz", "from": "me@corp.ru", "_filename": "reply1.md"},
        ])
        assert not has_outgoing_in_thread("thread_abc", tmp_path, "me@corp.ru")

    def test_different_sender_not_found(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "reply1", "thread_id": "thread_abc", "from": "other@corp.ru", "_filename": "reply1.md"},
        ])
        assert not has_outgoing_in_thread("thread_abc", tmp_path, "me@corp.ru")

    def test_case_insensitive_email(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "reply1", "thread_id": "thread_abc", "from": "ME@CORP.RU", "_filename": "reply1.md"},
        ])
        assert has_outgoing_in_thread("thread_abc", tmp_path, "me@corp.ru")

    def test_empty_vault(self, tmp_path):
        (tmp_path / "mail").mkdir()
        assert not has_outgoing_in_thread("thread_abc", tmp_path, "me@corp.ru")


class TestDetectFollowupNeeded:
    def _old_item(self, **kw):
        """Item dated 3 days ago — old enough to flag."""
        defaults = dict(
            date_str=(TODAY - timedelta(3)).isoformat(),
            extraction={"reply_required": True, "intent": "request"},
        )
        defaults.update(kw)
        return make_item(**defaults)

    def test_basic_flagged(self):
        items = [self._old_item()]
        flagged = detect_followup_needed(items, threshold_days=2)
        assert items[0]["id"] in flagged

    def test_too_new_not_flagged(self):
        item = make_item(
            date_str=TODAY.isoformat(),
            extraction={"reply_required": True, "intent": "request"},
        )
        flagged = detect_followup_needed([item], threshold_days=2)
        assert item["id"] not in flagged

    def test_no_reply_required_not_flagged(self):
        item = self._old_item(extraction={"reply_required": False, "intent": "info"})
        flagged = detect_followup_needed([item], threshold_days=2)
        assert item["id"] not in flagged

    def test_meeting_not_flagged(self):
        item = self._old_item(item_type="meeting")
        flagged = detect_followup_needed([item], threshold_days=2)
        assert item["id"] not in flagged

    def test_outgoing_reply_clears_flag(self, tmp_path):
        make_vault(tmp_path, [
            {"id": "reply1", "thread_id": "thread_abc", "from": "me@corp.ru", "_filename": "reply1.md"},
        ])
        item = self._old_item(thread_id="thread_abc")
        flagged = detect_followup_needed([item], vault_path=tmp_path, my_email="me@corp.ru", threshold_days=2)
        assert item["id"] not in flagged

    def test_multiple_items_mixed(self, tmp_path):
        old_needs_reply = self._old_item(id="need_reply", thread_id="t1")
        old_has_reply = self._old_item(id="has_reply", thread_id="t2")
        new_item = make_item(
            id="new_item",
            date_str=TODAY.isoformat(),
            extraction={"reply_required": True, "intent": "request"},
        )
        make_vault(tmp_path, [
            {"id": "out", "thread_id": "t2", "from": "me@corp.ru", "_filename": "out.md"},
        ])
        flagged = detect_followup_needed(
            [old_needs_reply, old_has_reply, new_item],
            vault_path=tmp_path, my_email="me@corp.ru", threshold_days=2
        )
        assert "need_reply" in flagged
        assert "has_reply" not in flagged
        assert "new_item" not in flagged

    def test_no_vault_path_uses_tags_only(self):
        item = self._old_item(extraction=None, tags_raw=["urgency:urgent"])
        flagged = detect_followup_needed([item], vault_path=None, my_email="", threshold_days=2)
        assert item["id"] in flagged

    def test_custom_threshold(self):
        item = make_item(
            date_str=(TODAY - timedelta(1)).isoformat(),
            extraction={"reply_required": True, "intent": "request"},
        )
        # threshold=3: 1 day old → NOT flagged
        assert item["id"] not in detect_followup_needed([item], threshold_days=3)
        # threshold=1: 1 day old → flagged
        assert item["id"] in detect_followup_needed([item], threshold_days=1)

    def test_empty_items(self):
        assert detect_followup_needed([]) == []


class TestEnrichWithFollowup:
    def test_adds_followup_needed_field(self):
        items = [make_item()]
        enrich_with_followup(items)
        assert "followup_needed" in items[0]
        assert isinstance(items[0]["followup_needed"], bool)

    def test_returns_same_list(self):
        items = [make_item()]
        result = enrich_with_followup(items)
        assert result is items

    def test_old_request_flagged(self):
        items = [make_item(
            date_str=(TODAY - timedelta(3)).isoformat(),
            extraction={"reply_required": True, "intent": "request"},
        )]
        enrich_with_followup(items, threshold_days=2)
        assert items[0]["followup_needed"] is True

    def test_new_item_not_flagged(self):
        items = [make_item(
            date_str=TODAY.isoformat(),
            extraction={"reply_required": True, "intent": "request"},
        )]
        enrich_with_followup(items, threshold_days=2)
        assert items[0]["followup_needed"] is False
